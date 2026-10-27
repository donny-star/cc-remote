"""Advisory whole-file locks with ``fcntl.flock`` semantics on every platform.

Windows byte-range locks are mandatory, so locking real file content would make
other handles' reads fail. The Windows backend therefore locks one byte far
beyond any real data, which keeps file content readable while the lock is held.

Windows differences callers must respect:

- There is no ``LOCK_SH``; only ``LOCK_EX`` (optionally ``| LOCK_NB``) and
  ``LOCK_UN`` are supported.
- Locks do not upgrade or re-enter: taking ``LOCK_EX`` twice on the same handle
  deadlocks (or fails with ``LOCK_NB``) instead of being a no-op.
- A lock belongs to the process that took it. A descriptor inherited by a child
  process cannot re-take, release or verify the parent's lock.
"""
from __future__ import annotations

import errno
from typing import IO, Any

from cc_remote.platform import IS_WINDOWS

if IS_WINDOWS:
    import msvcrt

    import pywintypes
    import win32con
    import win32file

    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    _ERROR_LOCK_VIOLATION = 33
    _ERROR_NOT_LOCKED = 158
    _OFFSET_LOW = 0xFFFFFFFE
    _OFFSET_HIGH = 0x7FFFFFFF

    def _overlapped() -> pywintypes.OVERLAPPED:
        overlapped = pywintypes.OVERLAPPED()
        overlapped.Offset = _OFFSET_LOW
        overlapped.OffsetHigh = _OFFSET_HIGH
        return overlapped

    def flock(fd: int | IO[Any], operation: int) -> None:
        if not isinstance(fd, int):
            fd = fd.fileno()  # fcntl.flock also accepts file objects
        handle = msvcrt.get_osfhandle(fd)
        base = operation & ~LOCK_NB
        if base == LOCK_UN:
            try:
                win32file.UnlockFileEx(handle, 1, 0, _overlapped())
            except pywintypes.error as exc:
                if exc.winerror != _ERROR_NOT_LOCKED:
                    raise OSError(None, exc.strerror, None, exc.winerror) from None
            return
        if base != LOCK_EX:
            raise ValueError(f"unsupported lock operation: {operation}")
        flags = win32con.LOCKFILE_EXCLUSIVE_LOCK
        if operation & LOCK_NB:
            flags |= win32con.LOCKFILE_FAIL_IMMEDIATELY
        try:
            win32file.LockFileEx(handle, flags, 1, 0, _overlapped())
        except pywintypes.error as exc:
            if exc.winerror == _ERROR_LOCK_VIOLATION:
                raise BlockingIOError(errno.EWOULDBLOCK, "file is locked") from None
            raise OSError(None, exc.strerror, None, exc.winerror) from None
else:
    import fcntl

    LOCK_EX = fcntl.LOCK_EX
    LOCK_NB = fcntl.LOCK_NB
    LOCK_UN = fcntl.LOCK_UN
    flock = fcntl.flock

__all__ = ["LOCK_EX", "LOCK_NB", "LOCK_UN", "flock"]
