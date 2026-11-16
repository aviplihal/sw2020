# Knowledge-base templates

One filled-in example of each file the knowledge base writes ([KNOWLEDGE-BASE.md](../KNOWLEDGE-BASE.md) §2).
The example job (DN17, an L-bracket), its numbers, session IDs and commits are invented. The
files contain no explanatory comments, so the token sizes are what an agent would really read.

The files are split by who reads them:
- **CAD agents** (doing a job) see only the files in the top part of the table, through the
  `kb_*` tools. Nothing there mentions time, tokens or fixes.
- **Fix agents** (you, or a session improving the tools) work from `fixes\`. No `kb_*` tool ever
  returns a file from that folder.

| File | Where | What it is | Who writes what | Size of this example |
|---|---|---|---|---|
| [INDEX.md](INDEX.md) | `kb\` | One line per case | Server | ~380 tokens for 12 cases |
| [case.md](case.md) | `kb\cases\DN17\` | One job: description, questions, how it was modelled, result, review, lessons | Server: header, *Drawing*, *How it was modelled*, *Result*, *Review*. Claude: *Description*, *Lessons*. You: answers and review note | ~470 tokens |
| [tool-card.md](tool-card.md) | `kb\tools\add_fillet.md` | One tool: when to use it, open known failures | Server: header line, *How to call*, counts. Claude: the rest. Fixed failures are removed | ~160 tokens |
| | | | | |
| [HOTSPOTS.md](HOTSPOTS.md) | `kb\fixes\` | **The one file for fix agents**: where the effort goes, every open issue with its jobs and sessions, and what fixed the closed ones | Server: table, issues, counts. `kb-fixed` command: the *Fixed* entries | ~730 tokens (4 open, 2 fixed) |
| [digest.md](digest.md) | `kb\fixes\DN17\` | One job's time and tokens, its hard parts explained, the session and transcript | Server: everything measured. Claude: one explanation per hard part. `kb-fixed`: the ✓ lines | ~580 tokens |
| [timeline.md](timeline.md) | `kb\fixes\DN17\` | One line per tool call: the raw record the digest, the card counts and *How it was modelled* are built from | Server | ~30 tokens per call |

Not shown: `spec.json`, the drawing spec, whose format is frozen in
[SHOP-DRAWINGS.md](../SHOP-DRAWINGS.md) §4.1 (the drawing tools read it as JSON).

Token sizes are estimated at ~3.5 characters per token.

## What a CAD job costs to look things up
INDEX.md (~30 per case) + two similar cases (~900) + cards for ~6 tools (~1,000) ≈
**2,500 tokens at 20 cases**, about the cost of one zoomed region.
