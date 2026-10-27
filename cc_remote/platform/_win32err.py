"""Translate pywin32 errors into the OSError family at the platform boundary."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


def as_oserror(exc: Any, filename: str | None = None) -> OSError:
    """``OSError`` whose ``winerror`` selects the subclass (5 -> PermissionError)."""
    return OSError(None, exc.strerror, filename, exc.winerror)


@contextmanager
def translated(filename: str | None = None) -> Iterator[None]:
    """Re-raise any ``pywintypes.error`` from the block as :func:`as_oserror`."""
    import pywintypes

    try:
        yield
    except pywintypes.error as exc:
        raise as_oserror(exc, filename) from None
