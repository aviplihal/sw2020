"""Hybrid search over the cases: keywords (FTS5) and vectors (cosine), merged by rank.

At this scale (thousands of cases at most) plain NumPy over every stored vector
is enough; no vector database. Lists are merged by reciprocal rank fusion,
1 / (60 + rank) per list, added up: no weights to tune (Docs/KNOWLEDGE-BASE.md §5.2).
"""

import re

import numpy as np

from ..errors import SolidWorksError

RRF_K = 60
IMAGE_KINDS = ("view_front", "view_top", "view_side", "sheet")
STATUS_TIER = {"approved": 0, "changes_needed": 1}  # everything else is unreviewed: 2
STATUSES = {"ingested", "described", "modelling", "finalised", "approved", "changes_needed"}


def fuse(*ranked_lists) -> dict:
    """Reciprocal rank fusion: {item: summed 1 / (60 + rank)}, rank counted from 1."""
    scores = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (RRF_K + rank)
    return scores


def words(text: str) -> list:
    """The words of a query, for keyword search ('M6', '1/4-20' -> '1', '4', '20', ...)."""
    return list(dict.fromkeys(re.findall(r"\w+", text or "")))


def vector_ranking(query_vectors: list, stored: list, kinds, exclude=None) -> list:
    """Case ids by their best cosine similarity to any query vector, over stored vectors of `kinds`.

    Image vectors are compared with every stored image kind, not only the same
    view name: one drawing's 'front' may be another's 'top'. Only input
    drawings are ever stored, so like is compared with like.
    """
    rows = [r for r in stored if r["kind"] in kinds and r["case_id"] != exclude]
    if not query_vectors or not rows:
        return []
    matrix = np.stack([np.frombuffer(r["data"], dtype=np.float32) for r in rows])
    queries = np.stack([np.asarray(q, dtype=np.float32) for q in query_vectors])
    if queries.shape[1] != matrix.shape[1]:
        return []  # another model's vectors: kb_reembed_pending rebuilds them
    best = {}
    for row, score in zip(rows, (matrix @ queries.T).max(axis=1)):
        best[row["case_id"]] = max(best.get(row["case_id"], -1.0), float(score))
    return sorted(best, key=lambda case_id: -best[case_id])


def check_filters(filters: dict | None) -> dict:
    """{'status': [...], 'part_type': str, 'size_mm': [min, max]}, checked."""
    filters = dict(filters or {})
    unknown = set(filters) - {"status", "part_type", "size_mm"}
    if unknown:
        raise SolidWorksError(f"Unknown filter(s) {sorted(unknown)}: use status, part_type, size_mm.",
                              code="KB_BAD_FILTER")
    statuses = filters.get("status")
    if statuses is not None:
        statuses = [statuses] if isinstance(statuses, str) else list(statuses)
        if not set(statuses) <= STATUSES:
            raise SolidWorksError(f"status filter takes {sorted(STATUSES)} (got {statuses}).", code="KB_BAD_FILTER")
        filters["status"] = statuses
    size = filters.get("size_mm")
    if size is not None:
        try:
            low, high = (float(v) for v in size)
        except (TypeError, ValueError):
            raise SolidWorksError(f"size_mm is [min, max] of the largest side in mm (got {size!r}).",
                                  code="KB_BAD_FILTER") from None
        filters["size_mm"] = (low, high)
    return filters


def largest_side_mm(case: dict) -> float | None:
    measured = case.get("review_measurements") or case.get("finalise_measurements") or {}
    box = (measured.get("mass_properties") or measured).get("bounding_box_mm") or {}
    size = box.get("size_mm") if isinstance(box, dict) else None
    return max(size) if size else None


def passes(case: dict, filters: dict) -> bool:
    if filters.get("status") and case["status"] not in filters["status"]:
        return False
    if filters.get("part_type") and (case.get("part_type") or "").lower() != filters["part_type"].lower():
        return False
    if filters.get("size_mm"):
        side = largest_side_mm(case)
        low, high = filters["size_mm"]
        if side is None or not low <= side <= high:
            return False
    return True


def order(cases_by_id: dict, scores: dict) -> list:
    """Case ids best first: approved above needed-changes above unreviewed, then by fused score."""
    return sorted(scores, key=lambda i: (STATUS_TIER.get(cases_by_id[i]["status"], 2), -scores[i]))


def summary(case: dict, lessons: list) -> dict:
    """One line per case: DN, what it is, outcome, key lessons (§5.2)."""
    outcome = {"approved": "approved", "changes_needed": "NEEDED CHANGES"}.get(case["status"],
                                                                            f"unreviewed ({case['status']})")
    what = (case.get("description") or "no description yet").strip().split("\n")[0]
    if len(what) > 120:
        what = what[:117] + "..."
    line = f"{case['dn']} · {case.get('part_type') or '?'} · {outcome} · {what}"
    if lessons:
        line += " · lessons: " + "; ".join(lessons)
    entry = {"dn": case["dn"], "summary": line}
    if case["status"] != "approved":
        entry["caution"] = ("its files needed correcting at review: see the review notes before copying anything"
                            if case["status"] == "changes_needed"
                            else "not reviewed yet: not a trusted reference")
    return entry
