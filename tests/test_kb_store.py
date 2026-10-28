"""Pure tests: the knowledge base's storage and case lifecycle (no SolidWorks)."""

import sqlite3

import pytest

from solidworks_mcp.errors import SolidWorksError
from solidworks_mcp.kb.store import KnowledgeBase


@pytest.fixture
def kb(tmp_path):
    ticks = iter(f"2026-10-{d:02d}T09:00:00+00:00" for d in range(1, 29))
    store = KnowledgeBase(tmp_path / "kb", clock=lambda: next(ticks))
    yield store
    store.close()


def _model(kb, tool="add_box", error=None, signature=None, read_only=False, **extra):
    return kb.record_event(tool, read_only=read_only, arguments={"width_mm": 10}, result={"ok": error is None},
                           error=error, signature=signature, server_version="0.16.0", sw_revision="28.5.0", **extra)


def test_the_database_is_in_wal_mode_and_survives_reopening(tmp_path):
    store = KnowledgeBase(tmp_path / "kb")
    store.ingest(17, "DN17.pdf", 1)
    store.close()
    again = KnowledgeBase(tmp_path / "kb")
    assert again.open_case()["dn"] == "DN17", "the open case must survive a server restart"
    assert again._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    again.close()


def test_ingest_opens_a_case(kb):
    case, warnings = kb.ingest(17, "DN17.pdf", 1)
    assert (case["dn"], case["status"], case["is_open"], warnings) == ("DN17", "ingested", 1, [])
    assert kb.case("dn17")["id"] == kb.case("17")["id"] == case["id"]
    assert (kb.folder / "cases").is_dir()


def test_only_one_case_is_open_at_a_time(kb):
    kb.ingest(17, "DN17.pdf", 1)
    with pytest.raises(SolidWorksError, match="DN17 is still open") as refused:
        kb.ingest(18, "DN18.pdf", 1)
    assert refused.value.code == "KB_CASE_ALREADY_OPEN"
    with pytest.raises(sqlite3.IntegrityError):  # the database itself allows one open case
        with kb._db:
            kb._db.execute("INSERT INTO cases (dn, number, status, is_open) VALUES ('DN99', 99, 'ingested', 1)")


def test_a_duplicate_dn_is_refused(kb):
    case, _ = kb.ingest(17, "DN17.pdf", 1)
    kb.set_status(case["id"], "abandoned")  # closed, but not renamed: still DN17
    with pytest.raises(SolidWorksError, match=r"DN17 already exists \(ingested 2026-10-01\)"):
        kb.ingest(17, "DN17.pdf", 1)


@pytest.mark.parametrize("earlier,new,warning", [(15, 17, "DN16 missing"), (12, 16, "DN13 to DN15 missing"),
                                                 (15, 16, None), (None, 40, None), (20, 17, None)])
def test_a_gap_in_the_dn_numbers_is_a_warning(kb, earlier, new, warning):
    if earlier is not None:
        case, _ = kb.ingest(earlier, f"DN{earlier}.pdf", 1)
        kb.update_case(case["id"], status="finalised", is_open=0)
    _, warnings = kb.ingest(new, f"DN{new}.pdf", 1)
    assert warnings == ([] if warning is None else [f"{warning}: the highest earlier case is DN{earlier}."])


def test_the_lifecycle_moves_only_forward(kb):
    case, _ = kb.ingest(17, "DN17.pdf", 1)
    with pytest.raises(SolidWorksError, match="DN17 is ingested; it cannot become finalised"):
        kb.set_status(case["id"], "finalised")
    kb.set_status(case["id"], "described", description="a bracket")
    kb.set_status(case["id"], "modelling")
    done = kb.set_status(case["id"], "finalised")
    assert (done["is_open"], kb.open_case()) == (0, None)
    assert kb.set_status(case["id"], "changes_needed")["status"] == "changes_needed"
    assert kb.set_status(case["id"], "approved")["status"] == "approved"
    with pytest.raises(SolidWorksError, match="cannot become modelling"):
        kb.set_status(case["id"], "modelling")


def test_json_fields_round_trip(kb):
    case, _ = kb.ingest(17, "DN17.pdf", 1)
    spec = {"units": "in", "items": [{"id": "D1", "kind": "dimension", "value": ".375", "decimals": 3}]}
    assert kb.update_case(case["id"], spec=spec)["spec"] == spec


def test_abandoning_frees_the_dn_and_keeps_the_record(kb):
    case, _ = kb.ingest(17, "DN17.pdf", 1)
    (kb.case_folder("DN17")).mkdir()
    gone = kb.abandon_open_case("wrong scan")
    assert (gone["dn"], gone["status"], gone["is_open"], gone["abandoned_reason"]) == \
        ("DN17~1", "abandoned", 0, "wrong scan")
    assert kb.case_folder("DN17~1").is_dir() and not kb.case_folder("DN17").exists()
    kb.ingest(17, "DN17.pdf", 1)
    assert kb.abandon_open_case("dropped")["dn"] == "DN17~2"
    kb.ingest(17, "DN17.pdf", 1)
    with pytest.raises(SolidWorksError, match="Say why"):
        kb.abandon_open_case("  ")


def test_abandon_needs_an_open_case(kb):
    with pytest.raises(SolidWorksError, match="No knowledge-base case is open"):
        kb.abandon_open_case("nothing to abandon")


def test_events_join_the_open_case_in_order(kb):
    _model(kb, tool="new_part")  # no case open: logged, but to no case
    case, _ = kb.ingest(17, "DN17.pdf", 1)
    _model(kb, tool="add_box")
    _model(kb, tool="list_faces", read_only=True)
    events = kb.events(case["id"])
    assert [(e["seq"], e["tool"], e["read_only"]) for e in events] == [(1, "add_box", 0), (2, "list_faces", 1)]
    assert [e["tool"] for e in kb.events(case["id"], include_read_only=False)] == ["add_box"]
    assert events[0]["arguments"] == {"width_mm": 10} and events[0]["sw_revision"] == "28.5.0"
    assert kb._db.execute("SELECT COUNT(*) FROM events WHERE case_id IS NULL").fetchone()[0] == 1


def test_the_first_modelling_call_moves_a_described_case_on(kb):
    case, _ = kb.ingest(17, "DN17.pdf", 1)
    kb.set_status(case["id"], "described")
    _model(kb, tool="list_faces", read_only=True)
    assert kb.open_case()["status"] == "described", "looking is not modelling"
    _model(kb, tool="add_box")
    assert kb.open_case()["status"] == "modelling"


def test_failures_are_counted_by_tool_and_signature(kb):
    _model(kb, tool="add_fillet", error="too big", signature="FILLET_TOO_LARGE")
    kb.ingest(9, "DN9.pdf", 1)
    _model(kb, tool="add_fillet", error="too big again", signature="FILLET_TOO_LARGE")
    _model(kb, tool="add_fillet", error="other", signature="T:abc")
    _model(kb, tool="add_chamfer", error="too big", signature="FILLET_TOO_LARGE")
    _model(kb, tool="add_fillet")  # a success counts nowhere here
    [top, other] = kb.error_signatures("add_fillet")
    assert (top["signature"], top["count"], top["last_case"], top["sample"]) == \
        ("FILLET_TOO_LARGE", 2, "DN9", "too big again")
    assert (other["signature"], other["count"]) == ("T:abc", 1)
    assert len(kb.error_signatures("add_chamfer")) == 1
