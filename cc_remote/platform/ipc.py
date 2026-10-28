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

import asyncio
import base64
import contextlib
import dataclasses
import errno
import functools
import hashlib
import hmac
import json
import os
import secrets
import socket
import stat
import struct
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from cc_remote.log import logger
from cc_remote.platform import IS_WINDOWS, files, locks, perms
from cc_remote.platform.identity import current_uid

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]
StrPath = str | os.PathLike[str]

DEFAULT_LIMIT = 2 ** 16
HANDSHAKE_TIMEOUT = 2.0
ENDPOINT_VERSION = 1
MAX_ENDPOINT_BYTES = 4096
TOKEN_BYTES = 32
NONCE_BYTES = 32
MAC_BYTES = 32
_FAILURE_LOG_INTERVAL = 60.0
_SHARING_VIOLATION = 32
# A listening server accepts a loopback connect at once, but Windows reports a
# refused one only after ~2 s of SYN retries -- for a closed port and equally
# for a live listener whose backlog is full. A dead owner's port gets the quick
# bound; a live owner's gets one long enough for a busy server to accept.
_PROBE_QUICK_CONNECT_TIMEOUT = 0.5
_PROBE_CONNECT_TIMEOUT = 3.0
_START_LOCK_TIMEOUT = 10.0
_START_LOCK_POLL = 0.02

log = logger("cc_remote.platform.ipc")


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
        try:
            value = json.loads(raw.decode("ascii"))
        except ValueError:
            raise
        except Exception:
            # Deep nesting raises RecursionError; keep the ValueError contract.
            raise ValueError("endpoint record is not valid JSON") from None
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
    except (ValueError, UnicodeDecodeError):
        pass
    # Raised outside the handler so neither __cause__ nor __context__ keeps
    # the raw record (and its token) reachable.
    raise UnsafeEndpointError("endpoint record is malformed") from None


def write_endpoint(path: StrPath, endpoint: Endpoint) -> tuple[int, int]:
    """Publish ``endpoint`` privately at ``path``; never overwrite another record.

    Returns the published file's ``(st_dev, st_ino)`` identity.
    """
    if not IS_WINDOWS:
        raise NotImplementedError("write_endpoint is Windows-only; POSIX publishes the Unix socket itself")
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
            # NTFS rename keeps the file id, so the descriptor names the record.
            info = os.fstat(fd)
        finally:
            os.close(fd)
        _publish(temporary, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        raise
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


# --- peer authentication ------------------------------------------------------

def _mac(token: bytes, label: bytes, first: bytes, second: bytes) -> bytes:
    return hmac.new(token, label + first + second, hashlib.sha256).digest()


async def _client_handshake(reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter, token: bytes) -> None:
    client_nonce = secrets.token_bytes(NONCE_BYTES)
    writer.write(client_nonce)
    await writer.drain()
    reply = await reader.readexactly(NONCE_BYTES + MAC_BYTES)
    server_nonce, server_mac = reply[:NONCE_BYTES], reply[NONCE_BYTES:]
    # Verify the server before sending anything derived from the token.
    if not hmac.compare_digest(server_mac, _mac(token, b"srv", client_nonce, server_nonce)):
        raise AuthenticationError("endpoint did not prove the shared token")
    writer.write(_mac(token, b"cli", server_nonce, client_nonce))
    await writer.drain()


async def _server_handshake(reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter, token: bytes) -> bool:
    client_nonce = await reader.readexactly(NONCE_BYTES)
    server_nonce = secrets.token_bytes(NONCE_BYTES)
    writer.write(server_nonce + _mac(token, b"srv", client_nonce, server_nonce))
    await writer.drain()
    proof = await reader.readexactly(MAC_BYTES)
    return hmac.compare_digest(proof, _mac(token, b"cli", server_nonce, client_nonce))


def _peer_uid(sock: socket.socket) -> int:
    """Return an AF_UNIX peer uid on Linux or macOS, failing closed elsewhere."""
    if IS_WINDOWS:
        raise AuthenticationError("Unix peer credentials are unsupported")
    if hasattr(socket, "SO_PEERCRED"):
        try:
            credentials = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _pid, uid, _gid = struct.unpack("3i", credentials)
            return int(uid)
        except (AttributeError, OSError, struct.error) as exc:
            raise AuthenticationError("cannot read Unix peer credentials") from exc

    import ctypes

    # macOS and the BSDs expose getpeereid(3), though Python does not wrap it.
    libc = ctypes.CDLL(None, use_errno=True)
    getpeereid = getattr(libc, "getpeereid", None)
    if getpeereid is None:
        raise AuthenticationError("Unix peer credentials are unsupported")
    uid = ctypes.c_uint()
    gid = ctypes.c_uint()
    getpeereid.argtypes = (
        ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint))
    getpeereid.restype = ctypes.c_int
    if getpeereid(sock.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise AuthenticationError(f"getpeereid failed: errno {ctypes.get_errno()}")
    return int(uid.value)


def _posix_peer_allowed(writer: asyncio.StreamWriter) -> bool:
    sock = writer.get_extra_info("socket")
    try:
        return sock is not None and _peer_uid(sock) == current_uid()
    except AuthenticationError:
        return False


class _Guard:
    """Authenticate each accepted connection before ``handler`` sees it."""

    def __init__(self, handler: Handler, path: Path, token: bytes | None = None):
        if IS_WINDOWS and token is None:
            # Windows has no peer uid; only the token proves the same user.
            raise UnsafeEndpointError("a Windows endpoint guard requires a token")
        self._handler = handler
        self._path = path
        self._token = token
        self.failures = 0
        self._last_log = float("-inf")

    async def __call__(self, reader: asyncio.StreamReader,
                       writer: asyncio.StreamWriter) -> None:
        if self._token is None:
            allowed = _posix_peer_allowed(writer)
        else:
            try:
                allowed = await asyncio.wait_for(
                    _server_handshake(reader, writer, self._token), HANDSHAKE_TIMEOUT)
            except (OSError, EOFError, asyncio.TimeoutError):
                allowed = False
        if not allowed:
            self._reject()
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            return
        await self._handler(reader, writer)

    def _reject(self) -> None:
        self.failures += 1
        now = time.monotonic()
        if now - self._last_log >= _FAILURE_LOG_INTERVAL:
            self._last_log = now
            log.warning("local endpoint rejected an unauthenticated peer",
                        endpoint=str(self._path), failures=self.failures)


# --- Windows liveness, stale endpoints and start serialization ---------------

async def _close_quietly(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()


def _process_start_time(pid: int) -> int | None:
    from cc_remote.platform import win32_process

    return win32_process.creation_time(pid)


def _owner_alive(endpoint: Endpoint) -> bool:
    return _process_start_time(endpoint.pid) == endpoint.start_time


def _require_live(endpoint: Endpoint) -> None:
    if not _owner_alive(endpoint):
        raise ConnectionRefusedError(errno.ECONNREFUSED, "endpoint owner is no longer running")


async def _probe(endpoint: Endpoint) -> str:
    """``live`` (proved the token), ``gone`` (stale) or ``unknown``.

    Neither a refusal nor a wrong proof is evidence against a recorded owner
    that is still running: a stalled server with a full backlog refuses, and
    any listener sharing its port can answer with garbage. Both are stale only
    once the owner is provably gone.
    """
    connection = None
    for timeout in (_PROBE_QUICK_CONNECT_TIMEOUT, _PROBE_CONNECT_TIMEOUT):
        try:
            connection = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", endpoint.port), timeout)
            break
        except ConnectionRefusedError:
            return "unknown" if _owner_alive(endpoint) else "gone"
        except (OSError, asyncio.TimeoutError):
            # Only a live owner earns the long wait for a busy server to
            # accept; a dead owner's silent port is simply stale.
            if not _owner_alive(endpoint):
                return "gone"
    if connection is None:
        return "unknown"
    reader, writer = connection
    try:
        await asyncio.wait_for(
            _client_handshake(reader, writer, endpoint.token), HANDSHAKE_TIMEOUT)
        return "live"
    except AuthenticationError:
        return "unknown" if _owner_alive(endpoint) else "gone"
    except (OSError, EOFError, asyncio.TimeoutError):
        return "unknown"
    finally:
        await _close_quietly(writer)


def _read_existing(target: Path) -> Endpoint | None:
    try:
        return read_endpoint(target)  # also proves the file is ours
    except FileNotFoundError:
        return None
    except OSError as exc:  # unsafe, malformed, a directory, unreadable
        raise UnsafeEndpointError(
            "refusing to replace a non-owned or non-endpoint path") from exc


async def _reclaim_windows(target: Path) -> None:
    """Remove a dead server's record; refuse a live or unverifiable one.

    A completed handshake is the proof of a running server; it also covers an
    owner whose process cannot be inspected (e.g. elevated). An inconclusive
    probe only counts as stale once the recorded owner is provably gone.
    """
    endpoint = _read_existing(target)
    if endpoint is None:
        return
    verdict = await _probe(endpoint)
    if verdict == "live":
        raise AlreadyRunningError(f"endpoint is already running at {target}")
    if verdict == "unknown" and _owner_alive(endpoint):
        raise UnsafeEndpointError(f"cannot verify the existing endpoint at {target}")
    # The probe took up to seconds: delete only the record that was judged.
    current = _read_existing(target)
    if current is None:
        return
    if current != endpoint:
        raise UnsafeEndpointError(
            f"cannot verify the existing endpoint at {target}: it changed while probing")
    with contextlib.suppress(FileNotFoundError):
        target.unlink()


@contextlib.asynccontextmanager
async def _start_lock(target: Path):
    """Serialize reclaim-and-publish between servers starting at ``target``.

    Precondition: the directory is already private (call only after
    ``ensure_private_directory``). The lock file carries no secret and simply
    inherits the directory's ACL. It is never removed: deleting it would let a third starter lock
    a fresh file while the second still holds the old one.
    """
    fd = files.open_nofollow(target.with_name(target.name + ".lock"),
                             os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + _START_LOCK_TIMEOUT
        while True:
            try:
                locks.flock(fd, locks.LOCK_EX | locks.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise AlreadyRunningError(
                        f"another server is starting at {target}") from None
                await asyncio.sleep(_START_LOCK_POLL)
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the lock


# --- server object ------------------------------------------------------------

class IpcServer:
    """An ``asyncio`` server plus the endpoint path it published."""

    def __init__(self, server: asyncio.AbstractServer, path: Path,
                 identity: tuple[int, int], guard: _Guard):
        self._server = server
        self._path = path
        self._identity = identity
        self._guard = guard
        self._removed = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def auth_failures(self) -> int:
        return self._guard.failures

    def is_serving(self) -> bool:
        return self._server.is_serving()

    def close(self) -> None:
        """Withdraw the endpoint if it is still ours, then stop listening.

        Withdrawing first means the recorded port is never released while the
        record still names it and its owner is alive.
        """
        if not self._removed:
            self._removed = True
            try:
                info = self._path.lstat()
            except FileNotFoundError:
                info = None
            if info is not None and (info.st_dev, info.st_ino) == self._identity:
                with contextlib.suppress(FileNotFoundError):
                    self._path.unlink()
        self._server.close()

    async def wait_closed(self) -> None:
        await self._server.wait_closed()

    async def serve_forever(self) -> None:
        await self._server.serve_forever()

    async def __aenter__(self) -> IpcServer:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.close()
        await self.wait_closed()


# --- public API ---------------------------------------------------------------

async def start_server(path: StrPath, handler: Handler, *,
                       limit: int = DEFAULT_LIMIT) -> IpcServer:
    """Serve ``handler`` for same-user peers at ``path``.

    Raises :class:`AlreadyRunningError` when a live server owns ``path`` and
    :class:`UnsafeEndpointError` when ``path`` or its directory is unsafe.
    """
    target = Path(path).expanduser().absolute()
    ensure_private_directory(target.parent)
    if IS_WINDOWS:
        return await _windows_start_server(target, handler, limit)
    return await _posix_start_server(target, handler, limit)


async def open_connection(path: StrPath, *, limit: int = DEFAULT_LIMIT,
                          timeout: float = HANDSHAKE_TIMEOUT,
                          ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect to a same-user server; the returned streams are authenticated."""
    target = Path(path).expanduser().absolute()
    if IS_WINDOWS:
        return await _windows_open(target, limit, timeout)
    return await _posix_open(target, limit, timeout)


def connect_sync(path: StrPath, *, timeout: float = HANDSHAKE_TIMEOUT) -> socket.socket:
    """Blocking :func:`open_connection` for synchronous CLIs; returns a blocking socket."""
    target = Path(path).expanduser().absolute()
    if IS_WINDOWS:
        return _windows_connect_sync(target, timeout)
    return _posix_connect_sync(target, timeout)


# --- POSIX backend ------------------------------------------------------------

def _check_posix_socket(target: Path) -> None:
    info = target.lstat()  # FileNotFoundError: nothing is listening
    if (info.st_uid != current_uid() or not stat.S_ISSOCK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise UnsafeEndpointError("socket must be owned by this uid with mode 0600")


def _reclaim_posix(target: Path) -> None:
    try:
        info = target.lstat()
    except FileNotFoundError:
        return
    if info.st_uid != current_uid() or not stat.S_ISSOCK(info.st_mode):
        raise UnsafeEndpointError("refusing to replace a non-owned or non-socket path")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.2)
        probe.connect(str(target))
    except (ConnectionRefusedError, FileNotFoundError):
        target.unlink()
    except OSError as exc:
        raise UnsafeEndpointError(f"cannot verify existing socket: {exc}") from exc
    else:
        raise AlreadyRunningError(f"endpoint is already running at {target}")
    finally:
        probe.close()


async def _posix_start_server(target: Path, handler: Handler, limit: int) -> IpcServer:
    _reclaim_posix(target)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old_umask = os.umask(0o177)
    try:
        listener.bind(str(target))
    except BaseException:
        listener.close()
        raise
    finally:
        os.umask(old_umask)
    guard = _Guard(handler, target)
    try:
        os.chmod(target, 0o600)
        info = target.lstat()
        if info.st_uid != current_uid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise UnsafeEndpointError("socket permissions are not 0600")
        listener.listen(socket.SOMAXCONN)
        listener.setblocking(False)
        server = await asyncio.start_unix_server(guard, sock=listener, limit=limit)
    except BaseException:
        listener.close()
        with contextlib.suppress(FileNotFoundError):
            target.unlink()
        raise
    return IpcServer(server, target, (info.st_dev, info.st_ino), guard)


async def _posix_open(target: Path, limit: int, timeout: float,
                      ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    _check_posix_socket(target)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(target), limit=limit), timeout)
    except asyncio.TimeoutError as exc:
        # Python 3.10's asyncio.TimeoutError is not an OSError.
        raise TimeoutError(errno.ETIMEDOUT, "endpoint did not accept the connection") from exc
    if not _posix_peer_allowed(writer):
        await _close_quietly(writer)
        raise AuthenticationError("endpoint peer uid does not match")
    return reader, writer


def _posix_connect_sync(target: Path, timeout: float) -> socket.socket:
    _check_posix_socket(target)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(str(target))
        if _peer_uid(sock) != current_uid():
            raise AuthenticationError("endpoint peer uid does not match")
        sock.settimeout(None)
    except BaseException:
        sock.close()
        raise
    return sock


# --- Windows backend ----------------------------------------------------------

async def _windows_start_server(target: Path, handler: Handler, limit: int) -> IpcServer:
    # start_server already made the directory private, as _start_lock requires.
    async with _start_lock(target):
        await _reclaim_windows(target)
        token = secrets.token_bytes(TOKEN_BYTES)
        guard = _Guard(handler, target, token)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Without exclusive use another process could bind the same port.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(socket.SOMAXCONN)
            listener.setblocking(False)
            server = await asyncio.start_server(guard, sock=listener, limit=limit)
        except BaseException:
            listener.close()
            raise
        try:
            start_time = _process_start_time(os.getpid())
            if start_time is None:
                raise OSError("cannot read this process's start time")
            identity = write_endpoint(target, Endpoint(
                port=listener.getsockname()[1], token=token,
                pid=os.getpid(), start_time=start_time))
        except BaseException:
            server.close()
            await server.wait_closed()
            raise
    return IpcServer(server, target, identity, guard)


async def _windows_open(target: Path, limit: int, timeout: float,
                        ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    endpoint = read_endpoint(target)
    _require_live(endpoint)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", endpoint.port, limit=limit), timeout)
    except (OSError, asyncio.TimeoutError) as exc:
        # Includes the ~2 s Windows takes to refuse a closed loopback port.
        raise ConnectionRefusedError(
            errno.ECONNREFUSED, "endpoint is not accepting connections") from exc
    try:
        await asyncio.wait_for(_client_handshake(reader, writer, endpoint.token), timeout)
    except AuthenticationError:
        await _close_quietly(writer)
        raise
    except (OSError, EOFError, asyncio.TimeoutError) as exc:
        # A timeout, an early close or a reset: the peer proved nothing.
        await _close_quietly(writer)
        raise AuthenticationError("endpoint handshake failed") from exc
    except BaseException:
        await _close_quietly(writer)
        raise
    return reader, writer


def _recv_exactly(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise AuthenticationError("endpoint closed during the handshake")
        data += chunk
    return bytes(data)


def _windows_connect_sync(target: Path, timeout: float) -> socket.socket:
    endpoint = read_endpoint(target)
    _require_live(endpoint)
    try:
        sock = socket.create_connection(("127.0.0.1", endpoint.port), timeout=timeout)
    except OSError as exc:  # refused, or timed out while Windows retried the SYN
        raise ConnectionRefusedError(
            errno.ECONNREFUSED, "endpoint is not accepting connections") from exc
    try:
        client_nonce = secrets.token_bytes(NONCE_BYTES)
        sock.sendall(client_nonce)
        reply = _recv_exactly(sock, NONCE_BYTES + MAC_BYTES)
        server_nonce, server_mac = reply[:NONCE_BYTES], reply[NONCE_BYTES:]
        if not hmac.compare_digest(
                server_mac, _mac(endpoint.token, b"srv", client_nonce, server_nonce)):
            raise AuthenticationError("endpoint did not prove the shared token")
        sock.sendall(_mac(endpoint.token, b"cli", server_nonce, client_nonce))
        sock.settimeout(None)
    except AuthenticationError:
        sock.close()
        raise
    except OSError as exc:  # a timeout (socket.timeout) or a reset mid-handshake
        sock.close()
        raise AuthenticationError("endpoint handshake failed") from exc
    except BaseException:
        sock.close()
        raise
    return sock


__all__ = [
    "AlreadyRunningError", "AuthenticationError", "DEFAULT_LIMIT", "Endpoint",
    "HANDSHAKE_TIMEOUT", "IpcServer", "UnsafeEndpointError", "connect_sync",
    "ensure_private_directory", "open_connection", "read_endpoint", "start_server",
    "write_endpoint",
]
