# Job Brief: Old Drawing → STEP + Clean Drawing

> Drop this into a new Claude Desktop session (or a Claude Desktop Project's instructions)
> before starting a drawing job. It describes the finished workflow. The `kb_*` tools and
> the new drawing options come from [KNOWLEDGE-BASE.md](KNOWLEDGE-BASE.md) and
> [SHOP-DRAWINGS.md](SHOP-DRAWINGS.md); until those are built, the steps that use them
> cannot run yet. Say so instead of improvising around a missing tool.

## The job

You are given an **old drawing** as a PDF named `DN<n>.pdf` (for example `DN17.pdf`). Old
drawings can be messy, faded, scanned from paper, missing views or laid out oddly.

You produce:
1. **A SolidWorks part** of exactly what the old drawing describes, saved as `DN17.SLDPRT`.
2. **A clean SolidWorks drawing** of it, saved as `DN17.SLDDRW`, with **all the information
   from the old drawing** and nothing invented.

The drawing is a **shop-floor document: parts are machined from it.** A wrong dimension is a
scrapped part. Accuracy beats speed, every time.

## Rules that never bend

1. **Nothing is invented.** Every dimension, tolerance, datum, GD&T frame, thread, hole
   callout, finish and note on the new drawing comes from the old drawing. If something is missing report back
2. **Never guess an unreadable value.** If you cannot read something with confidence, mark
   it unreadable and ask the user. Do not pick the "likely" number.
3. **Do not convert inches to millimetres in your head.** The drawing is in inches; the
   tools work in millimetres. The conversion is done by code (the drawing-spec tools).
   Copy values exactly as written: `.375` stays `.375`, `.50` stays `.50`.
4. **Precision comes from the old drawing, per dimension.** One drawing can mix `.xx` and
   `.xxx`. There is no house default; each contract differs.
5. **Never type a file name.** The tools name every output from the DN number.
6. **Verify, don't assume.** Every modelling tool returns measured volume, mass and bounding
   box. Compare them with what the drawing says before moving on.
7. **Leave the title block empty.** The user fills it in.

## Workflow

### 1. Ingest
- Call `kb_ingest_drawing` with the PDF's path (and `page` if it is not page 1).
- If it refuses (bad file name, DN already used, another case still open), stop and tell
  the user. If it warns about a gap in the DN numbers, mention it and continue.
- It returns an **overview** of the page: good for the layout, too coarse for small text.

### 2. Read the old drawing and write the drawing spec
**Read every value from a zoomed region** (`kb_view_region` with a box in page fractions,
`[x0, y0, x1, y1]` from 0 to 1), never from the overview. Work across the sheet region by
region until everything on it has been seen at full resolution.

Record **every item** in the drawing-spec format (SHOP-DRAWINGS.md §4.1):
- dimensions: value as written, decimal places, tolerance, what it measures from/to
- datums and their features; every GD&T frame (characteristic, tolerance, modifier, datum order)
- threads, holes (counterbore/countersink), finishes, notes (material, heat treat, deburr…)
- anything unreadable, flagged

If "DN17" is also written on the sheet, report what you read.
Then call `kb_describe_case` with the view boxes (page fractions), a short text description
of the part, a short part type (`bracket`, `shaft`, `flange`, …), the spec, and the
handwritten DN if any. If it refuses because the handwritten DN differs from the file name,
ask the user; only once they confirm the file name, call it again with `dn_confirmed=True`.

**Ask the user** about every unreadable item before modelling. Show the spec as a list so a
misreading is easy to spot. Put their answers into the spec and store it with
`kb_update_spec`. Always work from the latest stored spec.

### 3. Learn from earlier jobs
`kb_describe_case` returns similar earlier cases. For promising ones, call `kb_get_case`
(with `include_timeline` to see the steps that worked). Read the tool cards
(`kb_tool_card`) for the tools you plan to use. **Approved cases are trustworthy; cases that
needed changes show what to avoid.** Earlier cases are guidance, not a template: this
drawing's numbers win.

### 4. Model the part
- Build it from the spec's dimensions.
- **Model it the way the old drawing is dimensioned**: same datums, same reference edges.
  Then the model's own dimensions match the drawing's dimensioning scheme.
- After each feature, check the measured result against the drawing. If a tool fails,
  read the `kb_hint` in the error and the tool's card before retrying.
- When the part is done, call `save_part` **with no path**. With a case open the server
  saves it as `DN17.SLDPRT` in the output folder. Saving again after a fix overwrites it.

### 5. Make the drawing: clean views, same information
The views **do not** have to match the old drawing. The information does.
- Use the default drawing template (it carries the right title block). Third-angle
  projection, inches.
- **Fewest views that fully describe the part.** The front view is the most descriptive one.
- **Section views** for internal features instead of hidden lines; **detail views** for
  features too small to dimension clearly.
- Dimension each feature once, in the view where it shows true shape, never to hidden lines.
- Same datums as the old drawing, shown where they are clearly identifiable.
- Dimensions outside the outline where possible, not crossing each other.
- **Never an isometric view.**

Call `make_drawing` **with no path**, your chosen `views` and the latest `spec`; the server
saves it as `DN17.SLDDRW` next to the part. Then call `check_drawing`. Fix every item it
reports as `missing` or `differs`, and make the drawing again until it is clean. This
drawing is the one the user reviews and the shop machines from; nothing regenerates it later.

### 6. Finalise
Call `kb_finalise_case`. It checks that `DN17.SLDPRT` and `DN17.SLDDRW` are saved in the
output folder, records their measurements and closes the case. It does **not** export
anything yet. It hands you the tool cards for the tools you used, with this job's events:
rewrite each card's wording **short and clean** (counts are added by the server; it refuses
cards over the cap) and store it with `kb_update_tool_card`.

Then tell the user, in a short list:
- the file names written
- the `check_drawing` result
- anything you were unsure of or the user should look at first
- that they should check and fix the files **in place**, tell you the verdict, and only then
  move the files out

If the job has to stop before finalise (wrong file, dropped job), call `kb_abandon_case`
with the reason, after telling the user.

### 7. Review (the user, later)
The user checks and fixes the files in the output folder, then says e.g. *"DN17 approved"*
or *"DN17 needed changes: …"*. Call `kb_review_case` **before** the files are moved out. It
exports the STEP and the drawing PDF from their checked files into the knowledge base. If
the user says the files are already moved, say that the review needs them back in the output
folder. When it succeeds, tell the user the files can now be moved out.
