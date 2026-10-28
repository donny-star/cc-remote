"""Win32 process primitives used by cross-platform process scans (Windows only)."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_WAIT_TIMEOUT = 0x102
_PROCESS_COMMAND_LINE_INFORMATION = 60
_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
_ERROR_ACCESS_DENIED = 5
_PROCESS_VM_READ = 0x0010
_PROCESS_BASIC_INFORMATION = 0
_MAX_CWD_BYTES = 32 * 1024
_MAX_ENVIRONMENT_BYTES = 256 * 1024

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)

_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p
_kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
_kernel32.ReadProcessMemory.restype = wintypes.BOOL
_kernel32.IsWow64Process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
_kernel32.IsWow64Process.restype = wintypes.BOOL
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


class _ProcessBasicInformation(ctypes.Structure):
    _fields_ = [
        ("ExitStatus", ctypes.c_long),
        ("PebBaseAddress", ctypes.c_void_p),
        ("AffinityMask", ctypes.c_size_t),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_size_t),
        ("InheritedFromUniqueProcessId", ctypes.c_size_t),
    ]


def _open(pid: int, access: int = _PROCESS_QUERY_LIMITED_INFORMATION) -> int | None:
    if pid <= 0:
        return None
    return _kernel32.OpenProcess(access, False, pid) or None


def creation_time(pid: int) -> int | None:
    """Kernel creation time in 100ns FILETIME ticks, or None once exited."""
    # Liveness comes from the process object's signal state, not the exit
    # code: a process that exited with 259 would otherwise look STILL_ACTIVE.
    handle = _open(pid, _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE)
    if handle is None:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        if _kernel32.WaitForSingleObject(handle, 0) != _WAIT_TIMEOUT:
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        _kernel32.CloseHandle(handle)


def access_denied(pid: int) -> bool:
    """Whether a live process refuses the access :func:`creation_time` needs.

    ``creation_time`` is None both once a process exited and when this token
    may not open it; scans that must fail closed on a hidden live process
    tell the two apart here. A missing PID fails with ERROR_INVALID_PARAMETER.
    """
    if pid <= 0:
        return False
    handle = _kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, pid)
    if handle:
        _kernel32.CloseHandle(handle)
        return False
    return ctypes.get_last_error() == _ERROR_ACCESS_DENIED


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


def _read(handle: int, address: int, size: int) -> bytes | None:
    """Exactly ``size`` bytes of the target's memory, or None."""
    if address <= 0 or size < 0:
        return None
    buffer = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    if not _kernel32.ReadProcessMemory(
            handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(read)):
        return None
    return buffer.raw if read.value == size else None


def _environment_entries(block: bytes) -> tuple[str, ...]:
    """``K=V`` entries of a UTF-16 environment block, up to its double NUL."""
    text = block.decode("utf-16-le", "surrogatepass")
    return tuple(item for item in text.split("\0\0", 1)[0].split("\0") if item)


def process_parameters(pid: int) -> tuple[str, tuple[str, ...]] | None:
    """Current directory and environment of a 64-bit process, from its PEB.

    Offsets are those of the x64 PEB and RTL_USER_PROCESS_PARAMETERS
    (ProcessParameters +0x20; CurrentDirectory.DosPath +0x38; Environment
    +0x80; EnvironmentSize +0x3F0). WOW64 targets, unreadable memory and
    empty or oversized blocks return None; callers recheck the identity.
    """
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return None
    handle = _open(pid, _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_VM_READ)
    if handle is None:
        return None
    try:
        wow64 = wintypes.BOOL(False)
        if not _kernel32.IsWow64Process(handle, ctypes.byref(wow64)) or wow64.value:
            return None
        basic = _ProcessBasicInformation()
        returned = wintypes.ULONG(0)
        if _ntdll.NtQueryInformationProcess(
                handle, _PROCESS_BASIC_INFORMATION, ctypes.byref(basic),
                ctypes.sizeof(basic), ctypes.byref(returned)) != 0:
            return None
        raw = _read(handle, int(basic.PebBaseAddress or 0) + 0x20, 8)
        if raw is None:
            return None
        parameters = int.from_bytes(raw, "little")
        raw = _read(handle, parameters + 0x38, 16)
        if raw is None:
            return None
        length = int.from_bytes(raw[0:2], "little")
        if not length or length % 2 or length > _MAX_CWD_BYTES:
            return None
        text = _read(handle, int.from_bytes(raw[8:16], "little"), length)
        raw_env = _read(handle, parameters + 0x80, 8)
        raw_size = _read(handle, parameters + 0x3F0, 8)
        if text is None or raw_env is None or raw_size is None:
            return None
        size = int.from_bytes(raw_size, "little")
        if not size or size % 2 or size > _MAX_ENVIRONMENT_BYTES:
            return None
        block = _read(handle, int.from_bytes(raw_env, "little"), size)
        if block is None:
            return None
        cwd = os.path.normpath(text.decode("utf-16-le", "surrogatepass"))
        return cwd, _environment_entries(block)
    finally:
        _kernel32.CloseHandle(handle)
