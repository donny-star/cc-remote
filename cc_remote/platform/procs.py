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
from typing import Any

from cc_remote.platform import IS_WINDOWS

SIGTERM = int(signal.SIGTERM)
SIGKILL = int(getattr(signal, "SIGKILL", 9))

if IS_WINDOWS:
    import ctypes

    import pywintypes
    import win32api
    import win32con
    import win32job

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

    def _spawn_flags(kwargs: dict[str, Any]) -> dict[str, Any]:
        if "start_new_session" in kwargs:
            raise TypeError("popen_group always starts a new group")
        flags = kwargs.pop("creationflags", 0)
        return {**kwargs, "creationflags": flags | _CREATE_SUSPENDED | _CREATE_NEW_PROCESS_GROUP}

    def _prune_locked() -> None:
        for pid, job in list(_jobs.items()):
            accounting = win32job.QueryInformationJobObject(
                job, win32job.JobObjectBasicAccountingInformation)
            if accounting["ActiveProcesses"] == 0:
                job.Close()
                del _jobs[pid]

    def _adopt(pid: int) -> None:
        """Place the suspended ``pid`` in a fresh Job, then resume it."""
        process = win32api.OpenProcess(_PROCESS_ACCESS, False, pid)
        try:
            job = win32job.CreateJobObject(None, "")
            try:
                win32job.AssignProcessToJobObject(job, process)
            except pywintypes.error:
                job.Close()
                win32api.TerminateProcess(process, 1)
                raise
            with _jobs_lock:
                _prune_locked()
                _jobs[pid] = job
            if _ntdll.NtResumeProcess(int(process)) != 0:
                win32job.TerminateJobObject(job, 1)
                raise OSError("cannot resume the suspended child process")
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
        del sig  # Windows has no deliverable graceful signal for console-less trees
        with _jobs_lock:
            job = _jobs.pop(pid, None)
        if job is not None:
            try:
                win32job.TerminateJobObject(job, 1)
            finally:
                job.Close()
            return
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
