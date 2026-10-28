"""Knowledge base: earlier drawing jobs (cases) and one card per tool.

Design: Docs/KNOWLEDGE-BASE.md. The storage, logging and error signatures use
only the standard library, so every tool call is logged even without the
optional `solidworks-mcp[kb]` extra; rendering, embedding and vector search
need the extra and fail loud without it.
"""
