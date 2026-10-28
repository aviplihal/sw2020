"""Pure tests: the knowledge-base tools from ingest to search, on Markdown files (no SolidWorks, no network).

Drawings are made on the fly as PDFs."""

import ast
import asyncio
import inspect
import io
import json
import pathlib

import pytest
from kb_helpers import BOXES, SPEC, call, make_pdf, new_kb
from PIL import Image

from solidworks_mcp import server
from solidworks_mcp.errors import SolidWorksError
from solidworks_mcp.kb import images, log
from solidworks_mcp.kb.cases import CaseTools


@pytest.fixture
def tools(tmp_path):
    kb = new_kb(tmp_path / "kb")
    yield CaseTools(kb, server._tool_reference)
    log.use(None)


def ingest(tools, tmp_path, number, shape="bracket", **describe):
    tools.ingest_drawing(make_pdf(tmp_path / f"DN{number}.pdf", shape))
    args = {"view_boxes": BOXES, "description": f"An L {shape} with two holes. Second sentence.", "part_type": shape,
            "drawing_spec": SPEC, **describe}
    return tools.describe_case(**args)


def close(tools, status="approved", lessons=None):
    """Move the open case past finalise, as K2 and K5 will."""
    case = tools.kb.open_case()
    sections = {"Lessons": f"- {lessons}"} if lessons else None
    tools.kb.update_case(case, sections, status=status, size_mm="80 x 40 x 6")


# --- ingest and zoom ---------------------------------------------------------------


def test_ingest_renders_the_page_and_returns_an_overview(tools, tmp_path):
    result, jpeg = tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))
    assert (result["dn"], result["dpi"], result["warnings"]) == ("DN17", 200, [])
    assert result["page_px"] == [round(8.5 * 200), 11 * 200]
    assert jpeg[:2] == b"\xff\xd8" and len(jpeg) <= images.SHOWN_MAX_BYTES
    assert max(Image.open(io.BytesIO(jpeg)).size) <= images.SHOWN_MAX_SIDE
    folder = tools.kb.case_folder("DN17")
    assert all((folder / name).exists() for name in ("case.md", "input.pdf", "page.png", "thumb.png", "views"))
    case = tools.kb.open_case()
    assert (case["status"], case["dpi"], case["page"]) == ("ingested", 200, 1)


def test_ingest_refuses_a_wrong_name_or_page_and_opens_nothing(tools, tmp_path):
    with pytest.raises(SolidWorksError, match="not named DN<number>.pdf"):
        tools.ingest_drawing(make_pdf(tmp_path / "bracket.pdf"))
    with pytest.raises(SolidWorksError, match="The PDF has 2 page.s.; there is no page 3"):
        tools.ingest_drawing(make_pdf(tmp_path / "DN3.pdf", pages=2), 3)
    with pytest.raises(SolidWorksError, match="No file at"):
        tools.ingest_drawing(str(tmp_path / "DN4.pdf"))
    assert tools.kb.open_case() is None
    result, _ = tools.ingest_drawing(make_pdf(tmp_path / "DN3.pdf", pages=2), 2)
    assert result["page"] == 2


def test_a_zoomed_region_is_shown_at_full_resolution(tools, tmp_path):
    tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))
    small, jpeg = tools.view_region([0.1, 0.8, 0.6, 0.85])
    assert small["region_px"] == [850, 110] and small["effective_dpi"] == 200 and "note" not in small
    big, _ = tools.view_region([0, 0, 1, 1])
    assert big["effective_dpi"] < 150 and "smaller box" in big["note"]
    with pytest.raises(SolidWorksError, match="x0 < x1"):
        tools.view_region([0.5, 0, 0.4, 1])


def test_shown_images_shrink_until_they_fit():
    import random

    random.seed(1)
    noise = Image.frombytes("RGB", (3000, 3000), random.randbytes(3000 * 3000 * 3))
    jpeg, scale = images.shown(noise, max_bytes=200_000)
    assert len(jpeg) <= 200_000 and scale < images.SHOWN_MAX_SIDE / 3000


# --- describe -------------------------------------------------------------------------


def test_describe_writes_case_md_and_spec_json_and_returns_the_index(tools, tmp_path):
    result = ingest(tools, tmp_path, 17)
    assert [i["id"] for i in result["unreadable"]] == ["D7"] and "kb_update_spec" in result["next"]
    assert "| DN17 | bracket | — | ? | An L bracket with two holes · not reviewed yet |" in result["index"]
    case = tools.kb.open_case()
    assert (case["status"], case["part_type"]) == ("described", "bracket")
    assert case["sections"]["Description"] == "An L bracket with two holes. Second sentence."
    first, second = case["sections"]["Drawing"].splitlines()
    assert first == "input.pdf page 1 · front [0.10,0.15,0.50,0.48] · top [0.55,0.15,0.90,0.48]"
    assert second == "Spec: spec.json · 2 dimensions, 1 thread · 1 unreadable"
    folder = tools.kb.case_folder("DN17")
    assert json.loads((folder / "spec.json").read_text(encoding="utf-8")) == SPEC
    assert sorted(p.name for p in (folder / "views").iterdir()) == ["front.png", "top.png"]


@pytest.mark.parametrize("spec,message", [
    ({"items": []}, "needs \"units\""),
    ({"units": "in"}, "drawing_spec is"),
    ({"units": "in", "items": [{"id": "D1", "kind": "dimension"}]}, "status 'read' or 'unreadable'"),
    ({"units": "in", "items": [{"id": "D1", "kind": "x", "status": "read"}] * 2}, "the id 'D1' twice"),
])
def test_a_malformed_spec_is_refused(tools, tmp_path, spec, message):
    tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))
    with pytest.raises(SolidWorksError, match=message):
        tools.describe_case(BOXES, "a part", "bracket", spec)
    assert tools.kb.open_case()["status"] == "ingested"
    assert not (tools.kb.case_folder("DN17") / "spec.json").exists()


def test_a_handwritten_dn_that_differs_stops_until_confirmed(tools, tmp_path):
    tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))
    with pytest.raises(SolidWorksError, match="The paper says DN71 but the file is DN17") as refused:
        tools.describe_case(BOXES, "a part", "bracket", SPEC, handwritten_dn="DN 71")
    assert refused.value.code == "KB_DN_MISMATCH"
    assert tools.describe_case(BOXES, "a part", "bracket", SPEC, handwritten_dn="DN 71", dn_confirmed=True)["ok"]
    assert tools.kb.open_case()["sections"]["Drawing"].endswith("handwritten DN: DN 71 (file name confirmed)")


def test_the_same_handwritten_dn_passes_and_an_odd_one_is_a_warning(tools, tmp_path):
    tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))
    assert "warnings" not in tools.describe_case(BOXES, "a part", "bracket", SPEC, handwritten_dn="dn-017")
    assert tools.kb.open_case()["sections"]["Drawing"].endswith("handwritten DN: dn-017 ✓")
    assert "does not read as a DN number" in tools.describe_case(BOXES, "a part", "bracket", SPEC,
                                                                 handwritten_dn="smudge")["warnings"][0]


@pytest.mark.parametrize("boxes,message", [({}, "at least one"), ({"iso": [0, 0, 1, 1]}, "takes front, top, side"),
                                           ({"front": [0, 0, 2, 1]}, "within 0..1")])
def test_bad_view_boxes_are_refused(tools, tmp_path, boxes, message):
    tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))
    with pytest.raises(SolidWorksError, match=message):
        tools.describe_case(boxes, "a part", "bracket", SPEC)


def test_update_spec_replaces_spec_json_and_lists_each_question_and_answer(tools, tmp_path):
    ingest(tools, tmp_path, 17)
    answered = {"units": "in", "items": [dict(i, value=".250", status="read") if i["id"] == "D7" else i
                                         for i in SPEC["items"]]}
    result = tools.update_spec(answered, {"Slot width: .250 or .205?": ".250"})
    assert (result["unreadable"], result["answers_recorded"]) == ([], 1)
    case = tools.kb.open_case()
    assert json.loads((tools.kb.case_folder("DN17") / "spec.json").read_text(encoding="utf-8")) == answered
    assert case["sections"]["Questions asked"] == "- Slot width: .250 or .205? → **.250** (you, 2026-10-12)"
    assert case["sections"]["Drawing"].splitlines()[1] == "Spec: spec.json · 2 dimensions, 1 thread"
    tools.update_spec(answered, {"Second question": "yes"})
    assert tools.kb.open_case()["sections"]["Questions asked"].count("\n- ") == 1, "answers are added, not replaced"
    assert [r.split("|")[1].strip() for r in tools.search("250")["results"]] == ["DN17"], "the new spec is searchable"
    with pytest.raises(SolidWorksError, match="answers is"):
        tools.update_spec(answered, {"q": ""})


def test_update_spec_needs_a_described_case(tools, tmp_path):
    tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))
    with pytest.raises(SolidWorksError, match="has no spec yet"):
        tools.update_spec(SPEC)


# --- search, look up, abandon -----------------------------------------------------------


def test_search_returns_index_lines_approved_first(tools, tmp_path):
    ingest(tools, tmp_path, 1, description="bracket with M6 holes")
    close(tools, "finalised")
    ingest(tools, tmp_path, 2, description="bracket with M6 holes")
    close(tools, "changes_needed")
    ingest(tools, tmp_path, 3, description="plate, also M6")
    close(tools, "approved", lessons="M6 clearance is 6.6")
    found = tools.search("m6")
    assert [r.split("|")[1].strip() for r in found["results"]] == ["DN3", "DN2", "DN1"]
    assert found["results"][0].endswith("| plate, also M6 · M6 clearance is 6.6 |") and "| ⚠ |" in found["results"][1]
    assert len(tools.search("m6", status="approved")["results"]) == 1


def test_get_case_returns_the_case_file_and_the_files_it_has(tools, tmp_path):
    ingest(tools, tmp_path, 17)
    case = tools.get_case("dn17")
    assert case["dn"] == "DN17" and case["case"].startswith("---\ndn: DN17\nstatus: described\n")
    assert set(case["files"]) == {"scan", "spec"} and "## Description" in case["case"]
    assert "timeline" not in json.dumps(case)
    with pytest.raises(SolidWorksError, match="No case DN99"):
        tools.get_case("DN99")


def test_abandon_frees_the_dn_and_re_ingest_works(tools, tmp_path):
    tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))
    result = tools.abandon_case("wrong scan")
    assert (result["abandoned"], result["note"]) == (
        "DN17~1", "DN17 is free again: ingest the right file to start it afresh.")
    assert tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))[0]["dn"] == "DN17"
    assert tools.get_case("DN17~1")["status"] == "abandoned"


# --- the MCP side ---------------------------------------------------------------------


def _kb_delegations():
    source = pathlib.Path("src/solidworks_mcp/server.py").read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("kb_"):
            call_ = next(n for n in ast.walk(node) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_kb_call")
            method, *passed = call_.args
            yield node.name, [a.arg for a in node.args.args], method.value, [a.id for a in passed]


@pytest.mark.parametrize("tool,params,method,passed", list(_kb_delegations()))
def test_kb_tools_forward_their_arguments_in_order(tool, params, method, passed):
    assert method == tool.removeprefix("kb_")
    assert passed == params, f"{tool} forwards {passed} but takes {params}"
    assert list(inspect.signature(getattr(CaseTools, method)).parameters)[1:] == params


def test_the_tool_list_is_the_one_in_the_design():
    names = {tool for tool, *_ in _kb_delegations()}
    assert names == {"kb_ingest_drawing", "kb_view_region", "kb_describe_case", "kb_update_spec", "kb_search",
                     "kb_get_case", "kb_abandon_case", "kb_explain_digest", "kb_tool_card", "kb_update_tool_card"}
    assert list(inspect.signature(server.kb_search).parameters) == ["text", "part_type", "status", "limit"]
    assert list(inspect.signature(server.kb_get_case).parameters) == ["dn"]
    assert list(inspect.signature(server.kb_explain_digest).parameters) == ["explanations", "not_measured",
                                                                            "fix_ideas", "dn"]


def test_nothing_needs_voyage_or_numpy():
    import tomllib

    extra = tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert not any("voyage" in dep or "numpy" in dep for dep in extra["kb"])
    sources = "\n".join(p.read_text(encoding="utf-8") for p in pathlib.Path("src/solidworks_mcp").rglob("*.py"))
    assert "import numpy" not in sources and "voyageai" not in sources
    assert not (pathlib.Path("src/solidworks_mcp/kb/embed.py")).exists()


def test_the_server_sends_a_zoomed_region_as_an_image_and_logs_the_call(tools, tmp_path, monkeypatch):
    tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))
    monkeypatch.setattr(server, "_kb_tools", tools)
    content = asyncio.run(server.mcp.call_tool("kb_view_region", {"box": [0, 0, 0.5, 0.5]}))
    blocks = content[0] if isinstance(content, tuple) else content
    assert [b.type for b in blocks] == ["text", "image"] and blocks[1].mimeType == "image/jpeg"
    assert '"dn": "DN17"' in blocks[0].text
    [row] = tools.kb.read_timeline("DN17")[1]
    assert (row["tool"], row["read_only"], row["args"]) == ("kb_view_region", True, "box=[0,0,0.5,0.5]")
    assert row["outcome"].startswith("ok · ") and row["tok"] > 500, "the zoom's image tokens are counted"


def test_a_kb_tool_failure_comes_back_as_an_error_and_is_logged(tools, monkeypatch):
    monkeypatch.setattr(server, "_kb_tools", tools)
    assert asyncio.run(server._kb_call("view_region", [0, 0, 1, 1], None)) == {
        "ok": False, "error": "No knowledge-base case is open. Start one with kb_ingest_drawing."}
    [row] = tools.kb.read_timeline("_unassigned")[1]
    assert (row["tool"], row["ok"], row["signature"]) == ("kb_view_region", False, "KB_NO_OPEN_CASE")


def test_ingest_describe_and_abandon_are_logged_into_their_case(tools, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_kb_tools", tools)
    asyncio.run(server._kb_call("ingest_drawing", make_pdf(tmp_path / "DN17.pdf"), 1))
    asyncio.run(server._kb_call("abandon_case", "wrong scan"))
    call("get_status", 0)
    assert [r["tool"] for r in tools.kb.read_timeline("DN17~1")[1]] == ["kb_ingest_drawing", "kb_abandon_case"]
    assert [r["tool"] for r in tools.kb.read_timeline("_unassigned")[1]] == ["get_status"]
