"""The session digest: where a job's effort went, from its timeline (and transcript), and why.

The server measures; Claude only explains (Docs/KNOWLEDGE-BASE.md §10.3, template
Docs/kb-templates/digest.md). Written at finalise and at abandon by write_digest, the
one function both call. Pure apart from write_digest (which reads and writes the files).
"""

import re
from datetime import timedelta
from pathlib import Path

from . import timeline, transcript
from .files import atomic_write, read_text

MAX_HARD_PARTS = 6
MAX_NOT_MEASURED = 2
EXPLANATION_WORDS = 40
LONG_PAUSE_S = 60          # a pause (not waiting on the person) this long is a candidate hard part
CLAUDE_TOKENS_MIN = 2000   # so is a step Claude wrote this much before
CONTEXT_GROWTH = 30_000    # and a step where the context grew by more than this
MATCH_WINDOW_S = 600       # a transcript tool call belongs to a timeline row up to this long before it
LOCAL_TZ = None            # the zone the transcript's UTC times are shown in: None is this computer's (tests set one)
AWAITING = "(awaiting explanation)"
INTRO = ("Picked by the server from the measurements (longest pauses, loops, repeated failures).\n"
         "Explained by Claude at finalise. The transcript has the full detail.")

_CALLS = re.compile(r"\bcalls? (\d+(?:\s*[–-]\s*\d+)?(?:,\s*\d+(?:\s*[–-]\s*\d+)?)*)")
_COST = re.compile(r" · cost ([\d.]+) min, ([\d,]+) tok$")
_LINK = re.compile(r"^→ (H-\d+)(.*)$")


def duration(seconds: float) -> str:
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min" + (f" {rest} s" if rest else "")
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


def call_list(numbers: list) -> str:
    """'call 5', 'calls 3–4', 'calls 11, 13': how a hard part names its calls."""
    numbers = sorted(set(numbers))
    if len(numbers) == 1:
        return f"call {numbers[0]}"
    if numbers == list(range(numbers[0], numbers[-1] + 1)):
        return f"calls {numbers[0]}–{numbers[-1]}"
    return "calls " + ", ".join(str(n) for n in numbers)


def parse_calls(text: str) -> list:
    found = _CALLS.search(text)
    if not found:
        return []
    numbers = []
    for piece in found.group(1).split(","):
        span = re.fullmatch(r"\s*(\d+)(?:\s*[–-]\s*(\d+))?\s*", piece)
        numbers += range(int(span.group(1)), int(span.group(2) or span.group(1)) + 1)
    return numbers


# --- measuring -------------------------------------------------------------------


def annotate(rows: list, data: dict | None) -> list:
    """Copies of the rows with `waiting` (s waiting on the person within the pause) and, when a transcript is
    given, `claude_tok` (Claude's output tokens before the call, once per message), `ctx` and `growth`."""
    rows = [dict(r, waiting=0.0, claude_tok=None, ctx=None, growth=0) for r in rows]
    if data is None:
        for row in rows:
            row["waiting"] = row["pause"] if row["pause"] >= timeline.WAIT_S else 0.0
        return rows
    spans = transcript.waits(data)
    previous_end = None
    for row in rows:
        floor = previous_end or row["when"] - timedelta(seconds=row["pause"])
        row["waiting"] = min(row["pause"], sum(s for t, s in spans if floor <= t <= row["when"] + timedelta(seconds=1)))
        previous_end = timeline.end_of(row)
    names = [(m["end"], name, m) for m in data["messages"] for name in m["tools"]]
    taken, seen_messages, last_ctx = set(), set(), None
    for row in rows:
        best = None
        for index, (when, name, message) in enumerate(names):
            gap = (row["when"] - when).total_seconds()
            if index in taken or name != row["tool"] or not -5 <= gap <= MATCH_WINDOW_S:
                continue
            if best is None or when > names[best][0]:
                best = index
        if best is None:
            continue
        taken.add(best)
        message = names[best][2]
        if message["id"] in seen_messages:
            continue
        seen_messages.add(message["id"])
        row["claude_tok"], row["ctx"] = message["out"], message["ctx"]
        row["growth"] = message["ctx"] - last_ctx if last_ctx is not None else 0
        last_ctx = message["ctx"]
    return rows


def _free(row: dict) -> float:
    """The pause before a call minus the time spent waiting on the person."""
    return max(0.0, row["pause"] - row["waiting"])


def _cost(rows: list, numbers: list) -> tuple:
    chosen = [r for r in rows if r["n"] in numbers]
    minutes = sum(r["run"] + _free(r) for r in chosen) / 60
    tokens = sum(r["tok"] + (r["claude_tok"] or 0) for r in chosen)
    return minutes, tokens


def _with_cost(rows: list, text: str, numbers: list) -> str:
    minutes, tokens = _cost(rows, numbers)
    return f"{text} · cost {minutes:.1f} min, {tokens:,} tok"


def failures_by_signature(rows: list) -> list:
    groups = {}
    for row in rows:
        if not row["ok"]:
            groups.setdefault(row["signature"], []).append(row)
    return [{"signature": sig, "tools": sorted({r["tool"] for r in group}), "calls": [r["n"] for r in group]}
            for sig, group in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))]


def loops(rows: list) -> list:
    """[(description, call numbers)]: the same tool 3+ times in a row; the same zoom box twice or more."""
    found, run = [], []
    for row in rows + [None]:
        if run and (row is None or row["tool"] != run[0]["tool"]):
            if len(run) >= 3:
                found.append((f"{run[0]['tool']} {len(run)} times in a row", [r["n"] for r in run]))
            run = []
        if row is not None:
            run.append(row)
    boxes = {}
    for row in rows:
        if row["tool"] == "kb_view_region" and row["ok"]:
            boxes.setdefault(row["args"], []).append(row["n"])
    for numbers in boxes.values():
        if len(numbers) >= 2:
            found.append(("kb_view_region same box " + ("twice" if len(numbers) == 2 else f"{len(numbers)} times"),
                          numbers))
    return found


def hard_parts(rows: list) -> list:
    """At most 6 [{text, calls}] picked by measurement: every loop, every repeated failure, any step where
    the context grew by more than 30,000 tokens, then the longest pauses and the steps where Claude wrote most."""
    first, others = [], []
    for description, numbers in loops(rows):
        first.append((sum(r["run"] + _free(r) for r in rows if r["n"] in numbers),
                      _with_cost(rows, f"{description} ({call_list(numbers)})", numbers), numbers))
    for group in failures_by_signature(rows):
        if len(group["calls"]) >= 2:
            tools = " and ".join(group["tools"])
            first.append((sum(r["run"] + _free(r) for r in rows if r["n"] in group["calls"]),
                          _with_cost(rows, f"`{group['signature']}` ×{len(group['calls'])} on {tools} "
                                           f"({call_list(group['calls'])})", group["calls"]), group["calls"]))
    for row in rows:
        free, tokens, growth = _free(row), row["claude_tok"] or 0, row["growth"]
        if free < LONG_PAUSE_S and tokens < CLAUDE_TOKENS_MIN and growth <= CONTEXT_GROWTH:
            continue
        parts = ([f"pause {duration(free)} before call {row['n']} ({row['tool']})"] if free >= LONG_PAUSE_S
                 else [f"call {row['n']} ({row['tool']})"])
        if tokens >= CLAUDE_TOKENS_MIN:
            parts.append(f"Claude wrote {tokens:,} tokens")
        if growth > CONTEXT_GROWTH:
            parts.append(f"context grew {growth:,} tokens")
        score = free + tokens / 50 + growth / 300
        entry = (score, _with_cost(rows, " · ".join(parts), [row["n"]]), [row["n"]])
        (first if growth > CONTEXT_GROWTH else others).append(entry)
    chosen = sorted(first, key=lambda e: -e[0])[:MAX_HARD_PARTS]
    chosen += sorted(others, key=lambda e: -e[0])[:MAX_HARD_PARTS - len(chosen)]
    chosen.sort(key=lambda e: e[2][0])
    return [{"text": text, "calls": numbers} for _, text, numbers in chosen]


def measure(rows: list, data: dict | None = None) -> dict:
    """Every number of a digest, from a job's timeline rows and (optionally) its transcript."""
    rows = annotate(rows, data)
    start, end = rows[0]["when"], timeline.end_of(rows[-1])
    waiting = sum(r["waiting"] for r in rows)
    tools_s = sum(r["run"] for r in rows)
    claude_s = max(0.0, sum(r["pause"] for r in rows) - waiting)
    by_tool = {}
    for row in rows:
        entry = by_tool.setdefault(row["tool"], {"tool": row["tool"], "calls": 0, "seconds": 0.0, "tok": 0,
                                                 "claude_tok": 0})
        entry["calls"] += 1
        entry["seconds"] += row["run"] + _free(row)
        entry["tok"] += row["tok"]
        entry["claude_tok"] += row["claude_tok"] or 0
    top_time, top_tok = max(e["seconds"] for e in by_tool.values()) or 1, max(e["tok"] for e in by_tool.values()) or 1
    top = sorted(by_tool.values(), key=lambda e: (-(e["seconds"] / top_time + e["tok"] / top_tok), e["tool"]))[:5]
    claude_total = ctx_peak = None
    if data is not None:
        window = [m for m in data["messages"] if m["start"] >= _first_use(data, rows) and m["start"] <= end]
        claude_total = sum(m["out"] for m in window)
        ctx_peak = max((m["ctx"] for m in window), default=None)
    return {"rows": rows, "calls": len(rows), "failures": sum(1 for r in rows if not r["ok"]),
            "duration_s": (end - start).total_seconds(), "waiting_s": waiting, "exact": data is not None,
            "tools_s": tools_s, "claude_s": claude_s, "top": top, "failed": failures_by_signature(rows),
            "claude_tokens": claude_total, "context_peak": ctx_peak, "hard_parts": hard_parts(rows)}


def _first_use(data: dict, rows: list):
    """When the message that made the job's first call began (so work before the job is not counted)."""
    for message in data["messages"]:
        if rows[0]["tool"] in message["tools"] and abs((rows[0]["when"] - message["end"]).total_seconds()) \
                <= MATCH_WINDOW_S:
            return message["start"]
    return rows[0]["when"]


# --- digest.md ----------------------------------------------------------------------------


def render_part(part: dict) -> str:
    lines = [f"- **{part['id']}** · {part['text']}"]
    if not part["not_measured"]:
        lines.append(f"  Claude: {part['claude'] if part['claude'] is not None else AWAITING}")
    if part.get("issue"):
        lines.append(f"  → {part['issue']}{part.get('link_note') or ''}")
    return "\n".join(lines)


def render(job: str, date: str, sessions: list, numbers: dict, parts: list) -> str:
    """digest.md in the shape of Docs/kb-templates/digest.md."""
    lines = [f"# {job} · digest · {date}", ""]
    if sessions:
        for found in sessions:
            lines += [f"Session `{found['session_id']}` (Claude Code)", f"Transcript: {found.get('transcript_path') or '?'}"]
    else:
        lines.append("Session: not recorded (no hook, or a Claude Desktop chat)")
    summary = [f"{numbers['calls']} tool calls", f"{numbers['failures']} failures",
               f"{duration(numbers['duration_s'])}, of which {duration(numbers['waiting_s'])} waiting on you"]
    if numbers["claude_tokens"] is not None:
        summary.append(f"Claude wrote {numbers['claude_tokens']:,} tokens")
        if numbers["context_peak"]:
            summary.append(f"context peaked at {numbers['context_peak']:,}")
    working = numbers["tools_s"] + numbers["claude_s"] or 1
    source = "from your messages in the transcript" if numbers["exact"] else "pauses over 5 minutes"
    lines += [" · ".join(summary), "", "## Time", "| | min | share of Claude's time |", "|---|---|---|",
              f"| Tools running | {numbers['tools_s'] / 60:.1f} | {numbers['tools_s'] / working:.0%} |",
              f"| Claude working between calls | {numbers['claude_s'] / 60:.1f} | {numbers['claude_s'] / working:.0%} |",
              f"| Waiting on you ({source}) | {numbers['waiting_s'] / 60:.1f} | — |", "",
              "## Top tools", "| Tool | Calls | Run + pause (min) | Result tokens | Claude's tokens before it |",
              "|---|---|---|---|---|"]
    for entry in numbers["top"]:
        claude = f"{entry['claude_tok']:,}" if numbers["claude_tokens"] is not None else "—"
        lines.append(f"| {entry['tool']} | {entry['calls']} | {entry['seconds'] / 60:.1f} | {entry['tok']:,} | {claude} |")
    if numbers["failed"]:
        lines += ["", "## Failures"]
        lines += [f"- `{f['signature']}` ×{len(f['calls'])} on {' and '.join(f['tools'])} ({call_list(f['calls'])})"
                  for f in numbers["failed"]]
    lines += ["", "## Hard parts"]
    return render_parts("\n".join(lines) + "\n", parts)


def parse(text: str) -> dict:
    """A digest.md as {job, date, sessions, head, parts}; render_parts(head, parts) gives the file back."""
    head, _, body = text.partition("## Hard parts\n")
    title = re.match(r"# (\S+) · digest · (\S+)", head)
    sessions = [{"session_id": m.group(1), "transcript_path": m.group(2).strip()}
                for m in re.finditer(r"^Session `([^`]+)` \(Claude Code\)\nTranscript: (.*)$", head, re.M)]
    intro_end = body.find("\n\n")
    parts, current = [], None
    for line in (body[intro_end:] if intro_end >= 0 else "").splitlines():
        entry = re.match(r"- \*\*(D\d+)\*\* · (.*)$", line)
        if entry:
            current = {"id": entry.group(1), "text": entry.group(2), "claude": None, "issue": None,
                       "link_note": None, "not_measured": entry.group(2).startswith("not measured")}
            parts.append(current)
        elif current and line.startswith("  Claude: "):
            said = line[len("  Claude: "):]
            current["claude"] = None if said == AWAITING else said
        elif current and line.startswith("  → "):
            link = _LINK.match(line.strip())
            if link:
                current["issue"], current["link_note"] = link.group(1), link.group(2) or None
    return {"job": title.group(1) if title else None, "date": title.group(2) if title else None,
            "sessions": sessions, "head": head + "## Hard parts\n", "parts": parts}


def render_parts(head: str, parts: list) -> str:
    """The digest from its head (everything up to the '## Hard parts' line) and its hard parts."""
    body = "\n".join(render_part(p) for p in parts) if parts else "Nothing stood out."
    return f"{head}{INTRO}\n\n{body}\n"


def part_cost(part: dict) -> tuple:
    """(minutes, tokens) printed on a hard part, or (None, None) for one the numbers cannot show."""
    found = _COST.search(part["text"])
    return (float(found.group(1)), int(found.group(2).replace(",", ""))) if found else (None, None)


def part_signature(part: dict) -> str | None:
    found = re.match(r"`([^`]+)` ×\d+", part["text"])
    return found.group(1) if found else None


def part_calls(part: dict) -> list:
    return parse_calls(part["text"])


def explained(parsed: dict) -> bool:
    return all(p["not_measured"] or p["claude"] is not None for p in parsed["parts"])


def awaiting(parsed: dict) -> bool:
    return any(not p["not_measured"] and p["claude"] is None for p in parsed["parts"])


# --- writing -----------------------------------------------------------------------------------


def sessions_of(kb, job: str) -> list:
    return [{"session_id": s["session_id"], "transcript_path": s.get("transcript_path")}
            for s in kb.meta(job)["sessions"]]


def write_digest(kb, job: str, tz=None) -> dict:
    """Measure the job and write fixes/<job>/digest.md; returns the numbers and the hard parts to explain.

    Called by kb_abandon_case, and later by kb_finalise_case. With a transcript path known, the
    transcript is read as well. A job with no calls gets no digest (there is nothing to measure).
    """
    day, rows = kb.read_timeline(job)
    if not rows:
        return {"written": False, "hard_parts": []}
    paths = [Path(s["transcript_path"]) for s in sessions_of(kb, job) if s.get("transcript_path")]
    paths = [p for p in dict.fromkeys(paths) if p.is_file()]
    data = transcript.read(paths, tz if tz is not None else LOCAL_TZ) if paths else None
    numbers = measure(rows, data)
    marked = [dict(r, wait=m["waiting"] > 0 and m["waiting"] >= r["pause"] / 2) for r, m in zip(rows, numbers["rows"])]
    if marked != rows:
        kb.write_timeline(job, day, marked)  # the ⏸ marks: rows whose pause was mostly waiting on the person
    parts = [{"id": f"D{k}", "text": p["text"], "claude": None, "issue": None, "link_note": None,
              "not_measured": False} for k, p in enumerate(numbers["hard_parts"], start=1)]
    atomic_write(kb.digest_file(job), render(job, day, sessions_of(kb, job), numbers, parts))
    from . import hotspots  # imported here: hotspots reads digests, so the modules must not import each other at load

    hotspots.rebuild(kb, after=job)
    return {"written": True, "numbers": numbers, "hard_parts": [{"id": p["id"], "text": p["text"]} for p in parts]}


def read_digest(kb, job: str) -> dict | None:
    path = kb.digest_file(job)
    return parse(read_text(path)) if path.is_file() else None
