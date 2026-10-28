"""timeline.md: one Markdown table row per tool call, the server's raw record of a job.

Everything counted elsewhere (the digest, the tool cards' counts, HOTSPOTS) is built
from these rows. A row is a dict: n, when (naive local datetime), tool, read_only,
args (text, cut), ok, outcome (text), signature and sample for a failure, run (s),
tok (result tokens), pause (s since the previous call ended). Pure: stdlib only.
Format: Docs/kb-templates/timeline.md; design: Docs/KNOWLEDGE-BASE.md §10.1.
"""

import json
import re
import struct
from datetime import datetime, timedelta

from . import signatures

COLUMNS_NOTE = ("Columns: # · time · tool · arguments (cut to 80 chars) · outcome · run s · result tokens · "
                "pause before (s). `·` = read-only.")
TABLE_HEAD = "| # | Time | Tool | Arguments | Outcome | Run | Tok | Pause |\n|---|---|---|---|---|---|---|---|"
MAX_ARGS = 80
MAX_SAMPLE = 80
WAIT_S = 300  # a pause over 5 minutes counts as waiting on the person when no transcript says otherwise
WAIT_MARK = "⏸"  # on a row whose pause was mostly waiting on the person; set when the digest is written

_ROW = re.compile(r"^\|\s*\d+\s*\|")


def _cell(text) -> str:
    return re.sub(r"\s+", " ", str(text)).replace("|", "/").strip()


def cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _value(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def arguments_text(arguments: dict) -> str:
    """`radius_mm=3.05, edges=["#7"]`: the call's arguments, None left out, cut to ~80 characters."""
    parts = [f"{key}={_value(value)}" for key, value in (arguments or {}).items() if value is not None]
    return cut(_cell(", ".join(parts)), MAX_ARGS)


def image_tokens(width: int, height: int) -> int:
    """Anthropic's estimate for an image: width x height / 750."""
    return round(width * height / 750)


def image_size(data: bytes) -> tuple | None:
    """(width, height) read from a JPEG or PNG header, without any imaging library."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return width, height
            i += 2 + length
    return None


def result_tokens(result: dict, images=()) -> int:
    """What the result put into Claude's context: text at ~4 characters per token, images by size."""
    text = len(json.dumps(result, ensure_ascii=False, default=str))
    total = round(text / 4)
    for data in images:
        size = image_size(data)
        if size:
            total += image_tokens(*size)
    return total


def _num(value, digits=1) -> str:
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:.{digits}f}".rstrip("0").rstrip(".")


def outcome_text(tool: str, result: dict, error: BaseException | None, images=()) -> tuple:
    """(ok, outcome text, signature, sample): `ok · V 85,160 · bbox 76.2×50.8×38.1` or `✗ CODE · message`."""
    if error is not None or result.get("ok") is False:
        message = str(result.get("error") or error or "")
        signature = signatures.signature(getattr(error, "code", None), message)
        return False, f"✗ {signature} · {cut(_cell(message), MAX_SAMPLE)}".rstrip(" ·"), signature, _cell(message)
    notes = []
    props = result.get("mass_properties") or {}
    if "volume_mm3" in props:
        notes.append(f"V {_num(props['volume_mm3'], 0)}")
    size = (props.get("bounding_box_mm") or {}).get("size_mm")
    if size:
        notes.append("bbox " + "×".join(_num(v) for v in size))
    for data in images:
        shown = image_size(data)
        if shown:
            notes.append(f"{'overview ' if tool == 'kb_ingest_drawing' else ''}{shown[0]}×{shown[1]}")
    if tool == "kb_describe_case" and result.get("index"):
        notes.append(f"index {len(result['index'].splitlines())} lines")
    return True, " · ".join(["ok", *notes]), None, None


def _clock(when: datetime, day: str) -> str:
    return when.strftime("%H:%M:%S") if when.date().isoformat() == day else when.strftime("%Y-%m-%d %H:%M:%S")


def render_row(row: dict, day: str) -> str:
    pause = f"{round(row['pause']):,}" + (f" {WAIT_MARK}" if row.get("wait") else "")
    tool = ("· " if row["read_only"] else "") + row["tool"]
    line = (f"| {row['n']} | {_clock(row['when'], day)} | {tool} | {_cell(row['args'])} | {_cell(row['outcome'])} | "
            f"{row['run']:.1f} | {row['tok']:,} | {pause} |")
    return line.replace(" |  | ", " | | ")  # an empty arguments cell is written `| |`


def parse_row(line: str, day: str) -> dict:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    n, when, tool, args, outcome, run, tok, pause = cells[:8]
    when = when if " " in when else f"{day} {when}"
    ok = not outcome.startswith("✗")
    signature = sample = None
    if not ok:
        head, _, rest = outcome[1:].strip().partition(" · ")
        signature, sample = head.strip(), rest
    return {"n": int(n), "when": datetime.strptime(when, "%Y-%m-%d %H:%M:%S"), "tool": tool.removeprefix("· "),
            "read_only": tool.startswith("· "), "args": args, "ok": ok, "outcome": outcome, "signature": signature,
            "sample": sample, "run": float(run), "tok": int(tok.replace(",", "")),
            "pause": float(pause.replace(WAIT_MARK, "").replace(",", "").strip() or 0), "wait": WAIT_MARK in pause}


def render(job: str, day: str, rows: list) -> str:
    lines = [f"# {job} · timeline · {day}", COLUMNS_NOTE, "", TABLE_HEAD]
    lines += [render_row(row, day) for row in rows]
    return "\n".join(lines) + "\n"


def day_of(text: str) -> str | None:
    match = re.match(r"# \S+ · timeline · (\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else None


def parse(text: str) -> tuple:
    """(day, rows) of a timeline.md."""
    day = day_of(text) or "1970-01-01"
    rows = [parse_row(line, day) for line in text.splitlines() if _ROW.match(line)]
    return day, rows


def end_of(row: dict) -> datetime:
    return row["when"] + timedelta(seconds=row["run"])
