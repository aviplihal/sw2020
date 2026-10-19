# SolidWorks MCP Connector — Kickoff Brief

> Paste this into a new Claude Code session as starting context. It describes *what* we are building, *what* the architecture looks like, a concrete first set of tools, the verification loop, and the known pitfalls — so you don't have to discover the SolidWorks API from scratch.

## 1. Goal

Build an MCP server (Python) that drives a **locally running SolidWorks instance** through the COM API, so an AI agent can build, measure and export parametric models. The core of the project is not just "making geometry" but a **closed loop**: build → measure/verify → compare with requirements → correct → repeat.

### Why this is promising
Parametric CAD provides *hard, verifiable signals* (rebuild errors, mass properties, measure, interference checks). That makes an agentic correction loop realistic — unlike purely visual mesh work, where the only feedback is "it looks about right".

### Scope v0 (deliberately small)
Be able to build a single part with the most common features, and verify it yourself. Assemblies, drawings and simulation come later.

## 2. Technical stack

- **Language:** Python 3.11+
- **SolidWorks link:** `pywin32` (`win32com.client`) — COM/OLE Automation
- **MCP:** official Python MCP SDK (`mcp` package), stdio transport
- **Environment:** Windows, with SolidWorks installed and an active licence. No cloud — everything local.

### Connecting to SolidWorks
```python
import win32com.client
import pythoncom

# Grab a running instance, or start one
try:
    swApp = win32com.client.GetActiveObject("SldWorks.Application")
except pythoncom.com_error:
    swApp = win32com.client.Dispatch("SldWorks.Application")

swApp.Visible = True  # during development: see what is happening
```

> Tip: use `win32com.client.gencache.EnsureDispatch("SldWorks.Application")` for early binding and better autocompletion of constants. The `swconst` enums (e.g. `swDocPART`, `swSketchManager` options) are in the type library.

## 3. Architecture principles

1. **One COM session, single-threaded.** The SolidWorks API is stateful and not thread-safe. Serialise all calls. MCP stdio is sequential anyway — keep it that way.
2. **Defensive about state.** Selection, the active document and context determine whether an operation succeeds. Never assume the right doc/selection is active — set it explicitly in every tool.
3. **Idempotent where possible + naming.** Give features and sketches explicit names (`feature.Name = "..."`) so later tools can find them reliably instead of relying on selection order.
4. **Every mutation returns verification.** A tool that builds something also returns: rebuild status, error messages, and (where relevant) the changed mass properties. The loop lives on that feedback.
5. **Fail loud and structured.** Catch COM errors and return readable messages instead of stack traces. A failed rebuild is not a crash but a result the agent must be able to read.

## 4. First set of tools (v0)

| Tool | Purpose | Key API entry points |
|---|---|---|
| `connect` / `get_status` | Connect to/inspect SolidWorks; report the active doc | `GetActiveObject`, `swApp.ActiveDoc` |
| `new_part` | Create a new empty part document | `swApp.NewDocument` (part template path) |
| `open_part` / `save_part` | Open/save | `swApp.OpenDoc6`, `model.SaveAs3` |
| `create_sketch` | Start a sketch on a chosen plane (Front/Top/Right or a face) | `model.SketchManager`, `SelectByID2` on the plane |
| `add_extrude` | Extrude the active/named sketch | `FeatureManager.FeatureExtrusion3` |
| `add_revolve` | Revolve about an axis | `FeatureManager.FeatureRevolve2` |
| `add_fillet` | Fillet on selected edges | `FeatureManager.FeatureFillet3` |
| `set_dimension` | Change a named dimension (parametric!) | `model.Parameter("D1@Sketch1").SystemValue` |
| `set_equation` | Add/set an equation | `model.GetEquationMgr` |
| `rebuild` | Force a rebuild and read errors | `model.ForceRebuild3`, `model.GetFeatureCount` + error check |
| `get_mass_properties` | Volume, mass, centre of mass, bounding box | `model.Extension.CreateMassProperty` |
| `measure` | Distance/angle between selections | `model.Extension.CreateMeasure` |
| `check_interference` (later) | Collisions in an assembly | `InterferenceDetectionMgr` |
| `screenshot` | Render the viewport to PNG for a visual check | `model.SaveAs3` to `.png`, or `model.ActiveView` capture |
| `export` | Export to STEP/STL | `model.SaveAs3` with the right extension/options |

> Units: the SolidWorks API works internally in **metres** (and radians for angles), regardless of the document units. Convert explicitly (e.g. mm → m) and document that in every tool; this is a classic source of bugs.

## 5. The verification loop (the heart of the project)

After every build step the agent must be able to determine whether the result is correct. Build the loop around these signals:

1. **Rebuild status** — did a feature fail? Is a sketch under-defined (dangling)?
2. **Mass properties** — compare volume/mass/centre of mass with the expected spec.
3. **Measure** — verify critical dimensions, wall thicknesses and distances against the requirements.
4. **Bounding box** — does it fit within the intended envelope / print bed?
5. **Visual check** — read back a screenshot for gross errors (wrong orientation, missing feature).
6. **(Later) Interference/FEA** — assembly collisions or strength.

A typical iteration:
```
intent + requirements
  → build feature via API
  → rebuild + read errors
  → measure (mass props / measure / bbox)
  → compare with spec
  → deviates? → set_dimension / adjust feature → repeat
  → OK? → export STEP/STL
```

## 6. Known pitfalls (knowing them up front saves time)

- **COM is stateful & single-threaded.** Selections and the active doc determine success. Set them explicitly; reset the selection with `model.ClearSelection2(True)` between operations.
- **Units:** the API uses metres/radians, not the document units. A common silent error.
- **`SelectByID2` is fragile.** Selecting by name/coordinate is sensitive. Name your geometry and use the FeatureManager tree instead of spatial selection where possible.
- **Booleans on return values.** Many API calls return `False`/`None` on failure without raising an exception. Check return values actively.
- **Templates require a path.** `NewDocument`/`NewPart` needs a valid part template; get the default path via `swApp.GetUserPreferenceStringValue(swDefaultTemplatePart)`.
- **Version-dependent API names.** Many functions have numbered variants (`...3`, `...6`). Use the newest one your SolidWorks version supports.
- **Design intent ≠ valid geometry.** An AI easily makes *valid* geometry; a feature tree that *updates robustly* when dimensions change is the higher bar. Keep sketches fully defined and build relations deliberately.
- **Visual verification is not infallible.** Use screenshots as a supplement, not as the only source of truth.

## 7. Proposed milestones

- **M0 — Connection:** `connect`/`get_status` works; the agent can see a running SolidWorks and report the active doc.
- **M1 — One block:** `new_part` → `create_sketch` (rectangle) → `add_extrude` → `get_mass_properties`. The volume matches a hand calculation.
- **M2 — Parametric:** `set_dimension` changes a dimension, `rebuild`, mass properties change predictably. This proves the parametric loop.
- **M3 — Verification loop:** the agent gets a spec ("block 40×20×10 mm, Ø8 hole in the centre"), builds, measures, corrects until it is right, exports STL.
- **M4 — Expansion:** revolve, fillet, equations, multiple features; screenshot verification.
- **M5 — Later:** assemblies, interference detection, drawings, SolidWorks Simulation (FEA) in the loop.

## 8. Tips for the Claude Code session

- Set `swApp.Visible = True` so you can watch every step live in SolidWorks — indispensable when debugging the loop.
- Get M0–M1 fully working before adding tools. The skeleton (connect + build + measure) carries everything else.
- Log every COM call and return value during development; the API often fails silently.
- Keep one small test part as a reference to validate `measure`/`mass_properties` against known values.
- Consult the official **SOLIDWORKS API Help** for exact signatures; the numbered function variants differ between versions.

---
*Written as the starting document for the SolidWorks MCP project. Feel free to adjust the scope and milestones to your own priorities.*
