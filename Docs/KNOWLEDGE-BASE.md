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
old drawing (phone scan, PDF)  ──>  Claude models it  ──>  SLDPRT + SLDDRW  ──>  you approve  ──>  STEP + PDF
        DN17.pdf                    (MCP tools)            for your review                       into the knowledge base
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
| Description | Claude's text description of the part, its part type, the drawing spec | Keywords + text vector |
| Timeline | Every tool call in order: arguments, result, error, measurements | Keywords (tool names, error codes) |
| Output | `DN17.step`, `DN17 drawing.pdf`, **exported at approval** from the files you checked; final mass/bbox | Not searched; opened on request |
| Review | Your verdict (approved / needed changes), notes, and what you changed (measured) | Filters + ranking |

### 2.2 Tool cards (one per MCP tool)

One card per tool (`add_revolved_profile`, `add_fillet`, …), never several scattered entries.
**Short, simple and kept clean**, with a hard length limit (§7).

## 3. Case lifecycle

```
ingested ──> described ──> modelling ──> finalised ──> approved
   │            │              │                  └─> changes_needed
   │            │              │
   └────────────┴──────────────┴──> abandoned        (embedding pending: a flag, not a state)
```

- **Only one case is open at a time.** Every logged tool call belongs to the open case.
  The open case is stored in the database, not in memory, so it survives Claude Desktop
  restarting the server.
- **ingested:** the PDF has been read and rendered, and the DN number checked.
- **described:** Claude has given the view boxes, description, part type and drawing spec,
  and the vectors are stored (or marked pending).
- **modelling:** the timeline grows automatically with each modelling tool call. The part
  and drawing are saved under server-chosen names in the output folder (§6.2).
- **finalised:** both files exist in the output folder and are saved; their measurements are
  recorded; the tool cards are tidied. The case is closed: nothing more is logged to it.
  Nothing is exported to the knowledge base yet.
- **approved / changes_needed:** set by you after checking and fixing the SolidWorks files
  (§9). That is when the STEP and PDF are made and stored. Until then the case is
  **unreviewed**.
- **abandoned:** `kb_abandon_case` closes an open case that should not go on (wrong file,
  job dropped). It stays in the database, marked abandoned, and is never shown in search.
  Its DN number is freed: the same DN can be ingested again as a fresh case. The old
  abandoned record is kept under `DN17~1` for the log.

## 4. Input: the DN number and the PDF

### 4.1 The DN number comes from the file name
- You save the scan as **`DN<n>.pdf`** (e.g. `DN17.pdf`). The file name is the source of truth.
- Ingest **refuses** any file whose name does not match `DN<number>.pdf`. A file is never
  quietly given a number of its own.
- **Another case is open:** refused, naming the open case. Finalise or abandon it first.
- **Duplicate:** refused, e.g. *"DN17 already exists (ingested 2026-10-12)"*. An abandoned
  case does not count.
- **Gap:** if the highest existing case is DN15 and DN17 arrives, ingest warns that DN16 is
  missing, but accepts it.
- **Handwritten cross-check:** if "DN17" is also written on the paper, Claude reports what it
  reads at the describe step (`handwritten_dn`). If that differs from the file name, the tool
  refuses and Claude asks you. Once you confirm the file name is right, Claude calls it
  again with `dn_confirmed=True`. The handwriting is only a check, never the source.

### 4.2 Rendering the PDF
- Phone scans: use the iPhone's document scanner (Notes or Files → *Scan Documents*), which
  corrects perspective and lighting, and save as PDF.
- The ingest tool renders the page to PNG with **`pypdfium2`** (Apache/BSD licence,
  compatible with this project's MIT licence; **not** PyMuPDF, which is AGPL).
- About **200 dpi**, stored as `page.png`. This full-resolution render is the source for
  the view crops and for zooming in.
- Optional `page` argument, default 1. **If the page does not exist, ingest refuses**
  rather than using another one.

### 4.3 Letting Claude read small text
Claude's vision scales every image down to about 1,500 px on the long side, and Claude
Desktop limits the size of a tool result (about 1 MB). A whole sheet sent at 200 dpi would
be shrunk to roughly 70 dpi on a letter page, and less on 11×17 or larger, too coarse to
read small dimension text on a faded scan. So:

- Ingest returns an **overview**: the whole page scaled to fit about 1,500 px, as JPEG.
  Enough to see the layout and pick out the views, not to read every number.
- **`kb_view_region(box)`** returns one region of `page.png` at full resolution (also
  scaled to fit, as JPEG, so a small box is effectively a zoom). Claude reads every
  dimension, tolerance and note from zoomed regions, never from the overview.
- **Boxes are in page fractions**, `[x0, y0, x1, y1]` from 0 to 1, top-left origin. They
  mean the same thing whatever size the image Claude saw was scaled to. The same format is
  used for the view boxes at describe.
- A region whose image would exceed the result-size limit is scaled down further, and the
  result says so.

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
- Images sent to Voyage are scaled to at most 2 megapixels first, so each one costs a
  known amount.

### 5.2 Search
- **Keyword search:** SQLite FTS5 over descriptions, part type, spec callouts and notes, tool
  names, error codes and review notes.
- **Vector search:** plain NumPy cosine similarity over all stored vectors. At this scale
  (thousands of cases at most, ~12 MB of vectors), no vector database is needed.
- **Merge:** **reciprocal rank fusion**: each result scores `1 / (60 + rank)` in each
  list, and the scores are added. No tuning.
- **Filters:** review status, part type, size range (largest bounding-box side).
- **Part type** is a short free-text label Claude gives at describe (e.g. `bracket`,
  `shaft`, `flange`, `plate`). Search offers the labels already in use, so the vocabulary
  stays small.
- **Ranking:** approved above unreviewed; cases that needed changes are flagged in results.
  Abandoned cases are never returned.
- **Result shape:** top 3–5 cases as **one-line summaries** (DN, what it is, outcome,
  key lessons). Full detail is fetched only for a case Claude chooses.

### 5.3 Voyage
- Account and API key separate from Claude. Key in the environment variable
  `VOYAGE_API_KEY` on the Windows machine, never in the knowledge-base folder or the repo.
- Cost (October 2026): images $0.60 per billion pixels (at most $0.0012 per 2-megapixel
  image), text $0.06 per million tokens. Four images and one text per case: about
  **$0.005 per case at most**. The free allowance (150 B pixels, 200 M tokens) covers
  roughly 18,000 cases.
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

## 6. Output: two destinations, at two moments

| When | Output folder (`SWMCP_OUTPUT_DIR`, e.g. `C:\SW-Output\`) | Knowledge base (`cases\DN17\`) |
|---|---|---|
| **While modelling** | `DN17.SLDPRT`, `DN17.SLDDRW`, saved by Claude under server-chosen names (§6.2) | The scan, the rendered page, the crops |
| **At finalise** | Both files checked to exist and be saved; nothing is renamed or regenerated | Their measurements (mass, volume, bbox, drawing read-back), not the files |
| **While you review** | You check and fix the two files in place | — |
| **At approval** (§9) | The server opens your checked files and exports from them | `DN17.step` (from the SLDPRT), `DN17 drawing.pdf` (from the SLDDRW) |
| **After approval** | You move the files out and add the company's data | Never reads them again |

- **The SLDPRT and SLDDRW are never stored in the knowledge base.** The part carries the
  full feature tree, and both get company data added after they leave the output folder,
  which later sessions must not see. Apart from approval (§9), the knowledge-base tools
  **never read the output folder**, so moving files out of it afterwards breaks nothing.
- **The stored STEP and PDF are made from exactly the files you approved,** with your fixes,
  and before any company data goes in. The PDF is printed from the same SLDDRW Claude made
  and checked: nothing is regenerated, so it is the drawing the shop machines from.
- **The finalise result** lists both file names and reminds you: *review and fix the files
  in place, tell me "DN17 approved", and only then move them out.*

### 6.1 Shop drawings: designed separately
How the drawing itself is made (third-angle, inches, default template, per-dimension
precision, callouts, view choice, the spec-vs-drawing check, renaming with Pack and Go) is in
**[SHOP-DRAWINGS.md](SHOP-DRAWINGS.md)**, built on its own branch. What the knowledge base
needs to know about it:

- **The drawing is a shop-floor document**: parts are machined from it after review.
- **The drawing spec** (format in SHOP-DRAWINGS.md §4.1) is written by Claude at the
  describe step and **stored in the case as JSON**. The knowledge base does not import
  `drawing_spec.py`; it stores and returns the spec as-is. The spec is also searchable
  (keywords over its callouts and notes, e.g. a thread size or a finish).
- **The stored spec can be corrected.** After describe, Claude asks you about every
  `unreadable` item. Your answers go back into the stored spec with `kb_update_spec`, which
  replaces it whole and keeps the previous versions in the case. The spec Claude passes to
  `make_drawing` and `check_drawing`, and the one shown at review, is always the latest.
- **Claude makes and checks the drawing while modelling** (`make_drawing` with its chosen
  views and the spec, then `check_drawing`), saved as `DN17.SLDDRW`. Finalise does not make
  another drawing. Once the drawings branch has merged, finalise reads the saved drawing
  back with `check_drawing` against the stored spec and stores the result (§15.3).
- **Approval is a release gate.** A case stays *unreviewed* until you approve it, and
  search never presents an unreviewed or *changes_needed* drawing as a good reference.
- At review you are shown the stored spec beside the old drawing, so a misreading by Claude
  is visible as a line in a list (SHOP-DRAWINGS.md §6).

### 6.2 The server names every file
A drawing links to its part by file path, and `make_drawing` refuses an unsaved part. So the
part must be saved **before** the drawing is made, and under its final name: if Claude saved
it as `bracket.sldprt` and finalise renamed it to `DN17.SLDPRT` later, the drawing would still
point at `bracket.sldprt`. And Claude typing names is how a mix-up like `DN71` happens.

So while a case is open:
- **`save_part` and `make_drawing` take no path.** Their `path` argument becomes optional.
  With a case open and no path, the server writes `SWMCP_OUTPUT_DIR\DN17.SLDPRT` and
  `DN17.SLDDRW`. A path that differs from those is **refused**, naming the right one.
- The part has its final name from its first save, so the drawing's link is right from the
  start and nothing is ever renamed.
- Calling them again (after a fix) overwrites the same two files.
- With no case open, both tools work as today, with a path.

The lookup is one function, `case_output_path(kind)` in `src/solidworks_mcp/case_paths.py`,
returning `None` with no open case. It arrives as a stub in the shared commit (§15.1); the
knowledge-base branch fills it in. `save_part` and `make_drawing` call
`case_paths.resolve(kind, path)` as their first line; see §15.2 for who edits what.

## 7. Tool cards

### 7.1 Two layers
1. **Raw event log** (append-only, automatic): every call, its arguments, result, error
   signature and measurements (§8). It is never shown to the agent directly.
2. **One card per tool**, built from the log.

### 7.2 Card format (fixed, capped)

```
add_fillet                         (server 0.16.0, SOLIDWORKS 2020 SP5, updated 2026-10-18)
Use when:      <one line>
Don't use when:<one line>
How to call:   <one line; signature comes from the docstring>
Known failures:
  - <cause → fix>                                    (seen 4×, last DN17)
  - <cause → fix>                                    (seen 1×, last DN9)
```

- **Hard cap: 8 bullets or about 150 words.** The server **refuses** a card over the cap,
  so it stays short whatever the agent does.
- **The card is stored as fields, not as one text.** Claude writes only the words: the
  *Use when* and *Don't use when* lines, and each failure's *cause → fix* text, keyed by its
  error signature (§8.1). The server renders the card and adds everything that is counted:
  the header, the *How to call* line, and each failure's count and last case. Claude cannot
  drop, invent or miscount a failure line's numbers.
- The *How to call* line is generated from the tool's docstring in `server.py`, never
  maintained by hand, so it cannot drift from the code.
- Every failure line carries the **server version and the SOLIDWORKS revision** it was last
  seen on (`RevisionNumber`, e.g. `28.5.0`; the same tool can fail on 2020 and work on a
  later release). A failure is dropped from the card when it has not been seen since the
  server version or SOLIDWORKS revision changed, and the tool has since been used
  successfully at least 3 times. Neither branch bumps the server version (§15.2), so until
  the next release this rule only acts on SOLIDWORKS upgrades.

### 7.3 Keeping cards clean
- **Deduplication is pure code:** events are grouped by error signature (§8.1), so six
  Revolve failures with the same root become one bullet with a count.
- **Rewording needs an LLM**, and the only one is Claude in Desktop (the server cannot call
  Claude). So **tidying is part of finalise**: the server hands Claude each used tool's
  current card fields plus that case's new events; Claude returns the rewritten fields; the
  server checks that every signature on the card still has a text, checks the cap, and
  stores it.
- **Lessons from your review** (§9) are handed over at the next tidy of the tool at fault.

### 7.4 Cards reach the agent automatically
When a modelling tool returns an error, the response includes the matching line from that
tool's card (`kb_hint`), if one exists. The agent does not have to remember to look.

## 8. Errors and why they happened

### 8.1 Error signatures (pure code)
- **The server's own errors:** `SolidWorksError` gains a stable **`code`**
  (e.g. `FILLET_TOO_LARGE`) and structured **`params`**, set at each place it is raised in
  `session.py`. The signature is the code. There are about 400 raise sites, so this is done
  one tool family at a time, starting with the families that fail most (fillet, chamfer,
  shell, revolve, cuts, holes). Raises without a code fall back to the normalised text
  below, so nothing is lost meanwhile.
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

- The probe runs on the COM thread inside `run_guarded`, after the rollback, so it measures
  the part as it was before the failed call. **A probe that fails itself is recorded as
  such and never replaces or hides the original error.**
- Code also computes simple ratios (e.g. radius ÷ wall = 2.5). When the card is tidied,
  Claude can generalise to *"fails when radius > wall"*. That is interpretation of measured
  data, not memory.
- A probe is written once per tool family. Tools without a probe still log the error and
  the part's bounding box and volume.

## 9. Your review

While the files are still in the output folder, you check and **fix them in place**
(`DN17.SLDPRT`, `DN17.SLDDRW`), then tell Claude, for example:
- *"DN17 approved"*, or
- *"DN17 needed changes: the bore was Ø.750, not Ø.875"* (after you have fixed it).

`kb_review_case` then:
1. **Refuses if either file is missing** from the output folder, and says the review needs
   them there. It never approves without the files.
2. **Opens your checked files in SolidWorks and exports** `DN17.step` from the SLDPRT and
   `DN17 drawing.pdf` from the SLDDRW into `cases\DN17\`. It closes them again without
   saving. Opening, exporting and comparing are separate calls on the COM thread, so no single
   call runs into the 120 s timeout.
3. **Works out what you changed, by code**, against what was recorded at finalise: the
   part's volume and bounding box, and (once the drawings branch has merged) every drawing
   dimension, tolerance and note that was added, removed or altered, from `check_drawing`'s
   read-back. The changes are stored in the case next to your note, so a correction is
   recorded even if your note is brief. If the files did not change since finalise, it
   says so.
4. Never copies the SLDPRT or SLDDRW into the knowledge base (§6).
5. Ends with: *"Stored DN17.step and DN17 drawing.pdf. You can now move DN17.SLDPRT and
   DN17.SLDDRW out of the output folder."*

Both verdicts store the STEP and PDF of the fixed files; the verdict decides how the case
ranks. This:
- sets the case **outcome**: approved cases rank first; cases that needed changes are flagged;
- turns each correction into a **lesson on the card** of the tool at fault (e.g. *"check
  the bore diameter against the section view"*), at the next card tidy;
- means future sessions only ever see the **fixed** STEP and drawing, the ones the shop
  machined from, without the company data added later.

Your review is the most reliable signal the knowledge base gets.

## 10. Logging (automatic, at one choke point)

- Every tool call goes through `_call()` in `server.py` (which knows the tool name,
  arguments and result) and `run_guarded()` in `session.py` (which knows about errors and
  rollbacks). The event is written there, so the timeline is complete and accurate.
- **All tools are logged except the `kb_*` tools.** Read-only tools (`list_faces`,
  `screenshot`, `get_status`, …) are logged too, since what Claude looked at before a step
  is part of how a case was modelled; the timeline view marks them so they can be folded
  away.
- **The agent adds only judgement:** the description and spec at describe, spec answers,
  and the card wording at finalise.
- With no open case, calls are not logged to any case (still counted for tool cards).
- **A logging failure never breaks a modelling call**, but it is reported as a warning in
  that call's result, not swallowed (fail loud, rule 4).

## 11. New MCP tools

All typed and fixed, consistent with rule 8 (no arbitrary-code tool).

| Tool | Does |
|---|---|
| `kb_ingest_drawing(path, page=1)` | Check DN number, render the page, open the case, return the overview image |
| `kb_view_region(box)` | One region of the open case's page at full resolution, as an image (§4.3) |
| `kb_describe_case(view_boxes, description, part_type, drawing_spec, handwritten_dn=None, dn_confirmed=False)` | Crop, embed, store the description, part type and drawing spec (JSON, format in SHOP-DRAWINGS.md §4.1); run search; return the top similar cases |
| `kb_update_spec(drawing_spec)` | Replace the open case's spec (after you answer the unreadable items); earlier versions kept |
| `kb_search(text=None, like_case=None, filters=None, limit=5)` | Hybrid search; one-line summaries |
| `kb_get_case(dn, include_timeline=False)` | Full detail of one case; with `include_timeline`, the full step list. The case's STEP file can be opened for reference |
| `kb_tool_card(tool)` | One tool's card |
| `kb_finalise_case()` | Check both files are saved in the output folder; record their measurements; close the case; return the cards to tidy |
| `kb_update_tool_card(tool, card)` | Store a tidied card's fields (refused over the cap, or if a failure signature lost its text) |
| `kb_abandon_case(reason)` | Close the open case without finishing it; frees its DN (§3) |
| `kb_review_case(dn, verdict, notes=None)` | Record your review; export STEP and PDF from your checked files; store what changed (§9) |
| `kb_reembed_pending()` | Embed cases that were marked pending |

Changed existing tools: `save_part` and `make_drawing` take an optional `path`; with a case
open the server names the file (§6.2).

Adding them follows the usual pattern in `CLAUDE.md` (session method → server wrapper →
tests → README tool count, guide, CHANGELOG).

## 12. Storage and packaging

### 12.1 Layout
```
%LOCALAPPDATA%\solidworks-mcp\kb\        (override: SWMCP_KB_DIR)
├── kb.sqlite
│     cases            dn, status, open flag, part type, description, drawing spec (JSON,
│                      with earlier versions), drawing check result, measurements at
│                      finalise and at review, review, timestamps
│     events           case, seq, tool, args, result, error_code/signature, probe data,
│                      server version, SOLIDWORKS revision
│     error_signatures signature, tool, count, first/last seen, last versions
│     tool_cards       tool, card fields (JSON), versions, updated
│     vectors          case, kind (view_front/…/description), model, model_version, blob
│     fts              FTS5 index over descriptions, part types, spec callouts and notes,
│                      tools, error codes, review notes
└── cases\DN17\
      input.pdf  page.png  views\front.png top.png side.png  thumb.png
      DN17.step  DN17 drawing.pdf          (written at approval, §9)
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
- **Pure tests** (run on any OS, including the Mac): DN-name parsing, region boxes and
  scaling, error normalisation, signatures, card fields and cap, rank fusion, storage layer,
  lifecycle transitions (including abandon and re-ingest), server-chosen paths. Voyage is
  faked.
- **Integration tests** (Windows + SolidWorks): with a case open, `save_part` and
  `make_drawing` write `DN17.SLDPRT` and `DN17.SLDDRW` and the drawing links to that part;
  approval writes `DN17.step` and `DN17 drawing.pdf` from edited files; a forced fillet
  failure records the probe data.
- On the Mac nothing end-to-end can run. Say so rather than claiming it was verified.

## 13. Open points

Decided 2026-10-19:
1. **Past timelines and STEP files can be shown.** Search returns summaries; `kb_get_case`
   returns the full step list and the STEP file on request.
2. **Voyage data use:** opt out before the first real job (§5.4).
3. **Storage timing:** the SLDPRT and SLDDRW are the files you check, in the output folder.
   The STEP and PDF are made from them only at approval, before you move them out and add
   company data (§6, §9).
4. **File names:** chosen by the server from the open case while modelling, not at
   finalise (§6.2).
5. **Shop drawing standard:** see SHOP-DRAWINGS.md §3–§4.

Still open:
6. **Contract restrictions.** Whether any contract forbids sending its drawings to outside
   services at all (§5.4). This is yours to check per contract.

## 14. Build order

| Step | Content |
|---|---|
| — | Shared commit on `main` first (§15.1) |
| K0 | Storage layer (open case in the database), error normalisation, `SolidWorksError` codes for the first tool families, automatic logging |
| K1 | `kb_ingest_drawing`, `kb_view_region`, `kb_describe_case`, `kb_update_spec`, `kb_abandon_case`, Voyage embedding, `kb_search`, `kb_get_case` |
| K2 | Server-chosen paths in `save_part` / `make_drawing` (§6.2); `kb_finalise_case` |
| K3 | Tool cards: fields, rendering, cap, tidy at finalise, `kb_hint` on errors |
| K4 | Context probes, one tool family at a time (fillet/chamfer first), with their error codes |
| K5 | `kb_review_case`: export STEP and PDF from the checked files, part before/after; ranking by outcome; lessons from corrections. The drawing before/after comparison uses `check_drawing`'s read-back, so it lands after the drawings merge (with K6) |
| K6 | After the drawings branch has merged: finalise stores `check_drawing`'s result against the stored spec; review compares the drawing read-back before and after (§15.3) |

## 15. Working alongside the drawings branch

This section is the same in both design documents.

### 15.1 Shared commit on `main`, before either branch starts
One small commit, so both branches start from the same contract:
- `errors.py`: `SolidWorksError` gains optional `code` and `params` (default `None`), so
  existing raises keep working unchanged.
- `units.py`: exact inch ↔ mm helpers (1 in = 25.4 mm), with pure tests.
- This document, `SHOP-DRAWINGS.md` and `JOB-BRIEF.md`, with the drawing-spec format in
  SHOP-DRAWINGS.md §4.1 frozen. Changing the format afterwards needs agreement from both
  branches.
- `make_drawing`'s and `save_part`'s `path` becomes optional in their signatures, and the
  session methods resolve it with `case_paths.resolve(kind, path)` (new module
  `case_paths.py`), whose stub `case_output_path(kind)` returns `None` (no case open).
  The server wrappers stay plain `_call(_session.make_drawing, path)`.
  With `None` and no `path`, the tools refuse as now ("give a path"). The knowledge-base
  branch later makes the stub return the case's paths; the drawings branch never has to
  touch it again.
- Any uncommitted changes in the working tree committed or set aside first, so neither
  branch inherits them by accident.

### 15.2 Who owns what

| File / area | Drawings branch | Knowledge-base branch |
|---|---|---|
| `make_drawing` and drawing code in `session.py` | **Owns** | Never calls or edits it. At review it opens the saved SLDDRW and saves it as PDF with its own code |
| Other modelling code in `session.py` | Doesn't touch | Error codes at raise sites; context probes (fillet, shell, …); `save_part` path handling |
| `server.py` | Edits the `make_drawing` wrapper; adds `check_drawing`. Keeps the `case_paths.resolve` line at the top of `make_drawing` in `session.py` | Hooks logging into `_call()`; adds `kb_*` wrappers **in one block at the end** |
| `constants.py` | Paper size, projection values | Doesn't touch |
| `drawing_spec.py` (new) | **Owns** | Doesn't import it; stores the spec as JSON |
| `kb/` package (new) | Doesn't touch | **Owns** |
| `case_paths.py` (stub from the shared commit) | Doesn't touch | **Owns**; fills it in |
| Tests | New files (`test_drawing_spec.py`, `test_drawings.py`) | New files (`test_kb_*.py`) |
| `README.md`, `CHANGELOG.md`, `guide.md` | Own sections only | Own sections only |
| Version numbers | **Neither branch bumps the version** | — |

- CHANGELOG entries go under `## [Unreleased]`, in a subsection per branch.
- The README tool count is fixed by whichever branch merges second.

### 15.3 Merge order
1. **The knowledge base's first part is already on `main`** (K0, K1 and K3, merged
   2026-10-19 as PR #2). It does not depend on the drawings work.
2. **Drawings merges next.** It is self-contained, and the rest of the knowledge-base
   workflow relies on `make_drawing` with views and a spec, and on `check_drawing`.
3. The knowledge-base branch merges `main` in again, then one small follow-up commit (K6) makes
   finalise store `check_drawing`'s result for the saved `DN17.SLDDRW` against the stored
   spec, and review compare that read-back before and after your fixes. Until then,
   finalise and review record the part's measurements only.

### 15.4 Testing: one SolidWorks, one Mac
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
