"""The knowledge-base tools for a case: ingest, read, describe, search, look up, abandon, tool cards.

Each public method is one kb_* MCP tool (server.py wraps them in one block).
They return JSON-serialisable dicts; ingest_drawing and view_region return
(dict, JPEG bytes), which the server sends as text plus an image.
Design: Docs/KNOWLEDGE-BASE.md §3-§5, §7 and §11.
"""

import ntpath
import os
import re
import shutil

from .. import __version__
from ..errors import SolidWorksError
from . import cards, config, digest, hotspots, images, search
from . import dn as dn_names
from .files import atomic_write_json, read_json, read_text
from .store import KnowledgeBase

VIEWS = ("front", "top", "side")
SPEC_STATUSES = {"read", "unreadable"}


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


def _spec_summary(spec: dict) -> str:
    """'14 dimensions, 3 notes · 1 unreadable': the spec's items counted by kind."""
    kinds = {}
    for item in spec["items"]:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    text = ", ".join(f"{n} {kind}{'s' if n > 1 else ''}" for kind, n in kinds.items()) or "no items"
    unreadable = sum(1 for item in spec["items"] if item["status"] == "unreadable")
    return text + (f" · {unreadable} unreadable" if unreadable else "")


REVIEW_VERDICTS = {"approved": "approved", "changes needed": "changes_needed", "needs changes": "changes_needed"}


def _part_changes(before: dict | None, after: dict) -> list:
    """'part unchanged', or the volume and size changes since finalise (beyond measuring noise)."""
    if not before or "volume_mm3" not in after:
        return ["part: not compared (no finalise measurement)"]
    changes = []
    v0, v1 = before["volume_mm3"], after["volume_mm3"]
    if abs(v1 - v0) > max(1.0, 1e-4 * abs(v0)):
        changes.append(f"volume {v0:,.0f} → {v1:,.0f} mm³")
    s0, s1 = before.get("size_mm") or [], (after.get("bounding_box_mm") or {}).get("size_mm") or []
    if s0 and s1 and any(abs(a - b) > 0.01 for a, b in zip(s0, s1)):
        changes.append(f"bbox {' × '.join(f'{v:.1f}' for v in s0)} → {' × '.join(f'{v:.1f}' for v in s1)} mm")
    return ["part " + ", ".join(changes)] if changes else ["part unchanged"]


def _tools_used(rows: list) -> list:
    return sorted({r["tool"] for r in rows if not r["read_only"] and not r["tool"].startswith("kb_")})


class CaseTools:
    def __init__(self, kb: KnowledgeBase, tool_reference=None) -> None:
        self.kb = kb
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
        thumb.thumbnail((400, 400))
        thumb.save(folder / "thumb.png")
        self.kb.update_case(case, dpi=dpi)
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
                  "effective_dpi": round((case.get("dpi") or images.RENDER_DPI) * scale)}
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
        """Store what Claude read (case.md and spec.json), crop the views; return the unreadable items and INDEX.md."""
        case = self.kb.require_open_case()
        if case["status"] not in ("ingested", "described"):
            raise SolidWorksError(f"{case['dn']} is already {case['status']}: correct the spec with kb_update_spec.",
                                  code="KB_ALREADY_DESCRIBED")
        warnings = []
        handwritten = None
        if handwritten_dn:
            written = dn_names.from_written(handwritten_dn)
            if written is None:
                warnings.append(f"'{handwritten_dn}' does not read as a DN number; stored as written.")
                handwritten = handwritten_dn
            elif written != case["number"] and not dn_confirmed:
                raise SolidWorksError(
                    f"The paper says {dn_names.name(written)} but the file is {case['dn']}. Ask the user which is "
                    f"right. If the file name is, call kb_describe_case again with dn_confirmed=True; if not, "
                    f"abandon this case (kb_abandon_case), rename the file and ingest it again.",
                    code="KB_DN_MISMATCH", params={"file": case["dn"], "written": dn_names.name(written)})
            else:
                handwritten = f"{handwritten_dn} ✓" if written == case["number"] else f"{handwritten_dn} (file name confirmed)"
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
        atomic_write_json(folder / "spec.json", drawing_spec)
        views = " · ".join(f"{view} [{','.join(f'{v:.2f}' for v in box)}]" for view, box in boxes.items())
        spec_line = f"Spec: spec.json · {_spec_summary(drawing_spec)}" + (f" · handwritten DN: {handwritten}" if handwritten else "")
        page_no = case.get("page") or 1
        case = self.kb.set_status(case, "described", part_type=part_type.strip().lower(), sections={
            "Description": description.strip(), "Drawing": f"input.pdf page {page_no} · {views}\n{spec_line}"})
        result = {"ok": True, "dn": case["dn"], "views": sorted(boxes), "unreadable": unreadable,
                  "index": self.kb.index_text()}
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

    def update_spec(self, drawing_spec: dict, answers: dict | None = None) -> dict:
        """Replace the open case's spec.json; each question and the user's answer is listed in case.md."""
        case = self.kb.require_open_case()
        folder = self.kb.case_folder(case["dn"])
        if not (folder / "spec.json").is_file():
            raise SolidWorksError(f"{case['dn']} has no spec yet: give it with kb_describe_case.", code="KB_NO_SPEC")
        unreadable = check_spec(drawing_spec)
        if answers is not None and (not isinstance(answers, dict) or not all(
                isinstance(q, str) and isinstance(a, str) and q.strip() and a.strip() for q, a in answers.items())):
            raise SolidWorksError('answers is {"the question you asked": "the user\'s answer", ...}.',
                                  code="KB_BAD_ANSWERS")
        atomic_write_json(folder / "spec.json", drawing_spec)
        drawing = case["sections"].get("Drawing", "")
        handwritten = re.search(r" · (handwritten DN: .*)$", drawing, re.M)
        lines = [line for line in drawing.splitlines() if not line.startswith("Spec:")]
        lines.append(f"Spec: spec.json · {_spec_summary(drawing_spec)}" + (f" · {handwritten.group(1)}" if handwritten else ""))
        sections = {"Drawing": "\n".join(lines)}
        if answers:
            asked = [case["sections"].get("Questions asked", "")] if case["sections"].get("Questions asked") else []
            asked += [f"- {' '.join(q.split())} → **{' '.join(a.split())}** (you, {self.kb.today()})"
                      for q, a in answers.items()]
            sections["Questions asked"] = "\n".join(asked)
        self.kb.update_case(case, sections)
        return {"ok": True, "dn": case["dn"], "items": len(drawing_spec["items"]), "unreadable": unreadable,
                "answers_recorded": len(answers or {})}

    # --- search and look up -------------------------------------------------------

    def search(self, text: str | None = None, part_type: str | None = None, status=None, limit: int = 10) -> dict:
        """INDEX lines of the cases matching a text, a part type and a status."""
        return search.search(self.kb, text, part_type, status, limit)

    def get_case(self, dn: str) -> dict:
        """One case's case.md, and the files it has (the scan, the spec; once approved, the STEP and drawing PDF)."""
        case = self._case(dn)
        folder = self.kb.case_folder(case["dn"])
        files = {name: str(folder / file) for name, file in
                 (("scan", "input.pdf"), ("spec", "spec.json"), ("step", f"{case['dn']}.step"),
                  ("drawing_pdf", f"{case['dn']} drawing.pdf")) if (folder / file).exists()}
        result = {"ok": True, "dn": case["dn"], "status": case["status"], "case": read_text(folder / "case.md"),
                  "files": files}
        if "step" in files:
            result["note"] = "open_part on files.step opens the approved model for reference (no feature history)."
        return result

    def abandon_case(self, reason: str) -> dict:
        """Close the open case, write its digest, free its DN."""
        case = self.kb.abandon_open_case(reason)
        number = case["number"]
        result = {"ok": True, "abandoned": case["dn"],
                  "note": f"{dn_names.name(number)} is free again: ingest the right file to start it afresh."}
        try:
            written = digest.write_digest(self.kb, case["dn"])
        except Exception as exc:  # noqa: BLE001 - the case is closed either way; say what went wrong
            result["warning"] = f"The digest was not written: {type(exc).__name__}: {exc}"
            return result
        if written["written"] and written["hard_parts"]:
            result["hard_parts"] = written["hard_parts"]
            result["next"] = ("Explain each hard part with kb_explain_digest(explanations={'D1': '...'}): what you "
                              "were doing and why it took long, 40 words at most, 'not sure' is allowed.")
        return result

    def finalise_case(self, lessons: list, measured: dict | None = None) -> dict:
        """Check both files, record the part's measurements, the steps that worked and the
        lessons, close the case; write the digest; return the hard parts and the cards to tidy.

        `measured` is the saved part's get_mass_properties result, measured by the server
        before this runs (SolidWorks work stays out of the knowledge base)."""
        case = self.kb.require_open_case()
        if case["status"] != "modelling":
            raise SolidWorksError(f"{case['dn']} is {case['status']}: model the part and make the drawing first, "
                                  "or abandon the case (kb_abandon_case).", code="KB_NOT_MODELLED")
        if not isinstance(lessons, list) or not all(isinstance(x, str) and x.strip() for x in lessons):
            raise SolidWorksError('lessons is a list of short lines, e.g. ["Fillet before the holes."].',
                                  code="KB_BAD_LESSONS")
        if len(lessons) > 5:
            raise SolidWorksError(f"At most 5 lessons (got {len(lessons)}): keep the ones a later job needs most.",
                                  code="KB_BAD_LESSONS")
        folder = config.output_dir()
        missing = [name for name in (f"{case['dn']}.SLDPRT", f"{case['dn']}.SLDDRW") if not (folder / name).is_file()]
        if missing:
            raise SolidWorksError(f"Not saved in {folder}: {', '.join(missing)}. Call save_part and make_drawing "
                                  "without a path first.", code="KB_FILES_MISSING", params={"missing": missing})
        props = (measured or {}).get("mass_properties")
        if not props:
            reason = (measured or {}).get("error") or "the part was not measured"
            raise SolidWorksError(f"Could not measure {case['dn']}.SLDPRT: {reason}", code="KB_NOT_MEASURED")
        size = (props.get("bounding_box_mm") or {}).get("size_mm") or []
        size_text = " x ".join(f"{v:.1f}" for v in size) if size else None
        result_line = f"Volume {props['volume_mm3']:,.0f} mm³ · mass {props['mass_kg']:.3f} kg"
        if size_text:
            result_line += f" · bbox {size_text.replace(' x ', ' × ')} mm"
        _, rows = self.kb.read_timeline(case["dn"])
        steps = [f"{k}. {r['tool']}: {r['args']}" if r["args"] else f"{k}. {r['tool']}"
                 for k, r in enumerate((r for r in rows if r["ok"] and not r["read_only"]
                                        and not r["tool"].startswith("kb_")
                                        and r["tool"] not in ("save_part", "make_drawing")), start=1)]
        sections = {"Result": f"{result_line}\nFiles: {case['dn']}.SLDPRT, {case['dn']}.SLDDRW",
                    "Lessons": "\n".join(f"- {' '.join(x.split())}" for x in lessons)}
        if steps:
            sections["How it was modelled"] = "\n".join(steps)
        fields = {"finalised": self.kb.today()}
        if size_text:
            fields["size_mm"] = size_text
        atomic_write_json(self.kb.case_folder(case["dn"]) / "measured.json",
                          {"volume_mm3": props["volume_mm3"], "mass_kg": props["mass_kg"], "size_mm": list(size)})
        case = self.kb.set_status(case, "finalised", sections, **fields)
        result = {"ok": True, "finalised": case["dn"], "files": [str(folder / f"{case['dn']}{ext}")
                                                                 for ext in (".SLDPRT", ".SLDDRW")],
                  "measured": result_line, "cards_to_tidy": self._cards_to_tidy(case)}
        try:
            written = digest.write_digest(self.kb, case["dn"])
        except Exception as exc:  # noqa: BLE001 - the case is closed either way; say what went wrong
            result["warning"] = f"The digest was not written: {type(exc).__name__}: {exc}"
            written = {"written": False, "hard_parts": []}
        if written["written"] and written["hard_parts"]:
            result["hard_parts"] = written["hard_parts"]
        result["next"] = ("Explain each hard part with kb_explain_digest (if any), rewrite each card in "
                          "cards_to_tidy with kb_update_tool_card, then tell the user the files, the "
                          "check_drawing result and what to look at first.")
        return result

    # --- review ------------------------------------------------------------------------

    def review_files(self, dn: str, verdict: str, notes: str | None = None) -> dict:
        """Check a review can run; the files the server exports from and to, and the case's spec."""
        case = self._case(dn)
        verdict = REVIEW_VERDICTS.get(str(verdict).strip().lower().replace("_", " "))
        if verdict is None:
            raise SolidWorksError("verdict is 'approved' or 'changes_needed'.", code="KB_BAD_VERDICT")
        if case["status"] not in ("finalised", "approved", "changes_needed"):
            raise SolidWorksError(f"{case['dn']} is {case['status']}: only a finalised case is reviewed "
                                  "(kb_finalise_case first).", code="KB_NOT_FINALISED")
        if verdict == "changes_needed" and not (notes or "").strip():
            raise SolidWorksError("Say what the user changed (notes), e.g. 'the bore was Ø.750, not Ø.875'.",
                                  code="KB_NO_REVIEW_NOTES")
        out = config.output_dir()
        files = {"part": out / f"{case['dn']}.SLDPRT", "drawing": out / f"{case['dn']}.SLDDRW"}
        missing = [p.name for p in files.values() if not p.is_file()]
        if missing:
            raise SolidWorksError(f"Not in {out}: {', '.join(missing)}. The review needs the checked files back in "
                                  "the output folder.", code="KB_FILES_MISSING", params={"missing": missing})
        folder = self.kb.case_folder(case["dn"])
        return {"dn": case["dn"], "verdict": verdict, "part": str(files["part"]), "drawing": str(files["drawing"]),
                "step": str(folder / f"{case['dn']}.step"), "pdf": str(folder / f"{case['dn']} drawing.pdf"),
                "spec": read_json(folder / "spec.json")}

    def review_case(self, dn: str, verdict: str, notes: str | None = None, measured: dict | None = None) -> dict:
        """Record the user's verdict once the server exported the STEP and PDF from the checked files:
        what changed (measured by code), the note, and a lesson for changes_needed."""
        files = self.review_files(dn, verdict, notes)
        case = self._case(files["dn"])
        measured = measured or {}
        for side, label in (("part", "STEP"), ("drawing", "PDF")):
            got = measured.get(side) or {}
            if not got.get("ok"):
                raise SolidWorksError(f"The {label} was not exported from {case['dn']}'s checked {side}: "
                                      f"{got.get('error') or measured.get('error') or 'not run'}",
                                      code="KB_EXPORT_FAILED")
        for key in ("step", "pdf"):
            if not os.path.isfile(files[key]):
                raise SolidWorksError(f"{files[key]} was not written.", code="KB_EXPORT_FAILED")
        changes = _part_changes(read_json(self.kb.case_folder(case["dn"]) / "measured.json"),
                                measured["part"].get("mass_properties") or {})
        check = measured["drawing"].get("check")
        if check is None:
            changes.append("drawing: not compared (no spec)")
        elif "error" in check:
            changes.append(f"drawing: not compared ({check['error']})")
        else:
            counts = " · ".join(f"{n} {status}" for status, n in sorted(check["counts"].items()))
            changes.append(f"drawing against the spec: {counts or 'no items'}"
                           + (f" · {check['not_in_spec']} not in the spec" if check.get("not_in_spec") else ""))
        verdict = files["verdict"]
        note = " ".join((notes or "").split())
        heading = "**Approved**" if verdict == "approved" else "**Changes needed**"
        review = f"{heading} {self.kb.today()}." + (f' Your note: "{note}"' if note else "")
        review += "\nMeasured changes: " + " · ".join(changes)
        sections = {"Review": review}
        if verdict == "changes_needed":
            lessons = (case["sections"].get("Lessons") or "").splitlines()
            lessons = [line for line in lessons if not line.startswith("- Review: ")]
            sections["Lessons"] = "\n".join([f"- Review: {note}", *lessons])
        self.kb.set_status(case, verdict, sections, reviewed=self.kb.today())
        return {"ok": True, "dn": case["dn"], "verdict": verdict, "measured_changes": changes,
                "stored": [files["step"], files["pdf"]],
                "next": f"Stored {case['dn']}.step and {case['dn']} drawing.pdf. Tell the user the files can now "
                        "be moved out of the output folder."}

    def explain_digest(self, explanations: dict, not_measured: list | None = None, fix_ideas: dict | None = None,
                       dn: str | None = None) -> dict:
        """One line per hard part of a digest; links each to an issue and updates HOTSPOTS.md."""
        return hotspots.explain_digest(self.kb, explanations, not_measured, fix_ideas, dn)

    # --- tool cards -------------------------------------------------------------------

    def _tool(self, tool: str) -> str:
        reference = self.tool_reference(tool)
        if reference is None or tool.startswith("kb_"):
            raise SolidWorksError(f"No modelling tool is called '{tool}'.", code="KB_NO_SUCH_TOOL")
        return reference

    @staticmethod
    def _signature_list(rows) -> list:
        return [{"signature": r["signature"], "count": r["count"], "error": r["sample"]} for r in rows]

    def tool_card(self, tool: str) -> dict:
        """One tool's card, and the failure signatures a rewrite keys its texts by."""
        how_to_call = self._tool(tool)
        counted = cards.stats(self.kb, tool)
        active = cards.active_signatures(self.kb, tool, counted)
        return {"ok": True, "tool": tool, "card": cards.render(tool, cards.read(self.kb, tool), counted, active, how_to_call),
                "signatures": self._signature_list(active)}

    def update_tool_card(self, tool: str, card: dict) -> dict:
        """Store a tidied card's words: refused over the cap, or when a frequent failure lost its text."""
        how_to_call = self._tool(tool)
        counted = cards.stats(self.kb, tool)
        active = cards.active_signatures(self.kb, tool, counted)
        fields = cards.check(card, cards.must_cover(active), set(counted["sigs"]))
        server_version, sw_revision = counted["latest"] if counted["used"] else (__version__, None)
        cards.write(self.kb, tool, fields, how_to_call, server_version or __version__, sw_revision)
        return self.tool_card(tool)

    def _cards_to_tidy(self, case: dict) -> list:
        """For finalise: each modelling tool the case used, its card, the failures the
        rewrite must cover, and what happened to it in this case."""
        _, rows = self.kb.read_timeline(case["dn"])
        packets = []
        for tool in _tools_used(rows):
            if self.tool_reference(tool) is None:
                continue
            mine = [r for r in rows if r["tool"] == tool]
            failures = [{"seq": r["n"], "error": r["sample"], "signature": r["signature"]} for r in mine if not r["ok"]]
            counted = cards.stats(self.kb, tool)
            active = cards.active_signatures(self.kb, tool, counted)
            packets.append({
                "tool": tool,
                "card": self.tool_card(tool)["card"],
                "fields": (cards.read(self.kb, tool) or {}).get("fields") or {},
                "must_cover": self._signature_list(cards.must_cover(active)),
                "this_case": {"calls": len(mine), "failures": failures},
            })
        return packets
