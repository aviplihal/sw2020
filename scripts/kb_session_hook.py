"""Claude Code PreToolUse hook: tell the knowledge base which chat is calling.

An MCP server is not told which Claude Code session calls it. This hook (matcher
mcp__solidworks__.*|Bash|PowerShell) reads the hook JSON from stdin and writes the
session's id and transcript path to <kb dir>/fixes/.session.json before each job call;
the server reads that file when it logs the call (Docs/KNOWLEDGE-BASE.md §10.2). A job
call is a SolidWorks MCP tool, or a shell command that runs the bridge's call.py or
call_dev.py; every other shell command is ignored.

It never prints and always exits 0: a hook that failed or wrote to stdout could block
the tool call. Standalone (stdlib only), so it runs with any Python.
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

# the bridge's call.py / call_dev.py, also quoted or after `cd ... &&`
_BRIDGE_CALL = re.compile(r"""(?:^|[\s"'/\\])call(?:_dev)?\.py["']?\s+["']?[A-Za-z_]""")


def kb_dir() -> Path:
    """The same folder as solidworks_mcp.kb.config.kb_dir()."""
    override = os.environ.get("SWMCP_KB_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "solidworks-mcp" / "kb"


def is_job_call(data: dict) -> bool:
    """A SolidWorks MCP tool, or a Bash/PowerShell command that runs the bridge's call.py / call_dev.py.

    Any other shell command (pytest, git, ...) is not the job: its session is never recorded.
    """
    tool = data.get("tool_name")
    if not isinstance(tool, str):
        return False
    if tool.startswith("mcp__solidworks__"):
        return True
    if tool in ("Bash", "PowerShell"):
        command = (data.get("tool_input") or {}).get("command")
        return isinstance(command, str) and bool(_BRIDGE_CALL.search(command))
    return False


def write_session(data: dict, folder: Path) -> bool:
    """Atomically write {session_id, transcript_path}; False when it is not a job call or has no session."""
    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id or not is_job_call(data):
        return False
    target = folder / "fixes" / ".session.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=target.parent, prefix=".session.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump({"session_id": session_id, "transcript_path": data.get("transcript_path")}, out)
        os.replace(temp, target)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    return True


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        if isinstance(data, dict):
            write_session(data, kb_dir())
    except BaseException:  # noqa: BLE001 - never block a tool call, whatever went wrong
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
