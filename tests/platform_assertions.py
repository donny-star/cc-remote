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


def foreign_readable_dir(path: os.PathLike[str] | str) -> None:
    """Make files later created in ``path`` readable beyond their owner.

    POSIX sets mode 0755. Windows adds an inheritable Everyone read ACE, so a
    new file is private only if its creator restricts it explicitly (Python
    3.13 ``mkdir(mode=0o700)`` directories are private, making a tmp_path
    test of privacy otherwise vacuous).
    """
    if sys.platform != "win32":
        os.chmod(path, 0o755)
        return
    import ntsecuritycon
    import win32security

    descriptor = win32security.GetNamedSecurityInfo(
        os.fspath(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION)
    dacl = descriptor.GetSecurityDescriptorDacl()
    dacl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION,
        win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE,
        ntsecuritycon.FILE_GENERIC_READ,
        win32security.CreateWellKnownSid(win32security.WinWorldSid))
    win32security.SetNamedSecurityInfo(
        os.fspath(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION,
        None, None, dacl, None)


def security_snapshot(path: os.PathLike[str] | str) -> object:
    """Who may access ``path``: its mode bits, or its DACL and read-only bit.

    The Windows DACL is SDDL, which also records whether it is protected
    from inheritance. The auto-inheritance bookkeeping flags (``AI``/``AR``)
    grant nothing, and setting any DACL turns ``AI`` on, so they are dropped.
    """
    if sys.platform != "win32":
        return stat.S_IMODE(os.stat(path).st_mode)
    import win32security

    descriptor = win32security.GetNamedSecurityInfo(
        os.fspath(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION)
    dacl = win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
        descriptor, win32security.SDDL_REVISION_1,
        win32security.DACL_SECURITY_INFORMATION)
    flags, separator, aces = dacl.partition("(")
    flags = flags.replace("AI", "").replace("AR", "")
    readonly = bool(os.stat(path).st_file_attributes & stat.FILE_ATTRIBUTE_READONLY)
    return flags + separator + aces, readonly
