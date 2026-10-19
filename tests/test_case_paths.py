"""Pure tests: which file save_part and make_drawing write (no SolidWorks)."""

import os

import pytest

from solidworks_mcp import case_paths
from solidworks_mcp.errors import SolidWorksError


def test_a_solidworks_error_carries_an_optional_code_and_params():
    plain = SolidWorksError("refused")
    assert (str(plain), plain.code, plain.params) == ("refused", None, None)
    coded = SolidWorksError("too large", code="FILLET_TOO_LARGE", params={"radius_mm": 5})
    assert (str(coded), coded.code, coded.params) == ("too large", "FILLET_TOO_LARGE", {"radius_mm": 5})


def test_without_an_open_case_the_given_path_is_used():
    assert case_paths.resolve("part", "C:/jobs/bracket.sldprt") == "C:/jobs/bracket.sldprt"


@pytest.mark.parametrize("kind,tool", [("part", "save_part"), ("drawing", "make_drawing")])
def test_without_an_open_case_a_path_is_required(kind, tool):
    with pytest.raises(SolidWorksError, match=f"{tool} needs a path"):
        case_paths.resolve(kind, None)


def test_with_an_open_case_the_server_names_the_file(monkeypatch, tmp_path):
    named = str(tmp_path / "DN17.SLDPRT")
    monkeypatch.setattr(case_paths, "case_output_path", lambda kind: named)
    assert case_paths.resolve("part", None) == named
    assert case_paths.resolve("part", named) == named  # the same file is fine


def test_with_an_open_case_another_path_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(case_paths, "case_output_path", lambda kind: str(tmp_path / "DN17.SLDDRW"))
    with pytest.raises(SolidWorksError, match="DN17.SLDDRW. Call make_drawing without a path"):
        case_paths.resolve("drawing", str(tmp_path / "DN71.SLDDRW"))
    if os.name == "nt":  # Windows paths ignore case
        assert case_paths.resolve("drawing", str(tmp_path / "dn17.slddrw"))
