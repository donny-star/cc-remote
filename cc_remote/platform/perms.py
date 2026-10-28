"""Owner-only files and directories: POSIX mode bits or a Windows DACL."""
from __future__ import annotations

import errno
import os
import stat
from os import PathLike

from cc_remote.platform import IS_WINDOWS, files
from cc_remote.platform._win32err import translated
from cc_remote.platform.identity import current_euid, current_sid, current_uid

StrPath = str | PathLike[str]


def make_private(path: StrPath, *, directory: bool) -> None:
    if not IS_WINDOWS:
        os.chmod(path, 0o700 if directory else 0o600)
        return
    import win32security

    with translated(os.fspath(path)):
        win32security.SetNamedSecurityInfo(
            os.fspath(path), win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, _private_dacl(directory=directory), None)


def make_private_fd(fd: int) -> None:
    """Restrict an open file to its owner without re-resolving its path.

    A no-op on POSIX, where the creating ``open`` already applied the mode.
    Windows reopens the descriptor's handle with WRITE_DAC (the original
    handle lacks it) and sets a protected current-user + SYSTEM DACL on it.
    Failures raise ``OSError``.
    """
    if not IS_WINDOWS:
        return
    import msvcrt

    import win32file
    import win32security

    with translated():
        handle = win32file.ReOpenFile(
            msvcrt.get_osfhandle(fd),
            _WRITE_DAC | _READ_CONTROL,
            win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE
            | win32file.FILE_SHARE_DELETE,
            0)
        try:
            win32security.SetSecurityInfo(
                handle, win32security.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION
                | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                None, None, _private_dacl(directory=False), None)
        finally:
            handle.Close()


def make_private_dir_fd(fd: int) -> None:
    """Restrict an open directory to its owner through its descriptor.

    POSIX is ``os.fchmod(fd, 0o700)``. On Windows the descriptor's handle
    must already carry WRITE_DAC, because ReOpenFile refuses directories
    (:meth:`cc_remote.platform.dirs.DirHandle.make_private` opens one); it
    gets a protected current-user + SYSTEM DACL that new children inherit.
    Failures raise ``OSError``.
    """
    if not IS_WINDOWS:
        os.fchmod(fd, 0o700)
        return
    import msvcrt

    import win32security

    with translated():
        win32security.SetSecurityInfo(
            msvcrt.get_osfhandle(fd), win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, _private_dacl(directory=True), None)


def copy_security_fd(src_fd: int, dst_fd: int) -> None:
    """Give ``dst_fd``'s file the access control of ``src_fd``'s file.

    POSIX copies the mode bits. Windows copies the DACL, whether it is
    protected from inheritance (an unprotected one keeps its explicit
    entries and re-inherits from the destination's directory), and the
    read-only attribute; the destination handle is reopened with WRITE_DAC
    like :func:`make_private_fd`. Neither path is re-resolved. Failures
    raise ``OSError`` and may leave the destination partly updated.
    """
    if not IS_WINDOWS:
        os.fchmod(dst_fd, stat.S_IMODE(os.fstat(src_fd).st_mode))
        return
    import msvcrt

    import win32file
    import win32security

    with translated():
        descriptor = _handle_security_info(
            src_fd, win32security.DACL_SECURITY_INFORMATION)
        control, _revision = descriptor.GetSecurityDescriptorControl()
        protection = (
            win32security.PROTECTED_DACL_SECURITY_INFORMATION
            if control & win32security.SE_DACL_PROTECTED
            else win32security.UNPROTECTED_DACL_SECURITY_INFORMATION)
        read_only = bool(
            files._win32_handle_attributes(msvcrt.get_osfhandle(src_fd))
            & files._FILE_ATTRIBUTE_READONLY)
        handle = win32file.ReOpenFile(
            msvcrt.get_osfhandle(dst_fd),
            _WRITE_DAC | _READ_CONTROL | _FILE_READ_ATTRIBUTES | _FILE_WRITE_ATTRIBUTES,
            win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE
            | win32file.FILE_SHARE_DELETE,
            0)
        try:
            win32security.SetSecurityInfo(
                handle, win32security.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION | protection,
                None, None, descriptor.GetSecurityDescriptorDacl(), None)
            files._win32_set_read_only(int(handle), read_only)
        finally:
            handle.Close()


_FILE_READ_ATTRIBUTES = 0x0080
_FILE_WRITE_ATTRIBUTES = 0x0100
_READ_CONTROL = 0x00020000
_WRITE_DAC = 0x00040000


def _private_dacl(*, directory: bool):
    """A DACL granting full control to only the current user and SYSTEM."""
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
    return dacl


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


def _descriptor_is_private(descriptor, *, directory: bool, deny_mask: int | None = None) -> bool:
    """Owner is the user (or administrators); only trusted SIDs are granted access.

    A directory's inheritable ACEs decide who can read the files later created
    in it, so they count as grants; CREATOR OWNER is trusted there because it
    only ever resolves to each new child's owner. With ``deny_mask`` an
    untrusted grant fails only when it includes one of those rights.
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
        (ace_type, ace_flags), mask, sid = ace
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
        if deny_mask is not None and not mask & deny_mask:
            continue
        return False
    return True


# Rights that let a principal change a file or a directory's entries.
_WRITE_RIGHTS = (
    0x0002 | 0x0004 | 0x0010 | 0x0040 | 0x0100  # data/add, append/subdir, EA, delete child, attributes
    | 0x00010000 | 0x00040000 | 0x00080000      # DELETE, WRITE_DAC, WRITE_OWNER
    | 0x10000000 | 0x40000000                    # GENERIC_ALL, GENERIC_WRITE
)


def _win32_descriptor(target: int | StrPath, info: int):
    if isinstance(target, int):
        return _handle_security_info(target, info)
    return _named_security_info(target, info)


def owned_info(
    target: int | StrPath, info: os.stat_result, *, effective: bool = False,
) -> bool:
    """``info.st_uid == current_uid()`` (or euid) on POSIX; the owner SID on Windows.

    ``target`` is the descriptor or path ``info`` came from. Windows stat
    results report st_uid 0 for every file, so the owner must come from the
    security descriptor and must be the current user or the BUILTIN
    Administrators group; SYSTEM-owned directories such as the drive root
    and the Users directory are not owned. A missing or unverifiable target
    is not owned.
    ``effective`` keeps callers that compared against the effective uid exact
    on POSIX.
    """
    if not IS_WINDOWS:
        return info.st_uid == (current_euid() if effective else current_uid())
    try:
        return owned_by_current_user(target)
    except Exception:
        return False  # missing or unverifiable is not owned


def private_mode_info(target: int | StrPath, info: os.stat_result) -> bool:
    """No group/other mode bits on POSIX; an owner-only DACL on Windows.

    On Windows the owner must also be the current user or the BUILTIN
    Administrators group, so SYSTEM-owned directories such as the drive root
    and the Users directory report False. Any inspection failure reports False.
    """
    if not IS_WINDOWS:
        return not stat.S_IMODE(info.st_mode) & 0o077
    try:
        import win32security

        descriptor = _win32_descriptor(
            target, win32security.OWNER_SECURITY_INFORMATION
            | win32security.DACL_SECURITY_INFORMATION)
        if descriptor is None:
            return False
        return _descriptor_is_private(descriptor, directory=stat.S_ISDIR(info.st_mode))
    except Exception:
        return False  # unverifiable is not private


def no_foreign_write_info(target: int | StrPath, info: os.stat_result) -> bool:
    """No group/other write bits on POSIX; no foreign write ACE on Windows.

    On Windows the owner must also be the current user or the BUILTIN
    Administrators group, so SYSTEM-owned directories such as the drive root
    and the Users directory report False. Any inspection failure reports False.
    """
    if not IS_WINDOWS:
        return not stat.S_IMODE(info.st_mode) & 0o022
    try:
        import win32security

        descriptor = _win32_descriptor(
            target, win32security.OWNER_SECURITY_INFORMATION
            | win32security.DACL_SECURITY_INFORMATION)
        if descriptor is None:
            return False
        return _descriptor_is_private(
            descriptor, directory=stat.S_ISDIR(info.st_mode), deny_mask=_WRITE_RIGHTS)
    except Exception:
        return False
