# DN17 · digest · 2026-10-12

Session `efb18dde-4801-4a92-8324-975a981a78c7` (Claude Code)
Transcript: C:\Users\Sandbox\.claude\projects\C--Users-Sandbox-Projects-sw2020\efb18dde-4801-4a92-8324-975a981a78c7.jsonl
62 tool calls · 3 failures · 1 h 38 min, of which 22 min waiting on you · Claude wrote 41,800 tokens · context peaked at 118,000

## Time
| | min | share of Claude's time |
|---|---|---|
| Tools running | 6.1 | 8% |
| Claude working between calls | 69.9 | 92% |
| Waiting on you (from your messages in the transcript) | 22.0 | — |

## Top tools
| Tool | Calls | Run + pause (min) | Result tokens | Claude's tokens before it |
|---|---|---|---|---|
| kb_view_region | 14 | 18.5 | 34,200 | 9,900 |
| make_drawing | 3 | 14.2 | 6,900 | 8,400 |
| list_faces | 6 | 6.0 | 5,880 | 2,100 |
| add_hole_wizard | 4 | 5.1 | 760 | 3,300 |
| check_drawing | 3 | 4.8 | 3,600 | 1,700 |

## Hard parts
Picked by the server from the measurements (longest pauses, loops, repeated failures).
Explained by Claude at finalise. The transcript has the full detail.

- **D1** · pause 6 min 15 s before call 5 (kb_describe_case), after 4 zooms · Claude wrote 4,900 tokens
  Claude: writing the spec; the counterbore depth was faded and I compared three zooms before marking it unreadable.
  → H-002
- **D2** · kb_view_region same box twice (calls 3–4)
  Claude: I lost what I had read in the first zoom and zoomed again to re-read it.
  → H-002 · ✓ fixed by `kb_view_region` returning earlier readings of a box (a1b2c3d, 2026-10-20)
- **D3** · `HOLE_FACE_NOT_FOUND` ×2 on add_hole_wizard, 3 min 10 s pause between
  Claude: '+y' matched the upright's top on the L-shape; I only found '+y:outer' after list_faces.
  → H-006
- **D4** · make_drawing → check_drawing → make_drawing, 3 rounds (calls 41–49), 14 min
  Claude: check_drawing said the cbore callout was missing though it was on the section view.
  → H-005
- **D5** · context grew 34,000 tokens at calls 9–12 (list_edges, list_faces ×3)
  Claude: I listed all faces three times to find the base top; each list stayed in context.
  → H-004
- **D6** · not measured, noted by Claude: the guide has no recipe for an inside bend radius on an L-profile.
  → H-007
