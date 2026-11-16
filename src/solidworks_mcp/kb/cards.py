"""Tool cards: one short Markdown file per MCP tool, tools/<tool>.md.

A card is stored as fields: Claude writes only the words (when to use the tool, when not,
each failure's 'cause -> fix' keyed by its error signature, lessons from reviews). The
server writes everything counted or generated: the header line, the 'How to call' line
from the tool's docstring, and every count, which it recomputes from the timelines each
time, so the numbers in a file are never trusted and cannot be miscounted by editing.
Hard cap: 6 failure lines and 150 words of Claude's text (Docs/KNOWLEDGE-BASE.md §7).
A failure that belongs to a fixed HOTSPOTS issue is left off unless seen again after the
fix. Template: Docs/kb-templates/tool-card.md. Pure apart from the file functions.
"""

import re

from ..errors import SolidWorksError
from . import hotspots
from .files import atomic_write, read_text
from .store import UNASSIGNED

MAX_FAILURES = 6
MAX_LESSONS = 3
MAX_WORDS = 150
FIELDS = ("use_when", "dont_use_when", "failures", "lessons")
SUCCESSES_TO_RETIRE = 3
UNTIDY = "(not tidied yet) "

_COUNTS = re.compile(r"\s*\((?:again after H-\d+ · )?\d+×(?:, last [^,)]+)?(?:, [^)]*)?\)$")


def word_count(fields: dict) -> int:
    texts = [fields.get("use_when") or "", fields.get("dont_use_when") or ""]
    texts += list((fields.get("failures") or {}).values()) + list(fields.get("lessons") or [])
    return sum(len(text.split()) for text in texts)


# --- counting from the timelines ------------------------------------------------------------


def _version(meta: dict, call: int) -> tuple:
    found = (None, None)
    for entry in sorted(meta["versions"], key=lambda v: v["from_call"]):
        if entry["from_call"] <= call:
            found = (entry.get("server"), entry.get("sw"))
    return found


def events(kb, tool: str) -> list:
    """Every call of the tool in every timeline (cases and unassigned), oldest first."""
    found = []
    for job in kb.jobs():
        day, rows = kb.read_timeline(job)
        mine = [r for r in rows if r["tool"] == tool]
        if not mine:
            continue
        meta = kb.meta(job)
        for row in mine:
            server, sw = _version(meta, row["n"])
            found.append({"when": row["when"], "job": job, "n": row["n"], "ok": row["ok"],
                          "signature": row["signature"], "sample": row["sample"], "server": server, "sw": sw,
                          "day": row["when"].date().isoformat()})
    return sorted(found, key=lambda e: (e["when"], e["job"], e["n"]))


def stats(kb, tool: str) -> dict:
    """{used, failed, cases, latest: (server, sw), sigs: {signature: {...}}} counted from the timelines."""
    found = events(kb, tool)
    sigs = {}
    for index, event in enumerate(found):
        if not event["ok"]:
            entry = sigs.setdefault(event["signature"], {"signature": event["signature"], "events": []})
            entry["events"].append(dict(event, index=index))
    for entry in sigs.values():
        last = entry["events"][-1]
        entry["successes_since"] = sum(1 for e in found[last["index"] + 1:] if e["ok"])
    return {"used": len(found), "failed": sum(1 for e in found if not e["ok"]),
            "cases": len({e["job"] for e in found if e["job"] != UNASSIGNED}),
            "latest": (found[-1]["server"], found[-1]["sw"]) if found else (None, None), "sigs": sigs}


def active_signatures(kb, tool: str, counted: dict | None = None) -> list:
    """The tool's failure groups still worth a line, most frequent first.

    Dropped: a failure not seen since the server version or the SOLIDWORKS revision changed, once the
    tool has worked 3 times since (a later release fixed it); and a failure of a fixed HOTSPOTS issue,
    unless it was seen again after the fix (then counted from the fix and marked `again after H-00n`).
    """
    counted = counted or stats(kb, tool)
    fixed = hotspots.fixed_signatures(kb)
    rows = []
    for entry in counted["sigs"].values():
        signature, seen = entry["signature"], entry["events"]
        last = seen[-1]
        again = None
        if signature in fixed:
            again, day = fixed[signature]
            seen = [e for e in seen if e["day"] > day]
            if not seen:
                continue
        if (last["server"], last["sw"]) != counted["latest"] and entry["successes_since"] >= SUCCESSES_TO_RETIRE:
            continue
        rows.append({"signature": signature, "count": len(seen), "sample": seen[-1]["sample"] or "",
                     "last_case": seen[-1]["job"] if seen[-1]["job"] != UNASSIGNED else None,
                     "last_server_version": seen[-1]["server"], "last_sw_revision": seen[-1]["sw"],
                     "last_seen": seen[-1]["when"].isoformat(), "again": again})
    rows.sort(key=lambda r: r["last_seen"], reverse=True)
    return sorted(rows, key=lambda r: -r["count"])  # most frequent first, the latest first among equals


def must_cover(rows: list) -> list:
    """The failure groups a tidied card must give a text: the most frequent active ones, up to the cap."""
    return rows[:MAX_FAILURES]


def check(fields, required: list, known: set) -> dict:
    """A card's fields, checked against the cap and the failures it must cover; refused otherwise."""
    if not isinstance(fields, dict):
        raise SolidWorksError('card is {"use_when": "...", "dont_use_when": "...", "failures": {signature: '
                              '"cause -> fix"}, "lessons": ["..."]}.', code="KB_BAD_CARD")
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise SolidWorksError(f"card takes {', '.join(FIELDS)} (got {sorted(unknown)}).", code="KB_BAD_CARD")
    failures = fields.get("failures") or {}
    lessons = fields.get("lessons") or []
    if not isinstance(failures, dict) or not isinstance(lessons, list):
        raise SolidWorksError("failures is {signature: text}; lessons is a list of texts.", code="KB_BAD_CARD")
    texts = [fields.get("use_when"), fields.get("dont_use_when"), *failures.values(), *lessons]
    for text in texts:
        if text is not None and (not isinstance(text, str) or "\n" in text):
            raise SolidWorksError(f"Every card text is one line of text (got {text!r}).", code="KB_BAD_CARD")
    invented = set(failures) - known
    if invented:
        raise SolidWorksError(f"No failure of this tool has the signature(s) {sorted(invented)}: use the "
                              "signatures kb_tool_card lists.", code="KB_BAD_CARD")
    missing = [row["signature"] for row in required if not (failures.get(row["signature"]) or "").strip()]
    if missing:
        raise SolidWorksError(f"The card must keep a 'cause -> fix' text for {missing}: they are among the "
                              "tool's most frequent failures.", code="KB_CARD_DROPS_FAILURE")
    words = word_count(fields)
    if len(failures) > MAX_FAILURES or len(lessons) > MAX_LESSONS or words > MAX_WORDS:
        raise SolidWorksError(f"The card has {len(failures)} failure lines, {len(lessons)} lessons and {words} words; "
                              f"the cap is {MAX_FAILURES} failure lines, {MAX_LESSONS} lessons and {MAX_WORDS} words "
                              "of your text. Shorten it.", code="KB_CARD_TOO_LONG",
                              params={"failures": len(failures), "lessons": len(lessons), "words": words})
    return {key: fields[key] for key in FIELDS if fields.get(key)}


# --- the file ---------------------------------------------------------------------------------


def render(tool: str, stored: dict | None, counted: dict, active: list, how_to_call: str | None) -> str:
    """tools/<tool>.md: `stored` is {fields, server_version, sw_revision, updated} or None for no card yet."""
    fields = (stored or {}).get("fields") or {}
    server, sw = (stored["server_version"], stored["sw_revision"]) if stored else counted["latest"]
    updated = f"updated {stored['updated']}" if stored else "no card yet: written at the next finalise that uses it"
    lines = [f"# {tool}",
             f"server {server or '?'} · SOLIDWORKS {sw or '?'} · {updated} · used {counted['used']}×, "
             f"failed {counted['failed']}× in {counted['cases']} cases", ""]
    if fields.get("use_when"):
        lines.append(f"**Use when:** {fields['use_when']}")
    if fields.get("dont_use_when"):
        lines.append(f"**Don't use when:** {fields['dont_use_when']}")
    if how_to_call:
        lines.append(f"**How to call:** {how_to_call}")
    lines += ["", "## Known failures"]
    worded = fields.get("failures") or {}
    for row in active:
        text = worded.get(row["signature"]) or f"{UNTIDY}{row['sample']}".rstrip()
        seen = f"{row['count']}×" + (f", last {row['last_case']}" if row["last_case"] else "")
        seen += f", {row['last_sw_revision']}" if row["last_sw_revision"] else ""
        if row["again"]:
            seen = f"again after {row['again']} · {seen}"
        lines.append(f"- `{row['signature']}` {text} ({seen})")
    if not active:
        lines.append("(none seen)")
    if fields.get("lessons"):
        lines += ["", "## Lessons from reviews"] + [f"- {lesson}" for lesson in fields["lessons"]]
    return "\n".join(lines) + "\n"


def parse(text: str) -> dict:
    """{fields, server_version, sw_revision, updated} read back from a card file (counts are ignored)."""
    fields, section = {"failures": {}, "lessons": []}, None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        elif line.startswith("**Use when:** "):
            fields["use_when"] = line[len("**Use when:** "):]
        elif line.startswith("**Don't use when:** "):
            fields["dont_use_when"] = line[len("**Don't use when:** "):]
        elif section == "Known failures" and line.startswith("- `"):
            found = re.match(r"- `([^`]+)` (.*)$", line)
            body = _COUNTS.sub("", found.group(2)) if found else ""
            if found and not body.startswith(UNTIDY.strip()):
                fields["failures"][found.group(1)] = body
        elif section == "Lessons from reviews" and line.startswith("- "):
            fields["lessons"].append(line[2:])
    head = re.search(r"^server (\S+) · SOLIDWORKS (.+?) · updated (\S+) ·", text, re.M)
    return {"fields": {k: v for k, v in fields.items() if v}, "server_version": head.group(1) if head else None,
            "sw_revision": head.group(2) if head else None, "updated": head.group(3) if head else None}


def read(kb, tool: str) -> dict | None:
    path = kb.tool_file(tool)
    if not path.is_file():
        return None
    stored = parse(read_text(path))
    for key in ("server_version", "sw_revision"):
        if stored[key] == "?":
            stored[key] = None
    return stored


def write(kb, tool: str, fields: dict, how_to_call: str | None, server_version, sw_revision) -> dict:
    """Store the card's words; the file gets fresh counts. Returns what was stored."""
    counted = stats(kb, tool)
    active = active_signatures(kb, tool, counted)
    shown = {sig: text for sig, text in (fields.get("failures") or {}).items()
             if sig in {r["signature"] for r in active}}
    stored = {"fields": {**fields, "failures": shown} if shown else {k: v for k, v in fields.items() if k != "failures"},
              "server_version": server_version, "sw_revision": sw_revision, "updated": kb.today()}
    atomic_write(kb.tool_file(tool), render(tool, stored, counted, active, how_to_call))
    return stored


def hint(kb, tool: str, signature: str | None) -> str | None:
    """The card's line for this failure (kb_hint). A failure of a fixed issue gets the issue's
    'If it happens again' note instead; None when there is nothing to say."""
    if not signature:
        return None
    note = hotspots.hint_note(kb, signature)
    if note:
        return note
    card = read(kb, tool)
    return ((card or {}).get("fields") or {}).get("failures", {}).get(signature)


def rebuild_for_signatures(kb, signatures: list, tool_reference=None) -> list:
    """Rewrite the card of every tool that has failed with one of `signatures` (a fix drops them off)."""
    rebuilt = []
    for path in sorted(kb.folder.joinpath("tools").glob("*.md")):
        tool = path.stem
        counted = stats(kb, tool)
        if not set(counted["sigs"]) & set(signatures):
            continue
        stored = read(kb, tool)
        reference = tool_reference(tool) if tool_reference else None
        write(kb, tool, stored["fields"], reference, stored["server_version"], stored["sw_revision"])
        rebuilt.append(tool)
    return rebuilt
