from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from cc_remote.wrapper import process_scan


@pytest.mark.parametrize(("args", "expected"), [
    ((b"C:/npm/codex.exe", b"resume", b"sid"), 1),
    ((b"C:/npm/codex.exe", b"app-server", b"--stdio"), 0),
    ((b"node.exe", b"codex.js", b"app-server", b"proxy"), 0),
    ((b"codex.exe", b"exec", b"hi"), 0),
    ((b"codex.exe", b"mcp-server"), 0),
    ((), 1),
])
def test_win32_tty_classifies_headless_codex_commands(args, expected) -> None:
    assert process_scan.win32_tty(args) == expected


@pytest.mark.windows_only(reason="Toolhelp32 snapshots only exist on Windows")
def test_snapshot_contains_a_live_child_with_argv_and_parent(tmp_path) -> None:
    child = subprocess.Popen(["ping", "-n", "30", "127.0.0.1"], cwd=tmp_path,
                             stdout=subprocess.DEVNULL)
    try:
        time.sleep(0.3)
        rows, complete = process_scan.win32_process_snapshot()
        assert complete is True
        by_pid = {row[0].pid: row for row in rows}
        identity, ppid, tty, args = by_pid[child.pid]
        assert ppid == os.getpid()
        assert os.path.basename(os.fsdecode(args[0])).lower().startswith("ping")
        assert tty == 1
        assert process_scan.win32_process_info(child.pid) == by_pid[child.pid]
        cwds, cwd_complete = process_scan.win32_process_cwds([child.pid])
        assert cwd_complete is True
        assert os.path.normcase(cwds[child.pid]) == os.path.normcase(os.path.realpath(tmp_path))
    finally:
        child.kill()
        child.wait(timeout=10)


@pytest.mark.windows_only(reason="PEB environment reads only exist on Windows")
def test_environment_value_reads_one_key_from_exact_identity(tmp_path) -> None:
    child = subprocess.Popen(
        ["ping", "-n", "30", "127.0.0.1"],
        env={**os.environ, "CODEX_HOME": str(tmp_path)}, stdout=subprocess.DEVNULL)
    try:
        time.sleep(0.3)
        identity = process_scan.process_identity(child.pid)
        assert identity is not None
        complete, args, value = process_scan.process_command_environment_value(
            identity, "CODEX_HOME")
        assert complete is True and args and value == str(tmp_path)
        complete, _args, missing = process_scan.process_command_environment_value(
            identity, "CC_REMOTE_DEFINITELY_ABSENT")
        assert complete is True and missing is None
        stale = process_scan.ProcessIdentity(identity.pid, identity.start_ticks + 1)
        assert process_scan.process_command_environment_value(stale, "CODEX_HOME") == (
            False, None, None)
    finally:
        child.kill()
        child.wait(timeout=10)


@pytest.mark.windows_only(reason="Toolhelp32 snapshots only exist on Windows")
def test_cwds_of_exited_process_stay_complete() -> None:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=10)
    cwds, complete = process_scan.win32_process_cwds([child.pid])
    assert cwds == {} and complete is True


@pytest.fixture
def fake_processes(monkeypatch):
    """Stub win32_process: pid -> (creation ticks, argv or None), plus parents."""
    from cc_remote.platform import win32_process

    table: dict[int, tuple[int, tuple[bytes, ...] | None]] = {}
    parents: dict[int, int] = {}
    monkeypatch.setattr(win32_process, "parent_map", lambda: dict(parents))
    monkeypatch.setattr(
        win32_process, "creation_time",
        lambda pid: table[pid][0] if pid in table else None)
    monkeypatch.setattr(
        win32_process, "argv", lambda pid: table[pid][1] if pid in table else None)
    monkeypatch.setattr(win32_process, "access_denied", lambda pid: False)
    return table, parents


@pytest.mark.windows_only(reason="stubs the Win32-only win32_process module")
def test_snapshot_keeps_unreadable_rows_and_drops_stale_parents(fake_processes) -> None:
    table, parents = fake_processes
    table.update({
        100: (10, (b"explorer.exe",)),
        200: (20, (b"codex.exe", b"resume")),
        300: (30, None),  # argv unreadable: still an ancestry link
        400: (40, (b"claude.exe",)),
        500: (50, (b"x" * process_scan.MAX_CMDLINE_BYTES,)),
        600: (5, (b"orphan.exe",)),  # older than pid 100: its parent pid was reused
    })
    parents.update({0: 0, 4: 0, 100: 1, 200: 100, 300: 200, 400: 300,
                    500: 100, 600: 100, 700: 100})
    rows, complete = process_scan.win32_process_snapshot()
    assert complete is True
    by_pid = {row[0].pid: row for row in rows}
    assert set(by_pid) == {100, 200, 300, 400, 500, 600}
    assert by_pid[200] == (process_scan.ProcessIdentity(200, 20), 100, 1,
                           (b"codex.exe", b"resume"))
    assert by_pid[300] == (process_scan.ProcessIdentity(300, 30), 200, 1, ())
    assert by_pid[400][1] == 300
    assert by_pid[500][3] == ()
    assert by_pid[600][1] == 0
    for pid, row in by_pid.items():
        assert process_scan.win32_process_info(pid) == row
    assert process_scan.win32_process_info(700) is None


@pytest.mark.windows_only(reason="stubs the Win32-only win32_process module")
def test_snapshot_fails_closed_without_a_bounded_process_list(
        fake_processes, monkeypatch) -> None:
    from cc_remote.platform import win32_process

    _table, parents = fake_processes
    parents.update({pid: 1 for pid in range(8, 8 + process_scan.MAX_PROC_SCAN + 1)})
    assert process_scan.win32_process_snapshot() == ([], False)
    monkeypatch.setattr(win32_process, "parent_map", lambda: None)
    assert process_scan.win32_process_snapshot() == ([], False)


@pytest.mark.windows_only(reason="stubs the Win32-only win32_process module")
def test_cwds_are_incomplete_when_a_live_process_hides_them(
        fake_processes, monkeypatch) -> None:
    from cc_remote.platform import win32_process

    table, _parents = fake_processes
    table.update({100: (10, ()), 200: (20, ())})
    monkeypatch.setattr(
        win32_process, "process_parameters",
        lambda pid: ("C:\\work", ()) if pid == 100 else None)
    work = os.path.realpath("C:\\work")
    assert process_scan.win32_process_cwds([100]) == ({100: work}, True)
    assert process_scan.win32_process_cwds([100, 200]) == ({100: work}, False)
    monkeypatch.setattr(win32_process, "access_denied", lambda pid: pid == 300)
    assert process_scan.win32_process_cwds([300]) == ({}, False)
    assert process_scan.win32_process_cwds([301]) == ({}, True)
