"""File primitives with POSIX semantics on Windows.

Windows refuses to unlink or rename a file while any handle lacks
FILE_SHARE_DELETE, has no directory fsync, and has no pread/pwrite. These
helpers keep the POSIX contract callers were written against.
"""
from __future__ import annotations

import errno
import functools
import os
import secrets
import shutil
import stat
import sys
import tempfile
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
    Like POSIX rename, a link at ``src`` is renamed itself rather than
    followed, and a read-only target is replaced.
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
        src, delete_access, share_all, None, open_existing,
        backup_semantics | _FILE_FLAG_OPEN_REPARSE_POINT, None)
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

        replace_if_exists, posix_semantics, ignore_readonly = 0x1, 0x2, 0x40
        file_rename_info_ex = 22
        info = _RenameInfo(
            replace_if_exists | posix_semantics | ignore_readonly,
            None, len(target) * 2, target)
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


_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_ATTRIBUTE_TAG_INFO = 9


@functools.cache
def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


def _win32_handle_attributes(handle: int) -> int:
    import ctypes
    from ctypes import wintypes

    class _AttributeTagInfo(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    info = _AttributeTagInfo()
    if not _kernel32().GetFileInformationByHandleEx(
            handle, _FILE_ATTRIBUTE_TAG_INFO, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(info.FileAttributes)


_FILE_ATTRIBUTE_READONLY = 0x1
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_NORMAL = 0x80
_FILE_BASIC_INFO = 0
_FILE_DISPOSITION_INFO = 4


def _win32_set_read_only(handle: int, read_only: bool) -> None:
    """Set or clear FILE_ATTRIBUTE_READONLY through an open handle.

    The handle needs FILE_READ_ATTRIBUTES and FILE_WRITE_ATTRIBUTES; the
    file's timestamps are left alone.
    """
    import ctypes
    from ctypes import wintypes

    attributes = _win32_handle_attributes(handle)
    wanted = (attributes | _FILE_ATTRIBUTE_READONLY if read_only
              else attributes & ~_FILE_ATTRIBUTE_READONLY)
    if wanted == attributes:
        return

    class _BasicInfo(ctypes.Structure):
        _fields_ = [
            ("CreationTime", ctypes.c_longlong),
            ("LastAccessTime", ctypes.c_longlong),
            ("LastWriteTime", ctypes.c_longlong),
            ("ChangeTime", ctypes.c_longlong),
            ("FileAttributes", wintypes.DWORD),
        ]

    # Zero times mean "unchanged"; zero attributes would too, hence NORMAL.
    info = _BasicInfo(0, 0, 0, 0, wanted or _FILE_ATTRIBUTE_NORMAL)
    if not _kernel32().SetFileInformationByHandle(
            handle, _FILE_BASIC_INFO, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())


def _win32_unlink_read_only(path: str) -> bool:
    """Delete ``path`` if it is a read-only non-directory; False otherwise.

    The name itself is opened (a link is never followed), its read-only
    attribute cleared and the delete requested through the same handle, so
    nothing is resolved twice. Returns False, changing nothing, when the
    name cannot be opened or is not a read-only non-directory.
    """
    import ctypes
    from ctypes import wintypes

    delete_access, read_attributes, write_attributes = 0x00010000, 0x80, 0x100
    share_all, open_existing = 0x7, 3
    kernel32 = _kernel32()
    handle = kernel32.CreateFileW(
        path, delete_access | read_attributes | write_attributes, share_all, None,
        open_existing, _FILE_FLAG_OPEN_REPARSE_POINT, None)
    if handle == wintypes.HANDLE(-1).value:
        return False
    try:
        attributes = _win32_handle_attributes(handle)
        if (not attributes & _FILE_ATTRIBUTE_READONLY
                or attributes & _FILE_ATTRIBUTE_DIRECTORY):
            return False
        _win32_set_read_only(handle, False)
        delete = wintypes.BOOLEAN(True)
        if not kernel32.SetFileInformationByHandle(
                handle, _FILE_DISPOSITION_INFO, ctypes.byref(delete), ctypes.sizeof(delete)):
            error = ctypes.WinError(ctypes.get_last_error())
            try:
                _win32_set_read_only(handle, True)
            except OSError:
                pass
            raise error
        return True
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def _win32_create(path: StrPath, flags: int, *, refuse_reparse: bool) -> int:
    """CreateFileW with POSIX open() dispositions and FILE_SHARE_DELETE.

    With ``refuse_reparse`` the reparse point itself is opened and refused.
    CREATE_ALWAYS would supersede a reparse point before it can be inspected
    (an AF_UNIX socket file became a regular file), so truncation then
    happens only after the check.
    """
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
    attributes = file_attribute_normal
    truncate_after_check = False
    if refuse_reparse:
        attributes |= _FILE_FLAG_OPEN_REPARSE_POINT
        if disposition == create_always:
            disposition, truncate_after_check = open_always, True
        elif disposition == truncate_existing:
            disposition, truncate_after_check = open_existing, True

    kernel32 = _kernel32()
    handle = kernel32.CreateFileW(
        os.fspath(path), access, share_all, None, disposition, attributes, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if refuse_reparse and _win32_handle_attributes(handle) & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(errno.ELOOP, "refusing to open a reparse point", os.fspath(path))
        fd = msvcrt.open_osfhandle(
            handle, access_flags | os.O_BINARY | (flags & os.O_APPEND))
    except BaseException:
        kernel32.CloseHandle(wintypes.HANDLE(handle))
        raise
    if truncate_after_check:
        try:
            os.ftruncate(fd, 0)
        except BaseException:
            os.close(fd)
            raise
    return fd


def open_with_share_delete(path: StrPath, flags: int, mode: int = 0o600) -> int:
    """``os.open`` whose descriptor never blocks unlink/replace of ``path``.

    ``mode`` is ignored on Windows: a created file inherits its directory's
    ACL. Use :func:`cc_remote.platform.perms.make_private` to restrict it.
    """
    if not IS_WINDOWS:
        return os.open(path, flags, mode)
    return _win32_create(path, flags, refuse_reparse=False)


def is_reparse_point(path: StrPath) -> bool:
    """True when ``path`` itself is a link (POSIX) or a reparse point (Windows).

    On Windows, name-surrogate reparse points (symlinks, junctions) and
    AF_UNIX socket / AppExecLink files are always detected. Filter-managed
    tags (cloud and dedup placeholders) may report False because ``os.lstat``
    lets the filter resolve them, so do not use this to predict whether
    :func:`open_nofollow` will succeed: call it and handle ``OSError``.
    Raises FileNotFoundError when ``path`` does not exist.
    """
    info = os.lstat(path)
    if not IS_WINDOWS:
        return stat.S_ISLNK(info.st_mode)
    return bool(info.st_file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


_IO_REPARSE_TAG_AF_UNIX = 0x80000023


def is_unix_socket(info: os.stat_result) -> bool:
    """Whether an ``lstat`` result describes a Unix-domain socket file.

    Windows AF_UNIX sockets report S_IFREG plus the AF_UNIX reparse tag.
    """
    if not IS_WINDOWS:
        return stat.S_ISSOCK(info.st_mode)
    return getattr(info, "st_reparse_tag", 0) == _IO_REPARSE_TAG_AF_UNIX


def open_nofollow(path: StrPath, flags: int, mode: int = 0o600) -> int:
    """Open a regular file without following a final link.

    POSIX adds ``O_NOFOLLOW | O_CLOEXEC``. Windows opens the reparse point
    itself and refuses any reparse tag (file symlinks, OneDrive/dedup placeholders,
    AF_UNIX socket files, AppExecLink) with ``OSError(ELOOP)``; otherwise the
    descriptor is the share-delete, binary one of
    :func:`open_with_share_delete`. Like POSIX ``O_NOFOLLOW``, only the last
    path component is protected.

    On Windows, directories, junctions and directory symlinks cannot be opened
    and raise ``PermissionError`` rather than ELOOP; ``O_CREAT | O_EXCL`` on an
    existing junction also raises ``PermissionError``. ``mode`` is ignored on
    Windows: call :func:`cc_remote.platform.perms.make_private` after creating.
    """
    if not IS_WINDOWS:
        return os.open(path, flags | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), mode)
    return _win32_create(path, flags, refuse_reparse=True)


_ERROR_ACCESS_DENIED = 5
_ERROR_FILE_EXISTS = 80
_ERROR_ALREADY_EXISTS = 183


def create_private_temp(
    directory: StrPath, prefix: str = "", suffix: str = "",
) -> tuple[int, str]:
    """Create a new owner-only temporary file; return ``(fd, absolute path)``.

    POSIX is ``tempfile.mkstemp`` (mode 0600). Windows creates the file with
    a protected current-user + SYSTEM DACL in the CreateFile call itself, so
    no other principal can open it in between, even when ``directory`` is
    readable by others. The binary descriptor is not inherited and shares
    delete like :func:`open_with_share_delete`.
    """
    if not IS_WINDOWS:
        return tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=directory)
    import msvcrt

    import pywintypes
    import win32api
    import win32file
    import win32security

    from cc_remote.platform import perms
    from cc_remote.platform._win32err import as_oserror

    directory = os.path.abspath(os.fspath(directory))
    try:
        attributes = win32security.SECURITY_ATTRIBUTES()
        attributes.bInheritHandle = False
        descriptor = attributes.SECURITY_DESCRIPTOR
        descriptor.SetSecurityDescriptorDacl(True, perms._private_dacl(directory=False), False)
        descriptor.SetSecurityDescriptorControl(
            win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED)
    except pywintypes.error as exc:
        raise as_oserror(exc, directory) from None
    for _ in range(tempfile.TMP_MAX):
        path = os.path.join(directory, prefix + secrets.token_hex(8) + suffix)
        try:
            handle = win32file.CreateFile(
                path, win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE
                | win32file.FILE_SHARE_DELETE,
                attributes, win32file.CREATE_NEW, win32file.FILE_ATTRIBUTE_NORMAL, None)
        except pywintypes.error as exc:
            if exc.winerror in (_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS):
                continue
            if exc.winerror == _ERROR_ACCESS_DENIED and os.path.isdir(path):
                continue  # the name is taken by a directory
            raise as_oserror(exc, path) from None
        raw = handle.Detach()
        try:
            return msvcrt.open_osfhandle(raw, os.O_RDWR | os.O_BINARY), path
        except BaseException:
            win32api.CloseHandle(raw)
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
    raise FileExistsError(errno.EEXIST, "No usable temporary file name found", directory)


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
