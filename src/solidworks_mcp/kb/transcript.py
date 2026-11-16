"""Reading a Claude Code transcript (JSONL) for the digest. Pure: stdlib only.

Each line is a JSON object with `type`, `timestamp` (ISO, UTC), `sessionId` and, for an
assistant line, `message` {id, usage, content}. One API message is split over several
consecutive lines that repeat the same message.id and usage: usage is counted once per
message.id. A user line is either tool results or the person's own text; only the
person's text ends a wait (Docs/KNOWLEDGE-BASE.md §10.3).
"""

import json
import re
from datetime import datetime

# `python ...\sw_bridge\call.py add_fillet 3.05` (or call_dev.py): also quoted, after `cd ... &&`, in Bash or PowerShell
_BRIDGE_CALL = re.compile(r"""(?:^|[\s"'/\\])call(?:_dev)?\.py["']?\s+["']?([A-Za-z_]\w*)""")
SHELLS = ("Bash", "PowerShell")


def bridge_methods(command) -> list:
    """The tool names a shell command calls through the bridge's call.py / call_dev.py (none for any other command)."""
    return _BRIDGE_CALL.findall(command) if isinstance(command, str) else []


def _time(text, tz):
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (when.astimezone(tz) if when.tzinfo else when).replace(tzinfo=None)


def _person_text(message: dict) -> bool:
    content = (message or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").strip() for b in content)
    return False


def read(paths, tz=None) -> dict:
    """{messages: [{id, session, start, end, out, ctx, tools: [short tool names]}], assistant: [ts], person: [ts]}.

    Times are naive local datetimes (like the timeline's); `tz` overrides the zone (tests).
    """
    messages, order, assistant, person = {}, [], [], []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(line, dict) or line.get("isSidechain") or line.get("isMeta"):
                    continue
                when = _time(line.get("timestamp"), tz)
                if when is None:
                    continue
                kind, message = line.get("type"), line.get("message") or {}
                if kind == "user" and _person_text(message):
                    person.append(when)
                elif kind == "assistant":
                    assistant.append(when)
                    key = message.get("id") or line.get("uuid") or f"{path}:{len(order)}"
                    entry = messages.get(key)
                    if entry is None:
                        entry = messages[key] = {"id": key, "session": line.get("sessionId"), "start": when,
                                                 "end": when, "out": 0, "ctx": 0, "tools": []}
                        order.append(key)
                    entry["end"] = max(entry["end"], when)
                    usage = message.get("usage") or {}
                    entry["out"] = max(entry["out"], int(usage.get("output_tokens") or 0))
                    entry["ctx"] = max(entry["ctx"], int(usage.get("input_tokens") or 0)
                                       + int(usage.get("cache_read_input_tokens") or 0)
                                       + int(usage.get("cache_creation_input_tokens") or 0))
                    for block in message.get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") not in \
                                entry.setdefault("seen", set()):
                            entry["seen"].add(block.get("id"))
                            name = str(block.get("name") or "")
                            if name in SHELLS:  # a job's calls go through the bridge: its method is the tool
                                entry["tools"] += bridge_methods((block.get("input") or {}).get("command"))
                            else:
                                entry["tools"].append(name.rsplit("__", 1)[-1])
    return {"messages": [messages[k] for k in order], "assistant": sorted(assistant), "person": sorted(person)}


def waits(data: dict) -> list:
    """[(when the person replied, seconds Claude had been waiting)]: from the last assistant line before it."""
    found = []
    for reply in data["person"]:
        before = [t for t in data["assistant"] if t < reply]
        if before and (reply - before[-1]).total_seconds() >= 1:
            found.append((reply, (reply - before[-1]).total_seconds()))
    return found
