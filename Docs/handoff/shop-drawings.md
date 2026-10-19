You are building the shop-drawing support for the SolidWorks MCP server (repo github.com/aviplihal/sw2020), on the Windows machine where SOLIDWORKS 2020 runs. Nothing of this branch exists yet.

## Branch
- `git fetch origin`, then create your branch from the shared base: `git switch -c feat/shop-drawings origin/shared/kb-drawings-contract`. Push it when the first commit is in.
- `shared/kb-drawings-contract` (commit `fdf5171`) is the shared base the knowledge-base branch also starts from. It already contains step **D0**:
  - `SolidWorksError(message, code=None, params=None)`
  - `units.in_to_mm` / `mm_to_in` (exact, and a string such as `".375"` is converted as written)
  - `make_drawing(path=None)` and `save_part(path=None)`, with `case_paths.resolve(...)` as the first line of each
  - the design docs
- Don't change the shared base. Work only on `feat/shop-drawings`; never commit to `main`.
- The knowledge base is built on `feat/knowledge-base`, possibly by another session on this same machine (see "Rules").

## Read first
1. `CLAUDE.md`: project rules (early binding through `binding.wrap`, all COM on one thread, mm/deg at the boundary, fail loud, measure back, enum values from `constants.py`, verified against the typelib).
2. **`Docs/SHOP-DRAWINGS.md`: your design.** The drawing standard is in §3, the spec format in §4.1 (frozen: changing it needs agreement from both branches), the tool changes in §5, the check in §6, file ownership in §8, the build order in §10.
3. `Docs/JOB-BRIEF.md` step 5: how the agent in Claude Desktop will call `make_drawing` and `check_drawing`.
4. `Docs/KNOWLEDGE-BASE.md` §6.1–§6.2: the knowledge base calls `make_drawing` with no `path` while a case is open, and stores `check_drawing`'s result.
5. `Docs/PROGRESS.md`: the SOLIDWORKS 2020 conversion plan. Phase 1 is your baseline. Log your API findings there; drawing annotation calls will produce plenty.
6. The current code:
   - `make_drawing` in `src/solidworks_mcp/session.py` (search `def make_drawing`). Today it forces `SW_DWG_PAPER_A4`, uses `Create1stAngleViews2`, adds an isometric view, inserts model dimensions with `InsertModelAnnotations3`, saves through `_write_via_saveas3` and closes the drawing.
   - Its wrapper in `server.py`.
   - `DRAWING_FORMATS` and the `SW_DWG_*` / `SW_INSERT_*` constants in `constants.py`.
   - `tests/test_drawing.py` (integration tests).

## What you own (SHOP-DRAWINGS.md §8.2)
- `make_drawing` and all the drawing code in `session.py`
- the `make_drawing` wrapper in `server.py`, and a new `check_drawing` wrapper
- the drawing values in `constants.py`
- the new pure module `drawing_spec.py`
- `tests/test_drawing_spec.py` and `tests/test_drawings.py` (or extend `test_drawing.py`)

Keep the `path = case_paths.resolve("drawing", path)` line at the top of `make_drawing`. Don't touch `kb/`, `case_paths.py`, or modelling code elsewhere in `session.py`.

## Decide first, and write the decisions into SHOP-DRAWINGS.md (ask the user where it says so)
The design leaves these open. Settle them before D3–D6, because they shape the tool's arguments:
1. **The `views` argument format.** For example: `[{"kind": "standard", "direction": "front"}, {"kind": "section", "plane": ..., "through_mm": [...]}, {"kind": "detail", "around_mm": [...], "radius_mm": ..., "scale": ...}]`. It must be typed and fixed (rule 8: no arbitrary code), with front = the most descriptive view and the others projected in third angle. Write the format into §5 and the `make_drawing` docstring.
2. **How a spec item maps to something on the drawing.** The drawing doesn't carry spec ids. Options:
   - match a model dimension to a spec `dimension` by value, after converting the spec's inches to mm through `drawing_spec.py`;
   - have Claude pass an explicit `{spec_id: "D1@Sketch1"}` map;
   - add driven or reference dimensions for spec items no model dimension covers.

   `check_drawing` needs the same mapping to say ok / missing / differs. **Ask the user** before choosing, and record the choice in §4.3 and §6.
3. **Inches on the drawing.** Decide whether this means the drawing document's unit system and decimal places (a document property), or per-dimension precision. The spec gives `decimals` per dimension, so it is probably both: the document in inches, plus per-dimension precision. Confirm what 2020 allows.

## Build order (SHOP-DRAWINGS.md §10)
- **D1, layout:**
  - use the default drawing template as-is (stop forcing `SW_DWG_PAPER_A4`; check what `NewDocument` needs for "the template's own sheet");
  - third angle (the 2020 equivalent of `Create3rdAngleViews2`);
  - no isometric view;
  - the drawing in inches;
  - the title block left empty.

  `make_drawing(path)` with no spec and no views must still work (front/top/right), and the knowledge base depends on that. Update `tests/test_drawing.py`: it asserts 4 views including the isometric one today.
- **D2, `drawing_spec.py` (pure, testable anywhere):**
  - validate the §4.1 format (values are strings as written; `decimals` per dimension; `status` is `read` / `unreadable`; ids unique);
  - convert to mm through `units.in_to_mm`;
  - list the unreadable items.

  `tests/test_drawing_spec.py` needs no SolidWorks.
- **D3, `views`:** standard views by direction, then section views, then detail views.
- **D4:** per-dimension precision and tolerances from the spec.
- **D5:** datums and GD&T frames, thread and hole callouts, surface finish symbols, notes.
- **D6, `check_drawing(path, spec)`:** read the saved drawing back from SolidWorks (dimensions with their displayed value, precision and tolerance; notes; datums; GD&T), not from what was meant to be placed. Return one line per spec item (`ok` / `missing` / `differs` with both values / `unreadable`), plus anything on the drawing that is not in the spec.
- **D7:** `guide.md`: drafting habits (§4.4), modelling the way the old drawing is dimensioned (§4.2), and the Pack and Go note (§7). Add a line about Pack and Go to the `make_drawing` result.

**API calls are unverified on 2020.** Likely candidates to look up in the SOLIDWORKS 2020 API help, and to confirm on the generated v28 wrapper module (`binding.module()`, or `scripts/introspect_api.py`):
- `IDrawingDoc.Create3rdAngleViews2`, `CreateDrawViewFromModelView3`, section and detail view creation (`CreateSectionViewAt*`, `CreateDetailViewAt*`)
- `IDisplayDimension.SetPrecision*` and the dimension tolerance interfaces
- datum tag, GD&T (`Gtol`), note and surface-finish insertion; hole callouts
- the document unit preferences through `IModelDocExtension.SetUserPreference*`

Use the highest-numbered variant that exists in typelib 28. When something is missing, gate it with a clear `SolidWorksError`. Put every new enum value in `constants.py`, read from the 2020 `swconst.tlb`.

## Rules
- **Do Phase 1 of PROGRESS.md first, once:** `scripts/probe_connection.py`, `--selftest`, `pytest -m "not solidworks"`, full `pytest`. Save the baseline in the PROGRESS.md log. If the knowledge-base session already did it, reuse its results.
- **There is one SolidWorks instance.** Never run integration tests while another session runs the knowledge-base branch's tests. Agree with the user who uses SolidWorks when.
- **Merge order:** drawings merges first (SHOP-DRAWINGS.md §8.3), so keep `main`-bound changes self-contained. Don't bump the version. CHANGELOG entries go under `## [Unreleased]` → `### Shop drawings`. Edit only your own sections of README and guide. The README tool count is fixed by whichever branch merges second.
- **Follow the pattern in `CLAUDE.md` for every tool:** session method → wrapper with a precise docstring, args forwarded in order (`tests/test_logic.py` checks this) → pure tests plus `solidworks`-marked integration tests that measure back → README / guide / CHANGELOG / PROGRESS log.
- **Commits** use Conventional Commits in plain words and end with the attribution line your environment gives.
- **The drawing is a shop-floor document:** a wrong dimension is a scrapped part. Report test results as they are. If something couldn't be verified on 2020, say so.
