"""Windows process identity, argv, cwd and environment readers.

Windows has no procfs. These helpers mirror the Linux/macOS readers in
``process_scan`` with documented kernel32/ntdll calls. Every read happens
through one open process handle: the handle pins the kernel process object, so
a PID cannot be reused while it is open, and the creation time read through
that same handle is the stable identity token (the Linux ``start_ticks``
analog). Protected or other-account processes simply fail to open; ownership
scans treat that like a vanished process because they are never this
account's Claude/Codex clients.
"""
from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

MAX_SNAPSHOT_PROCESSES = 8192

_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_VM_READ = 0x0010
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_STILL_ACTIVE = 259
_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_BASIC_INFORMATION_CLASS = 0
_PROCESS_COMMAND_LINE_INFORMATION_CLASS = 60
_STATUS_INFO_LENGTH_MISMATCH = -1073741820  # 0xC0000004 as NTSTATUS
_STATUS_BUFFER_OVERFLOW = -2147483643  # 0x80000005
_STATUS_BUFFER_TOO_SMALL = -1073741789  # 0xC0000023

# x64 ``PEB`` / ``RTL_USER_PROCESS_PARAMETERS`` offsets (stable since Vista).
_PEB_PROCESS_PARAMETERS = 0x20
_PARAMS_CURRENT_DIRECTORY = 0x38
_PARAMS_ENVIRONMENT = 0x80
_PARAMS_ENVIRONMENT_SIZE = 0x3F0

# Process owned by a different account than this wrapper. All callers compare
# owner ids with ``current_uid()``, which is 0 on Windows.
FOREIGN_OWNER_UID = -1

if sys.platform == "win32":
    from ctypes import wintypes

    class _FileTime(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    class _ProcessBasicInformation(ctypes.Structure):
        _fields_ = [
            ("ExitStatus", ctypes.c_long),
            ("PebBaseAddress", ctypes.c_void_p),
            ("AffinityMask", ctypes.c_size_t),
            ("BasePriority", ctypes.c_long),
            ("UniqueProcessId", ctypes.c_size_t),
            ("InheritedFromUniqueProcessId", ctypes.c_size_t),
        ]

    class _UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ushort),
            ("MaximumLength", ctypes.c_ushort),
            ("Buffer", ctypes.c_void_p),
        ]

    class _ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]


_api: dict[str, Any] | None = None


def _load() -> dict[str, Any] | None:
    global _api
    if sys.platform != "win32":
        return None
    if _api is not None:
        return _api
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")
        handle = wintypes.HANDLE

        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = handle
        kernel32.CloseHandle.argtypes = [handle]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.GetProcessTimes.argtypes = [handle] + [
            ctypes.POINTER(_FileTime)] * 4
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess.argtypes = [
            handle, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.IsWow64Process.argtypes = [
            handle, ctypes.POINTER(wintypes.BOOL)]
        kernel32.IsWow64Process.restype = wintypes.BOOL
        kernel32.ReadProcessMemory.argtypes = [
            handle, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t)]
        kernel32.ReadProcessMemory.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = handle
        kernel32.CreateToolhelp32Snapshot.argtypes = [
            wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = handle
        kernel32.Process32FirstW.argtypes = [
            handle, ctypes.POINTER(_ProcessEntry32W)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [
            handle, ctypes.POINTER(_ProcessEntry32W)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p

        ntdll.NtQueryInformationProcess.argtypes = [
            handle, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong)]
        ntdll.NtQueryInformationProcess.restype = ctypes.c_long

        advapi32.OpenProcessToken.argtypes = [
            handle, wintypes.DWORD, ctypes.POINTER(handle)]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [
            handle, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD)]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        advapi32.EqualSid.restype = wintypes.BOOL

        shell32.CommandLineToArgvW.argtypes = [
            wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    except (AttributeError, OSError):
        return None
    _api = {
        "kernel32": kernel32,
        "advapi32": advapi32,
        "shell32": shell32,
        "ntdll": ntdll,
    }
    return _api


@contextmanager
def _open_process(pid: int, access: int) -> Iterator[Any]:
    api = _load()
    handle = None
    if api is not None and pid > 0:
        handle = api["kernel32"].OpenProcess(access, False, pid)
    try:
        yield handle or None
    finally:
        if handle:
            api["kernel32"].CloseHandle(handle)


def _creation_time(api: dict[str, Any], handle: Any) -> int | None:
    """Return the live process creation FILETIME, or None once it exited."""
    kernel32 = api["kernel32"]
    code = wintypes.DWORD()
    if (not kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            or code.value != _STILL_ACTIVE):
        return None
    created, exited, kernel, user = (_FileTime() for _ in range(4))
    if not kernel32.GetProcessTimes(
            handle, ctypes.byref(created), ctypes.byref(exited),
            ctypes.byref(kernel), ctypes.byref(user)):
        return None
    value = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
    return value if value > 0 else None


def _basic_information(
    api: dict[str, Any], handle: Any,
) -> _ProcessBasicInformation | None:
    info = _ProcessBasicInformation()
    status = api["ntdll"].NtQueryInformationProcess(
        handle, _PROCESS_BASIC_INFORMATION_CLASS, ctypes.byref(info),
        ctypes.sizeof(info), None)
    return info if status == 0 else None


def _command_line(
    api: dict[str, Any], handle: Any, max_bytes: int,
) -> str | None:
    query = api["ntdll"].NtQueryInformationProcess
    size = ctypes.c_ulong(4096)
    limit = ctypes.sizeof(_UnicodeString) + max_bytes * 2
    for _ in range(3):
        buffer = ctypes.create_string_buffer(size.value)
        needed = ctypes.c_ulong(0)
        status = query(
            handle, _PROCESS_COMMAND_LINE_INFORMATION_CLASS, buffer,
            size.value, ctypes.byref(needed))
        if status == 0:
            text = ctypes.cast(buffer, ctypes.POINTER(_UnicodeString)).contents
            if not text.Buffer or not text.Length:
                return ""
            return ctypes.wstring_at(text.Buffer, text.Length // 2)
        if status not in {
            _STATUS_INFO_LENGTH_MISMATCH,
            _STATUS_BUFFER_OVERFLOW,
            _STATUS_BUFFER_TOO_SMALL,
        } or needed.value <= size.value or needed.value > limit:
            return None
        size = ctypes.c_ulong(needed.value)
    return None


def _split_command_line(api: dict[str, Any], command_line: str) -> tuple[str, ...] | None:
    # CommandLineToArgvW substitutes *this* process's image for an empty
    # string, which would misattribute our own argv to the target.
    if not command_line.strip():
        return ()
    count = ctypes.c_int(0)
    argv = api["shell32"].CommandLineToArgvW(command_line, ctypes.byref(count))
    if not argv:
        return None
    try:
        return tuple(argv[index] for index in range(count.value))
    finally:
        api["kernel32"].LocalFree(argv)


def _read_memory(
    api: dict[str, Any], handle: Any, address: int, size: int,
) -> bytes | None:
    if not address or size < 0:
        return None
    if size == 0:
        return b""
    buffer = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    if (not api["kernel32"].ReadProcessMemory(
            handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(read))
            or read.value != size):
        return None
    return buffer.raw


def _read_pointer(api: dict[str, Any], handle: Any, address: int) -> int | None:
    raw = _read_memory(api, handle, address, ctypes.sizeof(ctypes.c_void_p))
    return int.from_bytes(raw, sys.byteorder) if raw is not None else None


def _token_user_sid(api: dict[str, Any], process: Any) -> bytes | None:
    """Return a copy of the process token's user SID bytes."""
    advapi32 = api["advapi32"]
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        needed = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(needed))
        if needed.value <= 0 or needed.value > 4096:
            return None
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
                token, _TOKEN_USER, buffer, needed.value, ctypes.byref(needed)):
            return None
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
        if not sid:
            return None
        # SID: revision, sub-authority count, 6-byte authority, 4 bytes each.
        header = ctypes.string_at(sid, 2)
        return ctypes.string_at(sid, 8 + 4 * header[1])
    finally:
        api["kernel32"].CloseHandle(token)


_own_sid: bytes | None = None


def identity(pid: int) -> tuple[int, int] | None:
    """Return ``(creation FILETIME, parent pid)`` for one live process."""
    api = _load()
    if api is None:
        return None
    with _open_process(pid, _PROCESS_QUERY_LIMITED_INFORMATION) as handle:
        if handle is None:
            return None
        started = _creation_time(api, handle)
        info = _basic_information(api, handle)
        if started is None or info is None:
            return None
        return started, int(info.InheritedFromUniqueProcessId)


def command(
    pid: int, start_ticks: int, max_bytes: int,
) -> tuple[bytes, ...] | None:
    """Return argv while the exact ``(pid, creation time)`` identity lives."""
    api = _load()
    if api is None:
        return None
    with _open_process(pid, _PROCESS_QUERY_LIMITED_INFORMATION) as handle:
        if handle is None or _creation_time(api, handle) != start_ticks:
            return None
        line = _command_line(api, handle, max_bytes)
        if line is None:
            return None
        if len(line) > max_bytes:
            return ()
        args = _split_command_line(api, line)
        if args is None:
            return None
        return tuple(os.fsencode(arg) for arg in args if arg)


def command_environment(
    pid: int, start_ticks: int, max_command_bytes: int, max_environment_bytes: int,
) -> tuple[tuple[bytes, ...], str | None, tuple[bytes, ...]] | None:
    """Return argv, cwd and environment from one exact process snapshot.

    Environment names are case-insensitive on Windows; they are upper-cased so
    callers can match canonical names such as ``CODEX_HOME`` exactly.
    """
    api = _load()
    if api is None or ctypes.sizeof(ctypes.c_void_p) != 8:
        return None
    access = _PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ
    with _open_process(pid, access) as handle:
        if handle is None or _creation_time(api, handle) != start_ticks:
            return None
        wow64 = wintypes.BOOL(False)
        if (not api["kernel32"].IsWow64Process(handle, ctypes.byref(wow64))
                or wow64.value):
            # A 32-bit process keeps its live cwd in the 32-bit PEB; the
            # x64 offsets below would read stale startup state.
            return None
        line = _command_line(api, handle, max_command_bytes)
        if line is None:
            return None
        args = () if len(line) > max_command_bytes else _split_command_line(api, line)
        info = _basic_information(api, handle)
        if args is None or info is None or not info.PebBaseAddress:
            return None
        params = _read_pointer(api, handle, info.PebBaseAddress + _PEB_PROCESS_PARAMETERS)
        if not params:
            return None
        cwd_raw = _read_memory(
            api, handle, params + _PARAMS_CURRENT_DIRECTORY,
            ctypes.sizeof(_UnicodeString))
        environment_address = _read_pointer(api, handle, params + _PARAMS_ENVIRONMENT)
        environment_size = _read_pointer(
            api, handle, params + _PARAMS_ENVIRONMENT_SIZE)
        if cwd_raw is None or not environment_address or not environment_size:
            return None
        if environment_size > max_environment_bytes:
            return None
        cwd_string = _UnicodeString.from_buffer_copy(cwd_raw)
        cwd: str | None = None
        if cwd_string.Buffer and cwd_string.Length:
            cwd_bytes = _read_memory(api, handle, cwd_string.Buffer, cwd_string.Length)
            if cwd_bytes is None:
                return None
            cwd = cwd_bytes.decode("utf-16-le", "surrogatepass")
            if len(cwd) > 3 and cwd.endswith("\\"):
                cwd = cwd[:-1]
        block = _read_memory(api, handle, environment_address, environment_size)
        if block is None or _creation_time(api, handle) != start_ticks:
            return None
    text = block.decode("utf-16-le", "surrogatepass").split("\0\0", 1)[0]
    environment: list[bytes] = []
    for entry in text.split("\0"):
        name, separator, value = entry.partition("=")
        if not name or not separator:
            continue  # empty slots and drive-cwd entries such as ``=C:=C:\``
        environment.append(os.fsencode(f"{name.upper()}={value}"))
    return (
        tuple(os.fsencode(arg) for arg in args if arg),
        cwd,
        tuple(environment),
    )


def owner_matches_current(pid: int) -> bool | None:
    """Whether ``pid`` runs as this account; None when the token is unreadable."""
    global _own_sid
    api = _load()
    if api is None:
        return None
    if _own_sid is None:
        _own_sid = _token_user_sid(api, api["kernel32"].GetCurrentProcess())
        if _own_sid is None:
            return None
    with _open_process(pid, _PROCESS_QUERY_LIMITED_INFORMATION) as handle:
        if handle is None:
            return None
        sid = _token_user_sid(api, handle)
    if sid is None:
        return None
    return sid == _own_sid


def process_entries() -> tuple[list[tuple[int, int, str]], bool]:
    """Return ``(pid, parent pid, image name)`` for every live process."""
    api = _load()
    if api is None:
        return [], False
    kernel32 = api["kernel32"]
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return [], False
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return [], False
        result: list[tuple[int, int, str]] = []
        while True:
            if len(result) >= MAX_SNAPSHOT_PROCESSES:
                return result, False
            result.append((
                int(entry.th32ProcessID),
                int(entry.th32ParentProcessID),
                entry.szExeFile,
            ))
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                return result, True
    finally:
        kernel32.CloseHandle(snapshot)
