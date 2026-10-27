"""Platform seams: every POSIX/Windows difference cc-remote relies on lives here.

POSIX branches call the native API unchanged. Windows branches use pywin32 or
ctypes. Business modules import these helpers instead of branching on
``sys.platform`` themselves.
"""
from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes

    # The UCRT opens os.open() descriptors in text mode by default, which
    # rewrites "\n" as "\r\n" and treats 0x1A as EOF. Every cc-remote fd user
    # expects POSIX byte semantics, so switch the process-wide default once.
    _O_BINARY = 0x8000
    if ctypes.CDLL("ucrtbase")._set_fmode(_O_BINARY) != 0:
        raise ImportError("cannot switch the C runtime to binary file mode")
