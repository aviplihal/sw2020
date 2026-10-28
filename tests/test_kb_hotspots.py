"""Pure tests: explaining a digest, HOTSPOTS.md, `kb-fixed`, and that CAD agents never see the fix files (no SolidWorks)."""

import json
import re
import sys

import pytest
from kb_helpers import call, make_pdf, new_kb, open_case

from solidworks_mcp import server
from solidworks_mcp.errors import SolidWorksError
from solidworks_mcp.kb import cards, digest, hotspots, log, search
from solidworks_mcp.kb.cases import CaseTools

DAY = 86_400


@pytest.fixture
def kb(tmp_path):
    yield new_kb(tmp_path / "kb")
    log.use(None)


@pytest.fixture
def tools(kb):
    return CaseTools(kb, server._tool_reference)


def closed_job(kb, tools, number, day=0, session=None, fail_twice="HOLE_FACE_NOT_FOUND", loop=True):
    """A job with a repeated failure (and a three-in-a-row loop), closed by abandoning it; returns the abandon result."""
    base = day * DAY
    open_case(kb, number)
    if session:
        (kb.folder / "fixes" / ".session.json").write_text(json.dumps({"session_id": session}), encoding="utf-8")
    call("new_part", base, run=1)
    for k in range(2):
        call("add_hole_wizard", base + 10 + 10 * k, run=1, ok=False, code=fail_twice, message="no face")
    if loop:
        for k in range(3):
            call("list_faces", base + 40 + 5 * k, run=1)
    return tools.abandon_case("done with this test job")


def explain_all(tools, result, text="Checking faces by hand.", **more):
    return tools.explain_digest({p["id"]: text for p in result["hard_parts"]}, **more)


# --- kb_explain_digest ---------------------------------------------------------------------------


def test_explaining_fills_the_digest_and_opens_one_issue_per_hard_part(kb, tools):
    result = closed_job(kb, tools, 17, session="efb18dde-4801-4a92-8324-975a981a78c7")
    assert [p["id"] for p in result["hard_parts"]] == ["D1", "D2"]
    done = tools.explain_digest({"D1": "'+y' matched the upright; I found '+y:outer' after list_faces.",
                                 "D2": "Listing faces to find the base top."},
                                not_measured=["The guide has no recipe for a bend radius."],
                                fix_ideas={"D1": "Refuse an ambiguous direction and list the faces."})
    assert done == {"ok": True, "dn": "DN17~1", "linked": {"D1": "H-001", "D2": "H-002", "D3": "H-003"}}
    parsed = digest.read_digest(kb, "DN17~1")
    assert [(p["id"], p["issue"]) for p in parsed["parts"]] == [("D1", "H-001"), ("D2", "H-002"), ("D3", "H-003")]
    assert parsed["parts"][0]["claude"].startswith("'+y' matched") and parsed["parts"][2]["not_measured"]
    assert parsed["parts"][2]["text"] == "not measured, noted by Claude: The guide has no recipe for a bend radius."
    text = kb.hotspots_file.read_text(encoding="utf-8")
    assert "- [ ] **H-001** '+y' matched the upright; I found '+y:outer' after list_faces. · `HOLE_FACE_NOT_FOUND`" in text
    assert "Seen: DN17~1 `efb18dde` D1" in text and "Fix idea (Claude, DN17~1): Refuse an ambiguous direction" in text
    assert "Cost: not measured · Seen: DN17~1 `efb18dde` D3" in text


@pytest.mark.parametrize("explanations,kwargs,message", [
    ({"D1": "ok"}, {}, "Every hard part needs an explanation .* missing \\['D2'\\]"),
    ({"D1": "ok", "D2": "ok", "D9": "x"}, {}, "no hard part\\(s\\) \\['D9'\\]"),
    ({"D1": "word " * 41, "D2": "ok"}, {}, "D1 is 41 words; the cap is 40"),
    ({"D1": "ok", "D2": "two\nlines"}, {}, "one line of text"),
    ({"D1": "ok", "D2": "ok"}, {"not_measured": ["a", "b", "c"]}, "At most 2 not-measured"),
    ({"D1": "ok", "D2": "ok"}, {"fix_ideas": {"D1": "word " * 61}}, "fix idea of D1 is 61 words"),
    ({"D1": "ok", "D2": "ok"}, {"fix_ideas": {"D7": "x"}}, "not hard parts"),
    ({"D1": "same as H-009", "D2": "ok"}, {}, "no issue H-009"),
])
def test_a_bad_explanation_is_refused_and_nothing_changes(kb, tools, explanations, kwargs, message):
    closed_job(kb, tools, 17)
    before = kb.digest_file("DN17~1").read_text(encoding="utf-8")
    with pytest.raises(SolidWorksError, match=message):
        tools.explain_digest(explanations, **kwargs)
    assert kb.digest_file("DN17~1").read_text(encoding="utf-8") == before and not kb.hotspots_file.read_text(
        encoding="utf-8").count("**H-")


def test_not_sure_is_an_allowed_answer_and_a_digest_is_explained_once(kb, tools):
    result = closed_job(kb, tools, 17)
    explain_all(tools, result, "not sure")
    assert digest.explained(digest.read_digest(kb, "DN17~1"))
    with pytest.raises(SolidWorksError, match="already explained"):
        explain_all(tools, result, dn="DN17~1")


def test_the_default_is_the_latest_digest_still_waiting_and_none_waiting_is_an_error(kb, tools):
    with pytest.raises(SolidWorksError, match="No digest is waiting"):
        tools.explain_digest({"D1": "x"})
    first = closed_job(kb, tools, 17)
    second = closed_job(kb, tools, 18, day=1)
    assert explain_all(tools, second)["dn"] == "DN18~1"
    assert explain_all(tools, first)["dn"] == "DN17~1"
    with pytest.raises(SolidWorksError, match="No digest is waiting"):
        tools.explain_digest({"D1": "x"})


def test_same_as_links_a_part_to_the_issue_it_names_and_averages_the_cost(kb, tools):
    first = closed_job(kb, tools, 17, session="aaaaaaaa-0000")
    explain_all(tools, first, "Faces by hand.")
    second = closed_job(kb, tools, 18, day=1, session="bbbbbbbb-1111")
    done = tools.explain_digest({"D1": "same as H-001: the same ambiguous face", "D2": "same as H-002"})
    assert done["linked"] == {"D1": "H-001", "D2": "H-002"}
    issues = {i["id"]: i for i in hotspots.load(kb)}
    assert [(e["job"], e["session"], e["parts"]) for e in issues["H-001"]["seen"]] == [
        ("DN18~1", "bbbbbbbb", ["D1"]), ("DN17~1", "aaaaaaaa", ["D1"])], "every job with its session and hard part"
    assert len(issues) == 2, "no new issues for a problem already listed"
    assert len(second["hard_parts"]) == 2


def test_a_failure_signature_links_to_its_open_issue_by_itself(kb, tools):
    explain_all(tools, closed_job(kb, tools, 17))
    second = closed_job(kb, tools, 18, day=1)
    done = tools.explain_digest({"D1": "Again the face.", "D2": "Loop."})
    assert done["linked"]["D1"] == "H-001", "the same signature is the same problem"
    assert done["linked"]["D2"] == "H-003", "a loop has no signature: it needs 'same as' to be merged"
    assert second["hard_parts"][0]["text"].startswith("`HOLE_FACE_NOT_FOUND`")


def test_open_issues_are_ranked_by_jobs_times_cost_and_the_cheapest_are_parked(kb):
    issues = []
    for k in range(1, 23):
        issue = hotspots.new_issue(issues, f"issue {k}")
        for job in range(1, k % 4 + 2):
            hotspots.add_seen(issue, f"DN{job}", None, f"D{k}", float(k), k * 100)
    hotspots.save(kb, issues)
    text = kb.hotspots_file.read_text(encoding="utf-8")
    open_part, _, parked = text.partition("## Parked")
    assert open_part.count("**H-") == 20 and parked.count("**H-") == 2 and "(none: parked" not in parked
    scores = [hotspots._score(i) for i in hotspots.parse(open_part)["issues"]]
    assert scores == sorted(scores, reverse=True)
    assert min(scores) >= max(hotspots._score(i) for i in hotspots.parse(parked)["issues"])


def test_a_signature_seen_in_two_jobs_becomes_an_issue_even_with_no_hard_part(kb, tools):
    for number, day in ((17, 0), (18, 1)):
        open_case(kb, number)
        call("add_fillet", day * DAY, ok=False, code="FILLET_REFUSED", message="R5 does not round")
        call("add_box", day * DAY + 20)
        tools.abandon_case("test")
    issues = hotspots.load(kb)
    assert [(i["title"], i["signatures"], [e["job"] for e in i["seen"]]) for i in issues] == [
        ("add_fillet fails: R5 does not round", ["FILLET_REFUSED"], ["DN18~1", "DN17~1"])]


def test_a_signature_seen_in_one_job_only_is_not_an_issue(kb, tools):
    open_case(kb, 17)
    call("add_fillet", 0, ok=False, code="FILLET_REFUSED")
    tools.abandon_case("test")
    assert hotspots.load(kb) == []


def test_the_measured_table_covers_the_last_ten_jobs(kb, tools):
    for number in range(1, 13):
        closed_job(kb, tools, number, day=number)
    text = kb.hotspots_file.read_text(encoding="utf-8")
    table = text.split("## Measured: last 10 jobs\n")[1].split("\n\n")[0]
    assert "| Tool | Calls/job | Min/job | Tokens/job | Share of tokens |" in table
    assert re.search(r"\| list_faces \| 3\.0 \|", table), "three calls per job over the last ten, not twelve jobs"
    assert hotspots.LAST_JOBS == 10


# --- kb-fixed ------------------------------------------------------------------------------------


def _fixed_setup(kb, tools):
    first = closed_job(kb, tools, 17, session="efb18dde-4801-4a92-8324-975a981a78c7")
    tools.explain_digest({"D1": "Faces by hand.", "D2": "Loop."}, fix_ideas={"D1": "Refuse an ambiguous face."})
    tools.update_tool_card("add_hole_wizard", {"failures": {"HOLE_FACE_NOT_FOUND": "use '+y:outer'"}})
    return first


def test_kb_fixed_updates_the_hotspots_the_digest_and_the_tool_card(kb, tools):
    _fixed_setup(kb, tools)
    assert "`HOLE_FACE_NOT_FOUND`" in kb.tool_file("add_hole_wizard").read_text(encoding="utf-8")
    done = hotspots.kb_fixed(kb, "h-001", "a1b2c3d", "list the faces in the error", "pass face='+y:outer'",
                             server._tool_reference, today="2026-10-20")
    assert done == {"ok": True, "issue": "H-001", "fixed": "2026-10-20", "digests_marked": ["DN17~1"],
                    "cards_rebuilt": ["add_hole_wizard"]}
    text = kb.hotspots_file.read_text(encoding="utf-8")
    fixed = text.split("## Fixed\n")[1].split("\n\n## Parked")[0]
    assert fixed.splitlines()[:4] == [
        "- [x] **H-001** Faces by hand. · `HOLE_FACE_NOT_FOUND`",
        "  - Fixed 2026-10-20 in a1b2c3d by: list the faces in the error",
        "  - Seen before the fix: DN17~1 `efb18dde` D1",
        "  - If it happens again: pass face='+y:outer'"]
    assert "**H-001**" not in text.split("## Fixed")[0], "gone from Open"
    part = digest.read_digest(kb, "DN17~1")["parts"][0]
    assert part["issue"] == "H-001" and part["link_note"] == " · ✓ fixed by list the faces in the error (a1b2c3d, 2026-10-20)"
    assert "→ H-001 · ✓ fixed by list the faces in the error (a1b2c3d, 2026-10-20)" in kb.digest_file(
        "DN17~1").read_text(encoding="utf-8")
    assert "HOLE_FACE_NOT_FOUND" not in kb.tool_file("add_hole_wizard").read_text(encoding="utf-8"), \
        "the failure drops off the card"


def test_kb_fixed_refuses_unknown_fixed_or_empty(kb, tools):
    _fixed_setup(kb, tools)
    with pytest.raises(SolidWorksError, match="No issue H-099"):
        hotspots.kb_fixed(kb, "H-099", "a", "b", "c")
    with pytest.raises(SolidWorksError, match="--note must say something"):
        hotspots.kb_fixed(kb, "H-001", "a", "b", " ")
    hotspots.kb_fixed(kb, "H-001", "a1b2c3d", "x", "y")
    with pytest.raises(SolidWorksError, match="already fixed"):
        hotspots.kb_fixed(kb, "H-001", "a1b2c3d", "x", "y")


def test_a_fixed_failure_that_comes_back_opens_a_new_issue_and_returns_to_the_card(kb, tools):
    _fixed_setup(kb, tools)
    hotspots.kb_fixed(kb, "H-001", "a1b2c3d", "list the faces", "pass face='+y:outer'", server._tool_reference,
                      today="2026-10-13")
    fixed_before = kb.hotspots_file.read_text(encoding="utf-8").split("## Fixed\n")[1].split("\n\n## Parked")[0]
    open_case(kb, 18)
    call("add_hole_wizard", 3 * DAY, ok=False, code="HOLE_FACE_NOT_FOUND", message="no face again")
    tools.abandon_case("test")
    text = kb.hotspots_file.read_text(encoding="utf-8")
    assert text.split("## Fixed\n")[1].split("\n\n## Parked")[0] == fixed_before, "a Fixed entry is never rewritten"
    again = [i for i in hotspots.load(kb) if i["status"] == "open" and i["signatures"] == ["HOLE_FACE_NOT_FOUND"]]
    assert len(again) == 1 and again[0]["again_of"] == "H-001" and [e["job"] for e in again[0]["seen"]] == ["DN18~1"]
    assert "· `HOLE_FACE_NOT_FOUND` · again after H-001" in text
    [row] = cards.active_signatures(kb, "add_hole_wizard")
    assert (row["again"], row["count"]) == ("H-001", 1)
    assert "(again after H-001 · 1×, last DN18~1" in tools.tool_card("add_hole_wizard")["card"]
    assert cards.hint(kb, "add_hole_wizard", "HOLE_FACE_NOT_FOUND") == "pass face='+y:outer'"


def test_the_kb_fixed_command_is_wired_into_the_entry_point(kb, monkeypatch, capsys):
    tools = CaseTools(kb, server._tool_reference)
    _fixed_setup(kb, tools)
    monkeypatch.setattr(sys, "argv", ["solidworks-mcp", "kb-fixed", "H-001", "--commit", "a1b2c3d", "--by",
                                      "list the faces", "--note", "pass the face"])
    monkeypatch.setattr(server, "_worker", None)
    with pytest.raises(SystemExit) as stopped:
        server.main()
    assert stopped.value.code == 0
    assert "H-001 fixed" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["solidworks-mcp", "kb-fixed", "H-001", "--commit", "x", "--by", "y", "--note", "z"])
    with pytest.raises(SystemExit) as stopped:
        server.main()
    assert stopped.value.code == 1 and "already fixed" in capsys.readouterr().err


# --- two readers, two folders --------------------------------------------------------------------


def test_no_cad_facing_tool_returns_anything_from_the_fix_files(kb, tools, tmp_path):
    canary = "CANARYZEBRA"
    result = closed_job(kb, tools, 17, session="canarysession-0000")
    tools.explain_digest({p["id"]: f"{canary} explanation" for p in result["hard_parts"]},
                         fix_ideas={"D1": f"{canary} fix idea"})
    kb.hotspots_file.write_text(kb.hotspots_file.read_text(encoding="utf-8") + f"\n{canary} in hotspots\n",
                                encoding="utf-8")
    kb.digest_file("DN17~1").write_text(kb.digest_file("DN17~1").read_text(encoding="utf-8") + canary, encoding="utf-8")
    # a live case, so every CAD-facing tool has something to return
    tools.ingest_drawing(make_pdf(tmp_path / "DN18.pdf"))
    call("add_hole_wizard", 0, ok=False, code="NOT_FIXED_YET", message="no face")
    outputs = [
        tools.describe_case({"front": [0.1, 0.15, 0.5, 0.48]}, "A bracket", "bracket",
                            {"units": "in", "items": [{"id": "D1", "kind": "dimension", "status": "read"}]}),
        tools.search("bracket"), tools.search(), tools.get_case("DN18"), tools.tool_card("add_hole_wizard"),
        tools.tool_card("list_faces"), tools.update_spec({"units": "in", "items": []}, {"q?": "a"}),
        tools.view_region([0, 0, 0.5, 0.5])[0], {"hint": log.hint_for("add_hole_wizard", "NOT_FIXED_YET")},
        {"index": kb.index_text(), "card": kb.tool_file("add_hole_wizard").exists()},
    ]
    blob = json.dumps(outputs, default=str)
    for secret in (canary, "canarysession", "Seen:", "H-001", "digest", "timeline.md", "HOTSPOTS", "Fix idea"):
        assert secret not in blob, secret
    assert [p for p in search.search(kb, canary)["results"]] == [], "search never reads the fixes folder"


def test_the_only_fix_text_a_cad_agent_sees_is_a_fixed_issues_note(kb, tools):
    _fixed_setup(kb, tools)
    hotspots.kb_fixed(kb, "H-001", "a1b2c3d", "list the faces", "pass face='+y:outer'", server._tool_reference,
                      today="2026-10-13")
    assert log.hint_for("add_hole_wizard", "HOLE_FACE_NOT_FOUND") == "pass face='+y:outer'"
    assert log.hint_for("add_hole_wizard", "SOME_OTHER") is None
