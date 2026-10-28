You are continuing the knowledge-base work on the SolidWorks MCP server (repo github.com/aviplihal/sw2020), now on the Windows machine where SOLIDWORKS 2020 runs. So far it was written on a Mac, where nothing touching SolidWorks could run.

## Branch
- Work on **`feat/knowledge-base`**. Check it out first: `git fetch origin` then `git switch feat/knowledge-base`. Do not commit to `main`.
- K0, K1 and K3 are already merged into `main` (PR #2). Continue on `feat/knowledge-base`, which is level with `main`.
- The shop-drawings branch (`feat/shop-drawings`) starts from `main`. It is built by a separate session with its own prompt; it owns `make_drawing` and the new `check_drawing`, and it merges next, before the rest of this branch.

## Read first
1. `CLAUDE.md`: project rules (early binding, all COM on one thread, mm/deg at the boundary, fail loud, measure back).
2. `Docs/KNOWLEDGE-BASE.md`: the design. Build steps K0–K6 are in §14, and §15 covers working alongside the drawings branch.
3. `Docs/JOB-BRIEF.md`: the workflow the agent in Claude Desktop follows.
4. `Docs/SHOP-DRAWINGS.md` §8 (the same rules as KNOWLEDGE-BASE.md §15).
5. `Docs/PROGRESS.md`: the SOLIDWORKS 2020 conversion plan. Log your findings there.

## What is done (4 commits on top of the docs)
- `fdf5171` **shared base**:
  - `SolidWorksError(message, code=None, params=None)`
  - `units.in_to_mm` / `mm_to_in`
  - `save_part` and `make_drawing` take an optional `path`, resolved through `case_paths.resolve(kind, path)`. Its `case_output_path(kind)` is still a stub that returns None.
- `a43f0a7` **K0**:
  - `src/solidworks_mcp/kb/` with `store.py` (SQLite in WAL mode: cases, events, error signatures, tool cards, vectors, FTS5), `dn.py`, `signatures.py`, `log.py`, `config.py`.
  - `server._call` logs every tool call through `_log_call`, and only when the COM worker exists (i.e. on Windows).
  - `session.connect` now stores `self.sw_revision`.
  - Fillet, full-round, chamfer and shell failures carry error codes.
  - `run_guarded` keeps the code when it re-raises.
- `0075448` **K1**:
  - `kb/images.py`, `kb/embed.py` (Voyage, guarded by `SWMCP_VOYAGE_OPTED_OUT=1`), `kb/search.py`, `kb/cases.py` (`CaseTools`).
  - New tools: `kb_ingest_drawing`, `kb_view_region`, `kb_describe_case`, `kb_update_spec`, `kb_search`, `kb_get_case`, `kb_abandon_case`, `kb_reembed_pending`.
  - New optional extra `[kb]`, which `dev` includes.
  - `tests/test_logic.py`'s argument-forwarding test now skips `kb_*` tools; `tests/test_kb_tools.py` covers them.
- `1164e38` **K3**:
  - `kb/cards.py`; new tools `kb_tool_card` and `kb_update_tool_card`.
  - `kb_hint` on error results.
  - `CaseTools._cards_to_tidy(case)`, ready for finalise.

**Verified only on macOS:** 121 tests in `tests/test_kb_*.py`, `test_case_paths.py`, `test_units.py`, `test_packaging.py` and `test_server_portability.py`, run with `--noconftest`. **Nothing has run on Windows or against SolidWorks yet**, including the logging hook inside a real tool call, `sw_revision`, and the optional-path change to `save_part` / `make_drawing`. Nothing has been sent to the real Voyage API either (the tests use a fake embedder; the code targets the `voyageai` 0.5 SDK with models `voyage-multimodal-3.5` and `voyage-4`).

## Do next, in order
1. **Setup and baseline**
   - `py -3.11 -m venv .venv` (or newer Python), then `.\.venv\Scripts\python.exe -m pip install -e .[dev]`.
   - Check that FTS5 works: `python -c "import sqlite3; sqlite3.connect(':memory:').execute('create virtual table t using fts5(x)')"`.
   - Run `pytest -m "not solidworks"`. Everything should pass, including `test_logic.py`, which the Mac couldn't run. Fix anything that fails and say what it was.
2. **Phase 1 of `Docs/PROGRESS.md`** on SOLIDWORKS 2020:
   - `scripts/probe_connection.py`
   - `solidworks-mcp --selftest`
   - full `pytest`, with the baseline saved in the PROGRESS.md log
   - Confirm in particular that `save_part`, `export` (STEP), `make_drawing` (with a path) and `tests/test_drawing.py` work. K2 and K5 depend on them.
   - Confirm a real tool call writes an event to `%LOCALAPPDATA%\solidworks-mcp\kb\kb.sqlite` with `sw_revision` filled in (28.x).
3. **K2: server-chosen paths and `kb_finalise_case`** (design §6, §6.2, §11):
   - `case_paths.case_output_path(kind)`: with an open case (`kb.log.knowledge_base().open_case()`), return `config.output_dir() / "DN17.SLDPRT"` or `"DN17.SLDDRW"`; otherwise None. It must stay cheap and must never fail a call when no knowledge base exists.
   - `kb_finalise_case()`:
     1. Check that both files exist in `SWMCP_OUTPUT_DIR` and that the part has no unsaved changes.
     2. Record the measurements (mass properties and bounding box) in `finalise_measurements`.
     3. Move the case to `finalised` (which closes it).
     4. Reindex it.
     5. Return the file names, `_cards_to_tidy(case)`, and the reminder: review and fix the files in place, say "DN17 approved", and only then move them out.
   - **Any COM work must run on the COM worker thread** (`_worker.call(...)`). The current `kb_*` tools run synchronously on the event loop because they do no COM; finalise and review are different. Don't call session methods from the event loop thread.
   - The drawings branch owns `make_drawing`. Only the `case_paths.resolve` line at its top is shared.
4. **K4: context probes** (§8.2): fillet and chamfer first. A probe runs inside `run_guarded` after the rollback, stores its measurements in `events.probe`, and never hides the original error. Add error codes to the rest of each tool family as you go.
5. **K5: `kb_review_case(dn, verdict, notes)`** (§9):
   - Refuse if either file is missing from the output folder.
   - Open the checked SLDPRT, export `cases\DN17\DN17.step`. Open the SLDDRW, save `DN17 drawing.pdf`. Close both without saving.
   - Use separate worker calls, so no single call hits the 120 s timeout.
   - Compare the part's measurements with `finalise_measurements` and store the changes.
   - Set `approved` / `changes_needed`, plus the review notes, and reindex.
   - Never copy the SLDPRT or SLDDRW into the knowledge base.
6. **K6** waits until the drawings branch has merged (§15.3).

## Rules
- There is one SolidWorks instance. **Never run integration tests while another agent runs the drawings branch's tests.**
- Don't bump the version. CHANGELOG entries go under `## [Unreleased]` → `### Knowledge base`. Leave the README tool count alone (the branch that merges second fixes it).
- Follow the pattern in `CLAUDE.md`: session method → server wrapper with a precise docstring → pure test plus a `solidworks`-marked integration test checked against a hand calculation → README / guide / CHANGELOG / PROGRESS log.
- Commits use Conventional Commits in plain words and end with the attribution line your environment gives.
- Report test results as they are. If something couldn't be verified, say so.

## Environment variables the user sets on this machine
- `SWMCP_OUTPUT_DIR`, e.g. `C:\SW-Output`
- `VOYAGE_API_KEY`
- `SWMCP_VOYAGE_OPTED_OUT=1`, only after the Voyage account has opted out (design §5.4)
- `SWMCP_KB_DIR`, optional
