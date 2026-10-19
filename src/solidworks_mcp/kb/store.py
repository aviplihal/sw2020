"""The knowledge base on disk: one SQLite file plus a folder per case.

SQLite in WAL mode, so a crash mid-write cannot corrupt it; one writer (the
one server process Claude Desktop runs). Backup = copy the folder. The open
case lives in the database, not in memory, so it survives the server being
restarted. Design: Docs/KNOWLEDGE-BASE.md §3 and §12.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..errors import SolidWorksError
from . import dn as dn_names

SCHEMA_VERSION = 1

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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY,
    dn TEXT NOT NULL UNIQUE,              -- 'DN17'; an abandoned case becomes 'DN17~1'
    number INTEGER NOT NULL,
    status TEXT NOT NULL,
    is_open INTEGER NOT NULL DEFAULT 0,
    source_name TEXT, page INTEGER,
    part_type TEXT, description TEXT, handwritten_dn TEXT,
    spec TEXT, spec_history TEXT,         -- JSON: the latest spec, and the earlier ones
    embedding_pending INTEGER NOT NULL DEFAULT 0,
    drawing_check TEXT,                   -- JSON: check_drawing's result at finalise
    finalise_measurements TEXT, review_measurements TEXT, review_changes TEXT,  -- JSON
    verdict TEXT, review_notes TEXT, abandoned_reason TEXT,
    ingested_at TEXT, described_at TEXT, finalised_at TEXT, reviewed_at TEXT, abandoned_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_case ON cases(is_open) WHERE is_open = 1;
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    case_id INTEGER REFERENCES cases(id),  -- NULL: no case was open
    seq INTEGER,                           -- order within the case
    tool TEXT NOT NULL,
    read_only INTEGER NOT NULL,
    arguments TEXT, result TEXT,           -- JSON
    ok INTEGER NOT NULL,
    error TEXT, error_code TEXT, error_params TEXT, signature TEXT,
    probe TEXT,                            -- JSON: measurements taken after a failure
    server_version TEXT, sw_revision TEXT,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_case ON events(case_id, seq);
CREATE INDEX IF NOT EXISTS events_by_tool ON events(tool);
CREATE TABLE IF NOT EXISTS error_signatures (
    tool TEXT NOT NULL, signature TEXT NOT NULL,
    code TEXT, sample TEXT,
    count INTEGER NOT NULL,
    first_seen TEXT, last_seen TEXT, last_case TEXT,
    last_server_version TEXT, last_sw_revision TEXT,
    PRIMARY KEY (tool, signature)
);
CREATE TABLE IF NOT EXISTS tool_cards (
    tool TEXT PRIMARY KEY,
    fields TEXT NOT NULL,                 -- JSON: the words Claude wrote, by part and signature
    server_version TEXT, sw_revision TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS vectors (
    case_id INTEGER NOT NULL REFERENCES cases(id),
    kind TEXT NOT NULL,                   -- view_front / view_top / view_side / sheet / description
    model TEXT NOT NULL, model_version TEXT,
    dim INTEGER NOT NULL, data BLOB NOT NULL,
    PRIMARY KEY (case_id, kind)
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(case_id UNINDEXED, field UNINDEXED, text);
"""

_JSON_COLUMNS = {"spec", "spec_history", "drawing_check", "finalise_measurements", "review_measurements",
                 "review_changes", "arguments", "result", "error_params", "probe", "fields"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(row) -> dict | None:
    if row is None:
        return None
    return {key: (json.loads(row[key]) if key in _JSON_COLUMNS and row[key] is not None else row[key])
            for key in row.keys()}


def _dump(value) -> str | None:
    return None if value is None else json.dumps(value, default=str, ensure_ascii=False)


class KnowledgeBase:
    """Cases, the event log and error signatures. Every method commits what it changes."""

    def __init__(self, folder: Path, clock=_now) -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "cases").mkdir(exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.folder / "kb.sqlite", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        try:
            self._db.executescript(_SCHEMA)
        except sqlite3.OperationalError as exc:
            if "fts5" in str(exc):
                raise SolidWorksError("This Python's sqlite3 has no FTS5, which the knowledge base's keyword "
                                      "search needs. Install Python from python.org (its sqlite3 includes it).") from exc
            raise
        with self._db:
            self._db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    def close(self) -> None:
        self._db.close()

    def case_folder(self, dn: str) -> Path:
        return self.folder / "cases" / dn

    # --- cases ----------------------------------------------------------------

    def open_case(self) -> dict | None:
        return _row(self._db.execute("SELECT * FROM cases WHERE is_open = 1").fetchone())

    def require_open_case(self) -> dict:
        case = self.open_case()
        if case is None:
            raise SolidWorksError("No knowledge-base case is open. Start one with kb_ingest_drawing.",
                                  code="KB_NO_OPEN_CASE")
        return case

    def case(self, dn: str) -> dict | None:
        """A case by its DN ('DN17', 'dn17', '17' all work)."""
        number = dn_names.from_written(str(dn)) if not str(dn).isdigit() else int(dn)
        key = dn_names.name(number) if number else str(dn)
        return _row(self._db.execute("SELECT * FROM cases WHERE dn = ?", (key,)).fetchone())

    def ingest(self, number: int, source_name: str, page: int) -> tuple[dict, list]:
        """Open a new case for DN<number>; returns it and any warnings (a gap in the numbers).

        Refused while another case is open, or when the DN already exists
        (an abandoned case does not count: it was renamed DN17~1).
        """
        open_case = self.open_case()
        if open_case is not None:
            raise SolidWorksError(f"{open_case['dn']} is still open ({open_case['status']}). Finalise it "
                                  "(kb_finalise_case) or abandon it (kb_abandon_case) first.",
                                  code="KB_CASE_ALREADY_OPEN")
        name = dn_names.name(number)
        existing = self.case(name)
        if existing is not None:
            raise SolidWorksError(f"{name} already exists (ingested {existing['ingested_at'][:10]}).",
                                  code="KB_DUPLICATE_DN")
        warnings = []
        highest = self._db.execute(
            "SELECT MAX(number) FROM cases WHERE status != 'abandoned'").fetchone()[0]
        if highest is not None and number > highest + 1:
            missing = (f"DN{highest + 1}" if number == highest + 2
                       else f"DN{highest + 1} to DN{number - 1}")
            warnings.append(f"{missing} missing: the highest earlier case is DN{highest}.")
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO cases (dn, number, status, is_open, source_name, page, ingested_at) "
                "VALUES (?, ?, 'ingested', 1, ?, ?, ?)", (name, number, source_name, page, self._clock()))
        return self.case(name), warnings

    def set_status(self, case_id: int, status: str, **fields) -> dict:
        """Move a case on in its lifecycle (§3), storing `fields` with it."""
        current = _row(self._db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone())
        if status not in TRANSITIONS[current["status"]]:
            raise SolidWorksError(f"{current['dn']} is {current['status']}; it cannot become {status}.",
                                  code="KB_BAD_TRANSITION")
        fields["status"] = status
        fields["is_open"] = 1 if status in OPEN_STATUSES else 0
        return self.update_case(case_id, **fields)

    def update_case(self, case_id: int, **fields) -> dict:
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = [_dump(v) if key in _JSON_COLUMNS else v for key, v in fields.items()]
        with self._lock, self._db:
            self._db.execute(f"UPDATE cases SET {columns} WHERE id = ?", (*values, case_id))
        return _row(self._db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone())

    def abandon_open_case(self, reason: str) -> dict:
        """Close the open case without finishing it. It is kept, renamed DN17~1
        (~2, ...), so its DN can be ingested again as a fresh case."""
        if not (reason or "").strip():
            raise SolidWorksError("Say why the case is abandoned (reason).", code="KB_NO_REASON")
        case = self.require_open_case()
        tries = self._db.execute("SELECT COUNT(*) FROM cases WHERE dn LIKE ?", (case["dn"] + "~%",)).fetchone()[0]
        renamed = f"{case['dn']}~{tries + 1}"
        self.set_status(case["id"], "abandoned", dn=renamed, abandoned_reason=reason.strip(),
                        abandoned_at=self._clock())
        folder = self.case_folder(case["dn"])
        if folder.exists():
            folder.rename(self.case_folder(renamed))
        return self.case(renamed) or _row(self._db.execute(
            "SELECT * FROM cases WHERE id = ?", (case["id"],)).fetchone())

    # --- events ---------------------------------------------------------------

    def record_event(self, tool: str, *, read_only: bool, arguments: dict, result: dict, error: str | None = None,
                     error_code: str | None = None, error_params: dict | None = None,
                     signature: str | None = None, probe: dict | None = None,
                     server_version: str | None = None, sw_revision: str | None = None) -> dict:
        """Append one tool call to the log, to the open case if there is one.

        A failure also counts towards its (tool, signature) group. The first
        modelling call of a described case moves it to 'modelling'.
        """
        case = self.open_case()
        now = self._clock()
        ok = error is None
        with self._lock, self._db:
            seq = None
            if case is not None:
                seq = self._db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE case_id = ?",
                                       (case["id"],)).fetchone()[0]
            cursor = self._db.execute(
                "INSERT INTO events (case_id, seq, tool, read_only, arguments, result, ok, error, error_code, "
                "error_params, signature, probe, server_version, sw_revision, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case and case["id"], seq, tool, int(read_only), _dump(arguments), _dump(result), int(ok), error,
                 error_code, _dump(error_params), signature, _dump(probe), server_version, sw_revision, now))
            if not ok:
                self._db.execute(
                    "INSERT INTO error_signatures VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (tool, signature) DO UPDATE SET count = count + 1, last_seen = excluded.last_seen, "
                    "last_case = COALESCE(excluded.last_case, last_case), sample = excluded.sample, "
                    "last_server_version = excluded.last_server_version, last_sw_revision = excluded.last_sw_revision",
                    (tool, signature, error_code, error, now, now, case and case["dn"], server_version, sw_revision))
            if case is not None and case["status"] == "described" and not read_only:
                self._db.execute("UPDATE cases SET status = 'modelling' WHERE id = ?", (case["id"],))
        return _row(self._db.execute("SELECT * FROM events WHERE id = ?", (cursor.lastrowid,)).fetchone())

    def events(self, case_id: int, include_read_only: bool = True) -> list[dict]:
        query = "SELECT * FROM events WHERE case_id = ?" + ("" if include_read_only else " AND read_only = 0")
        return [_row(r) for r in self._db.execute(query + " ORDER BY seq", (case_id,))]

    def error_signatures(self, tool: str) -> list[dict]:
        return [_row(r) for r in self._db.execute(
            "SELECT * FROM error_signatures WHERE tool = ? ORDER BY count DESC, last_seen DESC", (tool,))]
