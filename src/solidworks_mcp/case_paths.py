"""Where save_part and make_drawing write while a knowledge-base case is open.

While a case is open the server names the part and drawing from its DN number,
so the agent never types a file name and the drawing links to the part's final
name from the start (Docs/KNOWLEDGE-BASE.md §6.2). With no case open, the tools
take a path as before.
"""

import os

from .errors import SolidWorksError

KINDS = {"part": "save_part", "drawing": "make_drawing"}
EXTENSIONS = {"part": ".SLDPRT", "drawing": ".SLDDRW"}


def case_output_path(kind: str) -> str | None:
    """The open case's file for `kind` ('part' or 'drawing'), or None with no case open.

    SWMCP_OUTPUT_DIR\\DN17.SLDPRT and DN17.SLDDRW; the folder is made if missing.
    """
    from .kb import config
    from .kb import log as kb_log

    case = kb_log.knowledge_base().open_case()
    if case is None:
        return None
    folder = config.output_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder / f"{case['dn']}{EXTENSIONS[kind]}")


def resolve(kind: str, path: str | None) -> str:
    """The path `KINDS[kind]` writes to: the open case's file, else `path`."""
    case_path = case_output_path(kind)
    if case_path is None:
        if not path:
            raise SolidWorksError(f"{KINDS[kind]} needs a path (no knowledge-base case is open).")
        return path
    if path and os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(case_path)):
        raise SolidWorksError(f"A knowledge-base case is open, so the server names this file: {case_path}. "
                              f"Call {KINDS[kind]} without a path.")
    return case_path
