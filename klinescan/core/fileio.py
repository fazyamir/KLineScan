"""Crash-safe file writes: write a temp file in the same directory, then rename over."""

from __future__ import annotations

import os
import tempfile


def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    """Replace ``path`` with ``text``; a crash mid-write leaves the old file intact."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)  # atomic on POSIX and Windows
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
