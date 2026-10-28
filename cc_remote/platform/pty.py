"""Portable pseudo-terminals and raw local terminals for the Claude broker.

The broker hosts official Claude Code TUIs in pseudo-terminals: POSIX uses
``openpty`` with a new session and controlling terminal; Windows uses ConPTY
through pywinpty. ``claude-remote`` attaches the user's own terminal in raw
mode: termios on POSIX, console modes with VT input/output on Windows.

Windows process trees: each ConPTY child joins its own Job so ``kill_group``
ends the whole tree. pywinpty starts the child itself and cannot start it
suspended, so a descendant spawned before the Job assignment (microseconds)
escapes the Job; releasing the pseudo console still ends every process
attached to it. After ``wait()`` (which calls ``close()``) the Job is released
too, so ``kill_group`` no longer reaches descendants that detached from the
console (``CREATE_NO_WINDOW`` / ``DETACHED_PROCESS``). Both are residuals of
the kind a POSIX child that escapes with ``setsid()`` leaves.
"""
from __future__ import annotations

import asyncio
import codecs
import functools
import os
import shutil
import stat
import struct
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from cc_remote.log import logger
from cc_remote.platform import IS_WINDOWS, procs

READ_CHUNK_BYTES = 64 * 1024
_EXIT_POLL_SECONDS = 0.05
# ConPTY renders the child's last frame asynchronously after it exits: wait
# for this much output silence (bounded) before stopping the reader.
_EXIT_SETTLE_SECONDS = 0.1
_EXIT_SETTLE_MAX_SECONDS = 1.0
_READER_STOP_SECONDS = 2.0
_RESIZE_POLL_SECONDS = 0.25
# An auto-started server must not die with the console that launched it.
# POSIX needs nothing here: start_new_session already leaves the terminal.
DETACHED_CREATIONFLAGS = subprocess.DETACHED_PROCESS if IS_WINDOWS else 0

if IS_WINDOWS:
    import ctypes
    import msvcrt
    from ctypes import wintypes
else:
    import fcntl
    import termios
    import tty

OutputCallback = Callable[[bytes], None]
_log = logger("cc_remote.platform.pty")


class PtyProcess:
    """A child process whose stdio is a new pseudo-terminal.

    ``start`` delivers output chunks on the running event loop. ``wait``
    returns the exit status; ``drain`` then yields bytes still buffered in the
    terminal. ``kill_group`` signals the whole process tree and raises
    ``ProcessLookupError`` once it is gone. On Windows ``wait`` also releases
    the pseudo console (``close`` is idempotent), and concurrent ``write``
    calls are applied one at a time in call order.
    """

    pid: int
    start_ticks: int

    def start(self, on_output: OutputCallback) -> None:
        raise NotImplementedError

    def stop_reading(self) -> None:
        raise NotImplementedError

    def poll(self) -> int | None:
        raise NotImplementedError

    async def wait(self) -> int:
        raise NotImplementedError

    def drain(self) -> list[bytes]:
        raise NotImplementedError

    async def write(self, data: bytes) -> None:
        raise NotImplementedError

    def resize(self, rows: int, cols: int, xpixel: int = 0, ypixel: int = 0) -> None:
        raise NotImplementedError

    def kill_group(self, sig: int) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def spawn(argv: Sequence[str], *, cwd: str, env: dict[str, str],
          rows: int = 24, cols: int = 80) -> PtyProcess:
    """Start ``argv`` in a new pseudo-terminal of ``rows`` x ``cols``."""
    if IS_WINDOWS:
        return _WindowsPty.spawn(list(argv), cwd=cwd, env=env, rows=rows, cols=cols)
    return _PosixPty.spawn(list(argv), cwd=cwd, env=env, rows=rows, cols=cols)


# --- POSIX pseudo-terminal --------------------------------------------------

def _posix_start_ticks(pid: int) -> int:
    """Return a stable process-incarnation token on Linux and macOS.

    Linux exposes the kernel start tick in ``/proc``.  macOS has no equivalent
    cheap stdlib API, so a monotonic timestamp captured immediately after spawn
    serves the same PID-reuse guard when combined with the broker generation.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        # The comm field may contain spaces and parentheses.  Fields after its
        # final ')' start at stat field 3; starttime is field 22.
        return int(raw[raw.rfind(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return time.monotonic_ns()


def _child_setup() -> None:
    """Make the slave side the child's controlling terminal."""
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class _PosixPty(PtyProcess):
    def __init__(self, process: subprocess.Popen[bytes], master_fd: int):
        self.process = process
        self.master_fd = master_fd
        self.pid = process.pid
        self.start_ticks = _posix_start_ticks(process.pid)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_output: OutputCallback | None = None
        self._reader_active = False

    @classmethod
    def spawn(cls, argv: list[str], *, cwd: str, env: dict[str, str],
              rows: int, cols: int) -> "_PosixPty":
        master_fd, slave_fd = os.openpty()
        try:
            # Claude starts before a client necessarily attaches.  Give its TUI
            # a sane initial geometry instead of the platform's 0x0 PTY default;
            # the first attached terminal replaces this immediately.
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            process = subprocess.Popen(
                argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                env=env,
                close_fds=True,
                preexec_fn=_child_setup,
            )
        except BaseException:
            os.close(master_fd)
            raise
        finally:
            os.close(slave_fd)
        os.set_blocking(master_fd, False)
        return cls(process, master_fd)

    def start(self, on_output: OutputCallback) -> None:
        self._loop = asyncio.get_running_loop()
        self._on_output = on_output
        self._loop.add_reader(self.master_fd, self._on_readable)
        self._reader_active = True

    def _on_readable(self) -> None:
        try:
            data = os.read(self.master_fd, READ_CHUNK_BYTES)
        except BlockingIOError:
            return
        except OSError:
            # BSD and Linux PTYs commonly report EIO once the slave closes.
            self.stop_reading()
            return
        if not data:
            self.stop_reading()
            return
        assert self._on_output is not None
        self._on_output(data)

    def stop_reading(self) -> None:
        if self._reader_active and self._loop is not None:
            self._loop.remove_reader(self.master_fd)
            self._reader_active = False

    def poll(self) -> int | None:
        return self.process.poll()

    async def wait(self) -> int:
        # Keep the standalone broker single-threaded.  PTY spawning performs a
        # tiny controlling-terminal pre-exec step; avoiding executor threads
        # prevents later session spawns from hitting fork/preexec deadlocks.
        while True:
            returncode = self.process.poll()
            if returncode is not None:
                return returncode
            await asyncio.sleep(_EXIT_POLL_SECONDS)

    def drain(self) -> list[bytes]:
        chunks: list[bytes] = []
        while True:
            try:
                data = os.read(self.master_fd, READ_CHUNK_BYTES)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
        return chunks

    async def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            try:
                written = os.write(self.master_fd, view)
            except BlockingIOError:
                await self._wait_writable()
                continue
            view = view[written:]

    async def _wait_writable(self) -> None:
        loop = asyncio.get_running_loop()
        ready = loop.create_future()

        def mark_ready() -> None:
            if not ready.done():
                ready.set_result(None)

        loop.add_writer(self.master_fd, mark_ready)
        try:
            await ready
        finally:
            loop.remove_writer(self.master_fd)

    def resize(self, rows: int, cols: int, xpixel: int = 0, ypixel: int = 0) -> None:
        fcntl.ioctl(
            self.master_fd, termios.TIOCSWINSZ,
            struct.pack("HHHH", rows, cols, xpixel, ypixel),
        )

    def kill_group(self, sig: int) -> None:
        procs.killpg(self.pid, sig)

    def close(self) -> None:
        self.stop_reading()
        try:
            os.close(self.master_fd)
        except OSError:
            pass


# --- Windows ConPTY -----------------------------------------------------------

_PROCESS_TERMINATE_AND_QUOTA = 0x0001 | 0x0100 | 0x1000  # TERMINATE|SET_QUOTA|QUERY_LIMITED
_PROCESS_QUERY_AND_SYNCHRONIZE = 0x1000 | 0x00100000  # QUERY_LIMITED|SYNCHRONIZE


def _windows_job(pid: int) -> Any | None:
    """Put the running child in its own Job so ``kill_group`` reaches its tree.

    pywinpty cannot start the child suspended, so a descendant spawned before
    this assignment can escape the Job. Releasing the pseudo console (on exit
    in ``wait()``, or in ``close()``) still ends every process attached to it.
    """
    import win32api
    import win32job

    from cc_remote.platform._win32err import translated

    try:
        with translated():
            process = win32api.OpenProcess(_PROCESS_TERMINATE_AND_QUOTA, False, pid)
            try:
                job = win32job.CreateJobObject(None, "")
                try:
                    win32job.AssignProcessToJobObject(job, process)
                except BaseException:
                    job.Close()
                    raise
                return job
            finally:
                process.Close()
    except OSError:
        return None  # kill_group falls back to terminating the process itself


def _process_handle(pid: int) -> Any | None:
    """Our own handle on the child: its exit status outlives the pseudo console.

    The ``_WindowsPty`` (and, after ``close()``, its ``_ClosedTerminal``) keeps
    it for its whole lifetime; pywin32 closes it when the object is garbage
    collected. Until then it also keeps the pid from being reused.
    """
    import pywintypes
    import win32api

    try:
        return win32api.OpenProcess(_PROCESS_QUERY_AND_SYNCHRONIZE, False, pid)
    except pywintypes.error:
        return None


def _terminate_process(pid: int) -> None:
    import win32api

    from cc_remote.platform._win32err import translated

    with translated():
        process = win32api.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        try:
            win32api.TerminateProcess(process, 1)
        finally:
            process.Close()


def _environment_block(env: dict[str, str]) -> str:
    return "\0".join(f"{key}={value}" for key, value in env.items()) + "\0"


class _WindowsPty(PtyProcess):
    def __init__(self, terminal: Any, job: Any | None = None):
        from cc_remote.platform import win32_process

        self._terminal = terminal
        self._job = job
        self._reader: threading.Thread | None = None
        self._closed = False
        self._last_output = 0.0
        self.pid = int(terminal.pid)
        self.start_ticks = win32_process.creation_time(self.pid) or time.monotonic_ns()
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_output: OutputCallback | None = None
        self._reading = False
        self._stop = threading.Event()
        self._reader_done: asyncio.Future[None] | None = None
        self._returncode: int | None = None
        self._write_lock = asyncio.Lock()
        # Opened while pywinpty still holds the child's handle, so the pid is
        # this child; the handle in turn keeps the pid from being reused.
        self._process = _process_handle(self.pid)

    @classmethod
    def spawn(cls, argv: list[str], *, cwd: str, env: dict[str, str],
              rows: int, cols: int) -> "_WindowsPty":
        import winpty

        from cc_remote.platform import executables

        command = executables.command_for(argv[0]) + argv[1:]
        program = command[0] if os.path.isabs(command[0]) else shutil.which(command[0])
        if program is None:
            raise FileNotFoundError(f"executable not found: {command[0]}")
        arguments = " " + subprocess.list2cmdline(command[1:]) if len(command) > 1 else None
        try:
            terminal = winpty.PTY(cols, rows, backend=winpty.Backend.ConPTY)
            terminal.spawn(program, cmdline=arguments, cwd=cwd, env=_environment_block(env))
        except winpty.WinptyError as exc:
            raise OSError(f"ConPTY spawn failed: {exc}") from exc
        return cls(terminal, _windows_job(int(terminal.pid)))

    def start(self, on_output: OutputCallback) -> None:
        self._loop = asyncio.get_running_loop()
        self._on_output = on_output
        self._reading = True
        self._reader_done = self._loop.create_future()
        self._reader = threading.Thread(target=self._pump, name=f"conpty-{self.pid}", daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    text = self._terminal.read(blocking=True)
                except Exception:  # WinptyError: cancelled or closed pseudo console
                    return
                if text:
                    self._last_output = time.monotonic()
                    self._post(self._deliver, text.encode("utf-8", "replace"))
                else:
                    time.sleep(0.01)
        finally:
            self._post(self._finish_reading)

    def _post(self, callback: Callable[..., None], *args: Any) -> None:
        assert self._loop is not None
        try:
            self._loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:  # the loop already closed; nobody is listening
            pass

    def _deliver(self, data: bytes) -> None:
        if self._reading and self._on_output is not None:
            self._on_output(data)

    def _finish_reading(self) -> None:
        if self._reader_done is not None and not self._reader_done.done():
            self._reader_done.set_result(None)

    def stop_reading(self) -> None:
        self._reading = False

    def poll(self) -> int | None:
        if self._returncode is None:
            try:
                alive = self._terminal.isalive()
                status = None if alive else self._terminal.get_exitstatus()
            except OSError:
                raise
            except Exception as exc:  # WinptyError
                raise OSError(f"ConPTY poll failed: {exc}") from exc
            if not alive:
                # Exit codes are DWORDs; report them unsigned, as Popen does
                # (0xC000013A, not -1073741510), whichever source answered.
                self._returncode = 1 if status is None else int(status) & 0xFFFFFFFF
        return self._returncode

    async def wait(self) -> int:
        while self.poll() is None:
            await asyncio.sleep(_EXIT_POLL_SECONDS)
        await self._settle()
        await self._stop_reader()
        # Like a POSIX hangup when the session leader exits: release the
        # pseudo console so descendants that escaped the Job end with it.
        self.close()
        assert self._returncode is not None
        return self._returncode

    async def _settle(self) -> None:
        exited = time.monotonic()
        while True:
            now = time.monotonic()
            if (now - max(exited, self._last_output) >= _EXIT_SETTLE_SECONDS
                    or now - exited >= _EXIT_SETTLE_MAX_SECONDS):
                return
            await asyncio.sleep(0.02)

    async def _stop_reader(self) -> None:
        self._stop.set()
        done = self._reader_done
        if done is None:
            return
        deadline = time.monotonic() + _READER_STOP_SECONDS
        # cancel_io only cancels a read already in flight; repeat until the
        # reader observed either the cancellation or the stop flag.
        while not done.done() and time.monotonic() < deadline:
            try:
                self._terminal.cancel_io()
            except Exception:  # WinptyError: the reader is between two reads
                pass
            await asyncio.wait({done}, timeout=0.05)

    def drain(self) -> list[bytes]:
        return []  # the reader delivered everything before wait() returned

    async def write(self, data: bytes) -> None:
        # Each write runs on a worker thread: without the lock, concurrent
        # callers could reach the console out of order (and share the decoder).
        async with self._write_lock:
            text = self._decoder.decode(data)
            if not text:
                return  # an incomplete UTF-8 sequence waits for its next bytes
            # The worker thread cannot be stopped: a cancelled caller keeps the
            # lock until it finished, so the next write cannot overtake it.
            writing = asyncio.ensure_future(asyncio.to_thread(self._terminal.write, text))
            try:
                await asyncio.shield(writing)
            except asyncio.CancelledError:
                await _outlast(writing)
                raise
            except Exception as exc:  # WinptyError
                raise OSError(f"ConPTY write failed: {exc}") from exc

    def resize(self, rows: int, cols: int, xpixel: int = 0, ypixel: int = 0) -> None:
        del xpixel, ypixel  # ConPTY has no pixel geometry
        try:
            self._terminal.set_size(cols, rows)
        except Exception as exc:  # WinptyError
            raise OSError(f"ConPTY resize failed: {exc}") from exc

    def kill_group(self, sig: int) -> None:
        del sig  # Windows has no graceful signal for a console-less tree
        import win32job

        from cc_remote.platform._win32err import translated

        if self._job is None:
            if self.poll() is not None:
                raise ProcessLookupError(self.pid)
            # pywinpty and self._process keep the handle open: no pid reuse.
            try:
                _terminate_process(self.pid)
            except PermissionError:
                # TerminateProcess denies a child that exited after poll().
                if self.poll() is not None:
                    raise ProcessLookupError(self.pid) from None
                raise
            return
        with translated():
            accounting = win32job.QueryInformationJobObject(
                self._job, win32job.JobObjectBasicAccountingInformation)
            if int(accounting["ActiveProcesses"]) == 0:
                raise ProcessLookupError(self.pid)
            # A Job that drains after the query terminates as a no-op, like
            # killpg reaching a group whose members are all zombies.
            win32job.TerminateJobObject(self._job, 1)

    def close(self) -> None:
        self._reading = False
        self._stop.set()
        if self._closed:
            return
        self._closed = True
        try:
            self.poll()  # keep an exit status pywinpty already knows
        except OSError:
            pass  # the process handle below still reports it
        # _dispose pops the only other reference, so the pseudo console is
        # released there, before the Job handle closes.
        console = [self._terminal]
        self._terminal = _ClosedTerminal(self._process)
        job, self._job = self._job, None
        # Releasing the pseudo console ends every attached process; on Windows
        # 10 that can wait for its output pipe, so never block the event loop.
        threading.Thread(
            target=_dispose, args=(console, job, self._reader, self.pid),
            name=f"conpty-close-{self.pid}", daemon=True).start()


class _ClosedTerminal:
    """Stands in for a released pseudo console.

    The child's own process handle still reports whether it runs and its real
    exit status; without one (it could not be opened) it counts as exited.
    The handle is the ``_WindowsPty``'s and is closed when both are garbage
    collected.
    """

    def __init__(self, process: Any | None = None):
        self._process = process

    def isalive(self) -> bool:
        if self._process is None:
            return False
        import win32event

        from cc_remote.platform._win32err import translated

        with translated():
            return win32event.WaitForSingleObject(self._process, 0) == win32event.WAIT_TIMEOUT

    def get_exitstatus(self) -> int | None:
        if self._process is None:
            return None
        import win32process

        from cc_remote.platform._win32err import translated

        with translated():
            return win32process.GetExitCodeProcess(self._process) & 0xFFFFFFFF

    def cancel_io(self) -> bool:
        return False

    def read(self, blocking: bool = False) -> str:
        raise OSError("pseudo console is closed")

    def write(self, text: str) -> int:
        raise OSError("pseudo console is closed")

    def set_size(self, cols: int, rows: int) -> None:
        raise OSError("pseudo console is closed")


async def _outlast(future: asyncio.Future[Any]) -> None:
    """Wait for ``future`` to finish, however often the waiter is cancelled."""
    while not future.done():
        try:
            await asyncio.wait({future})
        except asyncio.CancelledError:
            pass
    if not future.cancelled():
        future.exception()  # retrieved: the caller reports its cancellation instead


def _dispose(console: list[Any], job: Any | None, reader: threading.Thread | None,
             pid: int) -> None:
    """Release a pseudo console off the event loop.

    ``console`` holds the only other reference; popping it keeps the terminal
    out of this thread's arguments. ``cancel_io`` only cancels a read already
    in flight, and the reader may be between two reads; repeat it until the
    reader has left ``read()``. Only then is this the last reference, so
    pywinpty closes the pseudo console, before the Job handle closes.
    """
    terminal = console.pop()
    deadline = time.monotonic() + _READER_STOP_SECONDS
    while True:
        try:
            terminal.cancel_io()
        except Exception:  # WinptyError: nothing in flight
            pass
        if reader is None or not reader.is_alive() or time.monotonic() >= deadline:
            break
        reader.join(0.05)
    if reader is not None and reader.is_alive():
        # Its read() still holds the terminal: the pseudo console is released
        # only when that read returns. One console, bounded by its lifetime.
        _log.warning("conpty_reader_leaked", pid=pid)
    del terminal  # the last reference: pywinpty closes the pseudo console
    if job is not None:
        job.Close()


# --- the attaching user's terminal --------------------------------------------

_ENABLE_PROCESSED_INPUT = 0x0001
_ENABLE_LINE_INPUT = 0x0002
_ENABLE_ECHO_INPUT = 0x0004
_ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
_DISABLE_NEWLINE_AUTO_RETURN = 0x0008
_UTF8_CODE_PAGE = 65001


def _kernel32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _console_handle(fd: int) -> int | None:
    """The console handle behind ``fd``, or None when it is not a console."""
    try:
        handle = msvcrt.get_osfhandle(fd)
    except OSError:
        return None
    mode = ctypes.c_uint32()
    if not _kernel32().GetConsoleMode(handle, ctypes.byref(mode)):
        return None
    return handle


def _console_mode(handle: int) -> int:
    mode = ctypes.c_uint32()
    if not _kernel32().GetConsoleMode(handle, ctypes.byref(mode)):
        raise ctypes.WinError(ctypes.get_last_error())
    return mode.value


def _set_console_mode(handle: int, mode: int) -> None:
    if not _kernel32().SetConsoleMode(handle, mode):
        raise ctypes.WinError(ctypes.get_last_error())


def _console_output_cp() -> int:
    code_page = _kernel32().GetConsoleOutputCP()
    if not code_page:
        raise ctypes.WinError(ctypes.get_last_error())
    return code_page


def _set_console_output_cp(code_page: int) -> None:
    if not _kernel32().SetConsoleOutputCP(code_page):
        raise ctypes.WinError(ctypes.get_last_error())


class RawTerminal:
    """Put the attaching terminal in raw mode; restore it on exit.

    POSIX: ``tty.setraw`` on stdin when it carries the keyboard, as before.
    Windows: VT output and UTF-8 output on the console screen buffer, and VT
    input with line/echo/Ctrl+C processing disabled when it carries the
    keyboard, so keys reach the remote TUI as the same byte sequences.
    Neither touches a pipe or a file. Whatever was changed is restored on
    every exit, including a failure part-way through entering: each undo is
    registered before its change, so no interrupt can fall between the two
    (restoring an unchanged setting is harmless).
    """

    def __init__(self, stdin_fd: int, stdout_fd: int, *, keyboard: bool):
        self._stdin_fd = stdin_fd
        self._stdout_fd = stdout_fd
        self._keyboard = keyboard
        self._restore: list[Callable[[], object]] = []

    def __enter__(self) -> RawTerminal:
        try:
            if IS_WINDOWS:
                self._enter_windows()
            elif self._keyboard and os.isatty(self._stdin_fd):
                original = termios.tcgetattr(self._stdin_fd)
                self._restore.append(lambda: termios.tcsetattr(
                    self._stdin_fd, termios.TCSAFLUSH, original))
                tty.setraw(self._stdin_fd)
        except BaseException:
            self.__exit__()
            raise
        return self

    def _enter_windows(self) -> None:
        output = _console_handle(self._stdout_fd)
        if output is not None:
            output_mode = _console_mode(output)
            code_page = _console_output_cp()
            self._restore.append(lambda: _set_console_mode(output, output_mode))
            _set_console_mode(output, output_mode | _ENABLE_VIRTUAL_TERMINAL_PROCESSING
                              | _DISABLE_NEWLINE_AUTO_RETURN)
            self._restore.append(lambda: _set_console_output_cp(code_page))
            _set_console_output_cp(_UTF8_CODE_PAGE)
        console_input = _console_handle(self._stdin_fd) if self._keyboard else None
        if console_input is not None:
            input_mode = _console_mode(console_input)
            raw = input_mode & ~(_ENABLE_PROCESSED_INPUT | _ENABLE_LINE_INPUT
                                 | _ENABLE_ECHO_INPUT)
            self._restore.append(lambda: _set_console_mode(console_input, input_mode))
            _set_console_mode(console_input, raw | _ENABLE_VIRTUAL_TERMINAL_INPUT)

    def __exit__(self, *_exc: object) -> None:
        while self._restore:
            restore = self._restore.pop()
            try:
                restore()
            except OSError:
                pass


def terminal_size(fd: int) -> tuple[int, int, int, int]:
    """``(rows, cols, xpixel, ypixel)`` of the terminal on ``fd`` (24x80 default).

    Windows ignores ``fd``: the size is ``shutil.get_terminal_size()``, i.e.
    exported COLUMNS/LINES win (a frozen size the resize poll never sees
    change), else the process's stdout screen buffer, else 80x24 when stdout
    is redirected even if stdin is a console.
    """
    if IS_WINDOWS:
        # A console input handle has no geometry; the process's screen buffer
        # (stdout) does, and shutil also honours COLUMNS/LINES overrides.
        size = shutil.get_terminal_size((80, 24))
        return size.lines, size.columns, 0, 0
    try:
        raw = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
        rows, cols, xpixel, ypixel = struct.unpack("HHHH", raw)
        if rows and cols:
            return rows, cols, xpixel, ypixel
    except OSError:
        pass
    return 24, 80, 0, 0


def watch_resize(fd: int, callback: Callable[[], None]) -> Callable[[], None]:
    """Call ``callback`` on the running loop when the terminal on ``fd`` resizes.

    POSIX uses SIGWINCH; Windows has none, so it polls every 250 ms. A
    callback that raises is logged and the watch goes on, on both. Returns an
    ``unwatch()`` callable.
    """
    loop = asyncio.get_running_loop()
    if not IS_WINDOWS:
        import signal

        try:
            loop.add_signal_handler(signal.SIGWINCH, callback)
        except (NotImplementedError, RuntimeError):
            return lambda: None
        return lambda: loop.remove_signal_handler(signal.SIGWINCH)

    async def poll() -> None:
        last = terminal_size(fd)
        while True:
            await asyncio.sleep(_RESIZE_POLL_SECONDS)
            current = terminal_size(fd)
            if current != last:
                last = current
                try:
                    callback()
                except Exception:
                    _log.exception("resize_callback_failed")

    task = loop.create_task(poll())
    return task.cancel


_KEY_EVENT = 0x0001
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_CONSOLE_POLL_MS = 100
_CONSOLE_RECORDS = 64
_CTRL_PRESSED = 0x0004 | 0x0008  # RIGHT_CTRL_PRESSED | LEFT_CTRL_PRESSED
_VK_SPACE = 0x20
_VK_2 = 0x32
_VK_MENU = 0x12
# (key down, repeat count, virtual key, UTF-16 code unit, control key state)
_ConsoleKey = tuple[bool, int, int, int, int]


@functools.lru_cache(maxsize=None)
def _console_input_api() -> tuple[Any, Any]:
    """kernel32 with prototypes for the console input calls, and INPUT_RECORD."""
    class KeyEventRecord(ctypes.Structure):
        _fields_ = [
            ("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD),
            ("wVirtualKeyCode", wintypes.WORD), ("wVirtualScanCode", wintypes.WORD),
            ("UnicodeChar", wintypes.WORD), ("dwControlKeyState", wintypes.DWORD),
        ]

    class Event(ctypes.Union):
        # MOUSE_EVENT_RECORD is the largest member (16 bytes).
        _fields_ = [("KeyEvent", KeyEventRecord), ("_size", ctypes.c_byte * 16)]

    class InputRecord(ctypes.Structure):
        _fields_ = [("EventType", wintypes.WORD), ("Event", Event)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetNumberOfConsoleInputEvents.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetNumberOfConsoleInputEvents.restype = wintypes.BOOL
    kernel32.ReadConsoleInputW.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(InputRecord), wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD))
    kernel32.ReadConsoleInputW.restype = wintypes.BOOL
    return kernel32, InputRecord


def _wait_console_input(handle: int, timeout_ms: int) -> bool:
    """Whether console input is queued, waiting up to ``timeout_ms``."""
    kernel32, _record = _console_input_api()
    result = kernel32.WaitForSingleObject(handle, timeout_ms)
    if result == _WAIT_OBJECT_0:
        return True
    if result == _WAIT_TIMEOUT:
        return False
    raise ctypes.WinError(ctypes.get_last_error())


def _read_console_input(handle: int) -> list[_ConsoleKey]:
    """Take the input records already queued; never waits for more.

    Only the key events are returned: focus, mouse, menu and buffer-size
    events are consumed and dropped.
    """
    kernel32, record = _console_input_api()
    queued = wintypes.DWORD()
    if not kernel32.GetNumberOfConsoleInputEvents(handle, ctypes.byref(queued)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not queued.value:
        return []
    records = (record * min(queued.value, _CONSOLE_RECORDS))()
    read = wintypes.DWORD()
    if not kernel32.ReadConsoleInputW(handle, records, len(records), ctypes.byref(read)):
        raise ctypes.WinError(ctypes.get_last_error())
    return [(bool(key.bKeyDown), key.wRepeatCount, key.wVirtualKeyCode, key.UnicodeChar,
             key.dwControlKeyState)
            for key in (item.Event.KeyEvent for item in records[:read.value]
                        if item.EventType == _KEY_EVENT)]


def _key_text(key: _ConsoleKey) -> str:
    """The characters a console key event types (VT input: escape sequences too)."""
    down, repeat, virtual_key, unit, control = key
    if not unit:
        # NUL has no character of its own: it is Ctrl+Space or Ctrl+@ (Ctrl+2).
        typed_nul = down and control & _CTRL_PRESSED and virtual_key in (_VK_SPACE, _VK_2)
        return "\0" * max(repeat, 1) if typed_nul else ""
    if not down and virtual_key != _VK_MENU:
        return ""  # a key-up; but Alt+numpad delivers its character on Alt's
    return chr(unit) * max(repeat, 1)


class _ConsoleKeyDecoder:
    """Console key events -> UTF-8, holding a high surrogate until its pair."""

    def __init__(self) -> None:
        self._high = ""

    def feed(self, keys: list[_ConsoleKey]) -> bytes:
        text = self._high + "".join(_key_text(key) for key in keys)
        self._high = ""
        if text and "\ud800" <= text[-1] <= "\udbff":
            text, self._high = text[:-1], text[-1]
        # Join surrogate pairs; a stray unpaired one still encodes.
        text = text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")
        return text.encode("utf-8", "surrogatepass")


class InputReader:
    """Read chunks of keyboard input from ``fd`` without blocking the loop.

    POSIX waits for readiness with ``add_reader``. Windows reads on a daemon
    thread (it never holds up interpreter exit). ``read`` returns b"" at EOF.

    A Windows console is read as raw key events (meant for use inside
    ``RawTerminal``): the thread waits on the console handle and takes only
    input already queued, so it is never blocked inside a read. Keys arrive
    as UTF-8 with VT sequences; every byte, 0x1a (Ctrl+Z) included, is a key,
    and EOF is only a console error or a closed handle. Pipes and files are
    read with a blocking ``os.read``, which ends at their EOF.

    ``close()`` stops reading and enqueues nothing more; cancel a pending
    ``read()`` yourself. On a Windows console, once ``close()`` returns no
    read is in flight or will be issued, so the next key reaches the
    console's next reader. A pipe read already blocked in ``os.read`` cannot
    be interrupted (non-interactive input): its data is dropped, and the
    thread ends at that read's return.
    """

    def __init__(self, fd: int):
        self._fd = fd
        self._queue: asyncio.Queue[bytes] | None = None
        self._stop = threading.Event()
        # Held around each console read and enqueue: close() waits it out.
        self._lock = threading.Lock()

    async def read(self) -> bytes:
        if not IS_WINDOWS:
            return await self._read_posix()
        if self._stop.is_set():
            return b""
        if self._queue is None:
            self._queue = asyncio.Queue()
            threading.Thread(
                target=self._pump, args=(asyncio.get_running_loop(), self._queue),
                name="stdin-reader", daemon=True).start()
        return await self._queue.get()

    def close(self) -> None:
        """Stop reading (idempotent); see the class docstring."""
        with self._lock:
            self._stop.set()

    async def _read_posix(self) -> bytes:
        mode = os.fstat(self._fd).st_mode
        if stat.S_ISREG(mode):
            return os.read(self._fd, READ_CHUNK_BYTES)
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[None] = loop.create_future()

        def mark_ready() -> None:
            if not ready.done():
                ready.set_result(None)

        loop.add_reader(self._fd, mark_ready)
        try:
            await ready
            return os.read(self._fd, READ_CHUNK_BYTES)
        finally:
            loop.remove_reader(self._fd)

    def _pump(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[bytes]) -> None:
        console = _console_handle(self._fd)
        if console is not None:
            self._pump_console(console, loop, queue)
            return
        while True:
            try:
                data = os.read(self._fd, READ_CHUNK_BYTES)
            except OSError:
                data = b""
            with self._lock:
                if self._stop.is_set() or not self._deliver(loop, queue, data) or not data:
                    return

    def _pump_console(self, console: int, loop: asyncio.AbstractEventLoop,
                      queue: asyncio.Queue[bytes]) -> None:
        decoder = _ConsoleKeyDecoder()
        while not self._stop.is_set():
            try:
                ready = _wait_console_input(console, _CONSOLE_POLL_MS)
                with self._lock:
                    if self._stop.is_set():
                        return
                    if not ready:
                        continue
                    data = decoder.feed(_read_console_input(console))
                    if data and not self._deliver(loop, queue, data):
                        return
            except OSError:
                with self._lock:
                    if not self._stop.is_set():
                        self._deliver(loop, queue, b"")
                return

    @staticmethod
    def _deliver(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[bytes],
                 data: bytes) -> bool:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, data)
        except RuntimeError:  # loop closed
            return False
        return True


__all__ = [
    "DETACHED_CREATIONFLAGS", "InputReader", "PtyProcess", "READ_CHUNK_BYTES", "RawTerminal",
    "spawn", "terminal_size", "watch_resize",
]
