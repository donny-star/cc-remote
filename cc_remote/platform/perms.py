"""Owner-only files and directories: POSIX mode bits or a Windows DACL."""
from __future__ import annotations

import os
import stat
from os import PathLike

from cc_remote.platform import IS_WINDOWS
from cc_remote.platform.identity import current_sid, current_uid

StrPath = str | PathLike[str]


def make_private(path: StrPath, *, directory: bool) -> None:
    if not IS_WINDOWS:
        os.chmod(path, 0o700 if directory else 0o600)
        return
    import ntsecuritycon
    import win32security

    user = win32security.ConvertStringSidToSid(current_sid())
    system = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)
    inherit = (
        win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
        if directory else 0
    )
    dacl = win32security.ACL()
    for sid in (user, system):
        dacl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION, inherit, ntsecuritycon.FILE_ALL_ACCESS, sid)
    win32security.SetNamedSecurityInfo(
        os.fspath(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, dacl, None)


def is_private(path: StrPath) -> bool:
    """True when only the current user (plus SYSTEM/administrators) has access."""
    if not IS_WINDOWS:
        info = os.stat(path)
        return info.st_uid == current_uid() and not stat.S_IMODE(info.st_mode) & 0o077
    import win32security

    user = win32security.ConvertStringSidToSid(current_sid())
    admins = win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid)
    trusted = (
        user,
        win32security.CreateWellKnownSid(win32security.WinLocalSystemSid),
        admins,
        win32security.ConvertStringSidToSid("S-1-3-4"),  # OWNER RIGHTS
    )
    descriptor = win32security.GetNamedSecurityInfo(
        os.fspath(path), win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION
        | win32security.DACL_SECURITY_INFORMATION)
    owner = descriptor.GetSecurityDescriptorOwner()
    if not (owner == user or owner == admins):
        return False
    dacl = descriptor.GetSecurityDescriptorDacl()
    if dacl is None:  # a NULL DACL grants everyone full access
        return False
    for index in range(dacl.GetAceCount()):
        (ace_type, ace_flags), _mask, sid = dacl.GetAce(index)
        if ace_flags & win32security.INHERIT_ONLY_ACE:
            continue
        if ace_type == win32security.ACCESS_DENIED_ACE_TYPE:
            continue
        if ace_type != win32security.ACCESS_ALLOWED_ACE_TYPE:
            return False
        if not any(sid == candidate for candidate in trusted):
            return False
    return True
