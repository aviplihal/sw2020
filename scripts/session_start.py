"""Claude Code SessionStart hook: brief a new session so a one-sentence prompt is enough.

Prints (stdout becomes session context): what this project is, the job workflow, which
tools exist, and live state: git branch, the knowledge-base index, whether the bridge
has been running. Never fails: any problem just shortens the briefing. Stdlib only.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT.parent / "sw_bridge"

STATIC = r"""\
# Session briefing (SolidWorks MCP, SOLIDWORKS 2020 fork)
You are working on / with the SolidWorks MCP server (tools `mcp__solidworks__*`, ~90 tools: modelling, assemblies,
drawings via make_drawing, and the knowledge base `kb_*`). Read CLAUDE.md for the project rules.
- A user message like "do DN17" or a path to DN17.pdf is a DRAWING JOB. Its PDF is normally in the job folder
  C:\Sandbox DropBox\Jobs\DN17\ and the part and drawing are saved there too. Read the MCP resource solidworks://job
  (source: src/solidworks_mcp/job.md) and follow it step by step. Nothing is invented; unreadable values are asked.
- Before modelling, look at earlier jobs: kb_search / kb_get_case, and kb_tool_card for each tool you will use.
- Real jobs are DN<n>. DTN<n> are old development test runs: never lessons, never KB cases.
- SolidWorks is driven through the bridge in ..\sw_bridge, which the USER starts (double-click
  scripts\start-bridge.cmd; never from this app). Call tools as
  `.venv\Scripts\python.exe ..\sw_bridge\call_dev.py <tool> [args] [key=value]`. Check with
  `call_dev.py bridge_ping`; if it is down, ask the user to start it.
- Drawing style: thread/hole notes as leader notes at the feature; size notes become real dimensions.
- Docs: Docs/KNOWLEDGE-BASE.md, Docs/SHOP-DRAWINGS.md, Docs/PROGRESS.md (2020 conversion log).
"""


def kb_dir() -> Path:
    override = os.environ.get("SWMCP_KB_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "solidworks-mcp" / "kb"


def git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10)
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def live_state() -> list[str]:
    lines = ["", "## Live state"]
    branch = git("branch", "--show-current")
    if branch:
        dirty = len(git("status", "--short").splitlines())
        lines.append(f"- Git: branch {branch}, {dirty} changed file(s)")
    index = kb_dir() / "INDEX.md"
    if index.is_file():
        text = index.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        lines.append(f"- Knowledge base index ({index}):")
        lines += ["  " + line for line in text[:25]]
    else:
        lines.append(f"- Knowledge base: no cases yet ({kb_dir()}).")
    for name in ("queue_dev", "queue"):
        log = BRIDGE / name / "bridge.log"
        if log.is_file():
            age = (time.time() - log.stat().st_mtime) / 60
            lines.append(f"- Bridge {name}: log last written {age:.0f} min ago (not proof it is running; ping it).")
    return lines


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        print(STATIC + "\n".join(live_state()))
    except BaseException:  # noqa: BLE001 - a briefing must never break the session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
