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


def _denied(*args, **kwargs):
    import pywintypes

    raise pywintypes.error(5, "X", "denied")


@pytest.mark.windows_only(reason="Job Object adoption only exists on Windows")
@pytest.mark.parametrize("target", [
    "win32api.OpenProcess",
    "win32job.CreateJobObject",
    "win32job.AssignProcessToJobObject",
])
def test_adopt_failure_raises_permission_error_and_reaps_child(
    monkeypatch, target,
) -> None:
    module_name, name = target.split(".")
    module = getattr(procs, module_name)
    real_open = procs.win32api.OpenProcess
    opened: list[int] = []

    def recording_open(access, inherit, pid):
        opened.append(pid)
        return real_open(access, inherit, pid)

    monkeypatch.setattr(procs.win32api, "OpenProcess", recording_open)
    if target == "win32api.OpenProcess":
        def failing_open(access, inherit, pid):
            opened.append(pid)
            _denied()

        monkeypatch.setattr(procs.win32api, "OpenProcess", failing_open)
    else:
        monkeypatch.setattr(module, name, _denied)
    before = dict(procs._jobs)
    with pytest.raises(PermissionError):
        procs.popen_group([sys.executable, "-c", "import time; time.sleep(60)"])
    assert len(opened) == 1
    assert _wait_gone(opened[0])
    assert opened[0] not in procs._jobs
    assert procs._jobs == before


@pytest.mark.windows_only(reason="Job Object termination only exists on Windows")
@pytest.mark.parametrize("name", ["QueryInformationJobObject", "TerminateJobObject"])
def test_killpg_job_failure_raises_permission_error(monkeypatch, name) -> None:
    child = procs.popen_group([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with monkeypatch.context() as patch:
            patch.setattr(procs.win32job, name, _denied)
            with pytest.raises(PermissionError):
                procs.killpg(child.pid, procs.SIGTERM)
        assert child.pid in procs._jobs  # still tracked: a retry can kill it
    finally:
        procs.killpg(child.pid, procs.SIGKILL)
        child.wait(timeout=10)


@pytest.mark.windows_only(reason="taskkill is the Windows fallback for unknown pids")
def test_killpg_escalation_never_falls_back_to_taskkill(tmp_path, monkeypatch) -> None:
    pid_file = tmp_path / "grandchild"
    child = procs.popen_group([sys.executable, "-c", SPAWN_GRANDCHILD, str(pid_file)])
    grandchild = _wait_for_pid(pid_file)

    def no_taskkill(*args, **kwargs):
        raise AssertionError(f"taskkill must not run for a Job-owned pid: {args}")

    monkeypatch.setattr(procs.subprocess, "run", no_taskkill)
    procs.killpg(child.pid, procs.SIGTERM)
    try:
        procs.killpg(child.pid, procs.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=10)
    assert _wait_gone(grandchild)
    if child.pid in procs._jobs:  # SIGKILL landed while the tree was dying
        with pytest.raises(ProcessLookupError):
            procs.killpg(child.pid, procs.SIGKILL)
    assert child.pid not in procs._jobs
