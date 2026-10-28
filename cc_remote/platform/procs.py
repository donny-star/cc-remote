"""Process groups that can be terminated as a whole tree on every platform.

POSIX uses sessions (``start_new_session``) and ``killpg``. Windows has no
process groups for signals, so each child starts suspended, joins a fresh Job
Object, and only then resumes; terminating the Job kills every descendant,
including ones spawned in the first microseconds (the venv python.exe
launcher re-spawns the real interpreter immediately). Jobs deliberately do
not use KILL_ON_JOB_CLOSE: like a POSIX session, the tree may outlive us.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import threading
from collections import OrderedDict
from typing import Any

from cc_remote.platform import IS_WINDOWS

SIGTERM = int(signal.SIGTERM)
SIGKILL = int(getattr(signal, "SIGKILL", 9))

if IS_WINDOWS:
    import ctypes

    import win32api
    import win32con
    import win32job

    from cc_remote.log import logger
    from cc_remote.platform._win32err import translated

    _log = logger("cc_remote.platform.procs")

    _CREATE_SUSPENDED = 0x00000004
    _CREATE_NEW_PROCESS_GROUP = 0x00000200
    _PROCESS_ACCESS = (
        win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE
        | 0x0800  # PROCESS_SUSPEND_RESUME
        | 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
    )
    _ntdll = ctypes.WinDLL("ntdll")
    _ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
    _ntdll.NtResumeProcess.restype = ctypes.c_long

    _jobs: dict[int, Any] = {}
    _jobs_lock = threading.Lock()
    # Creation time of each tracked group leader, and the same for groups whose
    # Job already drained. A tombstoned pid answers ProcessLookupError forever
    # (until a new group reuses it): falling back to ``taskkill /T`` could hit
    # an unrelated tree that inherited the recycled pid.
    # The creation times are reserved for a later identity check; killpg
    # currently decides by membership only.
    _created: dict[int, int | None] = {}
    _tombstones: OrderedDict[int, int | None] = OrderedDict()
    # Bounded LRU: an evicted (very old) pid falls back to taskkill, which is
    # acceptable only because callers killpg nothing but their own groups.
    _TOMBSTONE_LIMIT = 4096

    def _bury_locked(pid: int) -> None:
        _tombstones[pid] = _created.pop(pid, None)
        _tombstones.move_to_end(pid)
        while len(_tombstones) > _TOMBSTONE_LIMIT:
            _tombstones.popitem(last=False)

    def _spawn_flags(kwargs: dict[str, Any]) -> dict[str, Any]:
        if "start_new_session" in kwargs:
            raise TypeError("popen_group always starts a new group")
        flags = kwargs.pop("creationflags", 0)
        return {**kwargs, "creationflags": flags | _CREATE_SUSPENDED | _CREATE_NEW_PROCESS_GROUP}

    def _active_processes(job: Any) -> int:
        accounting = win32job.QueryInformationJobObject(
            job, win32job.JobObjectBasicAccountingInformation)
        return int(accounting["ActiveProcesses"])

    def _prune_locked(keep: int) -> None:
        for pid, job in list(_jobs.items()):
            if pid != keep and _active_processes(job) == 0:
                del _jobs[pid]
                _bury_locked(pid)
                job.Close()

    def _register(pid: int, job: Any, created: int | None) -> None:
        """Own ``job`` in the table first; pruning is housekeeping only."""
        with _jobs_lock:
            stale = _jobs.get(pid)
            _jobs[pid] = job
            _created[pid] = created
            _tombstones.pop(pid, None)
            if stale is not None:
                stale.Close()  # the pid was reused after an unpruned tree
            try:
                _prune_locked(pid)
            except Exception:  # never fail a spawn on cleanup
                _log.exception("job_prune_failed")

    def _discard(pid: int, job: Any) -> None:
        with _jobs_lock:
            if _jobs.get(pid) is job:
                del _jobs[pid]
                _bury_locked(pid)
        try:
            win32job.TerminateJobObject(job, 1)
        finally:
            job.Close()

    def _adopt(pid: int) -> None:
        """Place the suspended ``pid`` in a fresh Job, then resume it."""
        with translated():
            process = win32api.OpenProcess(_PROCESS_ACCESS, False, pid)
            try:
                job = win32job.CreateJobObject(None, "")
                try:
                    win32job.AssignProcessToJobObject(job, process)
                except BaseException:
                    job.Close()
                    win32api.TerminateProcess(process, 1)
                    raise
                try:
                    from cc_remote.platform.win32_process import creation_time

                    _register(pid, job, creation_time(pid))
                    if _ntdll.NtResumeProcess(int(process)) != 0:
                        raise OSError("cannot resume the suspended child process")
                except BaseException:
                    _discard(pid, job)
                    raise
            finally:
                process.Close()

    def popen_group(args: Any, **kwargs: Any) -> subprocess.Popen:
        child = subprocess.Popen(args, **_spawn_flags(kwargs))
        try:
            _adopt(child.pid)
        except BaseException:
            child.kill()
            raise
        return child

    async def create_subprocess_exec_group(
        program: Any, *args: Any, **kwargs: Any,
    ) -> asyncio.subprocess.Process:
        child = await asyncio.create_subprocess_exec(
            program, *args, **_spawn_flags(kwargs))
        try:
            _adopt(child.pid)
        except BaseException:
            child.kill()
            raise
        return child

    def killpg(pid: int, sig: int) -> None:
        """Terminate the Job for ``pid``; the Job stays tracked until it is empty.

        A SIGTERM-then-SIGKILL escalation therefore reaches the same Job (or
        ProcessLookupError once it drained), and a pid whose Job was pruned is
        tombstoned: it never reaches ``taskkill``, which could kill whatever
        unrelated tree inherited the recycled pid.
        """
        del sig  # Windows has no deliverable graceful signal for console-less trees
        with _jobs_lock:
            job = _jobs.get(pid)
            if job is not None:
                with translated():
                    if _active_processes(job) == 0:
                        del _jobs[pid]
                        _bury_locked(pid)
                        job.Close()
                        raise ProcessLookupError(pid)
                    win32job.TerminateJobObject(job, 1)
                return
            if pid in _tombstones:
                raise ProcessLookupError(pid)
        result = subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True, check=False)
        if result.returncode == 128:
            raise ProcessLookupError(pid)
        if result.returncode != 0:
            raise OSError(f"taskkill failed for pid {pid}: {result.returncode}")
else:
    def popen_group(args: Any, **kwargs: Any) -> subprocess.Popen:
        return subprocess.Popen(args, start_new_session=True, **kwargs)

    async def create_subprocess_exec_group(
        program: Any, *args: Any, **kwargs: Any,
    ) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            program, *args, start_new_session=True, **kwargs)

    def killpg(pid: int, sig: int) -> None:
        os.killpg(pid, sig)

__all__ = ["SIGKILL", "SIGTERM", "create_subprocess_exec_group", "killpg", "popen_group"]
