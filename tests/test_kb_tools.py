"""Pure tests: the knowledge-base tools from ingest to search (no SolidWorks, no Voyage).

Drawings are made on the fly as PDFs; a fake embedder turns images and texts
into vectors that are close when the inputs look alike.
"""

import ast
import asyncio
import hashlib
import inspect
import pathlib
import re

import numpy as np
import pytest
from PIL import Image, ImageDraw

from solidworks_mcp import server
from solidworks_mcp.errors import SolidWorksError
from solidworks_mcp.kb import embed, images, log
from solidworks_mcp.kb.cases import CaseTools
from solidworks_mcp.kb.embed import EmbeddingUnavailable
from solidworks_mcp.kb.store import KnowledgeBase


class FakeEmbedder(embed.Embedder):
    """Images: their 16 x 16 grey thumbnail. Texts: a bag of hashed words. Both unit length."""

    def __init__(self):
        self.down = None  # set to a reason to act like Voyage being unreachable
        self.calls = 0

    def images(self, pictures, query=False):
        self._check()
        return [embed.unit(np.asarray(p.convert("L").resize((16, 16)), dtype=np.float32).ravel() - 128)
                for p in pictures]

    def texts(self, texts, query=False):
        self._check()
        out = []
        for text in texts:
            v = np.zeros(64, dtype=np.float32)
            for word in re.findall(r"\w+", text.lower()):
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1
            out.append(embed.unit(v))
        return out

    def _check(self):
        self.calls += 1
        if self.down:
            raise EmbeddingUnavailable(self.down)


def make_pdf(path, shape="bracket", pages=1):
    """A letter-size drawing: a front view on the left, a top view on the right."""
    sheets = []
    for _ in range(pages):
        sheet = Image.new("RGB", (850, 1100), "white")
        draw = ImageDraw.Draw(sheet)
        if shape == "bracket":
            draw.polygon([(100, 200), (400, 200), (400, 260), (160, 260), (160, 500), (100, 500)], outline="black",
                         width=4)
        else:  # a round flange
            draw.ellipse([100, 200, 400, 500], outline="black", width=4)
            draw.ellipse([220, 320, 280, 380], outline="black", width=4)
        draw.rectangle([500, 200, 750, 500], outline="black", width=4)
        draw.text((100, 900), "DN NOTE: BREAK ALL SHARP EDGES .010 MAX", fill="black")
        sheets.append(sheet)
    sheets[0].save(path, save_all=True, append_images=sheets[1:], resolution=100)
    return str(path)


SPEC = {"units": "in", "items": [
    {"id": "D1", "kind": "dimension", "value": ".375", "decimals": 3, "status": "read"},
    {"id": "T1", "kind": "thread", "callout": "1/4-20 UNC-2B", "status": "read"},
    {"id": "D7", "kind": "dimension", "value": None, "between": "slot width", "status": "unreadable"},
]}
BOXES = {"front": [0.1, 0.15, 0.5, 0.48], "top": [0.55, 0.15, 0.9, 0.48]}


@pytest.fixture
def tools(tmp_path):
    kb = KnowledgeBase(tmp_path / "kb")
    yield CaseTools(kb, FakeEmbedder())
    kb.close()


def ingest(tools, tmp_path, number, shape="bracket", **describe):
    tools.ingest_drawing(make_pdf(tmp_path / f"DN{number}.pdf", shape))
    args = {"view_boxes": BOXES, "description": f"An L {shape} with two holes", "part_type": shape,
            "drawing_spec": SPEC, **describe}
    return tools.describe_case(**args)


def close(tools, status="approved", notes=None):
    """Move the open case past finalise, as K2 and K5 will."""
    case = tools.kb.open_case()
    tools.kb.update_case(case["id"], status="finalised", is_open=0)
    if status != "finalised":
        tools.kb.update_case(case["id"], status=status, review_notes=notes)
        tools.reindex(tools.kb.case(case["dn"]))


# --- ingest and zoom ---------------------------------------------------------------


def test_ingest_renders_the_page_and_returns_an_overview(tools, tmp_path):
    result, jpeg = tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))
    assert (result["dn"], result["dpi"], result["warnings"]) == ("DN17", 200, [])
    assert result["page_px"] == [round(8.5 * 200), 11 * 200]
    assert jpeg[:2] == b"\xff\xd8" and len(jpeg) <= images.SHOWN_MAX_BYTES
    assert max(Image.open(__import__("io").BytesIO(jpeg)).size) <= images.SHOWN_MAX_SIDE
    folder = tools.kb.case_folder("DN17")
    assert all((folder / name).exists() for name in ("input.pdf", "page.png", "thumb.png", "views"))
    assert tools.kb.open_case()["dpi"] == 200


def test_ingest_refuses_a_wrong_name_or_page(tools, tmp_path):
    with pytest.raises(SolidWorksError, match="not named DN<number>.pdf"):
        tools.ingest_drawing(make_pdf(tmp_path / "bracket.pdf"))
    with pytest.raises(SolidWorksError, match="The PDF has 2 page.s.; there is no page 3"):
        tools.ingest_drawing(make_pdf(tmp_path / "DN3.pdf", pages=2), 3)
    with pytest.raises(SolidWorksError, match="No file at"):
        tools.ingest_drawing(str(tmp_path / "DN4.pdf"))
    assert tools.kb.open_case() is None, "a refused ingest opens no case"
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
    with pytest.raises(SolidWorksError, match="fractions of the page"):
        tools.view_region([10, 20, 30])


def test_a_box_gets_a_margin_but_stays_on_the_page():
    assert images.with_margin((0.2, 0.2, 0.4, 0.6)) == pytest.approx((0.18, 0.16, 0.42, 0.64))
    assert images.with_margin((0.0, 0.0, 1.0, 1.0)) == (0.0, 0.0, 1.0, 1.0)


def test_shown_images_shrink_until_they_fit():
    noise = Image.fromarray(np.random.default_rng(1).integers(0, 255, (3000, 3000, 3), dtype=np.uint8))
    jpeg, scale = images.shown(noise, max_bytes=200_000)
    assert len(jpeg) <= 200_000 and scale < images.SHOWN_MAX_SIDE / 3000


# --- describe ---------------------------------------------------------------------


def test_describe_stores_the_case_and_returns_the_unreadable_items(tools, tmp_path):
    result = ingest(tools, tmp_path, 17)
    assert [i["id"] for i in result["unreadable"]] == ["D7"] and "kb_update_spec" in result["next"]
    assert result["embedding"] == {"status": "stored",
                                   "vectors": ["view_front", "view_top", "sheet", "description"]}
    case = tools.kb.open_case()
    assert (case["status"], case["part_type"], case["spec"]) == ("described", "bracket", SPEC)
    views = tools.kb.case_folder("DN17") / "views"
    assert sorted(p.name for p in views.iterdir()) == ["front.png", "top.png"]


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


def test_a_handwritten_dn_that_differs_stops_until_confirmed(tools, tmp_path):
    tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))
    with pytest.raises(SolidWorksError, match="The paper says DN71 but the file is DN17") as refused:
        tools.describe_case(BOXES, "a part", "bracket", SPEC, handwritten_dn="DN 71")
    assert refused.value.code == "KB_DN_MISMATCH"
    assert tools.describe_case(BOXES, "a part", "bracket", SPEC, handwritten_dn="DN 71", dn_confirmed=True)["ok"]
    assert tools.kb.open_case()["handwritten_dn"] == "DN 71"


def test_the_same_handwritten_dn_passes_and_an_odd_one_is_a_warning(tools, tmp_path):
    tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))
    assert "warnings" not in tools.describe_case(BOXES, "a part", "bracket", SPEC, handwritten_dn="dn-017")
    result = tools.describe_case(BOXES, "a part", "bracket", SPEC, handwritten_dn="smudge")
    assert "does not read as a DN number" in result["warnings"][0]


@pytest.mark.parametrize("boxes,message", [({}, "at least one"), ({"iso": [0, 0, 1, 1]}, "takes front, top, side"),
                                           ({"front": [0, 0, 2, 1]}, "within 0..1")])
def test_bad_view_boxes_are_refused(tools, tmp_path, boxes, message):
    tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))
    with pytest.raises(SolidWorksError, match=message):
        tools.describe_case(boxes, "a part", "bracket", SPEC)


def test_update_spec_keeps_the_earlier_versions(tools, tmp_path):
    ingest(tools, tmp_path, 17)
    answered = {"units": "in", "items": [dict(i, value=".250", status="read") if i["id"] == "D7" else i
                                         for i in SPEC["items"]]}
    result = tools.update_spec(answered)
    assert (result["unreadable"], result["spec_versions"]) == ([], 2)
    case = tools.kb.open_case()
    assert case["spec"] == answered and case["spec_history"] == [SPEC]
    assert tools.search(text="250")["results"][0]["dn"] == "DN17", "the new spec is searchable"


# --- embedding pending ---------------------------------------------------------------


def test_without_voyage_the_case_is_kept_and_caught_up_later(tools, tmp_path):
    tools.embedder.down = "nothing was sent to Voyage: set SWMCP_VOYAGE_OPTED_OUT=1"
    result = ingest(tools, tmp_path, 17)
    assert result["embedding"]["status"] == "pending" and "SWMCP_VOYAGE_OPTED_OUT" in result["embedding"]["why"]
    assert tools.kb.open_case()["embedding_pending"] == 1 and tools.kb.case_vectors(1) == []
    assert tools.reembed_pending()["pending"] == ["DN17"], "still down: still pending"
    tools.embedder.down = None
    assert tools.reembed_pending() == {"ok": True, "embedded": ["DN17"], "pending": []}
    assert len(tools.kb.case_vectors(1)) == 4 and tools.kb.case("DN17")["embedding_pending"] == 0


def test_the_voyage_embedder_sends_nothing_before_the_opt_out(monkeypatch):
    monkeypatch.delenv("SWMCP_VOYAGE_OPTED_OUT", raising=False)
    monkeypatch.setenv("VOYAGE_API_KEY", "key")
    voyage = embed.VoyageEmbedder()
    with pytest.raises(EmbeddingUnavailable, match="nothing was sent to Voyage: set SWMCP_VOYAGE_OPTED_OUT=1"):
        voyage.texts(["a bracket"])
    assert voyage._client is None
    monkeypatch.setenv("SWMCP_VOYAGE_OPTED_OUT", "1")
    monkeypatch.delenv("VOYAGE_API_KEY")
    with pytest.raises(EmbeddingUnavailable, match="VOYAGE_API_KEY is not set"):
        voyage.images([Image.new("RGB", (4, 4))])


# --- search -------------------------------------------------------------------------


def test_describe_finds_the_earlier_case_that_looks_alike(tools, tmp_path):
    ingest(tools, tmp_path, 10, shape="flange", description="A round flange with a bore")
    close(tools)
    ingest(tools, tmp_path, 11, shape="bracket", description="Steel angle with holes")
    close(tools)
    result = ingest(tools, tmp_path, 12, shape="bracket", description="Steel angle with slots")
    assert [r["dn"] for r in result["similar_cases"]] == ["DN11", "DN10"]


def test_approved_cases_rank_above_others_and_others_carry_a_caution(tools, tmp_path):
    ingest(tools, tmp_path, 1, description="bracket with M6 holes")
    close(tools, status="finalised")
    ingest(tools, tmp_path, 2, description="bracket with M6 holes")
    close(tools, status="changes_needed", notes="the bore was .750, not .875")
    ingest(tools, tmp_path, 3, description="plate, also M6")
    close(tools, status="approved")
    results = tools.search(text="bracket M6")["results"]
    assert [r["dn"] for r in results] == ["DN3", "DN2", "DN1"]
    assert "caution" not in results[0]
    assert "needed correcting" in results[1]["caution"] and "NEEDED CHANGES" in results[1]["summary"]
    assert "lessons: the bore was .750, not .875" in results[1]["summary"]
    assert "not reviewed yet" in results[2]["caution"]


def test_filters_and_abandoned_cases(tools, tmp_path):
    ingest(tools, tmp_path, 1, shape="flange")
    close(tools)
    ingest(tools, tmp_path, 2, shape="bracket")
    tools.kb.update_case(1, finalise_measurements={"mass_properties": {"bounding_box_mm": {"size_mm": [80, 40, 6]}}})
    close(tools, status="changes_needed")
    tools.ingest_drawing(make_pdf(tmp_path / "DN3.pdf"))
    tools.describe_case(BOXES, "bracket for flange", "bracket", SPEC)
    tools.abandon_case("wrong scan")
    found = lambda **f: [r["dn"] for r in tools.search(text="flange bracket", filters=f)["results"]]  # noqa: E731
    assert found() == ["DN1", "DN2"], "the abandoned DN3 is never shown"
    assert found(status=["changes_needed"]) == ["DN2"]
    assert found(part_type="FLANGE") == ["DN1"]
    assert found(size_mm=[50, 100]) == ["DN1"] and found(size_mm=[100, 200]) == []
    with pytest.raises(SolidWorksError, match="Unknown filter"):
        tools.search(text="x", filters={"colour": "red"})


def test_search_needs_something_to_look_for(tools):
    with pytest.raises(SolidWorksError, match="Give text, like_case, or both"):
        tools.search()


def test_text_search_falls_back_to_keywords_without_voyage(tools, tmp_path):
    ingest(tools, tmp_path, 5, description="shaft with a keyway")
    close(tools)
    tools.embedder.down = "VOYAGE_API_KEY is not set"
    result = tools.search(text="keyway")
    assert [r["dn"] for r in result["results"]] == ["DN5"] and "Keywords only" in result["notes"][0]


def test_rank_fusion_adds_reciprocal_ranks():
    from solidworks_mcp.kb.search import fuse
    scores = fuse(["a", "b"], ["b", "c"])
    assert scores == pytest.approx({"a": 1 / 61, "b": 1 / 62 + 1 / 61, "c": 1 / 62})


# --- look up and abandon ------------------------------------------------------------


def test_get_case_gives_the_timeline_of_modelling_steps(tools, tmp_path):
    log.use(tools.kb)
    try:
        ingest(tools, tmp_path, 17)
        log.record_call("add_box", {"width_mm": 10}, {"ok": True, "mass_properties": {
            "volume_mm3": 1000, "bounding_box_mm": {"size_mm": [10, 10, 10]}, "density": 1}}, None)
        log.record_call("list_faces", {}, {"ok": True}, None)
        error = SolidWorksError("too big", code="FILLET_TOO_LARGE")
        log.record_call("add_fillet", {"radius_mm": 9}, {"ok": False, "error": "too big"}, error)
    finally:
        log.use(None)
    case = tools.get_case("DN17", include_timeline=True)
    assert [(s["seq"], s["tool"], s["ok"]) for s in case["timeline"]] == [(1, "add_box", True), (3, "add_fillet", False)]
    assert case["timeline"][0]["measured"] == {"volume_mm3": 1000, "bounding_box_mm": {"size_mm": [10, 10, 10]}}
    assert case["timeline"][1]["error_code"] == "FILLET_TOO_LARGE"
    assert case["status"] == "modelling" and case["tools_used"] == ["add_box", "add_fillet"]
    assert set(case["files"]) == {"scan"} and "timeline" not in tools.get_case("17")
    with pytest.raises(SolidWorksError, match="No case DN99"):
        tools.get_case("DN99")


def test_abandon_frees_the_dn(tools, tmp_path):
    tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))
    assert tools.abandon_case("wrong scan") == {
        "ok": True, "abandoned": "DN17~1", "note": "DN17 is free again: ingest the right file to start it afresh."}
    assert tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))[0]["dn"] == "DN17"


# --- the MCP side ---------------------------------------------------------------------


def _kb_delegations():
    source = pathlib.Path("src/solidworks_mcp/server.py").read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("kb_"):
            call = next(n for n in ast.walk(node) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_kb_call")
            method, *passed = call.args
            yield node.name, [a.arg for a in node.args.args], method.value, [a.id for a in passed]


@pytest.mark.parametrize("tool,params,method,passed", list(_kb_delegations()))
def test_kb_tools_forward_their_arguments_in_order(tool, params, method, passed):
    assert method == tool.removeprefix("kb_")
    assert passed == params, f"{tool} forwards {passed} but takes {params}"
    assert list(inspect.signature(getattr(CaseTools, method)).parameters)[1:] == params


def test_the_server_sends_a_zoomed_region_as_an_image(tools, tmp_path, monkeypatch):
    tools.ingest_drawing(make_pdf(tmp_path / "DN17.pdf"))
    monkeypatch.setattr(server, "_kb_tools", tools)
    content = asyncio.run(server.mcp.call_tool("kb_view_region", {"box": [0, 0, 0.5, 0.5]}))
    blocks = content[0] if isinstance(content, tuple) else content
    assert [b.type for b in blocks] == ["text", "image"] and blocks[1].mimeType == "image/jpeg"
    assert '"dn": "DN17"' in blocks[0].text


def test_a_kb_tool_failure_comes_back_as_an_error(tools, monkeypatch):
    monkeypatch.setattr(server, "_kb_tools", tools)
    assert asyncio.run(server._kb_call("view_region", [0, 0, 1, 1], None)) == {
        "ok": False, "error": "No knowledge-base case is open. Start one with kb_ingest_drawing."}
