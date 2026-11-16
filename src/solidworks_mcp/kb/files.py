"""Plain-file helpers: every knowledge-base write is atomic.

The file is written to a temporary name in the same folder and then renamed over
the old one (os.replace), so a crash leaves either the old file or the new one,
never half of one. One writer (the one server process), so no locking.
"""

import json
import os
import tempfile
from pathlib import Path


def atomic_write(path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def atomic_write_json(path, value) -> None:
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")


def read_text(path) -> str:
    return Path(path).read_text(encoding="utf-8")


def read_json(path, default=None):
    try:
        return json.loads(read_text(path))
    except FileNotFoundError:
        return default
