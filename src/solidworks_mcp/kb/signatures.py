"""Error signatures: the same failure gets the same key, whatever the names and numbers in it.

The server's own errors carry a stable code (SolidWorksError.code), which is
the signature. Anything else is normalised: feature, sketch and dimension
names, quoted values, paths, HRESULTs and numbers become placeholders, and the
result is hashed. Pure and deterministic.
"""

import hashlib
import re

_RULES = [
    (re.compile(r"'[^']*'|\"[^\"]*\""), "<Q>"),  # first: quoted names and paths may hold spaces
    (re.compile(r"[A-Za-z]:[\\/]\S*|(?<![\w.])(?:/[\w.\-~]+){2,}"), "<PATH>"),
    (re.compile(r"\b[\w-]+@[\w-]+(?:@[\w.-]+)?"), "<DIM>"),
    (re.compile(r"\b0x[0-9A-Fa-f]{8}\b|-?\b21474\d{5}\b"), "<HRESULT>"),
    (re.compile(r"\b(?:3D)?Sketch\d+\b"), "<SKETCH>"),
    (re.compile(r"\b[A-Za-z][A-Za-z]*(?:-[A-Za-z]+)*\d+\b"), "<FEATURE>"),
    (re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"), "<N>"),
    (re.compile(r"\s+"), " "),
]


def normalise(message: str) -> str:
    text = message or ""
    for pattern, placeholder in _RULES:
        text = pattern.sub(placeholder, text)
    return text.strip()


def signature(code: str | None, message: str) -> str:
    """The key failures are grouped by: the error's code, else a hash of its normalised text."""
    if code:
        return code
    return "T:" + hashlib.sha1(normalise(message).encode("utf-8")).hexdigest()[:12]
