"""Pure tests: real jobs reach SolidWorks through the file-queue bridge, run from Bash or PowerShell as
`python ...\\sw_bridge\\call.py METHOD ...`. The session hook and the transcript matching must follow (no SolidWorks)."""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
from datetime import timezone

import pytest
from kb_helpers import call, new_kb, open_case, transcript_lines

from solidworks_mcp.kb import digest, log, transcript

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "kb_session_hook.py"
SESSION = "efb18dde-4801-4a92-8324-975a981a78c7"

BRIDGE_COMMANDS = [
    ("Bash", r"python C:\Users\Sandbox\Projects\sw_bridge\call.py add_fillet 3.05 edges=[\"#7\"]", ["add_fillet"]),
    ("Bash", r".venv\Scripts\python.exe ..\sw_bridge\call_dev.py add_box 40 20 10 name=Plate", ["add_box"]),
    ("PowerShell", r'& "C:\Users\Sandbox\Projects\sw2020\.venv\Scripts\python.exe" "C:\Users\Sandbox\Projects\sw_bridge\call.py" list_faces',
     ["list_faces"]),
    ("PowerShell", r"python 'C:\Users\Sandbox\Projects\sw_bridge\call_dev.py' 'kb_search' m6", ["kb_search"]),
    ("Bash", r"cd /c/Users/Sandbox/Projects/sw_bridge && python call.py new_part", ["new_part"]),
    ("Bash", "python /c/Users/Sandbox/Projects/sw_bridge/call.py add_box 1 && python /c/x/sw_bridge/call.py list_faces",
     ["add_box", "list_faces"]),
]
OTHER_COMMANDS = [
    ("Bash", r".venv\Scripts\python.exe -m pytest -m 'not solidworks' -q"),
    ("PowerShell", "git status --short"),
    ("Bash", "python recall.py add_box"),  # not call.py
    ("Bash", "cat call.py"),                 # no method after it
]


# --- the session hook ------------------------------------------------------------------------------


def _run(hook_json, kb_dir):
    env = {**os.environ, "SWMCP_KB_DIR": str(kb_dir)}
    return subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(hook_json), capture_output=True, text=True,
                          env=env, timeout=30)


def _hook(tool, command=None):
    data = {"session_id": SESSION, "transcript_path": "C:/t/x.jsonl", "tool_name": tool, "hook_event_name": "PreToolUse"}
    if command is not None:
        data["tool_input"] = {"command": command}
    return data


def _recorded(kb_dir):
    return (kb_dir / "fixes" / ".session.json").is_file()


def test_the_session_is_recorded_for_a_solidworks_mcp_tool(tmp_path):
    done = _run(_hook("mcp__solidworks__add_fillet"), tmp_path)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "") and _recorded(tmp_path)


@pytest.mark.parametrize("tool,command,methods", BRIDGE_COMMANDS)
def test_the_session_is_recorded_for_a_shell_command_that_runs_the_bridge(tmp_path, tool, command, methods):
    done = _run(_hook(tool, command), tmp_path)
    assert (done.returncode, done.stdout) == (0, "") and _recorded(tmp_path), command


@pytest.mark.parametrize("tool,command", OTHER_COMMANDS)
def test_a_session_running_anything_else_is_never_recorded_as_the_job(tmp_path, tool, command):
    done = _run(_hook(tool, command), tmp_path)
    assert (done.returncode, done.stdout) == (0, "") and not _recorded(tmp_path), command


def test_a_shell_hook_without_a_command_is_ignored_and_never_fails(tmp_path):
    for data in (_hook("Bash"), {**_hook("Bash"), "tool_input": "oops"}, {**_hook("Bash"), "tool_input": {"command": 5}},
                 {**_hook("PowerShell"), "tool_input": None}):
        done = _run(data, tmp_path)
        assert (done.returncode, done.stdout) == (0, "") and not _recorded(tmp_path)


def test_the_settings_match_the_shells_too():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "mcp__solidworks__.*|Bash|PowerShell"


def test_the_hook_and_the_transcript_reader_agree_on_what_a_bridge_command_is():
    spec = importlib.util.spec_from_file_location("kb_session_hook", SCRIPT)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    for tool, command, methods in BRIDGE_COMMANDS:
        assert hook.is_job_call(_hook(tool, command)) and transcript.bridge_methods(command) == methods
    for tool, command in OTHER_COMMANDS:
        assert not hook.is_job_call(_hook(tool, command)) and (tool == "Read" or not transcript.bridge_methods(command))


# --- matching the transcript to the timeline ---------------------------------------------------------


@pytest.mark.parametrize("tool,command,methods", BRIDGE_COMMANDS)
def test_a_bridge_command_is_the_tool_it_calls(tool, command, methods):
    assert transcript.bridge_methods(command) == methods


def test_other_shell_commands_are_not_tool_calls():
    for tool, command in OTHER_COMMANDS:
        assert transcript.bridge_methods(command) == []
    assert transcript.bridge_methods(None) == []


@pytest.fixture
def kb(tmp_path):
    yield new_kb(tmp_path / "kb")
    log.use(None)


def _job(kb):
    open_case(kb)
    call("kb_ingest_drawing", 0, run=2)
    call("add_fillet", 100, run=3)
    call("list_faces", 115, run=1)
    call("add_box", 118, run=1)
    call("add_box", 200, run=1)
    return kb.read_timeline("DN17")[1]


def test_rows_match_bridge_commands_and_mcp_calls_alike(kb, tmp_path):
    rows = _job(kb)
    entries = [
        ("assistant", -3, "m1", 120, 10_000, ["kb_ingest_drawing"]),  # an MCP tool
        ("result", 3),
        ("assistant", 10, "m2", 4_900, 12_000, [("Bash", r"python C:\Users\Sandbox\Projects\sw_bridge\call.py add_fillet 3.05")]),
        ("assistant", 110, "m3", 300, 13_000, [("PowerShell", r'python "C:\Users\Sandbox\Projects\sw_bridge\call.py" list_faces'),
                                              ("Bash", "python call.py add_box 1")]),
        ("assistant", 150, "m4", 250, 14_000, [("Bash", r".venv\Scripts\python.exe -m pytest -q")]),  # not a job call
        ("assistant", 190, "m5", 700, 15_000, [("Bash", r"cd C:\x\sw_bridge && python call_dev.py add_box 2")]),
    ]
    path = tmp_path / "t.jsonl"
    path.write_text(transcript_lines(entries), encoding="utf-8")
    data = transcript.read([path], tz=timezone.utc)
    assert [m["tools"] for m in data["messages"]] == [["kb_ingest_drawing"], ["add_fillet"], ["list_faces", "add_box"], [],
                                                      ["add_box"]]
    found = {r["n"]: r for r in digest.annotate(rows, data)}
    assert (found[1]["claude_tok"], found[2]["claude_tok"], found[3]["claude_tok"]) == (120, 4_900, 300)
    assert found[4]["claude_tok"] is None and found[4]["ctx"] is None, "the second call of m3 shares its message"
    assert found[5]["claude_tok"] == 700 and found[5]["ctx"] == 15_000
