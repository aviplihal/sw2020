"""The knowledge-base tools for a case: ingest, read, describe, search, look up, abandon.

Each public method is one kb_* MCP tool (server.py wraps them in one block).
They return JSON-serialisable dicts; ingest_drawing and view_region return
(dict, JPEG bytes), which the server sends as text plus an image.
Design: Docs/KNOWLEDGE-BASE.md §3-§5 and §11.
"""

import ntpath
import os
import shutil

from .. import __version__
from ..errors import SolidWorksError
from . import cards, images, search
from . import dn as dn_names
from .embed import Embedder, EmbeddingUnavailable
from .store import KnowledgeBase

VIEWS = ("front", "top", "side")
SPEC_STATUSES = {"read", "unreadable"}
THUMB_SIDE = 400


def check_spec(spec) -> list:
    """Check the drawing spec's outline (the full format is SHOP-DRAWINGS.md §4.1,
    validated by drawing_spec.py, which the knowledge base does not import);
    returns the items flagged unreadable."""
    if not isinstance(spec, dict) or not isinstance(spec.get("items"), list):
        raise SolidWorksError('drawing_spec is {"units": "in", "items": [...]} (SHOP-DRAWINGS.md §4.1).',
                              code="KB_BAD_SPEC")
    if not isinstance(spec.get("units"), str):
        raise SolidWorksError('drawing_spec needs "units", e.g. "in".', code="KB_BAD_SPEC")
    seen = set()
    for k, item in enumerate(spec["items"]):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("kind"), str):
            raise SolidWorksError(f"drawing_spec item {k} needs a string 'id' and 'kind' (got {item!r}).",
                                  code="KB_BAD_SPEC")
        if item["id"] in seen:
            raise SolidWorksError(f"drawing_spec has the id '{item['id']}' twice; ids name each item.",
                                  code="KB_BAD_SPEC")
        seen.add(item["id"])
        if item.get("status") not in SPEC_STATUSES:
            raise SolidWorksError(f"drawing_spec item '{item['id']}' needs status 'read' or 'unreadable' "
                                  f"(got {item.get('status')!r}).", code="KB_BAD_SPEC")
    return [item for item in spec["items"] if item["status"] == "unreadable"]


def _strings(value) -> list:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _measured(result: dict | None) -> dict | None:
    """The measurements of a logged result: volume, mass and bounding box."""
    props = (result or {}).get("mass_properties")
    if not props:
        return None
    return {key: props[key] for key in ("volume_mm3", "mass_g", "bounding_box_mm") if key in props}


class CaseTools:
    def __init__(self, kb: KnowledgeBase, embedder: Embedder, tool_reference=None) -> None:
        self.kb = kb
        self.embedder = embedder
        # tool name -> its 'How to call' line from server.py, or None for no such tool
        self.tool_reference = tool_reference or (lambda tool: None)

    # --- ingest and read ----------------------------------------------------------

    def ingest_drawing(self, path: str, page: int = 1) -> tuple:
        """Check the DN number, render the page, open the case; the overview image goes with the result."""
        number = dn_names.from_filename(path)
        if not os.path.isfile(path):
            raise SolidWorksError(f"No file at {path}.", code="KB_FILE_NOT_FOUND")
        image, dpi = images.render_page(path, page)
        case, warnings = self.kb.ingest(number, ntpath.basename(path), page)
        folder = self.kb.case_folder(case["dn"])
        (folder / "views").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, folder / "input.pdf")
        image.save(folder / "page.png")
        thumb = image.copy()
        thumb.thumbnail((THUMB_SIDE, THUMB_SIDE))
        thumb.save(folder / "thumb.png")
        self.kb.update_case(case["id"], dpi=dpi)
        overview, scale = images.shown(image)
        result = {"ok": True, "dn": case["dn"], "page": page, "dpi": dpi, "page_px": list(image.size),
                  "overview_dpi": round(dpi * scale), "warnings": warnings,
                  "next": "This overview is too coarse to read dimensions. Read every value from a zoomed region "
                          "(kb_view_region with a box in page fractions [x0, y0, x1, y1]), then call "
                          "kb_describe_case."}
        return result, overview

    def view_region(self, box, dn: str | None = None) -> tuple:
        """One region of a case's page (the open case, or `dn`) at full resolution, as an image."""
        case = self._case(dn)
        x0, y0, x1, y1 = images.parse_box(box)
        from PIL import Image

        with Image.open(self.kb.case_folder(case["dn"]) / "page.png") as page:
            region = images.crop(page, (x0, y0, x1, y1))
        shown, scale = images.shown(region)
        result = {"ok": True, "dn": case["dn"], "box": [x0, y0, x1, y1], "region_px": list(region.size),
                  "effective_dpi": round((case["dpi"] or images.RENDER_DPI) * scale)}
        if scale < 0.75:
            result["note"] = (f"Shown at {scale:.0%} of full resolution. For small text, view a smaller box: "
                              "the smaller the box, the closer the zoom.")
        return result, shown

    def _case(self, dn: str | None) -> dict:
        if dn is None:
            return self.kb.require_open_case()
        case = self.kb.case(dn)
        if case is None:
            raise SolidWorksError(f"No case {dn}.", code="KB_NO_SUCH_CASE")
        return case

    # --- describe -------------------------------------------------------------------

    def describe_case(self, view_boxes: dict, description: str, part_type: str, drawing_spec: dict,
                      handwritten_dn: str | None = None, dn_confirmed: bool = False) -> dict:
        """Store what Claude read; crop and embed the views; return the unreadable items and similar cases."""
        case = self.kb.require_open_case()
        if case["status"] not in ("ingested", "described"):
            raise SolidWorksError(f"{case['dn']} is already {case['status']}: correct the spec with kb_update_spec.",
                                  code="KB_ALREADY_DESCRIBED")
        warnings = []
        if handwritten_dn:
            written = dn_names.from_written(handwritten_dn)
            if written is None:
                warnings.append(f"'{handwritten_dn}' does not read as a DN number; stored as written.")
            elif written != case["number"] and not dn_confirmed:
                raise SolidWorksError(
                    f"The paper says {dn_names.name(written)} but the file is {case['dn']}. Ask the user which is "
                    f"right. If the file name is, call kb_describe_case again with dn_confirmed=True; if not, "
                    f"abandon this case (kb_abandon_case), rename the file and ingest it again.",
                    code="KB_DN_MISMATCH", params={"file": case["dn"], "written": dn_names.name(written)})
        if not (description or "").strip():
            raise SolidWorksError("description is empty: describe the part in a few sentences.",
                                  code="KB_NO_DESCRIPTION")
        if not (part_type or "").strip():
            raise SolidWorksError("part_type is empty: a short label such as bracket, shaft, flange or plate.",
                                  code="KB_NO_PART_TYPE")
        boxes = self._view_boxes(view_boxes)
        unreadable = check_spec(drawing_spec)

        from PIL import Image

        folder = self.kb.case_folder(case["dn"])
        with Image.open(folder / "page.png") as page:
            for view in VIEWS:
                stale = folder / "views" / f"{view}.png"
                if view not in boxes and stale.exists():
                    stale.unlink()
            for view, box in boxes.items():
                images.crop(page, images.with_margin(box)).save(folder / "views" / f"{view}.png")
        history = (case["spec_history"] or []) + ([case["spec"]] if case["spec"] else [])
        case = self.kb.set_status(case["id"], "described", description=description.strip(),
                                  part_type=part_type.strip().lower(), handwritten_dn=handwritten_dn,
                                  spec=drawing_spec, spec_history=history, described_at=self.kb.now())
        self.reindex(case)
        embedded = self._embed(case)
        found = self.search(like_case=case["dn"], limit=5)
        result = {"ok": True, "dn": case["dn"], "views": sorted(boxes), "unreadable": unreadable,
                  "similar_cases": found["results"], "embedding": embedded}
        if unreadable:
            result["next"] = (f"{len(unreadable)} item(s) are unreadable: ask the user about each before modelling, "
                              "then store the answers with kb_update_spec.")
        if warnings:
            result["warnings"] = warnings
        return result

    def _view_boxes(self, view_boxes) -> dict:
        if not isinstance(view_boxes, dict) or not view_boxes:
            raise SolidWorksError("view_boxes is {'front': [x0, y0, x1, y1], 'top': [...], 'side': [...]} in page "
                                  "fractions; give the views the drawing has (at least one).", code="KB_BAD_BOX")
        unknown = set(view_boxes) - set(VIEWS)
        if unknown:
            raise SolidWorksError(f"view_boxes takes {', '.join(VIEWS)} (got {sorted(unknown)}).", code="KB_BAD_BOX")
        return {view: images.parse_box(box, f"view_boxes['{view}']") for view, box in view_boxes.items()}

    def update_spec(self, drawing_spec: dict) -> dict:
        """Replace the open case's spec (e.g. with the user's answers); the earlier one is kept."""
        case = self.kb.require_open_case()
        if not case["spec"]:
            raise SolidWorksError(f"{case['dn']} has no spec yet: give it with kb_describe_case.", code="KB_NO_SPEC")
        unreadable = check_spec(drawing_spec)
        case = self.kb.update_case(case["id"], spec=drawing_spec,
                                   spec_history=(case["spec_history"] or []) + [case["spec"]])
        self.reindex(case)
        return {"ok": True, "dn": case["dn"], "items": len(drawing_spec["items"]), "unreadable": unreadable,
                "spec_versions": len(case["spec_history"]) + 1}

    def reindex(self, case: dict) -> None:
        """Rebuild the case's keyword index from everything searchable about it."""
        self.kb.index_case(case["id"], {
            "description": case.get("description"),
            "part_type": case.get("part_type"),
            "spec": " ".join(_strings(case.get("spec"))),
            "tools": " ".join(self.kb.tools_used(case["id"])),
            "errors": " ".join(self.kb.error_codes(case["id"])),
            "review": case.get("review_notes"),
        })

    # --- vectors --------------------------------------------------------------------

    def _embed(self, case: dict) -> dict:
        """Embed the case's views, page and description. On any failure the case is
        marked pending and nothing is lost; the result says why."""
        folder = self.kb.case_folder(case["dn"])
        from PIL import Image

        kinds, pictures = [], []
        for view in VIEWS:
            path = folder / "views" / f"{view}.png"
            if path.exists():
                with Image.open(path) as picture:
                    pictures.append(images.scaled_to_pixels(picture.convert("RGB"), images.EMBED_MAX_PIXELS))
                kinds.append(f"view_{view}")
        with Image.open(folder / "page.png") as page:
            pictures.append(images.scaled_to_pixels(page.convert("RGB"), images.EMBED_MAX_PIXELS))
        kinds.append("sheet")
        try:
            image_vectors = self.embedder.images(pictures)
            [text_vector] = self.embedder.texts([case["description"]])
        except EmbeddingUnavailable as exc:
            self.kb.update_case(case["id"], embedding_pending=1)
            return {"status": "pending", "why": str(exc)}
        rows = [(kind, self.embedder.image_model, None, len(v), v.tobytes()) for kind, v in zip(kinds, image_vectors)]
        rows.append(("description", self.embedder.text_model, None, len(text_vector), text_vector.tobytes()))
        self.kb.set_vectors(case["id"], rows)
        self.kb.update_case(case["id"], embedding_pending=0)
        return {"status": "stored", "vectors": [row[0] for row in rows]}

    def reembed_pending(self) -> dict:
        """Embed every case marked pending (Voyage was unreachable, or the opt-out not yet set)."""
        done, still = [], []
        for case in self.kb.cases("embedding_pending = 1 AND status != 'abandoned'"):
            outcome = self._embed(case)
            (done if outcome["status"] == "stored" else still).append(case["dn"])
            if outcome["status"] == "pending":
                return {"ok": True, "embedded": done, "pending": [c["dn"] for c in self.kb.cases(
                    "embedding_pending = 1 AND status != 'abandoned'")], "why": outcome["why"]}
        return {"ok": True, "embedded": done, "pending": still}

    # --- search and look up -------------------------------------------------------

    def search(self, text: str | None = None, like_case: str | None = None, filters: dict | None = None,
               limit: int = 5) -> dict:
        """Cases like a text or like another case, best first, as one-line summaries."""
        if not text and not like_case:
            raise SolidWorksError("Give text, like_case, or both.", code="KB_EMPTY_SEARCH")
        filters = search.check_filters(filters)
        limit = max(1, min(int(limit), 20))
        stored = self.kb.vectors()
        lists, searched, notes, exclude = [], [], [], None
        if like_case:
            case = self._case(like_case)
            exclude = case["id"]
            lists.append([i for i in self.kb.keyword_search(
                search.words(f"{case.get('description') or ''} {case.get('part_type') or ''}")) if i != exclude])
            searched.append("keywords")
            own = self.kb.case_vectors(case["id"])
            if own:
                import numpy as np

                as_vectors = {r["kind"]: np.frombuffer(r["data"], dtype=np.float32) for r in own}
                pictures = [v for k, v in as_vectors.items() if k in search.IMAGE_KINDS]
                lists.append(search.vector_ranking(pictures, stored, search.IMAGE_KINDS, exclude))
                if "description" in as_vectors:
                    lists.append(search.vector_ranking([as_vectors["description"]], stored, ("description",),
                                                       exclude))
                searched.append("drawing and description vectors")
            else:
                notes.append(f"{case['dn']} has no vectors yet (embedding pending): keywords only.")
        if text:
            lists.append(self.kb.keyword_search(search.words(text)))
            searched.append("keywords")
            try:
                [query] = self.embedder.texts([text], query=True)
                lists.append(search.vector_ranking([query], stored, ("description",), exclude))
                searched.append("text vectors")
            except EmbeddingUnavailable as exc:
                notes.append(f"Keywords only: {exc}.")
        scores = search.fuse(*lists)
        scores.pop(exclude, None)
        by_id = {c["id"]: c for c in self.kb.cases()}
        ranked = [i for i in search.order(by_id, {i: s for i, s in scores.items() if i in by_id})
                  if search.passes(by_id[i], filters)]
        results = [search.summary(by_id[i], self._lessons(by_id[i])) for i in ranked[:limit]]
        out = {"ok": True, "results": results, "searched": sorted(set(searched))}
        if notes:
            out["notes"] = notes
        if not results:
            out["notes"] = out.get("notes", []) + ["No earlier case matches."]
        return out

    def _lessons(self, case: dict) -> list:
        lessons = []
        if case.get("review_notes"):
            note = case["review_notes"].strip()
            lessons.append(note if len(note) <= 100 else note[:97] + "...")
        codes = [c for c in self.kb.error_codes(case["id"]) if c and not c.startswith("T:")]
        if codes:
            lessons.append("failed: " + ", ".join(codes[:3]))
        return lessons

    def get_case(self, dn: str, include_timeline: bool = False) -> dict:
        """Everything about one case; with include_timeline, every modelling step in order."""
        case = self._case(dn)
        folder = self.kb.case_folder(case["dn"])
        files = {name: str(folder / file) for name, file in
                 (("scan", "input.pdf"), ("step", f"{case['dn']}.step"), ("drawing_pdf", f"{case['dn']} drawing.pdf"))
                 if (folder / file).exists()}
        keep = ("dn", "status", "part_type", "description", "handwritten_dn", "spec", "drawing_check",
                "finalise_measurements", "review_measurements", "review_changes", "verdict", "review_notes",
                "abandoned_reason", "embedding_pending", "ingested_at", "described_at", "finalised_at", "reviewed_at")
        result = {"ok": True, **{key: case[key] for key in keep if case.get(key) not in (None, "")},
                  "files": files, "tools_used": self.kb.tools_used(case["id"]),
                  "spec_versions": len(case["spec_history"] or []) + (1 if case["spec"] else 0)}
        if "step" in files:
            result["note"] = "open_part on files.step opens the approved model for reference (no feature history)."
        if include_timeline:
            result["timeline"] = [
                {key: value for key, value in (("seq", e["seq"]), ("tool", e["tool"]), ("arguments", e["arguments"]),
                                               ("ok", bool(e["ok"])), ("error", e["error"]),
                                               ("error_code", e["error_code"]), ("measured", _measured(e["result"])),
                                               ("probe", e["probe"]))
                 if value is not None}
                for e in self.kb.events(case["id"], include_read_only=False)]
        return result

    def abandon_case(self, reason: str) -> dict:
        case = self.kb.abandon_open_case(reason)
        number = case["number"]
        return {"ok": True, "abandoned": case["dn"],
                "note": f"{dn_names.name(number)} is free again: ingest the right file to start it afresh."}

    # --- tool cards -------------------------------------------------------------------

    def _tool(self, tool: str) -> str:
        reference = self.tool_reference(tool)
        if reference is None or tool.startswith("kb_"):
            raise SolidWorksError(f"No modelling tool is called '{tool}'.", code="KB_NO_SUCH_TOOL")
        return reference

    def _signature_list(self, rows) -> list:
        return [{"signature": r["signature"], "count": r["count"], "error": r["sample"]} for r in rows]

    def tool_card(self, tool: str) -> dict:
        """One tool's card, and the failure signatures a rewrite keys its texts by."""
        how_to_call = self._tool(tool)
        card = self.kb.card(tool)
        active = cards.active_signatures(self.kb, tool)
        return {"ok": True, "tool": tool, "card": cards.render(tool, card, active, how_to_call),
                "signatures": self._signature_list(active)}

    def update_tool_card(self, tool: str, card: dict) -> dict:
        """Store a tidied card's fields: refused over the cap, or when a frequent failure lost its text."""
        how_to_call = self._tool(tool)
        known = {row["signature"] for row in self.kb.error_signatures(tool)}
        fields = cards.check(card, cards.must_cover(self.kb, tool), known)
        server_version, sw_revision = self.kb.latest_versions(tool) or (__version__, None)
        stored = self.kb.set_card(tool, fields, server_version, sw_revision)
        return {"ok": True, "tool": tool,
                "card": cards.render(tool, stored, cards.active_signatures(self.kb, tool), how_to_call)}

    def _cards_to_tidy(self, case: dict) -> list:
        """For finalise: each modelling tool the case used, its card, the failures the
        rewrite must cover, and what happened to it in this case."""
        packets = []
        for tool in self.kb.tools_used(case["id"]):
            if self.tool_reference(tool) is None:
                continue
            events = self.kb.case_tool_events(case["id"], tool)
            failures = [{key: e[key] for key in ("seq", "error", "error_code", "error_params", "probe", "signature")
                         if e[key] is not None} for e in events if not e["ok"]]
            card = self.kb.card(tool)
            packets.append({
                "tool": tool,
                "card": self.tool_card(tool)["card"],
                "fields": (card or {}).get("fields") or {},
                "must_cover": self._signature_list(cards.must_cover(self.kb, tool)),
                "this_case": {"calls": len(events), "failures": failures},
            })
        return packets
