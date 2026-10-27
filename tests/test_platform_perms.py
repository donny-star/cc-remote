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
