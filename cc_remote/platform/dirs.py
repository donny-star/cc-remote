r"""Directory handles that never follow a final link, for openat-style walks.

POSIX keeps an ``O_DIRECTORY | O_NOFOLLOW`` descriptor and resolves children
with ``dir_fd`` exactly as the callers did before. Windows has no ``dir_fd``:
it keeps a directory HANDLE opened on the reparse point itself and without
FILE_SHARE_DELETE. Windows refuses to rename or delete that directory or any
of its real ancestors while such a handle is open; every child open still
refuses reparse points itself.

The pin does not cover what the caller's path traversed to reach the
directory: a junction, directory symlink, mount point, or subst/mapped drive
letter above it can be removed and recreated pointing elsewhere. Windows
children are therefore resolved against a private path read back from the
pinned handle (``\\?\Volume{GUID}\...``, or ``\\?\C:\...`` / ``\\?\UNC\...``
where the volume has no GUID path), never against the caller's path, which
``path`` keeps for display and :meth:`DirHandle.child_path`. The ``\\?\``
prefix also disables Win32 name normalization, and child names Win32 would
otherwise alias (trailing dots or spaces, streams, DOS device names) are
refused.

The pinning semantics are verified on local NTFS. Remote, SMB-backed, 9P or
userspace filesystems may not honour the missing FILE_SHARE_DELETE pin.
"""
from __future__ import annotations

import errno
import functools
import os
import warnings
from os import PathLike
from typing import Iterator

from cc_remote.platform import IS_WINDOWS, files

StrPath = str | PathLike[str]

_POSIX_DIR_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)

_RESERVED_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in range(1, 10)])


@functools.cache
def _get_final_path_name():
    from ctypes import wintypes

    function = files._kernel32().GetFinalPathNameByHandleW
    function.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    function.restype = wintypes.DWORD
    return function


def _win32_final_path(handle: int, flags: int) -> str:
    import ctypes

    size = 260
    while True:
        buffer = ctypes.create_unicode_buffer(size)
        length = _get_final_path_name()(handle, buffer, size, flags)
        if length == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        if length < size:
            return buffer.value
        size = length  # buffer too small: length is the size needed with the NUL


def _win32_private_path(handle: int) -> str:
    """The pinned directory's own path, independent of links the caller used."""
    file_name_normalized, volume_name_dos, volume_name_guid = 0x0, 0x0, 0x1
    try:
        return _win32_final_path(handle, file_name_normalized | volume_name_guid)
    except OSError:
        pass  # e.g. a network share has no volume GUID path
    path = _win32_final_path(handle, file_name_normalized | volume_name_dos)
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _win32_open_directory(path: str) -> tuple[int, str]:
    """Open and pin a directory; return ``(fd, private path)``."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    file_list_directory, file_read_attributes = 0x1, 0x80
    read_control, synchronize = 0x00020000, 0x00100000
    share_read_write = 0x1 | 0x2  # no FILE_SHARE_DELETE: pins the directory
    open_existing, backup_semantics = 3, 0x02000000
    kernel32 = files._kernel32()
    handle = kernel32.CreateFileW(
        path, file_list_directory | file_read_attributes | read_control | synchronize,
        share_read_write, None, open_existing,
        backup_semantics | files._FILE_FLAG_OPEN_REPARSE_POINT, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = files._win32_handle_attributes(handle)
        if attributes & files._FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(errno.ELOOP, "refusing to open a reparse point", path)
        if not attributes & 0x10:  # FILE_ATTRIBUTE_DIRECTORY
            raise NotADirectoryError(errno.ENOTDIR, "not a directory", path)
        private = _win32_private_path(handle)
        return msvcrt.open_osfhandle(handle, os.O_RDONLY), private
    except BaseException:
        kernel32.CloseHandle(wintypes.HANDLE(handle))
        raise


def _win32_check_component(name: str) -> None:
    drive, _ = os.path.splitdrive(name)
    if (not name or name in (".", "..") or drive or "\\" in name or "/" in name
            or ":" in name or name != name.rstrip(" .")
            or name.split(".", 1)[0].rstrip(" ").upper() in _RESERVED_DEVICE_NAMES):
        raise OSError(errno.EINVAL, "not a single path component", name)


class DirHandle:
    """An open directory whose children are resolved without following links."""

    def __init__(self, path: str, fd: int, os_path: str | None = None) -> None:
        self.path = path
        self.fd = fd
        # Windows resolves children against this private path (see module doc).
        self._os_path = path if os_path is None else os_path
        self._closed = False

    @classmethod
    def open_root(cls, path: StrPath) -> "DirHandle":
        raw = os.fspath(path)
        absolute = os.path.abspath(raw)
        if IS_WINDOWS:
            return cls(absolute, *_win32_open_directory(absolute))
        # POSIX opens exactly what the caller passed, as the dir_fd walks did.
        return cls(absolute, os.open(raw, _POSIX_DIR_FLAGS))

    def child_path(self, name: str) -> str:
        """The caller-facing path of ``name``, for display and messages."""
        if IS_WINDOWS:
            _win32_check_component(name)
        return os.path.join(self.path, name)

    def _os_child(self, name: str) -> str:
        _win32_check_component(name)
        return os.path.join(self._os_path, name)

    def open_dir(self, name: str) -> "DirHandle":
        child = self.child_path(name)
        if IS_WINDOWS:
            return DirHandle(child, *_win32_open_directory(self._os_child(name)))
        return DirHandle(child, os.open(name, _POSIX_DIR_FLAGS, dir_fd=self.fd))

    def open_file(self, name: str, flags: int, mode: int = 0o600) -> int:
        if IS_WINDOWS:
            return files.open_nofollow(self._os_child(name), flags, mode)
        return os.open(
            name, flags | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), mode, dir_fd=self.fd)

    def mkdir(self, name: str, mode: int = 0o700) -> None:
        if IS_WINDOWS:
            os.mkdir(self._os_child(name), mode)
        else:
            os.mkdir(name, mode, dir_fd=self.fd)

    def lstat(self, name: str) -> os.stat_result:
        if IS_WINDOWS:
            return os.lstat(self._os_child(name))
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False)

    def unlink(self, name: str) -> None:
        if IS_WINDOWS:
            os.unlink(self._os_child(name))
        else:
            os.unlink(name, dir_fd=self.fd)

    def replace(self, src: str, dst: str) -> None:
        if IS_WINDOWS:
            files.replace(self._os_child(src), self._os_child(dst))
        else:
            os.replace(src, dst, src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def fsync(self) -> None:
        """Persist directory entries (POSIX); Windows has no directory fsync."""
        if not IS_WINDOWS:
            os.fsync(self.fd)

    def scandir(self) -> Iterator[os.DirEntry]:
        """Iterate the directory like ``os.scandir`` (also a context manager).

        ``entry.path`` differs per platform: the absolute private path on
        Windows, the bare name on POSIX (a descriptor scan). Use ``entry.name``
        with this handle's methods instead.
        """
        return os.scandir(self._os_path if IS_WINDOWS else self.fd)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            os.close(self.fd)

    def __del__(self) -> None:
        if not getattr(self, "_closed", True):
            warnings.warn(f"unclosed DirHandle {self.path!r}", ResourceWarning, source=self)
            try:
                self.close()
            except OSError:
                pass

    def __enter__(self) -> "DirHandle":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = ["DirHandle"]
