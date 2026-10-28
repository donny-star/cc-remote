"""cc_remote.platform.ipc: same-user local endpoints."""
from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
import uuid

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
