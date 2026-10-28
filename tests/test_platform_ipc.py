"""cc_remote.platform.ipc: same-user local endpoints."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import socket
import stat
import subprocess
import uuid
from pathlib import Path

import pytest

from cc_remote.platform import ipc, perms
from tests.ipc_helpers import share_with_everyone


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
