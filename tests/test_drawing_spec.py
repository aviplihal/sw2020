"""Pure tests for drawing_spec (no SolidWorks): the §4.1 spec format, inch values
as written, how each dimension is shown, the views/placements arguments and the
comparison check_drawing makes."""

import copy
import json
import math
import os
from fractions import Fraction

import pytest

from solidworks_mcp import drawing_spec as ds
from solidworks_mcp.errors import SolidWorksError

# The example in Docs/SHOP-DRAWINGS.md §4.1, verbatim.
EXAMPLE = {
    "units": "in",
    "items": [
        {"id": "D1", "kind": "dimension", "value": ".375", "decimals": 3,
         "tolerance": {"plus": ".005", "minus": ".005"},
         "view": "front", "between": "left face to bore centre", "status": "read"},
        {"id": "T1", "kind": "thread", "callout": "1/4-20 UNC-2B", "depth": ".50",
         "view": "top", "status": "read"},
        {"id": "H1", "kind": "hole", "callout": "Ø.266 THRU, ⌴ Ø.438 ↧ .25",
         "view": "top", "status": "read"},
        {"id": "F1", "kind": "finish", "symbol": "machined", "value": "63",
         "surface": "bore", "status": "read"},
        {"id": "A", "kind": "datum", "label": "A", "feature": "bottom face", "status": "read"},
        {"id": "G1", "kind": "gdt", "characteristic": "position", "tolerance": "Ø.010",
         "modifier": "MMC", "datums": ["A", "B", "C"], "feature": "4X Ø.266 holes",
         "status": "read"},
        {"id": "N1", "kind": "note", "text": "BREAK ALL SHARP EDGES .010 MAX", "status": "read"},
        {"id": "D7", "kind": "dimension", "value": None, "view": "right",
         "between": "slot width", "status": "unreadable"},
    ],
}


def dim(value, decimals=None, tolerance=None, sid="D1"):
    return {"units": "in", "items": [{"id": sid, "kind": "dimension", "value": value, "decimals": decimals,
                                      "tolerance": tolerance, "between": "x", "status": "read"}]}


def first(spec):
    return ds.validate(spec)["items"][0]


# --- values as written ---------------------------------------------------------------

@pytest.mark.parametrize("text, nominal, fmt, places", [
    (".375", Fraction(3, 8), "decimal", 3),
    ("1.250", Fraction(5, 4), "decimal", 3),
    (".50", Fraction(1, 2), "decimal", 2),
    ("2", Fraction(2), "decimal", 0),
    ("5 15/16", Fraction(95, 16), "fraction", None),
    ("1/4", Fraction(1, 4), "fraction", None),
    ("1-1/4", Fraction(5, 4), "fraction", None),
    ("Ø.266", Fraction(266, 1000), "decimal", 3),
])
def test_values_are_read_exactly_as_written(text, nominal, fmt, places):
    value = ds.parse_value(text)
    assert (value.nominal, value.format, value.places, value.kind) == (nominal, fmt, places, "length")


def test_angles_and_limits():
    angle = ds.parse_value("45°")
    assert (angle.kind, angle.nominal, angle.places) == ("angle", 45, 0)
    limits = ds.parse_value(".500/.498")
    assert (limits.format, limits.upper, limits.lower, limits.places) == ("limits", Fraction(1, 2), Fraction(498, 1000), 3)


@pytest.mark.parametrize("bad", ["", "abc", "3/7", "5/4", ".498/.500", ".50/.498", None, 0.375, "1/128"])
def test_unreadable_values_are_refused(bad):
    with pytest.raises(SolidWorksError):
        ds.parse_value(bad)


def test_millimetres_come_from_units_exactly():
    assert ds.parse_value("5 15/16").mm() == 150.8125
    assert ds.parse_value(".375").mm() == 9.525
    assert ds.parse_value("25/32").mm() == 19.84375


# --- the spec format -----------------------------------------------------------------------

def test_the_design_example_validates():
    items = ds.validate(EXAMPLE)["items"]
    assert [i["id"] for i in items] == ["D1", "T1", "H1", "F1", "A", "G1", "N1", "D7"]
    assert items[0]["parsed"].display == "symmetric"
    assert "parsed" not in items[-1]  # unreadable: kept, never parsed or guessed


def test_summary_converts_and_lists_unreadable():
    out = ds.summary(EXAMPLE)
    assert out["dimensions"] == [{"id": "D1", "value": ".375", "between": "left face to bore centre", "mm": 9.525}]
    assert out["unreadable"] == [{"id": "D7", "kind": "dimension", "remark": "slot width"}]
    assert out["counts"]["dimension"] == 2  # the unreadable one counts too


@pytest.mark.parametrize("change, message", [
    (lambda s: s.update(units="mm"), "units"),
    (lambda s: s["items"].append(dict(s["items"][0])), "share the id"),
    (lambda s: s["items"][0].update(status="maybe"), "status"),
    (lambda s: s["items"][0].update(decimals=2), "decimal places"),
    (lambda s: s["items"][0].update(value=0.375), "string as written"),
    (lambda s: s["items"][0].update(colour="red"), "unknown field"),
    (lambda s: s["items"][0].pop("between"), "needs between"),
    (lambda s: s["items"][3].update(symbol="ground"), "symbol"),
    (lambda s: s["items"][4].update(label="a"), "capital letter"),
    (lambda s: s["items"][5].update(characteristic="wobble"), "characteristic"),
    (lambda s: s["items"][5].update(datums=["A", "B", "C", "D"]), "up to 3"),
    (lambda s: s["items"][5].update(modifier="XYZ"), "modifier"),
])
def test_bad_specs_are_refused_by_name(change, message):
    spec = copy.deepcopy(EXAMPLE)
    change(spec)
    with pytest.raises(SolidWorksError, match=message):
        ds.validate(spec)


def test_fractions_have_no_decimals_and_decimals_must_match():
    first(dim("5/8"))
    with pytest.raises(SolidWorksError, match="fraction"):
        first(dim("5/8", 3))
    with pytest.raises(SolidWorksError, match="give decimals"):
        first(dim(".625"))


def test_limits_need_a_consistent_tolerance():
    item = first(dim(".500/.498", 3, {"plus": ".000", "minus": ".002", "display": "limits"}))
    assert item["parsed"].nominal == Fraction(1, 2)
    item = first(dim(".378/.375", 3, {"plus": ".003", "minus": ".000", "display": "limits"}))
    assert item["parsed"].nominal == Fraction(375, 1000)
    with pytest.raises(SolidWorksError, match="one nominal"):
        first(dim(".500/.498", 3, {"plus": ".001", "minus": ".002", "display": "limits"}))
    with pytest.raises(SolidWorksError, match="need their tolerance"):
        first(dim(".500/.498", 3))
    with pytest.raises(SolidWorksError, match="limits"):
        first(dim(".500", 3, {"plus": ".000", "minus": ".002", "display": "limits"}))


DTN6 = r"C:\Users\Sandbox\Projects\DTN6\DTN6_spec.json"


@pytest.mark.skipif(not os.path.isfile(DTN6), reason="the DTN6 job is only on the shop's machine")
def test_the_first_real_spec_validates():
    with open(DTN6, encoding="utf-8") as f:
        spec = json.load(f)
    out = ds.summary(spec)
    assert all(u["kind"] for u in out["unreadable"])  # which ones changes as the user answers
    mm = {d["id"]: d.get("mm") for d in out["dimensions"]}
    assert mm["D1"] == 150.8125 and mm["D13"] == 12.7 and mm["D10"] == 9.525


# --- how values are shown and compared ------------------------------------------------------

@pytest.mark.parametrize("number, places, text", [(0.375, 3, ".375"), (1.25, 3, "1.250"), (0.5, 2, ".50"),
                                                   (2, 0, "2"), (0, 3, ".000")])
def test_decimal_inches_ansi_style(number, places, text):
    assert ds.format_decimal(number, places) == text


@pytest.mark.parametrize("number, text", [(5.9375, "5 15/16"), (0.25, "1/4"), (2, "2"), (0.78125, "25/32"),
                                          (1 + 1 / 64, "1 1/64")])
def test_fractions_reduced(number, text):
    assert ds.format_fraction(number) == text


@pytest.mark.parametrize("value, decimals, tolerance, text", [
    (".375", 3, {"plus": ".005", "minus": ".005"}, ".375 ±.005"),
    (".375", 3, {"plus": ".002", "minus": ".001"}, ".375 +.002/-.001"),
    (".500/.498", 3, {"plus": ".000", "minus": ".002", "display": "limits"}, ".500/.498"),
    ("5 15/16", None, None, "5 15/16"),
    ("30°", None, None, "30°"),
])
def test_expected_text(value, decimals, tolerance, text):
    assert ds.expected_text(first(dim(value, decimals, tolerance))) == text


def test_a_limit_dimension_reads_back_ok_from_any_model_value_inside():
    item = first(dim(".500/.498", 3, {"plus": ".000", "minus": ".002", "display": "limits"}))
    read = {"value": 0.499, "decimals": 3, "tolerance": "limits", "tol_max": 0.001, "tol_min": -0.001, "tol_decimals": 3}
    assert ds.compare_dimension(item, read)["status"] == "ok"
    read.update(value=0.497, tol_max=0.003, tol_min=0.001)  # shows .500/.498 but the model is undersize
    out = ds.compare_dimension(item, read)
    assert out["status"] == "differs" and "outside" in out["detail"]


def test_a_fraction_hides_no_model_error():
    item = first(dim("15/16"))
    assert ds.compare_dimension(item, {"value": 0.9375, "fraction": True, "denominator": 64})["status"] == "ok"
    out = ds.compare_dimension(item, {"value": 0.938, "fraction": True, "denominator": 64})
    assert out["status"] == "differs" and out["shown"] == "15/16" and "measures" in out["detail"]


def test_wrong_precision_differs():
    item = first(dim(".50", 2))
    out = ds.compare_dimension(item, {"value": 0.5, "decimals": 3})
    assert out["status"] == "differs" and out["shown"] == ".500"


def test_note_symbols_round_trip():
    text = "Ø.266 THRU, ⌴ Ø.438 ↧ .25"
    assert ds.to_sw_text(text) == "<MOD-DIAM>.266 THRU, <HOLE-CBORE> <MOD-DIAM>.438 <HOLE-DEPTH> .25"
    assert ds.from_sw_text(ds.to_sw_text(text)) == text
    assert ds.norm_text("1/32 x 45<MOD-DEG>  CHAM") == ds.norm_text("1/32 X 45° CHAM")


# --- views and placements -------------------------------------------------------------------

def test_default_views_are_front_top_right():
    assert [v["id"] for v in ds.validate_views(None)] == ["front", "top", "right"]


@pytest.mark.parametrize("views, message", [
    ([{"kind": "standard", "direction": "top"}], "first view is the drawing's front"),
    ([{"kind": "isometric"}], "No isometric"),
    ([{"kind": "standard", "direction": "front"}, {"kind": "standard", "direction": "back"}], "top, bottom, left or right"),
    ([{"kind": "standard", "model_view": "front", "up": "+z"}], "line of sight"),
    ([{"kind": "standard", "direction": "front"},
      {"kind": "section", "from": "top", "cut": "vertical", "label": "A", "through_mm": [0, 0, 0]}], "earlier view"),
    ([{"kind": "standard", "direction": "front"},
      {"kind": "detail", "from": "front", "label": "B", "center_mm": [0, 0, 0], "radius_mm": 5, "scale": "2"}], "scale"),
])
def test_bad_views_are_refused(views, message):
    with pytest.raises(SolidWorksError, match=message):
        ds.validate_views(views)


def test_section_and_detail_views():
    views = ds.validate_views([
        {"kind": "standard", "direction": "front"},
        {"kind": "section", "from": "front", "cut": "vertical", "label": "A", "through_mm": [0, 10, 0]},
        {"kind": "detail", "from": "section A", "label": "B", "center_mm": [4, 0, 0], "radius_mm": 3, "scale": "4:1"},
    ])
    assert [v["id"] for v in views] == ["front", "section A", "detail B"]
    assert views[2]["scale"] == (4, 1)


def placements_for_example():
    return {
        "D1": {"view": "front", "measure": "horizontal", "points_mm": [[0, 0, 0], [9.525, 0, 0]]},
        "T1": {"view": "top", "point_mm": [1, 2, 3]},
        "H1": {"view": "top", "point_mm": [1, 2, 3]},
        "F1": {"view": "front", "point_mm": [1, 2, 3]},
        "A": {"view": "front", "point_mm": [0, 0, 0]},
        "G1": {"view": "top", "on": "H1"},
    }


def test_every_read_item_is_placed():
    checked = ds.validate(EXAMPLE)
    out = ds.validate_placements(placements_for_example(), checked, {"front", "top"})
    assert out["D1"]["offset_mm"] == 10.0 and "N1" not in out
    partial = placements_for_example()
    del partial["F1"]
    with pytest.raises(SolidWorksError, match="no placement for F1"):
        ds.validate_placements(partial, checked, {"front", "top"})


@pytest.mark.parametrize("sid, place, message", [
    ("D7", {"view": "front"}, "unreadable"),
    ("D1", {"view": "side", "measure": "horizontal", "points_mm": [[0, 0, 0], [1, 0, 0]]}, "view ids"),
    ("D1", {"view": "front", "measure": "diameter", "points_mm": [[0, 0, 0], [1, 0, 0]]}, "1 model point"),
    ("D1", {"view": "front", "measure": "angle", "points_mm": [[0, 0, 0], [1, 0, 0]]}, "needs measure"),
    ("A", {"view": "front"}, "either on"),
    ("G1", {"view": "front", "on": "N1"}, "dimension, thread or hole"),
])
def test_bad_placements_are_refused(sid, place, message):
    placements = placements_for_example()
    placements[sid] = place
    with pytest.raises(SolidWorksError, match=message):
        ds.validate_placements(placements, ds.validate(EXAMPLE), {"front", "top"})


# --- the whole check -------------------------------------------------------------------------

def test_compare_reports_ok_missing_differs_unreadable_and_extras():
    read = {
        "dimensions": [
            {"name": "SPEC_D1", "view": "Drawing View1", "value": 0.375, "decimals": 3, "tolerance": "symmetric",
             "tol_max": 0.005, "tol_min": -0.005, "tol_decimals": 3},
            {"name": "RD9", "view": "Drawing View1", "value": 1.0, "decimals": 3, "shown": "1.000"},
        ],
        "notes": [{"name": "SPEC_T1", "text": "1/4-20 UNC-2B\n<HOLE-DEPTH> .50"},
                  {"name": "SPEC_H1", "text": "<MOD-DIAM>.266 THRU"},
                  {"name": "Note7", "text": "BREAK ALL SHARP EDGES .010 MAX"}],
        "datums": [{"name": "SPEC_A", "label": "A"}],
        "gtols": [{"name": "SPEC_G1", "characteristic": "position", "tolerance": "Ø.010", "modifier": "MMC",
                   "datums": ["A", "B", "C"]}],
        "finishes": [],
    }
    out = ds.compare(EXAMPLE, read)
    status = {line["id"]: line["status"] for line in out["items"]}
    assert status == {"D1": "ok", "T1": "ok", "H1": "differs", "F1": "missing", "A": "ok", "G1": "ok",
                      "N1": "ok", "D7": "unreadable"}
    assert out["clean"] is False
    assert out["not_in_spec"] == [{"group": "dimensions", "name": "RD9", "view": "Drawing View1", "shown": "1.000"}]


# --- orientation and layout --------------------------------------------------------------

def test_the_natural_front_projects_to_the_standard_views():
    o = ds.view_orientations()
    assert {k: (v["model_view"], v["angle_deg"]) for k, v in o.items()} == {
        "front": ("front", 0.0), "top": ("top", 0.0), "bottom": ("bottom", 0.0),
        "right": ("right", 0.0), "left": ("left", 0.0)}


def test_a_shaft_along_y_drawn_lying_down():
    """DTN6 is turned about the model's Y axis; on the drawing it lies along the sheet."""
    o = ds.view_orientations("front", "-x")  # model -X up: model +Y points to the sheet's right
    assert o["front"]["right"] == (0, 1, 0)
    assert (o["front"]["model_view"], o["front"]["angle_deg"]) == ("front", -90.0)  # turned clockwise
    # the right view looks at the end of the shaft, from +Y
    assert o["right"]["towards_viewer"] == (0, 1, 0) and o["right"]["model_view"] == "top"


def test_extent_and_layout_pick_the_largest_scale_that_fits():
    views = ds.validate_views([{"kind": "standard", "model_view": "front", "up": "-x"},
                               {"kind": "standard", "direction": "right"}])
    o = ds.view_orientations("front", "-x")
    size = [20.0, 150.8, 20.0]  # a shaft along Y
    assert ds.view_extent(size, o["front"]) == (150.8, 20.0)
    out = ds.layout(views, o, size, [12.7, 63.5, 266.7, 203.2])
    assert out["scale"] == (1, 1)
    (fx, fy), (rx, ry) = out["centres_mm"]["front"], out["centres_mm"]["right"]
    assert fy == ry and rx > fx  # third angle: the right view to the right, on the same row
    big = ds.layout(views, o, [200, 800, 200], [12.7, 63.5, 266.7, 203.2])
    assert big["scale"] == (1, 5)  # 200 + 50 at 1:4 plus gaps is wider than 254


def test_the_angle_text_goes_in_the_sector_of_the_spec_angle():
    """A 45 deg bevel came out 135 when SolidWorks chose the side of the text."""
    vertical = ((0.0, 0.0), (0.0, 1.0))
    bevel = ((5.0, 5.0), (1.0, 1.0))  # a 45 deg line through the origin
    x, y = ds.angle_text_spot(vertical, bevel, 45, 10)
    assert math.isclose(math.degrees(math.atan2(y, x)), 67.5) and math.isclose(math.hypot(x, y), 10)
    x, y = ds.angle_text_spot(vertical, bevel, 135, 10)
    assert math.isclose(math.degrees(math.atan2(y, x)), 157.5)
    with pytest.raises(SolidWorksError, match="not 30"):
        ds.angle_text_spot(vertical, bevel, 30, 10)
    # the opposite sector shows the same 45 deg: take the one away from the part
    x, y = ds.angle_text_spot(vertical, bevel, 45, 10, away_from=(3, 6))
    assert math.isclose(math.degrees(math.atan2(y, x)), -112.5)


def test_an_angle_is_exact_to_a_thousandth_of_a_degree():
    """DTN6's 30 deg chamfer measured 29.9999 from a radius entered as 5.42076 mm."""
    item = first(dim("30°"))
    assert ds.compare_dimension(item, {"value": 29.9999, "angle": True})["status"] == "ok"
    assert ds.compare_dimension(item, {"value": 29.99, "angle": True})["status"] == "differs"


def test_leader_texts_do_not_land_on_each_other():
    slots = ds.TextSlots()
    slots.reserve("v", "above", 18)  # dimensions up to 18 mm above the view
    a = slots.place("v", "above", 50, 100, 40)
    b = slots.place("v", "above", 60, 100, 20)  # overlaps a: the next line out
    c = slots.place("v", "above", 120, 100, 20)  # clear of a: same line
    assert a[1] == c[1] == 100 + 18 + 8 and b[1] == a[1] + ds.LINE_MM
    assert slots.place("v", "below", 50, 80, 40)[1] == 80 - 8  # below: nothing reserved there


def test_detail_views_go_in_a_row_below():
    views = ds.validate_views([{"kind": "standard", "model_view": "front", "up": "-x"},
                               {"kind": "standard", "direction": "right"},
                               {"kind": "detail", "from": "front", "label": "B", "center_mm": [6, 128, 0],
                                "radius_mm": 4, "scale": "4:1"}])
    out = ds.layout(views, ds.view_orientations("front", "-x"), [20, 150.8, 20], [16, 64, 262, 199])
    (fx, fy), (dx, dy) = out["centres_mm"]["front"], out["centres_mm"]["detail B"]
    assert out["scale"] == (1, 1) and dy < fy


# --- basic, reference, prefix/suffix, projected zones (DTN7) ------------------------------

def decorated_dim(value, decimals, tolerance=None, **extra):
    spec = dim(value, decimals, tolerance)
    spec["items"][0].update(extra)
    return first(spec)


@pytest.mark.parametrize("extra, value, decimals, text", [
    ({"basic": True}, "3.936", 3, "[3.936]"),
    ({"basic": True, "prefix": "10X"}, "36°", 0, "10X [36°]"),
    ({"reference": True}, "6.78", 2, "(6.78)"),
    ({"suffix": "MAX"}, ".033", 3, ".033 MAX"),
    ({"prefix": "4X", "basic": True}, "1.625", 3, "4X [1.625]"),
])
def test_basic_reference_and_text_around_a_value(extra, value, decimals, text):
    item = decorated_dim(value, decimals, **extra)
    assert ds.expected_text(item) == text
    show = ds.display_settings(item)
    assert show["tolerance"] == ("basic" if extra.get("basic") else "none")
    assert show["reference"] == bool(extra.get("reference"))


def test_a_suffix_follows_the_tolerance():
    item = decorated_dim(".306", 3, {"plus": ".001", "minus": ".001"}, suffix="TYP")
    assert ds.expected_text(item) == ".306 ±.001 TYP"


@pytest.mark.parametrize("extra, message", [
    ({"basic": True, "reference": True}, "basic or reference, not both"),
    ({"basic": "yes"}, "true or false"),
    ({"prefix": ""}, "prefix is the text"),
    ({"suffix": "MAX\nTYP"}, "suffix is the text"),
])
def test_bad_decorations_are_refused(extra, message):
    with pytest.raises(SolidWorksError, match=message):
        decorated_dim("3.936", 3, **extra)


def test_a_basic_dimension_has_no_tolerance():
    with pytest.raises(SolidWorksError, match="no tolerance"):
        decorated_dim("3.936", 3, {"plus": ".005", "minus": ".005"}, basic=True)


def test_a_lost_box_or_bracket_reads_differs():
    basic = decorated_dim("36°", 0, basic=True, prefix="10X")
    read = {"value": 36.0, "angle": True, "decimals": 0, "tolerance": "basic", "prefix": "10X"}
    assert ds.compare_dimension(basic, read)["status"] == "ok"
    assert ds.compare_dimension(basic, {**read, "tolerance": "none"})["status"] == "differs"
    assert ds.compare_dimension(basic, {**read, "prefix": ""})["status"] == "differs"
    ref = decorated_dim(".75", 2, reference=True)
    read = {"value": .7515, "decimals": 2, "tolerance": "none", "reference": True}
    assert ds.compare_dimension(ref, read)["status"] == "ok", "a reference value is the model rounded"
    assert ds.compare_dimension(ref, {**read, "reference": False})["status"] == "differs"
    assert ds.compare_dimension(ref, {**read, "value": .7600})["status"] == "differs"


def test_a_projected_tolerance_zone_is_part_of_the_frame():
    spec = {"units": "in", "items": [{"id": "G7", "kind": "gdt", "characteristic": "position",
                                      "tolerance": "Ø.041", "modifier": None, "projected": ".56",
                                      "datums": ["A", "B", "C"], "feature": "4X radial", "status": "read"}]}
    got = {"name": "SPEC_G7", "characteristic": "position", "tolerance": "Ø.041", "modifier": None,
           "datums": ["A", "B", "C"], "projected": ".56"}
    lines = ds.compare(spec, {"gtols": [got]})["items"]
    assert lines[0]["status"] == "ok" and lines[0]["spec"] == "position | Ø.041 Ⓟ .56 | A | B | C"
    assert ds.compare(spec, {"gtols": [{**got, "projected": None}]})["items"][0]["status"] == "differs"
    spec["items"][0]["projected"] = "0.56 in"
    with pytest.raises(SolidWorksError, match="projected"):
        ds.validate(spec)


# --- angled sections, sections of sections, marks ---------------------------------------

FRONT_TOP = {"kind": "standard", "model_view": "top", "up": "+x"}  # DTN7: looking down the axis, slot up


def test_an_angled_section_through_two_points_stands_upright():
    a, b = [132.284, 47.625, -96.109], [-132.284, 47.625, 96.109]
    views = ds.validate_views([FRONT_TOP, {"kind": "section", "from": "front", "line_mm": [a, b], "up": "+y",
                                           "label": "C"}])
    assert views[1]["cut"] == "angled" and views[1]["through_mm"] == a
    o = ds.view_orientations("top", "+x")
    axes = ds.plan_axes(views, o)["section C"]
    assert axes["up"] == pytest.approx((0, 1, 0))  # the part's axis up the sheet
    along = [math.cos(math.radians(36)), 0, -math.sin(math.radians(36))]
    assert abs(ds._dot(axes["right"], along)) == pytest.approx(1.0)  # along the cutting line
    # -36 deg from +x lies up and left with +x up and +z right: the line falls at -54 deg on the sheet
    assert ds.cut_angle_deg(views[1], o["front"]) == pytest.approx(-54.0, abs=1e-3)


def test_a_section_of_a_section_looks_across_its_parent():
    views = ds.validate_views([FRONT_TOP, {"kind": "section", "from": "front", "cut": "vertical", "label": "A",
                                           "through_mm": [0, 0, 0]},
                               {"kind": "section", "from": "section A", "cut": "vertical", "label": "B",
                                "through_mm": [0, 47.625, 0]}])
    axes = ds.plan_axes(views, ds.view_orientations("top", "+x"))
    # section A looks across the front's right (z): the axis y runs across the sheet, as on 2020
    assert axes["section A"]["right"] == (0, 1, 0) and axes["section A"]["up"] == (1, 0, 0)
    # section B, cut at a height y in A, looks along the axis again: a plan
    assert abs(axes["section B"]["towards_viewer"][1]) == 1


@pytest.mark.parametrize("view, message", [
    ({"cut": "vertical", "angle_deg": 30, "through_mm": [0, 0, 0]}, "give one of"),
    ({"cut": "vertical", "through_mm": [0, 0, 0], "up": "+y"}, "up turns an angled section"),
    ({"line_mm": [[0, 0, 0], [0, 0, 0]]}, "the same point"),
    ({"line_mm": [[0, 0, 0], [1, 0, 0]], "through_mm": [0, 0, 0]}, "leave through_mm out"),
    ({"angle_deg": "36", "through_mm": [0, 0, 0]}, "angle_deg is"),
])
def test_bad_cuts_are_refused(view, message):
    with pytest.raises(SolidWorksError, match=message):
        ds.validate_views([FRONT_TOP, {"kind": "section", "from": "front", "label": "A", **view}])


def test_the_cutting_line_is_clipped_to_the_view():
    ends = ds.clip_line((5, 5), (1, 1), (0, 0, 10, 20))
    assert ends[0] == pytest.approx((0, 0)) and ends[1] == pytest.approx((10, 10))
    assert ds.clip_line((5, 30), (1, 0), (0, 0, 10, 20)) is None
    vertical = ds.clip_line((3, 4), (0, 1), (0, 0, 10, 20))
    assert vertical[0] == pytest.approx((3, 0)) and vertical[1] == pytest.approx((3, 20))


MARKS = [{"kind": "point", "id": "O", "at_mm": [0, 88.9, 0]},
         {"kind": "circle", "id": "BC", "center_mm": [0, 88.9, 0], "through_mm": [119.27, 88.9, 38.75]},
         {"kind": "line", "id": "CL1", "from_mm": [0, 88.9, 0], "to_mm": [119.27, 88.9, 38.75]},
         {"kind": "line", "id": "CL2", "from_mm": [0, 88.9, 0], "to_mm": [99.97, 88.9, 0], "extend_mm": 0},
         {"kind": "center_mark", "at_mm": [0, 85.83, 0]}]


def test_marks_are_checked_and_carry_ids():
    views = ds.validate_views([{**FRONT_TOP, "marks": MARKS}])
    marks = views[0]["marks"]
    assert [m["id"] for m in marks] == ["O", "BC", "CL1", "CL2", None]
    assert marks[2]["extend_mm"] == 5.0 and marks[3]["extend_mm"] == 0.0
    with pytest.raises(SolidWorksError, match="used twice"):
        ds.validate_views([{**FRONT_TOP, "marks": MARKS + [{"kind": "point", "id": "O", "at_mm": [0, 0, 0]}]}])
    with pytest.raises(SolidWorksError, match="needs an id"):
        ds.validate_views([{**FRONT_TOP, "marks": [{"kind": "line", "from_mm": [0, 0, 0], "to_mm": [1, 0, 0]}]}])
    with pytest.raises(SolidWorksError, match="kind is one of"):
        ds.validate_views([{**FRONT_TOP, "marks": [{"kind": "arc", "id": "X"}]}])


def basic_spec(*items):
    return ds.validate({"units": "in", "items": [
        {"id": sid, "kind": "dimension", "value": value, "decimals": decimals, "tolerance": None,
         "basic": True, "between": "x", "status": "read"} for sid, value, decimals in items]})


def test_placements_measure_to_marks_of_their_view():
    views = ds.validate_views([{**FRONT_TOP, "marks": MARKS}])
    marks = {v["id"]: v["marks"] for v in views}
    checked = basic_spec(("D7", "3.936", 3), ("D8", "9.875", 3), ("D9", "18°", 0))
    places = ds.validate_placements({
        "D7": {"view": "front", "measure": "vertical", "points_mm": ["O", [99.9744, 88.9, 0]]},
        "D8": {"view": "front", "measure": "diameter", "points_mm": ["BC"]},
        "D9": {"view": "front", "measure": "angle", "points_mm": ["CL2", "CL1"]}}, checked, {"front"}, marks)
    assert places["D7"]["points_mm"][0] == "O" and places["D8"]["points_mm"] == ["BC"]
    for bad, message in [({"measure": "diameter", "points_mm": ["CL1"]}, "measures to a circle"),
                         ({"measure": "diameter", "points_mm": ["XX"]}, "no mark of view")]:
        with pytest.raises(SolidWorksError, match=message):
            ds.validate_placements({"D8": {"view": "front", **bad}, "D7": places["D7"], "D9": places["D9"]},
                                   checked, {"front"}, marks)
    with pytest.raises(SolidWorksError, match="measures to a line"):
        ds.validate_placements({"D9": {"view": "front", "measure": "angle", "points_mm": ["O", "CL1"]},
                                "D7": places["D7"], "D8": places["D8"]}, checked, {"front"}, marks)


def test_the_angle_text_goes_between_centrelines_from_a_centre():
    up, right = ((0.0, 0.0), (0.0, 1.0)), ((0.0, 0.0), (0.95, 0.31))  # a slot up, a hole 18 deg off it
    assert ds.ray_side((3, 9), up, right) > 0 > ds.ray_side((-3, -9), up, right)


def test_an_angled_section_takes_the_free_spot_that_keeps_the_scale():
    views = ds.validate_views([FRONT_TOP, {"kind": "section", "from": "front", "line_mm": [
        [132.284, 47.625, -96.109], [-132.284, 47.625, 96.109]], "up": "+y", "label": "C"},
        {"kind": "detail", "from": "section C", "label": "E", "center_mm": [132.3, 47.6, -96.1],
         "radius_mm": 15, "scale": "1:2"}])
    out = ds.layout(views, ds.view_orientations("top", "+x"), [344.12, 88.9, 344.12], [16, 64, 262, 199])
    assert out["scale"] == (1, 5)
    (fx, fy), (cx, cy), (dx, dy) = (out["centres_mm"][k] for k in ("front", "section C", "detail E"))
    assert cx > fx and dy < fy


# --- sheet sizes ----------------------------------------------------------------------------

def dtn7_views():
    def p(deg):
        a = math.radians(deg)
        return [163.5125 * math.cos(a), 47.625, 163.5125 * math.sin(a)]
    return ds.validate_views([
        FRONT_TOP,
        {"kind": "section", "from": "front", "cut": "vertical", "label": "A", "through_mm": [0, 0, 0]},
        {"kind": "section", "from": "section A", "cut": "vertical", "label": "B", "through_mm": [0, 47.625, 0]},
        {"kind": "section", "from": "front", "line_mm": [p(-36), p(144)], "up": "+y", "label": "C"},
        {"kind": "detail", "from": "section A", "label": "D", "center_mm": [168, 85, 0], "radius_mm": 8,
         "scale": "1:1"}])


def test_a_b_sheet_takes_dtn7_at_a_quarter():
    """DTN7 (Ø13.548 x 3.5) with front, A-A, B-B from A-A, angled C-C and a 1:1 detail:
    1:10 on the A sheet, 1:4 on B (verified on 2020: the title block clear)."""
    from solidworks_mcp.constants import SHEETS
    o, size = ds.view_orientations("top", "+x"), [344.12, 88.9, 344.12]
    assert ds.layout(dtn7_views(), o, size, SHEETS["A"]["area_mm"])["scale"] == (1, 10)
    out = ds.layout(dtn7_views(), o, size, SHEETS["B"]["area_mm"])
    assert out["scale"] == (1, 4)
    (dx, dy), (ax, ay), (cx, cy) = (out["centres_mm"][k] for k in ("detail D", "section A", "section C"))
    assert dx == ax and dy < ay, "the detail goes under its section, where there is room, not in a new row"
    for key in ("A", "B", "C"):  # every sheet's area lies inside its border (12.7 mm in) and above the title block
        x0, y0, x1, y1 = SHEETS[key]["area_mm"]
        w, h = SHEETS[key]["size_mm"]
        assert 12.7 < x0 and x1 < w - 12.7 and 62.7 < y0 and y1 < h - 12.7
