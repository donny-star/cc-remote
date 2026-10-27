from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from cc_remote.platform import identity
from cc_remote.wrapper import process_scan

pytestmark = pytest.mark.windows_only(reason="Win32 process primitives")

MARKER = "cc-remote-probe-é中"


@pytest.fixture
def child():
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", MARKER])
    time.sleep(0.5)
    yield process
    process.kill()
    process.wait()


def test_identity_and_argv_of_live_child(child) -> None:
    from cc_remote.platform import win32_process

    ident = process_scan.process_identity(child.pid)
    assert ident is not None
    assert ident.start_ticks == win32_process.creation_time(child.pid)
    args = process_scan.process_command(ident)
    assert args is not None and os.fsencode(MARKER) == args[-1]
    assert process_scan.process_identity(child.pid, parent_pid=os.getpid()) == ident


def test_stale_identity_is_rejected(child) -> None:
    ident = process_scan.process_identity(child.pid)
    stale = process_scan.ProcessIdentity(ident.pid, ident.start_ticks + 1)
    assert process_scan.process_command(stale) is None


def test_exited_process_has_no_identity(child) -> None:
    pid = child.pid
    child.kill()
    child.wait()
    assert process_scan.process_identity(pid) is None


def test_process_that_exited_with_still_active_code_has_no_identity() -> None:
    from cc_remote.platform import win32_process

    # Popen keeps its handle open, so the exited process object is still
    # openable; only its signal state shows it is gone (exit code 259 ==
    # STILL_ACTIVE).
    process = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(259)"])
    assert process.wait() == 259
    assert win32_process.creation_time(process.pid) is None
    assert process_scan.process_identity(process.pid) is None


def test_pid_reused_during_parent_lookup_has_no_identity(monkeypatch) -> None:
    from cc_remote.platform import win32_process

    readings = iter([100, 200])
    monkeypatch.setattr(win32_process, "creation_time", lambda pid: next(readings))
    monkeypatch.setattr(win32_process, "parent_map", lambda: {1234: 42})
    assert process_scan.process_identity(1234, parent_pid=42) is None


def test_owner_of_own_child_is_current_uid(child) -> None:
    assert process_scan.process_owner_uid(child.pid) == identity.current_uid()


def test_parent_map_and_pid_listing(child) -> None:
    from cc_remote.platform import win32_process

    assert win32_process.parent_map()[child.pid] == os.getpid()
    assert child.pid in win32_process.all_pids()


def test_protected_system_process_is_unreadable_not_an_error() -> None:
    from cc_remote.platform import win32_process

    assert win32_process.argv(4) is None  # PID 4 is the System process


def test_oversized_argv_is_rejected_like_proc_cmdline(monkeypatch) -> None:
    from cc_remote.platform import win32_process

    ident = process_scan.ProcessIdentity(1234, 100)
    monkeypatch.setattr(win32_process, "creation_time", lambda pid: 100)
    fitting = (b"x" * (process_scan.MAX_CMDLINE_BYTES - 1),)
    monkeypatch.setattr(win32_process, "argv", lambda pid: fitting)
    assert process_scan.process_command(ident) == fitting
    oversized = (b"x" * (process_scan.MAX_CMDLINE_BYTES // 2),) * 2
    monkeypatch.setattr(win32_process, "argv", lambda pid: oversized)
    assert process_scan.process_command(ident) is None
