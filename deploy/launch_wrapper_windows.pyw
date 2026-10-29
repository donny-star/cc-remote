"""Run the cc-remote wrapper on Windows without ever showing a window.

Started by pythonw (no console). The wrapper itself runs under the venv's
console python.exe created with CREATE_NO_WINDOW: it gets a console that has
no window, and every node.exe/claude.exe/git child inherits that console
instead of opening a visible one. The wrapper is placed in a kill-on-close Job
so stopping this launcher (e.g. the scheduled task) stops the wrapper too.

Logs go to %LOCALAPPDATA%\\cc-remote\\logs\\wrapper.log (rotated at 10 MiB).
"""
import ctypes
import os
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "cc-remote" / "logs"
LOG = LOG_DIR / "wrapper.log"
CREATE_NO_WINDOW = 0x08000000
CREATE_SUSPENDED = 0x00000004


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if LOG.exists() and LOG.stat().st_size > 10 * 1024 * 1024:
        os.replace(LOG, LOG.with_suffix(".log.1"))
    python = APP / ".venv" / "Scripts" / "python.exe"
    env = dict(os.environ, PYTHONUTF8="1")
    import win32api
    import win32con
    import win32job

    job = win32job.CreateJobObject(None, "")
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    with open(LOG, "ab", buffering=0) as log:
        # Start suspended so the venv launcher cannot spawn the real
        # interpreter before both are inside the Job.
        child = subprocess.Popen(
            [str(python), "-X", "utf8", "-m", "cc_remote.wrapper"],
            cwd=str(APP), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=CREATE_NO_WINDOW | CREATE_SUSPENDED)
        handle = win32api.OpenProcess(
            win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE
            | win32con.PROCESS_SUSPEND_RESUME, False, child.pid)
        try:
            win32job.AssignProcessToJobObject(job, handle)
        finally:
            ctypes.WinDLL("ntdll").NtResumeProcess(ctypes.c_void_p(int(handle)))
        return child.wait()


if __name__ == "__main__":
    sys.exit(main())
