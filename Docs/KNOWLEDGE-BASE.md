# Knowledge Base — Design

> Status: **design agreed, not built.** Nothing in this document exists in code yet.
> It records the decisions made before implementation so a future session can build it
> without re-deriving them. Branch: `feat/knowledge-base`.
> Companion design: [SHOP-DRAWINGS.md](SHOP-DRAWINGS.md) (branch `feat/shop-drawings`),
> built at the same time by another agent. Read §15 (working alongside the drawings branch)
> before starting.

## 1. Purpose

Every job follows the same path:

```
old drawing (phone scan, PDF)  ──>  Claude models it  ──>  SLDPRT + SLDDRW for review
        DN17.pdf                    (MCP tools)            STEP + PDF into the knowledge base
```

The knowledge base gives every new session a memory of earlier jobs:

- **"Have we done one like this before?"**: find earlier cases whose drawing looks similar
  or whose description matches, and see how they were modelled and what went wrong.
- **"What do we know about this tool?"**: one short, maintained card per MCP tool: when to
  use it, when not to, how to call it, and the failures seen in practice with their cause.

It runs on **one Windows machine**, next to SolidWorks and the MCP server. It is not shared
across machines or users. The client is **Claude Desktop**.

## 2. Two sections

### 2.1 Cases (one per drawing)

A case is one drawing taken from input to output. Its ID is the **DN number** (`DN17`).

| Part of the case | Content | Searched by |
|---|---|---|
| Input | Original PDF, rendered page, three view crops | Image vectors |
| Description | Claude's text description of the part from the drawing | Keywords + text vector |
| Timeline | Every tool call in order: arguments, result, error, measurements | Keywords (tool names, error codes) |
| Output | `DN17.step`, `DN17 drawing.pdf`, final mass/bbox | Not searched; opened on request |
| Review | Your verdict (approved / needed changes) and notes | Filters + ranking |

### 2.2 Tool cards (one per MCP tool)

One card per tool (`add_revolved_profile`, `add_fillet`, …), never several scattered entries.
**Short, simple and kept clean**, with a hard length limit (§7).

## 3. Case lifecycle

```
ingested ──> described ──> modelling ──> finalised ──> approved
                │                                  └─> changes_needed
                └─ (embedding pending, if Voyage was unreachable)
```

- **Only one case is open at a time.** Every logged tool call belongs to the open case.
- **ingested:** the PDF has been read and rendered, and the DN number checked.
- **described:** Claude has given the view boxes and description, and the vectors are stored
  (or marked pending).
- **modelling:** the timeline grows automatically with each modelling tool call.
- **finalised:** outputs written to both destinations (§6), and the tool cards tidied.
- **approved / changes_needed:** set by you after checking the SolidWorks files (§9).
  Until then the case is **unreviewed**.

## 4. Input: the DN number and the PDF

### 4.1 The DN number comes from the file name
- You save the scan as **`DN<n>.pdf`** (e.g. `DN17.pdf`). The file name is the source of truth.
- Ingest **refuses** any file whose name does not match `DN<number>.pdf`. A file is never
  quietly given a number of its own.
- **Duplicate:** refused, e.g. *"DN17 already exists (ingested 2026-10-12)"*.
- **Gap:** if the highest existing case is DN15 and DN17 arrives, ingest warns that DN16 is
  missing, but accepts it.
- **Handwritten cross-check:** if "DN17" is also written on the paper, Claude reports what it
  reads at the describe step. If that differs from the file name, the tool stops and asks you.
  The handwriting is only a check, never the source.

### 4.2 Rendering the PDF
- Phone scans: use the iPhone's document scanner (Notes or Files → *Scan Documents*), which
  corrects perspective and lighting, and save as PDF.
- The ingest tool renders the page to PNG with **`pypdfium2`** (Apache/BSD licence,
  compatible with this project's MIT licence; **not** PyMuPDF, which is AGPL).
- About **150–200 dpi**: enough to read dimensions; each view crop stays under Voyage's
  2-megapixel billing cap.
- Optional `page` argument, default 1. **If the page does not exist, ingest refuses**
  rather than using another one.
- The rendered PNG is returned to Claude as an image so it can read the drawing.

## 5. Vectors and search

### 5.1 What is embedded

| Vector | Model | Input |
|---|---|---|
| `view_front`, `view_top`, `view_side` | `voyage-multimodal-3.5` | View crops (Claude gives rough boxes; tool adds a generous margin) |
| `sheet` | `voyage-multimodal-3.5` | Whole rendered page, a fallback in case the crops are poor |
| `description` | `voyage-4` | Claude's text description |

- **Compare like with like.** A new drawing's image vectors are only compared with stored
  *input* drawings' vectors, never with output drawings or STEP files.
- **Image embeddings, not outlines.** Contour fingerprints from photographed, annotated
  old drawings are unreliable (glare, pencil marks, dimension lines crossing geometry).
  Outline vectors are kept as an idea for later, if DXF inputs ever appear.
- Every vector stores its **model name and version**. Changing models means running the
  re-embed step, which rebuilds every vector from the stored crops and text.

### 5.2 Search
- **Keyword search:** SQLite FTS5 over descriptions, tool names, error codes and review notes.
- **Vector search:** plain NumPy cosine similarity over all stored vectors. At this scale
  (thousands of cases at most, ~12 MB of vectors), no vector database is needed.
- **Merge:** **reciprocal rank fusion**: each result scores `1 / (60 + rank)` in each
  list, and the scores are added. No tuning.
- **Filters:** review status, part type, size range.
- **Ranking:** approved above unreviewed; cases that needed changes are flagged in results.
- **Result shape:** top 3–5 cases as **one-line summaries** (DN, what it is, outcome,
  key lessons). Full detail is fetched only for a case Claude chooses.

### 5.3 Voyage
- Account and API key separate from Claude. Key in the environment variable
  `VOYAGE_API_KEY` on the Windows machine, never in the knowledge-base folder or the repo.
- Cost (October 2026): images $0.60 per billion pixels (at most $0.0012 per image), text
  $0.06 per million tokens. About **$0.004 per case at most**. The free allowance
  (150 B pixels, 200 M tokens) covers roughly 25,000 cases.
- **If Voyage fails, nothing is lost:** the case is still saved, marked *embedding pending*,
  and the tool says so. A re-embed step later catches up.
- Voyage receives images of the drawings and the spec text. See §5.4.

### 5.4 Keeping the drawings private
These are proprietary customer drawings, and two outside services see them.

**Voyage** (from Voyage's FAQ, checked 2026-10-19):
- For Voyage's hosted API, customers *"can opt-out from Voyage storing and using their data
  for future model training"*. Opting out gives **zero-day retention**: the data is not
  stored after the request. The FAQ implies that without the opt-out, data may be stored and
  used for training.
- To opt out you must be an **organization Admin** and have **a payment method on file**,
  even if you stay inside the free allowance. It is done in the dashboard, under
  Organization → Terms of Service.
- The opt-out **cannot be undone in the dashboard** (re-opting in means emailing Voyage's
  legal team). That is fine here: there is no reason to opt back in.
- **Do this before the first real job.** Re-read the FAQ and terms at that point; they can
  change.

**Safety catch in the server:** the knowledge-base tools **refuse to send anything to
Voyage** until the environment variable `SWMCP_VOYAGE_OPTED_OUT=1` is set. You set it once,
after opting out. Without it, ingest still saves the case and marks it *embedding pending*
(§5.3), so nothing is lost; `kb_reembed_pending` catches up once the variable is set.
This makes forgetting the opt-out a loud refusal instead of a silent leak.

**Claude** also reads every drawing. Check the data setting of the Claude account used in
Claude Desktop (on personal plans: Settings → Privacy, whether chats may be used to improve
the models), so it matches what Voyage is set to.

**Contracts:** some contracts (e.g. export-controlled or defence work) forbid sending
drawings to any outside service, opt-out or not. No setting fixes that; such a drawing
must not go through this workflow. Checking this is up to you, per contract (§13).

## 6. Output: two destinations

| Destination | Files | For |
|---|---|---|
| **Output folder** (`SWMCP_OUTPUT_DIR`, e.g. `C:\SW-Output\`) | `DN17.SLDPRT`, `DN17.SLDDRW` | **You**: review, move out, give proprietary numbers; then **the shop floor machines from the drawing** (§6.1) |
| **Knowledge base** (`cases\DN17\`) | `DN17.step`, `DN17 drawing.pdf`, the original scan, crops | **Future sessions** |

- **The SLDPRT is never stored in the knowledge base.** It carries the full feature tree,
  which later sessions must not have. Apart from the review pickup (§9), the
  knowledge-base tools **never read the output
  folder**, so moving files out of it breaks nothing.
- **The server names every file** from the open case's DN number. Claude never types a file
  name, so a mix-up like `DN71` cannot happen. The finalise result states the names used.
- Order at finalise: save the part as SLDPRT first (the drawing refers to the saved part),
  then the SLDDRW, then export STEP and the drawing PDF for the knowledge base from the
  same model at the same moment, so they match what you review.
  The existing tools already support this: `save_part`, `export` (STEP), `make_drawing`
  (`DRAWING_FORMATS = {"pdf", "slddrw"}`).

### 6.1 Shop drawings: designed separately
How the drawing itself is made (third-angle, inches, ANSI A, per-dimension precision,
callouts, the spec-vs-drawing check, renaming with Pack and Go) is in
**[SHOP-DRAWINGS.md](SHOP-DRAWINGS.md)**, built on its own branch. What the knowledge base
needs to know about it:

- **The drawing is a shop-floor document**: parts are machined from it after review.
- **The drawing spec** (format in SHOP-DRAWINGS.md §4.1) is written by Claude at the
  describe step and **stored in the case as JSON**. The knowledge base does not import
  `drawing_spec.py`; it stores and returns the spec as-is. The spec is also searchable
  (keywords over its callouts and notes, e.g. a thread size or a finish).
- **At finalise** the knowledge base calls `make_drawing`, and, once the drawings branch has
  merged, passes it the stored spec and stores `check_drawing`'s result in the case (§15.3).
- **Approval is a release gate.** A case stays *unreviewed* until you approve it, and
  search never presents an unreviewed or *changes_needed* drawing as a good reference.
- **The stored drawing must be the one that was machined from.** If you correct the drawing
  after review, the knowledge base picks up the corrected version at review (§9).
- At review you are shown the stored spec beside the old drawing, so a misreading by Claude
  is visible as a line in a list (SHOP-DRAWINGS.md §6).

## 7. Tool cards

### 7.1 Two layers
1. **Raw event log** (append-only, automatic): every call, its arguments, result, error
   signature and measurements (§8). It is never shown to the agent directly.
2. **One card per tool**, built from the log.

### 7.2 Card format (fixed, capped)

```
add_fillet                                   (server 0.16.0, updated 2026-10-18)
Use when:      <one line>
Don't use when:<one line>
How to call:   <one line; signature comes from the docstring>
Known failures:
  - <cause → fix>                                    (seen 4×, last DN17)
  - <cause → fix>                                    (seen 1×, last DN9)
```

- **Hard cap: 8 bullets or about 150 words.** The server **refuses** a card over the cap,
  so it stays short whatever the agent does.
- The *How to call* line is generated from the tool's docstring in `server.py`, never
  maintained by hand, so it cannot drift from the code.
- Every failure line carries the **server version** it was last seen on. If a later release
  fixes the cause, the line is dropped rather than steering agents away from a working tool.

### 7.3 Keeping cards clean
- **Deduplication is pure code:** events are grouped by error signature (§8.1), so six
  Revolve failures with the same root become one bullet with a count.
- **Rewording needs an LLM**, and the only one is Claude in Desktop (the server cannot call
  Claude). So **tidying is part of finalise**: the server hands Claude each used tool's
  current card plus that case's new events; Claude returns the rewritten card; the server
  checks the cap and stores it.

### 7.4 Cards reach the agent automatically
When a modelling tool returns an error, the response includes the matching line from that
tool's card (`kb_hint`), if one exists. The agent does not have to remember to look.

## 8. Errors and why they happened

### 8.1 Error signatures (pure code)
- **The server's own errors:** `SolidWorksError` gains a stable **`code`**
  (e.g. `FILLET_TOO_LARGE`) and structured **`params`**, set at each place it is raised in
  `session.py`. The signature is the code. Mechanical change; no guessing.
- **Raw COM errors and anything else:** a regex pass replaces feature names
  (`Sketch3` → `<SKETCH>`, `Boss-Extrude2` → `<FEATURE>`), numbers, paths and HRESULTs
  with placeholders, then hashes the result. Deterministic and unit-tested.
- Both the raw text and the signature are stored.

### 8.2 Context probes (the "because")
A fillet that fails at 5 mm may only fail because the wall is 2 mm. The cause must be
recorded **without the agent writing it**. When a tool fails, the server runs that tool's
**probe** and stores the measurements beside the error:

| Tool family | Probe records |
|---|---|
| `add_fillet`, `add_full_round`, `add_chamfer` | Requested size, edge length, widths of the two adjacent faces, local wall thickness, largest size that fits (already found by commit `766bfda` for fillets) |
| `add_shell` | Requested thickness, part's thinnest extent |
| `add_revolved_profile` | Whether the profile crosses or touches the axis |
| `cut_*`, extrude cuts | Requested depth vs the body's extent in that direction |
| Holes / Hole Wizard | Hole size vs face size and distance to the face edges |

- Code also computes simple ratios (e.g. radius ÷ wall = 2.5). When the card is tidied,
  Claude can generalise to *"fails when radius > wall"*. That is interpretation of measured
  data, not memory.
- A probe is written once per tool family. Tools without a probe still log the error and
  the part's bounding box and volume.

## 9. Your review

You check and **fix the files in the output folder** (`DN17.SLDPRT`, `DN17.SLDDRW`), then
tell Claude, for example:
- *"DN17 approved"*, or
- *"DN17 needed changes: the bore was Ø.750, not Ø.875"*.

**Review before you move the files out.** `kb_review_case` reads them from the output folder
one last time:
1. If either file changed since finalise (content hash), the server opens your fixed
   versions in SolidWorks and **re-exports `DN17.step` and `DN17 drawing.pdf` into the
   case**. The finalise copies are kept as `pre-review\` in the case folder.
2. It **works out what you changed, by code**: the part's volume and bounding box before and
   after, and every drawing dimension, tolerance and note that was added, removed or
   altered. The changes are stored in the case next to your note, so a correction is
   recorded even if your note is brief.
3. If the files are no longer in the output folder, it **refuses** and says so, rather than
   approving the unfixed finalise copies.
4. The SLDPRT is still **never copied** into the knowledge base (§6).

This:
- sets the case **outcome**: approved cases rank first; cases that needed changes are flagged;
- turns each correction into a **lesson on the card** of the tool at fault (e.g. *"check
  the bore diameter against the section view"*), at the next card tidy;
- means future sessions only ever see the **fixed** STEP and drawing, the ones the shop
  machined from.

Your review is the most reliable signal the knowledge base gets.

## 10. Logging (automatic, at one choke point)

- Every modelling call goes through `_call()` in `server.py` (which knows the tool name,
  arguments and result) and `run_guarded()` in `session.py` (which knows about errors and
  rollbacks). The event is written there, so the timeline is complete and accurate.
- **The agent adds only judgement:** the description at ingest, and the card rewording at
  finalise.
- Knowledge-base tools themselves are not logged as modelling events.
- With no open case, calls are not logged to any case (still counted for tool cards).
- **A logging failure never breaks a modelling call**, but it is reported as a warning in
  that call's result, not swallowed (fail loud, rule 4).

## 11. New MCP tools

All typed and fixed, consistent with rule 8 (no arbitrary-code tool).

| Tool | Does |
|---|---|
| `kb_ingest_drawing(path, page=1)` | Check DN number, render the page, open the case, return the image |
| `kb_describe_case(view_boxes, description, drawing_spec, handwritten_dn=None)` | Crop, embed, store the description and the drawing spec (JSON, format in SHOP-DRAWINGS.md §4.1); run search; return the top similar cases |
| `kb_search(text=None, like_case=None, filters=None, limit=5)` | Hybrid search; one-line summaries |
| `kb_get_case(dn, include_timeline=False)` | Full detail of one case; with `include_timeline`, the full step list. The case's STEP file can be opened for reference |
| `kb_tool_card(tool)` | One tool's card |
| `kb_finalise_case()` | Write outputs to both destinations; return the cards to tidy |
| `kb_update_tool_card(tool, card)` | Store a tidied card (refused over the cap) |
| `kb_review_case(dn, verdict, notes=None)` | Record your review; pick up your fixed files from the output folder, re-export STEP and PDF, store what changed (§9) |
| `kb_reembed_pending()` | Embed cases that were marked pending |

Adding them follows the usual pattern in `CLAUDE.md` (session method → server wrapper →
tests → README tool count, guide, CHANGELOG).

## 12. Storage and packaging

### 12.1 Layout
```
%LOCALAPPDATA%\solidworks-mcp\kb\        (override: SWMCP_KB_DIR)
├── kb.sqlite
│     cases            dn, status, description, drawing spec (JSON), drawing check result,
│                      review, timestamps, final mass/bbox
│     events           case, seq, tool, args, result, error_code/signature, probe data, version
│     error_signatures signature, tool, count, first/last seen, last version
│     tool_cards       tool, card text, version, updated
│     vectors          case, kind (view_front/…/description), model, model_version, blob
│     fts              FTS5 index over descriptions, spec callouts and notes, tools,
│                      error codes, review notes
└── cases\DN17\
      input.pdf  page.png  views\front.png top.png side.png
      DN17.step  DN17 drawing.pdf  thumb.png
```
- SQLite in **WAL mode**, so a crash mid-write cannot corrupt the database.
- One writer (Claude Desktop runs one server process): no locking issues.
- **Backup = copy the folder.**
- FTS5 is usually included in Python's `sqlite3` on Windows. **Check this on the target
  install** before building on it.

### 12.2 Packaging
- Optional extra **`solidworks-mcp[kb]`**: `voyageai`, `pypdfium2`, `numpy`, `Pillow`.
  The core server stays light.
- Without the extra, the knowledge-base tools fail loud: *"install solidworks-mcp[kb]"*.

### 12.3 Testing
- **Pure tests** (run on any OS, including the Mac): DN-name parsing, error normalisation,
  signatures, card cap, rank fusion, storage layer, lifecycle transitions. Voyage is faked.
- **Integration tests** (Windows + SolidWorks): finalise writes all four files with the
  right names; a forced fillet failure records the probe data.
- On the Mac nothing end-to-end can run. Say so rather than claiming it was verified.

## 13. Open points

Decided 2026-10-19:
1. **Past timelines and STEP files can be shown.** Search returns summaries; `kb_get_case`
   returns the full step list and the STEP file on request.
2. **Voyage data use:** opt out before the first real job (§5.4).
3. **Corrected files:** picked up from the output folder at review (§9).
4. **Shop drawing standard:** see SHOP-DRAWINGS.md §3–§4.

Still open:
5. **Contract restrictions.** Whether any contract forbids sending its drawings to outside
   services at all (§5.4). This is yours to check per contract.

## 14. Build order

| Step | Content |
|---|---|
| — | Shared commit on `main` first (§15.1) |
| K0 | Storage layer, `SolidWorksError` codes at raise sites, error normalisation, automatic logging |
| K1 | `kb_ingest_drawing`, `kb_describe_case`, Voyage embedding, `kb_search`, `kb_get_case` |
| K2 | `kb_finalise_case`: the two destinations and server-side naming |
| K3 | Tool cards: generation, cap, tidy at finalise, `kb_hint` on errors |
| K4 | Context probes, one tool family at a time (fillet/chamfer first) |
| K5 | `kb_review_case`: pickup of fixed files, re-export, part before/after; ranking by outcome; lessons from corrections. The drawing before/after comparison uses `check_drawing`'s read-back, so it lands after the drawings merge (with K6) |
| K6 | After the drawings branch has merged: finalise passes the stored spec to `make_drawing` and stores `check_drawing`'s result (§15.3) |

## 15. Working alongside the drawings branch

This section is the same in both design documents.

### 15.1 Shared commit on `main`, before either branch starts
One small commit, so both branches start from the same contract:
- `errors.py`: `SolidWorksError` gains optional `code` and `params` (default `None`), so
  existing raises keep working unchanged.
- `units.py`: exact inch ↔ mm helpers (1 in = 25.4 mm), with pure tests.
- This document and `SHOP-DRAWINGS.md`, with the drawing-spec format in SHOP-DRAWINGS.md
  §4.1 frozen. Changing the format afterwards needs agreement from both branches.
- The current uncommitted changes in the working tree (`Docs/PROGRESS.md`,
  `Docs/SolidWorks-MCP-Kickoff.md`, `tests/test_logic.py`, `CLAUDE.md`) committed or set
  aside first, so neither branch inherits them by accident.

### 15.2 Who owns what

| File / area | Drawings branch | Knowledge-base branch |
|---|---|---|
| `make_drawing` and drawing code in `session.py` | **Owns** | Calls it only; never edits it |
| Other modelling code in `session.py` | Doesn't touch | Error codes at raise sites; context probes (fillet, shell, …) |
| `server.py` | Edits the `make_drawing` wrapper; adds `check_drawing` | Hooks logging into `_call()`; adds `kb_*` wrappers **in one block at the end** |
| `constants.py` | Paper size, projection values | Doesn't touch |
| `drawing_spec.py` (new) | **Owns** | Doesn't import it; stores the spec as JSON |
| `kb/` package (new) | Doesn't touch | **Owns** |
| Tests | New files (`test_drawing_spec.py`, `test_drawings.py`) | New files (`test_kb_*.py`) |
| `README.md`, `CHANGELOG.md`, `guide.md` | Own sections only | Own sections only |
| Version numbers | **Neither branch bumps the version** | — |

- CHANGELOG entries go under `## [Unreleased]`, in a subsection per branch.
- The README tool count is fixed by whichever branch merges second.

### 15.3 Merge order
1. **Drawings merges first.** It is self-contained, and the knowledge base's finalise step
   calls `make_drawing`.
2. The knowledge-base branch rebases onto it, then one small follow-up commit (K6) makes
   finalise pass the case's stored spec to `make_drawing(path, spec)` and store
   `check_drawing`'s result. Until then, finalise calls `make_drawing(path)` with the
   current signature.

### 15.4 Testing: one SolidWorks, one Mac
- **The drawings branch is almost entirely COM work** and cannot be verified on the Mac;
  it must be built and tested on the Windows machine with SolidWorks.
- **The knowledge-base branch is mostly pure code** (storage, search, normalisation, cards)
  and can be built and unit-tested anywhere. Only finalise (K2) and the probes (K4) need
  SolidWorks.
- There is **one SolidWorks instance**. Two agents running integration tests at the same
  time would fight over the active document. **Never run both branches' integration tests
  at once.**
