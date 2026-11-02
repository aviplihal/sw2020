"""The COM worker's timeout: per call, and a message that tells a long call
from a dialog (pure; no SolidWorks: the jobs are plain Python callables)."""

import asyncio
import pathlib
import threading

import pytest

from solidworks_mcp.com_worker import ComWorker
from solidworks_mcp.errors import SolidWorksError
from solidworks_mcp.session import SolidWorksSession


@pytest.fixture
def worker():
    w = ComWorker(call_timeout_s=5.0)
    yield w
    w.shutdown()


def test_a_call_returns_its_result(worker):
    assert asyncio.run(worker.call(lambda: 42, label="get_status")) == 42


def test_a_slow_call_says_it_may_still_be_working(worker):
    release = threading.Event()
    with pytest.raises(SolidWorksError) as err:
        asyncio.run(worker.call(lambda: release.wait(10), timeout_s=0.2, label="make_drawing"))
    text = str(err.value)
    assert "make_drawing did not finish within 0s" in text
    assert "may still be working on it" in text and "make_drawing has been running for" in text
    assert "modal dialog is probably" not in text  # the old message blamed a dialog
    # the next call waits behind it and says so
    with pytest.raises(SolidWorksError, match="did not start within .*still busy with an earlier call.*"
                                              "make_drawing has been running"):
        asyncio.run(worker.call(lambda: 1, timeout_s=0.2, label="list_documents"))
    release.set()
    assert asyncio.run(worker.call(lambda: 2)) == 2


def test_the_timeout_per_call_overrides_the_default(worker):
    assert asyncio.run(worker.call(lambda: threading.Event().wait(0.3) or "done", timeout_s=2.0)) == "done"


def test_drawing_tools_get_longer_than_the_default():
    """make_drawing of the 33-item DTN6 drawing took 53-190 s on 2020: never
    120 s. The table is keyed by session method name, which _call reads."""
    source = pathlib.Path("src/solidworks_mcp/server.py").read_text(encoding="utf-8")
    assert '_CALL_TIMEOUT_S = {"make_drawing": 900.0, "check_drawing": 300.0}' in source
    assert callable(getattr(SolidWorksSession, "make_drawing"))
    assert SolidWorksSession.make_drawing.__name__ == "make_drawing"


def test_make_drawing_reports_its_slowest_items():
    timings = {"views": 9.04, "save": 1.26, "items": {"D1": 1.0, "D16": 12.345, "T1": 3.0}}
    report = SolidWorksSession._timing_summary(timings, 30.0)
    assert report == {"total": 30.0, "views": 9.0, "items": 16.3, "save": 1.3,
                      "slowest_items": {"D16": 12.35, "T1": 3.0, "D1": 1.0}}
    assert list(SolidWorksSession._timing_summary({"items": {f"N{i}": i for i in range(20)}}, 1.0)
                ["slowest_items"]) == [f"N{i}" for i in range(19, 11, -1)]
