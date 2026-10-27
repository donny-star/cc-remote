from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cc_remote.platform import procs
from cc_remote.wrapper.process_scan import process_identity

SPAWN_GRANDCHILD = (
    "import subprocess, sys, time, pathlib;"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
    "pathlib.Path(sys.argv[1]).write_text(str(g.pid));"
    "time.sleep(60)"
)


def _wait_for_pid(path: Path) -> int:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError("grandchild pid was never written")


def _wait_gone(pid: int) -> bool:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process_identity(pid) is None:
            return True
        time.sleep(0.05)
    return False


def test_killpg_terminates_the_whole_tree(tmp_path) -> None:
    pid_file = tmp_path / "grandchild"
    child = procs.popen_group([sys.executable, "-c", SPAWN_GRANDCHILD, str(pid_file)])
    grandchild = _wait_for_pid(pid_file)
    procs.killpg(child.pid, procs.SIGKILL)
    child.wait(timeout=10)
    assert _wait_gone(grandchild)


def test_async_group_spawn_runs_and_terminates(tmp_path) -> None:
    async def scenario() -> int:
        pid_file = tmp_path / "grandchild"
        child = await procs.create_subprocess_exec_group(
            sys.executable, "-c", SPAWN_GRANDCHILD, str(pid_file),
            stdout=asyncio.subprocess.DEVNULL)
        grandchild = await asyncio.to_thread(_wait_for_pid, pid_file)
        procs.killpg(child.pid, procs.SIGTERM)
        await asyncio.wait_for(child.wait(), 10)
        return grandchild

    assert _wait_gone(asyncio.run(scenario()))


def test_group_child_exits_normally_and_pipes_work() -> None:
    child = procs.popen_group(
        [sys.executable, "-c", "print('ok')"], stdout=subprocess.PIPE)
    out, _ = child.communicate(timeout=15)
    assert child.returncode == 0 and out.strip() == b"ok"


def test_killpg_of_unknown_pid_raises_process_lookup_error() -> None:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    with pytest.raises(ProcessLookupError):
        procs.killpg(child.pid, procs.SIGKILL)


def test_killpg_of_exited_group_raises_process_lookup_error() -> None:
    child = procs.popen_group([sys.executable, "-c", "pass"])
    child.wait(timeout=15)
    with pytest.raises(ProcessLookupError):
        procs.killpg(child.pid, procs.SIGKILL)


@pytest.mark.windows_only(reason="Job Object pruning only exists on Windows")
def test_spawn_survives_job_pruning_failure(tmp_path, monkeypatch) -> None:
    def broken_prune(keep: int) -> None:
        raise OSError("pruning failed")

    monkeypatch.setattr(procs, "_prune_locked", broken_prune)
    pid_file = tmp_path / "grandchild"
    child = procs.popen_group([sys.executable, "-c", SPAWN_GRANDCHILD, str(pid_file)])
    grandchild = _wait_for_pid(pid_file)
    procs.killpg(child.pid, procs.SIGKILL)
    child.wait(timeout=10)
    assert _wait_gone(grandchild)
