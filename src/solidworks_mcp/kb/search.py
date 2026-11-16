"""Text search over the cases: plain, case-insensitive, no vectors (Docs/KNOWLEDGE-BASE.md §5).

Every word of the query must appear somewhere in a case's case.md or spec.json (description,
spec callouts and notes, lessons, review notes, error codes), so '1/2-13 UNC' finds every case
with that thread. The answer is INDEX.md's own lines, in INDEX.md's order.
"""

from ..errors import SolidWorksError
from . import store
from .files import read_text

UNREVIEWED = "unreviewed"


def check_status(status) -> list | None:
    if status is None:
        return None
    wanted = [status] if isinstance(status, str) else list(status)
    allowed = (store.STATUSES - {"abandoned"}) | {UNREVIEWED}
    bad = [s for s in wanted if s not in allowed]
    if bad:
        raise SolidWorksError(f"status takes {sorted(allowed)} (got {bad}).", code="KB_BAD_FILTER")
    return wanted


def _corpus(kb, case: dict) -> str:
    folder = kb.case_folder(case["dn"])
    spec = folder / "spec.json"
    return (store.render_case(case) + "\n" + (read_text(spec) if spec.is_file() else "")).lower()


def search(kb, text: str | None = None, part_type: str | None = None, status=None, limit: int = 10) -> dict:
    """INDEX lines of the cases matching all the words of `text`, `part_type` and `status`."""
    wanted = check_status(status)
    limit = max(1, min(int(limit), 100))
    words = (text or "").lower().split()
    found = []
    for case in sorted(kb.cases(), key=store.order_key):
        if part_type and (case.get("part_type") or "").lower() != part_type.strip().lower():
            continue
        if wanted and case["status"] not in wanted and not (UNREVIEWED in wanted and store.tier(case) == 2):
            continue
        if words:
            corpus = _corpus(kb, case)
            if not all(word in corpus for word in words):
                continue
        found.append(store.index_line(case))
    result = {"ok": True, "results": found[:limit], "matches": len(found)}
    if len(found) > limit:
        result["note"] = f"{len(found)} cases match; the first {limit} are shown. Narrow the search or raise limit."
    if not found:
        result["note"] = "No earlier case matches."
    return result
