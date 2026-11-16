"""Knowledge base: earlier drawing jobs (cases) and one card per tool, as plain Markdown files.

Design: Docs/KNOWLEDGE-BASE.md. Storage, logging, search, cards, digests and hotspots use only
the standard library, so every tool call is logged even without the optional
`solidworks-mcp[kb]` extra; only rendering and zooming a drawing need the extra and fail loud without it.
"""
