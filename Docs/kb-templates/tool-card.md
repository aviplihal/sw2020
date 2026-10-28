# add_fillet
server 0.16.0 · SOLIDWORKS 2020 SP5 (28.5.0) · updated 2026-10-14 · used 41×, failed 6× in 9 cases

**Use when:** rounding edges of a finished body, radius below the thinnest wall next to the edge.
**Don't use when:** the round is in a sketch outline; use corner_radii_mm on the profile instead.
**How to call:** add_fillet(radius_mm, edges=['#3', '#7']); pick edges with list_edges first.

## Known failures
- `FILLET_TOO_LARGE` radius over the adjacent wall → use the largest radius the error names, or fillet before shelling. (4×, last DN17, 28.5.0)
