---
dn: DN17
status: approved
part_type: bracket
size_mm: 76.2 x 50.8 x 38.1
ingested: 2026-10-12
finalised: 2026-10-12
reviewed: 2026-10-14
---
# DN17 · bracket · approved

## Description
L-shaped bracket from 1/2" plate: base 3.00 × 2.00, upright 1.50 tall, inside bend R.12.
Two Ø.266 counterbored holes in the base (cbore Ø.44 × .25 deep), one Ø.375 through hole
in the upright. Break all edges .02. Material 6061-T6.

## Drawing
input.pdf page 1 · front [0.08,0.12,0.46,0.55] · top [0.08,0.58,0.46,0.92] · side [0.52,0.12,0.80,0.55]
Spec: spec.json · 14 dimensions, 2 hole callouts, 3 notes · handwritten DN: DN17 ✓

## Questions asked
- Upright hole: Ø.375 or Ø.315 (faded)? → **Ø.375** (you, 2026-10-12)
- Bend radius tolerance not shown → **use title-block ±.01** (you)

## How it was modelled
1. add_extruded_profile: L outline on Front, 50.8 deep
2. add_fillet: inside bend R3.05
3. add_hole_wizard: 2× cbore Ø.266, from the base top face ('+y:outer')
4. add_hole_on_face: Ø.375 through, upright
5. add_chamfer: .02 on all outer edges

## Result
Volume 79,412 mm³ · mass 0.214 kg (6061-T6) · bbox 76.2 × 50.8 × 38.1 mm
Files: DN17.SLDPRT, DN17.SLDDRW · check_drawing: 19/19 items match

## Review
**Approved** 2026-10-14. Your note: "Moved the cbore callout to the top view."
Measured changes: part unchanged · drawing: 1 note moved, no values changed.

## Lessons
- Fillet the inside bend before adding holes near it; after, the fillet edge is split.
- Hole Wizard cbore from the face, not a sketch point: one callout per hole size.
- Two holes in the base share one callout ("2× Ø.266"); keep it that way on the drawing.
