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
# A dimension can be BASIC (boxed, theoretically exact, no tolerance), a
# REFERENCE (in parentheses), and carry text before or after its value as
# written ("10X", "4X", "MAX", "TYP"). A frame can carry a projected tolerance
# zone: its height as written (".56"), shown after Ⓟ.
OPTIONAL_FIELDS = {"thread": ("depth",), "dimension": ("basic", "reference", "prefix", "suffix"),
                   "gdt": ("projected",)}

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
EXACT_DEG = 1e-3  # the same for an angle (0.001 deg: 4 millionths of an inch over a quarter inch)

# Symbols as written in a spec, and SolidWorks' note tokens for them.
_SYMBOLS = (("Ø", "<MOD-DIAM>"), ("⌀", "<MOD-DIAM>"), ("°", "<MOD-DEG>"), ("±", "<MOD-PM>"),
            ("⌴", "<HOLE-SPOT>"), ("⌵", "<HOLE-SINK>"), ("↧", "<HOLE-DEPTH>"))
# SolidWorks 2020's symbol library (gtol.sym, "Hole Symbols") names the
# counterbore SPOT and the countersink SINK: <HOLE-CBORE> and <HOLE-CSINK> are
# no symbols there and print as literal text (verified in a PDF).


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
            projected = item.get("projected")
            if projected is not None and (not isinstance(projected, str)
                                          or not re.fullmatch(_DECIMAL, projected.strip())):
                raise SolidWorksError(f"{where}: projected is the projected tolerance zone's height as written, "
                                      f"such as '.56' (got {projected!r}).")
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
    value.basic, value.reference = item.get("basic", False), item.get("reference", False)
    for key in ("basic", "reference"):
        if not isinstance(item.get(key, False), bool):
            raise SolidWorksError(f"{where}: {key} is true or false.")
    if (value.basic or value.reference) and tolerance is not None:
        raise SolidWorksError(f"{where}: a {'basic' if value.basic else 'reference'} dimension has no tolerance "
                              "(tolerance null).")
    if value.basic and value.reference:
        raise SolidWorksError(f"{where}: a dimension is basic or reference, not both.")
    for key in ("prefix", "suffix"):
        text = item.get(key)
        if text is not None and (not isinstance(text, str) or not text.strip() or "\n" in text):
            raise SolidWorksError(f"{where}: {key} is the text as written on one line, such as "
                                  f"{'10X' if key == 'prefix' else 'MAX'} (got {text!r}).")
    value.prefix = (item.get("prefix") or "").strip()
    value.suffix = (item.get("suffix") or "").strip()
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
    """What the drawing must show for a read dimension, in the spec's own style:
    a basic value boxed as [3.936], a reference one as (6.78), with its prefix
    and suffix text around it ('10X [36°]', '.033 MAX')."""
    value = item["parsed"]
    if value.format == "limits":
        core = f"{format_decimal(value.upper, value.places)}/{format_decimal(value.lower, value.places)}"
    else:
        base = _plain_text(value)
        if value.display == "symmetric":
            core = f"{base} ±{format_decimal(value.plus, value.tol_places)}"
        elif value.display == "bilateral":
            core = f"{base} +{format_decimal(value.plus, value.tol_places)}/-{format_decimal(value.minus, value.tol_places)}"
        else:
            core = base
    return decorated(core, value.basic, value.reference, value.prefix, value.suffix)


def decorated(core: str, basic: bool, reference: bool, prefix: str = "", suffix: str = "") -> str:
    """A dimension's text with its box or parentheses and its prefix/suffix text."""
    text = f"[{core}]" if basic else f"({core})" if reference else core
    return " ".join(t for t in (prefix, text, suffix) if t)


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
           "tolerance": "basic" if value.basic else value.display or "none", "reference": value.reference,
           "prefix": to_sw_text(value.prefix), "suffix": to_sw_text(value.suffix)}
    if value.display:
        out.update(plus_in=float(value.plus), minus_in=float(value.minus), tol_decimals=value.tol_places)
    if value.format == "limits":
        out.update(upper_in=float(value.upper), lower_in=float(value.lower), tol_decimals=value.places)
    return out


def shown_text(read: dict) -> str:
    """The text a read-back dimension shows, built from its read-back settings:
    {'value': in or deg, 'angle', 'fraction', 'decimals', 'denominator',
     'tolerance': none|basic|bilateral|symmetric|limits|other, 'tol_min', 'tol_max', 'tol_decimals',
     'reference', 'prefix', 'suffix'} (prefix and suffix as written, without the
    Ø or R SolidWorks adds itself)."""
    return decorated(_shown_core(read), read.get("tolerance") == "basic", bool(read.get("reference")),
                     (read.get("prefix") or "").strip(), (read.get("suffix") or "").strip())


def _shown_core(read: dict) -> str:
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
    if kind not in ("none", "basic", None):
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
    elif abs(actual - value.nominal) > _model_tolerance(value):
        unit = "°" if value.kind == "angle" else " in"
        notes.append(f"the model measures {float(actual):.5f}{unit}, spec nominal {float(value.nominal):.5f}{unit}")
    if (value.kind == "angle") != bool(read.get("angle")):
        notes.append("an angle is shown as a length, or the other way round")
    return {"status": "differs" if notes else "ok", "shown": got, "spec": want,
            **({"detail": "; ".join(notes)} if notes else {})}


def _model_tolerance(value: Value) -> Fraction | float:
    """How far the model may be from a dimension's nominal: exact, except for a
    reference dimension, which is the model rounded to the places it shows."""
    exact = EXACT_DEG if value.kind == "angle" else EXACT_IN
    if value.reference and value.format != "fraction":
        return Fraction(1, 2 * 10 ** (value.places or 0)) + Fraction(exact)
    if value.reference:
        return Fraction(1, 2 * FRACTION_DENOMINATOR) + Fraction(exact)
    return exact


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
DETAIL_LABEL_MM = 14.0  # a detail view's two-line label under it (SolidWorks' outline is wider than the circle)
VIEW_LABEL_MM = 14.0  # from a section's or detail's outline to below its label ('SECTION A-A', 'SCALE 1 : 4')


def layout(views: list, orientations: dict, size_mm, area_mm, span=None) -> dict:
    """Sheet scale and view centres (see _layout). Free views keep a margin
    round them for their neighbours' text when that costs no scale. `span`,
    if given, measures the part along a model direction (mm at 1:1): a view's
    size from it, else from the bounding box (too big for a round part seen
    at a slant)."""
    roomy = None
    try:
        roomy = _layout(views, orientations, size_mm, area_mm, FREE_VIEW_MARGIN_MM, span)
    except SolidWorksError:
        pass
    tight = _layout(views, orientations, size_mm, area_mm, 0.0, span)
    if roomy is not None and roomy["scale"][0] / roomy["scale"][1] >= tight["scale"][0] / tight["scale"][1]:
        return roomy
    return tight


def _layout(views: list, orientations: dict, size_mm, area_mm, margin_mm: float, span=None) -> dict:
    """Sheet scale and view centres for checked `views` in `area_mm`
    ([x0, y0, x1, y1] on the sheet, mm). Third-angle grid around the front view:
    top above, bottom below, left and right beside; sections cut vertically go
    in further columns on their parent's row, horizontal cuts above/below their
    parent's column. Views that are not aligned with another (angled sections,
    details) each take the free cell that keeps the largest scale, and of
    those the one that keeps the sheet's views most compact (ties: details a
    row below everything, an angled section a new column). Largest scale that
    fits."""
    # placed by at_mm: a full one, or a partial one (an aligned section) whose
    # parent is placed too, down a chain (B-B from A-A from the front)
    fixed = set()
    for v in views:
        at = v.get("at_mm")
        if at is not None and (None not in at or v.get("from") in fixed):
            fixed.add(v["id"])
    cells = {}  # view id -> (col, row)
    for view in views:
        if view["id"] in fixed:
            continue
        if view["kind"] == "standard":
            cells[view["id"]] = {"front": (0, 0), "top": (0, -1), "bottom": (0, 1),
                                 "left": (-1, 0), "right": (1, 0)}[view["direction"]]
    extra_col = max([c for c, _ in cells.values()] + [0]) + 1
    axes = plan_axes(views, orientations)
    for view in views:
        if view["kind"] == "section" and view["cut"] != "angled" and view["id"] not in fixed:
            if view["from"] not in cells:
                raise SolidWorksError(f"{view['id']} is aligned with {view['from']!r}, which has its own at_mm: give "
                                      f"{view['id']} an at_mm too ([x, null] or [null, y]).")
            col, row = cells[view["from"]]
            if view["cut"] == "vertical":
                cells[view["id"]] = (max(extra_col, max(c for c, _ in cells.values()) + 1), row)
            else:
                free = [r for r in (row + 1, row - 1) if (col, r) not in cells.values()]
                if not free:
                    raise SolidWorksError(f"No room above or below {view['from']!r} for {view['id']!r}; "
                                          "cut vertically, or leave out the top/bottom view.")
                cells[view["id"]] = (col, free[0])

    def extent(view, scale, margin=True):
        if view["kind"] == "detail":
            side = 2 * view["radius_mm"] * view["scale"][0] / view["scale"][1]
            w = h = side
        else:
            a = axes[view["id"]]
            w, h = ((span(a["right"]), span(a["up"])) if span else view_extent(size_mm, a))
            w, h = w * scale, h * scale
        if margin and _is_free(view):  # room around a free view for its neighbours' dimensions and leaders
            return w + 2 * margin_mm, h + 2 * margin_mm
        return w, h

    x0, y0, x1, y1 = area_mm

    def fit(placed, start=0):
        """(index in SCALES, cols, rows, width, height) of the largest scale at which `placed` fits."""
        for n, (num, den) in enumerate(SCALES):
            if n < start:
                continue
            cols, rows = {}, {}
            for view in views:
                if view["id"] not in placed:
                    continue
                w, h = extent(view, num / den)
                col, row = placed[view["id"]]
                cols[col] = max(cols.get(col, 0), w)
                rows[row] = max(rows.get(row, 0), h)
            width = sum(cols.values()) + VIEW_GAP_MM * (len(cols) - 1) + 2 * EDGE_MM
            height = sum(rows.values()) + VIEW_GAP_MM * (len(rows) - 1) + 2 * EDGE_MM
            if width <= x1 - x0 and height <= y1 - y0:
                return n, cols, rows, width, height
        return None

    for view in views:
        if view["id"] in cells or view["id"] in fixed:
            continue
        taken = set(cells.values())
        used_cols = sorted({c for c, _ in taken}) or [0]
        used_rows = sorted({r for _, r in taken}) or [0]
        below, beside = used_rows[-1] + 1, used_cols[-1] + 1
        first = [(used_cols[0], below)] if view["kind"] == "detail" else [(beside, cells[view["from"]][1])]
        candidates = first + [(c, r) for r in used_rows + [below] for c in used_cols + [beside]
                              if (c, r) not in taken and (c, r) not in first]
        best = None
        for cell in candidates:  # the largest scale; at the same scale, the most compact layout
            got = fit({**cells, view["id"]: cell})
            if got is not None:
                rank = (got[0], round(got[3] + got[4], 3))
                if best is None or rank < best[0]:
                    best = (rank, cell)
        if best is None:
            raise SolidWorksError("The views do not fit the sheet even at 1:20.")
        cells[view["id"]] = best[1]

    if not cells:  # every view placed by at_mm: the largest scale at which they all fit there
        problem = None
        for n, scale in enumerate(SCALES):
            centres = {}
            problem = _place_fixed(views, fixed, centres, cells,
                                   lambda v: extent(v, scale[0] / scale[1], False), area_mm, scale)
            if problem is None:
                return {"scale": scale, "centres_mm": {v["id"]: (round(centres[v["id"]][0], 3),
                                                                 round(centres[v["id"]][1], 3)) for v in views}}
        raise SolidWorksError(problem)
    got = fit(cells)
    if got is None:
        raise SolidWorksError("The views do not fit the sheet even at 1:20.")
    n, cols, rows, width, height = got
    centres = _grid_centres(views, cells, cols, rows, width, height, area_mm)
    # a view placed by at_mm must fit at the scale the other views get: refused, not shrunk
    problem = _place_fixed(views, fixed, centres, cells, lambda v: extent(v, SCALES[n][0] / SCALES[n][1], False),
                           area_mm, SCALES[n])
    if problem is not None:
        raise SolidWorksError(problem)
    num, den = SCALES[n]
    return {"scale": (num, den), "centres_mm": {v["id"]: (round(centres[v["id"]][0], 3), round(centres[v["id"]][1], 3))
                                                for v in views}}


FREE_VIEW_MARGIN_MM = 12.0  # paper kept round a free view (angled section, detail) for its neighbours' text


def _is_free(view) -> bool:
    return view["kind"] == "detail" or (view["kind"] == "section" and view["cut"] == "angled")


def _grid_centres(views, cells, cols, rows, width, height, area_mm) -> dict:
    """The centre of each grid view (paper mm), the grid centred in the area."""
    x0, y0, x1, y1 = area_mm
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
    last = max(rows)
    if all(v["kind"] == "detail" for v in views if v["id"] in cells and cells[v["id"]][1] == last) and len(rows) > 1:
        # a row of details only sits at the bottom of the area: the dimensions under the views need
        # the room; its labels ('DETAIL D', 'SCALE 1 : 1') go under it, above the title block
        row_y[last] = min(y0 + EDGE_MM + DETAIL_LABEL_MM + rows[last] / 2, row_y[last])
    return {v["id"]: (col_x[cells[v["id"]][0]], row_y[cells[v["id"]][1]]) for v in views if v["id"] in cells}


def _place_fixed(views, fixed, centres, cells, extent, area_mm, scale) -> str | None:
    """Put the views with at_mm where they say (and a half-given at_mm of an
    aligned section on its free axis), into `centres`. Returns why a view cannot
    go there at the sheet's scale (off the area, which ends at the title block,
    or on another view), or None."""
    boxes = {}
    for view in views:
        if view["id"] in centres and view["id"] not in fixed and view.get("at_mm") is None:
            w, h = extent(view)
            c = centres[view["id"]]
            boxes[view["id"]] = (c[0] - w / 2, c[1] - h / 2, c[0] + w / 2, c[1] + h / 2)
    for view in views:
        at = view.get("at_mm")
        if at is None:
            continue
        if None not in at:
            centre = tuple(at)
        else:  # an aligned section: the parent fixes one coordinate
            parent = centres[view["from"]]
            centre = (at[0], parent[1]) if at[1] is None else (parent[0], at[1])
        centres[view["id"]] = centre
        w, h = extent(view)
        box = (centre[0] - w / 2, centre[1] - h / 2, centre[0] + w / 2, centre[1] + h / 2)
        where = f"{view['id']} at ({centre[0]:g}, {centre[1]:g}) mm, {w:.0f} x {h:.0f} mm at {scale[0]}:{scale[1]},"
        if not box_is_free(box, area_mm, []):
            return (f"{where} runs off the drawing area {tuple(area_mm)} (it ends at the border and above the "
                    f"title block). Move it, or leave out at_mm.")
        for other, b in boxes.items():
            if not box_is_free(box, area_mm, [b], gap=0.0):
                return (f"{where} lands on {other} (centred at ({(b[0] + b[2]) / 2:.0f}, {(b[1] + b[3]) / 2:.0f}) mm, "
                        f"{b[2] - b[0]:.0f} x {b[3] - b[1]:.0f} mm). Move it, or leave out at_mm.")
        boxes[view["id"]] = box
    return None


def plan_axes(views, orientations) -> dict:
    """Each planned view's sheet-right, sheet-up and line-of-sight model axes,
    for its size on the sheet (signs aside: a section may look either way).
    A vertical cut looks across its parent's right, a horizontal one across its
    up; an angled cut across its own line, with `up` along the line or as given.
    A detail shows its parent's axes. Pure."""
    out = {}
    for view in views:
        if view["kind"] == "standard":
            o = orientations[view["direction"]]
            out[view["id"]] = {"right": o["right"], "up": o["up"], "towards_viewer": o["towards_viewer"]}
            continue
        parent = out[view["from"]]
        if view["kind"] == "detail":
            out[view["id"]] = dict(parent)
            continue
        r, u, t = parent["right"], parent["up"], parent["towards_viewer"]
        if view["cut"] == "vertical":
            out[view["id"]] = {"right": t, "up": u, "towards_viewer": r}
            continue
        if view["cut"] == "horizontal":
            out[view["id"]] = {"right": r, "up": t, "towards_viewer": u}
            continue
        angle = math.radians(cut_angle_deg(view, parent))
        along = tuple(math.cos(angle) * a + math.sin(angle) * b for a, b in zip(r, u))
        sight = tuple(-math.sin(angle) * a + math.cos(angle) * b for a, b in zip(r, u))
        up = along
        if view.get("up") is not None:
            w = view["up"]
            w = [c - _dot(w, sight) * s for c, s in zip(w, sight)]  # the part of it in the section's plane
            n = math.sqrt(_dot(w, w))
            if n < 1e-9:
                raise SolidWorksError(f"{view['id']}: up points along the section's line of sight; give a direction "
                                      "in the cutting plane.")
            up = tuple(c / n for c in w)
        out[view["id"]] = {"right": _cross(up, sight), "up": up, "towards_viewer": sight}
    return out


def cut_angle_deg(view, parent_axes) -> float:
    """An angled cut's angle on its parent's sheet (anticlockwise from the
    parent's horizontal): angle_deg, or the angle of line_mm as the parent shows it."""
    if view.get("line_mm") is None:
        return view["angle_deg"]
    a, b = view["line_mm"]
    d = [q - p for p, q in zip(a, b)]
    x, y = _dot(d, parent_axes["right"]), _dot(d, parent_axes["up"])
    if math.hypot(x, y) < 1e-6:
        raise SolidWorksError(f"{view['id']}: the two points of line_mm lie one behind the other in the "
                              "parent view; pick points apart on the sheet.")
    return math.degrees(math.atan2(y, x))


def angle_text_spot(line1, line2, nominal_deg: float, distance_mm: float, away_from=None) -> tuple:
    """The first of angle_text_spots: the most likely place for the text."""
    return angle_text_spots(line1, line2, nominal_deg, distance_mm, away_from)[0]


def angle_text_spots(line1, line2, nominal_deg: float, distance_mm: float, away_from=None) -> list:
    """Where an angle dimension's text goes so it shows `nominal_deg`: two
    sheet lines ((x, y), (dx, dy)) make four angles, and SolidWorks shows the
    one whose sector holds the text. The point is on that sector's bisector,
    distance_mm from where the lines meet. Pure."""
    (p1, d1), (p2, d2) = line1, line2
    d1, d2 = _unit2(d1), _unit2(d2)
    cross = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(cross) < 1e-9:
        raise SolidWorksError("The two lines of an angle are parallel on the sheet.")
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / cross
    meet = (p1[0] + t * d1[0], p1[1] + t * d1[1])
    sectors = []
    for a in (d1, (-d1[0], -d1[1])):
        for b in (d2, (-d2[0], -d2[1])):
            angle = math.degrees(math.acos(max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1]))))
            bisector = _unit2((a[0] + b[0], a[1] + b[1]))
            sectors.append((abs(angle - nominal_deg), angle, bisector))
    fits = [s for s in sectors if s[0] <= 1e-3]
    if not fits:
        shown = sorted({round(s[1], 3) for s in sectors})
        raise SolidWorksError(f"The lines meet at {' or '.join(f'{a:g}' for a in shown)} deg, not {nominal_deg:g}.")
    # each sector that shows the angle, near and further out; SolidWorks does not
    # always measure the sector the text is in (it gave 225 for 45 on DTN6), so
    # the caller tries them in turn
    spots = [(meet[0] + r * b[0], meet[1] + r * b[1]) for r in (distance_mm, 2 * distance_mm) for _, _, b in fits]
    if away_from is not None:  # two opposite sectors show the same angle: away from the part first
        spots.sort(key=lambda p: -math.dist(p, away_from))
    return spots


def clip_line(point, direction, rect) -> tuple | None:
    """The two ends of the line through `point` along `direction` where it
    crosses the rectangle (x0, y0, x1, y1), or None when it misses. Pure."""
    (px, py), (dx, dy) = point, _unit2(direction)
    lo, hi = -math.inf, math.inf
    for p, d, a, b in ((px, dx, rect[0], rect[2]), (py, dy, rect[1], rect[3])):
        if abs(d) < 1e-12:
            if not a <= p <= b:
                return None
            continue
        t1, t2 = sorted(((a - p) / d, (b - p) / d))
        lo, hi = max(lo, t1), min(hi, t2)
    if hi - lo < 1e-9:
        return None
    return (px + lo * dx, py + lo * dy), (px + hi * dx, py + hi * dy)


def box_is_free(box, area, taken, gap: float = 2.0) -> bool:
    """Whether a box (x0, y0, x1, y1) lies on the area and clear of the taken
    boxes by `gap`. Pure."""
    if box[0] < area[0] or box[1] < area[1] or box[2] > area[2] or box[3] > area[3]:
        return False
    return all(box[2] + gap <= t[0] or t[2] + gap <= box[0] or box[3] + gap <= t[1] or t[3] + gap <= box[1]
               for t in taken)


def ray_side(point, line1, line2) -> float:
    """How far `point` lies into the sector between two rays ((start, direction)
    on the sheet), along the bisector from the first ray's start: centrelines
    drawn out from a centre measure the angle between their directions. Pure."""
    (p, d1), (_, d2) = line1, line2
    a, b = _unit2(d1), _unit2(d2)
    bisector = _unit2((a[0] + b[0], a[1] + b[1])) if math.hypot(a[0] + b[0], a[1] + b[1]) > 1e-9 else (-a[1], a[0])
    return (point[0] - p[0]) * bisector[0] + (point[1] - p[1]) * bisector[1]


def _unit2(v) -> tuple:
    n = math.hypot(*v)
    return v[0] / n, v[1] / n


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
    out, ids, standard, mark_ids = [], set(), set(), set()
    for n, view in enumerate(views):
        where = f"view {n + 1}"
        if not isinstance(view, dict):
            raise SolidWorksError(f"{where} is not an object.")
        kind = view.get("kind")
        if kind == "standard" and n == 0:
            allowed = {"kind", "direction", "model_view", "up", "id", "marks", "at_mm"}
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
            allowed = {"kind", "direction", "id", "marks", "at_mm"}
            direction = view.get("direction")
            if direction not in THIRD_ANGLE_SIDE:
                raise SolidWorksError(f"{where}: a projected view's direction is top, bottom, left or right "
                                      "(where it sits from the front view, third angle).")
            if direction in standard:
                raise SolidWorksError(f"{where}: there is already a {direction} view.")
            standard.add(direction)
            entry = {"kind": kind, "direction": direction, "id": view.get("id") or direction}
        elif kind == "section":
            allowed = {"kind", "from", "cut", "through_mm", "angle_deg", "line_mm", "up", "label", "id", "flip",
                       "marks", "at_mm"}
            label = view.get("label")
            if not isinstance(label, str) or not re.fullmatch(r"[A-Z]{1,2}", label):
                raise SolidWorksError(f"{where}: label is the section's letter, such as \"A\".")
            entry = {"kind": kind, "from": view.get("from"), "label": label,
                     "flip": bool(view.get("flip", False)), "id": view.get("id") or f"section {label}"}
            entry.update(_section_cut(view, where))
        elif kind == "detail":
            allowed = {"kind", "from", "center_mm", "radius_mm", "scale", "label", "id", "marks", "at_mm"}
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
        entry["marks"] = _validate_marks(view.get("marks"), where, mark_ids)
        entry["at_mm"] = _view_at(view, entry, where)
        out.append(entry)
    return out


def _view_at(view, entry, where):
    """A view's "at_mm": [x, y], its centre in paper mm from the sheet's lower
    left corner, or None (the layout places it). A section cut vertically or
    horizontally stays aligned with its parent: give null for the coordinate
    the alignment fixes ([x, null] for a vertical cut, [null, y] for a horizontal one)."""
    at = view.get("at_mm")
    if at is None:
        return None
    if not isinstance(at, list) or len(at) != 2:
        raise SolidWorksError(f"{where}: at_mm is the view's centre on the sheet, [x, y] in paper mm.")
    aligned = {"vertical": 1, "horizontal": 0}.get(entry.get("cut")) if entry["kind"] == "section" else None
    out = []
    for i, c in enumerate(at):
        if c is None and i == aligned:
            out.append(None)
            continue
        if not isinstance(c, (int, float)) or isinstance(c, bool) or not math.isfinite(c):
            hint = (f" A {entry['cut']} section stays aligned with its parent: give null for "
                    f"{'y' if aligned else 'x'}." if aligned is not None else "")
            raise SolidWorksError(f"{where}: at_mm is [x, y] in paper mm (got {at!r}).{hint}")
        out.append(float(c))
    if aligned is not None and out[aligned] is not None:
        raise SolidWorksError(f"{where}: a {entry['cut']} section stays aligned with its parent; at_mm gives only "
                              f"its {'x' if aligned else 'y'}: {'[x, null]' if aligned else '[null, y]'}.")
    return out


def _section_cut(view, where) -> dict:
    """The cutting line of a section view: "cut": "vertical" / "horizontal"
    through through_mm, "angle_deg" (on the parent's sheet, anticlockwise from
    its horizontal) through through_mm, or "line_mm": two model points the line
    passes through as the parent shows them. An angled cut may say which model
    direction points `up` in the section (default: along the cutting line)."""
    given = [k for k in ("cut", "angle_deg", "line_mm") if view.get(k) is not None]
    if len(given) != 1:
        raise SolidWorksError(f"{where}: give one of cut (\"vertical\" or \"horizontal\"), angle_deg "
                              "(with through_mm) or line_mm (two model points the cutting line runs through).")
    if view.get("up") is not None and given[0] == "cut":
        raise SolidWorksError(f"{where}: up turns an angled section; a vertical or horizontal one stays aligned "
                              "with its parent.")
    up = view.get("up")
    if up is not None:
        up = list(AXES[up]) if up in AXES else _point(up, f"{where}: up (a model axis such as \"+y\" or a vector)")
        if not any(up):
            raise SolidWorksError(f"{where}: up is a direction, not [0, 0, 0].")
    if given[0] == "cut":
        if view["cut"] not in ("vertical", "horizontal"):
            raise SolidWorksError(f"{where}: cut is \"vertical\" or \"horizontal\" (the cutting line on the parent view).")
        if view.get("line_mm") is not None:
            raise SolidWorksError(f"{where}: line_mm goes without cut.")
        return {"cut": view["cut"], "through_mm": _point(view.get("through_mm"), f"{where}: through_mm"), "up": None}
    if given[0] == "angle_deg":
        angle = view["angle_deg"]
        if not isinstance(angle, (int, float)) or isinstance(angle, bool) or not math.isfinite(angle):
            raise SolidWorksError(f"{where}: angle_deg is the cutting line's angle on the parent view, in degrees.")
        return {"cut": "angled", "angle_deg": float(angle), "up": up,
                "through_mm": _point(view.get("through_mm"), f"{where}: through_mm")}
    line = view["line_mm"]
    if not isinstance(line, list) or len(line) != 2:
        raise SolidWorksError(f"{where}: line_mm is two model points [[x, y, z], [x, y, z]].")
    if view.get("through_mm") is not None:
        raise SolidWorksError(f"{where}: line_mm already says where the cut runs; leave through_mm out.")
    points = [_point(p, f"{where}: line_mm[{i}]") for i, p in enumerate(line)]
    if math.dist(*points) < 1e-6:
        raise SolidWorksError(f"{where}: the two points of line_mm are the same point.")
    return {"cut": "angled", "line_mm": points, "through_mm": points[0], "up": up}


# Centre marks and centrelines a view carries, so dimensions can measure to hole
# centres, bolt circles and axes. Each is checked against the model when drawn.
MARK_KINDS = ("center_mark", "circle", "line", "point")


def _validate_marks(marks, where, ids: set) -> list:
    """A view's "marks": [{"kind": "center_mark", "at_mm"}, {"kind": "circle", "id",
    "center_mm", "through_mm"}, {"kind": "line", "id", "from_mm", "to_mm",
    "extend_mm"}, {"kind": "point", "id", "at_mm"}]. Ids are unique across views."""
    if marks is None:
        return []
    if not isinstance(marks, list):
        raise SolidWorksError(f"{where}: marks is a list of centre marks and centrelines.")
    out = []
    fields = {"center_mark": ("at_mm",), "circle": ("center_mm", "through_mm"), "line": ("from_mm", "to_mm"),
              "point": ("at_mm",)}
    for n, mark in enumerate(marks):
        here = f"{where} mark {n + 1}"
        if not isinstance(mark, dict) or mark.get("kind") not in MARK_KINDS:
            raise SolidWorksError(f"{here}: kind is one of {', '.join(MARK_KINDS)}.")
        kind = mark["kind"]
        extra = sorted(set(mark) - {"kind", "id", *fields[kind], *(("extend_mm",) if kind == "line" else ())})
        if extra:
            raise SolidWorksError(f"{here}: unknown field(s) {', '.join(extra)} for a {kind}.")
        mid = mark.get("id")
        if kind != "center_mark" or mid is not None:
            if not isinstance(mid, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", mid):
                raise SolidWorksError(f"{here}: a {kind} needs an id (letters, digits, _), which placements "
                                      "use to measure to it.")
            if mid in ids:
                raise SolidWorksError(f"{here}: the mark id {mid!r} is used twice.")
            ids.add(mid)
        entry = {"kind": kind, "id": mid, **{k: _point(mark.get(k), f"{here}: {k}") for k in fields[kind]}}
        if kind == "line":
            extend = mark.get("extend_mm", 5.0)
            if not isinstance(extend, (int, float)) or isinstance(extend, bool) or extend < 0:
                raise SolidWorksError(f"{here}: extend_mm is how far the line runs past its points on paper (mm).")
            entry["extend_mm"] = float(extend)
            if math.dist(entry["from_mm"], entry["to_mm"]) < 1e-6:
                raise SolidWorksError(f"{here}: from_mm and to_mm are the same point.")
        if kind == "circle" and math.dist(entry["center_mm"], entry["through_mm"]) < 1e-6:
            raise SolidWorksError(f"{here}: through_mm is on the circle, away from center_mm.")
        out.append(entry)
    return out


def validate_placements(placements, checked_spec: dict, view_ids, marks: dict | None = None) -> dict:
    """make_drawing's `placements`: {spec id: where it goes}. Every read item of
    the spec must be placed, except notes (they go in the notes block). A
    dimension's points_mm entries may name a mark of its view by id (marks:
    {view id: [mark, ...]}, as validate_views gives them)."""
    marks = marks or {}
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
                                      "an edge that shows in the view, or the id of a mark of the view.")
            view_marks = {m["id"]: m for m in marks.get(view, []) if m["id"]}
            for p in points:
                if isinstance(p, str):
                    mark = view_marks.get(p)
                    if mark is None:
                        raise SolidWorksError(f"{where}: {p!r} is no mark of view {view!r} "
                                              f"({', '.join(sorted(view_marks)) or 'it has none'}).")
                    fits = {"diameter": ("circle",), "radius": ("circle",), "angle": ("line",)}.get(
                        measure, ("line", "point"))
                    if mark["kind"] not in fits:
                        raise SolidWorksError(f"{where}: a {measure} measures to a {' or '.join(fits)} mark, "
                                              f"not the {mark['kind']} {p!r}.")
            if (measure == "angle") != (item["parsed"].kind == "angle"):
                raise SolidWorksError(f"{where}: {item['value']!r} needs measure "
                                      f"{'angle' if item['parsed'].kind == 'angle' else 'distance, horizontal, vertical, diameter or radius'}.")
            offset = place.get("offset_mm", 10.0)
            if not isinstance(offset, (int, float)) or isinstance(offset, bool):
                raise SolidWorksError(f"{where}: offset_mm is the distance from the part to the dimension text on paper.")
            entry.update(measure=measure, offset_mm=float(offset), side=place.get("side"),
                         points_mm=[p if isinstance(p, str) else _point(p, f"{where}: points_mm[{i}]")
                                    for i, p in enumerate(points)])
        elif kind in ("datum", "gdt"):
            allowed = {"view", "on", "point_mm", *TEXT_KEYS}
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
            allowed = {"view", "point_mm", *TEXT_KEYS}
            entry["point_mm"] = _point(place.get("point_mm"), f"{where}: point_mm")
        if kind in ("finish", "thread", "hole", "datum", "gdt") or (kind == "note" and view is not None):
            entry.update(_text_place(place, where))
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


TEXT_KEYS = ("side", "offset_mm", "text_mm")


def _text_place(place, where) -> dict:
    """Where a leader item's text goes: "side" (above/below/left/right of its
    view, beyond its outline) with "offset_mm" (paper mm from the outline,
    default 10), or "text_mm": [dx, dy], the text's top-left corner in paper mm
    from the point its leader attaches to (from the dimension's text, for a
    datum or frame "on" a dimension). Neither: the tool picks a free line."""
    text = place.get("text_mm")
    if text is not None:
        if "side" in place or "offset_mm" in place:
            raise SolidWorksError(f"{where}: give text_mm, or side with offset_mm, not both.")
        if (not isinstance(text, list) or len(text) != 2
                or not all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in text)):
            raise SolidWorksError(f"{where}: text_mm is [dx, dy] in paper mm from the attach point.")
        return {"text_mm": [float(c) for c in text]}
    out = {}
    if place.get("side") is not None:
        if place["side"] not in ("above", "below", "left", "right"):
            raise SolidWorksError(f"{where}: side is above, below, left or right of the view.")
        out["side"] = place["side"]
    if place.get("offset_mm") is not None:
        offset = place["offset_mm"]
        if not isinstance(offset, (int, float)) or isinstance(offset, bool) or offset < 0:
            raise SolidWorksError(f"{where}: offset_mm is the paper distance from the view's outline.")
        if "side" not in out:
            raise SolidWorksError(f"{where}: offset_mm goes with a side.")
        out["offset_mm"] = float(offset)
    if "on" in place and out:
        raise SolidWorksError(f"{where}: a symbol on a dimension follows its text; move it with text_mm.")
    return out


# --- the layout report ------------------------------------------------------------------

LEADER_MAX_MM = 40.0  # a longer leader on paper is reported
DIM_STACK_MIN_MM = 6.0  # dimension texts of one view closer than this are reported


def text_box(runs) -> tuple | None:
    """The box (x0, y0, x1, y1) round text runs [(x, y, width, height, angle)]:
    each run's reference point is its baseline's left end, turned by angle. Pure."""
    corners = []
    for x, y, w, h, a in runs:
        c, s_ = math.cos(a), math.sin(a)
        corners += [(x + u * c - v * s_, y + u * s_ + v * c) for u, v in ((0, 0), (w, 0), (w, h), (0, h))]
    if not corners:
        return None
    xs, ys = [p[0] for p in corners], [p[1] for p in corners]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap(a, b, gap=0.0) -> bool:
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


def _box_distance(a, b) -> float:
    dx = max(0.0, b[0] - a[2], a[0] - b[2])
    dy = max(0.0, b[1] - a[3], a[1] - b[3])
    return math.hypot(dx, dy)


def layout_problems(items, views, border, title_block, leader_max=LEADER_MAX_MM,
                    stack_min=DIM_STACK_MIN_MM) -> list:
    """What is wrong with the layout, measured: items are the placed texts
    [{"name", "view", "kind", "box", "leader_mm"}] (paper mm), views {name: the
    box the view draws in}. Problems: text on text, text on a view, text nearer
    another view than its own, text off the border or on the title block, a
    long leader, and dimension texts of one view stacked too close. Pure."""
    out = []
    boxed = [i for i in items if i.get("box")]
    for n, a in enumerate(boxed):
        for b in boxed[n + 1:]:
            if _overlap(a["box"], b["box"]):
                out.append({"problem": "text_overlap", "items": [a["name"], b["name"]]})
            elif (a["kind"] == b["kind"] == "dimension" and a["view"] == b["view"]
                  and _box_distance(a["box"], b["box"]) < stack_min):
                out.append({"problem": "dimensions_close", "items": [a["name"], b["name"]],
                            "gap_mm": round(_box_distance(a["box"], b["box"]), 1)})
    for a in boxed:
        box = a["box"]
        for name, vbox in views.items():
            if _overlap(box, vbox):
                out.append({"problem": "text_on_view", "item": a["name"], "on": name, "own": name == a["view"]})
        own = views.get(a["view"])
        if own is not None and a["kind"] != "label":
            mine = _box_distance(box, own)
            nearest = min(((_box_distance(box, v), k) for k, v in views.items() if k != a["view"]), default=None)
            if nearest is not None and nearest[0] + 0.5 < mine:
                out.append({"problem": "nearer_other_view", "item": a["name"], "nearer": nearest[1],
                            "own_mm": round(mine, 1), "other_mm": round(nearest[0], 1)})
        if box[0] < border[0] or box[1] < border[1] or box[2] > border[2] or box[3] > border[3]:
            out.append({"problem": "off_border", "item": a["name"]})
        if title_block is not None and _overlap(box, title_block):
            out.append({"problem": "on_title_block", "item": a["name"]})
        if (a.get("leader_mm") or 0) > leader_max:
            out.append({"problem": "long_leader", "item": a["name"], "leader_mm": round(a["leader_mm"], 1)})
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
        want = gdt_frame_text(item["characteristic"], item["tolerance"], item["modifier"], item["datums"],
                              item.get("projected"))
        shown = gdt_frame_text(got.get("characteristic"), got.get("tolerance", ""), got.get("modifier"),
                               got.get("datums", []), got.get("projected"))
        return {"status": "ok" if norm_text(shown) == norm_text(want) else "differs", "shown": shown, "spec": want}
    return {"status": "differs", "detail": f"no check for kind {kind}"}


def gdt_frame_text(characteristic, tolerance, modifier, datums, projected=None) -> str:
    """One readable line for a feature control frame: 'position | Ø.010 MMC | A | B | C',
    with a projected tolerance zone 'position | Ø.041 Ⓟ .56 | A | B | C'."""
    cell = " ".join(t for t in (str(tolerance), modifier or "", f"Ⓟ {projected.strip()}" if projected else "") if t)
    return " | ".join([str(characteristic), cell, *datums])


CHAR_MM = 2.2  # the width of a note character on the stock A-size format (3 mm text)
LINE_MM = 6.0  # one line of note text, with its gap


class TextSlots:
    """Keeps leader notes, finish symbols and frames from landing on each other:
    per view and side (above / below), the paper lines the dimensions use and
    which stretches of each line further out are taken. Pure."""

    def __init__(self, area_mm=None) -> None:
        self.floor, self.taken, self.area = {}, {}, area_mm

    def reserve(self, view: str, side: str, offset_mm: float) -> None:
        """A dimension's text this far off the view: leader text goes further out."""
        key = (view, side)
        self.floor[key] = max(self.floor.get(key, 0.0), offset_mm)

    def place(self, view: str, side: str, x_mm: float, edge_mm: float, width_mm: float,
              lines: int = 1) -> tuple:
        """(x, y) for a text `width_mm` wide that starts at x, on the first free
        line beyond the view's edge (edge_mm: its top for 'above', bottom for 'below')."""
        if self.area is not None:  # keep the text on the drawing area: left of its right edge
            x_mm = max(self.area[0], min(x_mm, self.area[2] - width_mm))
        sign = 1 if side == "above" else -1
        base = self.floor.get((view, side), 0.0) + 8.0
        level = 0
        while True:
            used = self.taken.setdefault((view, side, level), [])
            span = (x_mm, x_mm + width_mm)
            if all(span[1] + 3 <= a or b + 3 <= span[0] for a, b in used):
                used.append(span)
                for extra in range(1, lines):  # a text of several lines takes the lines beyond too
                    self.taken.setdefault((view, side, level + extra), []).append(span)
                return x_mm, edge_mm + sign * (base + LINE_MM * (level + lines - 1 if sign > 0 else level))
            level += 1
