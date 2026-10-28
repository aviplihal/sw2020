"""Pure tests: the job digest, from a fixture timeline and a fixture transcript (no SolidWorks)."""

import json
from datetime import timedelta, timezone

import pytest
from kb_helpers import T0, call, new_kb, open_case, transcript_lines

from solidworks_mcp.kb import digest, log, timeline, transcript
from solidworks_mcp.kb.cases import CaseTools

BOX_A = {"box": [0.2, 0.3, 0.32, 0.4]}
SESSION = "efb18dde-4801-4a92-8324-975a981a78c7"


def _job():
    """Twelve calls: a long think, a wait on the person, a zoom twice, a failure twice, a loop of three."""
    plan = [  # (tool, start s, run s, ok, code, arguments)
        ("kb_ingest_drawing", 0, 2, True, None, {}),
        ("add_box", 100, 3, True, None, {}),
        ("list_faces", 115, 1, True, None, {}),
        ("list_edges", 118, 1, True, None, {}),
        ("add_fillet", 735, 4, True, None, {}),
        ("kb_view_region", 745, 0.5, True, None, BOX_A),
        ("kb_view_region", 750, 0.5, True, None, BOX_A),
        ("add_hole_wizard", 760, 1, False, "HOLE_FACE_NOT_FOUND", {}),
        ("add_hole_wizard", 770, 1, False, "HOLE_FACE_NOT_FOUND", {}),
        ("list_faces", 780, 1, True, None, {}),
        ("list_faces", 785, 1, True, None, {}),
        ("list_faces", 790, 1, True, None, {}),
    ]
    for tool, at, run, ok, code, arguments in plan:
        call(tool, at, run=run, ok=ok, code=code, arguments=arguments,
             result={"ok": True, "text": "x" * 400} if tool.startswith("list_") else None)


def _transcript(extra_before=True):
    entries = []
    if extra_before:
        entries.append(("assistant", -1000, "m0", 9_999, 1_000, []))  # work before the job: not counted
    entries += [
        ("assistant", -3, "m1", 120, 10_000, ["kb_ingest_drawing"]),
        ("result", 3),
        ("assistant", 10, "m2", 4_900, 12_000, ["add_box"]),
        ("result", 104),
        ("assistant", 110, "m3", 300, 13_000, ["list_faces", "list_edges"]),  # two calls, one message
        ("result", 120),
        ("assistant", 125, "m4", 200, 14_000, []),  # asks the person something
        ("user", 725, "go ahead, use R3"),          # 10 minutes later
        ("assistant", 730, "m5", 250, 50_000, ["add_fillet"]),
    ]
    return entries


@pytest.fixture
def kb(tmp_path):
    yield new_kb(tmp_path / "kb")
    log.use(None)


@pytest.fixture
def job(kb):
    open_case(kb)
    _job()
    return kb.read_timeline("DN17")[1]


def test_numbers_from_a_timeline_without_a_transcript(job):
    numbers = digest.measure(job)
    assert (numbers["calls"], numbers["failures"]) == (12, 2)
    assert numbers["duration_s"] == 791 - 0 and numbers["tools_s"] == pytest.approx(17.0)
    assert numbers["waiting_s"] == 616, "no transcript: a pause over 5 minutes counts as waiting"
    assert numbers["claude_s"] == pytest.approx(sum(r["pause"] for r in job) - 616)
    assert numbers["claude_tokens"] is None and numbers["context_peak"] is None and not numbers["exact"]
    assert len(numbers["top"]) == 5 and numbers["top"][0]["tool"] == "list_faces", "most tokens and time together"
    by_tool = {e["tool"]: e for e in numbers["top"]}
    assert by_tool["add_box"]["seconds"] == 101 and "add_fillet" not in by_tool, "the 616 s wait is not Claude's time"
    assert by_tool["list_faces"]["calls"] == 4 and by_tool["list_faces"]["tok"] == 4 * round(len(json.dumps({"ok": True, "text": "x" * 400})) / 4)


def test_loops_are_the_same_tool_three_times_in_a_row_and_the_same_zoom_twice(job):
    found = dict(digest.loops(job))
    assert found == {"list_faces 3 times in a row": [10, 11, 12], "kb_view_region same box twice": [6, 7]}


def test_failures_are_grouped_by_signature(job):
    assert digest.failures_by_signature(job) == [
        {"signature": "HOLE_FACE_NOT_FOUND", "tools": ["add_hole_wizard"], "calls": [8, 9]}]


def test_hard_parts_are_picked_by_measurement_and_name_their_calls_and_cost(job):
    parts = digest.hard_parts(digest.annotate(job, None))
    texts = [p["text"] for p in parts]
    assert any(t.startswith("`HOLE_FACE_NOT_FOUND` ×2 on add_hole_wizard (calls 8–9) · cost ") for t in texts)
    assert any(t.startswith("list_faces 3 times in a row (calls 10–12) · cost ") for t in texts)
    assert any(t.startswith("kb_view_region same box twice (calls 6–7) · cost ") for t in texts)
    assert [p["calls"][0] for p in parts] == sorted(p["calls"][0] for p in parts), "listed in call order"
    assert len(parts) <= digest.MAX_HARD_PARTS


def test_at_most_six_hard_parts_whatever_the_job_does(kb):
    open_case(kb)
    at = 0
    for k in range(5):  # five different loops of four, each a hard part, plus a failure pair
        for _ in range(4):
            call(f"add_loop_{k}", at)
            at += 5
    for _ in range(2):
        call("add_box", at, ok=False, code="BOX_BAD")
        at += 5
    for _ in range(3):
        call("add_box", at + 400)
        at += 400
    parts = digest.hard_parts(digest.annotate(kb.read_timeline("DN17")[1], None))
    assert len(parts) == digest.MAX_HARD_PARTS


def test_a_job_that_flowed_has_no_hard_parts(kb):
    open_case(kb)
    for k, tool in enumerate(["new_part", "add_box", "add_fillet", "add_hole", "save_part"]):
        call(tool, k * 20)
    assert digest.measure(kb.read_timeline("DN17")[1])["hard_parts"] == []


# --- the transcript ------------------------------------------------------------------------------


@pytest.fixture
def transcript_file(tmp_path):
    path = tmp_path / f"{SESSION}.jsonl"
    path.write_text(transcript_lines(_transcript()), encoding="utf-8")
    return path


def test_a_message_split_over_several_lines_is_counted_once(transcript_file):
    data = transcript.read([transcript_file], tz=timezone.utc)
    assert [(m["id"], m["out"], m["ctx"]) for m in data["messages"]] == [
        ("m0", 9_999, 1_000), ("m1", 120, 10_000), ("m2", 4_900, 12_000), ("m3", 300, 13_000), ("m4", 200, 14_000),
        ("m5", 250, 50_000)]
    assert [m["tools"] for m in data["messages"]][3] == ["list_faces", "list_edges"]
    assert len(data["assistant"]) > len(data["messages"]), "several lines per message were read"
    assert len(data["person"]) == 1, "tool results are not the person"


def test_waiting_on_you_runs_from_claudes_last_line_to_your_reply(transcript_file):
    data = transcript.read([transcript_file], tz=timezone.utc)
    assert transcript.waits(data) == [(T0 + timedelta(seconds=725), 600.0)]


def test_numbers_with_a_transcript_are_exact(job, transcript_file):
    data = transcript.read([transcript_file], tz=timezone.utc)
    numbers = digest.measure(job, data)
    assert numbers["exact"] and numbers["waiting_s"] == 600, "from your message, not the 5-minute rule"
    assert numbers["claude_tokens"] == 120 + 4_900 + 300 + 200 + 250, "once per message.id, nothing before the job"
    assert numbers["context_peak"] == 50_000
    rows = {r["n"]: r for r in numbers["rows"]}
    assert (rows[2]["claude_tok"], rows[3]["claude_tok"], rows[4]["claude_tok"]) == (4_900, 300, None)
    assert rows[5]["growth"] == 37_000 and rows[5]["waiting"] == 600
    assert {e["tool"]: e["claude_tok"] for e in numbers["top"]}["add_box"] == 4_900


def test_hard_parts_with_a_transcript_include_long_thinking_and_big_context_growth(job, transcript_file):
    data = transcript.read([transcript_file], tz=timezone.utc)
    texts = [p["text"] for p in digest.measure(job, data)["hard_parts"]]
    assert any(t.startswith("pause 1 min 38 s before call 2 (add_box) · Claude wrote 4,900 tokens") for t in texts)
    assert any("context grew 37,000 tokens" in t and "call 5 (add_fillet)" in t for t in texts)


# --- writing the digest ---------------------------------------------------------------------------


def test_write_digest_makes_the_file_with_session_and_awaiting_explanations(kb, tmp_path, monkeypatch, job, transcript_file):
    monkeypatch.setattr(digest, "LOCAL_TZ", timezone.utc)
    meta = kb.meta("DN17")
    meta["sessions"] = [{"session_id": SESSION, "transcript_path": str(transcript_file), "from_call": 1}]
    kb.write_meta("DN17", meta)
    written = digest.write_digest(kb, "DN17")
    assert written["written"] and [p["id"] for p in written["hard_parts"]] == [f"D{k}" for k in range(1, len(written["hard_parts"]) + 1)]
    text = kb.digest_file("DN17").read_text(encoding="utf-8")
    assert text.startswith(f"# DN17 · digest · 2026-10-12\n\nSession `{SESSION}` (Claude Code)\nTranscript: {transcript_file}\n")
    assert "12 tool calls · 2 failures · 13 min 11 s, of which 10 min waiting on you · Claude wrote 5,770 tokens · " \
           "context peaked at 50,000" in text
    assert "Waiting on you (from your messages in the transcript) | 10.0 |" in text
    assert text.count("Claude: (awaiting explanation)") == len(written["hard_parts"])
    parsed = digest.parse(text)
    assert digest.awaiting(parsed) and digest.render_parts(parsed["head"], parsed["parts"]) == text


def test_the_waiting_marks_are_written_into_the_timeline(kb, job, tmp_path, monkeypatch, transcript_file):
    monkeypatch.setattr(digest, "LOCAL_TZ", timezone.utc)
    meta = kb.meta("DN17")
    meta["sessions"] = [{"session_id": SESSION, "transcript_path": str(transcript_file), "from_call": 1}]
    kb.write_meta("DN17", meta)
    digest.write_digest(kb, "DN17")
    marked = [r["n"] for r in kb.read_timeline("DN17")[1] if r["wait"]]
    assert marked == [5] and "616 ⏸" in kb.timeline_file("DN17").read_text(encoding="utf-8")


def test_a_digest_without_a_transcript_says_so(kb, job):
    digest.write_digest(kb, "DN17")
    text = kb.digest_file("DN17").read_text(encoding="utf-8")
    assert "Session: not recorded" in text and "Waiting on you (pauses over 5 minutes) | 10.3 |" in text
    assert "Claude wrote" not in text


def test_a_missing_transcript_file_falls_back_to_the_timeline(kb, job, tmp_path):
    meta = kb.meta("DN17")
    meta["sessions"] = [{"session_id": SESSION, "transcript_path": str(tmp_path / "gone.jsonl"), "from_call": 1}]
    kb.write_meta("DN17", meta)
    digest.write_digest(kb, "DN17")
    text = kb.digest_file("DN17").read_text(encoding="utf-8")
    assert f"Session `{SESSION}`" in text and "pauses over 5 minutes" in text


def test_abandon_writes_the_digest_and_names_the_hard_parts(kb, job):
    tools = CaseTools(kb)
    result = tools.abandon_case("wrong scan")
    assert result["abandoned"] == "DN17~1" and result["hard_parts"] and "kb_explain_digest" in result["next"]
    assert kb.digest_file("DN17~1").is_file() and kb.hotspots_file.is_file()
    assert digest.read_digest(kb, "DN17~1")["job"] == "DN17~1"


def test_the_timeline_round_trips_after_rewriting(job, kb):
    day, rows = kb.read_timeline("DN17")
    assert timeline.parse(timeline.render("DN17", day, rows))[1] == rows
    assert json.dumps(rows[0], default=str)
