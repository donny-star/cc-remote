"""Directory handles that never follow a final link, for openat-style walks.

POSIX keeps an ``O_DIRECTORY | O_NOFOLLOW`` descriptor and resolves children
with ``dir_fd`` exactly as the callers did before. Windows has no ``dir_fd``:
it keeps a directory HANDLE opened on the reparse point itself and without
FILE_SHARE_DELETE. Windows refuses to rename or delete a directory (or any
ancestor) while such a handle is open, so resolving children by joined path
cannot be redirected; every child open still refuses reparse points itself.
"""
from __future__ import annotations

import errno
import os
from os import PathLike
from typing import Iterator

from cc_remote.platform import IS_WINDOWS, files

StrPath = str | PathLike[str]

_POSIX_DIR_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _win32_open_directory(path: str) -> int:
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
        return msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except BaseException:
        kernel32.CloseHandle(wintypes.HANDLE(handle))
        raise


class DirHandle:
    """An open directory whose children are resolved without following links."""

    def __init__(self, path: str, fd: int) -> None:
        self.path = path
        self.fd = fd
        self._closed = False

    @classmethod
    def open_root(cls, path: StrPath) -> "DirHandle":
        raw = os.fspath(path)
        absolute = os.path.abspath(raw)
        if IS_WINDOWS:
            return cls(absolute, _win32_open_directory(absolute))
        # POSIX opens exactly what the caller passed, as the dir_fd walks did.
        return cls(absolute, os.open(raw, _POSIX_DIR_FLAGS))

    def child_path(self, name: str) -> str:
        if IS_WINDOWS:
            drive, _ = os.path.splitdrive(name)
            if (not name or name in (".", "..") or drive
                    or "\\" in name or "/" in name):
                raise OSError(errno.EINVAL, "not a single path component", name)
        return os.path.join(self.path, name)

    def open_dir(self, name: str) -> "DirHandle":
        child = self.child_path(name)
        if IS_WINDOWS:
            return DirHandle(child, _win32_open_directory(child))
        return DirHandle(child, os.open(name, _POSIX_DIR_FLAGS, dir_fd=self.fd))

    def open_file(self, name: str, flags: int, mode: int = 0o600) -> int:
        if IS_WINDOWS:
            return files.open_nofollow(self.child_path(name), flags, mode)
        return os.open(
            name, flags | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), mode, dir_fd=self.fd)

    def mkdir(self, name: str, mode: int = 0o700) -> None:
        if IS_WINDOWS:
            os.mkdir(self.child_path(name), mode)
        else:
            os.mkdir(name, mode, dir_fd=self.fd)

    def lstat(self, name: str) -> os.stat_result:
        if IS_WINDOWS:
            return os.lstat(self.child_path(name))
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False)

    def unlink(self, name: str) -> None:
        if IS_WINDOWS:
            os.unlink(self.child_path(name))
        else:
            os.unlink(name, dir_fd=self.fd)

    def replace(self, src: str, dst: str) -> None:
        if IS_WINDOWS:
            files.replace(self.child_path(src), self.child_path(dst))
        else:
            os.replace(src, dst, src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def fsync(self) -> None:
        """Persist directory entries (POSIX); Windows has no directory fsync."""
        if not IS_WINDOWS:
            os.fsync(self.fd)

    def scandir(self) -> Iterator[os.DirEntry]:
        return os.scandir(self.path if IS_WINDOWS else self.fd)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            os.close(self.fd)

    def __enter__(self) -> "DirHandle":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = ["DirHandle"]
