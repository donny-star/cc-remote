"""Small OS compatibility helpers that preserve Unix durability semantics."""
from __future__ import annotations

import os
import shutil
import stat
import sys
import time
from os import PathLike


def use_binary_descriptors() -> None:
    """Make raw ``os.open`` descriptors binary on Windows, as on POSIX.

    The Windows CRT opens descriptors in *text* mode unless ``O_BINARY`` is
    passed: ``os.write`` then expands LF to CRLF and ``os.read`` folds CRLF
    and stops at Ctrl-Z. Every journal, lock, attachment
    and atomic-replace writer in cc-remote assumes byte-exact descriptors, so
    switch the process-wide UCRT default once instead of trusting each call
    site to remember the flag. Buffered ``open()`` already forces binary.
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        set_fmode = ctypes.CDLL("ucrtbase")._set_fmode
    except (AttributeError, OSError):
        return
    set_fmode.argtypes = [ctypes.c_int]
    set_fmode.restype = ctypes.c_int
    set_fmode(os.O_BINARY)


def current_uid() -> int:
    """Return the Unix uid, or the stable Windows ``st_uid`` placeholder."""
    getuid = getattr(os, "getuid", None)
    return getuid() if getuid is not None else 0


def current_euid() -> int:
    """Return the effective Unix uid, or the Windows ``st_uid`` placeholder."""
    geteuid = getattr(os, "geteuid", None)
    return geteuid() if geteuid is not None else 0


def mode_exposed(mode: int, mask: int = 0o077) -> bool:
    """Whether POSIX permission bits grant access outside ``mask``'s owner.

    Windows synthesizes ``st_mode`` (0o666/0o444) and enforces privacy with
    the user profile's ACLs instead, so the bit test never applies there.
    """
    return sys.platform != "win32" and bool(mode & mask)


def is_redirect(info: os.stat_result) -> bool:
    """Whether an ``lstat`` result is a symlink or a Windows reparse point.

    Junctions and other name-surrogate reparse points redirect path lookups
    exactly like symlinks but are not reported by ``S_ISLNK``.
    """
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def kill_process_tree(pid: int) -> None:
    """Force-stop a child started with ``start_new_session`` and its descendants.

    POSIX kills the child's process group. Windows has no process groups for
    this purpose; npm installs ``claude``/``codex`` as ``.cmd`` shims, so
    killing only the direct child would orphan the real CLI. ``taskkill /T``
    walks the recorded parent links instead. Raises ``ProcessLookupError``
    when the process is already gone, matching ``os.killpg``.
    """
    if sys.platform != "win32":
        import signal

        os.killpg(pid, signal.SIGKILL)
        return
    import subprocess

    result = subprocess.run(
        ["taskkill", "/T", "/F", "/PID", str(pid)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    # 128: no such process. Any other failure leaves the caller's own wait()
    # to surface a still-running child.
    if result.returncode == 128:
        raise ProcessLookupError(pid)


def fchmod(fd: int, path: str | PathLike[str], mode: int) -> None:
    """Set a file mode using the descriptor where the platform supports it."""
    native_fchmod = getattr(os, "fchmod", None)
    if native_fchmod is not None:
        native_fchmod(fd, mode)
    else:
        os.chmod(path, mode)


def fsync_directory(path: str | PathLike[str]) -> None:
    """Persist a directory entry on Unix; Windows has no directory fsync."""
    if sys.platform == "win32":
        return
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _win32_long_path(path: str) -> str:
    """Prefix an absolute Windows path with \\\\?\\ so it bypasses MAX_PATH.

    Without this, any path at or beyond 260 characters silently misbehaves
    on delete (directory entries can be enumerated but not reliably
    unlinked/rmdir'd) unless the machine has opted into the
    ``LongPathsEnabled`` registry setting, which cc-remote cannot assume.
    """
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def force_rmtree(path: str | PathLike[str]) -> None:
    """Remove a directory tree, clearing Windows read-only bits first.

    Git always creates loose objects read-only. POSIX directory write
    permission is enough to unlink them; Windows blocks the unlink outright
    unless the read-only attribute is cleared first. The path is also
    resolved through the \\\\?\\ long-path form so deletion isn't silently
    unreliable for paths at or beyond MAX_PATH (260 chars), which ordinary
    temp-directory nesting reaches easily. A short retry absorbs the
    transient "directory not empty" Windows reports while another process
    (antivirus, search indexing) briefly holds a just-deleted file.
    """
    if sys.platform != "win32":
        shutil.rmtree(path)
        return

    def _clear_readonly_and_retry(func, target, exc_info):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except FileNotFoundError:
            pass

    long_path = _win32_long_path(os.fspath(path))
    attempts = 5
    for attempt in range(attempts):
        try:
            shutil.rmtree(long_path, onerror=_clear_readonly_and_retry)
            return
        except OSError:
            if not os.path.exists(long_path):
                return
            if attempt == attempts - 1:
                raise
            time.sleep(min(0.05 * (attempt + 1), 0.5))


def open_with_share_delete(
    path: str | PathLike[str], flags: int, mode: int = 0o600,
) -> int:
    """Open a file so it can still be unlinked/replaced while this fd is open.

    Plain ``os.open`` on Windows does not grant ``FILE_SHARE_DELETE``, so any
    unlink or rename of the path from another descriptor (including a second
    open in this same process) raises ``PermissionError: [WinError 32]``
    while this fd remains open. Unix descriptors never carry that
    restriction, so this only changes behavior on Windows; unlike
    ``os.O_TEMPORARY`` it does not delete the file when the fd closes, so it
    is safe for paths that may legitimately outlive this open.
    """
    if sys.platform != "win32":
        return os.open(path, flags, mode)

    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    generic_write = 0x40000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    create_new = 1
    open_existing = 3
    file_attribute_normal = 0x80
    invalid_handle_value = wintypes.HANDLE(-1).value

    if flags & os.O_RDWR:
        access, access_flags = generic_read | generic_write, os.O_RDWR
    elif flags & os.O_WRONLY:
        access, access_flags = generic_write, os.O_WRONLY
    else:
        access, access_flags = generic_read, os.O_RDONLY
    disposition = (
        create_new if flags & os.O_CREAT and flags & os.O_EXCL
        else open_existing
    )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        os.fspath(path), access,
        file_share_read | file_share_write | file_share_delete,
        None, disposition, file_attribute_normal, None,
    )
    if handle == invalid_handle_value:
        raise ctypes.WinError(ctypes.get_last_error())
    return msvcrt.open_osfhandle(handle, access_flags)


def pread(fd: int, length: int, offset: int) -> bytes:
    """Positioned read, backed by lseek+read where the platform lacks pread.

    Callers only use this on descriptors they exclusively own for the
    duration of the call, so relocating the file position is safe.
    """
    native_pread = getattr(os, "pread", None)
    if native_pread is not None:
        return native_pread(fd, length, offset)
    saved = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        return os.read(fd, length)
    finally:
        os.lseek(fd, saved, os.SEEK_SET)


def pwrite(fd: int, data: bytes, offset: int) -> int:
    """Positioned write, backed by lseek+write where the platform lacks pwrite."""
    native_pwrite = getattr(os, "pwrite", None)
    if native_pwrite is not None:
        return native_pwrite(fd, data, offset)
    saved = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        return os.write(fd, data)
    finally:
        os.lseek(fd, saved, os.SEEK_SET)
