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


_FOLDER = re.compile(r"DN([1-9]\d*)", re.IGNORECASE)


def from_path(path: str) -> tuple:
    """(DN number, job folder or None) for a drawing PDF.

    DN17.pdf gives 17. A PDF inside a folder named DN17 (a job folder, such as
    Jobs/DN17/scan.pdf) gives 17 too, and that folder is where the job's files go.
    A DN17.pdf in a folder named DN18 is refused: the two disagree.
    """
    folder = ntpath.dirname(path)
    in_folder = _FOLDER.fullmatch(ntpath.basename(folder))
    if not path.lower().endswith(".pdf"):
        raise SolidWorksError(f"'{ntpath.basename(path)}' is not a PDF.", code="KB_BAD_DN_NAME")
    if in_folder is None:
        return from_filename(path), None
    number = int(in_folder.group(1))
    named = _FILE.fullmatch(ntpath.basename(path))
    if named and int(named.group(1)) != number:
        raise SolidWorksError(f"The file is DN{named.group(1)} but its folder is DN{number}: move or rename one.",
                              code="KB_DN_MISMATCH", params={"file": f"DN{named.group(1)}", "folder": f"DN{number}"})
    return number, folder


def from_written(text: str) -> int | None:
    """The number in a DN read off the paper ('DN17', 'DN 17', 'dn-017'), or None if it is not one."""
    match = _WRITTEN.fullmatch(text or "")
    return int(match.group(1)) if match else None
