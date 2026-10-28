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
import time
import warnings
from os import PathLike
from typing import Iterator

from cc_remote.platform import IS_WINDOWS, files, perms

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


def _win32_open_directory(path: str, *, write_dac: bool = False) -> tuple[int, str]:
    """Open and pin a directory; return ``(fd, private path)``."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    file_list_directory, file_read_attributes = 0x1, 0x80
    read_control, synchronize = 0x00020000, 0x00100000
    share_read_write = 0x1 | 0x2  # no FILE_SHARE_DELETE: pins the directory
    open_existing, backup_semantics, write_dac_access = 3, 0x02000000, 0x00040000
    access = file_list_directory | file_read_attributes | read_control | synchronize
    if write_dac:
        access |= write_dac_access
    kernel32 = files._kernel32()
    handle = kernel32.CreateFileW(
        path, access, share_read_write, None, open_existing,
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


def lock_path(directory: StrPath, name: str) -> str:
    """Where to lock ``directory`` when it may be renamed while locked.

    POSIX keeps the lock inside the directory, as before. Windows refuses to
    rename a directory that contains an open handle, so the lock is the
    sibling file ``.<directory name>.<name>`` next to it.
    """
    directory = os.fspath(directory)
    if not IS_WINDOWS:
        return os.path.join(directory, name)
    parent, base = os.path.split(os.path.normpath(directory))
    return os.path.join(parent, f".{base}.{name}")


def open_lock(
    directory: StrPath, name: str, flags: int, mode: int = 0o600, *,
    attempts: int = 20, delay: float = 0.01,
) -> int:
    """``os.open`` of :func:`lock_path` that rides out a concurrent discard.

    POSIX opens once, as before. On Windows a :func:`discard_lock` that is
    deleting the unused file makes opens fail with ``PermissionError`` until
    it completes, so those are retried ``attempts`` times ``delay`` apart.
    """
    path = lock_path(directory, name)
    if not IS_WINDOWS:
        return os.open(path, flags, mode)
    for attempt in range(attempts):
        try:
            return os.open(path, flags, mode)
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
    raise ValueError("attempts must be at least 1")


def discard_lock(directory: StrPath, name: str) -> None:
    """Best-effort removal of a sibling lock once ``directory`` is gone.

    POSIX locks were removed with the directory. On Windows plain handles
    lack FILE_SHARE_DELETE, so the unlink fails while anyone still holds or
    waits on the lock; it never splits a lock that is in use.
    """
    if not IS_WINDOWS:
        return
    try:
        os.unlink(lock_path(directory, name))
    except (FileNotFoundError, PermissionError):
        pass


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

    def make_private(self) -> None:
        """Restrict this directory to its owner without the caller's path.

        POSIX is ``os.fchmod(fd, 0o700)``. Windows cannot reopen a directory
        handle with WRITE_DAC, so unless the directory is already private it
        opens the pinned private path again, checks that this is the same
        directory, and sets the protected owner-only DACL through that
        handle. Skipping a private directory avoids re-propagating the DACL
        to every child.
        """
        if not IS_WINDOWS:
            perms.make_private_dir_fd(self.fd)
            return
        if perms.is_private_fd(self.fd):
            return
        fd, _ = _win32_open_directory(self._os_path, write_dac=True)
        try:
            pinned, reopened = os.fstat(self.fd), os.fstat(fd)
            if (pinned.st_dev, pinned.st_ino) != (reopened.st_dev, reopened.st_ino):
                raise OSError(errno.ESTALE, "pinned directory changed", self.path)
            perms.make_private_dir_fd(fd)
        finally:
            os.close(fd)

    def lstat(self, name: str) -> os.stat_result:
        if IS_WINDOWS:
            return os.lstat(self._os_child(name))
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False)

    def unlink(self, name: str) -> None:
        """Remove a non-directory name; a link itself, never its target.

        Like POSIX unlink, a read-only file is removed: Windows clears the
        attribute through a no-follow handle first.
        """
        if IS_WINDOWS:
            path = self._os_child(name)
            try:
                os.unlink(path)
            except PermissionError:
                if not files._win32_unlink_read_only(path):
                    raise
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


__all__ = ["DirHandle", "discard_lock", "lock_path", "open_lock"]
