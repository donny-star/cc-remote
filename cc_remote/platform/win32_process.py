"""Win32 process primitives used by cross-platform process scans (Windows only)."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_PROCESS_COMMAND_LINE_INFORMATION = 60
_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)

_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_ntdll.NtQueryInformationProcess.argtypes = [
    wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]
_ntdll.NtQueryInformationProcess.restype = wintypes.LONG
_shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
_shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)


class _UnicodeString(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", ctypes.c_void_p),
    ]


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]


def _open(pid: int) -> int | None:
    if pid <= 0:
        return None
    return _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid) or None


def creation_time(pid: int) -> int | None:
    """Kernel creation time in 100ns FILETIME ticks, or None once exited."""
    handle = _open(pid)
    if handle is None:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        code = wintypes.DWORD()
        if (not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                or code.value != _STILL_ACTIVE):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        _kernel32.CloseHandle(handle)


def command_line(pid: int) -> str | None:
    handle = _open(pid)
    if handle is None:
        return None
    try:
        needed = wintypes.ULONG(0)
        _ntdll.NtQueryInformationProcess(
            handle, _PROCESS_COMMAND_LINE_INFORMATION, None, 0, ctypes.byref(needed))
        if not needed.value:
            return None
        buffer = ctypes.create_string_buffer(needed.value)
        status = _ntdll.NtQueryInformationProcess(
            handle, _PROCESS_COMMAND_LINE_INFORMATION, buffer, needed,
            ctypes.byref(needed))
        if status != 0:
            return None
        text = _UnicodeString.from_buffer(buffer)
        if not text.Buffer:
            return None
        return ctypes.wstring_at(text.Buffer, text.Length // 2)
    finally:
        _kernel32.CloseHandle(handle)


def argv(pid: int) -> tuple[bytes, ...] | None:
    line = command_line(pid)
    if line is None:
        return None
    if not line.strip():
        return ()  # CommandLineToArgvW("") would return *our* executable
    count = ctypes.c_int(0)
    array = _shell32.CommandLineToArgvW(line, ctypes.byref(count))
    if not array:
        return None
    try:
        return tuple(os.fsencode(array[i]) for i in range(count.value))
    finally:
        _kernel32.LocalFree(ctypes.cast(array, ctypes.c_void_p))


def parent_map() -> dict[int, int] | None:
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, _INVALID_HANDLE_VALUE):
        return None
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
        if not _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        parents: dict[int, int] = {}
        while True:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                return parents
    finally:
        _kernel32.CloseHandle(snapshot)


def owner_sid(pid: int) -> str | None:
    import pywintypes
    import win32api
    import win32con
    import win32security

    try:
        handle = win32api.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except pywintypes.error:
        return None
    try:
        token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
        try:
            sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        return win32security.ConvertSidToStringSid(sid)
    except pywintypes.error:
        return None
    finally:
        handle.Close()


def all_pids() -> tuple[int, ...]:
    import win32process

    return tuple(int(pid) for pid in win32process.EnumProcesses())
