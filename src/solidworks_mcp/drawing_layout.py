"""Arrange the texts of a drawing sheet: every object gets a box, a label of
what it is and what it belongs to, and a new spot where it overlaps nothing,
stays on the drawing area and stays close to what it describes.

The objects are the texts SolidWorks draws round the views: dimensions, notes
and callouts, datum symbols, feature control frames, finish symbols and view
labels. The views themselves, the border and the title block are fixed.

Each object is classified by how it may move:
- a linear dimension slides only across its dimension line, onto stacked
  tiers beside its view (first tier FIRST_TIER_MM out, then one text height
  plus STACK_GAP_MM per tier, the smaller span nearest the view), on the side
  that keeps it short and the sides of the view balanced;
- an angle slides out along its own direction from its vertex, off the view
  (SolidWorks draws its arc through the text and runs the extension lines
  out to it), a few degrees either way while it stays inside the angle;
- a radius or diameter drawn with a leader is a leader item; any other
  dimension that is not linear moves a little round where it is;
- a leader item (note, hole or thread callout, datum, frame with a leader,
  finish symbol) goes anywhere off the views near the point its leader
  touches, keeping the leader short;
- a datum or frame attached to a dimension moves with that dimension;
- a view label stays under (or above) its view.

Objects are placed one by one: dimensions view by view, then leader items,
then the view labels beneath all that; each takes the cheapest free candidate spot. A spot is
free when its box stays inside the border, off the title block, off every
view (an angle may stay inside its own view), clear of the boxes, leaders
and extension lines placed before it, and nearer its own view than any other.
Pure Python, no SolidWorks.
"""

import math

FIRST_TIER_MM = 10.0  # from the view to the first dimension line (ASME Y14.5: 10 mm)
STACK_GAP_MM = 6.0  # between stacked dimension texts (drawing_spec.DIM_STACK_MIN_MM)
STACK_MARGIN_MM = 0.1  # kept past it: SolidWorks sets a text a hair off the plan (DTN4: 5.99)
TEXT_GAP_MM = 2.0  # between any two texts
VIEW_GAP_MM = 1.5  # between a text and a view's drawn box
LEADER_MAX_MM = 40.0  # drawing_spec.LEADER_MAX_MM
LABEL_GAP_MM = 2.0  # from a view's drawn box to its label
# A detail view's circle runs this far inside the box the layout report gives it
# (SolidWorks' outline is some 10 mm round the circle, the report takes 5 off)
DETAIL_MARGIN_MM = 5.0
# ... which gives its radius to about a mm (DTN7's detail E: 19.6 for 19.4 drawn), so
# a text is on it only when it reaches further in than this
DETAIL_SLACK_MM = 1.0
MAX_TIERS = 8
LINE_MM = 6.0  # one line of note text with its gap (drawing_spec.LINE_MM)
# Rings round a leader's point where its text may go, and the spots on each:
# a crowded corner leaves gaps of a few mm (DTN7's frame beside A-A's stack)
LEADER_RINGS_MM = (6.0, 8.0, 10.0, 12.5, 15.0, 17.5, 20.0, 23.0, 26.0, 29.5, 33.0, 36.5, 40.0, 45.0, 50.0, 56.0, 62.0)
LEADER_ANGLES = 48
DIM_LINE_STEP_MM = 8.0  # between stacked dimension lines whose texts step past each other
CROSSING_FAULT = 0.25
# A text inside the VIEW_GAP_MM round a view but off the view: tight, not
# wrong (DTN7's frame 0.5 mm over detail E's circle; moving it pushed three texts on)
NEAR_VIEW_FAULT = 0.5
NEAR_TEXT_FAULT = 0.5  # likewise a text closer than TEXT_GAP_MM to another
LEADER_IN_VIEW_MM = 4.0  # a leader may cross this much of its own view's box (0.5 fault beyond)
SLIDE_COST = 12.0  # a text slid past the end of its span costs two tiers further out
ARROW_STUB_MM = 5.0  # an arrow drawn outside a short span, past its end
SHOULDER_MM = 6.3  # from a vertical dimension's line to its level text past the span (measured, 2020)
# Each mm a text moves from where it is costs this much in "full" mode: a
# hand-laid sheet keeps its sides, and a text does not cross its view for balance
MOVE_COST_PER_MM = 0.15
SIDE_CHANGE_COST = 15.0  # a dimension moved to the other side of its view ("full" mode)
# A line counts as running through a text only this far inside its box: chained
# dimensions share an extension line that grazes the neighbour's text.
LINE_GRAZE_MM = -0.6
CUT_LINE_CLEAR_MM = 1.0  # DTN7's 18 degrees sat against A-A's cutting line

MODES = ("full", "fix")


# --- geometry -------------------------------------------------------------------------

def _w(b):
    return b[2] - b[0]


def _h(b):
    return b[3] - b[1]


def _centre(b):
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _at(b, cx, cy):
    """Box b moved so its centre is (cx, cy)."""
    hw, hh = _w(b) / 2, _h(b) / 2
    return cx - hw, cy - hh, cx + hw, cy + hh


def _shift(b, dx, dy):
    return b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def overlap(a, b, gap=0.0) -> bool:
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


def box_distance(a, b) -> float:
    dx = max(0.0, b[0] - a[2], a[0] - b[2])
    dy = max(0.0, b[1] - a[3], a[1] - b[3])
    return math.hypot(dx, dy)


def nearest_point(b, p) -> tuple:
    """The point of box b nearest to p."""
    return min(max(p[0], b[0]), b[2]), min(max(p[1], b[1]), b[3])


def leader_end(text_box, anchor) -> tuple:
    """Where SolidWorks starts a moved text's leader (session._leader_from_box):
    at the end of the text facing the point, on its top line when the point is
    above the text, its bottom line when below, else level with its middle."""
    ax, ay = anchor
    x = text_box[2] if ax > (text_box[0] + text_box[2]) / 2 else text_box[0]
    half = min(LINE_MM / 2, _h(text_box) / 2)
    y = (text_box[3] - half if ay > text_box[3] else
         text_box[1] + half if ay < text_box[1] else (text_box[1] + text_box[3]) / 2)
    return x, y


def segment_hits_box(p, q, b, pad=0.0) -> bool:
    """Whether segment p-q passes through box b grown by pad (Liang-Barsky)."""
    x0, y0, x1, y1 = b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad
    dx, dy = q[0] - p[0], q[1] - p[1]
    t0, t1 = 0.0, 1.0
    for edge_p, edge_q in ((-dx, p[0] - x0), (dx, x1 - p[0]), (-dy, p[1] - y0), (dy, y1 - p[1])):
        if abs(edge_p) < 1e-12:
            if edge_q < 0:
                return False
            continue
        t = edge_q / edge_p
        if edge_p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def _length_inside(p, q, b) -> float:
    """How much of segment p-q lies inside box b (sampled every 0.5 mm)."""
    length = math.dist(p, q)
    n = max(1, int(length / 0.5))
    inside = sum(1 for i in range(n) if b[0] < p[0] + (q[0] - p[0]) * (i + 0.5) / n < b[2]
                 and b[1] < p[1] + (q[1] - p[1]) * (i + 0.5) / n < b[3])
    return length * inside / n


def on_detail_circle(box, vbox, gap=0.0) -> bool:
    """Whether a text box comes within gap of the circle a detail view draws
    in its box vbox (radius DETAIL_MARGIN_MM inside the box's half width)."""
    if not overlap(box, vbox, max(gap, 0.0)):
        return False
    cx, cy = _centre(vbox)
    return math.dist(nearest_point(box, (cx, cy)), (cx, cy)) < min(_w(vbox), _h(vbox)) / 2 - DETAIL_MARGIN_MM + gap


def segments_cross(a, b, c, d) -> bool:
    """Whether segments a-b and c-d cross (touching ends do not count)."""
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2, d3, d4 = orient(c, d, a), orient(c, d, b), orient(a, b, c), orient(a, b, d)
    return ((d1 > 1e-9 and d2 < -1e-9) or (d1 < -1e-9 and d2 > 1e-9)) and \
           ((d3 > 1e-9 and d4 < -1e-9) or (d3 < -1e-9 and d4 > 1e-9))


# --- classifying ----------------------------------------------------------------------

def classify_dimension(dim_type: int | None, lines, arcs=()) -> dict:
    """How a dimension moves, from SolidWorks' dimension type (swDimensionType_e:
    2 linear, 3 angular, 5 radial, 6 diameter), its display lines [(x1, y1, x2,
    y2)] (or only their start points [(x, y)]) and its display arcs [(start x,
    y, end x, y, centre x, y)], in paper mm. Measured on 2020: a linear
    dimension's last two lines are the halves of its dimension line, starting
    at its ends, and the two before them its extension lines, starting at the
    part; a basic dimension's box adds four lines in front. An angle's arcs
    (split round its text) are centred on its vertex. A radius or diameter
    drawn with a leader ends in a level shoulder to its text, the leader
    before it starting on the arc. Returns {"move": "linear", "axis": "x"|"y"
    (the direction it slides), "span": (lo, hi) along the dimension line,
    "witness": [(x, y), (x, y)]}, {"move": "angle", "vertex": (x, y),
    "sides": (a0, a1) its sides' directions in radians, counter-clockwise
    from a0 to a1, "lines_from": the radius its extension lines start at, or
    None}, {"move": "leader", "anchor": (x, y)} or {"move": "near"}."""
    if dim_type == 3 and arcs:
        return _classify_angle(lines, arcs)
    if dim_type in (5, 6) and lines and len(lines) >= 2 and len(lines[-1]) >= 4:
        shoulder, leader = lines[-1], lines[-2]
        if abs(shoulder[1] - shoulder[3]) < 0.05 and abs(leader[1] - leader[3]) > 0.05:
            return {"move": "leader", "anchor": (leader[0], leader[1])}
    lines = [tuple(l[:2]) for l in lines or ()]
    if dim_type == 2 and lines and len(lines) >= 4:
        (ax, ay), (bx, by) = lines[-2], lines[-1]
        w1, w2 = lines[-4], lines[-3]
        if abs(ay - by) < 0.05 and abs(ax - bx) > 0.05:  # a horizontal dimension line
            return {"move": "linear", "axis": "y", "span": (min(ax, bx), max(ax, bx)), "line_at": ay,
                    "witness": [tuple(w1), tuple(w2)]}
        if abs(ax - bx) < 0.05 and abs(ay - by) > 0.05:
            return {"move": "linear", "axis": "x", "span": (min(ay, by), max(ay, by)), "line_at": ax,
                    "witness": [tuple(w1), tuple(w2)]}
    return {"move": "near"}


def _classify_angle(lines, arcs) -> dict:
    """An angle's vertex, its sides (the ends of its arcs furthest apart round
    the vertex) and where its extension lines start."""
    vx, vy = arcs[0][4], arcs[0][5]
    ends = [(a[0], a[1]) for a in arcs] + [(a[2], a[3]) for a in arcs]
    angles = sorted(math.atan2(y - vy, x - vx) % (2 * math.pi) for x, y in ends)
    # the sides bound the arc: the widest empty gap between ends lies outside the angle
    gaps = [((angles[(i + 1) % len(angles)] - a) % (2 * math.pi), i) for i, a in enumerate(angles)]
    _, i = max(gaps)
    a0, a1 = angles[(i + 1) % len(angles)], angles[i]
    # extension lines run out along a side, from near the part
    starts = []
    for l in lines or ():
        if len(l) < 4:
            continue
        r0, r1 = math.dist(l[:2], (vx, vy)), math.dist(l[2:4], (vx, vy))
        t = math.atan2(l[3] - l[1], l[2] - l[0]) % (2 * math.pi)
        for side in (a0, a1):
            if min(abs(t - side), 2 * math.pi - abs(t - side)) < math.radians(2) and r1 > r0:
                starts.append(r0)
    return {"move": "angle", "vertex": (vx, vy), "sides": (a0, a1), "lines_from": min(starts) if starts else None}


def shared_leader_hosts(items, reach_mm: float = 1.5) -> dict:
    """{key of a datum or frame: key of the note (a hole or thread callout)
    whose leader touches the same point}: a frame "on" a callout has a leader
    of its own to the callout's edge, and the two read as one. Pure."""
    hosts = {}
    notes = [o for o in items if o["kind"] == "note" and o.get("anchor") is not None]
    for it in items:
        if it["kind"] not in ("datum", "gtol") or it.get("anchor") is None or it.get("host"):
            continue
        near = [(math.dist(it["anchor"], o["anchor"]), o["key"]) for o in notes if o["view"] == it["view"]]
        near = [n for n in near if n[0] <= reach_mm]
        if near:
            hosts[it["key"]] = min(near)[1]
    return hosts


def find_hosts(items, reach_mm: float = 25.0) -> dict:
    """{key of a datum or frame without a leader: key of the dimension or
    callout it sits on}: the nearest dimension or note in its view within
    reach (SolidWorks attaches it there and draws no leader). Pure."""
    hosts = {}
    for it in items:
        if it["kind"] not in ("datum", "gtol") or it.get("anchor") is not None:
            continue
        near = [(box_distance(it["box"], o["box"]), o["key"]) for o in items
                if o["view"] == it["view"] and o["kind"] in ("dimension", "note") and o["key"] != it["key"]]
        near = [n for n in near if n[0] <= reach_mm]
        if near:
            hosts[it["key"]] = min(near)[1]
    return hosts


# --- the arranger ---------------------------------------------------------------------

class _Sheet:
    """What is placed so far: text boxes, lines (leaders, extension and
    dimension lines) and how many dimensions each side of a view carries."""

    def __init__(self, area, title_block, views, obstacles=None, round_views=()):
        self.area, self.title_block, self.views = area, title_block, views
        self.round_views = set(round_views)
        obstacles = obstacles or {}
        self.boxes = [(tuple(b), "fixed", "mark", None) for b in obstacles.get("boxes", ())]  # (box, key, kind, view)
        self.lines = [(tuple(p), tuple(q), "fixed") for p, q in obstacles.get("lines", ())]  # (p, q, key)
        self.load = {}  # (view, side): count
        self.dim_texts = []  # (box, key, view): each placed dimension's own text
        self.reserved = []  # (box, key, kind, view): spots kept for texts not placed yet

    def faults(self, unit, box, lines, allow_own_view: bool, why: list | None = None,
               reserved: bool = True) -> float:
        """How badly a spot breaks the rules: 0 is free; off the area or on the
        title block counts 100, on a view 20 (inside the VIEW_GAP_MM round it
        but off the view itself NEAR_VIEW_FAULT), nearer another view 10, each
        text or line it runs into 1 (a text within TEXT_GAP_MM but not touching
        NEAR_TEXT_FAULT), a leader across its own view 0.5, each line
        crossing a line (an extension line through a stacked dimension's line,
        accepted where there is no room) CROSSING_FAULT. `why` collects the
        reasons."""
        bad = 0.0

        def fault(points, reason):
            nonlocal bad
            bad += points
            if why is not None:
                why.append(reason)

        a = self.area
        if box[0] < a[0] or box[1] < a[1] or box[2] > a[2] or box[3] > a[3]:
            fault(100, "off the border")
        if self.title_block is not None and overlap(box, self.title_block):
            fault(100, "on the title block")
        own = self.views.get(unit["view"])
        for name, vbox in self.views.items():
            if name == unit["view"] and allow_own_view:
                continue
            if self._on_view(name, vbox, box, -DETAIL_SLACK_MM if name in self.round_views else 0.0):
                fault(20, f"on view {name}")
            elif self._on_view(name, vbox, box, VIEW_GAP_MM):
                fault(NEAR_VIEW_FAULT, f"close to view {name}")
        if own is not None:  # a view's label too: it must read as that view's
            # the group, and its own text alone: a frame under a dimension's text
            # is nearer the view than the text (DTN4's Ø4.120)
            for b in {box, self._text_at(unit, box)}:
                mine = box_distance(b, own)
                n = next((n for n, v in self.views.items() if n != unit["view"] and box_distance(b, v) + 0.5 < mine), None)
                if n is not None:
                    fault(10, f"nearer view {n}")
                    break
        if unit.get("move") == "leader" and any(math.dist(p, q) > LEADER_MAX_MM for p, q in lines):
            fault(1, "leader too long")
        if unit.get("move") == "leader" and own is not None:
            for p, q in lines:
                if _length_inside(p, q, own) > LEADER_IN_VIEW_MM:
                    fault(0.5, "leader across its view")
        hit = set()
        for other, key, kind, view in self.boxes + (self.reserved if reserved else []):
            if overlap(box, other, TEXT_GAP_MM):
                hit.add(key)
                if overlap(box, other):
                    fault(1, f"on {key}")
                else:  # within TEXT_GAP_MM of another text without touching it: tight, not wrong
                    fault(NEAR_TEXT_FAULT, f"close to {key}")
            if key == "fixed":
                continue  # a cutting line's letter sits on the part, where extension lines start
            for p, q in lines:
                if segment_hits_box(p, q, other, LINE_GRAZE_MM):
                    fault(1, f"its line through {key}")
        if unit["kind"] == "dimension" and unit.get("move") == "linear":
            # stacked dimension texts keep STACK_GAP_MM, measured between the
            # texts themselves: a frame riding on one does not count
            text = self._text_at(unit, box)
            for other, key, view in self.dim_texts:
                if view == unit["view"] and key not in hit and overlap(text, other, STACK_GAP_MM + STACK_MARGIN_MM):
                    fault(1, f"stacked close to {key}")
        for p, q, key in self.lines:
            if segment_hits_box(p, q, box, LINE_GRAZE_MM):
                fault(1, f"line of {key} through it")
            elif key == "fixed" and segment_hits_box(p, q, box, CUT_LINE_CLEAR_MM):
                fault(NEAR_TEXT_FAULT, "close to a cutting line")  # tight, like a text close to a text
            if key == "fixed":
                continue  # extension lines and leaders cross cutting lines as a matter of course
            for c, d in lines:
                if segments_cross(p, q, c, d):
                    fault(CROSSING_FAULT, f"crosses a line of {key}")
        return bad

    def _on_view(self, name, vbox, box, gap) -> bool:
        """Whether a text box comes within gap of a view: a detail view draws a
        circle, so the corners of its box are free."""
        if name in self.round_views:
            return on_detail_circle(box, vbox, gap)
        return overlap(box, vbox, gap)

    @staticmethod
    def _text_at(unit, box):
        """A unit's own text (without its riders) when its box is at box."""
        if "text_box" not in unit:
            return tuple(box)
        return _shift(unit["text_box"], box[0] - unit["box"][0], box[1] - unit["box"][1])

    def add(self, unit, boxes, lines, side=None):
        kind = unit["kind"] + ("+" if unit.get("riders") else "")  # a group is not a bare dimension
        for b in boxes:
            self.boxes.append((b, unit["key"], kind, unit["view"]))
            if unit["kind"] == "dimension":
                self.dim_texts.append((self._text_at(unit, b), unit["key"], unit["view"]))
        for p, q in lines:
            self.lines.append((p, q, unit["key"]))
        if side is not None:
            self.load[(unit["view"], side)] = self.load.get((unit["view"], side), 0) + 1


def _linear_candidates(unit, vbox):
    """(cost, text centre, lines, side) for a linear dimension on each tier of
    both sides of its view. Its dimension line runs DIM_LINE_STEP_MM from the
    next one out; the text sits on the line, in the middle of the span or slid
    past either end (with the line run out to it), so the texts of stacked
    dimensions step past each other."""
    text, axis, (lo, hi) = unit["text_box"], unit["axis"], unit["span"]
    (w1x, w1y), (w2x, w2y) = unit["witness"]
    if axis == "y":  # a horizontal dimension: above or below the view
        across, along, sides = _h(text) / 2, _w(text) / 2, (("above", vbox[3], 1), ("below", vbox[1], -1))
    else:  # a vertical one, its text level: the text is wide across the line
        across, along, sides = _w(text) / 2, _h(text) / 2, (("right", vbox[2], 1), ("left", vbox[0], -1))
    first = max(FIRST_TIER_MM, across + VIEW_GAP_MM + 1.0)
    step = max(DIM_LINE_STEP_MM, 2 * across + STACK_GAP_MM) if axis == "y" else DIM_LINE_STEP_MM
    # a span too short for the text between its arrows: the text goes past an end
    squeezed = (hi - lo) < 2 * along + 4.0
    slides = [((lo + hi) / 2, SLIDE_COST if squeezed else 0.0), (lo - along - 2.0, 0.0 if squeezed else SLIDE_COST),
              (hi + along + 2.0, 0.0 if squeezed else SLIDE_COST)]
    out = []
    for side, edge, sign in sides:
        for tier in range(MAX_TIERS):
            at = edge + sign * (first + tier * step)
            for n, (t, extra) in enumerate(slides):
                # an extension line counts from where it leaves the view: on the
                # part it runs along the view's own lines. Arrows outside a
                # squeezed span stick out past its ends.
                stub = ARROW_STUB_MM if squeezed else 0.0
                if axis == "y":
                    centre = (t, at)
                    leave = (lambda y: max(y, edge)) if sign > 0 else (lambda y: min(y, edge))
                    ext = [((w1x, leave(w1y)), (w1x, at)), ((w2x, leave(w2y)), (w2x, at))]
                    lines = ext + [((min(lo - stub, t), at), (max(hi + stub, t), at))]
                    reach = abs(at - w1y) + abs(at - w2y)
                else:
                    centre = (at, t)
                    leave = (lambda x: max(x, edge)) if sign > 0 else (lambda x: min(x, edge))
                    ext = [((leave(w1x), w1y), (at, w1y)), ((leave(w2x), w2y), (at, w2y))]
                    lines = ext + [((at, min(lo - stub, t)), (at, max(hi + stub, t)))]
                    reach = abs(at - w1x) + abs(at - w2x)
                    if n:  # past the span, SolidWorks sets level text beside the line, on a shoulder
                        tip = at + sign * SHOULDER_MM
                        centre = (tip + sign * across, t)
                        lines.append(((at, t), (tip, t)))
                cost = tier * 6.0 + 0.3 * reach + extra
                out.append((cost, centre, lines, side))
    return out


def _angle_candidates(unit, step_mm: float = 1.5, reach_mm: float = 90.0):
    """(cost, text centre, lines, None) for an angle: its text slid out from
    the vertex along its direction now, or turned up to ANGLE_TURN_DEG either
    way while it stays inside the angle; lines are its sides from where they
    start (or the arc now) out to the arc, and the arc in a few chords."""
    (vx, vy), (a0, a1) = unit["vertex"], unit["sides"]
    cx, cy = _centre(unit["text_box"])
    r_now = math.hypot(cx - vx, cy - vy)
    t_now = math.atan2(cy - vy, cx - vx)
    span = (a1 - a0) % (2 * math.pi)
    start = unit.get("lines_from") or r_now
    out = []
    for turn in (0, -5, 5, -10, 10):
        t = t_now + math.radians(turn)
        # the text must stay well between the sides (an angle with its arrows
        # outside draws its arc past them: DTN7's 18 degrees spans 39)
        if turn and not (0.2 * span < (t - a0) % (2 * math.pi) < 0.8 * span):
            continue
        for k in range(int(reach_mm / step_mm) + 1):
            r = r_now + k * step_mm
            arc = [(vx + r * math.cos(a0 + span * j / 8), vy + r * math.sin(a0 + span * j / 8)) for j in range(9)]
            lines = [((vx + min(start, r) * math.cos(a), vy + min(start, r) * math.sin(a)),
                      (vx + (r + 1.5) * math.cos(a), vy + (r + 1.5) * math.sin(a))) for a in (a0, a0 + span)]
            lines += list(zip(arc, arc[1:]))
            out.append((0.3 * (r - r_now) + abs(turn) / 5.0, (vx + r * math.cos(t), vy + r * math.sin(t)), lines, None))
    return out


def _near_candidates(unit, radius_mm: float = 60.0, step_mm: float = 3.0):
    """Spots round where a dimension that is neither linear, an angle nor on a
    leader is now, nearest first: far enough to leave the middle of a view."""
    cx, cy = _centre(unit["box"])
    out = []
    n = int(radius_mm // step_mm)
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            d = math.hypot(i * step_mm, j * step_mm)
            if d <= radius_mm:
                out.append((d, (cx + i * step_mm, cy + j * step_mm), [], None))
    return out


def _leader_candidates(unit, vbox):
    """Spots for a leader item round the point its leader touches: on rings
    round it, the box reaching away from the point, and beside the view."""
    box, (ax, ay) = unit["box"], unit["anchor"]
    w, h = _w(box), _h(box)
    out = []
    for r in LEADER_RINGS_MM:
        for k in range(LEADER_ANGLES):
            t = 2 * math.pi * k / LEADER_ANGLES
            px, py = ax + r * math.cos(t), ay + r * math.sin(t)
            # the box's corner or edge middle nearest the anchor sits on the ring
            cx = px + (w / 2 if math.cos(t) > 0.38 else -w / 2 if math.cos(t) < -0.38 else 0.0)
            cy = py + (h / 2 if math.sin(t) > 0.38 else -h / 2 if math.sin(t) < -0.38 else 0.0)
            out.append((None, (cx, cy), None, None))
    if vbox is not None:  # straight out beside the view, level with the point
        for tier in range(4):
            off = 6.0 + tier * (h + 3.0)
            out += [(None, (vbox[2] + off + w / 2, ay), None, None), (None, (vbox[0] - off - w / 2, ay), None, None),
                    (None, (ax + w / 2, vbox[3] + off + h / 2), None, None),
                    (None, (ax - w / 2, vbox[1] - off - h / 2), None, None)]
    return out


def _label_candidates(unit, vbox, below_mm: float):
    """Spots for a view's label, placed after everything else of its view:
    centred under the view and the texts placed under it (whose lowest edge
    is below_mm), nearest first, then above the view, then beside it."""
    box = unit["box"]
    w, h = _w(box), _h(box)
    cx = (vbox[0] + vbox[2]) / 2
    out = []
    for k in range(60):
        off = LABEL_GAP_MM + k * 1.0
        out += [(off, (cx, below_mm - off - h / 2), [], None), (off + 15.0, (cx, vbox[3] + off + h / 2), [], None)]
    out += [(80.0, (vbox[2] + LABEL_GAP_MM + w / 2, (vbox[1] + vbox[3]) / 2), [], None),
            (80.0, (vbox[0] - LABEL_GAP_MM - w / 2, (vbox[1] + vbox[3]) / 2), [], None)]
    return out


def arrange(items, views, area, title_block=None, mode: str = "full", obstacles=None, round_views=()) -> dict:
    """New places for a sheet's texts. Pure.

    items: [{"key", "kind" (dimension/note/datum/gtol/finish/label), "view"
    (its view's name, None for the sheet), "box": (x0, y0, x1, y1) paper mm,
    "anchor": (x, y) where its leader touches the part, or None,
    "dimension": classify_dimension(...) for a dimension}]. views: {name: the
    box the view draws in}; area: the free drawing area (x0, y0, x1, y1);
    title_block: its box or None. obstacles: what else is drawn and stays,
    {"boxes": [box], "lines": [((x, y), (x, y))]} (cutting-line arrows and
    letters, detail-circle letters); round_views: the detail views, which draw
    a circle in their box. mode "full" places every text afresh; "fix"
    keeps each text where it is when that spot is free, and moves the rest.

    An item may name its "host" (the key of the dimension SolidWorks attached
    it to, or None when it is attached to the part); a datum or frame without
    a leader and without a "host" entry rides on the nearest dimension or
    note, and one whose leader touches where a callout's does rides on that
    callout. A rider moves with its host as one box.

    Returns {"moves": {key: (dx, dy)}, "objects": [{"key", "what", "belongs_to",
    "box" (new), "moved_mm"}], "unplaced": [keys that found no free spot and
    took the least bad one], "lines_crossed": [keys free but for a line
    crossing another dimension's line]}.
    """
    if mode not in MODES:
        raise ValueError(f"mode is one of {', '.join(MODES)} (got {mode!r}).")
    by_key = {it["key"]: it for it in items}
    # SolidWorks names the dimension a symbol is attached to; else the nearest one
    hosts = {k: h for k, h in find_hosts([it for it in items if "host" not in it]).items()}
    hosts.update({it["key"]: it["host"] for it in items if it.get("host") in by_key})
    hosts.update(shared_leader_hosts(items))
    riders = {}
    for rider, host in hosts.items():
        riders.setdefault(host, []).append(rider)
    sheet = _Sheet(area, title_block, views, obstacles, round_views)
    units = []
    for it in items:
        if it.get("view") not in views:  # texts on the sheet itself (general notes) stay put
            sheet.boxes.append((tuple(it["box"]), "fixed", "note", None))
            continue
        if it["key"] in hosts:
            continue  # it rides on its host
        unit = {"key": it["key"], "kind": it["kind"], "view": it["view"],
                "box": _union([it["box"]] + [by_key[r]["box"] for r in riders.get(it["key"], ())]),
                "text_box": it["box"], "anchor": it.get("anchor"), "riders": riders.get(it["key"], [])}
        if it["kind"] == "dimension":
            unit.update(it.get("dimension") or {"move": "near"})
            if unit["move"] == "linear":
                # the riders keep their offset from the dimension's text
                unit["ride"] = [(r, _centre(by_key[r]["box"])[0] - _centre(it["box"])[0],
                                 _centre(by_key[r]["box"])[1] - _centre(it["box"])[1]) for r in riders.get(it["key"], ())]
        elif it["kind"] == "label":
            unit["move"] = "label"
        elif unit["anchor"] is not None:
            unit["move"] = "leader"
        else:
            unit["move"] = "near"
        units.append(unit)

    def order(u):
        rank = {"linear": 0, "angle": 1, "near": 1, "leader": 2, "label": 3}[u["move"]]
        span = (u["span"][1] - u["span"][0]) if u["move"] == "linear" else 0.0
        return rank, u["view"] or "", span, u["key"]

    # a text's spot is kept for it until it is placed: a view label's always
    # (it goes last: a leader item would otherwise take the spot under its
    # view), in "fix" every text's (an angle moved off its view took the spot
    # of the hole callout beside it, which went 99 mm; DTN7)
    for unit in units:
        if unit["move"] == "label" or mode == "fix":
            sheet.reserved.append((tuple(unit["box"]), unit["key"], unit["kind"], unit["view"]))
    moves, unplaced, crossed, sides = {}, [], [], {}
    for unit in sorted(units, key=order):
        sheet.reserved = [b for b in sheet.reserved if b[1] != unit["key"]]  # its own spot is free to it
        vbox = views.get(unit["view"])
        box = unit["box"]
        c0 = _centre(box)
        allow_own = False  # no text stays on a view, its own included
        if unit["move"] == "label":
            half = _w(box) / 2 + TEXT_GAP_MM
            mid = (vbox[0] + vbox[2]) / 2
            under = [b[1] for b, k, kind, view in sheet.boxes if view == unit["view"]
                     and b[3] <= vbox[1] and b[0] < mid + half and b[2] > mid - half]
            cands = _label_candidates(unit, vbox, min(under + [vbox[1]]))
        elif unit["move"] == "linear":
            # the text box is the union with its riders: centre the TEXT on the dimension line
            tc = _centre(unit["text_box"])
            off = (c0[0] - tc[0], c0[1] - tc[1])
            cands = [(c, (p[0] + off[0], p[1] + off[1]), l, s) for c, p, l, s in _linear_candidates(unit, vbox)]
            now = _side_of(unit, vbox)
            cands = [(c + 4.0 * sheet.load.get((unit["view"], s), 0) + (SIDE_CHANGE_COST if s != now else 0.0), p, l, s)
                     for c, p, l, s in cands]
        elif unit["move"] == "leader":
            cands = []
            for _, centre, _, _ in _leader_candidates(unit, vbox):
                # moved, its leader is redrawn from its own text's end (riders aside)
                end = leader_end(_shift(unit["text_box"], centre[0] - c0[0], centre[1] - c0[1]), unit["anchor"])
                length = math.dist(end, unit["anchor"])
                cost = length + (40.0 if length > LEADER_MAX_MM else 0.0)
                cands.append((cost, centre, [(unit["anchor"], end)], None))
        elif unit["move"] == "angle":
            tc = _centre(unit["text_box"])  # the text slides; its riders keep their offset
            off = (c0[0] - tc[0], c0[1] - tc[1])
            cands = [(c, (p[0] + off[0], p[1] + off[1]), l, s) for c, p, l, s in _angle_candidates(unit)]
        else:
            cands = _near_candidates(unit)
        if mode == "fix":
            lines = [(unit["anchor"], nearest_point(box, unit["anchor"]))] if unit["move"] == "leader" else []
            if unit["move"] == "linear":
                lines = _current_dimension_lines(unit)
            cands.append((-1e6, c0, lines, unit.get("side_now")))
        else:
            # the spot it is in is a candidate too, priced like the others
            if unit["move"] == "linear":
                (w1x, w1y), (w2x, w2y) = unit["witness"]
                at = unit["line_at"]
                reach = abs(at - w1y) + abs(at - w2y) if unit["axis"] == "y" else abs(at - w1x) + abs(at - w2x)
                side = _side_of(unit, vbox)
                cands.append((0.3 * reach + 4.0 * sheet.load.get((unit["view"], side), 0), c0,
                              _current_dimension_lines(unit), side))
            elif unit["move"] in ("leader", "label"):
                here = [(unit["anchor"], nearest_point(box, unit["anchor"]))] if unit["move"] == "leader" else []
                cost = math.dist(*here[0]) if here else 0.0
                cands.append((cost + (40.0 if cost > LEADER_MAX_MM else 0.0), c0, here, None))
            # moving costs by the mm; staying put breaks ties
            cands = [(c + MOVE_COST_PER_MM * math.dist(p, c0) - (0.5 if math.dist(p, c0) < 0.05 else 0.0), p, l, s)
                     for c, p, l, s in cands]
        cands.sort(key=lambda c: (c[0], math.dist(c[1], c0)))
        best = None  # (faults, rank, box, lines, side): the first free spot, else the least bad
        if mode == "fix":  # it stays unless it runs into something (a line crossing is no reason)
            # judged without the spots kept for texts still to come: they keep
            # others out, they do not push a text that is fine off its own spot
            cost, centre, lines, side = cands[0]
            bad = sheet.faults(unit, _at(box, *centre), lines, allow_own, reserved=False)
            if bad < 1:
                best, cands = (bad, 0, _at(box, *centre), lines, side), []
        for rank, (cost, centre, lines, side) in enumerate(cands):
            b = _at(box, *centre)
            bad = sheet.faults(unit, b, lines, allow_own)
            if best is None or bad < best[0]:
                best = (bad, rank, b, lines, side)
            if bad == 0:
                break
        if best[0] >= 1:
            unplaced.append(unit["key"])
        elif best[0]:
            crossed.append(unit["key"])
        _, _, b, lines, side = best
        sheet.add(unit, [b], lines, side)
        dx, dy = b[0] - box[0], b[1] - box[1]
        moves[unit["key"]] = (dx, dy)
        for r in riders.get(unit["key"], ()):
            moves[r] = (dx, dy)
        sides[unit["key"]] = side
    objects = []
    for it in items:
        dx, dy = moves.get(it["key"], (0.0, 0.0))
        objects.append({"key": it["key"], "what": it["kind"],
                        "belongs_to": {"view": it.get("view"), "on": hosts.get(it["key"]),
                                       "leader_to": [round(c, 1) for c in it["anchor"]] if it.get("anchor") else None},
                        "box": [round(c, 1) for c in _shift(it["box"], dx, dy)],
                        "moved_mm": round(math.hypot(dx, dy), 1)})
    moves = {k: v for k, v in moves.items() if math.hypot(*v) > 0.05}
    return {"moves": moves, "objects": objects, "unplaced": unplaced, "lines_crossed": crossed}


def _side_of(unit, vbox) -> str:
    """The side of its view a linear dimension's line is on now."""
    if unit["axis"] == "y":
        return "above" if unit["line_at"] > (vbox[1] + vbox[3]) / 2 else "below"
    return "right" if unit["line_at"] > (vbox[0] + vbox[2]) / 2 else "left"


def _current_dimension_lines(unit) -> list:
    """A linear dimension's extension and dimension lines as they are now."""
    (w1x, w1y), (w2x, w2y) = unit["witness"]
    lo, hi = unit["span"]
    at = unit["line_at"]
    if unit["axis"] == "y":
        return [((w1x, w1y), (w1x, at)), ((w2x, w2y), (w2x, at)), ((lo, at), (hi, at))]
    return [((w1x, w1y), (at, w1y)), ((w2x, w2y), (at, w2y)), ((at, lo), (at, hi))]


# --- spreading the views ----------------------------------------------------------------

ENVELOPE_GAP_MM = 6.0  # between the envelopes of two views (a view with its texts)
# Views spread to this far short of the area's edges: a text the spread puts on
# the edge exactly counts as off it and is sent away (DTN7's hole callout, 18 mm)
EDGE_SLACK_MM = 0.5


def envelopes(views, objects) -> dict:
    """{view: the box round the view and the texts that belong to it}, from
    arrange()'s objects. Pure."""
    out = {}
    for name, box in views.items():
        mine = [o["box"] for o in objects if o["belongs_to"]["view"] == name]
        out[name] = _union([box] + [tuple(b) for b in mine])
    return out


def _aligned(views, axis: int, tol: float = 1.0) -> dict:
    """{view: its group}: views whose centres share x (axis 0, a column) or y
    (axis 1, a row) within tol; projected views stay aligned so they move together."""
    centres = {n: _centre(b)[axis] for n, b in views.items()}
    groups, out = [], {}
    for name in sorted(views, key=lambda n: centres[n]):
        if groups and abs(centres[groups[-1][0]] - centres[name]) <= tol:
            groups[-1].append(name)
        else:
            groups.append([name])
    for g in groups:
        for name in g:
            out[name] = tuple(g)
    return out


def spread_views(views, envelope, area, title_block=None, iterations: int = 30, balance: bool = True) -> dict:
    """{view: (dx, dy)} that pull apart views whose envelopes come closer than
    ENVELOPE_GAP_MM, keeping every envelope on the area and off the title
    block. Views in one row move only across (x) together with their column,
    so projected views stay aligned: a pair side by side separates along x by
    moving their columns apart, a pair one above the other along y by moving
    their rows. With balance, the free space along each row and column of
    views is then shared equally between the gaps (when nothing then clashes),
    so a crowded view gets room from an empty part of the sheet. Pure."""
    area = (area[0] + EDGE_SLACK_MM, area[1] + EDGE_SLACK_MM, area[2] - EDGE_SLACK_MM, area[3] - EDGE_SLACK_MM)
    cols, rows = _aligned(views, 0), _aligned(views, 1)
    shift = {n: [0.0, 0.0] for n in views}

    def env(n):
        return _shift(envelope[n], *shift[n])

    def room(group, axis, direction):
        """How far a group's envelopes can move along axis in direction before
        leaving the area (or, going down, reaching the title block)."""
        limit = math.inf
        for n in group:
            e = env(n)
            if direction > 0:
                limit = min(limit, area[2 + axis] - e[2 + axis])
            else:
                lo = area[axis]
                if axis == 1 and title_block is not None and e[2] > title_block[0] and e[0] < title_block[2]:
                    lo = max(lo, title_block[3])
                limit = min(limit, e[axis] - lo)
        return max(0.0, limit)

    for _ in range(iterations):
        worst = None
        names = sorted(views)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                ea, eb = env(a), env(b)
                if not overlap(ea, eb, ENVELOPE_GAP_MM):
                    continue
                need_x = min(ea[2], eb[2]) - max(ea[0], eb[0]) + ENVELOPE_GAP_MM
                need_y = min(ea[3], eb[3]) - max(ea[1], eb[1]) + ENVELOPE_GAP_MM
                ca, cb = _centre(_shift(views[a], *shift[a])), _centre(_shift(views[b], *shift[b]))
                if rows[a] == rows[b]:
                    axis = 0
                elif cols[a] == cols[b]:
                    axis = 1
                else:
                    axis = 0 if need_x <= need_y else 1
                need = need_x if axis == 0 else need_y
                if worst is None or need > worst[0]:
                    worst = (need, a, b, axis, ca, cb)
        if worst is None:
            break
        need, a, b, axis, ca, cb = worst
        low, high = (a, b) if ca[axis] <= cb[axis] else (b, a)
        groups = cols if axis == 0 else rows
        # everything on the low side of the gap moves down, the rest up
        cut = (_centre(views[low])[axis] + _centre(views[high])[axis]) / 2 + shift[low][axis]
        lows = {n for n in views if _centre(views[n])[axis] + shift[n][axis] <= cut}
        lows = {m for n in lows for m in groups[n]}
        highs = {m for n in views if n not in lows for m in groups[n]} - lows
        free_low, free_high = room(lows, axis, -1), room(highs, axis, 1)
        if free_low + free_high < 0.1:
            break  # no room left on the sheet
        go_low = min(free_low, max(need / 2, need - free_high))
        go_high = min(free_high, need - go_low)
        for n in lows:
            shift[n][axis] -= go_low
        for n in highs:
            shift[n][axis] += go_high
    if balance:
        for axis, groups in ((0, cols), (1, rows)):
            _balance(views, groups, axis, shift, env, area, title_block)
    return {n: tuple(v) for n, v in shift.items() if math.hypot(*v) > 0.05}


def _balance(views, groups, axis, shift, env, area, title_block) -> None:
    """Share the free length of the area equally between the gaps round the
    columns (axis 0) or rows (axis 1) of views, each as wide as its views'
    envelopes, when that leaves every pair of envelopes apart and on the area."""
    bands = sorted(set(groups.values()), key=lambda g: min(env(n)[axis] for n in g))
    spans = [(min(env(n)[axis] for n in g), max(env(n)[axis + 2] for n in g)) for g in bands]
    if any(b[0] < a[1] for a, b in zip(spans, spans[1:])):
        return  # the bands overlap along this axis: views are not on a grid
    lo, hi = area[axis], area[axis + 2]
    if axis == 1 and title_block is not None:
        # a band over the title block must stay above it
        lo = max([lo] + [title_block[3] for g in bands[:1] for n in g
                         if env(n)[2] > title_block[0] and env(n)[0] < title_block[2]])
    free = (hi - lo) - sum(b - a for a, b in spans)
    if free <= 0:
        return
    gap = free / (len(bands) + 1)
    before = {n: list(v) for n, v in shift.items()}
    at = lo + gap
    for g, (a, b) in zip(bands, spans):
        for n in g:
            shift[n][axis] += at - a
        at += (b - a) + gap
    names = sorted(views)
    clash = any(overlap(env(a), env(b), ENVELOPE_GAP_MM) for i, a in enumerate(names) for b in names[i + 1:])
    off = any(env(n)[0] < area[0] - 0.1 or env(n)[1] < area[1] - 0.1 or env(n)[2] > area[2] + 0.1
              or env(n)[3] > area[3] + 0.1 or (title_block is not None and overlap(env(n), title_block))
              for n in names)
    if clash or off:
        for n, v in before.items():
            shift[n][:] = v
