"""cc_remote.platform.ipc: same-user local endpoints."""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import errno
import hashlib
import hmac
import json
import os
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from cc_remote.platform import IS_WINDOWS, ipc, perms
from tests.ipc_helpers import SHORT_TMP, share_with_everyone


def test_ensure_private_directory_creates_an_owner_only_tree(tmp_path):
    target = ipc.ensure_private_directory(tmp_path / "state" / "endpoint")
    assert target == (tmp_path / "state" / "endpoint").absolute()
    assert target.is_dir()
    assert perms.is_private(target)
    assert perms.owned_by_current_user(target)


def test_ensure_private_directory_accepts_an_existing_private_directory(tmp_path):
    first = ipc.ensure_private_directory(tmp_path / "again")
    assert ipc.ensure_private_directory(first) == first


def test_ensure_private_directory_rejects_a_shared_directory(tmp_path):
    shared = ipc.ensure_private_directory(tmp_path / "shared")
    share_with_everyone(shared)
    with pytest.raises(ipc.UnsafeEndpointError, match="must not be accessible by other users"):
        ipc.ensure_private_directory(shared)


def test_ensure_private_directory_rejects_a_regular_file(tmp_path):
    plain = tmp_path / "plain"
    plain.write_text("x", encoding="utf-8")
    with pytest.raises(ipc.UnsafeEndpointError):
        ipc.ensure_private_directory(plain)


def test_ipc_errors_stay_in_the_oserror_family():
    assert issubclass(ipc.UnsafeEndpointError, PermissionError)
    assert issubclass(ipc.AlreadyRunningError, OSError)
    assert issubclass(ipc.AuthenticationError, ConnectionError)


@pytest.mark.requires_symlink(reason="a symlinked endpoint directory must be refused")
def test_ensure_private_directory_rejects_a_symlinked_directory(tmp_path):
    real = ipc.ensure_private_directory(tmp_path / "real")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ipc.UnsafeEndpointError, match="real directory"):
        ipc.ensure_private_directory(link)


@pytest.mark.windows_only(reason="NTFS junctions are Windows reparse points")
def test_ensure_private_directory_rejects_a_junction(tmp_path):
    real = ipc.ensure_private_directory(tmp_path / "real")
    junction = tmp_path / "junction"
    subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(junction), str(real)],
        check=True, capture_output=True)
    with pytest.raises(ipc.UnsafeEndpointError, match="real directory"):
        ipc.ensure_private_directory(junction)


@pytest.mark.windows_only(reason="Windows ownership comes from the security descriptor")
def test_ensure_private_directory_fails_closed_when_ownership_is_unverifiable(tmp_path, monkeypatch):
    target = ipc.ensure_private_directory(tmp_path / "unverifiable")
    # perms.owned_by_current_user returns False on any inspection failure.
    monkeypatch.setattr(ipc.perms, "owned_by_current_user", lambda _target: False)
    with pytest.raises(ipc.UnsafeEndpointError, match="owned by the current"):
        ipc.ensure_private_directory(target)


@pytest.mark.windows_only(reason="the profile boundary only exists for Windows endpoints")
def test_ensure_private_directory_requires_the_user_profile(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setattr(ipc, "_profile_directory", lambda: str(profile))
    assert ipc.ensure_private_directory(profile / "inside").is_dir()
    with pytest.raises(ipc.UnsafeEndpointError, match="profile"):
        ipc.ensure_private_directory(tmp_path / "outside")


@pytest.mark.windows_only(reason="the profile boundary only exists for Windows endpoints")
def test_ensure_private_directory_rejects_another_drive(tmp_path, monkeypatch):
    monkeypatch.setattr(ipc, "_profile_directory", lambda: r"Z:\profile")
    with pytest.raises(ipc.UnsafeEndpointError, match="profile") as caught:
        ipc.ensure_private_directory(tmp_path / "elsewhere")
    assert isinstance(caught.value, OSError)
    assert not (tmp_path / "elsewhere").exists()


@pytest.mark.windows_only(reason="the profile boundary only exists for Windows endpoints")
def test_ensure_private_directory_creates_nothing_outside_the_profile(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setattr(ipc, "_profile_directory", lambda: str(profile))
    outside = tmp_path / "outside" / uuid.uuid4().hex / "x"
    with pytest.raises(ipc.UnsafeEndpointError, match="profile"):
        ipc.ensure_private_directory(outside)
    assert not (tmp_path / "outside").exists()


@pytest.mark.windows_only(reason="the profile root comes from the process token")
def test_profile_directory_translates_pywin32_errors(monkeypatch):
    import pywintypes
    import win32profile

    def denied(_token):
        raise pywintypes.error(5, "GetUserProfileDirectory", "denied")

    monkeypatch.setattr(win32profile, "GetUserProfileDirectory", denied)
    ipc._profile_directory.cache_clear()
    try:
        with pytest.raises(OSError) as caught:
            ipc._profile_directory()
        assert isinstance(caught.value, PermissionError)
        with pytest.raises(OSError):
            ipc.ensure_private_directory("C:/Users/never-created-by-ipc-tests/x")
    finally:
        ipc._profile_directory.cache_clear()


@pytest.mark.windows_only(reason="the profile root comes from the process token")
def test_profile_directory_ignores_environment_overrides(monkeypatch):
    real = ipc._profile_directory()
    monkeypatch.setenv("USERPROFILE", "C:\\")
    ipc._profile_directory.cache_clear()
    try:
        assert ipc._profile_directory() == real
    finally:
        ipc._profile_directory.cache_clear()


@pytest.mark.posix_only(reason="POSIX endpoints keep the 0700 mode-bit contract")
def test_posix_directory_mode_is_0700(tmp_path):
    target = ipc.ensure_private_directory(tmp_path / "mode")
    assert stat.S_IMODE(target.stat().st_mode) == 0o700


TOKEN = bytes(range(32))


def _endpoint(**changes) -> ipc.Endpoint:
    values = {"port": 43210, "token": TOKEN, "pid": os.getpid(), "start_time": 123456789}
    values.update(changes)
    return ipc.Endpoint(**values)


def test_endpoint_record_round_trips():
    raw = _endpoint().encode()
    assert json.loads(raw) == {
        "v": 1, "port": 43210, "token": base64.b64encode(TOKEN).decode("ascii"),
        "pid": os.getpid(), "start_time": 123456789,
    }
    assert ipc.Endpoint.decode(raw) == _endpoint()


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(v=2),
    lambda value: value.update(v=True),
    lambda value: value.update(extra=1),
    lambda value: value.pop("token"),
    lambda value: value.update(port=0),
    lambda value: value.update(port=65536),
    lambda value: value.update(port="43210"),
    lambda value: value.update(pid=True),
    lambda value: value.update(start_time=0),
    lambda value: value.update(token=base64.b64encode(b"short").decode("ascii")),
    lambda value: value.update(token="not base64!"),
], ids=["version", "version-bool", "extra", "missing", "port-zero", "port-high", "port-text",
        "pid-bool", "start-zero", "token-short", "token-text"])
def test_endpoint_record_rejects_deviations(mutate):
    value = json.loads(_endpoint().encode())
    mutate(value)
    with pytest.raises(ValueError):
        ipc.Endpoint.decode(json.dumps(value).encode("ascii"))


def test_endpoint_record_rejects_non_json():
    with pytest.raises(ValueError):
        ipc.Endpoint.decode(b"do not replace")


def test_endpoint_record_rejects_deep_nesting_as_value_error():
    with pytest.raises(ValueError):
        ipc.Endpoint.decode(b"[" * 4000)


@pytest.mark.posix_only(reason="POSIX publishes the Unix socket itself")
def test_write_endpoint_is_windows_only(tmp_path):
    with pytest.raises(NotImplementedError, match="Windows-only"):
        ipc.write_endpoint(tmp_path / "service.sock", _endpoint())
    assert not list(tmp_path.iterdir())


windows_records = pytest.mark.windows_only(
    reason="endpoint records stand in for AF_UNIX sockets only on Windows")


@windows_records
def test_write_endpoint_publishes_a_private_record(tmp_path):
    directory = ipc.ensure_private_directory(tmp_path / "ep")
    path = directory / "service.sock"
    identity = ipc.write_endpoint(path, _endpoint())
    info = path.lstat()
    assert identity == (info.st_dev, info.st_ino)
    assert perms.is_private(path)
    assert ipc.read_endpoint(path) == _endpoint()
    assert [entry.name for entry in directory.iterdir()] == ["service.sock"]


@windows_records
def test_write_endpoint_never_replaces_an_existing_record(tmp_path):
    directory = ipc.ensure_private_directory(tmp_path / "ep")
    path = directory / "service.sock"
    ipc.write_endpoint(path, _endpoint())
    with pytest.raises(ipc.AlreadyRunningError, match="already running"):
        ipc.write_endpoint(path, _endpoint(port=1))
    assert ipc.read_endpoint(path) == _endpoint()
    assert [entry.name for entry in directory.iterdir()] == ["service.sock"]


@windows_records
def test_read_endpoint_refuses_a_record_readable_by_others(tmp_path):
    path = ipc.ensure_private_directory(tmp_path / "ep") / "service.sock"
    ipc.write_endpoint(path, _endpoint())
    share_with_everyone(path)
    with pytest.raises(ipc.UnsafeEndpointError, match="private"):
        ipc.read_endpoint(path)


@windows_records
def test_read_endpoint_refuses_malformed_and_oversized_records(tmp_path):
    directory = ipc.ensure_private_directory(tmp_path / "ep")
    malformed = directory / "malformed.sock"
    malformed.write_bytes(b"do not replace")
    with pytest.raises(ipc.UnsafeEndpointError, match="malformed"):
        ipc.read_endpoint(malformed)
    oversized = directory / "oversized.sock"
    oversized.write_bytes(b" " * (ipc.MAX_ENDPOINT_BYTES + 1))
    with pytest.raises(ipc.UnsafeEndpointError, match="small regular file"):
        ipc.read_endpoint(oversized)


@windows_records
def test_read_endpoint_hides_the_raw_record_of_a_malformed_record(tmp_path):
    directory = ipc.ensure_private_directory(tmp_path / "ep")
    nested = directory / "nested.sock"
    nested.write_bytes(b"[" * 4000)
    with pytest.raises(ipc.UnsafeEndpointError, match="malformed") as caught:
        ipc.read_endpoint(nested)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    leaky = directory / "leaky.sock"
    value = json.loads(_endpoint().encode())
    value["extra"] = 1
    leaky.write_bytes(json.dumps(value).encode("ascii") + b"}")
    with pytest.raises(ipc.UnsafeEndpointError, match="malformed") as caught:
        ipc.read_endpoint(leaky)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@windows_records
def test_read_endpoint_reports_a_missing_record(tmp_path):
    with pytest.raises(FileNotFoundError):
        ipc.read_endpoint(ipc.ensure_private_directory(tmp_path / "ep") / "absent.sock")


@windows_records
@pytest.mark.requires_symlink(reason="a symlinked endpoint record must be refused")
def test_read_endpoint_refuses_a_symlink(tmp_path):
    directory = ipc.ensure_private_directory(tmp_path / "ep")
    real = directory / "real.sock"
    ipc.write_endpoint(real, _endpoint())
    link = directory / "link.sock"
    link.symlink_to(real)
    with pytest.raises(ipc.UnsafeEndpointError, match="link"):
        ipc.read_endpoint(link)


OTHER_TOKEN = bytes(range(1, 33))


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition was not reached in time")
        await asyncio.sleep(0.01)


async def _echo(reader, writer) -> None:
    try:
        while data := await reader.read(1024):
            writer.write(data.upper())
            await writer.drain()
    finally:
        writer.close()


async def _loopback(server_side):
    """One loopback TCP connection whose accepting side runs ``server_side``."""
    result = asyncio.get_running_loop().create_future()

    async def accept(reader, writer):
        try:
            result.set_result(await server_side(reader, writer))
        except BaseException as exc:  # surfaced to the test through ``result``
            result.set_exception(exc)
        finally:
            writer.close()

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", server.sockets[0].getsockname()[1])
    return server, reader, writer, result


async def _shutdown(server, writer) -> None:
    writer.close()
    server.close()
    await server.wait_closed()


def test_mac_binds_direction_and_nonce_order():
    first, second = os.urandom(32), os.urandom(32)
    expected = hmac.new(TOKEN, b"srv" + first + second, hashlib.sha256).digest()
    assert ipc._mac(TOKEN, b"srv", first, second) == expected
    assert ipc._mac(TOKEN, b"cli", first, second) != expected
    assert ipc._mac(TOKEN, b"srv", second, first) != expected


@pytest.mark.asyncio
async def test_handshake_proves_the_shared_token_both_ways():
    server, reader, writer, result = await _loopback(
        lambda r, w: ipc._server_handshake(r, w, TOKEN))
    try:
        await ipc._client_handshake(reader, writer, TOKEN)
        assert await asyncio.wait_for(result, 2) is True
    finally:
        await _shutdown(server, writer)


@pytest.mark.asyncio
async def test_client_rejects_a_server_with_another_token():
    server, reader, writer, result = await _loopback(
        lambda r, w: ipc._server_handshake(r, w, TOKEN))
    try:
        with pytest.raises(ipc.AuthenticationError):
            await ipc._client_handshake(reader, writer, OTHER_TOKEN)
        writer.close()
        with pytest.raises(asyncio.IncompleteReadError):
            await asyncio.wait_for(result, 2)
    finally:
        await _shutdown(server, writer)


@pytest.mark.asyncio
async def test_client_reveals_only_a_fresh_nonce_to_an_impostor():
    async def impostor(reader, writer):
        seen = await reader.readexactly(ipc.NONCE_BYTES)
        writer.write(os.urandom(ipc.NONCE_BYTES + ipc.MAC_BYTES))
        await writer.drain()
        return seen + await reader.read()

    server, reader, writer, result = await _loopback(impostor)
    try:
        with pytest.raises(ipc.AuthenticationError):
            await ipc._client_handshake(reader, writer, TOKEN)
        writer.close()
        seen = await asyncio.wait_for(result, 2)
    finally:
        await _shutdown(server, writer)
    assert len(seen) == ipc.NONCE_BYTES
    assert TOKEN not in seen


@pytest.mark.asyncio
async def test_server_rejects_its_own_proof_reflected_back():
    server, reader, writer, result = await _loopback(
        lambda r, w: ipc._server_handshake(r, w, TOKEN))
    try:
        writer.write(os.urandom(ipc.NONCE_BYTES))
        await writer.drain()
        reply = await reader.readexactly(ipc.NONCE_BYTES + ipc.MAC_BYTES)
        writer.write(reply[ipc.NONCE_BYTES:])
        await writer.drain()
        assert await asyncio.wait_for(result, 2) is False
    finally:
        await _shutdown(server, writer)


@pytest.mark.asyncio
async def test_server_rejects_a_replayed_client_proof():
    """Bytes captured from a successful handshake are useless in a new one."""
    server, reader, writer, result = await _loopback(
        lambda r, w: ipc._server_handshake(r, w, TOKEN))
    try:
        client_nonce = os.urandom(ipc.NONCE_BYTES)
        writer.write(client_nonce)
        await writer.drain()
        reply = await reader.readexactly(ipc.NONCE_BYTES + ipc.MAC_BYTES)
        proof = ipc._mac(TOKEN, b"cli", reply[:ipc.NONCE_BYTES], client_nonce)
        writer.write(proof)
        await writer.drain()
        assert await asyncio.wait_for(result, 2) is True
    finally:
        await _shutdown(server, writer)

    server, reader, writer, result = await _loopback(
        lambda r, w: ipc._server_handshake(r, w, TOKEN))
    try:
        writer.write(client_nonce)
        await writer.drain()
        fresh = await reader.readexactly(ipc.NONCE_BYTES + ipc.MAC_BYTES)
        assert fresh[:ipc.NONCE_BYTES] != reply[:ipc.NONCE_BYTES]
        writer.write(proof)
        await writer.drain()
        assert await asyncio.wait_for(result, 2) is False
    finally:
        await _shutdown(server, writer)


@pytest.mark.asyncio
async def test_client_rejects_a_replayed_server_reply():
    """A recorded server reply only proves the token for the nonce it answered."""
    old_client_nonce = os.urandom(ipc.NONCE_BYTES)
    old_server_nonce = os.urandom(ipc.NONCE_BYTES)
    recorded = old_server_nonce + ipc._mac(TOKEN, b"srv", old_client_nonce, old_server_nonce)

    async def replayer(reader, writer):
        await reader.readexactly(ipc.NONCE_BYTES)
        writer.write(recorded)
        await writer.drain()
        return await reader.read()

    server, reader, writer, result = await _loopback(replayer)
    try:
        with pytest.raises(ipc.AuthenticationError):
            await ipc._client_handshake(reader, writer, TOKEN)
        writer.close()
        # The client sent nothing after its nonce: no proof for the replayer.
        assert await asyncio.wait_for(result, 2) == b""
    finally:
        await _shutdown(server, writer)


async def _guarded(handler):
    guard = ipc._Guard(handler, Path("endpoint"), TOKEN)
    server = await asyncio.start_server(guard, "127.0.0.1", 0)
    return guard, server, server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
async def test_guard_hands_authenticated_streams_to_the_handler():
    guard, server, port = await _guarded(_echo)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await ipc._client_handshake(reader, writer, TOKEN)
        writer.write(b"after")
        await writer.drain()
        assert await reader.readexactly(5) == b"AFTER"
    finally:
        await _shutdown(server, writer)
    assert guard.failures == 0


@pytest.mark.asyncio
async def test_guard_rejects_a_bad_proof_without_calling_the_handler():
    calls = []

    async def handler(reader, writer):
        calls.append(1)

    guard, server, port = await _guarded(handler)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(os.urandom(ipc.NONCE_BYTES))
        await writer.drain()
        await reader.readexactly(ipc.NONCE_BYTES + ipc.MAC_BYTES)
        writer.write(os.urandom(ipc.MAC_BYTES))
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        await _wait_until(lambda: guard.failures == 1)
    finally:
        await _shutdown(server, writer)
    assert calls == []


@pytest.mark.asyncio
async def test_guard_drops_a_silent_peer_after_the_handshake_timeout(monkeypatch):
    monkeypatch.setattr(ipc, "HANDSHAKE_TIMEOUT", 0.2)
    guard, server, port = await _guarded(_echo)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        assert await asyncio.wait_for(reader.read(), 2) == b""
        await _wait_until(lambda: guard.failures == 1)
    finally:
        await _shutdown(server, writer)


def test_guard_rate_limits_its_rejection_log(monkeypatch):
    warnings = []
    monkeypatch.setattr(ipc.log, "warning", lambda message, **fields: warnings.append(fields))
    guard = ipc._Guard(_echo, Path("endpoint"), TOKEN)
    for _ in range(3):
        guard._reject()
    assert guard.failures == 3
    assert warnings == [{"endpoint": "endpoint", "failures": 1}]
    guard._last_log -= ipc._FAILURE_LOG_INTERVAL
    guard._reject()
    assert warnings[-1] == {"endpoint": "endpoint", "failures": 4}


@pytest.mark.posix_only(reason="SO_PEERCRED/getpeereid is the POSIX same-user proof")
def test_peer_uid_reads_the_connected_user():
    left, right = socket.socketpair()
    try:
        assert ipc._peer_uid(left) == os.getuid()
        assert ipc._peer_uid(right) == os.getuid()
    finally:
        left.close()
        right.close()


@pytest.mark.windows_only(reason="Windows has no Unix peer credentials")
def test_peer_uid_fails_closed_on_windows():
    sock = socket.socket()
    try:
        with pytest.raises(ipc.AuthenticationError, match="unsupported"):
            ipc._peer_uid(sock)
    finally:
        sock.close()


@pytest.mark.windows_only(reason="Windows connections are authenticated only by the token")
def test_guard_requires_a_token_on_windows():
    with pytest.raises(ipc.UnsafeEndpointError, match="requires a token"):
        ipc._Guard(_echo, Path("endpoint"))


@pytest.fixture
def endpoint_path():
    root = Path(tempfile.mkdtemp(prefix="ccipc-", dir=SHORT_TMP))
    try:
        yield root / "ep.sock"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _own_start_time() -> int:
    from cc_remote.platform import win32_process

    return win32_process.creation_time(os.getpid())


def _dead_pid() -> int:
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    return finished.pid


def _closed_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def _record(path: Path, port: int, token: bytes, *, pid: int | None = None,
            start_time: int | None = None) -> None:
    ipc.ensure_private_directory(path.parent)
    ipc.write_endpoint(path, ipc.Endpoint(
        port=port, token=token, pid=pid or os.getpid(),
        start_time=start_time or _own_start_time()))


def _leave_stale_endpoint(path: Path) -> None:
    """What a killed server leaves behind: an owned endpoint nobody listens on."""
    ipc.ensure_private_directory(path.parent)
    if IS_WINDOWS:
        _record(path, _closed_port(), os.urandom(32), pid=_dead_pid(), start_time=1)
        return
    old_umask = os.umask(0o177)
    try:
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(path))
        stale.close()
    finally:
        os.umask(old_umask)


async def _listen(behaviour):
    server = await asyncio.start_server(behaviour, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _silent(reader, writer):
    """Accept and never answer: a hung or impostor listener."""
    await reader.read()
    writer.close()


async def _random_reply(reader, writer):
    """Answer the nonce with garbage: another process reused the port."""
    await reader.readexactly(ipc.NONCE_BYTES)
    writer.write(os.urandom(ipc.NONCE_BYTES + ipc.MAC_BYTES))
    await writer.drain()
    await reader.read()
    writer.close()


async def _stop(listener) -> None:
    listener.close()
    await listener.wait_closed()


def _spy_refusals(monkeypatch) -> list:
    refusals = []
    original = asyncio.open_connection

    async def spy(*args, **kwargs):
        try:
            return await original(*args, **kwargs)
        except ConnectionRefusedError:
            refusals.append(True)
            raise

    monkeypatch.setattr(ipc.asyncio, "open_connection", spy)
    return refusals


windows_reclaim = pytest.mark.windows_only(
    reason="endpoint records and their reclaim rules exist only on Windows")


@windows_reclaim
@pytest.mark.asyncio
async def test_reclaim_refuses_a_live_server(endpoint_path):
    token = os.urandom(32)

    async def genuine(reader, writer):
        await ipc._server_handshake(reader, writer, token)
        await reader.read()
        writer.close()

    listener, port = await _listen(genuine)
    try:
        _record(endpoint_path, port, token)
        with pytest.raises(ipc.AlreadyRunningError, match="already running"):
            await ipc._reclaim_windows(endpoint_path)
    finally:
        await _stop(listener)
    assert ipc.read_endpoint(endpoint_path).port == port


@windows_reclaim
@pytest.mark.asyncio
async def test_reclaim_removes_a_record_whose_port_is_closed(endpoint_path, monkeypatch):
    # The owner is dead and nothing listens; wait for the ~2 s Windows refusal.
    monkeypatch.setattr(ipc, "_PROBE_QUICK_CONNECT_TIMEOUT", ipc._PROBE_CONNECT_TIMEOUT)
    refusals = _spy_refusals(monkeypatch)
    _leave_stale_endpoint(endpoint_path)
    await ipc._reclaim_windows(endpoint_path)
    assert refusals
    assert not endpoint_path.exists()


@windows_reclaim
@pytest.mark.asyncio
async def test_reclaim_refuses_a_live_owner_whose_full_backlog_refuses(endpoint_path, monkeypatch):
    # A stalled server with a full backlog refuses like a closed port (~2 s on
    # Windows); while its owner runs, that must not hand the endpoint over.
    refusals = _spy_refusals(monkeypatch)
    stalled = socket.socket()
    stalled.bind(("127.0.0.1", 0))
    stalled.listen(1)
    port = stalled.getsockname()[1]
    filler = socket.create_connection(("127.0.0.1", port), timeout=1)
    try:
        _record(endpoint_path, port, os.urandom(32))
        with pytest.raises(ipc.UnsafeEndpointError, match="cannot verify"):
            await ipc._reclaim_windows(endpoint_path)
    finally:
        filler.close()
        stalled.close()
    assert refusals
    assert ipc.read_endpoint(endpoint_path).port == port


@windows_reclaim
@pytest.mark.asyncio
async def test_reclaim_treats_a_reused_pid_as_a_dead_owner(endpoint_path, monkeypatch):
    # Same pid, another creation time: the recorded owner is gone, so the
    # refused port is stale.
    monkeypatch.setattr(ipc, "_PROBE_QUICK_CONNECT_TIMEOUT", ipc._PROBE_CONNECT_TIMEOUT)
    refusals = _spy_refusals(monkeypatch)
    _record(endpoint_path, _closed_port(), os.urandom(32),
            pid=os.getpid(), start_time=_own_start_time() + 1)
    await ipc._reclaim_windows(endpoint_path)
    assert refusals
    assert not endpoint_path.exists()


@windows_reclaim
@pytest.mark.asyncio
async def test_reclaim_of_a_dead_owner_does_not_wait_for_the_refusal(endpoint_path):
    # A crashed server's record must not cost the ~2 s Windows refusal: a
    # restarted broker has to publish within claude-remote's 3 s readiness wait.
    _record(endpoint_path, _closed_port(), os.urandom(32), pid=_dead_pid(), start_time=1)
    loop = asyncio.get_running_loop()
    started = loop.time()
    await ipc._reclaim_windows(endpoint_path)
    assert loop.time() - started < 1.5
    assert not endpoint_path.exists()


@windows_reclaim
@pytest.mark.asyncio
async def test_reclaim_removes_a_record_whose_port_was_taken_by_another_process(endpoint_path):
    listener, port = await _listen(_random_reply)
    try:
        _record(endpoint_path, port, os.urandom(32), pid=_dead_pid(), start_time=1)
        await ipc._reclaim_windows(endpoint_path)
    finally:
        await _stop(listener)
    assert not endpoint_path.exists()


@windows_reclaim
@pytest.mark.asyncio
async def test_reclaim_refuses_a_wrong_proof_while_the_owner_runs(endpoint_path):
    # A listener sharing a live owner's port is no evidence the owner stopped.
    listener, port = await _listen(_random_reply)
    try:
        _record(endpoint_path, port, os.urandom(32))
        with pytest.raises(ipc.UnsafeEndpointError, match="cannot verify"):
            await ipc._reclaim_windows(endpoint_path)
    finally:
        await _stop(listener)
    assert endpoint_path.exists()


@windows_reclaim
@pytest.mark.asyncio
async def test_reclaim_keeps_a_record_replaced_while_probing(endpoint_path, monkeypatch):
    _record(endpoint_path, _closed_port(), os.urandom(32), pid=_dead_pid(), start_time=1)
    newer = ipc.Endpoint(port=_closed_port(), token=os.urandom(32),
                         pid=os.getpid(), start_time=_own_start_time())

    async def republished(endpoint):
        endpoint_path.unlink()
        ipc.write_endpoint(endpoint_path, newer)
        return "gone"

    monkeypatch.setattr(ipc, "_probe", republished)
    with pytest.raises(ipc.UnsafeEndpointError, match="changed while probing"):
        await ipc._reclaim_windows(endpoint_path)
    assert ipc.read_endpoint(endpoint_path) == newer


@windows_reclaim
@pytest.mark.asyncio
async def test_reclaim_refuses_an_unverifiable_record_of_a_live_owner(endpoint_path, monkeypatch):
    # A live owner whose loop is busy must never lose its endpoint to a second starter.
    monkeypatch.setattr(ipc, "HANDSHAKE_TIMEOUT", 0.2)
    listener, port = await _listen(_silent)
    try:
        _record(endpoint_path, port, os.urandom(32))
        with pytest.raises(ipc.UnsafeEndpointError, match="cannot verify"):
            await ipc._reclaim_windows(endpoint_path)
    finally:
        await _stop(listener)
    assert endpoint_path.exists()


@windows_reclaim
@pytest.mark.asyncio
async def test_reclaim_removes_an_unverifiable_record_of_a_dead_owner(endpoint_path, monkeypatch):
    monkeypatch.setattr(ipc, "HANDSHAKE_TIMEOUT", 0.2)
    listener, port = await _listen(_silent)
    try:
        _record(endpoint_path, port, os.urandom(32), pid=_dead_pid(), start_time=1)
        await ipc._reclaim_windows(endpoint_path)
    finally:
        await _stop(listener)
    assert not endpoint_path.exists()


@windows_reclaim
@pytest.mark.asyncio
async def test_reclaim_ignores_a_missing_record_and_refuses_foreign_files(endpoint_path):
    ipc.ensure_private_directory(endpoint_path.parent)
    await ipc._reclaim_windows(endpoint_path)
    endpoint_path.write_text("do not replace", encoding="utf-8")
    with pytest.raises(ipc.UnsafeEndpointError, match="refusing to replace"):
        await ipc._reclaim_windows(endpoint_path)
    assert endpoint_path.read_text(encoding="utf-8") == "do not replace"


@windows_reclaim
@pytest.mark.asyncio
async def test_start_lock_serializes_starters_and_gives_up(endpoint_path, monkeypatch):
    ipc.ensure_private_directory(endpoint_path.parent)
    order = []

    async def starter(name: str, hold: float) -> None:
        async with ipc._start_lock(endpoint_path):
            order.append(f"{name}+")
            await asyncio.sleep(hold)
            order.append(f"{name}-")

    first = asyncio.create_task(starter("a", 0.3))
    await asyncio.sleep(0.05)
    await starter("b", 0)
    await first
    assert order == ["a+", "a-", "b+", "b-"]

    monkeypatch.setattr(ipc, "_START_LOCK_TIMEOUT", 0.2)
    holder = asyncio.create_task(starter("c", 1.0))
    await asyncio.sleep(0.05)
    with pytest.raises(ipc.AlreadyRunningError, match="another server is starting"):
        await starter("d", 0)
    await holder


@windows_reclaim
@pytest.mark.asyncio
async def test_start_lock_is_released_on_error_and_cancellation(endpoint_path, monkeypatch):
    ipc.ensure_private_directory(endpoint_path.parent)
    monkeypatch.setattr(ipc, "_START_LOCK_TIMEOUT", 0.5)

    with pytest.raises(RuntimeError):
        async with ipc._start_lock(endpoint_path):
            raise RuntimeError("publish failed")
    async with ipc._start_lock(endpoint_path):
        pass

    held = asyncio.Event()

    async def hold_forever() -> None:
        async with ipc._start_lock(endpoint_path):
            held.set()
            await asyncio.sleep(3600)

    holder = asyncio.create_task(hold_forever())
    await asyncio.wait_for(held.wait(), 1.0)
    holder.cancel()
    with pytest.raises(asyncio.CancelledError):
        await holder
    async with ipc._start_lock(endpoint_path):
        pass


async def _ping(path: Path, payload: bytes = b"ping") -> bytes:
    reader, writer = await ipc.open_connection(path)
    try:
        writer.write(payload)
        await writer.drain()
        return await reader.readexactly(len(payload))
    finally:
        writer.close()


@pytest.mark.asyncio
async def test_round_trip_and_endpoint_removed_on_close(endpoint_path):
    async with await ipc.start_server(endpoint_path, _echo) as server:
        assert server.path == endpoint_path
        assert server.is_serving()
        assert await _ping(endpoint_path) == b"PING"
    assert not endpoint_path.exists()


@pytest.mark.asyncio
async def test_second_server_is_refused_while_the_first_is_live(endpoint_path):
    first = await ipc.start_server(endpoint_path, _echo)
    try:
        with pytest.raises(ipc.AlreadyRunningError, match="already running"):
            await ipc.start_server(endpoint_path, _echo)
        assert await _ping(endpoint_path, b"x") == b"X"
    finally:
        first.close()
        await first.wait_closed()


@pytest.mark.asyncio
async def test_a_foreign_file_at_the_endpoint_is_never_replaced(endpoint_path):
    endpoint_path.write_text("do not replace", encoding="utf-8")
    with pytest.raises(ipc.UnsafeEndpointError, match="refusing to replace"):
        await ipc.start_server(endpoint_path, _echo)
    assert endpoint_path.read_text(encoding="utf-8") == "do not replace"


@pytest.mark.asyncio
async def test_a_stale_endpoint_refuses_clients_and_is_reclaimed(endpoint_path):
    _leave_stale_endpoint(endpoint_path)
    with pytest.raises(ConnectionRefusedError):
        await ipc.open_connection(endpoint_path)
    async with await ipc.start_server(endpoint_path, _echo):
        assert await _ping(endpoint_path) == b"PING"


@pytest.mark.asyncio
async def test_missing_endpoint_raises_file_not_found(endpoint_path):
    with pytest.raises(FileNotFoundError):
        await ipc.open_connection(endpoint_path)
    with pytest.raises(FileNotFoundError):
        ipc.connect_sync(endpoint_path)


@pytest.mark.asyncio
async def test_connect_sync_returns_a_blocking_authenticated_socket(endpoint_path):
    def talk() -> bytes:
        with ipc.connect_sync(endpoint_path) as sock:
            assert sock.gettimeout() is None
            sock.sendall(b"sync")
            data = b""
            while len(data) < 4:
                data += sock.recv(4 - len(data))
            return data

    async with await ipc.start_server(endpoint_path, _echo):
        assert await asyncio.to_thread(talk) == b"SYNC"


@pytest.mark.asyncio
async def test_reader_limit_applies_to_server_streams(endpoint_path):
    seen = []

    async def handler(reader, writer):
        try:
            await reader.readline()
        except ValueError:
            seen.append("limit")
        finally:
            writer.close()

    async with await ipc.start_server(endpoint_path, handler, limit=64):
        reader, writer = await ipc.open_connection(endpoint_path)
        writer.write(b"x" * 200 + b"\n")
        await writer.drain()
        await asyncio.wait_for(reader.read(), 2)
        writer.close()
    assert seen == ["limit"]


@pytest.mark.asyncio
async def test_close_leaves_a_newer_endpoint_alone(endpoint_path):
    server = await ipc.start_server(endpoint_path, _echo)
    endpoint_path.unlink()
    endpoint_path.write_text("newer", encoding="utf-8")
    server.close()
    await server.wait_closed()
    assert endpoint_path.read_text(encoding="utf-8") == "newer"


windows_endpoint = pytest.mark.windows_only(
    reason="the HMAC endpoint replaces AF_UNIX only on Windows")


@windows_endpoint
@pytest.mark.asyncio
async def test_wrong_token_is_rejected_before_the_handler(endpoint_path):
    calls = []

    async def handler(reader, writer):
        calls.append(1)
        writer.close()

    async with await ipc.start_server(endpoint_path, handler) as server:
        forged = endpoint_path.with_name("forged.sock")
        ipc.write_endpoint(forged, dataclasses.replace(
            ipc.read_endpoint(endpoint_path), token=os.urandom(32)))
        with pytest.raises(ipc.AuthenticationError):
            await ipc.open_connection(forged)
        await _wait_until(lambda: server.auth_failures == 1)
    assert calls == []


@windows_endpoint
@pytest.mark.asyncio
async def test_impostor_on_the_recorded_port_never_learns_the_token(endpoint_path):
    received = bytearray()
    finished = asyncio.Event()

    async def impostor(reader, writer):
        received.extend(await reader.readexactly(ipc.NONCE_BYTES))
        writer.write(os.urandom(ipc.NONCE_BYTES + ipc.MAC_BYTES))
        await writer.drain()
        received.extend(await reader.read())
        writer.close()
        finished.set()

    listener = await asyncio.start_server(impostor, "127.0.0.1", 0)
    token = os.urandom(32)
    _record(endpoint_path, listener.sockets[0].getsockname()[1], token)
    try:
        with pytest.raises(ipc.AuthenticationError):
            await ipc.open_connection(endpoint_path)
        await asyncio.wait_for(finished.wait(), 2)
    finally:
        listener.close()
        await listener.wait_closed()
    assert len(received) == ipc.NONCE_BYTES
    assert token not in bytes(received)


@windows_endpoint
@pytest.mark.asyncio
async def test_dead_owner_is_refused_before_any_connection(endpoint_path):
    connections = []

    async def impostor(reader, writer):
        connections.append(1)
        writer.close()

    listener = await asyncio.start_server(impostor, "127.0.0.1", 0)
    _record(endpoint_path, listener.sockets[0].getsockname()[1], os.urandom(32),
            pid=_dead_pid(), start_time=1)
    try:
        with pytest.raises(ConnectionRefusedError):
            await ipc.open_connection(endpoint_path)
        with pytest.raises(ConnectionRefusedError):
            ipc.connect_sync(endpoint_path)
        await asyncio.sleep(0.1)
    finally:
        listener.close()
        await listener.wait_closed()
    assert connections == []


@windows_endpoint
@pytest.mark.asyncio
async def test_a_reused_pid_with_another_start_time_is_refused(endpoint_path):
    async with await ipc.start_server(endpoint_path, _echo):
        record = ipc.read_endpoint(endpoint_path)
        moved = endpoint_path.with_name("moved.sock")
        ipc.write_endpoint(moved, dataclasses.replace(record, start_time=record.start_time + 1))
        with pytest.raises(ConnectionRefusedError):
            await ipc.open_connection(moved)


@windows_endpoint
@pytest.mark.asyncio
async def test_a_non_private_endpoint_record_is_refused(endpoint_path):
    async with await ipc.start_server(endpoint_path, _echo):
        share_with_everyone(endpoint_path)
        with pytest.raises(ipc.UnsafeEndpointError, match="private"):
            await ipc.open_connection(endpoint_path)
        with pytest.raises(ipc.UnsafeEndpointError, match="private"):
            ipc.connect_sync(endpoint_path)


@windows_endpoint
@pytest.mark.asyncio
async def test_a_silent_peer_is_dropped_after_the_handshake_timeout(endpoint_path, monkeypatch):
    monkeypatch.setattr(ipc, "HANDSHAKE_TIMEOUT", 0.2)
    async with await ipc.start_server(endpoint_path, _echo) as server:
        record = ipc.read_endpoint(endpoint_path)
        reader, writer = await asyncio.open_connection("127.0.0.1", record.port)
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        await _wait_until(lambda: server.auth_failures == 1)


@windows_endpoint
@pytest.mark.asyncio
async def test_listener_is_loopback_only_and_exclusive(endpoint_path):
    async with await ipc.start_server(endpoint_path, _echo) as server:
        record = ipc.read_endpoint(endpoint_path)
        listening = server._server.sockets[0]
        assert listening.getsockname()[0] == "127.0.0.1"
        # Windows 10 already refuses this same-address bind by default, so the
        # option itself is asserted as well.
        assert listening.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE) == 1
        intruder = socket.socket()
        intruder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            with pytest.raises(OSError):
                intruder.bind(("127.0.0.1", record.port))
        finally:
            intruder.close()


@windows_endpoint
@pytest.mark.asyncio
async def test_simultaneous_starters_on_a_stale_endpoint_leave_exactly_one_server(
        endpoint_path, monkeypatch):
    _leave_stale_endpoint(endpoint_path)
    real_probe = ipc._probe
    calls = []

    async def slow_second_probe(endpoint):
        # Without the start lock the second starter would wake up after the
        # first one published and delete that fresh record.
        verdict = await real_probe(endpoint)
        calls.append(verdict)
        if len(calls) == 2:
            await asyncio.sleep(0.5)
        return verdict

    monkeypatch.setattr(ipc, "_probe", slow_second_probe)
    results = await asyncio.gather(
        ipc.start_server(endpoint_path, _echo), ipc.start_server(endpoint_path, _echo),
        return_exceptions=True)
    servers = [item for item in results if isinstance(item, ipc.IpcServer)]
    try:
        assert len(servers) == 1
        assert [type(item) for item in results if item not in servers] == [ipc.AlreadyRunningError]
        assert calls == ["gone", "live"]
        published = ipc.read_endpoint(endpoint_path).port
        assert published == servers[0]._server.sockets[0].getsockname()[1]
        assert await _ping(endpoint_path) == b"PING"
    finally:
        for server in servers:
            server.close()
            await server.wait_closed()


@pytest.mark.asyncio
async def test_close_withdraws_the_endpoint_before_the_listener_stops(endpoint_path):
    server = await ipc.start_server(endpoint_path, _echo)
    stop_listening = server._server.close
    seen = []

    def close_listener():
        seen.append(endpoint_path.exists())
        stop_listening()

    server._server.close = close_listener
    server.close()
    await server.wait_closed()
    assert seen == [False]


posix_endpoint = pytest.mark.posix_only(reason="AF_UNIX peer credentials are POSIX-only")


@posix_endpoint
@pytest.mark.asyncio
async def test_posix_socket_is_0600_inside_a_0700_directory(endpoint_path):
    async with await ipc.start_server(endpoint_path, _echo):
        info = endpoint_path.lstat()
        assert stat.S_ISSOCK(info.st_mode)
        assert stat.S_IMODE(info.st_mode) == 0o600
        assert stat.S_IMODE(endpoint_path.parent.stat().st_mode) == 0o700


@posix_endpoint
@pytest.mark.asyncio
async def test_server_drops_a_peer_with_another_uid(endpoint_path, monkeypatch):
    calls = []

    async def handler(reader, writer):
        calls.append(1)
        writer.close()

    real = ipc._peer_uid
    # Accepted sockets are bound to the endpoint path; the client side is not.
    monkeypatch.setattr(
        ipc, "_peer_uid", lambda sock: real(sock) + (1 if sock.getsockname() else 0))
    async with await ipc.start_server(endpoint_path, handler) as server:
        reader, writer = await ipc.open_connection(endpoint_path)
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        await _wait_until(lambda: server.auth_failures == 1)
    assert calls == []


@posix_endpoint
@pytest.mark.asyncio
async def test_client_refuses_a_server_with_another_uid(endpoint_path, monkeypatch):
    async with await ipc.start_server(endpoint_path, _echo):
        monkeypatch.setattr(ipc, "_peer_uid", lambda sock: os.getuid() + 1)
        with pytest.raises(ipc.AuthenticationError):
            await ipc.open_connection(endpoint_path)
        with pytest.raises(ipc.AuthenticationError):
            ipc.connect_sync(endpoint_path)


@posix_endpoint
@pytest.mark.asyncio
async def test_client_refuses_a_socket_with_loose_permissions(endpoint_path):
    async with await ipc.start_server(endpoint_path, _echo):
        endpoint_path.chmod(0o666)
        with pytest.raises(ipc.UnsafeEndpointError):
            await ipc.open_connection(endpoint_path)


@pytest.mark.asyncio
async def test_close_is_idempotent_and_never_withdraws_a_later_endpoint(endpoint_path):
    server = await ipc.start_server(endpoint_path, _echo)
    server.close()
    await server.wait_closed()
    assert not endpoint_path.exists()
    endpoint_path.write_text("later", encoding="utf-8")
    server.close()
    await server.wait_closed()
    assert not server.is_serving()
    assert endpoint_path.read_text(encoding="utf-8") == "later"


def _lock_is_held(path: Path) -> bool:
    fd = os.open(path.with_name(path.name + ".lock"), os.O_RDWR)
    try:
        ipc.locks.flock(fd, ipc.locks.LOCK_EX | ipc.locks.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)  # also releases a lock taken just above
    return False


@windows_endpoint
@pytest.mark.asyncio
async def test_reclaim_and_publish_both_run_inside_the_start_lock(endpoint_path, monkeypatch):
    _leave_stale_endpoint(endpoint_path)
    real_reclaim, real_write = ipc._reclaim_windows, ipc.write_endpoint
    seen = []

    async def reclaim(target):
        seen.append(("reclaim", _lock_is_held(target), perms.is_private(target.parent)))
        await real_reclaim(target)

    def write(target, endpoint):
        seen.append(("publish", _lock_is_held(target), target.exists()))
        return real_write(target, endpoint)

    monkeypatch.setattr(ipc, "_reclaim_windows", reclaim)
    monkeypatch.setattr(ipc, "write_endpoint", write)
    async with await ipc.start_server(endpoint_path, _echo):
        assert seen == [("reclaim", True, True), ("publish", True, False)]
        assert not _lock_is_held(endpoint_path)


def _secret_forms(token: bytes) -> list[str]:
    return [token.hex(), base64.b64encode(token).decode("ascii"), repr(token)]


def _assert_reveals_nothing(exc: BaseException, token: bytes) -> None:
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        text = f"{exc!s} {exc!r}"
        assert not any(form in text for form in _secret_forms(token))
        assert token not in [arg for arg in exc.args if isinstance(arg, bytes)]
        exc = exc.__cause__ or exc.__context__


async def _hang_up(reader, writer):
    """Read the nonce and close: the peer quit in the middle of the handshake."""
    await reader.readexactly(ipc.NONCE_BYTES)
    writer.close()


async def _reset(reader, writer):
    """Read the nonce and abort with a TCP reset."""
    await reader.readexactly(ipc.NONCE_BYTES)
    writer.get_extra_info("socket").setsockopt(
        socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("HH", 1, 0))
    writer.transport.abort()


@windows_endpoint
@pytest.mark.asyncio
@pytest.mark.parametrize("behaviour", [_silent, _hang_up, _reset, _random_reply],
                         ids=["silent", "hang-up", "reset", "wrong-proof"])
async def test_client_handshake_failures_are_bounded_authentication_errors(
        endpoint_path, behaviour):
    accepted = []

    async def tracked(reader, writer):
        accepted.append(writer)
        await behaviour(reader, writer)

    listener, port = await _listen(tracked)
    token = os.urandom(32)
    _record(endpoint_path, port, token)
    loop = asyncio.get_running_loop()
    caught = []
    try:
        started = loop.time()
        with pytest.raises(ipc.AuthenticationError) as raised:
            # The outer bound turns a missing handshake timeout into a failure.
            await asyncio.wait_for(ipc.open_connection(endpoint_path, timeout=0.3), 2)
        caught.append(raised.value)
        started_sync = time.monotonic()
        with pytest.raises(ipc.AuthenticationError) as raised:
            await asyncio.wait_for(
                asyncio.to_thread(ipc.connect_sync, endpoint_path, timeout=0.3), 2)
        caught.append(raised.value)
        assert time.monotonic() - started_sync < 1.5
        assert loop.time() - started < 3.0
    finally:
        for writer in accepted:  # unblocks a client that ignored its timeout
            writer.transport.abort()
        await _stop(listener)
    for exc in caught:
        _assert_reveals_nothing(exc, token)


@windows_endpoint
@pytest.mark.asyncio
async def test_sync_client_reveals_only_a_fresh_nonce_to_an_impostor(endpoint_path):
    received = bytearray()
    finished = asyncio.Event()

    async def impostor(reader, writer):
        received.extend(await reader.readexactly(ipc.NONCE_BYTES))
        writer.write(os.urandom(ipc.NONCE_BYTES + ipc.MAC_BYTES))
        await writer.drain()
        received.extend(await reader.read())
        writer.close()
        finished.set()

    listener, port = await _listen(impostor)
    token = os.urandom(32)
    _record(endpoint_path, port, token)
    try:
        with pytest.raises(ipc.AuthenticationError):
            await asyncio.to_thread(ipc.connect_sync, endpoint_path)
        await asyncio.wait_for(finished.wait(), 2)
    finally:
        await _stop(listener)
    assert len(received) == ipc.NONCE_BYTES
    assert token not in bytes(received)


@windows_endpoint
@pytest.mark.asyncio
async def test_sync_client_with_a_wrong_token_is_rejected_before_the_handler(endpoint_path):
    calls = []

    async def handler(reader, writer):
        calls.append(1)
        writer.close()

    async with await ipc.start_server(endpoint_path, handler) as server:
        forged = endpoint_path.with_name("forged.sock")
        ipc.write_endpoint(forged, dataclasses.replace(
            ipc.read_endpoint(endpoint_path), token=os.urandom(32)))
        with pytest.raises(ipc.AuthenticationError):
            await asyncio.to_thread(ipc.connect_sync, forged)
        await _wait_until(lambda: server.auth_failures == 1)
    assert calls == []


@windows_endpoint
@pytest.mark.asyncio
async def test_start_over_a_dead_owners_silent_port_fits_the_readiness_budget(endpoint_path):
    # claude-remote waits 3 s for an auto-started service to become ready.
    listener, port = await _listen(_silent)
    _record(endpoint_path, port, os.urandom(32), pid=_dead_pid(), start_time=1)
    loop = asyncio.get_running_loop()
    try:
        started = loop.time()
        async with await ipc.start_server(endpoint_path, _echo):
            assert loop.time() - started < 3.0
            assert await _ping(endpoint_path) == b"PING"
    finally:
        await _stop(listener)


@windows_endpoint
@pytest.mark.asyncio
async def test_cancelling_serve_forever_withdraws_the_endpoint_before_the_port(endpoint_path):
    server = await ipc.start_server(endpoint_path, _echo)
    stop_listening = server._server.close
    seen = []

    def close_listener():
        seen.append(endpoint_path.exists())
        stop_listening()

    server._server.close = close_listener
    serving = asyncio.ensure_future(server.serve_forever())
    assert await _ping(endpoint_path) == b"PING"
    serving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await serving
    await server.wait_closed()
    assert seen and not any(seen)
    assert not endpoint_path.exists()
    async with await ipc.start_server(endpoint_path, _echo):
        assert await _ping(endpoint_path) == b"PING"


def _fail_endpoint_unlink(monkeypatch, path: Path, errors: list) -> list:
    """Raise ``errors`` in turn from unlinking ``path``, then really unlink it."""
    calls = []
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self == path:
            calls.append(1)
            if errors:
                raise errors.pop(0)
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    return calls


@windows_endpoint
@pytest.mark.asyncio
async def test_close_retries_a_sharing_violation_on_the_endpoint(endpoint_path, monkeypatch):
    server = await ipc.start_server(endpoint_path, _echo)
    calls = _fail_endpoint_unlink(monkeypatch, endpoint_path, [
        OSError(errno.EACCES, "sharing violation", None, 32)])
    server.close()
    await server.wait_closed()
    assert len(calls) == 2
    assert not endpoint_path.exists()
    assert not server.is_serving()


@windows_endpoint
@pytest.mark.asyncio
async def test_close_stops_listening_before_raising_a_withdrawal_failure(
        endpoint_path, monkeypatch):
    server = await ipc.start_server(endpoint_path, _echo)
    port = ipc.read_endpoint(endpoint_path).port
    calls = _fail_endpoint_unlink(monkeypatch, endpoint_path, [
        OSError(errno.EACCES, "access denied", None, 5)])
    with pytest.raises(PermissionError, match="access denied"):
        server.close()
    assert len(calls) == 1
    assert not server.is_serving()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=3).close()
    await asyncio.wait_for(server.wait_closed(), 2)


@windows_endpoint
@pytest.mark.asyncio
async def test_sync_handshake_timeout_bounds_the_whole_handshake(endpoint_path):
    accepted = []

    async def dribble(reader, writer):
        accepted.append(writer)
        await reader.readexactly(ipc.NONCE_BYTES)
        for _ in range(ipc.NONCE_BYTES + ipc.MAC_BYTES):
            writer.write(os.urandom(1))
            await writer.drain()
            await asyncio.sleep(0.3)

    listener, port = await _listen(dribble)
    _record(endpoint_path, port, os.urandom(32))
    try:
        started = time.monotonic()
        with pytest.raises(ipc.AuthenticationError):
            await asyncio.wait_for(
                asyncio.to_thread(ipc.connect_sync, endpoint_path, timeout=1), 5)
        assert time.monotonic() - started < 1.5
    finally:
        for writer in accepted:
            writer.transport.abort()
        await _stop(listener)


@windows_endpoint
@pytest.mark.asyncio
async def test_close_withdraws_its_own_record_when_the_file_id_differs(endpoint_path):
    server = await ipc.start_server(endpoint_path, _echo)
    server._identity = (-1, -1)
    server.close()
    await server.wait_closed()
    assert not endpoint_path.exists()

    server = await ipc.start_server(endpoint_path, _echo)
    published = ipc.read_endpoint(endpoint_path)
    endpoint_path.unlink()
    other = dataclasses.replace(published, token=os.urandom(32))
    ipc.write_endpoint(endpoint_path, other)
    server._identity = (-1, -1)
    server.close()
    await server.wait_closed()
    assert ipc.read_endpoint(endpoint_path) == other
