"""Pure tests: tool cards, kept short and honest by the server, as files (no SolidWorks)."""

import pytest
from kb_helpers import call, new_kb, open_case

from solidworks_mcp import server
from solidworks_mcp.errors import SolidWorksError
from solidworks_mcp.kb import cards, log
from solidworks_mcp.kb.cases import CaseTools

FIXED = """# Hotspots and known issues

Updated 2026-10-20.

## Open (worst first: jobs × cost per job)
(none)

## Fixed
- [x] **H-001** fillet refused for a large radius · `FILLET_REFUSED`
  - Fixed {date} in a1b2c3d by: the error now names the largest radius
  - Seen before the fix: DN16 `3c479092`
  - If it happens again: use the largest radius the error names

## Parked
(none)
"""


@pytest.fixture
def kb(tmp_path):
    yield new_kb(tmp_path / "kb")
    log.use(None)


@pytest.fixture
def tools(kb):
    return CaseTools(kb, server._tool_reference)


def fail(at, tool="add_fillet", code="FILLET_REFUSED", message="R5 does not round all 4 edge(s)"):
    return call(tool, at, ok=False, code=code, message=message)


def work(at, tool="add_fillet", times=1):
    for k in range(times):
        call(tool, at + k)


def test_how_to_call_comes_from_the_server_code():
    line = server._tool_reference("add_fillet")
    assert line.startswith("add_fillet(radius_mm, edges='all', name='Fillet', ")
    assert line.endswith(": " + server.add_fillet.__doc__.strip().split("\n")[0])
    assert server._tool_reference("kb_search") is None and server._tool_reference("no_such_tool") is None


def test_a_tool_without_a_card_shows_its_raw_failures(kb, tools):
    open_case(kb)
    fail(0)
    fail(10)
    result = tools.tool_card("add_fillet")
    assert "no card yet" in result["card"] and "**How to call:** add_fillet(radius_mm" in result["card"]
    assert "- `FILLET_REFUSED` (not tidied yet) R5 does not round all 4 edge(s) (2×, last DN17, 28.5.0)" in result["card"]
    assert result["signatures"] == [{"signature": "FILLET_REFUSED", "count": 2,
                                     "error": "R5 does not round all 4 edge(s)"}]
    assert not kb.tool_file("add_fillet").exists(), "reading a card writes nothing"
    with pytest.raises(SolidWorksError, match="No modelling tool is called 'kb_search'"):
        tools.tool_card("kb_search")


def test_a_tidied_card_is_a_file_whose_counts_come_from_the_timelines(kb, tools):
    open_case(kb)
    fail(0)
    work(10, times=3)
    fail(20, code=None, message="SolidWorks COM error: Fillet3 failed")
    sig = next(r["signature"] for r in cards.active_signatures(kb, "add_fillet") if r["signature"].startswith("T:"))
    card = {"use_when": "Rounding edges of one body.", "dont_use_when": "A rib's top: use add_full_round.",
            "failures": {"FILLET_REFUSED": "Radius larger than the thinnest wall → use the largest R the error names.",
                         sig: "A sketch is still open → close it first."},
            "lessons": ["Check fillet radii against the section view."]}
    text = tools.update_tool_card("add_fillet", card)["card"]
    on_disk = kb.tool_file("add_fillet").read_text(encoding="utf-8")
    assert text == on_disk
    assert text.startswith("# add_fillet\nserver ") and "SOLIDWORKS 28.5.0 · updated 2026-10-12 · used 5×, failed 2× in 1 cases" in text
    assert "**Use when:** Rounding edges of one body." in text and "- `FILLET_REFUSED` Radius larger" in text
    assert "→ use the largest R the error names. (1×, last DN17, 28.5.0)" in text
    assert "## Lessons from reviews\n- Check fillet radii" in text and "not tidied yet" not in text
    # hand-editing a count changes nothing: the next render recounts from the timelines
    kb.tool_file("add_fillet").write_text(on_disk.replace("used 5×", "used 999×"), encoding="utf-8")
    assert "used 5×, failed 2×" in tools.tool_card("add_fillet")["card"]
    assert cards.read(kb, "add_fillet")["fields"] == card


@pytest.mark.parametrize("card,message", [
    ({"failures": {"NOT_A_FAILURE": "x"}}, "No failure of this tool has the signature"),
    ({"use_when": "two\nlines", "failures": {"FILLET_REFUSED": "x"}}, "one line of text"),
    ({"failures": {}}, "must keep a 'cause -> fix' text for \\['FILLET_REFUSED'\\]"),
    ({"failures": {"FILLET_REFUSED": "x"}, "lessons": [f"lesson {i}" for i in range(4)]}, "4 lessons"),
    ({"failures": {"FILLET_REFUSED": "word " * 151}}, "151 words"),
    ({"failures": {"FILLET_REFUSED": "x"}, "colour": "red"}, "card takes"),
    ("a card as text", "card is"),
])
def test_a_bad_card_is_refused_and_nothing_is_written(kb, tools, card, message):
    open_case(kb)
    fail(0)
    with pytest.raises(SolidWorksError, match=message):
        tools.update_tool_card("add_fillet", card)
    assert not kb.tool_file("add_fillet").exists()


def test_the_cap_is_six_failure_lines_and_150_words(kb, tools):
    open_case(kb)
    for k in range(8):
        for rep in range(k + 1):
            fail(k * 20 + rep, code=f"FAILURE_{k}", message=f"failure {k}")
    required = [r["signature"] for r in cards.must_cover(cards.active_signatures(kb, "add_fillet"))]
    assert required == [f"FAILURE_{k}" for k in range(7, 1, -1)], "the six most frequent must be covered"
    ok = {sig: "too big → smaller" for sig in required}
    assert tools.update_tool_card("add_fillet", {"failures": ok})["ok"]
    with pytest.raises(SolidWorksError, match="7 failure lines"):
        tools.update_tool_card("add_fillet", {"failures": {**ok, "FAILURE_1": "x"}})
    assert cards.word_count({"use_when": "a b c", "failures": {"X": "d e"}, "lessons": ["f"]}) == 6


def test_a_failure_fixed_by_an_upgrade_retires_after_three_successes(kb, tools):
    open_case(kb)
    call("add_fillet", 0, ok=False, code="FILLET_REFUSED", message="x")
    # the failure was met on 28.2.0: later calls ran on 28.5.0
    meta = kb.meta("DN17")
    meta["versions"] = [{"from_call": 1, "server": "0.16.0", "sw": "28.2.0"}, {"from_call": 2, "server": "0.16.0", "sw": "28.5.0"}]
    kb.write_meta("DN17", meta)
    work(10, times=2)
    assert [r["signature"] for r in cards.active_signatures(kb, "add_fillet")] == ["FILLET_REFUSED"]
    work(20)
    assert cards.active_signatures(kb, "add_fillet") == []
    assert tools.update_tool_card("add_fillet", {"use_when": "rounding edges"})["ok"], "no longer required"


def test_a_failure_still_seen_on_this_version_stays(kb):
    open_case(kb)
    fail(0)
    work(10, times=5)
    assert len(cards.active_signatures(kb, "add_fillet")) == 1


def test_an_error_result_carries_the_cards_line(kb, tools):
    open_case(kb)
    fail(0)
    tools.update_tool_card("add_fillet", {"failures": {"FILLET_REFUSED": "R too big → use the R the error names"}})

    def add_fillet(radius_mm):
        raise AssertionError("not called")

    error = SolidWorksError("R5 does not round", code="FILLET_REFUSED")
    result = server._log_call(add_fillet, (5,), {}, {"ok": False, "error": "R5 does not round"}, error)
    assert result["kb_hint"] == "R too big → use the R the error names"
    other = SolidWorksError("radius must be > 0", code="FILLET_BAD_RADIUS")
    assert "kb_hint" not in server._log_call(add_fillet, (-1,), {}, {"ok": False, "error": "x"}, other)


def test_a_fixed_failure_leaves_the_card_and_its_hint_is_the_issues_note(kb, tools):
    open_case(kb)
    fail(0)
    tools.update_tool_card("add_fillet", {"failures": {"FILLET_REFUSED": "use a smaller R"}})
    kb.hotspots_file.write_text(FIXED.format(date="2026-10-20"), encoding="utf-8")
    assert cards.active_signatures(kb, "add_fillet") == [], "a failure from before the fix is left off the card"
    assert tools.tool_card("add_fillet")["signatures"] == []
    assert cards.hint(kb, "add_fillet", "FILLET_REFUSED") == "use the largest radius the error names"
    assert cards.hint(kb, "add_fillet", "OTHER") is None
    assert tools.update_tool_card("add_fillet", {"use_when": "rounding"})["ok"], "nothing to cover any more"


def test_a_fixed_failure_that_comes_back_returns_to_the_card_marked(kb, tools):
    open_case(kb)
    fail(0)
    kb.hotspots_file.write_text(FIXED.format(date="2026-10-10"), encoding="utf-8")
    [row] = cards.active_signatures(kb, "add_fillet")
    assert (row["signature"], row["count"], row["again"]) == ("FILLET_REFUSED", 1, "H-001")
    assert "(again after H-001 · 1×, last DN17, 28.5.0)" in tools.tool_card("add_fillet")["card"]


def test_finalise_gets_the_cards_to_tidy(kb, tools):
    case = open_case(kb)
    work(0)
    fail(10)
    work(20, tool="add_box")
    call("list_faces", 30)
    call("kb_search", 40)
    packets = tools._cards_to_tidy(case)
    assert [p["tool"] for p in packets] == ["add_box", "add_fillet"], "modelling tools only"
    fillet = packets[1]
    assert fillet["this_case"] == {"calls": 2, "failures": [{"seq": 2, "error": "R5 does not round all 4 edge(s)",
                                                             "signature": "FILLET_REFUSED"}]}
    assert fillet["must_cover"][0]["signature"] == "FILLET_REFUSED" and fillet["fields"] == {}
