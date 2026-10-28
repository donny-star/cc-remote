"""Privacy assertions that mean the same thing on POSIX and Windows."""
from __future__ import annotations

import os
import stat
import sys

from cc_remote.platform import perms


def assert_private_file(path: os.PathLike[str] | str) -> None:
    if sys.platform == "win32":
        assert perms.is_private(path), f"{path} is readable by other principals"
        return
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def assert_private_dir(path: os.PathLike[str] | str) -> None:
    if sys.platform == "win32":
        assert perms.is_private(path), f"{path} is accessible to other principals"
        return
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o700


def grant_foreign_read(path: os.PathLike[str] | str) -> None:
    """Make ``path`` readable beyond its owner: mode 0640, or an Everyone read ACE."""
    if sys.platform != "win32":
        os.chmod(path, 0o640)
        return
    import ntsecuritycon
    import win32security

    descriptor = win32security.GetNamedSecurityInfo(
        os.fspath(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION)
    dacl = descriptor.GetSecurityDescriptorDacl()
    dacl.AddAccessAllowedAce(
        win32security.ACL_REVISION, ntsecuritycon.FILE_GENERIC_READ,
        win32security.CreateWellKnownSid(win32security.WinWorldSid))
    win32security.SetNamedSecurityInfo(
        os.fspath(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION,
        None, None, dacl, None)
