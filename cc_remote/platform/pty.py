"""Portable pseudo-terminals and raw local terminals for the Claude broker.

The broker hosts official Claude Code TUIs in pseudo-terminals: POSIX uses
``openpty`` with a new session and controlling terminal; Windows uses ConPTY
through pywinpty. ``claude-remote`` attaches the user's own terminal in raw
mode: termios on POSIX, console modes with VT input/output on Windows.
"""
from __future__ import annotations

import asyncio
import codecs
import os
import shutil
import struct
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from cc_remote.platform import IS_WINDOWS, procs

READ_CHUNK_BYTES = 64 * 1024
_EXIT_POLL_SECONDS = 0.05
# ConPTY renders the child's last frame asynchronously after it exits: wait
# for this much output silence (bounded) before stopping the reader.
_EXIT_SETTLE_SECONDS = 0.1
_EXIT_SETTLE_MAX_SECONDS = 1.0
_READER_STOP_SECONDS = 2.0

if not IS_WINDOWS:
    import fcntl
    import termios

OutputCallback = Callable[[bytes], None]


class PtyProcess:
    """A child process whose stdio is a new pseudo-terminal.

    ``start`` delivers output chunks on the running event loop. ``wait``
    returns the exit status; ``drain`` then yields bytes still buffered in the
    terminal. ``kill_group`` signals the whole process tree and raises
    ``ProcessLookupError`` once it is gone.
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
        return cls(terminal)

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
        if self._returncode is None and not self._terminal.isalive():
            status = self._terminal.get_exitstatus()
            self._returncode = 1 if status is None else int(status)
        return self._returncode

    async def wait(self) -> int:
        while self.poll() is None:
            await asyncio.sleep(_EXIT_POLL_SECONDS)
        await self._settle()
        await self._stop_reader()
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
            self._terminal.cancel_io()
            await asyncio.wait({done}, timeout=0.05)

    def drain(self) -> list[bytes]:
        return []  # the reader delivered everything before wait() returned

    async def write(self, data: bytes) -> None:
        text = self._decoder.decode(data)
        if not text:
            return  # an incomplete UTF-8 sequence waits for its next bytes
        try:
            await asyncio.to_thread(self._terminal.write, text)
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
        if self.poll() is not None:
            raise ProcessLookupError(self.pid)
        # pywinpty keeps the process handle open, so the pid is not reused.
        _terminate_process(self.pid)

    def close(self) -> None:
        self._reading = False
        self._stop.set()
        if self._closed:
            return
        self._closed = True
        terminal, self._terminal = self._terminal, _ClosedTerminal()
        job, self._job = self._job, None
        # Releasing the pseudo console ends every attached process; on Windows
        # 10 that can wait for its output pipe, so never block the event loop.
        threading.Thread(
            target=_dispose, args=(terminal, job, self._reader),
            name=f"conpty-close-{self.pid}", daemon=True).start()


class _ClosedTerminal:
    """Stands in for a released pseudo console."""

    def isalive(self) -> bool:
        return False

    def get_exitstatus(self) -> int:
        return 1

    def cancel_io(self) -> bool:
        return False

    def read(self, blocking: bool = False) -> str:
        raise OSError("pseudo console is closed")

    def write(self, text: str) -> int:
        raise OSError("pseudo console is closed")

    def set_size(self, cols: int, rows: int) -> None:
        raise OSError("pseudo console is closed")


def _dispose(terminal: Any, job: Any | None, reader: threading.Thread | None) -> None:
    """Release a pseudo console off the event loop.

    ``cancel_io`` only cancels a read already in flight, and the reader may be
    between two reads; repeat it until the reader has left ``read()``. Only
    then is this the last reference, so pywinpty closes the pseudo console.
    """
    deadline = time.monotonic() + _READER_STOP_SECONDS
    while True:
        try:
            terminal.cancel_io()
        except Exception:  # WinptyError: nothing in flight
            pass
        if reader is None or not reader.is_alive() or time.monotonic() >= deadline:
            break
        reader.join(0.05)
    del terminal  # the last reference: pywinpty closes the pseudo console
    if job is not None:
        job.Close()
