"""Pure tests: every tool call is logged as one timeline row, and logging never breaks a call (no SolidWorks)."""

import io
import json
import os
import time
from datetime import timedelta

import pytest
from kb_helpers import T0, call, new_kb, open_case
from PIL import Image

from solidworks_mcp import __version__, server
from solidworks_mcp.errors import SolidWorksError
from solidworks_mcp.kb import log, store


@pytest.fixture
def kb(tmp_path):
    yield new_kb(tmp_path / "kb")
    log.use(None)


def rows(kb, job="DN17"):
    return kb.read_timeline(job)[1]


def test_a_success_is_one_row_with_its_measurements_duration_and_tokens(kb):
    open_case(kb)
    event = call("add_box", 5, run=3.4, arguments={"width_mm": 10, "name": None},
                 result={"ok": True, "mass_properties": {"volume_mm3": 85160.0,
                                                         "bounding_box_mm": {"size_mm": [76.2, 50.8, 38.1]}}})
    assert (event["job"], event["n"], event["ok"], event["signature"]) == ("DN17", 1, True, None)
    [row] = rows(kb)
    assert (row["tool"], row["args"], row["outcome"]) == ("add_box", "width_mm=10", "ok · V 85,160 · bbox 76.2×50.8×38.1")
    assert (row["run"], row["when"], row["pause"], row["read_only"]) == (3.4, T0 + timedelta(seconds=5), 0, False)
    assert row["tok"] == round(len(json.dumps({"ok": True, "mass_properties": {
        "volume_mm3": 85160.0, "bounding_box_mm": {"size_mm": [76.2, 50.8, 38.1]}}})) / 4)


def test_a_coded_failure_carries_its_code_and_a_short_message(kb):
    open_case(kb)
    event = call("add_fillet", 0, ok=False, code="FILLET_TOO_LARGE", message="Fillet 5 mm | does not fit")
    assert (event["ok"], event["signature"]) == (False, "FILLET_TOO_LARGE")
    [row] = rows(kb)
    assert (row["ok"], row["signature"], row["sample"]) == (False, "FILLET_TOO_LARGE", "Fillet 5 mm / does not fit")


def test_a_failure_without_a_code_is_signed_by_its_text(kb):
    open_case(kb)
    assert call("add_revolved_profile", 0, ok=False, message="SolidWorks COM error: Revolve1")["signature"].startswith("T:")


def test_kb_tools_are_logged_too_and_read_only_ones_are_marked(kb):
    open_case(kb)
    call("kb_view_region", 0, arguments={"box": [0.1, 0.2, 0.3, 0.4], "dn": None})
    call("list_faces", 5)
    call("add_box", 10)
    got = rows(kb)
    assert [(r["tool"], r["read_only"]) for r in got] == [("kb_view_region", True), ("list_faces", True), ("add_box", False)]
    assert got[0]["args"] == "box=[0.1,0.2,0.3,0.4]"
    assert "· kb_view_region |" in kb.timeline_file("DN17").read_text(encoding="utf-8")


def test_the_pause_is_the_time_since_the_previous_call_ended(kb, tmp_path):
    open_case(kb)
    call("add_box", 0, run=2)
    call("add_box", 10, run=2)
    call("add_box", 400, run=1)
    assert [r["pause"] for r in rows(kb)] == [0, 8, 388]
    log.use(store.KnowledgeBase(tmp_path / "kb"))  # a restarted server: nothing in memory
    call("add_box", 500, run=1)
    assert rows(kb)[-1]["pause"] == pytest.approx(99, abs=1.0)


def test_with_no_case_open_calls_go_to_unassigned(kb):
    call("get_status", 0)
    assert kb.timeline_file(store.UNASSIGNED).is_file() and kb.read_timeline(store.UNASSIGNED)[1][0]["tool"] == "get_status"
    open_case(kb)
    call("add_box", 5)
    assert [r["tool"] for r in rows(kb)] == ["add_box"] and len(kb.read_timeline(store.UNASSIGNED)[1]) == 1


def test_the_first_modelling_call_of_a_described_case_moves_it_on(kb):
    case = open_case(kb)
    kb.set_status(case, "described")
    call("list_faces", 0)
    call("kb_search", 1)
    assert kb.case("DN17")["status"] == "described", "looking is not modelling"
    call("add_box", 2)
    assert kb.case("DN17")["status"] == "modelling"


def test_the_call_that_abandons_a_case_is_logged_in_its_renamed_folder(kb):
    open_case(kb)
    call("add_box", 0)
    kb.abandon_open_case("wrong scan")
    call("kb_abandon_case", 5, hint="DN17")
    assert [r["tool"] for r in rows(kb, "DN17~1")] == ["add_box", "kb_abandon_case"]
    assert not (kb.folder / "fixes" / "DN17").exists()


def test_images_count_by_size_in_the_result_tokens_and_the_outcome(kb):
    open_case(kb)
    picture = io.BytesIO()
    Image.new("RGB", (1500, 970)).save(picture, format="JPEG")
    call("kb_ingest_drawing", 0, images=(picture.getvalue(),), result={"ok": True})
    [row] = rows(kb)
    assert row["outcome"] == "ok · overview 1500×970" and row["tok"] == 3 + round(1500 * 970 / 750)


def test_the_server_logs_a_call_by_its_parameter_names(kb):
    open_case(kb)

    def add_box(width_mm, height_mm, depth_mm, name="Box"):
        pass

    result = server._log_call(add_box, (10, 20), {"depth_mm": 5}, {"ok": True}, None, T0, T0 + timedelta(seconds=2))
    assert result == {"ok": True}
    [row] = rows(kb)
    assert (row["tool"], row["args"], row["run"]) == ("add_box", "width_mm=10, height_mm=20, depth_mm=5", 2.0)


def test_a_logging_failure_never_breaks_the_call(kb, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(log, "record_call", broken)
    result = server._log_call(lambda: None, (), {}, {"ok": True, "volume": 1}, None)
    assert result["ok"] is True and result["volume"] == 1
    assert result["kb_warning"] == "The knowledge base did not log this call: OSError: disk full"


def test_the_calls_session_and_versions_are_recorded_once(kb):
    open_case(kb)
    (kb.folder / "fixes" / ".session.json").write_text(
        json.dumps({"session_id": "efb18dde-0000", "transcript_path": "C:/t/efb18dde-0000.jsonl"}), encoding="utf-8")
    call("add_box", 0)
    call("add_box", 5)
    meta = kb.meta("DN17")
    assert meta["sessions"] == [{"session_id": "efb18dde-0000", "transcript_path": "C:/t/efb18dde-0000.jsonl",
                                 "from_call": 1}]
    assert meta["versions"] == [{"from_call": 1, "server": __version__, "sw": "28.5.0"}]
    (kb.folder / "fixes" / ".session.json").write_text(json.dumps({"session_id": "3c479092-1111"}), encoding="utf-8")
    call("add_box", 10)
    assert [(s["session_id"], s["from_call"]) for s in kb.meta("DN17")["sessions"]] == [("efb18dde-0000", 1),
                                                                                       ("3c479092-1111", 3)]


def test_a_stale_session_file_is_not_attributed_to_a_call(kb):
    open_case(kb)
    path = kb.folder / "fixes" / ".session.json"
    path.write_text(json.dumps({"session_id": "old-session"}), encoding="utf-8")
    old = time.time() - 86_400
    os.utime(path, (old, old))
    call("add_box", 0)
    assert kb.meta("DN17")["sessions"] == []


def test_an_error_from_the_server_keeps_its_code_through_the_log(kb):
    open_case(kb)
    error = SolidWorksError("too big", code="FILLET_TOO_LARGE")
    assert log.record_call("add_fillet", {}, {"ok": False, "error": "too big"}, error)["signature"] == "FILLET_TOO_LARGE"
