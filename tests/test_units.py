"""Pure unit tests for unit conversions (no SolidWorks)."""

import math

from solidworks_mcp.units import deg_to_rad, m_to_mm, mm_to_m


def test_mm_to_m():
    assert mm_to_m(1000) == 1.0
    assert mm_to_m(0) == 0.0
    assert mm_to_m(40) == 0.04


def test_m_to_mm():
    assert m_to_mm(1.0) == 1000.0
    assert m_to_mm(0.04) == 40.0


def test_length_roundtrip():
    for v in (0, 1, 12.5, 8000):
        assert abs(m_to_mm(mm_to_m(v)) - v) < 1e-9


def test_deg_to_rad():
    assert deg_to_rad(0) == 0.0
    assert abs(deg_to_rad(180) - math.pi) < 1e-12
    assert abs(deg_to_rad(360) - 2 * math.pi) < 1e-12
    assert abs(deg_to_rad(45) - math.pi / 4) < 1e-12


def test_an_inch_is_exactly_25_4_mm():
    from solidworks_mcp.units import in_to_mm, mm_to_in
    assert in_to_mm(1) == 25.4
    assert in_to_mm(".375") == 9.525  # read as written, no float step first
    assert in_to_mm(".50") == 12.7
    assert in_to_mm("1.0005") == 25.4127
    assert mm_to_in(25.4) == 1.0
    for v in (".375", "2.125", ".0005"):
        assert abs(mm_to_in(in_to_mm(v)) - float(v)) < 1e-12


def test_an_unreadable_inch_value_is_refused():
    import pytest

    from solidworks_mcp.units import in_to_mm
    with pytest.raises(ValueError, match="Not a number of inches"):
        in_to_mm("1/4")
