"""File primitives with POSIX semantics on Windows.

Windows refuses to unlink or rename a file while any handle lacks
FILE_SHARE_DELETE, has no directory fsync, and has no pread/pwrite. These
helpers keep the POSIX contract callers were written against.
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import time
from os import PathLike

from cc_remote.platform import IS_WINDOWS

StrPath = str | PathLike[str]


def fchmod(fd: int, path: StrPath, mode: int) -> None:
    """``os.fchmod`` on POSIX; Windows only toggles the read-only attribute.

    On Windows this does not make a file private; use
    :func:`cc_remote.platform.perms.make_private` for that.
    """
    if IS_WINDOWS:
        os.chmod(path, mode)
    else:
        os.fchmod(fd, mode)


def fsync_directory(path: StrPath) -> None:
    """Persist a directory entry on POSIX; Windows has no directory fsync."""
    if IS_WINDOWS:
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


_ERROR_SHARING_VIOLATION = 32
_ERROR_INVALID_PARAMETER = 87
_ERROR_NOT_SUPPORTED = 50


def _posix_rename(src: str, dst: str) -> None:
    """Rename with POSIX semantics (Windows 10 1809+, NTFS).

    Plain MoveFileEx refuses to replace a target that any handle keeps open,
    even with FILE_SHARE_DELETE. FILE_RENAME_FLAG_POSIX_SEMANTICS replaces it
    and leaves existing handles reading the old content, like a POSIX inode.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    delete_access, share_all, open_existing, backup_semantics = 0x00010000, 0x7, 3, 0x02000000
    handle = kernel32.CreateFileW(
        src, delete_access, share_all, None, open_existing, backup_semantics, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        target = os.path.abspath(dst)

        class _RenameInfo(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("RootDirectory", wintypes.HANDLE),
                ("FileNameLength", wintypes.DWORD),
                ("FileName", ctypes.c_wchar * (len(target) + 1)),
            ]

        replace_if_exists, posix_semantics, file_rename_info_ex = 0x1, 0x2, 22
        info = _RenameInfo(
            replace_if_exists | posix_semantics, None, len(target) * 2, target)
        if not kernel32.SetFileInformationByHandle(
                handle, file_rename_info_ex, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


def replace(src: StrPath, dst: StrPath, *, attempts: int = 10) -> None:
    """``os.replace`` with POSIX semantics on Windows.

    Replaces targets held open with FILE_SHARE_DELETE, and rides out the
    short-lived sharing violations antivirus scanners and indexers cause.
    Falls back to MoveFileEx where POSIX renames are unsupported (FAT, old OS).
    Other access errors, such as a directory target, raise immediately.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if not IS_WINDOWS:
        os.replace(src, dst)
        return
    for attempt in range(attempts):
        try:
            try:
                _posix_rename(os.fspath(src), os.fspath(dst))
            except OSError as exc:
                if exc.winerror not in (_ERROR_INVALID_PARAMETER, _ERROR_NOT_SUPPORTED):
                    raise
                os.replace(src, dst)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) != _ERROR_SHARING_VIOLATION:
                raise
            if attempt == attempts - 1:
                raise
            time.sleep(min(0.02 * (2 ** attempt), 0.5))


def _win32_long_path(path: str) -> str:
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def force_rmtree(path: StrPath) -> None:
    """Remove a tree, clearing Windows read-only bits and long-path limits."""
    if not IS_WINDOWS:
        shutil.rmtree(path)
        return

    def clear_readonly_and_retry(func, target, _error) -> None:
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except FileNotFoundError:
            pass

    long_path = _win32_long_path(os.fspath(path))
    for attempt in range(5):
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(long_path, onexc=clear_readonly_and_retry)
            else:
                shutil.rmtree(long_path, onerror=clear_readonly_and_retry)
            return
        except OSError:
            if not os.path.exists(long_path):
                return
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def open_with_share_delete(path: StrPath, flags: int, mode: int = 0o600) -> int:
    """``os.open`` whose descriptor never blocks unlink/replace of ``path``.

    ``mode`` is ignored on Windows: a created file inherits its directory's
    ACL. Use :func:`cc_remote.platform.perms.make_private` to restrict it.
    """
    if not IS_WINDOWS:
        return os.open(path, flags, mode)

    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read, generic_write = 0x80000000, 0x40000000
    share_all = 0x1 | 0x2 | 0x4
    create_new, create_always, open_existing, open_always, truncate_existing = 1, 2, 3, 4, 5
    file_attribute_normal = 0x80

    if flags & os.O_RDWR:
        access, access_flags = generic_read | generic_write, os.O_RDWR
    elif flags & os.O_WRONLY:
        access, access_flags = generic_write, os.O_WRONLY
    else:
        access, access_flags = generic_read, os.O_RDONLY
    if flags & os.O_CREAT and flags & os.O_EXCL:
        disposition = create_new
    elif flags & os.O_CREAT and flags & os.O_TRUNC:
        disposition = create_always
    elif flags & os.O_CREAT:
        disposition = open_always
    elif flags & os.O_TRUNC:
        disposition = truncate_existing
    else:
        disposition = open_existing

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        os.fspath(path), access, share_all, None, disposition,
        file_attribute_normal, None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(
            handle, access_flags | os.O_BINARY | (flags & os.O_APPEND))
    except BaseException:
        kernel32.CloseHandle(wintypes.HANDLE(handle))
        raise


def pread(fd: int, length: int, offset: int) -> bytes:
    native = getattr(os, "pread", None)
    if native is not None:
        return native(fd, length, offset)
    saved = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        return os.read(fd, length)
    finally:
        os.lseek(fd, saved, os.SEEK_SET)


def pwrite(fd: int, data: bytes, offset: int) -> int:
    native = getattr(os, "pwrite", None)
    if native is not None:
        return native(fd, data, offset)
    saved = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        return os.write(fd, data)
    finally:
        os.lseek(fd, saved, os.SEEK_SET)
