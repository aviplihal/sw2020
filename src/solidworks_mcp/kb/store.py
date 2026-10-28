"""The knowledge base on disk: Markdown and JSON files in the layout of Docs/KNOWLEDGE-BASE.md §2.

    INDEX.md  tools/<tool>.md  cases/DN17/{case.md, spec.json, images}      for CAD agents
    fixes/{HOTSPOTS.md, .session.json, DN17/{timeline.md, digest.md, meta.json}, _unassigned/...}   for fix agents

One writer (the one server process), every write atomic (files.atomic_write). The
open case is whichever case.md has an open `status:`, so it survives the server
being restarted. Backup = copy the folder.
"""

import re
from datetime import datetime
from pathlib import Path

from ..errors import SolidWorksError
from . import dn as dn_names
from . import timeline
from .files import atomic_write, atomic_write_json, read_json, read_text

# status -> the statuses it may move to (§3)
TRANSITIONS = {
    "ingested": {"described", "abandoned"},
    "described": {"described", "modelling", "abandoned"},
    "modelling": {"modelling", "finalised", "abandoned"},
    "finalised": {"approved", "changes_needed"},
    "approved": {"approved", "changes_needed"},
    "changes_needed": {"approved", "changes_needed"},
    "abandoned": set(),
}
OPEN_STATUSES = {"ingested", "described", "modelling"}
STATUSES = set(TRANSITIONS)
UNASSIGNED = "_unassigned"

HEADER_KEYS = ("dn", "status", "part_type", "size_mm", "page", "dpi", "ingested", "finalised", "reviewed",
               "abandoned", "reason")
SECTIONS = ("Description", "Drawing", "Questions asked", "How it was modelled", "Result", "Review", "Lessons")
TIERS = {"approved": 0, "changes_needed": 1}  # everything else is unreviewed: 2
MARKS = {0: "✓", 1: "⚠", 2: "?"}


def tier(case: dict) -> int:
    return TIERS.get(case["status"], 2)


def render_case(case: dict) -> str:
    """case.md: a short YAML header, a title line and the fixed sections in their fixed order."""
    lines = ["---"] + [f"{key}: {case[key]}" for key in HEADER_KEYS if case.get(key) not in (None, "")] + ["---"]
    label = case["status"].replace("_", " ")
    lines.append(f"# {case['dn']} · {case.get('part_type') or '?'} · {label}")
    sections = case.get("sections") or {}
    ordered = [t for t in SECTIONS if t in sections] + [t for t in sections if t not in SECTIONS]
    for title in ordered:
        if (sections[title] or "").strip():
            lines += ["", f"## {title}", sections[title].strip()]
    return "\n".join(lines) + "\n"


def parse_case(text: str) -> dict:
    head, _, body = text.partition("\n---\n")
    if not head.startswith("---"):
        raise SolidWorksError("case.md has no header.", code="KB_BAD_CASE_FILE")
    case = {}
    for line in head.splitlines()[1:]:
        key, _, value = line.partition(":")
        if key.strip() in HEADER_KEYS:
            case[key.strip()] = value.strip()
    for key in ("page", "dpi"):
        if key in case:
            case[key] = int(case[key])
    match = re.fullmatch(r"DN(\d+)(?:~\d+)?", case.get("dn", ""))
    if not match or case.get("status") not in STATUSES:
        raise SolidWorksError(f"case.md header has a bad dn or status ({case.get('dn')!r}, {case.get('status')!r}).",
                              code="KB_BAD_CASE_FILE")
    case["number"] = int(match.group(1))
    sections, title, chunk = {}, None, []
    for line in body.splitlines():
        if line.startswith("## "):
            if title is not None:
                sections[title] = "\n".join(chunk).strip()
            title, chunk = line[3:].strip(), []
        elif title is not None:
            chunk.append(line)
    if title is not None:
        sections[title] = "\n".join(chunk).strip()
    case["sections"] = sections
    return case


def _first_sentence(text: str, limit: int = 80) -> str:
    one = " ".join((text or "").split())
    cut = re.split(r"(?<=[.!?])\s", one, maxsplit=1)[0]
    return cut if len(cut) <= limit else cut[:limit - 1].rstrip() + "…"


def _lesson(case: dict) -> str:
    for line in (case["sections"].get("Lessons") or "").splitlines():
        if line.strip().startswith("- "):
            return line.strip()[2:].strip()
    return "not reviewed yet" if tier(case) == 2 else "—"


def largest_side(case: dict) -> int | None:
    numbers = re.findall(r"\d+(?:\.\d+)?", case.get("size_mm") or "")
    return round(max(float(n) for n in numbers)) if numbers else None


def index_line(case: dict) -> str:
    """One INDEX.md table row: DN, type, largest side, mark, summary · lesson."""
    side = largest_side(case)
    summary = _first_sentence(case["sections"].get("Description") or "no description yet").rstrip(".")
    return (f"| {case['dn']} | {case.get('part_type') or '?'} | {side if side is not None else '—'} | "
            f"{MARKS[tier(case)]} | {summary.replace('|', '/')} · {_lesson(case).replace('|', '/')} |")


def order_key(case: dict) -> tuple:
    """Approved, then changes needed, then unreviewed; newest DN first within each."""
    return tier(case), -case["number"]


def render_index(cases: list) -> str:
    live = sorted((c for c in cases if c["status"] != "abandoned"), key=order_key)
    kinds = {}
    for case in live:
        label = case.get("part_type") or "?"
        kinds[label] = kinds.get(label, 0) + 1
    counts = [sum(1 for c in live if tier(c) == t) for t in (0, 1, 2)]
    types = ", ".join(f"{k} {n}" for k, n in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))) or "none yet"
    lines = ["# Knowledge base index", "",
             f"{len(live)} cases · {counts[0]} approved · {counts[1]} changes needed · {counts[2]} unreviewed · "
             f"part types: {types}",
             "✓ approved · ⚠ changes needed · ? unreviewed. Largest side in mm.", "",
             "| DN | Type | Size | | Summary · lesson |", "|---|---|---|---|---|"]
    return "\n".join(lines + [index_line(c) for c in live]) + "\n"


class KnowledgeBase:
    """Cases, INDEX.md, timelines and tool-card files. Every method that changes something writes it atomically."""

    def __init__(self, folder, clock=datetime.now) -> None:
        self.folder = Path(folder)
        self._clock = clock
        (self.folder / "cases").mkdir(parents=True, exist_ok=True)
        (self.folder / "tools").mkdir(exist_ok=True)
        (self.folder / "fixes").mkdir(exist_ok=True)

    def close(self) -> None:
        """Nothing is held open; kept so a KnowledgeBase can be used like a resource."""

    def now(self) -> datetime:
        return self._clock()

    def today(self) -> str:
        return self.now().date().isoformat()

    # --- paths -------------------------------------------------------------------

    def case_folder(self, dn: str) -> Path:
        return self.folder / "cases" / dn

    def fixes_folder(self, job: str) -> Path:
        return self.folder / "fixes" / job

    def tool_file(self, tool: str) -> Path:
        return self.folder / "tools" / f"{tool}.md"

    @property
    def index_file(self) -> Path:
        return self.folder / "INDEX.md"

    @property
    def hotspots_file(self) -> Path:
        return self.folder / "fixes" / "HOTSPOTS.md"

    @property
    def session_file(self) -> Path:
        return self.folder / "fixes" / ".session.json"

    # --- cases ---------------------------------------------------------------------

    def case_names(self) -> list:
        base = self.folder / "cases"
        return sorted(p.name for p in base.iterdir() if (p / "case.md").is_file()) if base.is_dir() else []

    def read_case(self, dn: str) -> dict | None:
        path = self.case_folder(dn) / "case.md"
        return parse_case(read_text(path)) if path.is_file() else None

    def case(self, dn) -> dict | None:
        """A case by its DN ('DN17', 'dn17', '17' all work; 'DN17~1' is an abandoned one)."""
        text = str(dn).strip()
        if re.fullmatch(r"(?i)dn\d+~\d+", text):
            return self.read_case(text.upper())
        number = int(text) if text.isdigit() else dn_names.from_written(text)
        return self.read_case(dn_names.name(number)) if number else None

    def cases(self, include_abandoned: bool = False) -> list:
        found = [self.read_case(name) for name in self.case_names()]
        return [c for c in found if c and (include_abandoned or c["status"] != "abandoned")]

    def open_case(self) -> dict | None:
        """The one case that is ingested, described or modelling (read from the headers)."""
        found = [c for c in self.cases() if c["status"] in OPEN_STATUSES]
        if len(found) > 1:
            raise SolidWorksError(f"{' and '.join(c['dn'] for c in found)} are both open, but only one case may be: "
                                  "abandon one with kb_abandon_case, or fix the status: line in its case.md.",
                                  code="KB_TWO_OPEN_CASES")
        return found[0] if found else None

    def require_open_case(self) -> dict:
        case = self.open_case()
        if case is None:
            raise SolidWorksError("No knowledge-base case is open. Start one with kb_ingest_drawing.",
                                  code="KB_NO_OPEN_CASE")
        return case

    def write_case(self, case: dict) -> dict:
        atomic_write(self.case_folder(case["dn"]) / "case.md", render_case(case))
        self.rebuild_index()
        return case

    def update_case(self, case: dict, sections: dict | None = None, **fields) -> dict:
        """Set header fields and replace whole sections (one set to '' is removed); writes case.md and INDEX.md."""
        case = {**case, **fields, "sections": {**case["sections"], **(sections or {})}}
        case["sections"] = {k: v for k, v in case["sections"].items() if (v or "").strip()}
        return self.write_case(case)

    def set_status(self, case: dict, status: str, sections: dict | None = None, **fields) -> dict:
        """Move a case on in its lifecycle (§3)."""
        if status not in TRANSITIONS[case["status"]]:
            raise SolidWorksError(f"{case['dn']} is {case['status']}; it cannot become {status}.",
                                  code="KB_BAD_TRANSITION")
        return self.update_case(case, sections, status=status, **fields)

    def ingest(self, number: int, source_name: str, page: int) -> tuple:
        """Open a new case for DN<number>; returns it and any warnings (a gap in the numbers).

        Refused while another case is open, or when the DN already exists (an
        abandoned case does not count: its folder was renamed DN17~1).
        """
        open_case = self.open_case()
        if open_case is not None:
            raise SolidWorksError(f"{open_case['dn']} is still open ({open_case['status']}). Finalise it "
                                  "(kb_finalise_case) or abandon it (kb_abandon_case) first.",
                                  code="KB_CASE_ALREADY_OPEN")
        name = dn_names.name(number)
        existing = self.read_case(name)
        if existing is not None:
            raise SolidWorksError(f"{name} already exists (ingested {existing.get('ingested', '?')}).",
                                  code="KB_DUPLICATE_DN")
        warnings = []
        highest = max((c["number"] for c in self.cases()), default=None)
        if highest is not None and number > highest + 1:
            missing = (f"DN{highest + 1}" if number == highest + 2 else f"DN{highest + 1} to DN{number - 1}")
            warnings.append(f"{missing} missing: the highest earlier case is DN{highest}.")
        case = {"dn": name, "number": number, "status": "ingested", "page": page, "ingested": self.today(),
                "sections": {"Drawing": f"input.pdf page {page} (from {source_name})"}}
        return self.write_case(case), warnings

    def abandon_open_case(self, reason: str) -> dict:
        """Close the open case without finishing it. Its folders are renamed DN17~1 (~2, ...), so its DN
        can be ingested again as a fresh case, and it is left out of INDEX.md."""
        if not (reason or "").strip():
            raise SolidWorksError("Say why the case is abandoned (reason).", code="KB_NO_REASON")
        case = self.require_open_case()
        tries = sum(1 for name in self.case_names() if name.startswith(case["dn"] + "~"))
        renamed = f"{case['dn']}~{tries + 1}"
        self.case_folder(case["dn"]).rename(self.case_folder(renamed))
        if self.fixes_folder(case["dn"]).exists():
            self.fixes_folder(case["dn"]).rename(self.fixes_folder(renamed))
        return self.update_case(case, dn=renamed, status="abandoned", abandoned=self.today(),
                                reason=" ".join(reason.split()))

    def resolve_job(self, dn: str) -> str:
        """The folder name a job is under now: 'DN17', or 'DN17~k' once it was abandoned."""
        if self.case_folder(dn).exists() or self.fixes_folder(dn).exists():
            return dn
        renamed = sorted((n for n in self.case_names() if n.startswith(dn + "~")),
                         key=lambda n: int(n.split("~")[1]))
        return renamed[-1] if renamed else dn

    # --- INDEX.md ---------------------------------------------------------------------

    def rebuild_index(self) -> str:
        """Rebuilt on every case change: approved, then changes needed, then unreviewed; abandoned left out."""
        text = render_index(self.cases())
        atomic_write(self.index_file, text)
        return text

    def index_text(self) -> str:
        return read_text(self.index_file) if self.index_file.is_file() else self.rebuild_index()

    # --- timelines and job files ---------------------------------------------------------

    def jobs(self) -> list:
        base = self.folder / "fixes"
        return sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []

    def timeline_file(self, job: str) -> Path:
        return self.fixes_folder(job) / "timeline.md"

    def read_timeline(self, job: str) -> tuple:
        """(day, rows) of a job's timeline; (today, []) when it has none yet."""
        path = self.timeline_file(job)
        return timeline.parse(read_text(path)) if path.is_file() else (self.today(), [])

    def append_row(self, job: str, row: dict) -> dict:
        """Add one call to the job's timeline (rewritten atomically); returns the row with its number."""
        day, rows = self.read_timeline(job)
        if not rows:
            day = row["when"].date().isoformat()
        row = {**row, "n": len(rows) + 1}
        atomic_write(self.timeline_file(job), timeline.render(job, day, rows + [row]))
        return row

    def write_timeline(self, job: str, day: str, rows: list) -> None:
        atomic_write(self.timeline_file(job), timeline.render(job, day, rows))

    def meta(self, job: str) -> dict:
        """{"sessions": [{session_id, transcript_path, from_call}], "versions": [{from_call, server, sw}]}"""
        found = read_json(self.fixes_folder(job) / "meta.json", {})
        return {"sessions": found.get("sessions", []), "versions": found.get("versions", [])}

    def write_meta(self, job: str, meta: dict) -> None:
        atomic_write_json(self.fixes_folder(job) / "meta.json", meta)

    def read_session_file(self) -> dict | None:
        """The hook's {session_id, transcript_path}, if the hook has run (§10.2)."""
        found = read_json(self.session_file) if self.session_file.is_file() else None
        return found if isinstance(found, dict) and found.get("session_id") else None

    def digest_file(self, job: str) -> Path:
        return self.fixes_folder(job) / "digest.md"
