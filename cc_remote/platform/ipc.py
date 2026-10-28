"""Same-user local IPC endpoints for long-lived cc-remote services.

POSIX keeps the original Unix-socket contract: a private 0700 directory, a
0600 socket owned by the current uid, stale-socket probing, and peer-uid
checks on both ends. Windows CPython has no AF_UNIX, so the endpoint path holds
a private JSON record ``{v, port, token, pid, start_time}`` for a 127.0.0.1
listener instead. Both sides prove knowledge of the token with an HMAC
challenge-response before any application byte flows; the token itself never
crosses the socket.
"""
from __future__ import annotations

import functools
import os
import stat
from pathlib import Path

from cc_remote.platform import IS_WINDOWS, files, perms
from cc_remote.platform.identity import current_uid

StrPath = str | os.PathLike[str]


class UnsafeEndpointError(PermissionError):
    """The endpoint, its record or its directory is not private to this user."""


class AlreadyRunningError(OSError):
    """A live, authenticated server already owns the endpoint."""


class AuthenticationError(ConnectionError):
    """The peer did not prove it is the same user (peer uid or HMAC)."""


# --- private directories ----------------------------------------------------

def ensure_private_directory(path: StrPath) -> Path:
    """Create ``path`` (0700) when missing and require it to be user-private."""
    directory = Path(path).expanduser().absolute()
    if IS_WINDOWS and not _inside_profile(directory):
        # Refuse before mkdir so a rejected path leaves nothing behind.
        raise UnsafeEndpointError("directory must be inside the current user's profile")
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise UnsafeEndpointError(f"directory cannot be prepared: {exc}") from exc
    if IS_WINDOWS:
        _check_windows_directory(directory)
    else:
        _check_posix_directory(directory)
    return directory


def _check_posix_directory(directory: Path) -> None:
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise UnsafeEndpointError("directory must be a real directory")
    if info.st_uid != current_uid():
        raise UnsafeEndpointError("directory must be owned by the current uid")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise UnsafeEndpointError("directory must not be accessible by other users")


@functools.cache
def _profile_directory() -> str:
    """The signed-in user's real profile root (independent of HOME/USERPROFILE)."""
    import win32api
    import win32con
    import win32profile
    import win32security

    from cc_remote.platform._win32err import translated

    with translated():
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            return win32profile.GetUserProfileDirectory(token)
        finally:
            token.Close()


def _inside_profile(directory: Path) -> bool:
    # Non-strict realpath resolves only the existing prefix (including 8.3
    # short names such as a default TEMP) and keeps the missing remainder.
    profile = os.path.normcase(os.path.realpath(_profile_directory()))
    real = os.path.normcase(os.path.realpath(directory))
    try:
        return os.path.commonpath([profile, real]) == profile
    except ValueError:  # another drive or a UNC share
        return False


def _check_windows_directory(directory: Path) -> None:
    if not directory.is_dir() or files.is_reparse_point(directory):
        raise UnsafeEndpointError("directory must be a real directory")
    if not _inside_profile(directory):
        raise UnsafeEndpointError("directory must be inside the current user's profile")
    if not perms.owned_by_current_user(directory):
        raise UnsafeEndpointError("directory must be owned by the current uid")
    if not perms.is_private(directory):
        raise UnsafeEndpointError("directory must not be accessible by other users")
