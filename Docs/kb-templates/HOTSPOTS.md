# Hotspots and known issues

Updated 2026-10-14 after DN18. Everything a fix session needs: where the effort goes, every open
issue with the jobs and sessions it came from, and what fixed the closed ones.
Close an issue with `solidworks-mcp kb-fixed H-00n --by "…" --commit … --note "…"`, not by hand.
Each job's full detail: fixes\DN<n>\digest.md (transcript path inside).

## Measured: last 10 jobs
| Tool | Calls/job | Min/job | Tokens/job | Share of tokens |
|---|---|---|---|---|
| kb_view_region | 13.2 | 16.8 | 31,500 | 49% |
| make_drawing + check_drawing | 5.8 | 17.0 | 9,800 | 15% |
| list_faces / list_edges | 9.1 | 7.4 | 8,700 | 14% |
| screenshot | 2.0 | 1.6 | 3,900 | 6% |
| everything else | 31.0 | 22.5 | 10,200 | 16% |

## Open (worst first: jobs × cost per job)
- [ ] **H-005** check_drawing reports hole callouts on section views as missing → extra drawing rounds
  - Cost: ~4,600 tok + 9 min per job · Seen: DN17 `efb18dde` D4 · DN16 `3c479092` D2 · DN15 `da7f8b2f` D3 · DN13 `7c01aa5e` D1
  - Fix idea (Claude, DN17): look for a hole callout on section views too.
- [ ] **H-006** add_hole_wizard picks a face silently when '+y' matches several · `HOLE_FACE_NOT_FOUND`
  - Cost: ~1,100 tok + 3 min per job · Seen: DN17 `efb18dde` D3 · DN16 `3c479092` D4 · DN12 `51d9e0b2` D1
  - Fix idea (Claude, DN17): refuse an ambiguous direction and list the candidate faces in the error.
- [ ] **H-004** list_faces returns every face when Claude needs one direction
  - Cost: ~600 tok per job · Seen: DN17 `efb18dde` D5 · DN16 `3c479092` D1 · DN14 `0e4a7f13` D2 · DN11 `9b2c4d70` D1 · DN10 `c3f81e26` D2 · DN9 `2a6d0b95` D1
  - Fix idea (Claude, DN16): a `direction` filter on list_faces.
- [ ] **H-007** guide.md has no recipe for an inside bend radius on an L-profile
  - Cost: not measured · Seen: DN17 `efb18dde` D6
  - Fix idea (Claude, DN17): a short recipe: profile with corner_radii_mm, or fillet before holes.

## Fixed
- [x] **H-002** kb_view_region: a box already read is zoomed again because the reading is lost
  - Fixed 2026-10-20 in a1b2c3d by: `kb_view_region` returns the readings Claude stored for an overlapping box.
  - Seen before the fix: DN17 `efb18dde` D1 D2 · DN16 `3c479092` D3 · +5 jobs
  - If it happens again: call kb_view_region with `note=` after reading, so the reading is kept.
- [x] **H-001** screenshot of a blank view → retries
  - Fixed 2026-10-19 in 4b311bb by: screenshots taken from a view that draws.
  - Seen before the fix: DN6 `4f0c2e81` D1 · DN7 `b81e6c3a` D2

## Parked
(none: parked when more than 20 are open, cheapest first)
