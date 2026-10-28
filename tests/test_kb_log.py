"""Pure tests: every tool call is logged automatically, and logging never breaks a call."""

import pytest

from solidworks_mcp import __version__, server
from solidworks_mcp.errors import SolidWorksError
from solidworks_mcp.kb import log
from solidworks_mcp.kb.store import KnowledgeBase


@pytest.fixture
def kb(tmp_path):
    store = KnowledgeBase(tmp_path / "kb")
    log.use(store)
    yield store
    log.use(None)
    store.close()


def _all_events(kb):
    return [dict(r) for r in kb._db.execute("SELECT * FROM events ORDER BY id")]


def test_a_success_is_logged_with_its_versions(kb):
    event = log.record_call("add_box", {"width_mm": 10}, {"ok": True, "mass_properties": {"volume_mm3": 1000}},
                            None, "28.5.0")
    assert (event["tool"], event["ok"], event["read_only"], event["signature"]) == ("add_box", 1, 0, None)
    assert (event["server_version"], event["sw_revision"]) == (__version__, "28.5.0")
    assert event["result"]["mass_properties"] == {"volume_mm3": 1000}


def test_a_coded_failure_is_logged_with_its_code_and_params(kb):
    error = SolidWorksError("Fillet 5 mm does not fit", code="FILLET_TOO_LARGE", params={"largest_mm": 1.9})
    event = log.record_call("add_fillet", {}, {"ok": False, "error": str(error)}, error)
    assert (event["ok"], event["error"], event["error_code"], event["signature"]) == \
        (0, "Fillet 5 mm does not fit", "FILLET_TOO_LARGE", "FILLET_TOO_LARGE")
    assert event["error_params"] == {"largest_mm": 1.9}


def test_a_failure_without_a_code_is_signed_by_its_text(kb):
    event = log.record_call("add_revolved_profile", {}, {"ok": False, "error": "SolidWorks COM error: Revolve1"},
                            RuntimeError("x"))
    assert event["signature"].startswith("T:") and event["error_code"] is None


def test_read_only_tools_are_marked_and_kb_tools_are_not_logged(kb):
    assert log.record_call("list_faces", {}, {"ok": True}, None)["read_only"] == 1
    assert log.record_call("kb_search", {}, {"ok": True}, None) is None
    assert [e["tool"] for e in _all_events(kb)] == ["list_faces"]


def test_a_huge_result_keeps_only_its_outline(kb):
    result = {"ok": True, "faces": [{"index": i, "normal": [0, 0, 1]} for i in range(5000)],
              "mass_properties": {"volume_mm3": 1}}
    stored = log.record_call("list_faces", {}, result, None)["result"]
    assert stored["ok"] is True and stored["mass_properties"] == {"volume_mm3": 1}
    assert "faces" not in stored and "keys: ok, faces, mass_properties" in stored["truncated"]


def test_every_read_only_tool_is_a_real_tool():
    assert log.READ_ONLY_TOOLS <= {name for name in dir(server) if not name.startswith("_")}


def test_the_server_logs_a_call_by_its_parameter_names(kb):
    def add_box(width_mm, height_mm, depth_mm, name="Box"):
        pass

    result = server._log_call(add_box, (10, 20), {"depth_mm": 5}, {"ok": True}, None)
    assert result == {"ok": True}
    [event] = _all_events(kb)
    assert event["tool"] == "add_box" and event["arguments"] == '{"width_mm": 10, "height_mm": 20, "depth_mm": 5}'


def test_a_logging_failure_never_breaks_the_call(kb, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(log, "record_call", broken)
    result = server._log_call(lambda: None, (), {}, {"ok": True, "volume": 1}, None)
    assert result["ok"] is True and result["volume"] == 1
    assert result["kb_warning"] == "The knowledge base did not log this call: OSError: disk full"
