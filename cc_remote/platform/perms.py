"""Owner-only files and directories: POSIX mode bits or a Windows DACL."""
from __future__ import annotations

import errno
import os
import stat
from os import PathLike

from cc_remote.platform import IS_WINDOWS
from cc_remote.platform._win32err import translated
from cc_remote.platform.identity import current_sid, current_uid

StrPath = str | PathLike[str]


def make_private(path: StrPath, *, directory: bool) -> None:
    if not IS_WINDOWS:
        os.chmod(path, 0o700 if directory else 0o600)
        return
    import ntsecuritycon
    import win32security

    with translated(os.fspath(path)):
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

    descriptor = _named_security_info(
        path, win32security.OWNER_SECURITY_INFORMATION
        | win32security.DACL_SECURITY_INFORMATION)
    if descriptor is None:
        return False
    try:
        return _descriptor_is_private(descriptor, directory=os.path.isdir(path))
    except Exception:
        # Fail-closed gate over a side-effect-free evaluation: any inspection failure means "not private".
        return False


def is_private_fd(fd: int) -> bool:
    """:func:`is_private` for an open descriptor (no path re-resolution)."""
    if not IS_WINDOWS:
        info = os.fstat(fd)
        return info.st_uid == current_uid() and not stat.S_IMODE(info.st_mode) & 0o077
    import win32security

    try:
        descriptor = _handle_security_info(
            fd, win32security.OWNER_SECURITY_INFORMATION
            | win32security.DACL_SECURITY_INFORMATION)
        return _descriptor_is_private(
            descriptor, directory=stat.S_ISDIR(os.fstat(fd).st_mode))
    except Exception:
        return False  # unverifiable is not private


def owned_by_current_user(target: int | StrPath) -> bool:
    """True when the current user owns ``target`` (a descriptor or a path).

    Windows also accepts the BUILTIN Administrators group as owner, matching
    :func:`is_private`; elevated processes create files owned by that group.
    """
    if not IS_WINDOWS:
        info = os.fstat(target) if isinstance(target, int) else os.stat(target)
        return info.st_uid == current_uid()
    import win32security

    try:
        if isinstance(target, int):
            descriptor = _handle_security_info(
                target, win32security.OWNER_SECURITY_INFORMATION)
        else:
            descriptor = _named_security_info(
                target, win32security.OWNER_SECURITY_INFORMATION)
            if descriptor is None:
                return False
        user, admins, _trusted = _trusted_sids()
        owner = descriptor.GetSecurityDescriptorOwner()
        return owner == user or owner == admins
    except FileNotFoundError:
        raise
    except Exception:
        return False


def _named_security_info(path: StrPath, info: int):
    """The path's security descriptor, or None when it cannot be read."""
    import pywintypes
    import win32security

    try:
        return win32security.GetNamedSecurityInfo(
            os.fspath(path), win32security.SE_FILE_OBJECT, info)
    except pywintypes.error as exc:
        # ERROR_FILE_NOT_FOUND / ERROR_PATH_NOT_FOUND; anything else is
        # unverifiable and therefore not private.
        if exc.winerror in (2, 3):
            raise FileNotFoundError(errno.ENOENT, exc.strerror, os.fspath(path)) from exc
        return None


def _handle_security_info(fd: int, info: int):
    import msvcrt

    import win32security

    return win32security.GetSecurityInfo(
        msvcrt.get_osfhandle(fd), win32security.SE_FILE_OBJECT, info)


def _trusted_sids():
    import win32security

    user = win32security.ConvertStringSidToSid(current_sid())
    admins = win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid)
    trusted = (
        user,
        win32security.CreateWellKnownSid(win32security.WinLocalSystemSid),
        admins,
        win32security.ConvertStringSidToSid("S-1-3-4"),  # OWNER RIGHTS
    )
    return user, admins, trusted


def _descriptor_is_private(descriptor, *, directory: bool) -> bool:
    """Owner is the user (or administrators); only trusted SIDs are granted access.

    A directory's inheritable ACEs decide who can read the files later created
    in it, so they count as grants; CREATOR OWNER is trusted there because it
    only ever resolves to each new child's owner.
    """
    import win32security

    user, admins, trusted = _trusted_sids()
    owner = descriptor.GetSecurityDescriptorOwner()
    if not (owner == user or owner == admins):
        return False
    dacl = descriptor.GetSecurityDescriptorDacl()
    if dacl is None:  # a NULL DACL grants everyone full access
        return False
    creator_owner = win32security.ConvertStringSidToSid("S-1-3-0")
    for index in range(dacl.GetAceCount()):
        ace = dacl.GetAce(index)
        # Only plain allowed/denied ACEs have the ((type, flags), mask, sid)
        # shape; object, callback, audit or unknown ACEs fail closed.
        if not (isinstance(ace, tuple) and len(ace) == 3
                and isinstance(ace[0], tuple) and len(ace[0]) == 2):
            return False
        (ace_type, ace_flags), _mask, sid = ace
        if ace_type not in (win32security.ACCESS_ALLOWED_ACE_TYPE,
                            win32security.ACCESS_DENIED_ACE_TYPE):
            return False
        inherit_only = ace_flags & win32security.INHERIT_ONLY_ACE
        if inherit_only and not directory:
            continue
        if ace_type == win32security.ACCESS_DENIED_ACE_TYPE:
            continue
        if any(sid == candidate for candidate in trusted):
            continue
        if inherit_only and sid == creator_owner:
            continue
        return False
    return True
