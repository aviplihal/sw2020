"""Pure tests: the six file types of the knowledge base round-trip (write -> parse -> same), against the
templates in Docs/kb-templates, and every write is atomic (no SolidWorks)."""

import os
from datetime import datetime

import pytest
from kb_helpers import template

from solidworks_mcp.kb import cards, digest, files, hotspots, store, timeline

# --- atomic writes ------------------------------------------------------------------------------


def test_a_write_replaces_the_file_whole(tmp_path):
    target = tmp_path / "deep" / "case.md"
    files.atomic_write(target, "one\n")
    files.atomic_write(target, "two\n")
    assert target.read_text(encoding="utf-8") == "two\n"
    assert [p.name for p in target.parent.iterdir()] == ["case.md"], "no temporary file is left behind"


def test_a_crash_while_writing_leaves_the_old_file_and_no_debris(tmp_path, monkeypatch):
    target = tmp_path / "INDEX.md"
    files.atomic_write(target, "old\n")

    def crash(*args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError, match="disk full"):
        files.atomic_write(target, "new\n")
    assert target.read_text(encoding="utf-8") == "old\n"
    assert [p.name for p in tmp_path.iterdir()] == ["INDEX.md"]


# --- case.md ------------------------------------------------------------------------------------


def test_the_case_template_round_trips():
    text = template("case.md")
    case = store.parse_case(text)
    assert (case["dn"], case["status"], case["part_type"], case["size_mm"]) == ("DN17", "approved", "bracket",
                                                                              "76.2 x 50.8 x 38.1")
    assert list(case["sections"]) == list(store.SECTIONS)
    assert store.render_case(case) == text


def test_sections_come_out_in_their_fixed_order():
    case = {"dn": "DN3", "status": "ingested", "number": 3,
            "sections": {"Lessons": "- b", "Description": "a", "Questions asked": "- q"}}
    text = store.render_case(case)
    assert text.index("## Description") < text.index("## Questions asked") < text.index("## Lessons")
    assert store.parse_case(text)["sections"] == case["sections"]


def test_a_case_file_with_a_bad_header_is_refused_loudly():
    with pytest.raises(Exception, match="no header"):
        store.parse_case("# DN1\n")
    with pytest.raises(Exception, match="bad dn or status"):
        store.parse_case("---\ndn: DN1\nstatus: weird\n---\n# DN1\n")


# --- INDEX.md ----------------------------------------------------------------------------------


def _case(number, status, part="bracket", size="60 x 20 x 10", description="A part. More.", lessons=None):
    sections = {"Description": description}
    if lessons:
        sections["Lessons"] = f"- {lessons}"
    return {"dn": f"DN{number}", "number": number, "status": status, "part_type": part, "size_mm": size,
            "sections": sections}


def test_the_index_is_ordered_approved_then_changes_needed_then_unreviewed_and_leaves_out_abandoned():
    cases = [_case(18, "finalised"), _case(16, "changes_needed"), _case(17, "approved"), _case(15, "approved"),
             _case(9, "abandoned"), _case(19, "modelling", part="shaft")]
    text = store.render_index(cases)
    order = [line.split("|")[1].strip() for line in text.splitlines() if line.startswith("| DN") and line[4].isdigit()]
    assert order == ["DN17", "DN15", "DN16", "DN19", "DN18"]
    assert "5 cases · 2 approved · 1 changes needed · 2 unreviewed · part types: bracket 4, shaft 1" in text
    assert "DN9 " not in text
    marks = [line.split("|")[4].strip() for line in text.splitlines() if line.startswith("| DN") and line[4].isdigit()]
    assert marks == ["✓", "✓", "⚠", "?", "?"]


def test_an_index_line_has_the_shape_of_the_template():
    shaped = [line for line in template("INDEX.md").splitlines() if line.startswith("| DN1")]
    fine = _case(17, "approved", size="76.2 x 50.8 x 38.1", description="L-bracket, 2 cbore holes. Break edges.",
                 lessons="fillet the bend before the holes")
    assert store.index_line(fine) == "| DN17 | bracket | 76 | ✓ | L-bracket, 2 cbore holes · fillet the bend before the holes |"
    assert all(len(line.split("|")) == len(store.index_line(fine).split("|")) for line in shaped)
    assert store.index_line(_case(18, "finalised", description="Threaded shaft")).endswith("not reviewed yet |")


# --- timeline.md -------------------------------------------------------------------------------


def test_every_template_row_parses_and_renders_back():
    day = "2026-10-12"
    rows = [line for line in template("timeline.md").splitlines() if line.startswith("| ") and line[2].isdigit()]
    assert len(rows) == 13
    for line in rows:
        assert timeline.render_row(timeline.parse_row(line, day), day) == line


def test_a_timeline_round_trips_with_its_date_in_the_title():
    rows = [timeline.parse_row(line, "2026-10-12") for line in template("timeline.md").splitlines()
            if line.startswith("| ") and line[2].isdigit()]
    text = timeline.render("DN17", "2026-10-12", rows)
    assert text.startswith("# DN17 · timeline · 2026-10-12\nColumns: # · time · tool")
    day, parsed = timeline.parse(text)
    assert (day, parsed) == ("2026-10-12", rows)
    assert timeline.render("DN17", day, parsed) == text
    assert parsed[5]["pause"] == 1295 and timeline.render_row(parsed[5], day).endswith("1,295 ⏸ |")


def test_a_row_after_midnight_carries_its_date():
    row = {"n": 1, "when": datetime(2026, 10, 13, 0, 5, 0), "tool": "add_box", "read_only": False, "args": "",
           "ok": True, "outcome": "ok", "signature": None, "sample": None, "run": 1.0, "tok": 5, "pause": 0}
    line = timeline.render_row(row, "2026-10-12")
    assert "2026-10-13 00:05:00" in line and timeline.parse_row(line, "2026-10-12")["when"] == row["when"]


def test_cells_cannot_break_the_table():
    assert timeline.arguments_text({"a": "x|y\nz", "b": None, "c": [1, 2]}) == "a=x/y z, c=[1,2]"
    assert len(timeline.arguments_text({"spec": "word " * 100})) == timeline.MAX_ARGS


def test_result_tokens_count_text_at_four_characters_and_images_by_size():
    assert timeline.result_tokens({"ok": True}) == 3
    assert timeline.image_tokens(1500, 1250) == 2500
    import io

    from PIL import Image

    jpeg = io.BytesIO()
    Image.new("RGB", (300, 150)).save(jpeg, format="JPEG")
    png = io.BytesIO()
    Image.new("RGB", (60, 30)).save(png, format="PNG")
    assert timeline.image_size(jpeg.getvalue()) == (300, 150) and timeline.image_size(png.getvalue()) == (60, 30)
    assert timeline.result_tokens({"ok": True}, [jpeg.getvalue()]) == 3 + 60


# --- tool cards ----------------------------------------------------------------------------------


def test_the_tool_card_template_is_read_back():
    card = cards.parse(template("tool-card.md"))
    assert card["fields"]["use_when"].startswith("rounding edges of a finished body")
    assert card["fields"]["failures"] == {"FILLET_TOO_LARGE": "radius over the adjacent wall → use the largest "
                                                              "radius the error names, or fillet before shelling."}
    assert card["updated"] == "2026-10-14"


def test_a_card_renders_and_parses_to_the_same_fields():
    counted = {"used": 41, "failed": 6, "cases": 9, "latest": ("0.16.0", "28.5.0"), "sigs": {}}
    row = {"signature": "FILLET_TOO_LARGE", "count": 4, "sample": "x", "last_case": "DN17", "last_server_version": "0.16.0",
           "last_sw_revision": "28.5.0", "again": None}
    fields = {"use_when": "rounding edges", "dont_use_when": "a sketch outline",
              "failures": {"FILLET_TOO_LARGE": "radius over the wall (really) → smaller (see list_edges)"},
              "lessons": ["check the section view"]}
    stored = {"fields": fields, "server_version": "0.16.0", "sw_revision": "28.5.0", "updated": "2026-10-14"}
    text = cards.render("add_fillet", stored, counted, [row], "add_fillet(radius_mm): rounds edges")
    assert "used 41×, failed 6× in 9 cases" in text and text.endswith("- check the section view\n")
    assert "- `FILLET_TOO_LARGE` radius over the wall (really) → smaller (see list_edges) (4×, last DN17, 28.5.0)" in text
    assert cards.parse(text)["fields"] == fields


# --- digest.md and HOTSPOTS.md --------------------------------------------------------------------


def test_the_digest_template_round_trips():
    text = template("digest.md")
    parsed = digest.parse(text)
    assert (parsed["job"], parsed["date"]) == ("DN17", "2026-10-12")
    assert parsed["sessions"][0]["session_id"] == "efb18dde-4801-4a92-8324-975a981a78c7"
    assert [p["id"] for p in parsed["parts"]] == ["D1", "D2", "D3", "D4", "D5", "D6"]
    assert parsed["parts"][1]["issue"] == "H-002" and "✓ fixed by" in parsed["parts"][1]["link_note"]
    assert parsed["parts"][5]["not_measured"] and parsed["parts"][5]["claude"] is None
    assert digest.render_parts(parsed["head"], parsed["parts"]) == text


def test_a_hard_part_names_its_calls_and_cost_in_a_form_that_reads_back():
    part = {"id": "D1", "text": "`X` ×2 on add_box (calls 3, 5) · cost 1.5 min, 300 tok", "claude": None,
            "issue": None, "link_note": None, "not_measured": False}
    assert digest.part_calls(part) == [3, 5] and digest.part_cost(part) == (1.5, 300)
    assert digest.part_signature(part) == "X"
    assert digest.call_list([41, 42, 43]) == "calls 41–43" and digest.parse_calls("pause before call 7 (x)") == [7]
    assert digest.parse_calls("loop (calls 3–5)") == [3, 4, 5]


def test_the_hotspots_template_issues_read_back_and_render_to_the_same_text():
    text = template("HOTSPOTS.md")
    parsed = hotspots.parse(text)
    assert (parsed["updated"], parsed["after"]) == ("2026-10-14", "DN18")
    issues = {i["id"]: i for i in parsed["issues"]}
    assert list(issues) == ["H-005", "H-006", "H-004", "H-007", "H-002", "H-001"]
    assert issues["H-006"]["signatures"] == ["HOLE_FACE_NOT_FOUND"] and issues["H-007"]["minutes"] is None
    assert [e["job"] for e in issues["H-004"]["seen"]] == ["DN17", "DN16", "DN14", "DN11", "DN10", "DN9"]
    assert issues["H-002"]["status"] == "fixed" and issues["H-002"]["commit"] == "a1b2c3d"
    for issue in parsed["issues"]:
        block = "\n".join(text[text.index(f"**{issue['id']}**") - 6:].split("\n\n")[0].splitlines()[:len(
            hotspots.render_issue(issue).splitlines())])
        assert hotspots.render_issue(issue) == block, issue["id"]
