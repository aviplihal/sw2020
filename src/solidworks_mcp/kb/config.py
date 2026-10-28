"""Where the knowledge base and the case output files live."""

import os
from pathlib import Path

from ..errors import SolidWorksError


def kb_dir() -> Path:
    """%LOCALAPPDATA%\\solidworks-mcp\\kb, or SWMCP_KB_DIR."""
    override = os.environ.get("SWMCP_KB_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "solidworks-mcp" / "kb"


def output_dir() -> Path:
    """The folder a case's SLDPRT and SLDDRW are saved to for review (SWMCP_OUTPUT_DIR)."""
    folder = os.environ.get("SWMCP_OUTPUT_DIR")
    if not folder:
        raise SolidWorksError("Set the environment variable SWMCP_OUTPUT_DIR to the folder where a case's "
                              "part and drawing go for your review (e.g. C:\\SW-Output), then restart the server.")
    return Path(folder)
