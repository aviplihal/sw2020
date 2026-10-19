"""Error types surfaced to the agent."""


class SolidWorksError(RuntimeError):
    """A readable, agent-facing failure from a SolidWorks operation.

    Raised instead of letting raw COM errors / stack traces escape, so the
    agent receives a structured message it can act on in the correction loop.

    `code` is a stable name for the kind of failure (e.g. 'FILLET_TOO_LARGE')
    and `params` the measured values behind it; both are optional, so a plain
    SolidWorksError("...") keeps working. The knowledge base groups failures
    by code; without one it falls back to the normalised message.
    """

    def __init__(self, message: str = "", code: str | None = None, params: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.params = params
