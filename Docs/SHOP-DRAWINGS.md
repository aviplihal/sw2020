# Shop Drawings — Design

> Status: **built (D1–D7) and verified on SOLIDWORKS 2020 SP4 through the bridge** (§11).
> Branch: `feat/shop-drawings`.
> Companion design: [KNOWLEDGE-BASE.md](KNOWLEDGE-BASE.md) (branch `feat/knowledge-base`),
> built at the same time by another agent. Read §8 (working alongside the knowledge base)
> before starting.

## 1. Purpose

Every job is: **an old drawing comes in → Claude models the part → Claude makes a new
drawing.** The new drawing is a **clean copy of the old one**, with a STEP file behind it.
Nothing on it is invented: precision, tolerances and callouts all come from the old drawing.

**The new drawing is a shop-floor document.** After review, parts are **machined from it**.
A wrong or missing dimension is a scrapped part, not a cosmetic issue.

## 2. What the current `make_drawing` does, and what is wrong with it

`make_drawing(path)` in `session.py` (saves `.pdf` or `.slddrw`, `DRAWING_FORMATS`) places:
- front, top and right views in **first-angle (European) projection** — wrong for this shop
- an **isometric** view — never wanted
- an **A4** sheet at the scale SolidWorks picks — wrong size
- the model's own dimensions, each shown once, in **millimetres** — wrong units

It does **not** add: per-dimension precision, tolerances, GD&T or fits, surface-finish
symbols, thread or hole callouts, notes, section or detail views, or any choice of which
datums dimensions are measured from.

## 3. Shop drawing standard (decided 2026-10-18)

| Item | Decision | Change from current `make_drawing` |
|---|---|---|
| Projection | **Third-angle** (ANSI) | Was first-angle: use SolidWorks' third-angle view creation |
| Views | **Chosen per part by good drafting practice** (§4.4). **No isometric, ever** | Was a fixed front/top/right + isometric |
| Units | **Inches** on the drawing | Was millimetres |
| Template, sheet and title block | **The user's default drawing template**, the one SolidWorks' own *New → Drawing* uses. On this SOLIDWORKS 2020 install that template carries **no sheet format** (New → Drawing asks for one), so the sheet is **always A size (11 × 8.5 in) landscape with SolidWorks' stock ANSI format `a - landscape.slddrt`** and its title block (decided by the user, 2026-10-19) | Was A4. `make_drawing` starts from the default template, then sets the sheet with `SetupSheet5` |
| Title block contents | **Left empty.** The user fills it in; it holds information the model cannot see | The server writes nothing into it |

**Inches in, millimetres inside, inches out.** The server keeps working in millimetres at
its boundary (rule 3 in `CLAUDE.md`):
- **Conversion is done by code, never by Claude in its head.** 1 in = 25.4 mm exactly, so
  the conversion is lossless; mental arithmetic is where mistakes would come from. The
  inch/mm helpers live in `units.py` (added in the shared commit, §8.1).
- Dimensions on the new drawing are **displayed in inches**, at the precision the spec gives.

## 4. The drawing spec

Precision and callouts differ per contract and per drawing, so there is **no house default:
whatever the old drawing says is what the new drawing says.** Claude reads the old drawing
once and writes a **drawing spec**: a structured list of everything on it. The spec drives
the model, the drawing, and the final check.

### 4.1 Format (the contract: frozen in the shared commit, §8.1)

```json
{
  "units": "in",
  "items": [
    {"id": "D1", "kind": "dimension", "type": "linear", "value": ".375", "decimals": 3,
     "tolerance": {"type": "symmetric", "plus": ".005"},
     "view": "front", "between": "left face to bore centre", "status": "read",
     "link": {"dimension": "D1@Sketch2"}},
    {"id": "D2", "kind": "dimension", "type": "diameter", "value": null, "decimals": 4,
     "tolerance": {"type": "limit", "upper": ".3755", "lower": ".3750"},
     "view": "front", "between": "bore", "status": "read",
     "link": {"dimension": "diameter@Sketch3"}},
    {"id": "D3", "kind": "dimension", "type": "linear", "value": "2.000", "decimals": 3,
     "tolerance": {"type": "basic"}, "prefix": "2X",
     "view": "top", "between": "hole pattern spacing", "status": "read",
     "link": {"dimension": "spacing@Sketch4"}},
    {"id": "D4", "kind": "dimension", "type": "angle", "value": "30°", "decimals": 0,
     "tolerance": {"type": "symmetric", "plus": "0°30'"},
     "view": "front", "between": "chamfer face to top face", "status": "read",
     "link": {"dimension": "angle@Chamfer1"}},
    {"id": "D5", "kind": "dimension", "type": "linear", "value": "3.50", "decimals": 2,
     "tolerance": null, "reference": true,
     "view": "front", "between": "overall length", "status": "read",
     "link": {"between": ["face:-x", "face:+x"]}},
    {"id": "D6", "kind": "dimension", "type": "diameter", "value": ".500", "decimals": 3,
     "tolerance": {"type": "fit", "hole": "H7", "shaft": null, "show_limits": true},
     "view": "top", "between": "dowel hole", "status": "read"},
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
    {"id": "D7", "kind": "dimension", "value": null, "view": "right",
     "between": "slot width", "status": "unreadable"}
  ]
}
```

Rules:
- **Values are strings exactly as written** (`".375"`, `".50"`), never floats: a trailing
  zero or a leading dot is part of what the drawing says. `decimals` is explicit **per
  dimension**, because one drawing can mix `.xx` and `.xxx`.
- `type` says what a dimension measures: `linear`, `diameter`, `radius`, `angle` or
  `chamfer` (a chamfer callout: `value` is the leg length, `angle` is its angle, e.g.
  `".03"` and `"45°"`). Only `linear`, `diameter`, `radius` and a chamfer's leg are
  converted to mm. Angles stay in degrees: the modelling tools take degrees (rule 3 in
  `CLAUDE.md`), and `0°30'` is converted to `0.5`.
- `tolerance` is `null` when the old drawing shows none on that dimension (the title-block
  tolerance applies, which the user handles). Otherwise its `type` is one of:

  | `type` | Drawn as | Fields |
  |---|---|---|
  | `symmetric` | `.375 ±.005` | `plus` |
  | `bilateral` | `.375 +.005/-.002` (including `+.005/-.000`) | `plus`, `minus` |
  | `limit` | `.3755/.3750`, stacked | `upper`, `lower`; the dimension's `value` is `null` because no nominal is written |
  | `min` / `max` | `.375 MIN` | none |
  | `basic` | `2.000` in a box, theoretically exact (§4.1.1) | none |
  | `fit` | `Ø.500 H7`, or `H7/g6` | `hole`, `shaft` (either may be `null`), `show_limits`: also show the limits in brackets |

- `reference: true` is a reference dimension, drawn in brackets `(3.50)` with no tolerance.
  It is information only and is never inspected.
- `prefix` and `suffix` are the text around the value, exactly as written: `"2X"`, `"4X"`,
  `"TYP"`, `"THRU"`. `"4X"` is part of the dimension, not a separate note.
- **`link` (optional, filled in while modelling, not when reading)** says which part of the
  model the item belongs to, in the terms the other tools already use:
  - `{"dimension": "width@Sketch1"}`: a model dimension, by the names the modelling tools
    return. The drawing shows that dimension; the check (§6) finds it by its name.
  - `{"between": ["face:-x", "hole:#2"]}`: no model dimension covers it, so the drawing adds
    one between these entities (faces by direction or `#index`, holes, edges).
  - `{"feature": "CBORE for 1/4"}` or `{"face": "+z"}`: where a callout, datum symbol, GD&T
    frame or finish symbol is attached.

  The reading fields (`view`, `between`, `feature`, `surface`) stay as written: they record
  what the old drawing said, and the `link` records where it went in the model. An item with
  no `link` is reported by `make_drawing` as "not placed", so nothing goes missing silently.
  Once the links are filled in, Claude stores the spec again with `kb_update_spec`
  (KNOWLEDGE-BASE.md §6.1).
- `status` is `read` or `unreadable`. **Anything Claude cannot read with confidence is
  flagged `unreadable`, never guessed.**
- `id`s are stable within the spec, so the check (§6) can name each item.
- **Datums and GD&T are copied exactly**: the datum letters, which feature each datum is
  on, and every feature control frame with its characteristic, tolerance, modifier and
  datum order.
- `view` records **where the item was on the old drawing**. It is reading context only: it
  does **not** say which view the item goes in on the new drawing (§4.4).
- A dimension may also carry (added 2026-10-27 for DTN7): `"basic": true` (a theoretically
  exact value, boxed, `tolerance` null), `"reference": true` (in parentheses, `tolerance`
  null; checked against the model to the places it shows), and `"prefix"` / `"suffix"`
  text exactly as written (`"10X"`, `"4X"`, `"MAX"`, `"TYP"`). The drawing must show
  `10X [36°]`, `(6.78)`, `.033 MAX`; check_drawing reads the box, the parentheses and the
  texts back, so a lost box or bracket reads `differs`.
- A `gdt` item may carry `"projected": ".56"`: a projected tolerance zone, shown as
  `Ø.041 Ⓟ .56` in the frame and read back.
- A pure module **`drawing_spec.py`** validates this format and converts dimension values to
  mm through `units.py`. The knowledge base stores the spec as JSON and does not depend on
  this module (§8.2). Claude reaches it through the `read_drawing_spec` tool (§5).
- **A limit dimension is modelled at the middle of its limits** (`.3750/.3755` → `.37525`
  in = `9.53135` mm), calculated by `drawing_spec.py`. The STEP file then sits in the centre
  of the tolerance band, which is what a CNC programmer working from it expects. The
  drawing still shows the two limits exactly as written. A `min` or `max` dimension is
  modelled at its written value. The same applies to a `bilateral` tolerance that is not
  symmetric: the model sits at the middle of the band (`.375 +.005/-.000` → `.3775`), and
  `read_drawing_spec` says so for each such dimension so the user sees it at review.

#### 4.1.1 Basic dimensions

A **basic dimension** (the value in a box) is a theoretically exact size or location. It has
**no tolerance of its own**: the allowed variation comes entirely from a GD&T frame, such
as a position or a profile tolerance. They always come in pairs:

> 4X Ø.266 holes, located from datums B and C by basic `1.000` and `2.000`, with a
> position frame `⌖ Ø.010 Ⓜ A B C`. Each hole centre may lie anywhere inside a Ø.010
> circle (bigger when the hole is at its largest size) centred on the exact point
> 1.000 / 2.000 from B and C.

**What goes wrong without the box.** The format says `tolerance: null` means the
title-block tolerance applies. A basic dimension written as a plain `"2.000"` with
`tolerance: null` therefore becomes `2.000 ±.005` (say) from the title block, and the hole
is then controlled twice:
- **a ±.005 square zone** from the dimension, and **a Ø.010 round zone** from the frame.
  The two disagree (the square's corners reach .007 from centre, the circle .005), so the
  drawing contradicts itself. ASME Y14.5 does not allow it.
- **The inspector cannot tell which one wins.** Some shops will inspect to the looser one,
  some to the tighter. Parts that pass at one shop fail at another, and a part the
  customer would reject can be passed.
- **The MMC bonus disappears in practice**: Ⓜ lets the position zone grow as the hole
  gets bigger, but the ±.005 on the location never grows, so the drawing is tighter than
  the customer intended. Good parts get scrapped.

The old drawing did not have this problem; it only appears if the box is lost while copying.
So:
- **Basic is a tolerance type** (`{"type": "basic"}`), never `null`. Reading the old
  drawing, a boxed value is recorded as basic.
- `make_drawing` draws it with SolidWorks' basic tolerance type, which draws the box and
  stops the title-block tolerance from applying.
- `read_drawing_spec` **warns** when the spec has a `position` or `profile` frame but no
  basic dimensions, or basic dimensions but no frame. Either one usually means a box was
  misread. It is a warning, not an error: an old drawing may have the box in a note
  ("UNTOLERANCED DIMENSIONS ARE BASIC"), which the user confirms.
- `check_drawing` reports a basic dimension that comes back without the box as `differs`,
  even when the number matches.

### 4.2 The spec drives the model
- The part is built from the spec's dimensions, converted to mm **by `drawing_spec.py`**.
- Where possible the part is modelled **the way the old drawing is dimensioned** (same
  datums, same reference edges). Then the model's own dimensions, inserted into the drawing,
  follow the old drawing's dimensioning scheme instead of the modelling order. This is
  modelling guidance, written into `guide.md`.

### 4.3 The spec drives the drawing

**How a spec item maps to the drawing (decided by the user, 2026-10-19):** the drawing
carries **its own dimensions, one per spec dimension**. The model's own dimensions are
not inserted (on DTN6 they were radii and distances from the origin, not the old
drawing's scheme). For each spec item Claude gives a **placement**: the view and the
model points (mm) on the edges it runs between, or the circle for a diameter (§5.2). The
tool adds that dimension in the drawing and **names its annotation after the spec id**
(`SPEC_D10`, `IAnnotation.SetName`), so the id travels inside the saved `.slddrw`; no
side file is needed. Datums, frames, finishes, callouts and notes are named the same way.

- Each dimension shown at the spec's `decimals`, in inches, with its tolerance if any.
  A value written as a fraction (`"5 15/16"`, `decimals` null) is shown as a fraction
  (64ths, reduced, no inch mark); limits (`".500/.498"`) are shown as limits, set from the
  model's measured value so the two numbers are exactly the spec's.
- **Inches on the drawing** is both: the document is set to IPS / inches / ANSI, and each
  dimension gets its own units, places and tolerance (`IDisplayDimension.SetUnits2`,
  `SetPrecision3`, `IDimensionTolerance`). Both work on 2020.
- Every thread and hole callout, finish symbol and note in the spec placed on the drawing.
- Dimensions the model's own dimensions don't cover get added as reference or driven
  dimensions in the drawing, so that nothing in the spec goes missing.
- Datum feature symbols and feature control frames placed exactly as in the spec.

### 4.4 Views: clean, not copied (decided 2026-10-19)

**What must match the old drawing:** every dimension, tolerance, datum, GD&T frame, callout,
finish and note: the *information*.
**What does not:** the *views*. Old drawings may be missing views, have bad views, or have
odd split views. The new drawing does not reproduce them; it uses **clean views chosen by
good drafting practice** that carry all of the old drawing's information.

Good drafting habits the drawing follows:
- **Fewest views that fully describe the part.** A plain turned part may need one view plus
  a diameter; a block with features on several faces needs more. No view without a reason.
- **Front view = the most descriptive view**, with the part in a natural, stable or machined
  orientation, and the other views projected from it in third-angle.
- **Section views for internal features** (bores, counterbores, internal grooves) instead of
  dimensioning hidden lines. **Detail views** for features too small to dimension clearly
  at sheet scale.
- **Dimension each feature in the view where it shows its true shape**, once only, never
  to hidden lines.
- **Datum features shown where they are clearly identifiable**, and dimensions measured
  from the same datums the old drawing uses (the dimensioning *scheme* is copied, even
  when the views are not).
- Dimensions outside the part outline where possible, grouped, not crossing each other or
  extension lines; a readable sheet scale from the template's sheet.
- **Never an isometric view.**

Because view choice depends on the part, it is **Claude's judgement**, passed to the tool
as a list of views (§5). The tool places them; the check (§6) then confirms nothing from
the spec was lost in the re-layout.

## 5. Tool changes

| Tool | Change |
|---|---|
| `make_drawing(path=None, spec=None, views=None, placements=None)` | Default template as-is, third-angle, no isometric, inches, empty title block. `views`: the list Claude chose (standard orthographic views by direction; section views by cutting plane; detail views by a feature and scale). With a `spec`: apply precision, tolerances, datums, GD&T, callouts, finishes, notes. **Without either it still works** (front/top/right layout), so existing callers keep working. `path` is optional: with a knowledge-base case open, the server names the file (`DN17.SLDDRW` in the output folder) through `case_output_path` and refuses a different path (KNOWLEDGE-BASE.md §6.2). Without an open case a path is required, as now |
| `read_drawing_spec(spec)` *(new, pure, needs no SolidWorks)* | Validates the spec (§4.1) and returns every dimension in the units the modelling tools take: mm for lengths, degrees for angles; for a limit or a one-sided tolerance, the upper, lower and middle values, and which one to model. Lists the problems found (unknown `type`, a `limit` with only one value, `decimals` that don't match the written value, items with no `link`, basic/GD&T warnings §4.1.1). This is the "drawing-spec tool" the job brief means when it says code does the conversion |
| `check_drawing(path, spec)` *(new)* | Reads the saved drawing back and compares it with the spec (§6) |

### 5.1 `views` (decided 2026-10-19)
A typed list; the first is the drawing's front view, the rest are placed third angle:

```json
[{"kind": "standard", "model_view": "front", "up": "-x"},
 {"kind": "standard", "direction": "right"},
 {"kind": "section", "from": "front", "cut": "vertical", "label": "A", "through_mm": [0, 133, 0]},
 {"kind": "detail", "from": "front", "label": "B", "center_mm": [6.34, 127.8, 0],
  "radius_mm": 3, "scale": "4:1"}]
```

- The front view shows a model view (`model_view`: front/back/top/bottom/left/right),
  optionally turned so model axis `up` points up on the sheet: a part turned about model Y
  (DTN6) lies along the sheet with `"up": "-x"`. Projected views are `top`, `bottom`,
  `left` or `right` of it; `drawing_spec.view_orientations` works out which model view and
  turn each needs.
- Sections cut through a model point, vertically or horizontally across their parent
  (`"flip": true` looks the other way). Details circle a model point at a scale.
- An **angled section** cuts through two model points (`"line_mm": [[x,y,z], [x,y,z]]`) or at
  `"angle_deg"` on its parent through `through_mm`; it is turned upright (`"up"`: a model
  axis or vector pointing up the sheet, default along the cutting line) and placed freely.
  A section or detail may be cut **from a section** (`"from": "section A"`).
- **Marks** on any view: `{"kind": "center_mark", "at_mm"}`, `{"kind": "point", "id",
  "at_mm"}`, `{"kind": "circle", "id", "center_mm", "through_mm"}` (a bolt circle),
  `{"kind": "line", "id", "from_mm", "to_mm", "extend_mm"}` (a centreline). Each point
  must be a hole or arc centre, on an axis, or on an edge of the model. Placements measure
  to them by id (§5.2).
- Each view gets an id (`front`, `right`, `section A`, `detail B`, or its own `id`),
  which placements refer to. The sheet scale is the largest standard one at which the
  views fit (`drawing_spec.layout`). An isometric view is refused.
- Without `views`: front, top, right.
- `"at_mm": [x, y]` on a view puts its centre there (paper mm from the sheet's lower left;
  `[x, null]` / `[null, y]` for a vertical / horizontal section); a collision is refused.
- `sheet`: `"A"` (default), `"B"` or `"C"`, landscape, each with SolidWorks' stock ANSI format
  (`a/b/c - landscape.slddrt`). The views use the area inside the border above the title
  block; on B and C the general notes go left of the title block.

### 5.2 `placements`
`{spec id: where}`. Every read item except general notes needs one; an item flagged
`unreadable` is refused. All points are model points in mm on an edge as the view shows
it, or on a silhouette line (the side of a cone or cylinder):

| Kind | Placement |
|---|---|
| dimension | `{"view", "measure": horizontal / vertical / distance / diameter / radius / angle, "points_mm": [two points] or [one point on the circle], "side": above / below / left / right, "offset_mm": 10}`. A circle seen edge-on gives its diameter. A point may be a mark id of the view: `["BC"]` (bolt circle diameter), `["CL_SLOT", "CL_H1"]` (angle between centrelines), `["O", [x,y,z]]` (centre to an edge), `[[x,y,z], "AX"]` (an edge to a hole axis) |
| datum, gdt | `{"view", "on": "D13"}` on a placed dimension (a feature of size; the symbol sits on its dimension line) or thread/hole, or `{"view", "point_mm"}` on a surface's edge |
| finish, thread, hole, local note | `{"view", "point_mm"}`: a leader to that edge |
| text of any leader item | optional `"side"` (above/below/left/right) with `"offset_mm"` beyond the view's outline, or `"text_mm": [dx, dy]` from the attach point (from the dimension's text for a symbol `"on"` one) |
| general note | none: they stack at the lower left |

Following the usual pattern in `CLAUDE.md`: session method → server wrapper with a precise
docstring → pure tests + integration tests → README tool table, `guide.md`, CHANGELOG,
`Docs/PROGRESS.md` (new API findings, which drawing annotation calls will produce plenty of).

## 6. The check: spec against the finished drawing

**Measure back** (rule 5): after saving, the server reads the drawing's dimensions and
annotations back from SolidWorks — not from what it meant to place — and compares them with
the spec. It returns one line per spec item: `ok`, `missing`, `differs` (with both values),
or `unreadable` (flagged at reading), plus anything on the drawing that is **not** in the spec.

As built: `check_drawing` opens the saved `.slddrw` and finds each item by its annotation
name (§4.3); a general note is also found by its text. A dimension is `ok` only when the
text it shows (rebuilt from its read-back units, places, fraction and tolerance) equals the
spec's **and** the model value behind it is the spec's nominal (inside the limits, for
limits): a fraction rounded to 64ths cannot hide a wrong model. The title block's own notes
(sheet format) and the section/detail view labels are left out of the extras.

**Limit of this check:** it compares the new drawing against *Claude's reading* of the old
one. If Claude misreads `.375` as `.315`, the model, the drawing and the check all agree
with the misreading and the check passes. Two guards:
1. The **spec is shown to the user at review** beside the old drawing, so a misreading is a
   visible line in a list, not hidden inside the model.
2. **The user's review compares the new drawing with the old drawing itself**, not with the
   spec. That comparison is the one the shop relies on.

## 7. Renaming the SolidWorks files

A `.slddrw` does not contain the part; it **points to the part file by name and path**.
This is also why the part is saved under its final name (`DN17.SLDPRT`) **before** the
drawing is made, never renamed afterwards by the server (KNOWLEDGE-BASE.md §6.2).

The user renames files to proprietary numbers **after approval**, once the knowledge base has
stored its STEP and PDF (KNOWLEDGE-BASE.md §9). Renaming `DN17.SLDPRT` in Windows Explorer
breaks the drawing. Rename through SolidWorks instead:
- **Pack and Go** (File → Pack and Go): rename both files, or add a prefix, and copy them to a
  new folder with the link intact; or
- rename the part, open the drawing, point it at the renamed part when asked, and save.

Worth one line in `guide.md` and in the `make_drawing` result.

## 8. Working alongside the knowledge base

This section is the same in both design documents.

### 8.1 Shared commit on `main`, before either branch starts
One small commit, so both branches start from the same contract:
- `errors.py`: `SolidWorksError` gains optional `code` and `params` (default `None`), so
  existing raises keep working unchanged.
- `units.py`: exact inch ↔ mm helpers (1 in = 25.4 mm), with pure tests.
- This document, `KNOWLEDGE-BASE.md` and `JOB-BRIEF.md`, with the drawing-spec format in
  §4.1 frozen. Changing the format afterwards needs agreement from both
  branches.
- `make_drawing`'s and `save_part`'s `path` becomes optional in their signatures, and the
  session methods resolve it with `case_paths.resolve(kind, path)` (new module
  `case_paths.py`), whose stub `case_output_path(kind)` returns `None` (no case open).
  The server wrappers stay plain `_call(_session.make_drawing, path)`.
  With `None` and no `path`, the tools refuse as now ("give a path"). The knowledge-base
  branch later makes the stub return the case's paths; the drawings branch never has to
  touch it again.
- Any uncommitted changes in the working tree committed or set aside first, so neither
  branch inherits them by accident.

### 8.2 Who owns what

| File / area | Drawings branch | Knowledge-base branch |
|---|---|---|
| `make_drawing` and drawing code in `session.py` | **Owns** | Never calls or edits it. At review it opens the saved SLDDRW and saves it as PDF with its own code |
| Other modelling code in `session.py` | Doesn't touch | Error codes at raise sites; context probes (fillet, shell, …); `save_part` path handling |
| `server.py` | Edits the `make_drawing` wrapper; adds `check_drawing`. Keeps the `case_paths.resolve` line at the top of `make_drawing` in `session.py` | Hooks logging into `_call()`; adds `kb_*` wrappers **in one block at the end** |
| `constants.py` | Paper size, projection values | Doesn't touch |
| `drawing_spec.py` (new) | **Owns** | Doesn't import it; stores the spec as JSON |
| `kb/` package (new) | Doesn't touch | **Owns** |
| `case_paths.py` (stub from the shared commit) | Doesn't touch | **Owns**; fills it in |
| Tests | New files (`test_drawing_spec.py`, `test_drawings.py`) | New files (`test_kb_*.py`) |
| `README.md`, `CHANGELOG.md`, `guide.md` | Own sections only | Own sections only |
| Version numbers | **Neither branch bumps the version** | — |

- CHANGELOG entries go under `## [Unreleased]`, in a subsection per branch.
- The README tool count is fixed by whichever branch merges second.

### 8.3 Merge order
1. **The knowledge base's first part is already on `main`** (K0, K1 and K3, merged
   2026-10-19 as PR #2). It does not depend on the drawings work.
2. **Drawings merges next.** It is self-contained, and the rest of the knowledge-base
   workflow relies on `make_drawing` with views and a spec, and on `check_drawing`.
3. The knowledge-base branch merges `main` in again, then one small follow-up commit (K6) makes
   finalise store `check_drawing`'s result for the saved `DN17.SLDDRW` against the stored
   spec, and review compare that read-back before and after your fixes. Until then,
   finalise and review record the part's measurements only.

### 8.4 Testing: one SolidWorks, one Mac
- **The drawings branch is almost entirely COM work** and cannot be verified on the Mac;
  it must be built and tested on the Windows machine with SolidWorks.
- **The knowledge-base branch is mostly pure code** (storage, search, normalisation, cards)
  and can be built and unit-tested anywhere. Only the server-chosen saves (K2), the probes
  (K4) and the review exports (K5) need SolidWorks.
- There is **one SolidWorks instance**. Two agents running integration tests at the same
  time would fight over the active document. **Never run both branches' integration tests
  at once.**
- Both branches depend on the existing tools working on **SOLIDWORKS 2020**
  ([PROGRESS.md](PROGRESS.md)). `save_part`, `export` (STEP) and `make_drawing` have not
  been verified on 2020 yet; Phase 1 of that plan should run before K2 and D1.

## 9. Decisions (2026-10-19)
1. **Template:** the user's default drawing template, exactly what SolidWorks' *New →
   Drawing* gives, with its title block. No paper size is forced.
2. **Views:** chosen by good drafting practice, including section and detail views where
   they make the part clearer. They do not copy the old drawing's views; the information
   does (§4.4).

3. **Sheet:** always A size landscape with the stock ANSI format (the 2020 default
   template has none).
4. **Spec to drawing:** the drawing's own dimension per spec dimension, placed by Claude,
   named after the spec id (§4.3).
5. **Inches:** document in IPS/ANSI, plus per-dimension units, places and tolerance (§4.3).

## 10. Build order

| Step | Content |
|---|---|
| D0 | Shared commit on `main` (§8.1) |
| D1 | Layout: default template as-is, third-angle, no isometric, inches, empty title block |
| D2 | `drawing_spec.py` and the `read_drawing_spec` tool: validate the format, dimension and tolerance types, inch → mm conversion, middle-of-band values, basic/GD&T warnings (pure, testable anywhere) |
| D3 | `views`: standard views by direction, then section views, then detail views |
| D4 | Per-dimension precision and tolerances from the spec |
| D5 | Datums and GD&T frames; thread and hole callouts; finish symbols; notes |
| D6 | `check_drawing`: read the drawing back and compare with the spec |
| D7 | `guide.md`: drafting habits (§4.4), model the way the old drawing is dimensioned, Pack and Go note |

## 11. As built (2026-10-19)

All of D1–D7 are on `feat/shop-drawings`. Verified on SOLIDWORKS 2020 SP4 (28.4) through
the file-queue bridge, with the DTN6 part (on a copy) and an exact-inch test box:

- fractions (`5 15/16`, `5/8`, `25/32`, `5/16`), limits (`.378/.375`, `.500/.498`),
  symmetric tolerance (`1.500 ±.005`), a whole number (`1`), an angle (`45°`): placed and
  read back `ok`; a wrong spec value reads back `differs` with both values;
- datum A on the Ø.500/.498 dimension, a datum on an edge; feature control frames
  (concentricity `.002 TIR | A` on a thread callout, position `Ø.010 MMC | A` on a hole
  diameter); finish symbols on an edge and on a silhouette; a thread callout with its
  length; leader notes and general notes;
- the front view turned along the sheet, a projected right view, section A-A, detail B 4:1;
- `.slddrw` and `.pdf` output; the part is never left changed.

Not verified on 2020: the `radius` and `distance` measures, horizontal section cuts,
sections or details cut from a section or detail view, and hole callouts (placed as notes
with a leader, not SolidWorks' own hole callout). The integration tests in
`tests/test_drawing.py` were run as the same calls through the bridge, not under pytest
(pytest against SolidWorks is not run from the Claude app on this machine).
Layout is placement-driven: dimension text goes where `side` and `offset_mm` put it, and
crowded areas (DTN6's 45° bevel text over the part) need the agent to choose them.
