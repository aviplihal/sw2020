"""Automatic logging of every tool call, from the one choke point in server._call.

The agent never writes the timeline: the server records each call's tool,
arguments, result or error, and error signature. Knowledge-base tools are
not logged. A logging failure never breaks the call; server._call turns it
into a warning in the call's result (Docs/KNOWLEDGE-BASE.md §10).
"""

import json

from .. import __version__
from . import config, signatures
from .store import KnowledgeBase

# Tools that look but change nothing. They are logged too (what Claude looked
# at is part of how a case was modelled) and marked, so a timeline can fold them away.
READ_ONLY_TOOLS = frozenset({
    "get_status", "list_bodies", "list_dimensions", "read_sketch", "list_features", "list_equations",
    "list_documents", "get_mass_properties", "get_bounding_box", "list_faces", "list_edges",
    "check_printability", "export", "screenshot", "slice_mesh", "compare_with_mesh", "list_components",
    "check_interference", "measure_distance", "check_motion", "swept_region", "get_assembly_bounding_box",
})

# a stored result larger than this keeps only its outline (big face/edge lists)
MAX_RESULT_CHARS = 20_000

_kb: KnowledgeBase | None = None


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


def _stored_result(result: dict) -> dict:
    text = json.dumps(result, default=str, ensure_ascii=False)
    if len(text) <= MAX_RESULT_CHARS:
        return result
    kept = {key: result[key] for key in ("ok", "mass_properties", "bounding_box_mm", "path") if key in result}
    return {**kept, "truncated": f"{len(text)} characters; keys: {', '.join(result)}"}


def record_call(tool: str, arguments: dict, result: dict, error: BaseException | None,
                sw_revision: str | None = None) -> dict | None:
    """Log one tool call; returns the stored event, or None for a tool that is not logged."""
    if tool.startswith("kb_"):
        return None
    message = code = params = signature = None
    if error is not None or result.get("ok") is False:
        message = result.get("error") or str(error)
        code = getattr(error, "code", None)
        params = getattr(error, "params", None)
        signature = signatures.signature(code, message)
    return knowledge_base().record_event(
        tool, read_only=tool in READ_ONLY_TOOLS, arguments=arguments, result=_stored_result(result),
        error=message, error_code=code, error_params=params, signature=signature,
        server_version=__version__, sw_revision=sw_revision)
