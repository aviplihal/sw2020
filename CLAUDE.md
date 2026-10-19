# CLAUDE.md — SolidWorks MCP (sw2020)

Orientation notes for future sessions. Read this first; drill into the linked files only as needed.

## ⚠️ Project goal: convert to SOLIDWORKS 2020

**This fork targets SOLIDWORKS 2020** (revision 28.x, typelib v28). The code it started from was built for newer
releases (2023–2026; upstream developed and tested it only on SOLIDWORKS 2026 / R2026x, typelib v34). It must be
converted back and verified on 2020.

- The step-by-step conversion plan and checklist is **[Docs/PROGRESS.md](Docs/PROGRESS.md)**. Work through its phases and log
  results there.
- The old progress log and its "Key API findings" (all observed on 2026) are in git: `git show 9ea3c86:Docs/PROGRESS.md`.
- Everything below "What this is" describes the upstream code as it stands. Statements like "tested on 2026" are
  upstream facts, not this fork's target.

## What this is

`solidworks-mcp` (v0.16.0, MIT, Python ≥3.11): an **MCP server that drives a running SolidWorks on Windows over COM**, so an AI agent can model parametric parts and assemblies **and verify its own work**. Every modelling tool returns measured volume / mass / bounding box, so the agent can compare with a hand calculation and correct itself.

- 86 MCP tools over stdio (part modelling, editing, inspection, export, assemblies/mates/joints, mesh comparison, printability, drawings).
- Upstream: `github.com/hjbaard/SolidWorks-MCP` (author Henk Jan Baard). Published to PyPI as `solidworks-mcp` and in the MCP Registry as `io.github.hjbaard/solidworks-mcp`.
- This checkout: `origin` = `github.com/aviplihal/sw2020` (a copy of upstream at release 0.16.0, commit `9ea3c86`).
- Tested only on **SOLIDWORKS 2026 (3DEXPERIENCE R2026x)**; 2020 SP2+ should work in theory but is untested.

## Platform reality (important on this Mac)

- Real work needs **Windows + a licensed, running SolidWorks**. The server attaches with `GetActiveObject`; it never launches SolidWorks.
- `pywin32` is Windows-only. Off Windows, `server.py` swaps in `_NoSolidWorks`, so the server still starts and lists tools (MCP directories introspect it in Linux containers), but every call fails loud.
- **pytest does not run on macOS**: `tests/conftest.py` imports `session.py`, which imports `pythoncom`. On this machine you can read, edit and reason about code, but you cannot execute tests or tools. Say so instead of claiming they were verified.

## Repository map

```
sw2020/
├── CLAUDE.md                     ← this file
├── README.md                     User docs: quickstart, full tool tables, architecture, limits
├── CHANGELOG.md                  Per-release notes (SemVer, Keep-a-Changelog style)
├── LICENSE                       MIT
├── pyproject.toml                Package metadata; deps: pywin32 (win32 only), mcp>=1.26,<2
├── server.json                   MCP Registry manifest (version must match pyproject)
├── glama.json                    Glama directory listing metadata
├── .github/
│   ├── workflows/publish.yml     On GitHub release: unit tests (windows) → PyPI → MCP Registry (OIDC, no secrets)
│   └── ISSUE_TEMPLATE/           bug_report.yml, compatibility.yml (ask for --selftest output)
├── Docs/
│   ├── SolidWorks-MCP-Kickoff.md Original project brief: goals, principles, v0 tools, milestones M0–M5
│   ├── PROGRESS.md               SOLIDWORKS 2020 conversion plan + checklist + log (old dev log: git show 9ea3c86:Docs/PROGRESS.md)
│   └── images/                   README screenshots (bracket, thread, vase, pipe, loft)
├── src/solidworks_mcp/
│   ├── __init__.py               __version__
│   ├── server.py      (1.2k)     FastMCP app: INSTRUCTIONS text, solidworks://guide resource,
│   │                             86 @mcp.tool wrappers → _call() → ComWorker → session.run_guarded; main() / --selftest
│   ├── session.py     (6.7k)     SolidWorksSession: ALL SolidWorks logic (318 methods, see section map below)
│   ├── com_worker.py             One dedicated STA thread; every COM call serialised through it; 120 s call timeout
│   ├── binding.py                Early binding: makepy wrappers from installed typelib; wrap() raw dispatches
│   ├── constants.py              Enum values read from the installed swconst.tlb (trust these over web docs)
│   ├── units.py                  mm↔m, deg↔rad (API is metres/radians internally)
│   ├── errors.py                 SolidWorksError → returned to agent as {ok: false, error}
│   ├── sketch_constraints.py     Pure planner that fully defines polygon sketches (relations + dims from origin)
│   ├── free_sketch.py            Pure: chains of lines/arcs/splines, tangency, rank-based full-definition plan
│   ├── mesh_tools.py             Pure: load STL/3MF, slice to sections, compare/simplify (reverse-engineering)
│   ├── region_tools.py           Pure: bit-grid outline of a swept region (joint clearance) with margin
│   ├── selftest.py               `solidworks-mcp --selftest`: small hand-checked parts, prints a report
│   └── guide.md                  Long agent guide served as solidworks://guide (packaged as package-data)
├── tests/                        ~680 tests; see "Testing"
└── scripts/                      Standalone milestone proofs (run on Windows with SolidWorks open)
    ├── probe_connection.py       M0 connection probe
    ├── m1_block.py / m2_parametric.py   M1 build+measure, M2 parametric change
    ├── test_mcp_server.py        M3 full MCP loop over stdio, like a real agent
    ├── m4_*.py                   One per M4 feature (hole, fillet, chamfer, cone, shell, pattern, …)
    ├── m5_demo_bracket.py        M5 end-to-end 3D-print bracket, every step verified
    ├── demo_plate.py             Demo: design a mounting plate from a spec
    └── introspect_api.py         Dump installed typelib enums/signatures (rerun after a SolidWorks upgrade)
```

### `session.py` section map (line numbers approximate)

| Lines | Section |
|---|---|
| 158–470 | connection, status, document lifecycle (new/open/save/close, STEP/IGES/Parasolid import) |
| 471–2183 | geometry: planes, face/edge selection, extrude/revolve/sweep/loft, rib, holes, threads |
| 2184–2380 | Hole Wizard (SolidWorks' own ISO tables) |
| 2380–3845 | on-face sketches, text, cuts, slots, fillets (uniform/variable/full round), chamfer, shell, patterns, planes, free sketches |
| 3846–3970 | **`run_guarded`**: reattach after SolidWorks restart, quiet UI, CommandInProgress, rollback on failure |
| 3971–4350 | mirror, `set_dimension`, equations, dimensions list |
| 4350–4680 | a person's sketches (read/extrude/cut), history (list/delete/suppress/reorder features) |
| 4679–4875 | mesh slicing/comparison, appearance, materials, rebuild |
| 4875–5160 | measurement, faces/edges, bodies (combine/split) |
| 5160–5565 | printability, export (STL/3MF/STEP…), screenshots, drawings |
| 5565–6711 | assemblies: placement maths, components, faces in components, mates, interference, distances, check_motion, swept_region |

## How a tool call flows

```
MCP client ──stdio──> server.py @mcp.tool fn
   └─ _call(session.method, *args)            # positional forwarding (tested in test_logic.py)
        └─ ComWorker.call()                   # posts to the single STA thread, awaits with timeout
             └─ session.run_guarded(fn, ...)
                  ├─ _reattach_if_gone()      # SolidWorks restarted → reconnect
                  ├─ _history_snapshot()      # feature names before the call
                  ├─ CommandInProgress=True + _quiet_ui()   # ~100× faster; avoids SP4.0 crash
                  ├─ fn(...)                  # the actual SolidWorks work
                  └─ on error: _roll_back()   # delete features the failed call added
   └─ SolidWorksError / COM error / any Exception → {"ok": False, "error": "..."}
```

## Load-bearing design rules (do not break)

1. **Early binding is mandatory.** `GetActiveObject`'s dispatch has a broken `GetTypeInfo()`, so late binding fails (`DISP_E_MEMBERNOTFOUND`). Always `binding.wrap(obj, self._mod.IInterface)`.
2. **All COM on one thread** (`com_worker.py`). Never call session methods from another thread.
3. **Millimetres and degrees at the boundary**; convert with `units.py`. The API is metres/radians.
4. **Fail loud.** Raise `SolidWorksError` with a readable cause; never return silently wrong geometry. Check API return values, since many calls fail by returning `False`/`None`.
5. **Measure back.** Dimensions, mates and transforms are read back and verified after the rebuild; feature tools return mass properties.
6. **Every sketch fully defined.** The `part` fixture asserts no under-defined sketches remain after each test. Tools return their dimensions by role (`{"width": "width@Sketch1"}`).
7. **Language-independent selection.** Planes are found by geometry, not names like "Front Plane". Faces are picked by direction (`+z`, `-x`, `+y:inner`) or `#index`.
8. **No arbitrary-code tool.** The tool surface is fixed and typed.
9. Enum values come from `constants.py` (verified against the typelib, e.g. `swDefaultTemplatePart = 8`, not 9).
10. All user-facing text, errors and docs are **English** (an earlier Dutch phase was fully translated).

Before writing COM code, check the old "Key API findings" (`git show 9ea3c86:Docs/PROGRESS.md`; observed on 2026, re-verify on 2020). They list many non-obvious SolidWorks behaviours, such as column-major transforms, `AddComponent5` placing the bbox centre, `SystemValue` silently ignoring writes, and the VARIANT array requirement for colours.

## Adding or changing a tool (the pattern every feature commit follows)

1. `session.py`: public method returning a JSON-serialisable dict (pure helpers as `_private` methods or in a pure module).
2. `server.py`: `@mcp.tool()` async wrapper with a precise docstring (the agent reads it) → `return await _call(_session.method, args...)` in the **same order** as the signature.
3. Tests: a pure unit test in `test_logic.py` (or the module's test file); an integration test (`solidworks` marker) comparing with a hand calculation, e.g. in `test_features.py` / `test_assembly.py`.
4. Docs: the README tool table (and the tool count), `guide.md` recipe if useful, a `CHANGELOG.md` entry (Added/Fixed under the next version), and the Log in `Docs/PROGRESS.md` with any 2020 API findings.

Commit style: Conventional Commits in plain words (`feat: chamfer at any angle from a named face`, `fix: …`, `chore: release X.Y.Z`).

## Testing

```powershell
.\.venv\Scripts\python.exe -m pip install -e .[dev]
.\.venv\Scripts\python.exe -m pytest -m "not solidworks"   # pure unit layer (CI runs this on windows-latest)
.\.venv\Scripts\python.exe -m pytest                       # + integration; auto-skips if SolidWorks unreachable
```

- Pure tests: `test_logic.py` (helpers + tool-arg forwarding), `test_sketch_constraints.py`, `test_free_sketch.py`, `test_mesh_tools.py`, `test_region_tools.py`, `test_units.py`, `test_binding.py`, `test_packaging.py`, `test_server_portability.py`, `test_selftest.py`.
- Integration tests (real SolidWorks): `test_features.py` (largest), `test_assembly.py`, `test_joints.py`, `test_history.py`, `test_person_part.py`, `test_shape_tools.py`, and others.
- Fixtures (`conftest.py`): `sw` (session-wide connection), `part` (fresh part, asserts fully defined sketches), `blocks` + `assembly` (saved blocks and a fresh assembly).

## Releasing

Bump the version in **four places** (`test_packaging.py` enforces that they match): `pyproject.toml`, `server.json` (twice), `src/solidworks_mcp/__init__.py`, plus the `CHANGELOG.md` heading. Commit `chore: release X.Y.Z`, then publish a GitHub release; `publish.yml` handles PyPI and the MCP Registry. Publishing from this fork would need its own PyPI trusted-publisher setup and a different registry name.

## Known limits (upstream)

- Not supported: component patterns, in-context features, configurations, SolidWorks Simulation (FEA).
- Rebuild errors often report only code 1 (unknown), so readable causes are limited.
- SOLIDWORKS memory grows in long sessions; save and restart SolidWorks between runs.
- Repeated `--keep-open` script runs leave untitled parts open (harmless).
