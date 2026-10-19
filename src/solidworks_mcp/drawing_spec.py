"""The drawing spec: what an old drawing says, item by item (Docs/SHOP-DRAWINGS.md §4.1).

Pure (no SolidWorks), so it is tested anywhere. It
- validates a spec and the `views` and `placements` that make_drawing takes,
- reads dimension values exactly as written (".375", "5 15/16", ".500/.498", "45°")
  and converts them to millimetres through units.in_to_mm, never by hand,
- says how each dimension must be shown (decimal places, fraction, limits, tolerance),
- and compares what check_drawing reads back from a saved drawing with the spec.

Values stay strings as written: a trailing zero or a leading dot is part of what
the drawing says. Inch fractions are binary (/2 … /64), so they convert exactly.
"""

import math
import re
from decimal import Decimal
from fractions import Fraction

from .errors import SolidWorksError
from .units import in_to_mm

KINDS = ("dimension", "thread", "hole", "finish", "datum", "gdt", "note")
STATUSES = ("read", "unreadable")
TOLERANCE_DISPLAYS = ("bilateral", "symmetric", "limits")
# Optional extra keys an item may carry besides its kind's own fields.
OPTIONAL_KEYS = {"view", "remark"}
FIELDS = {  # required keys per kind (besides id, kind, status)
    "dimension": ("value", "decimals", "tolerance", "between"),
    "thread": ("callout",),
    "hole": ("callout",),
    "finish": ("symbol", "value", "surface"),
    "datum": ("label", "feature"),
    "gdt": ("characteristic", "tolerance", "modifier", "datums", "feature"),
    "note": ("text",),
}
OPTIONAL_FIELDS = {"thread": ("depth",), "dimension": ()}

GDT_CHARACTERISTICS = (
    "straightness", "flatness", "circularity", "cylindricity", "profile of a line",
    "profile of a surface", "angularity", "perpendicularity", "parallelism", "position",
    "concentricity", "symmetry", "circular runout", "total runout",
)
GDT_ALIASES = {"roundness": "circularity", "true position": "position", "runout": "circular runout",
               "line profile": "profile of a line", "surface profile": "profile of a surface"}
GDT_MODIFIERS = ("MMC", "LMC", "RFS")
FINISH_SYMBOLS = ("basic", "machined", "no removal")
FRACTION_DENOMINATOR = 64  # the finest inch fraction a drawing shows
EXACT_IN = 5e-6  # a model value this close to the spec's is the same value

# Symbols as written in a spec, and SolidWorks' note tokens for them.
_SYMBOLS = (("Ø", "<MOD-DIAM>"), ("⌀", "<MOD-DIAM>"), ("°", "<MOD-DEG>"), ("±", "<MOD-PM>"),
            ("⌴", "<HOLE-CBORE>"), ("⌵", "<HOLE-CSINK>"), ("↧", "<HOLE-DEPTH>"))


# --- values ---------------------------------------------------------------------

_DECIMAL = r"\d*\.\d+|\d+\.?"
_FRACTION = re.compile(r"^(?:(\d+)[ -]+)?(\d+)/(\d+)$")


class Value:
    """A dimension value as written: kind 'length' (inches) or 'angle' (degrees),
    format 'decimal', 'fraction' or 'limits'. Numbers are exact Fractions."""

    def __init__(self, text, kind, fmt, nominal, places=None, upper=None, lower=None):
        self.text, self.kind, self.format = text, kind, fmt
        self.nominal, self.places, self.upper, self.lower = nominal, places, upper, lower

    def mm(self, number=None) -> float:
        """`number` (default the nominal) in mm; through units.in_to_mm, exactly."""
        return in_to_mm(_decimal_text(self.nominal if number is None else number))


def _decimal_text(number: Fraction) -> str:
    """An exact decimal string for a binary fraction (15/16 -> '0.9375')."""
    value = Decimal(number.numerator) / Decimal(number.denominator)
    if Fraction(value) != number:
        raise SolidWorksError(f"{number} is not a whole number of 64ths or a finite decimal.")
    return format(value, "f")


def _places(text: str) -> int:
    return len(text.split(".", 1)[1]) if "." in text else 0


def parse_value(text) -> Value:
    """'.375', '1.250', '5 15/16', '1/4', '.500/.498' (limits, upper first), '45°'."""
    if not isinstance(text, str) or not text.strip():
        raise SolidWorksError(f"A dimension value is a string as written, such as '.375' (got {text!r}).")
    raw = text.strip()
    body = raw.lstrip("Ø⌀").strip()
    if body.endswith("°"):
        number = body[:-1].strip()
        if not re.fullmatch(_DECIMAL, number):
            raise SolidWorksError(f"Cannot read the angle {raw!r}: write it like '45°' or '7.5°'.")
        return Value(raw, "angle", "decimal", Fraction(Decimal(number)), places=_places(number))
    if re.fullmatch(_DECIMAL, body):
        return Value(raw, "length", "decimal", Fraction(Decimal(body)), places=_places(body))
    match = _FRACTION.fullmatch(body)
    if match:
        whole, num, den = (int(g) if g else 0 for g in match.groups())
        if den == 0 or den & (den - 1) or den > FRACTION_DENOMINATOR or num >= den:
            raise SolidWorksError(f"Cannot read the fraction {raw!r}: the part after the whole inches "
                                  f"must be a proper fraction over 2, 4, … {FRACTION_DENOMINATOR}.")
        return Value(raw, "length", "fraction", whole + Fraction(num, den))
    limits = re.fullmatch(rf"({_DECIMAL})\s*/\s*({_DECIMAL})", body)
    if limits and "." in limits.group(1) and "." in limits.group(2):
        upper, lower = (Fraction(Decimal(g)) for g in limits.groups())
        if upper <= lower:
            raise SolidWorksError(f"Limits {raw!r} must be written upper/lower, the larger first.")
        if _places(limits.group(1)) != _places(limits.group(2)):
            raise SolidWorksError(f"Limits {raw!r} show different decimal places.")
        return Value(raw, "length", "limits", None, places=_places(limits.group(1)), upper=upper, lower=lower)
    raise SolidWorksError(f"Cannot read the dimension value {raw!r}: write it as on the drawing, "
                          "such as '.375', '1 1/4', '.500/.498' or '30°'.")


def _number(text, label: str) -> Fraction:
    if not isinstance(text, str) or not re.fullmatch(_DECIMAL, text.strip().lstrip("+-")):
        raise SolidWorksError(f"{label} is a string of inches as written, such as '.005' (got {text!r}).")
    return Fraction(Decimal(text.strip().lstrip("+-")))


# --- validation -------------------------------------------------------------------

def validate(spec) -> dict:
    """Check a spec against §4.1 and return {"items": [...]}: each item with its
    parsed value (dimensions). Raises SolidWorksError naming the first problem."""
    if not isinstance(spec, dict):
        raise SolidWorksError("A drawing spec is an object: {\"units\": \"in\", \"items\": [...]}.")
    if spec.get("units") != "in":
        raise SolidWorksError(f"A drawing spec's units are \"in\" (got {spec.get('units')!r}).")
    items = spec.get("items")
    if not isinstance(items, list) or not items:
        raise SolidWorksError("A drawing spec needs a non-empty \"items\" list.")
    seen, names, out = set(), {}, []
    for n, item in enumerate(items):
        where = f"spec item {n + 1}"
        if not isinstance(item, dict):
            raise SolidWorksError(f"{where} is not an object.")
        sid = item.get("id")
        if not isinstance(sid, str) or not sid.strip():
            raise SolidWorksError(f"{where} needs an \"id\" string.")
        where = f"spec item {sid}"
        if sid in seen:
            raise SolidWorksError(f"Two spec items share the id {sid!r}; ids must be unique.")
        seen.add(sid)
        name = annotation_name(sid)
        if name in names:
            raise SolidWorksError(f"Spec ids {names[name]!r} and {sid!r} are too alike (both become {name}).")
        names[name] = sid
        kind = item.get("kind")
        if kind not in KINDS:
            raise SolidWorksError(f"{where}: kind must be one of {', '.join(KINDS)} (got {kind!r}).")
        status = item.get("status")
        if status not in STATUSES:
            raise SolidWorksError(f"{where}: status is \"read\" or \"unreadable\" (got {status!r}).")
        allowed = {"id", "kind", "status", *FIELDS[kind], *OPTIONAL_FIELDS.get(kind, ()), *OPTIONAL_KEYS}
        extra = sorted(set(item) - allowed)
        if extra:
            raise SolidWorksError(f"{where}: unknown field(s) {', '.join(extra)} for a {kind}.")
        entry = dict(item)
        if status == "unreadable":
            out.append(entry)  # kept as read; never placed, never guessed
            continue
        missing = [key for key in FIELDS[kind] if key not in item]
        if missing:
            raise SolidWorksError(f"{where}: a {kind} needs {', '.join(missing)}.")
        if kind == "dimension":
            entry["parsed"] = _validate_dimension(item, where)
        elif kind in ("thread", "hole", "note"):
            key = "text" if kind == "note" else "callout"
            if not isinstance(item[key], str) or not item[key].strip():
                raise SolidWorksError(f"{where}: {key} is the text as written.")
            if kind == "thread" and item.get("depth") is not None:
                parse_value(item["depth"])
        elif kind == "finish":
            if item["symbol"] not in FINISH_SYMBOLS:
                raise SolidWorksError(f"{where}: symbol is one of {', '.join(FINISH_SYMBOLS)} (got {item['symbol']!r}).")
            if not isinstance(item["value"], str) or not item["value"].strip():
                raise SolidWorksError(f"{where}: value is the roughness as written, such as '63'.")
        elif kind == "datum":
            if not isinstance(item["label"], str) or not re.fullmatch(r"[A-Z]{1,2}", item["label"]):
                raise SolidWorksError(f"{where}: a datum label is a capital letter, such as 'A' (got {item['label']!r}).")
        elif kind == "gdt":
            entry["characteristic"] = gdt_characteristic(item["characteristic"], where)
            if not isinstance(item["tolerance"], str) or not item["tolerance"].strip():
                raise SolidWorksError(f"{where}: tolerance is the frame's tolerance as written, such as 'Ø.010'.")
            if item["modifier"] is not None and item["modifier"] not in GDT_MODIFIERS:
                raise SolidWorksError(f"{where}: modifier is null or one of {', '.join(GDT_MODIFIERS)}.")
            datums = item["datums"]
            if not isinstance(datums, list) or len(datums) > 3 or not all(
                    isinstance(d, str) and re.fullmatch(r"[A-Z]{1,2}(-[A-Z]{1,2})?", d) for d in datums):
                raise SolidWorksError(f"{where}: datums is a list of up to 3 labels in frame order, such as [\"A\", \"B\"].")
        out.append(entry)
    return {"items": out}


def _validate_dimension(item, where) -> Value:
    try:
        value = parse_value(item["value"])
    except SolidWorksError as exc:
        raise SolidWorksError(f"{where}: {exc}") from None
    decimals, tolerance = item["decimals"], item["tolerance"]
    if decimals is not None and (not isinstance(decimals, int) or isinstance(decimals, bool) or not 0 <= decimals <= 6):
        raise SolidWorksError(f"{where}: decimals is the number of decimal places shown (0-6), or null for a fraction.")
    if value.format == "fraction":
        if decimals is not None:
            raise SolidWorksError(f"{where}: {value.text!r} is a fraction, so decimals is null.")
    elif value.kind == "angle":
        if decimals is not None and decimals != value.places:
            raise SolidWorksError(f"{where}: {value.text!r} shows {value.places} decimal places, not {decimals}.")
    else:
        if decimals is None:
            raise SolidWorksError(f"{where}: give decimals for {value.text!r} (the places it shows).")
        if decimals != value.places:
            raise SolidWorksError(f"{where}: {value.text!r} shows {value.places} decimal places, not {decimals}.")
    if value.format == "limits" and tolerance is None:
        raise SolidWorksError(f"{where}: limits {value.text!r} need their tolerance (plus/minus from the nominal "
                              "the part is modelled at), with display \"limits\".")
    if tolerance is not None:
        if not isinstance(tolerance, dict) or set(tolerance) - {"plus", "minus", "display"} \
                or "plus" not in tolerance or "minus" not in tolerance:
            raise SolidWorksError(f"{where}: tolerance is null or {{\"plus\": \".005\", \"minus\": \".005\"}} "
                                  "with an optional \"display\".")
        plus, minus = _number(tolerance["plus"], f"{where}: plus"), _number(tolerance["minus"], f"{where}: minus")
        display = tolerance.get("display") or ("limits" if value.format == "limits" else
                                               "symmetric" if plus == minus else "bilateral")
        if display not in TOLERANCE_DISPLAYS:
            raise SolidWorksError(f"{where}: tolerance display is one of {', '.join(TOLERANCE_DISPLAYS)}.")
        if (display == "limits") != (value.format == "limits"):
            raise SolidWorksError(f"{where}: a limits tolerance goes with a value written as limits ('.500/.498').")
        if display == "symmetric" and plus != minus:
            raise SolidWorksError(f"{where}: a symmetric tolerance has plus equal to minus.")
        if value.format == "limits":
            if value.upper - plus != value.lower + minus:
                raise SolidWorksError(f"{where}: limits {value.text!r} and +{tolerance['plus']}/-{tolerance['minus']} "
                                      "do not give one nominal.")
            value.nominal = value.upper - plus
        value.plus, value.minus, value.display = plus, minus, display
        value.tol_places = max(_places(tolerance["plus"].lstrip("+-")), _places(tolerance["minus"].lstrip("+-")))
    else:
        value.display = None
    return value


def gdt_characteristic(name, where="gdt") -> str:
    key = str(name or "").strip().lower()
    key = GDT_ALIASES.get(key, key)
    if key not in GDT_CHARACTERISTICS:
        raise SolidWorksError(f"{where}: characteristic is one of {', '.join(GDT_CHARACTERISTICS)} (got {name!r}).")
    return key


def annotation_name(spec_id: str) -> str:
    """The name make_drawing gives the annotation for a spec item, so check_drawing
    finds it in the saved drawing: 'SPEC_D10'. Letters, digits and '_' only."""
    return "SPEC_" + re.sub(r"[^A-Za-z0-9]", "_", spec_id.strip())


def spec_id_of(name: str, spec_ids) -> str | None:
    """The spec id an annotation name belongs to, or None."""
    for sid in spec_ids:
        if annotation_name(sid) == name:
            return sid
    return None


def summary(spec) -> dict:
    """Each read dimension in inches and mm (code does the arithmetic), plus the
    unreadable items. The agent models from these numbers."""
    checked = validate(spec)
    dims = []
    for item in checked["items"]:
        value = item.get("parsed")
        if value is None:
            continue
        entry = {"id": item["id"], "value": value.text, "between": item.get("between")}
        if value.kind == "angle":
            entry["deg"] = float(value.nominal)
        else:
            entry["mm"] = value.mm()
            if value.format == "limits":
                entry["upper_mm"], entry["lower_mm"] = value.mm(value.upper), value.mm(value.lower)
        dims.append(entry)
    return {"ok": True, "dimensions": dims, "unreadable": unreadable(spec),
            "counts": _counts(checked["items"])}


def unreadable(spec) -> list:
    return [{"id": item.get("id"), "kind": item.get("kind"), "remark": item.get("remark") or item.get("between")
             or item.get("feature")} for item in spec.get("items", []) if item.get("status") == "unreadable"]


def _counts(items) -> dict:
    counts = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return counts


# --- how a value is shown ------------------------------------------------------------

def format_decimal(number, places: int) -> str:
    """ANSI inch style: no leading zero, trailing zeros kept ('.375', '1.250', '0' for 0)."""
    text = f"{float(number):.{places}f}"
    if text.startswith("0.") or text.startswith("-0."):
        text = text.replace("0.", ".", 1)
    return text


def format_fraction(number, denominator: int = FRACTION_DENOMINATOR) -> str:
    """Nearest 1/denominator, reduced: 5.9375 -> '5 15/16', .25 -> '1/4', 2 -> '2'."""
    steps = round(Fraction(number) * denominator)
    whole, rest = divmod(steps, denominator)
    if not rest:
        return str(whole)
    part = Fraction(rest, denominator)
    text = f"{part.numerator}/{part.denominator}"
    return f"{whole} {text}" if whole else text


def expected_text(item) -> str:
    """What the drawing must show for a read dimension, in the spec's own style."""
    value = item["parsed"]
    if value.format == "limits":
        return f"{format_decimal(value.upper, value.places)}/{format_decimal(value.lower, value.places)}"
    base = _plain_text(value)
    if value.display == "symmetric":
        return f"{base} ±{format_decimal(value.plus, value.tol_places)}"
    if value.display == "bilateral":
        return f"{base} +{format_decimal(value.plus, value.tol_places)}/-{format_decimal(value.minus, value.tol_places)}"
    return base


def _plain_text(value: Value) -> str:
    if value.kind == "angle":
        return f"{format_decimal(value.nominal, value.places)}°"
    if value.format == "fraction":
        return format_fraction(value.nominal)
    return format_decimal(value.nominal, value.places)


def display_settings(item) -> dict:
    """How make_drawing sets one dimension: units, places, tolerance (inches
    relative to the nominal; limits are re-based on the measured value)."""
    value = item["parsed"]
    out = {"angle": value.kind == "angle", "fraction": value.format == "fraction",
           "decimals": 0 if value.format == "fraction" else (value.places or 0),
           "tolerance": value.display or "none"}
    if value.display:
        out.update(plus_in=float(value.plus), minus_in=float(value.minus), tol_decimals=value.tol_places)
    if value.format == "limits":
        out.update(upper_in=float(value.upper), lower_in=float(value.lower), tol_decimals=value.places)
    return out


def shown_text(read: dict) -> str:
    """The text a read-back dimension shows, built from its read-back settings:
    {'value': in or deg, 'angle', 'fraction', 'decimals', 'denominator',
     'tolerance': none|bilateral|symmetric|limits|other, 'tol_min', 'tol_max', 'tol_decimals'}."""
    value, places = read["value"], read.get("decimals", 0)
    if read.get("angle"):
        base = f"{format_decimal(value, places)}°"
    elif read.get("fraction"):
        base = format_fraction(value, read.get("denominator") or FRACTION_DENOMINATOR)
    else:
        base = format_decimal(value, places)
    kind, tp = read.get("tolerance", "none"), read.get("tol_decimals", places)
    if kind == "limits":
        return f"{format_decimal(value + read['tol_max'], tp)}/{format_decimal(value + read['tol_min'], tp)}"
    if kind == "symmetric":
        return f"{base} ±{format_decimal(abs(read['tol_max']), tp)}"
    if kind == "bilateral":
        return f"{base} +{format_decimal(read['tol_max'], tp)}/-{format_decimal(-read['tol_min'], tp)}"
    if kind not in ("none", None):
        return f"{base} ({kind} tolerance)"
    return base


def compare_dimension(item, read: dict) -> dict:
    """ok / differs for one placed dimension against its spec item."""
    value = item["parsed"]
    want, got = expected_text(item), shown_text(read)
    notes = []
    if _norm_number_text(want) != _norm_number_text(got):
        notes.append(f"shows {got!r}, spec says {want!r}")
    actual = Fraction(read["value"])
    if value.format == "limits":
        if not value.lower - EXACT_IN <= actual <= value.upper + EXACT_IN:
            notes.append(f"the model measures {float(actual):.5f}, outside {value.text}")
    elif abs(actual - value.nominal) > EXACT_IN:
        unit = "°" if value.kind == "angle" else " in"
        notes.append(f"the model measures {float(actual):.5f}{unit}, spec nominal {float(value.nominal):.5f}{unit}")
    if (value.kind == "angle") != bool(read.get("angle")):
        notes.append("an angle is shown as a length, or the other way round")
    return {"status": "differs" if notes else "ok", "shown": got, "spec": want,
            **({"detail": "; ".join(notes)} if notes else {})}


def _norm_number_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("Ø", "").replace("⌀", "")).strip()


# --- note text -------------------------------------------------------------------

def to_sw_text(text: str) -> str:
    """Spec text with Ø ° ± ⌴ ⌵ ↧ as SolidWorks note tokens."""
    for char, token in _SYMBOLS:
        text = text.replace(char, token)
    return text


def from_sw_text(text: str) -> str:
    """Note text read back from SolidWorks, tokens as the spec's symbols."""
    for char, token in _SYMBOLS:
        if char != "⌀":
            text = text.replace(token, char)
    return text


def norm_text(text: str) -> str:
    """For comparing note texts: symbols unified, case and spacing ignored."""
    text = from_sw_text(text or "").replace("⌀", "Ø").replace("\r", "\n")
    return re.sub(r"\s+", " ", text).strip().upper()


def thread_text(item) -> str:
    """A thread note: the callout as written, with its depth or length when the spec gives one."""
    depth = item.get("depth")
    return item["callout"] if depth is None else f"{item['callout']}\n↧ {depth}"


# --- views and placements (make_drawing's arguments) ---------------------------------

STANDARD_DIRECTIONS = ("front", "back", "top", "bottom", "left", "right")
# third angle: where a projected view sits relative to the front view (x, y signs)
THIRD_ANGLE_SIDE = {"top": (0, 1), "bottom": (0, -1), "right": (1, 0), "left": (-1, 0)}
MEASURES = ("distance", "horizontal", "vertical", "diameter", "radius", "angle")
DEFAULT_VIEWS = [{"kind": "standard", "direction": "front"}, {"kind": "standard", "direction": "top"},
                 {"kind": "standard", "direction": "right"}]


# SolidWorks' standard model views: the direction the viewer looks FROM, and the
# model axis that points up on the sheet.
MODEL_VIEWS = {
    "front": ((0, 0, 1), (0, 1, 0)), "back": ((0, 0, -1), (0, 1, 0)),
    "top": ((0, 1, 0), (0, 0, -1)), "bottom": ((0, -1, 0), (0, 0, 1)),
    "right": ((1, 0, 0), (0, 1, 0)), "left": ((-1, 0, 0), (0, 1, 0)),
}
AXES = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1)}


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _neg(a):
    return tuple(-x for x in a)


def view_orientations(front: str = "front", up: str | None = None) -> dict:
    """For the drawing's front view (a model view, optionally turned so model axis
    `up` points up) and its third-angle projections: which model view each drawing
    view shows, and by how many degrees it is turned anticlockwise on the sheet.
    Also each view's sheet-right and sheet-up model axes. Pure."""
    d_front, natural_up = MODEL_VIEWS[front]
    u = AXES[up] if up else natural_up
    if _dot(u, d_front):
        raise SolidWorksError(f"up {up!r} points along the {front} view's line of sight; give an axis across it.")
    r = _cross(u, d_front)
    wanted = {"front": (d_front, u), "top": (u, _neg(d_front)), "bottom": (_neg(u), d_front),
              "right": (r, u), "left": (_neg(r), u)}
    out = {}
    for name, (d, up_axis) in wanted.items():
        model_view = next(m for m, (md, _) in MODEL_VIEWS.items() if md == d)
        up0 = MODEL_VIEWS[model_view][1]
        right0 = _cross(up0, d)
        phi = math.degrees(math.atan2(_dot(up_axis, up0), _dot(up_axis, right0)))
        angle = (90 - phi + 180) % 360 - 180
        out[name] = {"model_view": model_view, "angle_deg": 0.0 if abs(angle) < 1e-9 else angle + 0.0,
                     "right": _cross(up_axis, d), "up": up_axis, "towards_viewer": d}
    return out


def view_extent(size_mm, orientation) -> tuple:
    """(width, height) in mm at 1:1 of a box of size_mm seen in that orientation."""
    return (sum(abs(a) * s for a, s in zip(orientation["right"], size_mm)),
            sum(abs(a) * s for a, s in zip(orientation["up"], size_mm)))


SCALES = ((4, 1), (2, 1), (1, 1), (1, 2), (1, 4), (1, 5), (1, 10), (1, 20))
VIEW_GAP_MM = 25.0  # paper between views, room for the dimensions on both
EDGE_MM = 12.0  # paper between the views and the edge of the drawing area


def layout(views: list, orientations: dict, size_mm, area_mm) -> dict:
    """Sheet scale and view centres for checked `views` in `area_mm`
    ([x0, y0, x1, y1] on the sheet, mm). Third-angle grid around the front view:
    top above, bottom below, left and right beside; sections cut vertically go
    in further columns on the front's row, horizontal cuts above/below their
    parent's column; detail views in a last column. Largest scale that fits."""
    cells = {}  # view id -> (col, row)
    for view in views:
        if view["kind"] == "standard":
            cells[view["id"]] = {"front": (0, 0), "top": (0, -1), "bottom": (0, 1),
                                 "left": (-1, 0), "right": (1, 0)}[view["direction"]]
    extra_col = max([c for c, _ in cells.values()] + [0]) + 1
    for view in views:
        if view["kind"] == "section":
            col, row = cells[view["from"]]
            if view["cut"] == "vertical":
                cells[view["id"]] = (extra_col, row)
                extra_col += 1
            else:
                free = [r for r in (row + 1, row - 1) if (col, r) not in cells.values()]
                if not free:
                    raise SolidWorksError(f"No room above or below {view['from']!r} for {view['id']!r}; "
                                          "cut vertically, or leave out the top/bottom view.")
                cells[view["id"]] = (col, free[0])
    details = [v for v in views if v["kind"] == "detail"]
    for n, view in enumerate(details):
        cells[view["id"]] = (extra_col, n - (len(details) - 1) // 2)

    def extent(view, scale):
        if view["kind"] == "detail":
            side = 2 * view["radius_mm"] * view["scale"][0] / view["scale"][1]
            return side, side
        if view["kind"] == "section":
            parent = next(v for v in views if v["id"] == view["from"])
            key = parent.get("direction", "front")
            side = "right" if view["cut"] == "vertical" else "top"
            w, h = view_extent(size_mm, _turned(orientations, key, side))
        else:
            w, h = view_extent(size_mm, orientations[view["direction"]])
        return w * scale, h * scale

    x0, y0, x1, y1 = area_mm
    for num, den in SCALES:
        scale = num / den
        cols, rows = {}, {}
        for view in views:
            w, h = extent(view, scale)
            col, row = cells[view["id"]]
            cols[col] = max(cols.get(col, 0), w)
            rows[row] = max(rows.get(row, 0), h)
        width = sum(cols.values()) + VIEW_GAP_MM * (len(cols) - 1) + 2 * EDGE_MM
        height = sum(rows.values()) + VIEW_GAP_MM * (len(rows) - 1) + 2 * EDGE_MM
        if width <= x1 - x0 and height <= y1 - y0:
            break
    else:
        raise SolidWorksError("The views do not fit the sheet even at 1:20.")
    left = x0 + (x1 - x0 - width) / 2 + EDGE_MM
    col_x, x = {}, left
    for col in sorted(cols):
        col_x[col] = x + cols[col] / 2
        x += cols[col] + VIEW_GAP_MM
    top = y1 - (y1 - y0 - height) / 2 - EDGE_MM
    row_y, y = {}, top
    for row in sorted(rows):  # row -1 (top view) is highest on the sheet
        row_y[row] = y - rows[row] / 2
        y -= rows[row] + VIEW_GAP_MM
    return {"scale": (num, den), "centres_mm": {v["id"]: (round(col_x[cells[v["id"]][0]], 3),
                                                          round(row_y[cells[v["id"]][1]], 3)) for v in views}}


def _turned(orientations, parent: str, side: str) -> dict:
    """The orientation of a view projected from `parent` towards `side`; for
    the front it is that standard view, otherwise approximated by the front's."""
    return orientations[side] if parent == "front" else orientations.get(parent, orientations["front"])


def _point(value, label) -> list:
    try:
        point = [float(c) for c in value]
    except (TypeError, ValueError):
        point = None
    if point is None or len(point) != 3 or not all(math.isfinite(c) for c in point):
        raise SolidWorksError(f"{label} is a model point [x, y, z] in mm (got {value!r}).")
    return point


def _scale(text, label) -> tuple:
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", str(text))
    if not match or not all(int(g) for g in match.groups()):
        raise SolidWorksError(f"{label} is a scale such as \"2:1\" (got {text!r}).")
    return int(match.group(1)), int(match.group(2))


def validate_views(views) -> list:
    """make_drawing's `views`, checked and given ids (see the tool docstring)."""
    views = DEFAULT_VIEWS if views is None else views
    if not isinstance(views, list) or not views:
        raise SolidWorksError("views is a non-empty list; the first is the front (most descriptive) view.")
    out, ids, standard = [], set(), set()
    for n, view in enumerate(views):
        where = f"view {n + 1}"
        if not isinstance(view, dict):
            raise SolidWorksError(f"{where} is not an object.")
        kind = view.get("kind")
        if kind == "standard" and n == 0:
            allowed = {"kind", "direction", "model_view", "up", "id"}
            if view.get("direction", "front") != "front":
                raise SolidWorksError("The first view is the drawing's front view (direction \"front\"), the most "
                                      "descriptive one. Choose what it shows with model_view and up.")
            model_view = view.get("model_view", "front")
            if model_view not in MODEL_VIEWS:
                raise SolidWorksError(f"{where}: model_view is one of {', '.join(MODEL_VIEWS)}.")
            up = view.get("up")
            if up is not None and up not in AXES:
                raise SolidWorksError(f"{where}: up is the model axis that points up on the sheet: {', '.join(AXES)}.")
            view_orientations(model_view, up)  # refuses an up along the line of sight
            standard.add("front")
            entry = {"kind": kind, "direction": "front", "model_view": model_view, "up": up,
                     "id": view.get("id") or "front"}
        elif kind == "standard":
            allowed = {"kind", "direction", "id"}
            direction = view.get("direction")
            if direction not in THIRD_ANGLE_SIDE:
                raise SolidWorksError(f"{where}: a projected view's direction is top, bottom, left or right "
                                      "(where it sits from the front view, third angle).")
            if direction in standard:
                raise SolidWorksError(f"{where}: there is already a {direction} view.")
            standard.add(direction)
            entry = {"kind": kind, "direction": direction, "id": view.get("id") or direction}
        elif kind == "section":
            allowed = {"kind", "from", "cut", "through_mm", "label", "id", "flip"}
            if view.get("cut") not in ("vertical", "horizontal"):
                raise SolidWorksError(f"{where}: cut is \"vertical\" or \"horizontal\" (the cutting line on the parent view).")
            label = view.get("label")
            if not isinstance(label, str) or not re.fullmatch(r"[A-Z]{1,2}", label):
                raise SolidWorksError(f"{where}: label is the section's letter, such as \"A\".")
            entry = {"kind": kind, "from": view.get("from"), "cut": view["cut"], "label": label,
                     "through_mm": _point(view.get("through_mm"), f"{where}: through_mm"),
                     "flip": bool(view.get("flip", False)), "id": view.get("id") or f"section {label}"}
        elif kind == "detail":
            allowed = {"kind", "from", "center_mm", "radius_mm", "scale", "label", "id"}
            label = view.get("label")
            if not isinstance(label, str) or not re.fullmatch(r"[A-Z]{1,2}", label):
                raise SolidWorksError(f"{where}: label is the detail's letter, such as \"B\".")
            radius = view.get("radius_mm")
            if not isinstance(radius, (int, float)) or isinstance(radius, bool) or radius <= 0:
                raise SolidWorksError(f"{where}: radius_mm is the detail circle's radius on the model, in mm.")
            entry = {"kind": kind, "from": view.get("from"), "label": label, "radius_mm": float(radius),
                     "center_mm": _point(view.get("center_mm"), f"{where}: center_mm"),
                     "scale": _scale(view.get("scale"), f"{where}: scale"), "id": view.get("id") or f"detail {label}"}
        elif kind in ("isometric", "iso"):
            raise SolidWorksError("No isometric view on a shop drawing.")
        else:
            raise SolidWorksError(f"{where}: kind is \"standard\", \"section\" or \"detail\" (got {kind!r}).")
        extra = sorted(set(view) - allowed)
        if extra:
            raise SolidWorksError(f"{where}: unknown field(s) {', '.join(extra)} for a {kind} view.")
        if kind != "standard" and entry["from"] not in ids:
            raise SolidWorksError(f"{where}: from names an earlier view's id ({', '.join(sorted(ids))}).")
        if entry["id"] in ids:
            raise SolidWorksError(f"{where}: the view id {entry['id']!r} is used twice.")
        ids.add(entry["id"])
        out.append(entry)
    return out


def validate_placements(placements, checked_spec: dict, view_ids) -> dict:
    """make_drawing's `placements`: {spec id: where it goes}. Every read item of
    the spec must be placed, except notes (they go in the notes block)."""
    placements = placements or {}
    if not isinstance(placements, dict):
        raise SolidWorksError("placements is an object keyed by spec id.")
    items = {item["id"]: item for item in checked_spec["items"]}
    unknown = sorted(set(placements) - set(items))
    if unknown:
        raise SolidWorksError(f"placements names id(s) not in the spec: {', '.join(unknown)}.")
    out = {}
    for sid, place in placements.items():
        item, where = items[sid], f"placement {sid}"
        if item["status"] == "unreadable":
            raise SolidWorksError(f"{where}: the spec item is flagged unreadable. Ask the user, update the "
                                  "spec, then place it.")
        if not isinstance(place, dict):
            raise SolidWorksError(f"{where} is not an object.")
        view = place.get("view")
        kind = item["kind"]
        if kind == "note" and view is None:
            allowed = {"view"}
        elif view not in view_ids:
            raise SolidWorksError(f"{where}: view is one of the view ids {', '.join(sorted(view_ids))}.")
        entry = {"view": view}
        if kind == "dimension":
            allowed = {"view", "measure", "points_mm", "offset_mm", "side"}
            if place.get("side") not in (None, "above", "below", "left", "right"):
                raise SolidWorksError(f"{where}: side is above, below, left or right of the view.")
            measure = place.get("measure")
            if measure not in MEASURES:
                raise SolidWorksError(f"{where}: measure is one of {', '.join(MEASURES)}.")
            points = place.get("points_mm")
            count = 1 if measure in ("diameter", "radius") else 2
            if not isinstance(points, list) or len(points) != count:
                raise SolidWorksError(f"{where}: a {measure} takes {count} model point(s) in points_mm, each on "
                                      "an edge that shows in the view.")
            if (measure == "angle") != (item["parsed"].kind == "angle"):
                raise SolidWorksError(f"{where}: {item['value']!r} needs measure "
                                      f"{'angle' if item['parsed'].kind == 'angle' else 'distance, horizontal, vertical, diameter or radius'}.")
            offset = place.get("offset_mm", 10.0)
            if not isinstance(offset, (int, float)) or isinstance(offset, bool):
                raise SolidWorksError(f"{where}: offset_mm is the distance from the part to the dimension text on paper.")
            entry.update(measure=measure, offset_mm=float(offset), side=place.get("side"),
                         points_mm=[_point(p, f"{where}: points_mm[{i}]") for i, p in enumerate(points)])
        elif kind in ("datum", "gdt"):
            allowed = {"view", "on", "point_mm"}
            if ("on" in place) == ("point_mm" in place):
                raise SolidWorksError(f"{where}: give either on (a placed dimension's spec id, for a feature of "
                                      "size) or point_mm (a model point on the feature's edge).")
            if "on" in place:
                target = items.get(place["on"])
                if target is None or target["kind"] not in ("dimension", "thread", "hole"):
                    raise SolidWorksError(f"{where}: on names a dimension, thread or hole in the spec.")
                if place["on"] not in placements:
                    raise SolidWorksError(f"{where}: on names {place['on']}, which is not placed.")
                entry["on"] = place["on"]
            else:
                entry["point_mm"] = _point(place["point_mm"], f"{where}: point_mm")
        elif kind in ("finish", "thread", "hole") or (kind == "note" and view is not None):
            allowed = {"view", "point_mm"}
            entry["point_mm"] = _point(place.get("point_mm"), f"{where}: point_mm")
        extra = sorted(set(place) - allowed)
        if extra:
            raise SolidWorksError(f"{where}: unknown field(s) {', '.join(extra)} for a {kind}.")
        out[sid] = entry
    missing = [item["id"] for item in checked_spec["items"]
               if item["status"] == "read" and item["kind"] != "note" and item["id"] not in out]
    if missing:
        raise SolidWorksError(f"Every read spec item is placed on the drawing; no placement for "
                              f"{', '.join(missing)}. (Notes without one go in the notes block.)")
    return out


# --- the check ------------------------------------------------------------------------

def compare(spec, read: dict) -> dict:
    """check_drawing's verdict: one line per spec item, plus what is on the drawing
    and not in the spec. `read` is what the session read back from the saved drawing:
    {'dimensions': [{name, view, value, angle, fraction, decimals, ...}],
     'notes': [{name, text}], 'datums': [{name, label}],
     'gtols': [{name, characteristic, tolerance, modifier, datums}],
     'finishes': [{name, symbol, value}]}"""
    checked = validate(spec)
    ids = [item["id"] for item in checked["items"]]
    by_name = {}
    for group in ("dimensions", "notes", "datums", "gtols", "finishes"):
        for entry in read.get(group, []):
            by_name.setdefault(entry.get("name"), []).append((group, entry))
    used, lines = set(), []
    for item in checked["items"]:
        sid, kind = item["id"], item["kind"]
        line = {"id": sid, "kind": kind}
        if item["status"] == "unreadable":
            lines.append({**line, "status": "unreadable", "detail": "flagged unreadable when the old drawing was read"})
            continue
        found = by_name.get(annotation_name(sid), [])
        if kind == "note":  # also found by its text when it sits unnamed in the notes block
            found = found or [("notes", n) for n in read.get("notes", [])
                              if norm_text(n.get("text")) == norm_text(item["text"])]
        if not found:
            lines.append({**line, "status": "missing"})
            continue
        group, got = found[0]
        used.add(id(got))
        lines.append({**line, **_compare_one(item, group, got)})
    extra = [{"group": group, **{k: v for k, v in entry.items() if k in ("name", "view", "text", "label", "shown")}}
             for group in ("dimensions", "notes", "datums", "gtols", "finishes")
             for entry in read.get(group, []) if id(entry) not in used]
    counts = {}
    for line in lines:
        counts[line["status"]] = counts.get(line["status"], 0) + 1
    return {"ok": True, "clean": counts.get("missing", 0) == counts.get("differs", 0) == 0,
            "counts": counts, "items": lines, "not_in_spec": extra}


def _compare_one(item, group, got) -> dict:
    kind = item["kind"]
    if kind == "dimension":
        if group != "dimensions":
            return {"status": "differs", "detail": f"found as a {group[:-1]}, not a dimension"}
        return {**compare_dimension(item, got), "view": got.get("view")}
    if kind in ("note", "thread", "hole"):
        want = item["text"] if kind == "note" else thread_text(item) if kind == "thread" else item["callout"]
        text = got.get("text", "")
        if norm_text(text) == norm_text(want):
            return {"status": "ok", "shown": from_sw_text(text)}
        return {"status": "differs", "shown": from_sw_text(text), "spec": want}
    if kind == "datum":
        ok = got.get("label") == item["label"]
        return {"status": "ok" if ok else "differs", "shown": got.get("label"), "spec": item["label"]}
    if kind == "finish":
        shown = f"{got.get('symbol')} {got.get('value')}"
        ok = got.get("symbol") == item["symbol"] and norm_text(got.get("value")) == norm_text(item["value"])
        return {"status": "ok" if ok else "differs", "shown": shown, "spec": f"{item['symbol']} {item['value']}"}
    if kind == "gdt":
        want = gdt_frame_text(item["characteristic"], item["tolerance"], item["modifier"], item["datums"])
        shown = gdt_frame_text(got.get("characteristic"), got.get("tolerance", ""), got.get("modifier"),
                               got.get("datums", []))
        return {"status": "ok" if norm_text(shown) == norm_text(want) else "differs", "shown": shown, "spec": want}
    return {"status": "differs", "detail": f"no check for kind {kind}"}


def gdt_frame_text(characteristic, tolerance, modifier, datums) -> str:
    """One readable line for a feature control frame: 'position | Ø.010 MMC | A | B | C'."""
    cells = [str(characteristic), f"{tolerance} {modifier}".strip() if modifier else str(tolerance), *datums]
    return " | ".join(cells)
