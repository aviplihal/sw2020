"""Automatic logging of every tool call, from the one choke point in server._call.

The agent never writes the timeline: the server appends one row per call (tool, short
arguments, outcome, run time, result tokens, pause before it) to the open case's
fixes/DN<n>/timeline.md, or to fixes/_unassigned/ with no case open. Knowledge-base tools
are logged too. A logging failure never breaks the call; server._call turns it into a warning
in the call's result (Docs/KNOWLEDGE-BASE.md §10.1).
"""

import time

from .. import __version__
from . import cards, config, timeline
from .store import UNASSIGNED, KnowledgeBase

# Tools that look but change nothing. They are logged too (what Claude looked
# at is part of how a case was modelled) and marked, so a timeline can fold them away.
READ_ONLY_TOOLS = frozenset({
    "get_status", "list_bodies", "list_dimensions", "read_sketch", "list_features", "list_equations",
    "list_documents", "get_mass_properties", "get_bounding_box", "list_faces", "list_edges",
    "check_printability", "export", "screenshot", "slice_mesh", "compare_with_mesh", "list_components",
    "check_interference", "measure_distance", "check_motion", "swept_region", "get_assembly_bounding_box",
    "kb_view_region", "kb_search", "kb_get_case", "kb_tool_card", "measure_saved_part",
})

SESSION_MAX_AGE_S = 1800  # the hook's .session.json counts for a call if it was written this recently

_kb: KnowledgeBase | None = None
_last_end: dict = {}


def knowledge_base() -> KnowledgeBase:
    """The one KnowledgeBase of this server process, opened on first use."""
    global _kb
    if _kb is None:
        _kb = KnowledgeBase(config.kb_dir())
    return _kb


def use(kb: KnowledgeBase | None) -> None:
    """Point this process at another knowledge base (tests), or None to reopen the configured one."""
    global _kb
    _kb = kb
    _last_end.clear()


def _pause(kb: KnowledgeBase, job: str, rows: list, started) -> float:
    """Seconds since the previous call of this job ended (0 for its first)."""
    last = _last_end.get((str(kb.folder), job)) or (timeline.end_of(rows[-1]) if rows else None)
    return max(0.0, (started - last).total_seconds()) if last else 0.0


def _record_meta(kb: KnowledgeBase, job: str, n: int, sw_revision, run_s: float) -> None:
    """Remember the server/SOLIDWORKS versions and the Claude Code session this call was made under."""
    meta = kb.meta(job)
    changed = False
    version = {"server": __version__, "sw": sw_revision}
    if not meta["versions"] or {k: meta["versions"][-1].get(k) for k in version} != version:
        meta["versions"].append({"from_call": n, **version})
        changed = True
    session = kb.read_session_file()
    if session is not None:
        try:
            fresh = time.time() - kb.session_file.stat().st_mtime <= SESSION_MAX_AGE_S + run_s
        except OSError:
            fresh = False
        if fresh and (not meta["sessions"] or meta["sessions"][-1]["session_id"] != session["session_id"]):
            meta["sessions"].append({"session_id": session["session_id"],
                                     "transcript_path": session.get("transcript_path"), "from_call": n})
            changed = True
    if changed:
        kb.write_meta(job, meta)


def record_call(tool: str, arguments: dict, result: dict, error: BaseException | None,
                sw_revision: str | None = None, *, started=None, ended=None, images=(), job_hint: str | None = None) -> dict:
    """Log one tool call to the open case's timeline (or fixes/_unassigned); returns what was logged.

    `job_hint` is the case that was open when the call began, for a call that closes its case
    (kb_abandon_case): its row goes to that job's folder, wherever the case is now.
    """
    kb = knowledge_base()
    started = started or kb.now()
    ended = ended or started
    case = kb.open_case()
    job = case["dn"] if case else (kb.resolve_job(job_hint) if job_hint else UNASSIGNED)
    day, rows = kb.read_timeline(job)
    ok, outcome, signature, sample = timeline.outcome_text(tool, result, error, images)
    row = kb.append_row(job, {
        "when": started, "tool": tool, "read_only": tool in READ_ONLY_TOOLS, "args": timeline.arguments_text(arguments),
        "ok": ok, "outcome": outcome, "signature": signature, "sample": sample, "run": (ended - started).total_seconds(),
        "tok": timeline.result_tokens(result, images), "pause": _pause(kb, job, rows, started)})
    _last_end[(str(kb.folder), job)] = ended
    _record_meta(kb, job, row["n"], sw_revision, row["run"])
    if case is not None and case["status"] == "described" and not row["read_only"] and not tool.startswith("kb_"):
        kb.set_status(case, "modelling")
    return {"job": job, "n": row["n"], "tool": tool, "ok": ok, "signature": signature,
            "message": sample}


def hint_for(tool: str, signature: str | None) -> str | None:
    """The tool card's line for this failure (kb_hint), or a fixed issue's note, if there is one."""
    return cards.hint(knowledge_base(), tool, signature)
