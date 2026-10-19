# SolidWorks MCP — Conversion to SOLIDWORKS 2020

**Goal:** make this server work on **SOLIDWORKS 2020** (revision 28.x, `sldworks.tlb` typelib **v28**).

**Starting point:** upstream `hjbaard/SolidWorks-MCP` v0.16.0 (commit `9ea3c86`). It was developed and tested only on
SOLIDWORKS 2026 (3DEXPERIENCE R2026x, typelib v34) and is treated here as the 2023–2026 codebase. The upstream README
*claims* every API call it uses exists since 2020 SP2, but nobody has tested that.

The previous progress log (milestones M0–M6, and the "Key API findings" observed on 2026) was cleared from this file.
It is still in git: `git show 9ea3c86:Docs/PROGRESS.md`.

## Why this is mostly verification, not a rewrite

- COM interfaces are **append-only**. SolidWorks never changes an existing method's signature; it adds numbered
  variants (`FeatureCut4`, `AddMate5`, `HoleWizard5`). So a call is either present in typelib 28 with the
  same signature, or missing. Missing calls fail at run time with `AttributeError` / `DISP_E_MEMBERNOTFOUND`.
- `binding.py` already loads the wrappers for whichever release is running (major version taken from
  `RevisionNumber`), and `selftest.release_year` already maps 28 → 2020. Nothing there is pinned to v34.
- The real risks are: (1) members newer than 2020, (2) behaviour that differs between releases (most workarounds
  were found by trial on 2026), (3) data that ships with SolidWorks (Hole Wizard tables, thread profiles,
  materials, templates), and (4) docs, metadata and tests that say "2026".

## Prerequisites

- [ ] A Windows machine with **SOLIDWORKS 2020** installed, licensed and running. Record the service pack (SP0–SP5);
      the upstream claim is "since 2020 SP2", so aim for **SP5**.
- [ ] Python 3.11+, `pip install -e .[dev]` in a `.venv` (pywin32 ≥306, mcp 1.26–1.x).
- [ ] Note the 2020 install paths (normally `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\`, with data under
      `C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2020\`). The R2026x path in the code does not exist there.

## Phase 1 — Baseline on SOLIDWORKS 2020

- [ ] `scripts/probe_connection.py`: connection works, revision reads `28.x`.
- [ ] Confirm `gencache.EnsureModule(..., 28, 0)` generates wrappers (delete the `gen_py` cache first).
- [ ] `python -m solidworks_mcp.server --selftest` (or `solidworks-mcp --selftest`): save the full output here.
- [ ] `pytest -m "not solidworks"`: the pure layer should pass unchanged (it has no SolidWorks dependency).
- [ ] `pytest` (full): save the pass/fail list here as the baseline to work down.

## Phase 2 — API audit against typelib 28 (do this before fixing anything)

- [ ] Write `scripts/api_audit.py`: collect every COM member `session.py` / `selftest.py` / `binding.py` use
      (`obj.Member` on wrapped objects), then check each one exists on the matching interface class in the
      generated v28 module (`binding.module()`). Print the members that are missing. Keep it in the repo so it can be
      rerun for other releases.
- [ ] Also check every value in `constants.py` against the 2020 `swconst.tlb` (117 constants; enums are append-only, so
      they should match, but a missing name means the feature is newer than 2020).
- [ ] For each missing member: find the older equivalent in the 2020 API help (usually the next-lower number,
      e.g. `…3` → `…2`), or gate the tool with a clear `SolidWorksError("needs SOLIDWORKS 20xx or later")`,
      following the existing pattern for threads (`session.py` ~2143).

Interfaces in use (all must exist in v28): IModelDoc2, IModelDocExtension, IPartDoc, IAssemblyDoc, IDrawingDoc,
IFeatureManager, IFeature, ISketchManager, ISketch, ISketchSegment/Line/Arc/Spline/Point/Text, ISketchRelationManager,
ISketchRelation, ISelectionMgr, ISelectData, IBody2, IFace2, IEdge, IVertex, ILoop2, ICurve, ISurface, IEntity,
IDimension, IDisplayDimension, IAnnotation, IEquationMgr, IMassProperty, IMeasure, IMathUtility, IMathTransform,
IMathPoint, IModelView, IView, ISheet, IRefPlane, IComponent2, IMate2, IMateEntity2, IInterferenceDetectionMgr,
IInterference, ITextFormat, IThreadFeatureData, IVariableFilletFeatureData2, IWizardHoleFeatureData2.

Members to check first (newest-looking numbered variants, or features added in later releases):

| Area | Members | Notes |
|---|---|---|
| Features | `FeatureExtrusion3`, `FeatureCut4`, `FeatureRevolve2`, `FeatureFillet3`, `InsertProtrusionSwept4`, `InsertProtrusionBlend`, `InsertMirrorFeature2`, `InsertCombineFeature`, `InsertRefPlane`, `InsertAxis2`, `InsertRib`, `SketchOffsetEntities2`, `ReorderFeature` | Extrusion3/Cut4 have 20+ args; signatures must match 2020's |
| Holes / threads | `HoleWizard5`, `CreateDefinition(SW_FM_SWEEP_THREAD)`, `InitializeThreadData`, `IThreadFeatureData` | Thread feature needs 2016+ (already gated) |
| Fillets | `CreateDefinition` → `IVariableFilletFeatureData2`, `GetFilletEdgeAtIndex`, `FilletEdgeCount` | Variable fillets / full round |
| Sketch | `CreateSpline2`, `CreateSketchSlot`, `CreateArc`, `CreateCenterLine`, `InsertSketchText`, `Insert3DSketch`, `GetSketchPoints2`, `GetConstrainedStatus` | |
| Selection | `SelectByRay`, `SelectByID2`, `Select4`, `GetSelectedObject6`, `GetSelectionPoint2`, `GetPersistReference3`, `GetObjectByPersistReference3` | |
| Geometry | `GetRayIntersectionsPoints`, `RayIntersections`, `GetTessTriangles`, `GetTessNorms`, `GetTwoAdjacentFaces2`, `GetLength3`, `GetClosestPointOn`, `GetExtremePoint`, `PreSplitBody`/`PostSplitBody` | Printability, split, distances |
| Equations | `GetEquationMgr`, `AutomaticSolveOrder`, `SetEquation`, `GlobalVariable` | |
| Assemblies | `AddComponent5`, `AddMate5`, `EditAssembly`, `IsEditingSelf`, `FixComponent`, `InterferenceDetectionManager`, `IncludeMultibodyPartInterferences`, `TreatSubAssembliesAsComponents`, `GetMateEntityCount` | |
| Appearance / material | `SetMaterialPropertyValues2`, `GetMaterialPropertyValues2`, `MaterialPropertyValues`, `SetMaterialPropertyName2` | |
| Views / output | `Orientation3`, `ViewZoomTo2`, `ShowNamedView2`, `CreateDrawViewFromModelView3`, `Create1stAngleViews2`, `InsertModelAnnotations3`, `SaveAs3`, `LoadFile4`, `OpenDoc6`, `ActivateDoc3` | |
| UI control | `CommandInProgress`, `EnableGraphicsUpdate`, `EnableFeatureTree`, `EnableFeatureTreeWindow`, `DisplayWhenAdded` | Used by `_quiet_ui` on every call |

## Phase 3 — Behaviour that was found by trial on 2026 (re-verify each on 2020)

Each item below is a workaround or hard-coded choice in the code. Rerun its integration test on 2020 and keep, change or
remove the workaround.

- [ ] **`_quiet_ui` + CommandInProgress** (`session.py` ~3889): added for a 2026 SP4.0 crash. Check it causes no harm on
      2020 (views redraw afterwards, feature tree comes back).
- [ ] **Hole Wizard value vectors** (`_wizard_values`, ~2198): "found by trial on SOLIDWORKS 2026". Re-verify
      clearance / counterbore / countersink / tapped, through and blind, against volumes.
- [ ] **Hole Wizard ISO tables and thread profiles**: 2020 ships its own tables (`Metric Tap` `.sldlfp`, the
      hole-wizard database). Re-check the sizes and the expected volumes in `test_features.py` and in
      `selftest._check_hole_wizard` / `_check_thread`.
- [ ] **Default plane detection** (finds Front/Top/Right by geometry, the 0.16.0 fix): confirm with the 2020 default
      `Part.prtdot`.
- [ ] **STEP / Parasolid import with 3D Interconnect switched off** (`_import_document`, ~423): 3D Interconnect exists since
      2017, but the import behaviour (parts in `%TEMP%\spiop`, `LoadFile4` returning an open doc) must be re-verified.
- [ ] **STL / 3MF export toggles** (`SW_TOGGLE_STL_DONT_TRANSLATE` 71, `SW_TOGGLE_STL_ONE_FILE` 72, quality prefs): confirm
      the IDs and the no-translate behaviour; check 3MF export is available in 2020.
- [ ] **Screenshots**: `SaveAs3` to png/jpg/tif; `ViewZoomTo2` screen-axis behaviour; view rotations
      (`test_a_view_from_a_direction_matches_solidworks_own`).
- [ ] **Drawings** (`make_drawing`, ~5517): 2020 drawing template path and A4 sheet; first-angle views; model annotations.
- [ ] **`IDimension.SystemValue` silently ignoring writes** (write-again retry in `_write_dimension`): check if 2020 does
      the same.
- [ ] **Material database loading** (temporary `.sldmat` folder added to `swFileLocationsMaterialDatabases`).
- [ ] **Colours** (`SetMaterialPropertyValues2` needing a VT_ARRAY|VT_R8 VARIANT).
- [ ] **Mates**: `AddMate5` status codes, angle mates beyond 180°, `EditPart2` on unsaved assemblies, sub-assembly
      dimension names (`D1@Angle1@Leg-1`).
- [ ] **Equations**: `AutomaticSolveOrder` default on a new part.
- [ ] **Fillets**: tangent propagation flag, refused-edge reporting, variable radii.
- [ ] **Text on face**: `InsertSketchText`, installed-font check.
- [ ] **Feature error codes** (`constants.FEATURE_ERRORS`): confirm the codes 2020 reports.
- [ ] Go through the old "Key API findings" (`git show 9ea3c86:Docs/PROGRESS.md`) and mark each one as same or
      different on 2020.

## Phase 4 — Fix what Phases 1–3 found

- [ ] Replace each missing member with its 2020 equivalent, or gate the tool with a clear error.
- [ ] Optional: a version guard in `binding.connect` / `get_status` that names the release, plus a per-tool
      "needs SOLIDWORKS 20xx" message for features 2020 cannot do.
- [ ] Update test expectations that depend on SolidWorks data (thread and hole-wizard volumes); keep hand
      calculations as the source of truth.
- [ ] Full `pytest` green on 2020; `--selftest` all ok.

## Phase 5 — Docs, metadata, identity

- [ ] `README.md`: "Tested on SOLIDWORKS 2026" → 2020 (SP level); update compatibility, troubleshooting and the
      install path note; upstream links (`hjbaard/SolidWorks-MCP`) → this fork.
- [ ] `constants.py` docstring (says "SOLIDWORKS 2026, typelib v34"), `selftest.py` / `session.py` comments that cite
      2026 (~173, ~2201, ~3892).
- [ ] `scripts/introspect_api.py`: hard-coded `SW_DIR` is the R2026x path (the docstring also says "typelib v22",
      which is wrong) → 2020 path or auto-detect it from the registry.
- [ ] Packaging, if this fork is published: `pyproject.toml` name/URLs/author, `server.json` name
      (`io.github.hjbaard/...` belongs to upstream), `glama.json`, the README `mcp-name:` marker (`test_packaging.py`
      checks these agree), and `.github/workflows/publish.yml` (needs its own PyPI trusted publisher). The PyPI name
      `solidworks-mcp` is taken by upstream.
- [ ] Issue templates (`.github/ISSUE_TEMPLATE/*`): mention 2020.
- [ ] `CHANGELOG.md`: start a fork section describing the 2020 conversion.

## User-facing notes to document

- Files are saved with `swSaveAsCurrentVersion`, i.e. 2020 format. SolidWorks is **not backward compatible**: parts
  made in 2021+ cannot be opened in 2020. Use STEP / Parasolid (`open_part` imports them) to bring them in.
- The test suite builds its own parts, so it needs no sample files from a newer release.

## Log

_(Add dated entries here as the phases are worked through.)_
