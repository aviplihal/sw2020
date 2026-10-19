"""Pure tests: tool cards, kept short and honest by the server (no SolidWorks, no Voyage)."""

import pytest

from solidworks_mcp import server
from solidworks_mcp.errors import SolidWorksError
from solidworks_mcp.kb import cards, log
from solidworks_mcp.kb.cases import CaseTools
from solidworks_mcp.kb.store import KnowledgeBase


@pytest.fixture
def tools(tmp_path):
    kb = KnowledgeBase(tmp_path / "kb")
    log.use(kb)
    yield CaseTools(kb, embedder=None, tool_reference=server._tool_reference)
    log.use(None)
    kb.close()


def fail(tool="add_fillet", code="FILLET_REFUSED", message="R5 does not round all 4 edge(s)", revision="28.5.0"):
    error = SolidWorksError(message, code=code)
    return log.record_call(tool, {"radius_mm": 5}, {"ok": False, "error": message}, error, revision)


def work(tool="add_fillet", revision="28.5.0", times=1):
    for _ in range(times):
        log.record_call(tool, {"radius_mm": 1}, {"ok": True}, None, revision)


def test_how_to_call_comes_from_the_server_code():
    line = server._tool_reference("add_fillet")
    assert line.startswith("add_fillet(radius_mm, edges='all', name='Fillet', ")
    assert line.endswith(": " + server.add_fillet.__doc__.strip().split("\n")[0])
    assert server._tool_reference("kb_search") is None and server._tool_reference("no_such_tool") is None
    assert server._tool_reference("_call") is None


def test_a_tool_without_a_card_shows_its_raw_failures(tools):
    fail()
    fail()
    result = tools.tool_card("add_fillet")
    assert "no card yet" in result["card"] and "How to call:    add_fillet(radius_mm" in result["card"]
    assert "(not tidied yet) R5 does not round all 4 edge(s)    (seen 2×" in result["card"]
    assert result["signatures"] == [{"signature": "FILLET_REFUSED", "count": 2,
                                     "error": "R5 does not round all 4 edge(s)"}]
    with pytest.raises(SolidWorksError, match="No modelling tool is called 'kb_search'"):
        tools.tool_card("kb_search")


def test_a_tidied_card_is_rendered_with_server_counted_numbers(tools):
    tools.kb.ingest(17, "DN17.pdf", 1)
    fail()
    fail(code=None, message="SolidWorks COM error: Fillet3 failed")
    sig = tools.kb.error_signatures("add_fillet")[1]["signature"]
    card = {"use_when": "Rounding convex or concave edges of one body.",
            "dont_use_when": "A rib's top: use add_full_round.",
            "failures": {"FILLET_REFUSED": "Radius larger than the thinnest wall -> use the largest R the error names.",
                         sig: "Radius larger than the thinnest wall -> use the largest R the error names."},
            "lessons": ["Check fillet radii against the section view."]}
    text = tools.update_tool_card("add_fillet", card)["card"]
    assert text.startswith("add_fillet    (server ") and "SOLIDWORKS 28.5.0, updated" in text
    assert "  - Radius larger than the thinnest wall -> use the largest R the error names.    " \
           "(seen 2×, last DN17;" in text, "two signatures with one cause make one bullet, counts added"
    assert "Lessons from reviews:\n  - Check fillet radii against the section view." in text
    assert "not tidied yet" not in text


@pytest.mark.parametrize("card,message", [
    ({"failures": {"NOT_A_FAILURE": "x"}}, "No failure of this tool has the signature"),
    ({"use_when": "two\nlines", "failures": {"FILLET_REFUSED": "x"}}, "one line of text"),
    ({"failures": {}}, "must keep a 'cause -> fix' text for \\['FILLET_REFUSED'\\]"),
    ({"failures": {"FILLET_REFUSED": "x"}, "lessons": [f"lesson {i}" for i in range(8)]}, "9 bullets"),
    ({"failures": {"FILLET_REFUSED": "word " * 151}}, "151 words"),
    ({"failures": {"FILLET_REFUSED": "x"}, "colour": "red"}, "card takes"),
    ("a card as text", "card is"),
])
def test_a_bad_card_is_refused(tools, card, message):
    fail()
    with pytest.raises(SolidWorksError, match=message):
        tools.update_tool_card("add_fillet", card)
    assert tools.kb.card("add_fillet") is None


def test_only_the_most_frequent_failures_must_be_covered(tools):
    for k in range(10):
        for _ in range(k + 1):
            fail(code=f"FAILURE_{k}", message=f"failure {k}")
    required = [r["signature"] for r in cards.must_cover(tools.kb, "add_fillet")]
    assert required == [f"FAILURE_{k}" for k in range(9, 1, -1)]
    card = {"failures": {sig: "too big -> smaller" for sig in required}}
    assert tools.update_tool_card("add_fillet", card)["ok"]


def test_a_failure_fixed_by_an_upgrade_retires_after_three_successes(tools):
    fail(revision="28.2.0")
    work(revision="28.5.0", times=2)
    assert [r["signature"] for r in cards.active_signatures(tools.kb, "add_fillet")] == ["FILLET_REFUSED"]
    work(revision="28.5.0")
    assert cards.active_signatures(tools.kb, "add_fillet") == []
    assert tools.update_tool_card("add_fillet", {"use_when": "rounding edges"})["ok"], "no longer required"


def test_a_failure_still_seen_on_this_version_stays(tools):
    fail()
    work(times=5)
    assert len(cards.active_signatures(tools.kb, "add_fillet")) == 1


def test_an_error_result_carries_the_cards_line(tools):
    fail()
    tools.update_tool_card("add_fillet", {"failures": {"FILLET_REFUSED": "R too big -> use the R the error names"}})

    def add_fillet(radius_mm):
        raise AssertionError("not called")

    error = SolidWorksError("R5 does not round", code="FILLET_REFUSED")
    result = server._log_call(add_fillet, (5,), {}, {"ok": False, "error": "R5 does not round"}, error)
    assert result["kb_hint"] == "R too big -> use the R the error names"
    other = SolidWorksError("radius must be > 0", code="FILLET_BAD_RADIUS")
    assert "kb_hint" not in server._log_call(add_fillet, (-1,), {}, {"ok": False, "error": "x"}, other)


def test_finalise_gets_the_cards_to_tidy(tools):
    tools.kb.ingest(17, "DN17.pdf", 1)
    work()
    fail()
    work(tool="add_box")
    log.record_call("list_faces", {}, {"ok": True}, None)
    packets = tools._cards_to_tidy(tools.kb.open_case())
    assert [p["tool"] for p in packets] == ["add_box", "add_fillet"], "modelling tools only"
    fillet = packets[1]
    assert fillet["this_case"]["calls"] == 2
    assert fillet["this_case"]["failures"] == [{"seq": 2, "error": "R5 does not round all 4 edge(s)",
                                               "error_code": "FILLET_REFUSED", "signature": "FILLET_REFUSED"}]
    assert fillet["must_cover"][0]["signature"] == "FILLET_REFUSED" and fillet["fields"] == {}
