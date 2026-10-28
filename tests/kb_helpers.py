"""Helpers shared by the knowledge-base tests: a fake clock, drawings made on the fly, jobs built from rows."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from solidworks_mcp.errors import SolidWorksError
from solidworks_mcp.kb import log
from solidworks_mcp.kb.store import KnowledgeBase

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "Docs" / "kb-templates"
T0 = datetime(2026, 10, 12, 9, 0, 0)

SPEC = {"units": "in", "items": [
    {"id": "D1", "kind": "dimension", "value": ".375", "decimals": 3, "status": "read"},
    {"id": "T1", "kind": "thread", "callout": "1/4-20 UNC-2B", "status": "read"},
    {"id": "D7", "kind": "dimension", "value": None, "between": "slot width", "status": "unreadable"},
]}
BOXES = {"front": [0.1, 0.15, 0.5, 0.48], "top": [0.55, 0.15, 0.9, 0.48]}


def template(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8").replace("\r\n", "\n")


class Clock:
    """A clock the test moves by hand; kb.now() reads it."""

    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        return self.now


def new_kb(folder, clock=None):
    """A KnowledgeBase in `folder`, set as the process's own."""
    store = KnowledgeBase(folder, clock=clock or Clock())
    log.use(store)
    return store


def call(tool, at, run=1.0, ok=True, code=None, message="boom", arguments=None, result=None, images=(), hint=None):
    """Log one call that started `at` seconds after T0 and took `run` seconds."""
    started = T0 + timedelta(seconds=at)
    error = None if ok else SolidWorksError(message, code=code)
    outcome = result if result is not None else ({"ok": True} if ok else {"ok": False, "error": message})
    return log.record_call(tool, arguments or {}, outcome, error, "28.5.0", started=started,
                           ended=started + timedelta(seconds=run), images=images, job_hint=hint)


def open_case(kb, number=17):
    """An open case (ingested, no files), as if kb_ingest_drawing had run."""
    return kb.ingest(number, f"DN{number}.pdf", 1)[0]


def make_pdf(path, shape="bracket", pages=1):
    """A letter-size drawing: a front view on the left, a top view on the right."""
    sheets = []
    for _ in range(pages):
        sheet = Image.new("RGB", (850, 1100), "white")
        draw = ImageDraw.Draw(sheet)
        if shape == "bracket":
            draw.polygon([(100, 200), (400, 200), (400, 260), (160, 260), (160, 500), (100, 500)], outline="black",
                         width=4)
        else:  # a round flange
            draw.ellipse([100, 200, 400, 500], outline="black", width=4)
            draw.ellipse([220, 320, 280, 380], outline="black", width=4)
        draw.rectangle([500, 200, 750, 500], outline="black", width=4)
        draw.text((100, 900), "DN NOTE: BREAK ALL SHARP EDGES .010 MAX", fill="black")
        sheets.append(sheet)
    sheets[0].save(path, save_all=True, append_images=sheets[1:], resolution=100)
    return str(path)


def transcript_lines(entries):
    """JSONL text of a small Claude Code transcript. Each entry is a tuple:
    ('assistant', seconds, message_id, output_tokens, context_tokens, [tool names], extra) or ('user', seconds, text)
    or ('result', seconds). Times are seconds after T0, written as UTC (tests read them with tz=UTC)."""
    lines = []
    for entry in entries:
        kind, at = entry[0], entry[1]
        stamp = (T0.replace(tzinfo=timezone.utc) + timedelta(seconds=at)).isoformat().replace("+00:00", "Z")
        base = {"timestamp": stamp, "sessionId": "efb18dde-4801-4a92-8324-975a981a78c7"}
        if kind == "assistant":
            _, _, message_id, out, ctx, tools = entry[:6]
            content = [{"type": "text", "text": "..."}] + [
                {"type": "tool_use", "id": f"toolu_{message_id}_{k}", "name": f"mcp__solidworks__{name}", "input": {}}
                for k, name in enumerate(tools)]
            usage = {"input_tokens": 5, "output_tokens": out, "cache_read_input_tokens": ctx - 5,
                     "cache_creation_input_tokens": 0}
            # one API message arrives as several lines that repeat its id and usage
            for block in content:
                lines.append({**base, "type": "assistant", "message": {"id": message_id, "usage": usage,
                                                                       "content": [block]}})
        elif kind == "user":
            lines.append({**base, "type": "user", "message": {"role": "user", "content": entry[2]}})
        elif kind == "result":
            lines.append({**base, "type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}})
    return "\n".join(json.dumps(line) for line in lines) + "\n"
