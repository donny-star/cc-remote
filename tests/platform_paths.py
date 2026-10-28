"""Fixture paths written POSIX-style, made absolute for the running platform."""
from __future__ import annotations

import os
import sys


def native_path(path: str) -> str:
    """``"/repo/x"`` on POSIX; ``"C:\\repo\\x"`` (current drive) on Windows."""
    if sys.platform != "win32":
        return path
    return os.path.abspath(path)
