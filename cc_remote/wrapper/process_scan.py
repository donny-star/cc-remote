"""Cross-platform process identity helpers shared by engine ownership scans."""
from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from cc_remote.platform import IS_WINDOWS
from cc_remote.platform.identity import FOREIGN_UID, current_sid, current_uid

MAX_PROC_SCAN = 8192
MAX_CMDLINE_BYTES = 64 * 1024
MAX_ENVIRON_BYTES = 256 * 1024
MAX_DARWIN_ARGC = 4096

_CTL_KERN = 1
_KERN_PROCARGS2 = 49
_PROC_ALL_PIDS = 1
_PROC_PIDTBSDINFO = 3
_MAXCOMLEN = 16


@dataclass(frozen=True, order=True)
class ProcessIdentity:
    pid: int
    start_ticks: int


def _process_stat(proc_dir: Path) -> tuple[int, int, int] | None:
    """Return (parent pid, start ticks, tty number) from /proc stat."""
    try:
        raw = (proc_dir / "stat").read_bytes()
        end = raw.rfind(b") ")
        if end < 0:
            return None
        fields = raw[end + 2:].split()  # starts at field 3 (state)
        return int(fields[1]), int(fields[19]), int(fields[4])
    except (OSError, ValueError, IndexError):
        return None


def _process_start_ticks(proc_dir: Path) -> int | None:
    stat = _process_stat(proc_dir)
    return stat[1] if stat is not None else None


def _process_cmdline(proc_dir: Path) -> tuple[bytes, ...] | None:
    try:
        raw = (proc_dir / "cmdline").read_bytes()
    except OSError:
        return None
    if not raw or len(raw) > MAX_CMDLINE_BYTES:
        return ()
    return tuple(arg for arg in raw.split(b"\0") if arg)


DarwinProcessInfo = tuple[
    ProcessIdentity, int, int, tuple[bytes, ...]
]


class _DarwinBsdInfo(ctypes.Structure):
    """``proc_bsdinfo`` from ``<sys/proc_info.h>``."""

    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * _MAXCOMLEN),
        ("pbi_name", ctypes.c_char * (2 * _MAXCOMLEN)),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


DarwinBsdSnapshot = tuple[
    ProcessIdentity, int, int, int, bytes, bytes
]

_darwin_libc: ctypes.CDLL | None = None
_darwin_libproc: ctypes.CDLL | None = None


def _darwin_libraries() -> tuple[ctypes.CDLL, ctypes.CDLL] | None:
    global _darwin_libc, _darwin_libproc
    if sys.platform != "darwin":
        return None
    if _darwin_libc is not None and _darwin_libproc is not None:
        return _darwin_libc, _darwin_libproc
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        libc.sysctl.argtypes = [
            ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        libc.sysctl.restype = ctypes.c_int
        libproc.proc_pidinfo.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
            ctypes.c_void_p, ctypes.c_int,
        ]
        libproc.proc_pidinfo.restype = ctypes.c_int
        libproc.proc_listpids.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int,
        ]
        libproc.proc_listpids.restype = ctypes.c_int
    except (AttributeError, OSError):
        return None
    _darwin_libc = libc
    _darwin_libproc = libproc
    return libc, libproc


def _darwin_bsd_snapshot(pid: int) -> DarwinBsdSnapshot | None:
    libraries = _darwin_libraries()
    if libraries is None or pid <= 0:
        return None
    _libc, libproc = libraries
    info = _DarwinBsdInfo()
    size = ctypes.sizeof(info)
    read = libproc.proc_pidinfo(
        pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(info), size)
    if read != size or int(info.pbi_pid) != pid:
        return None
    # Nanoseconds are an opaque cross-platform identity token here. Unlike
    # ``ps lstart`` they distinguish two processes reusing a PID in one second.
    started = (
        int(info.pbi_start_tvsec) * 1_000_000_000
        + int(info.pbi_start_tvusec) * 1_000
    )
    if started <= 0:
        return None
    tty_nr = 0 if int(info.e_tdev) in {0, 0xFFFFFFFF} else 1
    return (
        ProcessIdentity(pid, started),
        int(info.pbi_ppid),
        tty_nr,
        int(info.pbi_uid),
        bytes(info.pbi_comm).split(b"\0", 1)[0],
        bytes(info.pbi_name).split(b"\0", 1)[0],
    )


def _parse_darwin_procargs(
    raw: bytes,
) -> tuple[tuple[bytes, ...], tuple[bytes, ...]] | None:
    """Parse one bounded ``KERN_PROCARGS2`` argv/environment payload."""
    header = ctypes.sizeof(ctypes.c_int)
    if len(raw) < header:
        return None
    argc = int.from_bytes(raw[:header], sys.byteorder, signed=True)
    if argc < 0 or argc > MAX_DARWIN_ARGC:
        return None

    def next_string(offset: int) -> tuple[bytes, int] | None:
        end = raw.find(b"\0", offset)
        if end < 0:
            return None
        return raw[offset:end], end + 1

    executable = next_string(header)
    if executable is None or not executable[0]:
        return None
    offset = executable[1]
    while offset < len(raw) and raw[offset] == 0:
        offset += 1

    args: list[bytes] = []
    for _ in range(argc):
        item = next_string(offset)
        if item is None:
            return None
        value, offset = item
        args.append(value)

    environment: list[bytes] = []
    while offset < len(raw):
        while offset < len(raw) and raw[offset] == 0:
            offset += 1
        if offset >= len(raw):
            break
        item = next_string(offset)
        if item is None:
            return None
        value, offset = item
        if value:
            environment.append(value)
    return tuple(args), tuple(environment)


def _darwin_procargs(
    pid: int,
) -> tuple[tuple[bytes, ...], tuple[bytes, ...]] | None:
    libraries = _darwin_libraries()
    if libraries is None:
        return None
    libc, _libproc = libraries
    mib = (ctypes.c_int * 3)(_CTL_KERN, _KERN_PROCARGS2, pid)
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
        return None
    if size.value <= 0 or size.value > MAX_ENVIRON_BYTES:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    actual = ctypes.c_size_t(size.value)
    if libc.sysctl(
        mib, 3, buffer, ctypes.byref(actual), None, 0,
    ) != 0:
        return None
    if actual.value <= 0 or actual.value > size.value:
        return None
    return _parse_darwin_procargs(buffer.raw[:actual.value])


def _darwin_exact_process_payload(
    pid: int,
    expected: ProcessIdentity | None = None,
) -> tuple[DarwinBsdSnapshot, tuple[bytes, ...], tuple[bytes, ...]] | None:
    before = _darwin_bsd_snapshot(pid)
    if before is None or (expected is not None and before[0] != expected):
        return None
    payload = _darwin_procargs(pid)
    if payload is None:
        return None
    after = _darwin_bsd_snapshot(pid)
    if after is None or after[0] != before[0]:
        return None
    args, environment = payload
    return before, args, environment


def _darwin_process_info(pid: int) -> DarwinProcessInfo | None:
    """Return stable process metadata on macOS where procfs is unavailable."""
    payload = _darwin_exact_process_payload(pid)
    if payload is None:
        return None
    metadata, args, _environment = payload
    return metadata[0], metadata[1], metadata[2], args


def _darwin_all_pids() -> tuple[tuple[int, ...], bool]:
    libraries = _darwin_libraries()
    if libraries is None:
        return (), False
    _libc, libproc = libraries
    values = (ctypes.c_int * (MAX_PROC_SCAN + 1))()
    capacity = ctypes.sizeof(values)
    read = libproc.proc_listpids(
        _PROC_ALL_PIDS, 0, ctypes.byref(values), capacity)
    if read <= 0 or read > capacity:
        return (), False
    count = read // ctypes.sizeof(ctypes.c_int)
    pids = tuple(pid for pid in values[:count] if pid > 0)
    if len(pids) > MAX_PROC_SCAN or read == capacity:
        return (), False
    return pids, True


def _darwin_needs_engine_argv(metadata: DarwinBsdSnapshot) -> bool:
    # A wrapper can only attribute clients from its own login account. Avoid
    # turning an unreadable root/other-user Node process into a permanent
    # fail-closed state for every local Codex profile; exact rollout writers
    # remain covered independently by the inode/lsof scan below.
    if metadata[3] != current_uid():
        return False
    names = {metadata[4].lower(), metadata[5].lower()}
    return any(
        b"claude" in name or b"codex" in name
        or name.rsplit(b"/", 1)[-1] in {b"node", b"nodejs"}
        for name in names if name
    )


def darwin_process_snapshot() -> tuple[list[DarwinProcessInfo], bool]:
    """Return a bounded kernel snapshot used for ancestry and CLI matching.

    Metadata is collected for every PID so descendant checks remain complete.
    Only plausible Claude/Codex executables require the more expensive argv
    read. A live candidate whose argv is unreadable makes the scan incomplete.
    """
    pids, complete = _darwin_all_pids()
    if not complete:
        return [], False
    result: list[DarwinProcessInfo] = []
    for pid in pids:
        metadata = _darwin_bsd_snapshot(pid)
        if metadata is None:
            continue
        args: tuple[bytes, ...] = ()
        if _darwin_needs_engine_argv(metadata):
            payload = _darwin_exact_process_payload(pid, metadata[0])
            if payload is None:
                current = _darwin_bsd_snapshot(pid)
                if current is not None and current[0] == metadata[0]:
                    complete = False
                continue
            metadata, args, _environment = payload
        result.append((metadata[0], metadata[1], metadata[2], args))
    return result, complete


_WIN32_HEADLESS_ARGS = frozenset({b"app-server", b"mcp-server", b"exec", b"execpolicy"})


def win32_tty(args: tuple[bytes, ...]) -> int:
    """Stand-in for tty_nr on Windows, which has no controlling terminal to read.

    Codex helpers that never own a terminal (app-server, proxies, exec, MCP)
    count as headless (0); every other process counts as interactive (1).
    The ownership predicates only compare tty_nr with 0, so this reproduces
    their POSIX classification from argv alone.
    """
    return 0 if _WIN32_HEADLESS_ARGS.intersection(args) else 1


def _win32_row(pid: int, ppid: int) -> DarwinProcessInfo | None:
    """One row while the creation time is stable around the argv read.

    Unreadable or oversized argv yields ``()`` like /proc cmdline, so the row
    still links ancestry; only a vanished or unopenable process is dropped.
    """
    from cc_remote.platform import win32_process

    before = win32_process.creation_time(pid)
    if before is None:
        return None
    args = win32_process.argv(pid)
    if win32_process.creation_time(pid) != before:
        return None
    if args is None or sum(len(arg) + 1 for arg in args) > MAX_CMDLINE_BYTES:
        args = ()
    return ProcessIdentity(pid, before), ppid, win32_tty(args), args


def _win32_live_parent(ppid: int, child_ticks: int, parent_ticks: int | None) -> int:
    # Windows never reparents orphans: a parent pid created after its child
    # names a reused pid, not the real (exited) parent.
    return 0 if parent_ticks is not None and parent_ticks > child_ticks else ppid


def win32_process_info(pid: int) -> DarwinProcessInfo | None:
    """One exact Windows process row, or None once it exited or is unopenable."""
    from cc_remote.platform import win32_process

    if pid in (0, 4):
        return None
    parents = win32_process.parent_map()
    if parents is None or pid not in parents:
        return None
    row = _win32_row(pid, parents[pid])
    if row is None:
        return None
    identity, ppid, tty_nr, args = row
    ppid = _win32_live_parent(
        ppid, identity.start_ticks, win32_process.creation_time(ppid))
    return identity, ppid, tty_nr, args


def win32_process_snapshot() -> tuple[list[DarwinProcessInfo], bool]:
    """Windows counterpart of :func:`darwin_process_snapshot` (same row shape).

    Processes that cannot be opened (protected, elevated or another user's)
    are skipped without making the scan incomplete: they are not this login's
    Claude/Codex clients, and failing closed on them would make every session
    read-only forever.
    """
    from cc_remote.platform import win32_process

    parents = win32_process.parent_map()
    if parents is None or len(parents) > MAX_PROC_SCAN:
        return [], False
    rows: list[DarwinProcessInfo] = []
    for pid, ppid in parents.items():
        if pid in (0, 4):  # System Idle Process, System
            continue
        row = _win32_row(pid, ppid)
        if row is not None:
            rows.append(row)
    ticks = {row[0].pid: row[0].start_ticks for row in rows}
    return [
        (identity, _win32_live_parent(ppid, identity.start_ticks, ticks.get(ppid)),
         tty_nr, args)
        for identity, ppid, tty_nr, args in rows
    ], True


def win32_process_cwds(pids: Collection[int]) -> tuple[dict[int, str], bool]:
    """Current directories by pid; incomplete when a live process hides its cwd."""
    from cc_remote.platform import win32_process

    result: dict[int, str] = {}
    complete = True
    for pid in pids:
        before = win32_process.creation_time(pid)
        if before is None:
            if win32_process.access_denied(pid):
                complete = False
            continue
        params = win32_process.process_parameters(pid)
        after = win32_process.creation_time(pid)
        if after != before:
            continue  # exited or replaced: no longer the process asked about
        if params is None:
            complete = False
            continue
        result[pid] = os.path.realpath(params[0])
    return result, complete


def process_identity(pid: int, *, proc_root: str = "/proc",
                     parent_pid: int | None = None) -> ProcessIdentity | None:
    if IS_WINDOWS and proc_root == "/proc":
        from cc_remote.platform import win32_process

        ticks = win32_process.creation_time(pid)
        if ticks is None:
            return None
        if parent_pid is not None:
            parents = win32_process.parent_map()
            if parents is None or parents.get(pid) != parent_pid:
                return None
            # The PID may have been reused between the two reads.
            if win32_process.creation_time(pid) != ticks:
                return None
        return ProcessIdentity(pid, ticks)
    stat = _process_stat(Path(proc_root) / str(pid))
    if stat is not None:
        if parent_pid is not None and stat[0] != parent_pid:
            return None
        return ProcessIdentity(pid, stat[1])
    if sys.platform == "darwin" and proc_root == "/proc":
        info = _darwin_process_info(pid)
        if info is None or (parent_pid is not None and info[1] != parent_pid):
            return None
        return info[0]
    return None


def process_command(
    identity: ProcessIdentity,
    *,
    proc_root: str = "/proc",
) -> tuple[bytes, ...] | None:
    """Read argv only while the exact cross-platform process identity lives."""
    if IS_WINDOWS and proc_root == "/proc":
        from cc_remote.platform import win32_process

        if win32_process.creation_time(identity.pid) != identity.start_ticks:
            return None
        args = win32_process.argv(identity.pid)
        if win32_process.creation_time(identity.pid) != identity.start_ticks:
            return None
        if args is not None and sum(len(arg) + 1 for arg in args) > MAX_CMDLINE_BYTES:
            return None  # same bound as the NUL-separated /proc cmdline
        return args
    proc_dir = Path(proc_root) / str(identity.pid)
    stat = _process_stat(proc_dir)
    if stat is not None:
        if stat[1] != identity.start_ticks:
            return None
        args = _process_cmdline(proc_dir)
        return (
            args
            if _process_start_ticks(proc_dir) == identity.start_ticks
            else None
        )
    if sys.platform == "darwin" and proc_root == "/proc":
        info = _darwin_process_info(identity.pid)
        if info is None or info[0] != identity:
            return None
        return info[3]
    return None


def _environment_value(
    environment: tuple[bytes, ...],
    key: str,
    *,
    fold_case: bool = False,
) -> tuple[bool, str | None]:
    prefix = os.fsencode(key) + b"="
    if fold_case:
        # Windows variable names are case-insensitive (``Path`` is ``PATH``).
        prefix = prefix.upper()
    values = [
        value[len(prefix):]
        for value in environment
        if (value[:len(prefix)].upper() == prefix if fold_case
            else value.startswith(prefix))
    ]
    if len(values) > 1:
        return False, None
    if not values:
        return True, None
    try:
        return True, os.fsdecode(values[0])
    except (TypeError, ValueError):
        return False, None


def process_command_environment_value(
    identity: ProcessIdentity,
    key: str,
    *,
    proc_root: str = "/proc",
) -> tuple[bool, tuple[bytes, ...] | None, str | None]:
    """Read argv and one env value from the same exact process snapshot.

    Account routing needs only one named root such as ``CODEX_HOME`` or
    ``CLAUDE_CONFIG_DIR``. Returning that one value keeps credentials and
    unrelated environment data out of callers and logs. The boolean
    distinguishes a successfully inspected environment where the key is absent
    from a failed/racy read; account attribution must fail closed on the latter.
    """
    if (
        not key
        or len(key) > 128
        or any(not (char.isalnum() or char == "_") for char in key)
    ):
        return False, None, None
    if IS_WINDOWS and proc_root == "/proc":
        from cc_remote.platform import win32_process

        if win32_process.creation_time(identity.pid) != identity.start_ticks:
            return False, None, None
        args = win32_process.argv(identity.pid)
        params = win32_process.process_parameters(identity.pid)
        if (args is None or params is None
                or win32_process.creation_time(identity.pid) != identity.start_ticks):
            return False, None, None
        environment = tuple(os.fsencode(item) for item in params[1])
        if sum(len(item) + 1 for item in environment) > MAX_ENVIRON_BYTES:
            return False, None, None
        complete, value = _environment_value(environment, key, fold_case=True)
        return complete, args, value
    proc_dir = Path(proc_root) / str(identity.pid)
    stat = _process_stat(proc_dir)
    if stat is not None:
        if stat[1] != identity.start_ticks:
            return False, None, None
        args = _process_cmdline(proc_dir)
        if args is None:
            return False, None, None
        try:
            raw = (proc_dir / "environ").read_bytes()
        except OSError:
            return False, None, None
        if len(raw) > MAX_ENVIRON_BYTES:
            return False, None, None
        if _process_start_ticks(proc_dir) != identity.start_ticks:
            return False, None, None
        complete, value = _environment_value(
            tuple(item for item in raw.split(b"\0") if item), key)
        return complete, args, value
    if sys.platform != "darwin" or proc_root != "/proc":
        return False, None, None
    payload = _darwin_exact_process_payload(identity.pid, identity)
    if payload is None:
        return False, None, None
    _metadata, args, environment = payload
    complete, value = _environment_value(environment, key)
    return complete, args, value


def process_environment_value(
    identity: ProcessIdentity,
    key: str,
    *,
    proc_root: str = "/proc",
) -> tuple[bool, str | None]:
    """Read one environment value while the exact process identity is alive."""
    complete, _args, value = process_command_environment_value(
        identity, key, proc_root=proc_root)
    return complete, value


def process_owner_uid(pid: int, *, proc_root: str = "/proc") -> int | None:
    """Return the process owner without weakening the caller's identity check."""
    if IS_WINDOWS and proc_root == "/proc":
        from cc_remote.platform import win32_process

        sid = win32_process.owner_sid(pid)
        if sid is None:
            return None
        return current_uid() if sid == current_sid() else FOREIGN_UID
    try:
        return os.stat(str(Path(proc_root) / str(pid))).st_uid
    except OSError:
        pass
    if sys.platform != "darwin" or proc_root != "/proc":
        return None
    metadata = _darwin_bsd_snapshot(pid)
    return metadata[3] if metadata is not None else None
