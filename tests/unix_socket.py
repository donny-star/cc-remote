"""Bind a real AF_UNIX listener on every platform for socket-file fixtures.

CPython on Windows has no ``socket.AF_UNIX``, but Windows 10 1803+ supports
AF_UNIX in Winsock. The bound path is a reparse point tagged
IO_REPARSE_TAG_AF_UNIX, exactly what the official Codex daemon creates.
"""
from __future__ import annotations

import contextlib
import os
import socket
import sys
import tempfile
from pathlib import Path


def short_temp_root() -> str:
    """A temp root short enough for the 108-byte ``sun_path`` limit."""
    if sys.platform != "win32":
        return "/tmp"
    local = os.environ.get("LOCALAPPDATA")
    candidate = os.path.join(local, "Temp") if local else ""
    return candidate if candidate and os.path.isdir(candidate) else tempfile.gettempdir()


@contextlib.contextmanager
def unix_listener(path: Path):
    """Bind and listen on ``path``; close (but do not unlink) on exit."""
    if sys.platform != "win32":
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(path))
            listener.listen()
            yield listener
        return
    import ctypes

    ws2 = ctypes.WinDLL("ws2_32")

    class _WsaData(ctypes.Structure):
        _fields_ = [("data", ctypes.c_byte * 512)]

    class _SockaddrUn(ctypes.Structure):
        _fields_ = [("family", ctypes.c_ushort), ("path", ctypes.c_char * 108)]

    if ws2.WSAStartup(0x0202, ctypes.byref(_WsaData())) != 0:
        raise OSError("WSAStartup failed")
    ws2.socket.restype = ctypes.c_size_t
    ws2.bind.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
    ws2.listen.argtypes = [ctypes.c_size_t, ctypes.c_int]
    ws2.closesocket.argtypes = [ctypes.c_size_t]
    raw = os.fsencode(path)
    if len(raw) >= 108:
        raise OSError(f"AF_UNIX path too long ({len(raw)} bytes): {path}")
    handle = ws2.socket(1, 1, 0)  # AF_UNIX, SOCK_STREAM
    if handle == ctypes.c_size_t(-1).value:
        raise OSError(f"AF_UNIX socket failed: {ws2.WSAGetLastError()}")
    try:
        address = _SockaddrUn(1, raw)
        if ws2.bind(handle, ctypes.byref(address), ctypes.sizeof(address)) != 0:
            raise OSError(f"AF_UNIX bind failed: {ws2.WSAGetLastError()}")
        if ws2.listen(handle, 4) != 0:
            raise OSError(f"AF_UNIX listen failed: {ws2.WSAGetLastError()}")
        yield handle
    finally:
        ws2.closesocket(handle)
