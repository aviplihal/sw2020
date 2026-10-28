"""Pure tests for drawing_layout (no SolidWorks): boxes, how a text may move,
what it rides on, and the arranger keeping texts apart, on the sheet and
close to what they describe."""

import math

import pytest

from solidworks_mcp import drawing_layout as dl

AREA = (10.0, 10.0, 420.0, 270.0)
TITLE = (250.0, 10.0, 420.0, 60.0)
VIEW = {"V": (100.0, 100.0, 200.0, 180.0)}


def horizontal(key, span, line_at, box, view="V"):
    """A horizontal dimension of view V: witness points on the view's top edge."""
    return {"key": key, "kind": "dimension", "view": view, "box": box, "anchor": None,
            "dimension": {"move": "linear", "axis": "y", "span": span, "line_at": line_at,
                          "witness": [(span[0], 180.0), (span[1], 180.0)]}}


def note(key, box, anchor, view="V"):
    return {"key": key, "kind": "note", "view": view, "box": box, "anchor": anchor}


def placed(items, plan):
    return {it["key"]: dl._shift(it["box"], *plan["moves"].get(it["key"], (0.0, 0.0))) for it in items}


# --- geometry ------------------------------------------------------------------


def test_boxes_overlap_with_a_gap():
    assert dl.overlap((0, 0, 10, 10), (5, 5, 15, 15))
    assert not dl.overlap((0, 0, 10, 10), (11, 0, 20, 10))
    assert dl.overlap((0, 0, 10, 10), (11, 0, 20, 10), gap=2)
    assert dl.box_distance((0, 0, 10, 10), (13, 14, 20, 20)) == pytest.approx(5.0)


def test_a_segment_through_a_box():
    box = (0, 0, 10, 10)
    assert dl.segment_hits_box((-5, 5), (15, 5), box)
    assert not dl.segment_hits_box((-5, 15), (15, 15), box)
    assert dl.segment_hits_box((-5, 11), (15, 11), box, pad=1.5)


def test_crossing_segments_but_not_touching_ends():
    assert dl.segments_cross((0, 0), (10, 10), (0, 10), (10, 0))
    assert not dl.segments_cross((0, 0), (10, 0), (10, 0), (10, 10))
    assert not dl.segments_cross((0, 0), (10, 0), (0, 1), (10, 1))


# --- classifying -----------------------------------------------------------------


def test_a_linear_dimension_slides_across_its_line():
    # extension lines start at the part, the dimension line's halves at its ends
    lines =[(100, 180), (160, 180), (100, 195), (160, 195)]
    h = dl.classify_dimension(2, lines)
    assert (h["move"], h["axis"], h["span"], h["line_at"]) == ("linear", "y", (100, 160), 195)
    v = dl.classify_dimension(2, [(200, 100), (200, 150), (215, 100), (215, 150)])
    assert (v["axis"], v["span"], v["line_at"]) == ("x", (100, 150), 215)


@pytest.mark.parametrize("dim_type,lines", [(3, [(0, 0)] * 4), (6, []), (2, [(0, 0), (1, 1)]),
                                            (2, [(0, 0), (1, 1), (0, 0), (5, 5)])])
def test_angles_diameters_and_odd_lines_move_near(dim_type, lines):
    assert dl.classify_dimension(dim_type, lines) == {"move": "near"}


def test_a_symbol_without_a_leader_rides_on_the_nearest_dimension():
    items = [horizontal("D1", (100, 160), 195, (120, 196, 140, 199)),
             horizontal("D2", (100, 200), 215, (140, 216, 160, 219)),
             {"key": "G1", "kind": "gtol", "view": "V", "box": (120, 200, 145, 205), "anchor": None}]
    assert dl.find_hosts(items) == {"G1": "D1"}


def test_a_frame_whose_leader_meets_a_callouts_rides_on_it():
    items = [note("H1", (220, 150, 260, 156), (190, 140)),
             {"key": "G1", "kind": "gtol", "view": "V", "box": (220, 158, 250, 163), "anchor": (190.5, 140.2)}]
    assert dl.shared_leader_hosts(items) == {"G1": "H1"}


# --- arranging -------------------------------------------------------------------


def test_fix_leaves_a_free_text_where_it_is():
    items = [note("N1", (210, 150, 240, 155), (195, 150))]
    plan = dl.arrange(items, VIEW, AREA, TITLE, "fix")
    assert plan["moves"] == {} and plan["unplaced"] == []


def test_full_keeps_a_free_text_near_where_it_is():
    """A hand-laid text in a free spot stays close (at most a shorter leader):
    moving costs by the mm (DTN7, where texts went 116 mm)."""
    items = [note("N1", (210, 150, 240, 155), (195, 150)),
             horizontal("D1", (100, 200), 195, (140, 194, 160, 197))]
    plan = dl.arrange(items, VIEW, AREA, TITLE, "full")
    box = placed(items, plan)["N1"]
    assert math.hypot(*plan["moves"].get("N1", (0, 0))) < 10 and box[0] > VIEW["V"][2]


def test_overlapping_notes_are_pulled_apart_near_their_points():
    items = [note("N1", (210, 150, 240, 155), (195, 150)), note("N2", (212, 151, 242, 156), (195, 152))]
    plan = dl.arrange(items, VIEW, AREA, TITLE, "fix")
    boxes = placed(items, plan)
    assert not dl.overlap(boxes["N1"], boxes["N2"])
    for it in items:
        assert math.dist(dl.nearest_point(boxes[it["key"]], it["anchor"]), it["anchor"]) <= dl.LEADER_MAX_MM


def test_stacked_dimensions_step_out_from_their_view():
    # three dimensions crowded onto one line above the view
    items = [horizontal("D1", (100, 140), 184, (110, 183, 130, 186)),
             horizontal("D2", (100, 170), 184, (125, 183, 145, 186)),
             horizontal("D3", (100, 200), 184, (140, 183, 160, 186))]
    plan = dl.arrange(items, VIEW, AREA, TITLE, "full")
    boxes = placed(items, plan)
    assert plan["unplaced"] == []
    names = sorted(boxes)
    for i, a in enumerate(names):
        assert boxes[a][1] >= VIEW["V"][3] + dl.VIEW_GAP_MM
        for b in names[i + 1:]:
            assert not dl.overlap(boxes[a], boxes[b], dl.TEXT_GAP_MM)
    # the smallest span sits nearest the view
    assert boxes["D1"][1] <= boxes["D2"][1] <= boxes["D3"][1]


def test_full_keeps_a_dimension_on_its_side():
    """Balancing the sides never sends a hand-laid dimension across its view (DTN7)."""
    items = [horizontal(f"D{i}", (100, 200 - 10 * i), 190 + 9 * i, (140, 189 + 9 * i, 160, 192 + 9 * i))
             for i in range(4)]
    plan = dl.arrange(items, VIEW, AREA, TITLE, "full")
    for key, box in placed(items, plan).items():
        assert box[1] > VIEW["V"][3], key


def test_a_frame_on_a_dimension_moves_with_it():
    items = [horizontal("D1", (100, 200), 182, (140, 181, 160, 184)),
             {"key": "G1", "kind": "gtol", "view": "V", "box": (140, 185, 165, 190), "anchor": None, "host": "D1"}]
    plan = dl.arrange(items, VIEW, AREA, TITLE, "full")
    assert plan["moves"]["G1"] == plan["moves"]["D1"]
    g1 = next(o for o in plan["objects"] if o["key"] == "G1")
    assert g1["belongs_to"] == {"view": "V", "on": "D1", "leader_to": None}


def test_a_label_stays_under_its_view_and_off_the_title_block():
    views = {"V": (100.0, 100.0, 200.0, 180.0), "W": (100.0, 20.0, 200.0, 80.0)}
    items = [{"key": "L", "kind": "label", "view": "V", "box": (130, 60, 170, 64), "anchor": None}]
    plan = dl.arrange(items, views, AREA, TITLE, "full")
    box = placed(items, plan)["L"]
    assert box[3] <= views["V"][1] and dl.box_distance(box, views["V"]) < dl.box_distance(box, views["W"])


def test_texts_stay_on_the_area_and_off_the_title_block():
    items = [note("N1", (380, 40, 410, 46), (200, 120)), note("N2", (2, 120, 30, 125), (100, 120))]
    plan = dl.arrange(items, VIEW, AREA, TITLE, "full")
    for box in placed(items, plan).values():
        assert AREA[0] <= box[0] and box[2] <= AREA[2] and AREA[1] <= box[1] and box[3] <= AREA[3]
        assert not dl.overlap(box, TITLE)


def test_texts_on_the_sheet_itself_stay_put():
    items = [{"key": "sheet/N", "kind": "note", "view": None, "box": (20, 20, 80, 30), "anchor": None}]
    assert dl.arrange(items, VIEW, AREA, TITLE, "full")["moves"] == {}


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="full, fix"):
        dl.arrange([], VIEW, AREA, TITLE, "tidy")


# --- spreading the views -----------------------------------------------------------


def test_views_whose_texts_clash_move_apart_and_stay_aligned():
    views = {"A": (50.0, 100.0, 150.0, 180.0), "B": (160.0, 100.0, 240.0, 180.0)}
    envelope = {"A": (50.0, 100.0, 170.0, 180.0), "B": (160.0, 100.0, 240.0, 180.0)}
    moves = dl.spread_views(views, envelope, AREA, TITLE, balance=False)
    a, b = (dl._shift(envelope[n], *moves.get(n, (0, 0))) for n in "AB")
    assert b[0] - a[2] >= dl.ENVELOPE_GAP_MM - 1e-6
    assert all(abs(d[1]) < 1e-9 for d in moves.values())  # one row: they move only along x


def test_views_with_room_do_not_move_without_balance():
    views = {"A": (50.0, 100.0, 100.0, 150.0), "B": (200.0, 100.0, 250.0, 150.0)}
    assert dl.spread_views(views, dict(views), AREA, TITLE, balance=False) == {}


def test_envelopes_hold_a_views_texts():
    objects = [{"belongs_to": {"view": "V"}, "box": [90, 120, 99, 125]}]
    assert dl.envelopes(VIEW, objects) == {"V": (90, 100.0, 200.0, 180.0)}
