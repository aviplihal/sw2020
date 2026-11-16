# Knowledge Base — Design (v2: plain Markdown)

> Status: **design agreed, partly built.** v1 (SQLite + FTS5 + Voyage image vectors) was
> partly built and merged into `main` as K0, K1 and K3 (PR #2). v2 replaces its storage
> with plain Markdown files and drops the vectors; §12.3 lists what changes in that code.
> Every file type has a filled-in example in **[kb-templates/](kb-templates/)**, with its
> size in tokens. Review those first: they are the format.
> Companion design: [SHOP-DRAWINGS.md](SHOP-DRAWINGS.md) (branch `feat/shop-drawings`).
> Section numbers match v1, so references from other files and the code still hold.

## 1. Purpose

Every job follows the same path:

```
old drawing (phone scan, PDF)  ──>  Claude models it  ──>  SLDPRT + SLDDRW  ──>  you approve  ──>  STEP + PDF
        DN17.pdf                    (MCP tools)            for your review                       into the knowledge base
```

The knowledge base gives every new session a memory of earlier jobs, so Claude improves
over time:

- **"Have we done one like this before?"** Earlier cases: what the part was, how it was
  modelled, what went wrong, what you corrected.
- **"What do we know about this tool?"** One short, maintained card per MCP tool.
- **"Where is Claude wasting effort?"** A digest of every job (time and tokens per tool) and
  one shared list of hotspots for you, or another session, to fix in the code.

It runs on **one Windows machine**, next to SolidWorks and the MCP server. The client is
**Claude Desktop**.

### Why plain Markdown
- **Claude reads it directly and cheaply.** No query layer: a file is the answer.
- **You can read and edit it** in any editor. Backup = copy the folder.
- **Small scale.** Hundreds of cases, not millions. An index of one line per case stays a
  few thousand tokens; searching it is a text search.
- **No outside service.** Only Claude sees the drawings (v1 also sent them to Voyage).

## 2. What is stored: six types of file, for two readers

```
%LOCALAPPDATA%\solidworks-mcp\kb\        (override: SWMCP_KB_DIR)
│   ── for CAD agents (doing a job), through the kb_* tools ──
├── INDEX.md                 one line per case                       server, rebuilt on every change
├── tools\add_fillet.md      one card per MCP tool, open failures    Claude's words, server's counts
├── cases\DN17\
│     case.md                the job: description, questions, model, result, review, lessons
│     spec.json              the drawing spec (format: SHOP-DRAWINGS.md §4.1; tools read JSON)
│     input.pdf  page.png  views\front.png …                         ingest / describe
│     DN17.step  DN17 drawing.pdf                                    written at approval (§9)
│   ── for fix agents (you, or a session improving the tools); no kb_* tool returns these ──
└── fixes\
      HOTSPOTS.md            the one file: effort, every issue, sessions, what fixed it
      DN17\digest.md         this job's time and tokens, hard parts explained, session link
      DN17\timeline.md       one line per tool call: the raw record
```

| File | Written by | Read when | Size (example) |
|---|---|---|---|
| [INDEX.md](kb-templates/INDEX.md) | Server | CAD: every job, at describe | ~30 tokens per case |
| [case.md](kb-templates/case.md) | Server + Claude (per section, see the templates README) | CAD: a similar case looks useful | ~470 tokens |
| [tools/&lt;tool&gt;.md](kb-templates/tool-card.md) | Claude's words, server's counts | CAD: before using a tool; on its errors | ≤ 250 tokens |
| [HOTSPOTS.md](kb-templates/HOTSPOTS.md) | Server; `kb-fixed` command | Fix: the starting point | ~150 tokens per issue |
| [digest.md](kb-templates/digest.md) | Server numbers, Claude's explanations | Fix: detail of one job | ~600 tokens |
| [timeline.md](kb-templates/timeline.md) | Server only | Fix: rarely; it feeds the others | ~30 tokens per call |

**Nothing about time, tokens, hard parts or fixes is in the CAD files.** A CAD agent sees what
helps it model; a fix agent sees what helps it fix.

**Rules for every file**
- Fixed headings in a fixed order, so the server can find its sections and Claude knows
  where to look. A short YAML header only where the server needs fields (case.md).
- The server writes everything that is **counted or measured** (counts, times, sizes,
  mass, dates). Claude writes only **judgement** (descriptions, lessons, card wording,
  what was hard). Claude can never miscount.
- Hard caps on the parts Claude writes (§7.2, §10.3); the server refuses text over a cap.
- Units: millimetres in measurements, as the tools report them. The spec keeps the
  drawing's inches exactly as written.

## 3. Case lifecycle

```
ingested ──> described ──> modelling ──> finalised ──> approved
   │            │              │                  └─> changes_needed
   └────────────┴──────────────┴──> abandoned
```

- The status is the `status:` field in `case.md`'s header. **Only one case is open** (ingested,
  described or modelling) at a time; the server finds it by reading the headers, so it
  survives Claude Desktop restarting the server. Every logged tool call goes to the open case.
- **ingested:** the PDF is read and rendered, the DN number checked.
- **described:** Claude has given the view boxes, description, part type and spec.
- **modelling:** the timeline grows with each tool call. The part and drawing are saved
  under server-chosen names in the output folder (§6.2).
- **finalised:** both files are saved; their measurements, the digest and the tidied tool
  cards are written. Nothing more is logged to the case. Nothing is exported yet.
- **approved / changes_needed:** set by you after checking and fixing the files (§9). Until
  then the case is **unreviewed**.
- **abandoned:** `kb_abandon_case(reason)` closes a case that should not go on. Its folder is
  renamed `DN17~1`, it is left out of INDEX.md, and the DN can be ingested again.

## 4. Input: the DN number and the PDF

### 4.1 The DN number comes from the file name
- You save the scan as **`DN<n>.pdf`**. The file name is the source of truth; ingest
  **refuses** any other name.
- **Another case open:** refused, naming it. **Duplicate:** refused (an abandoned case does
  not count). **Gap** (DN15 then DN17): a warning, accepted.
- **Handwritten DN** on the sheet is only a cross-check: if it differs, describe refuses
  and Claude asks you; once you confirm, it calls again with `dn_confirmed=True`.

### 4.2 Rendering the PDF
- Phone scans: use the iPhone's document scanner and save as PDF.
- Rendered with **`pypdfium2`** (Apache/BSD; **not** PyMuPDF, which is AGPL) at about
  **200 dpi** to `page.png`. Optional `page` argument; a missing page is refused.

### 4.3 Letting Claude read small text
Claude's vision scales images to about 1,500 px, so a whole sheet is too coarse for small
dimension text.
- Ingest returns an **overview** (whole page, ~1,500 px JPEG): for layout only.
- **`kb_view_region(box)`** returns one region of `page.png` at full resolution. Claude reads
  every value from zoomed regions, never the overview.
- **Boxes are page fractions** `[x0, y0, x1, y1]`, 0 to 1, top-left origin.
- Each zoom costs about 1,500–2,000 tokens, which is why the digest counts them (§10.3).

## 5. Finding earlier cases

No vectors, no search engine. Two steps, both plain text:

1. **INDEX.md** is one line per case (DN, part type, size, outcome, a one-line summary and
   lesson), approved cases first, abandoned ones left out. `kb_describe_case` returns
   the whole index (a few thousand tokens at a few hundred cases). Claude picks the lines that
   look like the new part.
2. **`kb_get_case(dn)`** returns that case's `case.md`. Its *How it was modelled* section
   (built from the timeline) is the list of steps that worked; the timeline itself stays
   with the fix files.

`kb_search(text, part_type, status)` is a plain case-insensitive text search over all
`case.md` files (description, spec callouts and notes, lessons, review notes, error codes),
returning the matching INDEX lines. Use it when the index has grown too long to read whole,
or to find, say, every `1/2-13 UNC` thread.

- **Part type** is a short label Claude gives at describe (`bracket`, `shaft`, `flange`).
  The index shows the labels in use, so the vocabulary stays small.
- **Ranking:** approved first; `changes_needed` flagged ⚠; unreviewed marked `?`.
- **Given up from v1:** finding a case because its *drawing looks similar* (image vectors).
  At this scale a description and part type find the same cases. If the index ever outgrows
  this, local vectors could be added beside the files without changing them.

## 6. Output: two destinations, at two moments

| When | Output folder (`SWMCP_OUTPUT_DIR`, e.g. `C:\SW-Output\`) | Knowledge base (`cases\DN17\`) |
|---|---|---|
| **While modelling** | `DN17.SLDPRT`, `DN17.SLDDRW`, server-named (§6.2) | Scan, page render, crops, timeline |
| **At finalise** | Both files checked to exist and be saved | Measurements, digest, cards |
| **While you review** | You check and fix the two files in place | — |
| **At approval** (§9) | The server opens your checked files and exports | `DN17.step`, `DN17 drawing.pdf` |
| **After approval** | You move the files out and add company data | Never reads them again |

- **The SLDPRT and SLDDRW are never stored in the knowledge base**, and apart from approval
  the knowledge-base tools never read the output folder.
- **The stored STEP and PDF are made from exactly the files you approved,** with your fixes,
  before any company data goes in.

### 6.1 Shop drawings: designed separately
How the drawing is made is in **[SHOP-DRAWINGS.md](SHOP-DRAWINGS.md)**. What the knowledge
base needs:
- **The drawing spec** is written by Claude at describe and stored as `spec.json`, as-is
  (the knowledge base does not import `drawing_spec.py`).
- **The spec can be corrected** with `kb_update_spec`, after you answer the `unreadable`
  items. It replaces `spec.json`; each question and your answer are listed in `case.md`
  under *Questions asked*, so the change is visible without keeping old copies.
- **Approval is a release gate:** an unreviewed or `changes_needed` drawing is never presented
  as a good reference.

### 6.2 The server names every file
While a case is open, **`save_part` and `make_drawing` take no path**: the server writes
`SWMCP_OUTPUT_DIR\DN17.SLDPRT` and `DN17.SLDDRW` and refuses a different path. The part
has its final name from its first save, so the drawing's link to it is right from the start.
With no case open both tools work as today, with a path. The lookup is
`case_paths.case_output_path(kind)`: `SWMCP_OUTPUT_DIR\DN17.SLDPRT` / `.SLDDRW` for the open case (built 2026-11-16).

## 7. Tool cards

### 7.1 One file per tool
`tools\add_fillet.md`. Built from the timelines of all cases; never several scattered
entries for one tool.

### 7.2 Card format (fixed, capped)
See [kb-templates/tool-card.md](kb-templates/tool-card.md). In short:

```
# add_fillet
<server line: versions, updated, used N×, failed N×>
**Use when:** <one line>
**Don't use when:** <one line>
**How to call:** <one line, from the docstring>
## Known failures
- `<SIGNATURE>` <cause → fix> (<server: seen N×, last DN17, SW revision>)
```

- **Cap: 6 failure lines and 150 words of Claude's text.** The server refuses more.
- Claude writes only the *Use when* / *Don't use when* lines and each failure's
  *cause → fix* text. The server writes the header line, *How to call* (from the docstring
  in `server.py`) and every count, so nothing can be miscounted or drift from the code.
- The server **recounts from the timelines** each time it writes a card; the counts in the
  file are never the source of truth, so hand-editing a card cannot break them.
- **A fixed failure is removed from the card** (§10.5), so CAD agents never read about
  problems that no longer happen. If it is seen again after the fix, it comes back on the
  card marked *again after H-00n*.
- A failure not seen since a SOLIDWORKS upgrade, with 3+ successful uses since, is dropped.

### 7.3 Keeping cards clean
- **Deduplication is code:** failures are grouped by error signature (§8.1).
- **Rewording is Claude's job at finalise:** the server hands Claude each used tool's card
  plus this case's failures; Claude returns the new wording; the server checks the cap and
  that every signature still has a text.
- **Lessons from your review** (§9) are handed over at the next tidy of that tool's card.

### 7.4 Cards reach Claude automatically
When a tool returns an error, the result includes the matching card line (`kb_hint`). If
the failure belongs to a **fixed** issue, `kb_hint` instead gives that issue's
*If it happens again* note from HOTSPOTS.md. That is the only time a CAD agent sees anything
from the fix files, and only when it is needed.

## 8. Errors and why they happened

### 8.1 Error signatures (pure code; already built)
- The server's own errors carry a stable **`code`** (e.g. `FILLET_TOO_LARGE`); that is the
  signature. Codes are added one tool family at a time.
- Other errors: feature names, numbers, paths and HRESULTs are replaced with placeholders,
  then hashed (`T:9f2c1a04b7e3`).

### 8.2 Context probes: later, only if needed
v1 planned a measuring probe per tool family after each failure (wall thickness next to a
fillet, and so on). **Deferred:** build one only when a card or HOTSPOTS.md shows that a
failure keeps coming back and the error alone doesn't say why. The fillet error already
names the largest radius that fits.

## 9. Your review

While the files are still in the output folder, you check and **fix them in place**, then
tell Claude *"DN17 approved"* or *"DN17 needed changes: the bore was Ø.750, not Ø.875"*.

`kb_review_case(dn, verdict, notes)` then:
1. **Refuses if either file is missing** from the output folder.
2. **Opens your checked files and exports** `DN17.step` and `DN17 drawing.pdf` into
   `cases\DN17\`, closing them without saving. Separate worker calls, so none hits the
   120 s timeout.
3. **Works out what you changed, by code**: volume and bounding box against finalise (and,
   after the drawings branch merges, every drawing dimension and note from `check_drawing`).
4. Writes the verdict, your note and the measured changes into `case.md` → *Review*, and
   rebuilds INDEX.md.
5. Ends with: *"Stored DN17.step and DN17 drawing.pdf. You can now move the files out."*

Each correction becomes a lesson on the card of the tool at fault at its next tidy, and a
line in the case's *Lessons*. Your review is the most reliable signal the knowledge base gets.

## 10. Logging, the session digest and hotspots

### 10.1 Timeline: why it exists
The timeline is the server's own record of a job. Everything counted elsewhere is built
from it: the digest's numbers, the tool cards' counts, and case.md's *How it was modelled*.
It is kept because it is the only record that is **always there**: the session transcript
may not exist (a Claude Desktop chat has none on disk), and Claude Code deletes transcripts
after 30 days by default (setting `cleanupPeriodDays`). Agents rarely read it directly.

- Every tool call goes through `_call()` in `server.py`; the server appends **one line**
  to `fixes\DN17\timeline.md`: number, time, tool, short arguments, outcome, duration,
  result size in tokens, and the pause before it.
  See [kb-templates/timeline.md](kb-templates/timeline.md).
- **All tools are logged, including `kb_*`** (v1 left those out, but zooms are among the
  biggest token costs). Read-only tools are marked `·`.
- Arguments are cut to ~80 characters; full results are not stored (only the measured
  volume / bbox, or the error code). A 100-call job is about 3,000 tokens.
- With no case open nothing is written to a case, but tool-card counts still go up
  (in `fixes\_unassigned\timeline.md`).
- **A logging failure never breaks a modelling call**; it is reported as a warning in that
  call's result.

### 10.2 Linking the session
Each digest names the **session ID and the transcript file** of the chat that did the job,
so a fix agent can open exactly that conversation (it is a local `.jsonl` file) instead of
searching through all the sessions on the computer.

- An MCP server is not told which chat called it, so a **Claude Code hook** tells it: a
  `PreToolUse` hook on `mcp__solidworks__.*` writes the hook's `session_id` and
  `transcript_path` to `kb\fixes\.session.json` before each call. The server reads that file
  when logging, so a job that spans two sessions lists both. Setting the hook up is one
  entry in the CAD project's `.claude/settings.json`; it ships in the repo.
- **Claude Desktop chat** (not the Code tab) has no hooks and no local transcript. The digest
  then says *session: not recorded*, and everything measured still works.
- Raise `cleanupPeriodDays` in Claude Code's user settings so transcripts outlive the 30-day
  default (set to 3650 on the Windows machine, 2026-10-28; `0` would turn saving off). The
  digest keeps the numbers either way.

### 10.3 Session digest: where the effort went, and why
Written at **finalise** (and at abandon), to `fixes\DN17\digest.md`. See
[kb-templates/digest.md](kb-templates/digest.md).

**The server measures.** From the timeline:
- **Time:** total; tools running; Claude working between calls (the pause before each call).
- **Result tokens:** what each tool result put into Claude's context: text at ~4 characters
  per token, images at width × height ÷ 750 (Anthropic's formula).
- **Top 5 tools** by time and tokens; **loops** (the same tool 3+ times in a row, the same
  zoom box twice); **failures** by signature.

When the transcript is linked, the server also reads it at finalise. It is plain JSON, with
a timestamp, Claude's output tokens and the context size for every step. From it:
- **Claude's own tokens** (thinking and text) written before each call, so a long pause can
  be told apart: a lot of thinking, or a slow tool, or a long reply to you.
- **Waiting on you**, exactly: from Claude's last message to your next one. (Without the
  transcript, any pause over 5 minutes counts as waiting.)
- **Context size**, so you can see when a job's context got large.

**Then the server picks the hard parts** (`D1`, `D2`, …, at most 6): the longest pauses,
the steps where Claude wrote the most tokens, every loop, every repeated failure, and (with
the transcript) any step where the context grew by more than 30,000 tokens, so Claude says
what filled it (a large `list_faces` result, several zooms kept in context). These are
chosen by measurement. Claude does not decide what was hard.

**Claude explains each one** (`kb_explain_digest`, one line each, 40 words at most):
what it was doing in that pause and why it took long. It writes these at finalise, while
the job is still in its context. It may add up to 2 hard parts the numbers can't show
(say, a missing recipe in the guide), marked *not measured*. "Not sure" is an allowed answer.
The transcript is the evidence; Claude's line is the summary.

Each hard part links to an issue in HOTSPOTS.md (`→ H-005`): an existing one if it is the
same problem, otherwise a new one. **Fix ideas go to HOTSPOTS.md**, not the digest.

### 10.4 HOTSPOTS.md: the one file for fix agents
`fixes\HOTSPOTS.md` ([kb-templates/HOTSPOTS.md](kb-templates/HOTSPOTS.md)) brings together
everything a fix session needs, so it starts there and opens a digest only for detail.

- **Measured:** the tools costing the most time and tokens over the last 10 jobs.
- **Open issues**, worst first (jobs × cost per job). Each issue lists:
  - the measured cost per job;
  - **every job it was seen in, with that job's session ID and hard part** (`DN17 efb18dde D4`),
    so the conversation can be opened straight away;
  - Claude's fix idea.
  Issues come from hard parts (§10.3), and from any failure signature seen in 2 or more jobs.
- **Fixed issues**, with what fixed it, the commit, and an *If it happens again* note for a
  CAD agent that runs into it anyway.
- **Cap:** 20 open issues; the cheapest move to *Parked*.

### 10.5 Marking an issue fixed
When you or a fix session has fixed something, run one command. It keeps the three files
that mention the issue in step, so nobody edits them by hand:

```
solidworks-mcp kb-fixed H-002 --commit a1b2c3d --by "kb_view_region returns earlier readings of a box" --note "call kb_view_region with note= after reading"
```

1. **HOTSPOTS.md:** moves H-002 to *Fixed* with the date, commit, `--by` and `--note`.
2. **Each digest** whose hard part points to H-002 gets, next to it:
   `✓ fixed by <--by> (<commit>, <date>)`. Reading a digest later, you see what was hard
   *and* what was made to make it easier.
3. **Tool cards:** failure signatures that belong to H-002 are removed from the cards.

If an H-002 failure happens again after that date: `kb_hint` shows the *If it happens again*
note, the failure returns to the card marked *again after H-002*, and HOTSPOTS.md opens a new
issue pointing back to H-002. So a fix that didn't hold is visible at once.

## 11. MCP tools

All typed and fixed (rule 8: no arbitrary-code tool).

| Tool | Does |
|---|---|
| `kb_ingest_drawing(path, page=1)` | Check DN, render the page, open the case, return the overview image |
| `kb_view_region(box)` | One region of the page at full resolution (§4.3) |
| `kb_describe_case(view_boxes, description, part_type, drawing_spec, handwritten_dn=None, dn_confirmed=False)` | Crop the views, write `case.md` and `spec.json`; return INDEX.md |
| `kb_update_spec(drawing_spec, answers)` | Replace `spec.json`; add the questions and your answers to `case.md` |
| `kb_search(text=None, part_type=None, status=None, limit=10)` | Text search over the cases; returns INDEX lines |
| `kb_get_case(dn)` | That case's `case.md` |
| `kb_tool_card(tool)` | One tool's card |
| `kb_finalise_case(lessons)` | Check files, record measurements, write the digest's numbers, close the case; return the cards to tidy and the hard parts to explain |
| `kb_explain_digest(explanations, not_measured=None, fix_ideas=None)` | One line per hard part (`{"D1": "…"}`), up to 2 extra ones, and fix ideas; links each to an H-issue and updates HOTSPOTS.md |
| `kb_update_tool_card(tool, card)` | Store a tidied card's wording (refused over the cap) |
| `kb_abandon_case(reason)` | Close the case without finishing; writes the digest; frees the DN |
| `kb_review_case(dn, verdict, notes=None)` | Export STEP + PDF from your checked files; record what changed (§9) |

Dropped from v1: `kb_reembed_pending` (no vectors), `include_timeline`. Changed: `save_part` /
`make_drawing` take an optional `path` (§6.2).

Not an MCP tool (CAD agents never need it): the command `solidworks-mcp kb-fixed` (§10.5),
run by you or a fix session.

## 12. Storage, packaging and the change from v1

### 12.1 Layout
See §2. Every file is UTF-8 Markdown or JSON. **One writer** (the one server process), so no
locking. Each write goes to a temporary file that is then renamed over the old one, so a
crash never leaves half a file. **Backup = copy the folder.** You may keep it in git.

### 12.2 Packaging
Optional extra **`solidworks-mcp[kb]`**: `pypdfium2`, `Pillow` (for the page render and zooms).
`voyageai` and `numpy` are no longer needed. Without the extra, the PDF tools fail loud.

### 12.3 What changes in the code already on `main`
| File | v1 (built) | v2 |
|---|---|---|
| `kb/store.py` | SQLite tables, FTS5 | Read/write the Markdown files: headers, fixed sections, atomic writes |
| `kb/embed.py` | Voyage client | **Removed** |
| `kb/search.py` | FTS5 + vectors + rank fusion | Text search over `case.md` files, ~40 lines |
| `kb/cards.py` | Card fields in SQLite | Same fields and cap, rendered to / parsed from `tools\*.md` |
| `kb/log.py` | Event rows | One timeline line per call; adds duration, tokens, pause; logs `kb_*` too |
| `kb/digest.py` | — | **New**: digest numbers from a timeline (+ transcript), hard-part picking, HOTSPOTS update, `kb-fixed` |
| `.claude/settings.json`, `scripts/kb_session_hook.py` | — | **New**: the `PreToolUse` hook that records the session (§10.2) |
| `kb/dn.py`, `signatures.py`, `images.py`, `config.py` | | Kept as they are |
| `server.py` `kb_*` wrappers | | Signatures as in §11; `kb_reembed_pending` removed |

### 12.4 Testing
- **Pure tests** (any OS): file round-trips for each of the six types (write → parse →
  identical), caps, lifecycle (including abandon and re-ingest), index order, text search,
  digest numbers from a sample timeline and a sample transcript, hard-part picking,
  hotspot merging, `kb-fixed` updating all three files, a fixed failure coming back,
  atomic writes. A CAD-facing tool never returning anything from `fixes\`.
- **Integration tests** (Windows + SolidWorks): server-named saves, a real call appending
  a timeline line with duration and tokens, approval exporting STEP + PDF from edited files.

## 13. Open points

Decided:
1. **Plain Markdown, no database, no outside embedding service** (2026-10-28).
1a. **Two readers, two folders:** CAD agents never see time, tokens, hard parts or fixes;
   fix agents work from `fixes\`, with the session of every issue linked (2026-10-28).
2. Past timelines and STEP files can be shown on request.
3. The SLDPRT and SLDDRW stay in the output folder; STEP and PDF are made at approval.
4. File names are chosen by the server from the open case.
5. Shop drawing standard: SHOP-DRAWINGS.md §3–§4.

6. **The case prefix is `DN<n>`** (2026-10-28). `DTN<n>` (DTN6, DTN7, in
   `C:\Users\Sandbox\Projects\DTN*`) means **drawing test number**: test runs made while the
   MCP tools and drawings were still being built, before the knowledge base existed. They are
   full of errors from that development work, so they are **not cases**: ingest refuses a
   `DTN` name like any other, and their errors must never be read as lessons about
   the finished tools. Use them only as test material for the tools.

Still open:
7. **Contract restrictions.** Whether a contract forbids letting Claude read its drawings.

## 14. Build order

| Step | Content | Needs SolidWorks |
|---|---|---|
| ✓ | Shared commit; K0 (logging, signatures, error codes); K1 (ingest, zoom, describe); K3 (cards). On `main`, with SQLite storage | — |
| M1 | **Switch storage to Markdown** (§12.3): `store.py`, `search.py`, `cards.py`, `log.py` with duration/tokens/pause; remove Voyage; tests per file type | No |
| M2 | Digest, transcript reading, hard parts, HOTSPOTS, `kb-fixed` (`kb/digest.py`); session hook. Pure, tested on sample files | No |
| K2 | Server-named paths; `kb_finalise_case` (measurements, digest, card tidy) | Yes |
| K5 | `kb_review_case`: STEP + PDF export, measured changes | Yes |
| K6 | After the drawings branch merges: `check_drawing` read-back at finalise and review | Yes |
| later | Context probes (§8.2), only for failures that keep coming back | Yes |

## 15. Working alongside the drawings branch

This section is the same in both design documents.

### 15.1 Shared commit on `main`, before either branch starts
One small commit, so both branches start from the same contract:
- `errors.py`: `SolidWorksError` gains optional `code` and `params` (default `None`), so
  existing raises keep working unchanged.
- `units.py`: exact inch ↔ mm helpers (1 in = 25.4 mm), with pure tests.
- This document, `SHOP-DRAWINGS.md` and `job.md`, with the drawing-spec format in
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
