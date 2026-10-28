"""DN numbers: a case's ID comes from the drawing's file name, never from guessing."""

import ntpath
import re

from ..errors import SolidWorksError

_FILE = re.compile(r"DN([1-9]\d*)\.pdf", re.IGNORECASE)
_WRITTEN = re.compile(r"\s*DN\s*[-#.]?\s*0*([1-9]\d*)\s*", re.IGNORECASE)


def name(number: int) -> str:
    return f"DN{number}"


def from_filename(path: str) -> int:
    """The DN number in a file named DN<number>.pdf; anything else is refused."""
    base = ntpath.basename(path)  # splits at \ and /, on any OS
    match = _FILE.fullmatch(base)
    if not match:
        raise SolidWorksError(f"'{base}' is not named DN<number>.pdf (e.g. DN17.pdf, no leading zeros). "
                              "The file name gives the case its DN number: rename the file and try again.",
                              code="KB_BAD_DN_NAME")
    return int(match.group(1))


def from_written(text: str) -> int | None:
    """The number in a DN read off the paper ('DN17', 'DN 17', 'dn-017'), or None if it is not one."""
    match = _WRITTEN.fullmatch(text or "")
    return int(match.group(1)) if match else None
