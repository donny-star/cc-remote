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

import base64
import contextlib
import dataclasses
import errno
import functools
import json
import os
import secrets
import stat
import time
from pathlib import Path

from cc_remote.platform import IS_WINDOWS, files, perms
from cc_remote.platform.identity import current_uid

StrPath = str | os.PathLike[str]

ENDPOINT_VERSION = 1
MAX_ENDPOINT_BYTES = 4096
TOKEN_BYTES = 32
_SHARING_VIOLATION = 32


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


# --- Windows endpoint records -------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Endpoint:
    """The private record a Windows server publishes at the endpoint path."""

    port: int
    token: bytes = dataclasses.field(repr=False)
    pid: int
    start_time: int

    def encode(self) -> bytes:
        return json.dumps({
            "v": ENDPOINT_VERSION, "port": self.port,
            "token": base64.b64encode(self.token).decode("ascii"),
            "pid": self.pid, "start_time": self.start_time,
        }, separators=(",", ":"), sort_keys=True).encode("ascii")

    @classmethod
    def decode(cls, raw: bytes) -> Endpoint:
        """Parse a record; any deviation raises ``ValueError``."""
        value = json.loads(raw.decode("ascii"))
        if not isinstance(value, dict) or set(value) != {"v", "port", "token", "pid", "start_time"}:
            raise ValueError("endpoint record has unexpected fields")
        port, pid, start_time = value["port"], value["pid"], value["start_time"]
        # type() rather than isinstance(): JSON true must not pass as 1.
        if (type(value["v"]) is not int or value["v"] != ENDPOINT_VERSION
                or not all(type(item) is int for item in (port, pid, start_time))
                or not 0 < port < 65536 or pid <= 0 or start_time <= 0
                or not isinstance(value["token"], str)):
            raise ValueError("endpoint record is invalid")
        token = base64.b64decode(value["token"].encode("ascii"), validate=True)
        if len(token) != TOKEN_BYTES:
            raise ValueError("endpoint token has the wrong size")
        return cls(port=port, token=token, pid=pid, start_time=start_time)


def read_endpoint(path: StrPath) -> Endpoint:
    """Read a private Windows endpoint record without following links."""
    target = Path(path)
    try:
        fd = files.open_nofollow(target, os.O_RDONLY)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise UnsafeEndpointError("endpoint must not be a link") from exc
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ENDPOINT_BYTES:
            raise UnsafeEndpointError("endpoint must be a small regular file")
        if not (perms.owned_by_current_user(fd) and perms.is_private_fd(fd)):
            raise UnsafeEndpointError("endpoint file must be private to the current user")
        raw = os.read(fd, MAX_ENDPOINT_BYTES + 1)
    finally:
        os.close(fd)
    try:
        return Endpoint.decode(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise UnsafeEndpointError("endpoint record is malformed") from exc


def write_endpoint(path: StrPath, endpoint: Endpoint) -> tuple[int, int]:
    """Publish ``endpoint`` privately at ``path``; never overwrite another record.

    Returns the published file's ``(st_dev, st_ino)`` identity.
    """
    target = Path(path)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = files.open_nofollow(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        try:
            # Restrict the record before the token is written into it.
            perms.make_private(temporary, directory=False)
            data = memoryview(endpoint.encode())
            while data:
                data = data[os.write(fd, data):]
            os.fsync(fd)
        finally:
            os.close(fd)
        _publish(temporary, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        raise
    info = target.lstat()
    return info.st_dev, info.st_ino


def _publish(temporary: Path, target: Path, attempts: int = 10) -> None:
    """No-overwrite atomic rename: like a POSIX bind, a racing server loses."""
    for attempt in range(attempts):
        try:
            os.rename(temporary, target)
            return
        except FileExistsError as exc:
            raise AlreadyRunningError(f"endpoint is already running at {target}") from exc
        except PermissionError as exc:
            # Antivirus and indexers briefly hold freshly written files.
            if getattr(exc, "winerror", None) != _SHARING_VIOLATION or attempt == attempts - 1:
                raise
            time.sleep(min(0.02 * (2 ** attempt), 0.5))
