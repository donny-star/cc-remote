"""The current user as POSIX uid (or its Windows placeholder) and Windows SID."""
from __future__ import annotations

import functools
import os

from cc_remote.platform import IS_WINDOWS

# Windows os.stat() reports st_uid == 0 for every file, so 0 is the only uid
# value that compares equal to on-disk ownership. Other users' processes map
# to FOREIGN_UID, which never equals current_uid().
FOREIGN_UID = 0x7FFFFFFF


def current_uid() -> int:
    return 0 if IS_WINDOWS else os.getuid()


def current_euid() -> int:
    return 0 if IS_WINDOWS else os.geteuid()


@functools.cache
def current_sid() -> str | None:
    if not IS_WINDOWS:
        return None
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    return win32security.ConvertSidToStringSid(sid)
