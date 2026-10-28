"""Tool cards: one short card per MCP tool, built from the event log.

A card is stored as fields, not as one text. Claude writes only the words: when
to use the tool, when not, each failure's 'cause -> fix' (keyed by its error
signature) and lessons from reviews. The server renders the card and adds
everything that is counted or generated: the header, the 'How to call' line
from the tool's docstring, each failure's count, last case and versions. So
Claude cannot drop, invent or miscount a failure. Hard cap: 8 bullets and
about 150 words of Claude's text (Docs/KNOWLEDGE-BASE.md §7). Pure: stdlib only.
"""

from ..errors import SolidWorksError

MAX_BULLETS = 8
MAX_WORDS = 150
FIELDS = ("use_when", "dont_use_when", "failures", "lessons")
SUCCESSES_TO_RETIRE = 3


def word_count(fields: dict) -> int:
    texts = [fields.get("use_when") or "", fields.get("dont_use_when") or ""]
    texts += list((fields.get("failures") or {}).values()) + list(fields.get("lessons") or [])
    return sum(len(text.split()) for text in texts)


def bullets(fields: dict) -> list:
    """The card's bullets: one per distinct failure text (signatures with the same
    cause share one), then the lessons."""
    return list(dict.fromkeys((fields.get("failures") or {}).values())) + list(fields.get("lessons") or [])


def active_signatures(kb, tool: str) -> list[dict]:
    """The tool's failure groups still worth a line, most frequent first.

    A failure is retired once it has not been seen since the server version or
    the SOLIDWORKS revision changed, and the tool has since worked at least 3
    times: a later release fixed it, and it should not steer agents away.
    """
    latest = kb.latest_versions(tool)
    active = []
    for row in kb.error_signatures(tool):
        moved_on = latest is not None and (row["last_server_version"], row["last_sw_revision"]) != latest
        if moved_on and kb.successes_since(tool, row["signature"]) >= SUCCESSES_TO_RETIRE:
            continue
        active.append(row)
    return active


def must_cover(kb, tool: str) -> list[dict]:
    """The failure groups a tidied card must give a text: the most frequent active ones, up to the cap."""
    return active_signatures(kb, tool)[:MAX_BULLETS]


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
                              "tool's most frequent failures. Give several the same text if they share a cause.",
                              code="KB_CARD_DROPS_FAILURE")
    count, words = len(bullets(fields)), word_count(fields)
    if count > MAX_BULLETS or words > MAX_WORDS:
        raise SolidWorksError(f"The card has {count} bullets and {words} words; the cap is {MAX_BULLETS} bullets "
                              f"and {MAX_WORDS} words. Shorten it: merge failures with one cause into one text.",
                              code="KB_CARD_TOO_LONG", params={"bullets": count, "words": words})
    return {key: fields[key] for key in FIELDS if fields.get(key)}


def render(tool: str, card: dict | None, active: list, how_to_call: str | None) -> str:
    """The card as the agent reads it. Failures not tidied yet show their raw error once."""
    fields = (card or {}).get("fields") or {}
    if card:
        header = (f"{tool}    (server {card['server_version']}, SOLIDWORKS {card['sw_revision'] or '?'}, "
                  f"updated {card['updated_at'][:10]})")
    else:
        header = f"{tool}    (no card yet: it is written at the next finalise that uses this tool)"
    lines = [header]
    if fields.get("use_when"):
        lines.append(f"Use when:       {fields['use_when']}")
    if fields.get("dont_use_when"):
        lines.append(f"Don't use when: {fields['dont_use_when']}")
    if how_to_call:
        lines.append(f"How to call:    {how_to_call}")
    worded = fields.get("failures") or {}
    grouped = {}
    for row in active:
        text = worded.get(row["signature"])
        key = text or f"(not tidied yet) {row['sample']}"
        group = grouped.setdefault(key, {"count": 0, "last_seen": "", "row": row})
        group["count"] += row["count"]
        if row["last_seen"] >= group["last_seen"]:
            group["last_seen"], group["row"] = row["last_seen"], row
    if grouped:
        lines.append("Known failures:")
        for text, group in grouped.items():
            row = group["row"]
            seen = f"seen {group['count']}×" + (f", last {row['last_case']}" if row["last_case"] else "")
            versions = f"{row['last_server_version'] or '?'} / {row['last_sw_revision'] or '?'}"
            lines.append(f"  - {text}    ({seen}; {versions})")
    if fields.get("lessons"):
        lines.append("Lessons from reviews:")
        lines += [f"  - {lesson}" for lesson in fields["lessons"]]
    return "\n".join(lines)


def hint(card: dict | None, signature: str | None) -> str | None:
    """The card's line for this failure, for the error result (kb_hint); None without one."""
    if not card or not signature:
        return None
    return ((card.get("fields") or {}).get("failures") or {}).get(signature)
