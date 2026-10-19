"""Unit helpers.

The SolidWorks API works in METRES and RADIANS internally, regardless of the
document's display units. Values crossing the COM boundary are converted here so
the rest of the code can think in millimetres and degrees -- this is one of the
most common silent bug sources in SolidWorks automation.
"""

import math
from decimal import Decimal, InvalidOperation


def mm_to_m(value_mm: float) -> float:
    return value_mm / 1000.0


def m_to_mm(value_m: float) -> float:
    return value_m * 1000.0


def deg_to_rad(value_deg: float) -> float:
    return math.radians(value_deg)


MM_PER_INCH = 25.4  # exact, by definition


def in_to_mm(value_in: float | str) -> float:
    """Inches to millimetres. A string is read exactly as written ('.375'),
    so a drawing's value is converted without a float rounding step first."""
    if isinstance(value_in, str):
        try:
            return float(Decimal(value_in.strip()) * Decimal("25.4"))
        except InvalidOperation:
            raise ValueError(f"Not a number of inches: {value_in!r}") from None
    return value_in * MM_PER_INCH


def mm_to_in(value_mm: float) -> float:
    return value_mm / MM_PER_INCH
