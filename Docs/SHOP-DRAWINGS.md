# Shop Drawings — Design

> Status: **design agreed, not built.** Branch: `feat/shop-drawings`.
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
| Template, sheet and title block | **The user's default drawing template**, the one SolidWorks' own *New → Drawing* uses. It already carries the correct sheet and title block | `make_drawing` already reads the default template, but then forces `SW_DWG_PAPER_A4`. Stop forcing a paper size: the template's sheet is used as-is |
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
    {"id": "D7", "kind": "dimension", "value": null, "view": "right",
     "between": "slot width", "status": "unreadable"}
  ]
}
```

Rules:
- **Values are strings exactly as written** (`".375"`, `".50"`), never floats: a trailing
  zero or a leading dot is part of what the drawing says. `decimals` is explicit **per
  dimension**, because one drawing can mix `.xx` and `.xxx`.
- `tolerance` is `null` when the old drawing shows none on that dimension (the title-block
  tolerance applies, which the user handles).
- `status` is `read` or `unreadable`. **Anything Claude cannot read with confidence is
  flagged `unreadable`, never guessed.**
- `id`s are stable within the spec, so the check (§6) can name each item.
- **Datums and GD&T are copied exactly**: the datum letters, which feature each datum is
  on, and every feature control frame with its characteristic, tolerance, modifier and
  datum order.
- `view` records **where the item was on the old drawing**. It is reading context only: it
  does **not** say which view the item goes in on the new drawing (§4.4).
- A pure module **`drawing_spec.py`** validates this format and converts dimension values to
  mm through `units.py`. The knowledge base stores the spec as JSON and does not depend on
  this module (§8.2).

### 4.2 The spec drives the model
- The part is built from the spec's dimensions, converted to mm **by `drawing_spec.py`**.
- Where possible the part is modelled **the way the old drawing is dimensioned** (same
  datums, same reference edges). Then the model's own dimensions, inserted into the drawing,
  follow the old drawing's dimensioning scheme instead of the modelling order. This is
  modelling guidance, written into `guide.md`.

### 4.3 The spec drives the drawing
- Each dimension shown at the spec's `decimals`, in inches, with its tolerance if any.
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
| `make_drawing(path=None, spec=None, views=None)` | Default template as-is, third-angle, no isometric, inches, empty title block. `views`: the list Claude chose (standard orthographic views by direction; section views by cutting plane; detail views by a feature and scale). With a `spec`: apply precision, tolerances, datums, GD&T, callouts, finishes, notes. **Without either it still works** (front/top/right layout), so existing callers keep working. `path` is optional: with a knowledge-base case open, the server names the file (`DN17.SLDDRW` in the output folder) through `case_output_path` and refuses a different path (KNOWLEDGE-BASE.md §6.2). Without an open case a path is required, as now |
| `check_drawing(path, spec)` *(new)* | Reads the saved drawing back and compares it with the spec (§6) |

Following the usual pattern in `CLAUDE.md`: session method → server wrapper with a precise
docstring → pure tests + integration tests → README tool table, `guide.md`, CHANGELOG,
`Docs/PROGRESS.md` (new API findings, which drawing annotation calls will produce plenty of).

## 6. The check: spec against the finished drawing

**Measure back** (rule 5): after saving, the server reads the drawing's dimensions and
annotations back from SolidWorks — not from what it meant to place — and compares them with
the spec. It returns one line per spec item: `ok`, `missing`, `differs` (with both values),
or `unreadable` (flagged at reading), plus anything on the drawing that is **not** in the spec.

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
  server wrappers call a stub `case_output_path(kind)` (new module `case_paths.py`) that
  returns `None` (no case open).
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
| `server.py` | Edits the `make_drawing` wrapper, keeping the `case_output_path` call from the shared commit; adds `check_drawing` | Hooks logging into `_call()`; adds `kb_*` wrappers **in one block at the end**; fills in `case_output_path` |
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
1. **Drawings merges first.** It is self-contained, and the knowledge-base workflow relies
   on `make_drawing` with views and a spec, and on `check_drawing`.
2. The knowledge-base branch rebases onto it, then one small follow-up commit (K6) makes
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

## 10. Build order

| Step | Content |
|---|---|
| D0 | Shared commit on `main` (§8.1) |
| D1 | Layout: default template as-is, third-angle, no isometric, inches, empty title block |
| D2 | `drawing_spec.py`: validate the format, inch → mm conversion (pure, testable anywhere) |
| D3 | `views`: standard views by direction, then section views, then detail views |
| D4 | Per-dimension precision and tolerances from the spec |
| D5 | Datums and GD&T frames; thread and hole callouts; finish symbols; notes |
| D6 | `check_drawing`: read the drawing back and compare with the spec |
| D7 | `guide.md`: drafting habits (§4.4), model the way the old drawing is dimensioned, Pack and Go note |
