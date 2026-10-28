"""Pure tests: the Claude Code hook that tells the knowledge base which session is calling (no SolidWorks)."""

import importlib.util
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "kb_session_hook.py"
HOOK = {"session_id": "efb18dde-4801-4a92-8324-975a981a78c7", "transcript_path": "C:/t/efb18dde.jsonl",
        "tool_name": "mcp__solidworks__add_fillet", "hook_event_name": "PreToolUse"}


def _run(stdin, kb_dir):
    env = {**__import__("os").environ, "SWMCP_KB_DIR": str(kb_dir)}
    return subprocess.run([sys.executable, str(SCRIPT)], input=stdin, capture_output=True, text=True, env=env, timeout=30)


def test_the_hook_writes_the_session_atomically_and_prints_nothing(tmp_path):
    done = _run(json.dumps(HOOK), tmp_path)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
    target = tmp_path / "fixes" / ".session.json"
    assert json.loads(target.read_text(encoding="utf-8")) == {"session_id": HOOK["session_id"],
                                                              "transcript_path": HOOK["transcript_path"]}
    assert [p.name for p in target.parent.iterdir()] == [".session.json"], "no temporary file is left behind"
    again = _run(json.dumps({**HOOK, "session_id": "3c479092"}), tmp_path)
    assert again.returncode == 0 and json.loads(target.read_text(encoding="utf-8"))["session_id"] == "3c479092"


def test_the_hook_never_fails_whatever_it_is_given(tmp_path):
    for stdin in ("", "not json", "[]", json.dumps({"tool_name": "x"}), json.dumps({"session_id": 5})):
        done = _run(stdin, tmp_path)
        assert (done.returncode, done.stdout) == (0, ""), stdin
    assert not (tmp_path / "fixes" / ".session.json").exists()


def test_the_hook_survives_an_unwritable_folder(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    done = _run(json.dumps(HOOK), blocker / "kb")  # a path under a file: cannot be created
    assert (done.returncode, done.stdout) == (0, "")


def test_the_hook_and_the_server_agree_on_the_folder(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("kb_session_hook", SCRIPT)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    from solidworks_mcp.kb import config

    monkeypatch.delenv("SWMCP_KB_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert hook.kb_dir() == config.kb_dir()
    monkeypatch.setenv("SWMCP_KB_DIR", str(tmp_path / "other"))
    assert hook.kb_dir() == config.kb_dir() == tmp_path / "other"


def test_the_repo_settings_register_the_hook_on_the_solidworks_tools():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    [entry] = settings["hooks"]["PreToolUse"]
    assert entry["matcher"] == "mcp__solidworks__.*"
    [hook] = entry["hooks"]
    assert hook["type"] == "command" and ".venv/Scripts/python.exe" in hook["command"]
    assert "scripts/kb_session_hook.py" in hook["command"]
