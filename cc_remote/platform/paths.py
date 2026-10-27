"""Virtual-environment interpreter locations per platform."""
from __future__ import annotations

from pathlib import Path

from cc_remote.platform import IS_WINDOWS


def venv_python(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def venv_pythonw(root: Path) -> Path:
    """Console-less interpreter for background services (same as python on POSIX)."""
    return root / ".venv" / ("Scripts/pythonw.exe" if IS_WINDOWS else "bin/python")
