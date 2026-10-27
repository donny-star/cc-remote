from __future__ import annotations

import os

import pytest

from cc_remote.platform import identity, perms


def test_current_uid_matches_stat_owner(tmp_path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"")
    assert os.stat(path).st_uid == identity.current_uid()
    assert identity.FOREIGN_UID != identity.current_uid()


def test_make_private_file_and_directory(tmp_path) -> None:
    directory = tmp_path / "d"
    directory.mkdir()
    perms.make_private(directory, directory=True)
    child = directory / "secret"
    child.write_bytes(b"x")
    perms.make_private(child, directory=False)
    assert perms.is_private(directory)
    assert perms.is_private(child)


@pytest.mark.posix_only(reason="mode bits are the POSIX privacy mechanism")
def test_group_readable_file_is_not_private(tmp_path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"")
    os.chmod(path, 0o640)
    assert not perms.is_private(path)


@pytest.mark.windows_only(reason="ACL entries are the Windows privacy mechanism")
def test_everyone_readable_file_is_not_private(tmp_path) -> None:
    import ntsecuritycon
    import win32security

    path = tmp_path / "f"
    path.write_bytes(b"")
    perms.make_private(path, directory=False)
    descriptor = win32security.GetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION)
    dacl = descriptor.GetSecurityDescriptorDacl()
    dacl.AddAccessAllowedAce(
        win32security.ACL_REVISION, ntsecuritycon.FILE_GENERIC_READ,
        win32security.CreateWellKnownSid(win32security.WinWorldSid))
    win32security.SetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION, None, None, dacl, None)
    assert not perms.is_private(path)


@pytest.mark.windows_only(reason="SIDs only exist on Windows")
def test_current_sid_is_a_user_sid() -> None:
    sid = identity.current_sid()
    assert sid is not None and sid.startswith("S-1-5-")


class _FakeDacl:
    def __init__(self, aces) -> None:
        self._aces = aces

    def GetAceCount(self) -> int:
        return len(self._aces)

    def GetAce(self, index):
        return self._aces[index]


class _FakeDescriptor:
    def __init__(self, owner, dacl) -> None:
        self._owner = owner
        self._dacl = dacl

    def GetSecurityDescriptorOwner(self):
        return self._owner

    def GetSecurityDescriptorDacl(self):
        return self._dacl


@pytest.mark.windows_only(reason="ACL entries are the Windows privacy mechanism")
def test_object_ace_fails_closed(tmp_path, monkeypatch) -> None:
    import win32security

    path = tmp_path / "f"
    path.write_bytes(b"")
    user = win32security.ConvertStringSidToSid(identity.current_sid())
    object_ace = ((5, 0), 0x1F01FF, b"guid", b"guid", user)
    descriptor = _FakeDescriptor(user, _FakeDacl([object_ace]))
    monkeypatch.setattr(
        win32security, "GetNamedSecurityInfo", lambda *args: descriptor)
    assert perms.is_private(path) is False


@pytest.mark.windows_only(reason="ACL entries are the Windows privacy mechanism")
def test_unreadable_security_info_fails_closed(tmp_path, monkeypatch) -> None:
    import pywintypes
    import win32security

    path = tmp_path / "f"
    path.write_bytes(b"")

    def denied(*args):
        raise pywintypes.error(5, "GetNamedSecurityInfo", "Access is denied.")

    monkeypatch.setattr(win32security, "GetNamedSecurityInfo", denied)
    assert perms.is_private(path) is False


@pytest.mark.windows_only(reason="ACL entries are the Windows privacy mechanism")
def test_missing_path_raises_file_not_found(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        perms.is_private(tmp_path / "missing")


@pytest.mark.windows_only(reason="ACL entries are the Windows privacy mechanism")
@pytest.mark.parametrize("failure", ["GetAceCount-denied", "GetAce-index"])
def test_dacl_inspection_error_fails_closed(tmp_path, monkeypatch, failure) -> None:
    import pywintypes
    import win32security

    path = tmp_path / "f"
    path.write_bytes(b"")
    user = win32security.ConvertStringSidToSid(identity.current_sid())

    class _FailingDacl(_FakeDacl):
        def GetAceCount(self) -> int:
            if failure == "GetAceCount-denied":
                raise pywintypes.error(5, "GetAceCount", "denied")
            return 1

        def GetAce(self, index):
            raise IndexError("ace index")

    descriptor = _FakeDescriptor(user, _FailingDacl([]))
    monkeypatch.setattr(
        win32security, "GetNamedSecurityInfo", lambda *args: descriptor)
    assert perms.is_private(path) is False


def _private_file(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(b"x")
    perms.make_private(path, directory=False)
    return path


def test_private_file_descriptor_is_private_and_owned(tmp_path) -> None:
    path = _private_file(tmp_path)
    fd = os.open(path, os.O_RDONLY)
    try:
        assert perms.is_private_fd(fd)
        assert perms.owned_by_current_user(fd)
    finally:
        os.close(fd)
    assert perms.owned_by_current_user(path)
    assert perms.owned_by_current_user(str(path))


def test_owned_by_current_user_missing_path_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        perms.owned_by_current_user(tmp_path / "missing")


@pytest.mark.posix_only(reason="mode bits are the POSIX privacy mechanism")
def test_group_readable_descriptor_is_not_private(tmp_path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"")
    os.chmod(path, 0o640)
    fd = os.open(path, os.O_RDONLY)
    try:
        assert not perms.is_private_fd(fd)
        assert perms.owned_by_current_user(fd)
    finally:
        os.close(fd)


def _grant_everyone_read(path, flags: int = 0) -> None:
    import ntsecuritycon
    import win32security

    descriptor = win32security.GetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION)
    dacl = descriptor.GetSecurityDescriptorDacl()
    dacl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION, flags, ntsecuritycon.FILE_GENERIC_READ,
        win32security.CreateWellKnownSid(win32security.WinWorldSid))
    win32security.SetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, dacl, None)


@pytest.mark.windows_only(reason="ACL entries are the Windows privacy mechanism")
def test_everyone_readable_descriptor_is_not_private(tmp_path) -> None:
    path = _private_file(tmp_path)
    _grant_everyone_read(path)
    fd = os.open(path, os.O_RDONLY)
    try:
        assert not perms.is_private_fd(fd)
        assert perms.owned_by_current_user(fd)
    finally:
        os.close(fd)


@pytest.mark.windows_only(reason="ACL entries are the Windows privacy mechanism")
def test_inherit_only_grant_makes_directory_not_private(tmp_path) -> None:
    import win32security

    directory = tmp_path / "d"
    directory.mkdir()
    perms.make_private(directory, directory=True)
    assert perms.is_private(directory)
    _grant_everyone_read(
        directory,
        win32security.INHERIT_ONLY_ACE | win32security.OBJECT_INHERIT_ACE)
    assert not perms.is_private(directory)


@pytest.mark.windows_only(reason="ACL entries are the Windows privacy mechanism")
def test_inherit_only_creator_owner_keeps_directory_private(tmp_path) -> None:
    import ntsecuritycon
    import win32security

    directory = tmp_path / "d"
    directory.mkdir()
    perms.make_private(directory, directory=True)
    descriptor = win32security.GetNamedSecurityInfo(
        str(directory), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION)
    dacl = descriptor.GetSecurityDescriptorDacl()
    dacl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION,
        win32security.INHERIT_ONLY_ACE | win32security.OBJECT_INHERIT_ACE
        | win32security.CONTAINER_INHERIT_ACE,
        ntsecuritycon.FILE_ALL_ACCESS,
        win32security.ConvertStringSidToSid("S-1-3-0"))
    win32security.SetNamedSecurityInfo(
        str(directory), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, dacl, None)
    assert perms.is_private(directory)


@pytest.mark.windows_only(reason="ACL entries are the Windows privacy mechanism")
def test_inherit_only_grant_is_ignored_on_a_file() -> None:
    import win32security

    user = win32security.ConvertStringSidToSid(identity.current_sid())
    world = win32security.CreateWellKnownSid(win32security.WinWorldSid)
    allowed = win32security.ACCESS_ALLOWED_ACE_TYPE
    aces = [((allowed, 0), 0x1F01FF, user),
            ((allowed, win32security.INHERIT_ONLY_ACE), 0x120089, world)]
    descriptor = _FakeDescriptor(user, _FakeDacl(aces))
    assert perms._descriptor_is_private(descriptor, directory=False)
    assert not perms._descriptor_is_private(descriptor, directory=True)


@pytest.mark.windows_only(reason="pywin32 errors only exist on Windows")
def test_make_private_failure_raises_permission_error(tmp_path, monkeypatch) -> None:
    import pywintypes
    import win32security

    path = tmp_path / "f"
    path.write_bytes(b"")

    def denied(*args):
        raise pywintypes.error(5, "X", "denied")

    monkeypatch.setattr(win32security, "SetNamedSecurityInfo", denied)
    with pytest.raises(PermissionError) as caught:
        perms.make_private(path, directory=False)
    assert caught.value.filename == str(path)
