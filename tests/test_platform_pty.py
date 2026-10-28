"""cc_remote.platform.pty: pseudo-terminals for the broker and claude-remote."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
import weakref
from pathlib import Path

import pytest

from cc_remote.platform import IS_WINDOWS, procs, pty
from cc_remote.wrapper.process_scan import process_identity

ROOT = Path(__file__).resolve().parents[1]


def test_pywinpty_is_pinned_for_windows_wrappers_only():
    for name in ("requirements.txt", "requirements-wrapper.txt"):
        assert 'pywinpty==3.1.0 ; sys_platform == "win32"' in (ROOT / name).read_text(encoding="utf-8")
    for name in ("requirements.lock", "requirements-wrapper.lock"):
        lock = (ROOT / name).read_text(encoding="utf-8")
        assert "pywinpty==3.1.0 ; sys_platform == 'win32'" in lock
        assert "index-url" not in lock and "tuna" not in lock
    for name in ("requirements-relay.txt", "requirements-relay.lock"):
        assert "pywinpty" not in (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.windows_only(reason="ConPTY is the Windows pseudo-terminal")
def test_conpty_backend_is_available():
    import winpty

    assert winpty.Backend.ConPTY == 0


RAW_TERMINAL = r'''
def raw_terminal():
    """Raw keyboard input on a POSIX tty or a Windows console (ConPTY)."""
    if os.name != "nt":
        import tty

        tty.setraw(0)

        def read_posix():
            return os.read(0, 65536)

        return read_posix
    import ctypes
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32")
    handle = msvcrt.get_osfhandle(0)
    mode = ctypes.c_uint32()
    kernel32.GetConsoleMode(handle, ctypes.byref(mode))
    # Clear processed/line/echo input; VT input delivers keys as bytes.
    kernel32.SetConsoleMode(handle, (mode.value & ~0x7) | 0x200)

    def read_console():
        return sys.stdin.buffer.raw.read(65536)

    return read_console
'''

CHILD = r'''
import json
import os
import subprocess
import sys
''' + RAW_TERMINAL + r'''
read = raw_terminal()
os.write(1, b"ARGS:" + json.dumps(sys.argv[1:]).encode() + b"\n")
os.write(1, b"READY\n")
while True:
    data = read()
    if not data or data == b"\x04":
        break
    if data == b"SIZE":
        size = os.get_terminal_size(1)
        os.write(1, b"SIZE=%dx%d\n" % (size.lines, size.columns))
    elif data == b"SPAWN":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        os.write(1, b"GRANDCHILD=%d\n" % child.pid)
    else:
        os.write(1, b"OUT<" + data.hex().encode() + b">")
os.write(1, b"BYE\n")
sys.exit(7)
'''


class _Collector:
    def __init__(self) -> None:
        self.data = bytearray()
        self._changed = asyncio.Event()

    def __call__(self, chunk: bytes) -> None:
        self.data.extend(chunk)
        self._changed.set()

    async def until(self, predicate, timeout: float = 15.0) -> bytes:
        async with asyncio.timeout(timeout):
            while not predicate(bytes(self.data)):
                self._changed.clear()
                await self._changed.wait()
        return bytes(self.data)


def _args(output: bytes) -> list[str]:
    return json.loads(re.search(rb"ARGS:(\[[^\r\n]*\])", output).group(1))


def _echoed(output: bytes) -> bytes:
    return b"".join(bytes.fromhex(part.decode()) for part in re.findall(rb"OUT<([0-9a-f]*)>", output))


BULK_CHILD = r'''
import os
import sys
''' + RAW_TERMINAL + r'''
read = raw_terminal()
os.write(1, b"READY\n")
total = 0
while total < int(sys.argv[1]):
    data = read()
    if not data:
        break
    total += len(data)
os.write(1, b"TOTAL=%d\n" % total)
'''


async def _conpty_threads_gone(pid: int, timeout: float = 5.0) -> bool:
    """The reader (conpty-<pid>) and releaser (conpty-close-<pid>) threads ended."""
    names = {f"conpty-{pid}", f"conpty-close-{pid}"}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while any(thread.name in names for thread in threading.enumerate()):
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.05)
    return True


def _spawn(tmp_path: Path, *args: str) -> pty.PtyProcess:
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    return pty.spawn([sys.executable, str(script), *args], cwd=str(tmp_path), env=dict(os.environ))


async def _gone(pid: int, timeout: float = 10.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while process_identity(pid) is not None:
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.05)
    return True


async def _finish(terminal: pty.PtyProcess) -> None:
    try:
        terminal.kill_group(procs.SIGKILL)
    except ProcessLookupError:
        pass
    await asyncio.wait_for(terminal.wait(), 10)
    terminal.close()


@pytest.mark.asyncio
async def test_child_runs_in_a_terminal_with_exact_argv(tmp_path):
    output = _Collector()
    terminal = _spawn(tmp_path, "a b", 'q"x', "ü", "")
    try:
        terminal.start(output)
        data = await output.until(lambda data: b"READY" in data)
        assert _args(data) == ["a b", 'q"x', "ü", ""]
        assert terminal.pid > 0
        assert terminal.start_ticks > 0
        assert terminal.poll() is None
    finally:
        await _finish(terminal)


@pytest.mark.asyncio
async def test_input_bytes_reach_the_child_unchanged(tmp_path):
    output = _Collector()
    terminal = _spawn(tmp_path)
    expected = b"plain" + "中".encode() + b"\x03\x1b[Z\r"
    try:
        terminal.start(output)
        await output.until(lambda data: b"READY" in data)
        for chunk in (b"plain", "中".encode()[:1], "中".encode()[1:], b"\x03", b"\x1b[Z", b"\r"):
            await terminal.write(chunk)
        await output.until(lambda data: _echoed(data) == expected)
    finally:
        await _finish(terminal)


@pytest.mark.asyncio
async def test_resize_reaches_the_child(tmp_path):
    output = _Collector()
    terminal = _spawn(tmp_path)
    try:
        terminal.start(output)
        await output.until(lambda data: b"READY" in data)
        terminal.resize(30, 100)
        await terminal.write(b"SIZE")
        await output.until(lambda data: b"SIZE=30x100" in data)
    finally:
        await _finish(terminal)


@pytest.mark.asyncio
async def test_exit_status_and_trailing_output_are_delivered(tmp_path):
    output = _Collector()
    terminal = _spawn(tmp_path)
    terminal.start(output)
    await output.until(lambda data: b"READY" in data)
    await terminal.write(b"\x04")
    assert await asyncio.wait_for(terminal.wait(), 10) == 7
    assert b"BYE" in bytes(output.data) + b"".join(terminal.drain())
    assert terminal.poll() == 7
    terminal.stop_reading()
    terminal.close()


@pytest.mark.asyncio
async def test_close_ends_a_running_child_without_blocking(tmp_path):
    output = _Collector()
    terminal = _spawn(tmp_path)
    terminal.start(output)
    await output.until(lambda data: b"READY" in data)
    loop = asyncio.get_running_loop()
    started = loop.time()
    terminal.close()
    assert loop.time() - started < 0.5
    # POSIX: the child dies on EIO/SIGHUP and wait() reaps it; Windows: the
    # released pseudo console ends it.
    await asyncio.wait_for(terminal.wait(), 10)
    assert await _gone(terminal.pid)
    if IS_WINDOWS:
        # The reader left its blocking read() and the console was released.
        assert await _conpty_threads_gone(terminal.pid)
    terminal.close()  # idempotent


@pytest.mark.asyncio
async def test_bulk_input_arrives_complete(tmp_path):
    # pywinpty's write() always returns 0: the payload must still arrive whole.
    script = tmp_path / "bulk.py"
    script.write_text(BULK_CHILD, encoding="utf-8")
    output = _Collector()
    terminal = pty.spawn([sys.executable, str(script), "100000"], cwd=str(tmp_path),
                         env=dict(os.environ))
    try:
        terminal.start(output)
        await output.until(lambda data: b"READY" in data)
        await terminal.write(b"x" * 100_000)
        await output.until(lambda data: b"TOTAL=100000" in data, timeout=30)
    finally:
        await _finish(terminal)


@pytest.mark.windows_only(reason="ConPTY consumes the focus reports it requests itself")
@pytest.mark.asyncio
async def test_conpty_consumes_focus_reports(tmp_path):
    output = _Collector()
    terminal = _spawn(tmp_path)
    try:
        terminal.start(output)
        await output.until(lambda data: b"READY" in data)
        await terminal.write(b"\x1b[I\x1b[Ofocus")
        await output.until(lambda data: _echoed(data).endswith(b"focus"))
        assert _echoed(bytes(output.data)) == b"focus"
    finally:
        await _finish(terminal)


@pytest.mark.windows_only(reason="npm .cmd shims only exist on Windows")
@pytest.mark.asyncio
async def test_a_cmd_shim_runs_inside_conpty(tmp_path):
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    shim = tmp_path / "fake-claude.cmd"
    shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    output = _Collector()
    terminal = pty.spawn([str(shim), "--session-id", "x"], cwd=str(tmp_path), env=dict(os.environ))
    try:
        terminal.start(output)
        data = await output.until(lambda data: b"READY" in data)
        assert _args(data) == ["--session-id", "x"]
    finally:
        await _finish(terminal)


class _IdleConPty:
    """A pseudo console whose child exited while the reader sat between reads."""

    def __init__(self) -> None:
        self.pid = os.getpid()
        self.cancels = 0

    def isalive(self) -> bool:
        return False

    def get_exitstatus(self) -> int:
        return 3

    def read(self, blocking: bool = False) -> str:
        return ""

    def cancel_io(self) -> bool:
        import winpty

        self.cancels += 1
        raise winpty.WinptyError("nothing in flight")


@pytest.mark.windows_only(reason="pywinpty's cancel_io fails when no read is in flight")
@pytest.mark.asyncio
async def test_wait_survives_cancel_io_with_no_read_in_flight():
    console = _IdleConPty()
    terminal = pty._WindowsPty(console)
    terminal.start(lambda chunk: None)
    assert await asyncio.wait_for(terminal.wait(), 10) == 3
    assert console.cancels >= 1
    terminal.close()
    assert await _conpty_threads_gone(terminal.pid)


@pytest.mark.asyncio
async def test_kill_group_ends_the_tree_and_then_reports_it_gone(tmp_path):
    output = _Collector()
    terminal = _spawn(tmp_path)
    terminal.start(output)
    await output.until(lambda data: b"READY" in data)
    await terminal.write(b"SPAWN")
    data = await output.until(lambda data: re.search(rb"GRANDCHILD=(\d+)\r?\n", data))
    grandchild = int(re.search(rb"GRANDCHILD=(\d+)", data).group(1))
    terminal.kill_group(procs.SIGKILL)
    await asyncio.wait_for(terminal.wait(), 10)
    assert await _gone(grandchild)
    with pytest.raises(ProcessLookupError):
        terminal.kill_group(procs.SIGKILL)
    terminal.close()


@pytest.mark.windows_only(reason="pywinpty starts the child before it can join a Job")
@pytest.mark.asyncio
async def test_descendants_outside_the_job_end_with_the_pseudo_console(tmp_path, monkeypatch):
    # Simulate the residual race: a descendant that escaped the Job assignment.
    monkeypatch.setattr(pty, "_windows_job", lambda _pid: None)
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    # The base interpreter, like node.exe: a venv python.exe launcher holds its
    # child in the launcher's own kill-on-close Job, which would mask the escape.
    interpreter = getattr(sys, "_base_executable", sys.executable)
    output = _Collector()
    terminal = pty.spawn([interpreter, str(script)], cwd=str(tmp_path), env=dict(os.environ))
    terminal.start(output)
    await output.until(lambda data: b"READY" in data)
    await terminal.write(b"SPAWN")
    data = await output.until(lambda data: re.search(rb"GRANDCHILD=(\d+)\r?\n", data))
    grandchild = int(re.search(rb"GRANDCHILD=(\d+)", data).group(1))
    terminal.kill_group(procs.SIGKILL)  # without a Job this ends only the child
    await asyncio.wait_for(terminal.wait(), 10)  # ...and wait() releases the console
    assert await _gone(grandchild)
    terminal.close()


@pytest.mark.windows_only(reason="Job Objects are the Windows process tree")
@pytest.mark.asyncio
async def test_kill_group_reports_a_drained_job_gone_before_wait(tmp_path):
    output = _Collector()
    terminal = _spawn(tmp_path)
    try:
        terminal.start(output)
        await output.until(lambda data: b"READY" in data)
        terminal.kill_group(procs.SIGKILL)
        assert await _gone(terminal.pid)
        # The Job is still open (wait() has not released it) but empty.
        with pytest.raises(ProcessLookupError):
            terminal.kill_group(procs.SIGKILL)
    finally:
        await _finish(terminal)


@pytest.mark.windows_only(reason="pywinpty's TerminateProcess denies an exited child")
def test_kill_group_without_a_job_reports_a_child_that_just_exited_gone(monkeypatch):
    console = _FakeConPty()

    def exits_first(pid: int) -> None:
        console.alive = False  # the child exits between poll() and TerminateProcess
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(pty, "_terminate_process", exits_first)
    terminal = pty._WindowsPty(console)
    with pytest.raises(ProcessLookupError):
        terminal.kill_group(procs.SIGKILL)
    terminal.close()


@pytest.mark.windows_only(reason="the exit status outliving a released pseudo console is ConPTY's")
@pytest.mark.asyncio
async def test_poll_and_wait_after_close_report_the_real_exit_status(tmp_path):
    output = _Collector()
    terminal = _spawn(tmp_path)
    terminal.start(output)
    await output.until(lambda data: b"READY" in data)
    await terminal.write(b"\x04")
    assert await _gone(terminal.pid)
    terminal.close()  # nobody polled the exit before the console went away
    assert terminal.poll() == 7
    assert await asyncio.wait_for(terminal.wait(), 10) == 7


class _FakeConPty:
    """A pseudo console stand-in hosting a real process (this one by default)."""

    def __init__(self, pid: int | None = None) -> None:
        self.pid = os.getpid() if pid is None else pid
        self.alive = True
        self.written: list[str] = []
        self.release = threading.Event()

    def isalive(self) -> bool:
        return self.alive

    def get_exitstatus(self) -> int:
        return 3

    def read(self, blocking: bool = False) -> str:
        self.release.wait(0.01)
        return ""

    def cancel_io(self) -> bool:
        return False

    def write(self, text: str) -> int:
        self.written.append(text)
        return 0


@pytest.mark.windows_only(reason="exercises the ConPTY host directly")
@pytest.mark.asyncio
async def test_close_of_a_running_child_still_reports_its_real_status():
    interpreter = getattr(sys, "_base_executable", sys.executable)
    child = subprocess.Popen([interpreter, "-c", "import sys; sys.stdin.read(); sys.exit(5)"],
                             stdin=subprocess.PIPE)
    try:
        terminal = pty._WindowsPty(_FakeConPty(child.pid))
        terminal.start(lambda chunk: None)
        terminal.close()  # the child outlives the pseudo console stand-in
        assert terminal.poll() is None
        child.stdin.close()
        assert await asyncio.wait_for(terminal.wait(), 10) == 5
        assert terminal.poll() == 5
    finally:
        child.kill()
        child.wait()
    assert await _conpty_threads_gone(terminal.pid)


@pytest.mark.windows_only(reason="pywinpty raises WinptyError")
def test_poll_reports_pywinpty_errors_as_oserror():
    import winpty

    console = _FakeConPty()

    def broken() -> bool:
        raise winpty.WinptyError("pseudo console gone")

    console.isalive = broken
    terminal = pty._WindowsPty(console)
    with pytest.raises(OSError) as raised:
        terminal.poll()
    assert not isinstance(raised.value, winpty.WinptyError)
    terminal.close()  # close() polls too, and must not raise


@pytest.mark.windows_only(reason="ConPTY writes run on worker threads")
@pytest.mark.asyncio
async def test_concurrent_writes_keep_their_order():
    console = _FakeConPty()
    delays = iter([0.2, 0.1, 0.0])

    def slow_write(text: str) -> int:
        time.sleep(next(delays))
        console.written.append(text)
        return 0

    console.write = slow_write
    terminal = pty._WindowsPty(console)
    try:
        await asyncio.gather(terminal.write(b"a"), terminal.write(b"b"), terminal.write(b"c"))
        assert console.written == ["a", "b", "c"]
    finally:
        terminal.close()


@pytest.mark.windows_only(reason="ConPTY writes run on worker threads")
@pytest.mark.asyncio
async def test_a_cancelled_write_still_precedes_the_next_one():
    console = _FakeConPty()
    started = threading.Event()
    release = threading.Event()

    def held_write(text: str) -> int:
        if text == "a":
            started.set()
            release.wait(5)
        console.written.append(text)
        return 0

    console.write = held_write
    terminal = pty._WindowsPty(console)
    try:
        first = asyncio.create_task(terminal.write(b"a"))
        assert await asyncio.to_thread(started.wait, 5)
        first.cancel()  # its worker thread keeps writing "a"
        second = asyncio.create_task(terminal.write(b"b"))
        await asyncio.sleep(0.2)
        assert console.written == []  # "b" waits behind the unfinished "a"
        release.set()
        await asyncio.wait_for(second, 5)
        with pytest.raises(asyncio.CancelledError):
            await first
        assert console.written == ["a", "b"]
    finally:
        release.set()
        terminal.close()


@pytest.mark.windows_only(reason="ConPTY writes run on worker threads")
@pytest.mark.asyncio
async def test_close_releases_a_cancelled_write_stuck_on_the_console():
    console = _FakeConPty()
    started = threading.Event()
    release = threading.Event()
    daemons: list[bool] = []

    def stuck_write(text: str) -> int:
        daemons.append(threading.current_thread().daemon)
        started.set()
        release.wait(30)
        return 0

    console.write = stuck_write
    terminal = pty._WindowsPty(console)
    try:
        writing = asyncio.create_task(terminal.write(b"a"))
        assert await asyncio.to_thread(started.wait, 5)
        writing.cancel()
        await asyncio.sleep(0.2)
        assert not writing.done()  # still ordered ahead of any later write
        terminal.close()  # the console is gone: nothing is left to order
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(writing, 2)
        # Never a default-executor thread: asyncio.run and interpreter exit
        # would both wait for it.
        assert daemons == [True]
    finally:
        release.set()
        terminal.close()


@pytest.mark.windows_only(reason="ConPTY writes run on worker threads")
@pytest.mark.asyncio
async def test_a_write_after_close_fails_while_an_abandoned_write_is_still_stuck():
    console = _FakeConPty()
    started = threading.Event()
    release = threading.Event()

    def stuck_write(text: str) -> int:
        if text == "a":
            started.set()
            release.wait(30)
        console.written.append(text)
        return 0

    console.write = stuck_write
    terminal = pty._WindowsPty(console)
    try:
        writing = asyncio.create_task(terminal.write(b"a"))
        assert await asyncio.to_thread(started.wait, 5)
        writing.cancel()
        terminal.close()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(writing, 2)
        # "a" is still blocked on the real console; nothing may reach it now.
        with pytest.raises(OSError):
            await asyncio.wait_for(terminal.write(b"b"), 2)
        assert console.written == []
    finally:
        release.set()
        terminal.close()


_STUCK_WRITE_SHUTDOWN = r"""
import asyncio, os, sys, threading
sys.path.insert(0, sys.argv[1])
from cc_remote.platform import pty

class Console:
    pid = os.getpid()
    def isalive(self): return True
    def get_exitstatus(self): return None
    def cancel_io(self): return False
    def read(self, blocking=False):
        threading.Event().wait(0.01)
        return ""
    def write(self, text):
        threading.Event().wait()  # a console that never takes the input

async def main():
    terminal = pty._WindowsPty(Console())
    # Abandoned like PTYSession.close abandons its writer past the bound.
    writer = asyncio.ensure_future(terminal.write(b"x"))
    await asyncio.sleep(0.2)
    writer.cancel()
    await asyncio.wait({writer}, timeout=0.2)
    terminal.close()
    print("closed", flush=True)

asyncio.run(main())
print("finished", flush=True)
"""


@pytest.mark.windows_only(reason="ConPTY writes run on worker threads")
def test_a_stuck_console_write_does_not_hold_up_process_exit():
    child = subprocess.Popen(
        [sys.executable, "-c", _STUCK_WRITE_SHUTDOWN, str(ROOT)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        output, _ = child.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        child.kill()
        output, _ = child.communicate()
        pytest.fail(f"process exit waited for the stuck write: {output!r}")
    assert child.returncode == 0, output
    assert output.split() == ["closed", "finished"]


_NTSTATUS_EXIT = "import os; os._exit(-1073741510)"  # STATUS_CONTROL_C_EXIT


@pytest.mark.windows_only(reason="Windows exit codes are DWORDs")
@pytest.mark.asyncio
async def test_an_ntstatus_exit_code_is_reported_unsigned_either_way(tmp_path):
    # pywinpty answers while it holds the console...
    terminal = pty.spawn([sys.executable, "-c", _NTSTATUS_EXIT], cwd=str(tmp_path),
                         env=dict(os.environ))
    terminal.start(lambda chunk: None)
    assert await asyncio.wait_for(terminal.wait(), 10) == 0xC000013A
    # ...and the process handle once the console is gone, in the same form.
    interpreter = getattr(sys, "_base_executable", sys.executable)
    child = subprocess.Popen([interpreter, "-c", "import sys; sys.stdin.read(); " + _NTSTATUS_EXIT],
                             stdin=subprocess.PIPE)
    try:
        released = pty._WindowsPty(_FakeConPty(child.pid))
        released.close()
        child.stdin.close()
        assert await asyncio.wait_for(released.wait(), 10) == 0xC000013A
        assert child.wait(10) == 0xC000013A  # Popen's form
    finally:
        child.kill()
        child.wait()
    assert await _conpty_threads_gone(terminal.pid)
    assert await _conpty_threads_gone(released.pid)


class _RecordingJob:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def Close(self) -> None:  # noqa: N802 - mirrors the pywin32 handle
        self.events.append("job")


@pytest.mark.windows_only(reason="exercises the ConPTY release order")
@pytest.mark.asyncio
async def test_close_releases_the_pseudo_console_before_the_job():
    events: list[str] = []
    console = _FakeConPty()
    weakref.finalize(console, events.append, "console")
    terminal = pty._WindowsPty(console, _RecordingJob(events))
    del console
    terminal.start(lambda chunk: None)
    terminal.close()
    assert await _conpty_threads_gone(terminal.pid)
    assert events == ["console", "job"]


@pytest.mark.windows_only(reason="exercises the ConPTY release on exit")
@pytest.mark.asyncio
async def test_wait_releases_the_pseudo_console_once_the_child_exits():
    # pywinpty's cancel_io already tears an idle console down, so the escape
    # test above cannot tell; this one sees the release itself.
    released = threading.Event()
    console = _FakeConPty()
    console.alive = False
    weakref.finalize(console, released.set)
    terminal = pty._WindowsPty(console)
    del console
    terminal.start(lambda chunk: None)
    assert await asyncio.wait_for(terminal.wait(), 10) == 3
    assert await asyncio.to_thread(released.wait, 5)
    assert await _conpty_threads_gone(terminal.pid)
    terminal.close()  # idempotent


@pytest.mark.windows_only(reason="exercises the ConPTY reader bound")
@pytest.mark.asyncio
async def test_a_reader_stuck_past_the_bound_is_logged_as_a_leak(monkeypatch):
    warnings: list[tuple[str, dict]] = []

    class _Log:
        def warning(self, msg: str, **fields) -> None:
            warnings.append((msg, fields))

    monkeypatch.setattr(pty, "_log", _Log(), raising=False)
    monkeypatch.setattr(pty, "_READER_STOP_SECONDS", 0.2)
    console = _FakeConPty()
    stuck = threading.Event()

    def blocked_read(blocking: bool = False) -> str:
        stuck.set()
        console.release.wait(10)  # cancel_io cannot reach this read
        return ""

    console.read = blocked_read
    terminal = pty._WindowsPty(console)
    terminal.start(lambda chunk: None)
    try:
        await asyncio.to_thread(stuck.wait, 5)
        terminal.close()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5
        while not warnings and loop.time() < deadline:
            await asyncio.sleep(0.05)
        assert warnings == [("conpty_reader_leaked", {"pid": terminal.pid})]
    finally:
        console.release.set()
    assert await _conpty_threads_gone(terminal.pid)


ATTACH_CHILD = r'''
import asyncio
import os

from cc_remote.platform import pty


def raw_state():
    if os.name == "nt":
        import ctypes
        import msvcrt

        mode = ctypes.c_uint32()
        ctypes.WinDLL("kernel32").GetConsoleMode(msvcrt.get_osfhandle(0), ctypes.byref(mode))
        return int(not mode.value & 0x2)  # ENABLE_LINE_INPUT
    import termios

    return int(not termios.tcgetattr(0)[3] & termios.ICANON)


async def main():
    os.write(1, b"BEFORE raw=%d\n" % raw_state())
    resized = asyncio.Event()
    with pty.RawTerminal(0, 1, keyboard=True):
        rows, cols, _x, _y = pty.terminal_size(0)
        os.write(1, b"RAW raw=%d size=%dx%d\n" % (raw_state(), rows, cols))
        unwatch = pty.watch_resize(0, resized.set)
        reader = pty.InputReader(0)
        received = b""
        while not received.endswith(b"\r"):
            chunk = await reader.read()
            if not chunk:
                break
            received += chunk
        os.write(1, b"GOT " + received.hex().encode() + b"\n")
        await asyncio.wait_for(resized.wait(), 10)
        rows, cols, _x, _y = pty.terminal_size(0)
        os.write(1, b"RESIZED size=%dx%d\n" % (rows, cols))
        unwatch()
    os.write(1, b"AFTER raw=%d\n" % raw_state())


asyncio.run(main())
'''


@pytest.mark.asyncio
async def test_raw_terminal_input_and_resize_inside_a_real_terminal(tmp_path):
    script = tmp_path / "attach_child.py"
    script.write_text(ATTACH_CHILD, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    # The size must come from the terminal, not an inherited override.
    env.pop("COLUMNS", None)
    env.pop("LINES", None)
    output = _Collector()
    terminal = pty.spawn([sys.executable, str(script)], cwd=str(tmp_path), env=env)
    try:
        terminal.start(output)
        await output.until(lambda data: b"RAW raw=1 size=24x80" in data)
        # ^Z alone and mid-chunk, NUL (Ctrl+Space) and an astral emoji are keys too.
        for chunk in (b"a", "中".encode(), b"\x03", b"\x1b[A", b"\x1a", b"x\x1ay", b"\x00",
                      "😀".encode(), b"\r"):
            await terminal.write(chunk)
        await output.until(lambda data: re.search(rb"GOT [0-9a-f]*\r?\n", data) is not None)
        assert re.search(rb"GOT ([0-9a-f]*)", output.data).group(1) == (
            b"61" b"e4b8ad" b"03" b"1b5b41" b"1a" b"781a79" b"00" b"f09f9880" b"0d")
        terminal.resize(30, 100)
        await output.until(lambda data: b"RESIZED size=30x100" in data)
        assert await asyncio.wait_for(terminal.wait(), 10) == 0
        data = bytes(output.data) + b"".join(terminal.drain())
    finally:
        terminal.close()
    assert b"BEFORE raw=0" in data
    assert b"AFTER raw=0" in data


CLOSE_CHILD = r'''
import asyncio
import os

from cc_remote.platform import pty


async def main():
    with pty.RawTerminal(0, 1, keyboard=True):
        reader = pty.InputReader(0)
        os.write(1, b"RAW\n")
        first = await reader.read()
        getattr(reader, "close", lambda: None)()
    os.write(1, b"FIRST " + first.hex().encode() + b" CLOSED\n")
    line = os.read(0, 64)
    os.write(1, b"COOKED " + line.hex().encode() + b"\n")


asyncio.run(main())
'''


@pytest.mark.asyncio
async def test_a_closed_reader_leaves_the_next_keys_to_the_console(tmp_path):
    script = tmp_path / "close_child.py"
    script.write_text(CLOSE_CHILD, encoding="utf-8")
    output = _Collector()
    terminal = pty.spawn([sys.executable, str(script)], cwd=str(tmp_path),
                         env={**os.environ, "PYTHONPATH": str(ROOT)})
    try:
        terminal.start(output)
        await output.until(lambda data: b"RAW" in data)
        await terminal.write(b"a")
        await output.until(lambda data: b"FIRST 61 CLOSED" in data)
        for chunk in (b"x", b"y", b"\r"):
            await terminal.write(chunk)
        await output.until(lambda data: re.search(rb"COOKED [0-9a-f]+\r?\n", data) is not None)
        assert await asyncio.wait_for(terminal.wait(), 10) == 0
    finally:
        terminal.close()
    # The whole line, x included: no read of the closed reader was left behind.
    expected = b"78790d0a" if IS_WINDOWS else b"78790a"
    assert re.search(rb"COOKED ([0-9a-f]+)", output.data).group(1) == expected


@pytest.mark.asyncio
async def test_input_reader_and_raw_terminal_accept_pipes():
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"piped")
        with pty.RawTerminal(read_fd, write_fd, keyboard=True):
            os.close(write_fd)
            write_fd = -1
            reader = pty.InputReader(read_fd)
            assert await asyncio.wait_for(reader.read(), 5) == b"piped"
            assert await asyncio.wait_for(reader.read(), 5) == b""
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        os.close(read_fd)


@pytest.mark.posix_only(reason="POSIX reads the size from the tty itself")
def test_terminal_size_defaults_off_a_terminal(tmp_path):
    with open(tmp_path / "plain", "wb") as stream:
        assert pty.terminal_size(stream.fileno()) == (24, 80, 0, 0)


@pytest.mark.windows_only(reason="Windows reads the size through shutil (stdout or COLUMNS/LINES)")
def test_terminal_size_honours_columns_and_lines(monkeypatch):
    monkeypatch.setenv("COLUMNS", "91")
    monkeypatch.setenv("LINES", "33")
    assert pty.terminal_size(0) == (33, 91, 0, 0)


def test_detached_servers_leave_the_launching_console():
    import subprocess

    breakaway = subprocess.CREATE_BREAKAWAY_FROM_JOB if IS_WINDOWS else 0
    expected = subprocess.CREATE_NO_WINDOW | breakaway if IS_WINDOWS else 0
    assert pty.DETACHED_CREATIONFLAGS == expected
    assert pty.JOB_BREAKAWAY_CREATIONFLAG == breakaway


@pytest.mark.windows_only(reason="console windows are a Windows concept")
def test_detached_server_never_gets_a_console_window(tmp_path):
    import subprocess
    import sys

    from cc_remote.platform import procs

    # A venv python.exe is a launcher that re-spawns the real interpreter; a
    # DETACHED_PROCESS launcher would hand that child a fresh, visible console.
    report = tmp_path / "window.txt"
    code = ("import ctypes, sys; open(sys.argv[1], 'w').write("
            "str(ctypes.windll.kernel32.GetConsoleWindow() or 0))")
    child = procs.popen_group(
        [sys.executable, "-c", code, str(report)], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
        creationflags=pty.DETACHED_CREATIONFLAGS)
    assert child.wait(30) == 0
    assert report.read_text() == "0"


_CONSOLE_PIDS = """
import ctypes, json, os, subprocess, sys
from cc_remote.platform import procs, pty
pids = (ctypes.c_uint32 * 64)()
count = ctypes.windll.kernel32.GetConsoleProcessList(pids, 64)
with open(sys.argv[1], "w") as report:
    json.dump({"pid": os.getpid(), "console": list(pids[:count])}, report)
"""


@pytest.mark.windows_only(reason="consoles are a Windows concept")
def test_a_detached_server_is_not_attached_to_the_launching_console(tmp_path):
    import subprocess
    import sys

    # The launcher owns a (hidden) console of its own, like a user's terminal.
    launcher_report = tmp_path / "launcher.json"
    child_report = tmp_path / "child.json"
    launcher_code = _CONSOLE_PIDS + (
        "child = procs.popen_group([sys.executable, '-c', sys.argv[3], sys.argv[2]],"
        " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,"
        " close_fds=True, creationflags=pty.DETACHED_CREATIONFLAGS)"
        "; sys.exit(child.wait(30))")
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0  # SW_HIDE
    launcher = subprocess.run(
        [sys.executable, "-c", launcher_code, str(launcher_report), str(child_report),
         _CONSOLE_PIDS],
        cwd=ROOT, creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup,
        timeout=60, check=False)
    assert launcher.returncode == 0
    launched = json.loads(launcher_report.read_text())
    child = json.loads(child_report.read_text())
    assert launched["pid"] in launched["console"]  # the launcher had a console
    assert child["pid"] in child["console"]  # and so does the server: its own
    assert launched["pid"] not in child["console"]


class _FakeConsole:
    """Console modes and the output code page behind fake handles 0 (in) / 1 (out)."""

    INPUT = 0x01F7  # processed, line, echo, window, mouse, insert, quick edit
    OUTPUT = 0x0003
    CODE_PAGE = 437

    def __init__(self, monkeypatch, *, fail_set: str | None = None,
                 fail_restore: str | None = None) -> None:
        self.modes = {"in": self.INPUT, "out": self.OUTPUT}
        self.code_page = self.CODE_PAGE
        self._fail_set = fail_set
        self._fail_restore = fail_restore
        monkeypatch.setattr(pty, "_console_handle", lambda fd: {0: "in", 1: "out"}.get(fd))
        monkeypatch.setattr(pty, "_console_mode", lambda handle: self.modes[handle])
        monkeypatch.setattr(pty, "_set_console_mode", self._set_mode)
        monkeypatch.setattr(pty, "_console_output_cp", lambda: self.code_page)
        monkeypatch.setattr(pty, "_set_console_output_cp", self._set_code_page)

    def _set_mode(self, handle: str, mode: int) -> None:
        original = self.INPUT if handle == "in" else self.OUTPUT
        if handle == self._fail_set and mode != original:
            raise PermissionError("SetConsoleMode denied")
        if handle == self._fail_restore and mode == original:
            raise OSError("SetConsoleMode failed")
        self.modes[handle] = mode

    def _set_code_page(self, code_page: int) -> None:
        self.code_page = code_page

    def assert_restored(self) -> None:
        assert self.modes == {"in": self.INPUT, "out": self.OUTPUT}
        assert self.code_page == self.CODE_PAGE


class _Escape(Exception):
    pass


@pytest.mark.windows_only(reason="exercises the Windows console modes")
@pytest.mark.parametrize("escape", [None, _Escape, asyncio.CancelledError, KeyboardInterrupt, SystemExit])
def test_raw_console_modes_are_restored_on_every_exit(monkeypatch, escape):
    console = _FakeConsole(monkeypatch)
    try:
        with pty.RawTerminal(0, 1, keyboard=True):
            assert console.modes["in"] == (_FakeConsole.INPUT & ~0x7) | 0x200
            assert console.modes["out"] == _FakeConsole.OUTPUT | 0x4 | 0x8
            assert console.code_page == 65001
            if escape is not None:
                raise escape()
    except BaseException as exc:
        assert escape is not None and isinstance(exc, escape)
    else:
        assert escape is None
    console.assert_restored()


@pytest.mark.windows_only(reason="exercises the Windows console modes")
def test_a_failed_raw_switch_restores_what_was_already_changed(monkeypatch):
    console = _FakeConsole(monkeypatch, fail_set="in")
    with pytest.raises(PermissionError):
        with pty.RawTerminal(0, 1, keyboard=True):
            pytest.fail("entered despite the failed input mode")
    console.assert_restored()


@pytest.mark.windows_only(reason="exercises the Windows console modes")
def test_one_failed_restore_does_not_skip_the_others(monkeypatch):
    console = _FakeConsole(monkeypatch, fail_restore="in")
    with pty.RawTerminal(0, 1, keyboard=True):
        pass
    assert console.modes["out"] == _FakeConsole.OUTPUT
    assert console.code_page == _FakeConsole.CODE_PAGE


@pytest.mark.windows_only(reason="exercises the Windows console modes")
def test_a_mirror_leaves_the_keyboard_alone(monkeypatch):
    console = _FakeConsole(monkeypatch)
    with pty.RawTerminal(0, 1, keyboard=False):
        assert console.modes["in"] == _FakeConsole.INPUT
        assert console.modes["out"] == _FakeConsole.OUTPUT | 0x4 | 0x8
    console.assert_restored()


@pytest.mark.windows_only(reason="exercises the Windows console modes")
@pytest.mark.parametrize("handle", ["out", "in"])
def test_an_interrupt_right_after_a_mode_change_still_restores_it(monkeypatch, handle):
    console = _FakeConsole(monkeypatch)
    set_mode = console._set_mode

    def interrupted(target: str, mode: int) -> None:
        set_mode(target, mode)
        if target == handle and mode != (_FakeConsole.INPUT if target == "in" else _FakeConsole.OUTPUT):
            monkeypatch.setattr(pty, "_set_console_mode", set_mode)
            raise KeyboardInterrupt  # lands after the change, before the next statement

    monkeypatch.setattr(pty, "_set_console_mode", interrupted)
    with pytest.raises(KeyboardInterrupt):
        with pty.RawTerminal(0, 1, keyboard=True):
            pytest.fail("entered despite the interrupt")
    console.assert_restored()


@pytest.mark.posix_only(reason="exercises termios raw mode")
@pytest.mark.parametrize("escape", [None, _Escape, asyncio.CancelledError, KeyboardInterrupt])
def test_termios_is_restored_on_every_exit(monkeypatch, escape):
    import termios

    calls: list[tuple] = []
    monkeypatch.setattr(pty.os, "isatty", lambda fd: fd == 0)
    monkeypatch.setattr(pty.termios, "tcgetattr", lambda fd: ["original", fd])
    monkeypatch.setattr(pty.tty, "setraw", lambda fd: calls.append(("setraw", fd)))
    monkeypatch.setattr(pty.termios, "tcsetattr", lambda fd, when, attrs: calls.append(
        ("tcsetattr", fd, when, attrs)))
    try:
        with pty.RawTerminal(0, 1, keyboard=True):
            assert calls == [("setraw", 0)]
            if escape is not None:
                raise escape()
    except BaseException as exc:
        assert escape is not None and isinstance(exc, escape)
    assert calls == [("setraw", 0), ("tcsetattr", 0, termios.TCSAFLUSH, ["original", 0])]


@pytest.mark.windows_only(reason="Ctrl+Break is a Windows console signal")
@pytest.mark.asyncio
async def test_a_watched_console_break_runs_the_callback_instead_of_exiting():
    import signal

    before = signal.getsignal(signal.SIGBREAK)
    broken = asyncio.Event()
    unwatch = pty.watch_break(broken.set)
    try:
        signal.raise_signal(signal.SIGBREAK)  # the default handler would end pytest
        await asyncio.wait_for(broken.wait(), 5)
    finally:
        unwatch()
    assert signal.getsignal(signal.SIGBREAK) == before


@pytest.mark.posix_only(reason="POSIX raw mode clears ISIG: no key signals")
@pytest.mark.asyncio
async def test_watch_break_leaves_posix_signals_alone():
    import signal

    before = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    unwatch = pty.watch_break(lambda: None)
    assert {signum: signal.getsignal(signum) for signum in before} == before
    unwatch()


@pytest.mark.windows_only(reason="Windows has no SIGWINCH and polls the size")
@pytest.mark.asyncio
async def test_watch_resize_polls_until_unwatched(monkeypatch):
    sizes = [(24, 80, 0, 0)]
    monkeypatch.setattr(pty, "terminal_size", lambda fd: sizes[-1])
    monkeypatch.setattr(pty, "_RESIZE_POLL_SECONDS", 0.01)
    resized = asyncio.Event()
    unwatch = pty.watch_resize(0, resized.set)
    try:
        await asyncio.sleep(0.05)
        assert not resized.is_set()
        sizes.append((30, 100, 0, 0))
        await asyncio.wait_for(resized.wait(), 5)
    finally:
        unwatch()
    resized.clear()
    sizes.append((40, 120, 0, 0))
    await asyncio.sleep(0.05)
    assert not resized.is_set()


@pytest.mark.windows_only(reason="Windows has no SIGWINCH and polls the size")
@pytest.mark.asyncio
async def test_a_failing_resize_callback_is_logged_and_polling_goes_on(monkeypatch, caplog):
    sizes = [(24, 80, 0, 0)]
    monkeypatch.setattr(pty, "terminal_size", lambda fd: sizes[-1])
    monkeypatch.setattr(pty, "_RESIZE_POLL_SECONDS", 0.01)
    calls: list[tuple[int, int, int, int]] = []
    resized = asyncio.Event()

    def callback() -> None:
        calls.append(sizes[-1])
        if len(calls) == 1:
            raise RuntimeError("resize handler failed")
        resized.set()

    unwatch = pty.watch_resize(0, callback)
    try:
        await asyncio.sleep(0.05)  # the poller has taken its first size
        sizes.append((30, 100, 0, 0))
        async with asyncio.timeout(5):
            while not calls:
                await asyncio.sleep(0.01)
        sizes.append((40, 120, 0, 0))
        await asyncio.wait_for(resized.wait(), 5)
    finally:
        unwatch()
    assert calls == [(30, 100, 0, 0), (40, 120, 0, 0)]
    assert any("resize_callback_failed" in record.getMessage() and record.exc_info
               for record in caplog.records)


@pytest.mark.windows_only(reason="the Windows reader is a thread")
@pytest.mark.asyncio
async def test_the_input_thread_ends_at_eof():
    read_fd, write_fd = os.pipe()
    try:
        os.close(write_fd)
        reader = pty.InputReader(read_fd)
        assert await asyncio.wait_for(reader.read(), 5) == b""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5
        while any(thread.name == "stdin-reader" for thread in threading.enumerate()):
            assert loop.time() < deadline, "stdin reader thread outlived EOF"
            await asyncio.sleep(0.02)
    finally:
        os.close(read_fd)


def _key(char: str, *, down: bool = True, repeat: int = 1, vk: int = 0,
         state: int = 0) -> tuple[bool, int, int, int, int]:
    """A console KEY_EVENT as ``(down, repeat, virtual key, UTF-16 unit, control state)``."""
    return down, repeat, vk, ord(char), state


class _FakeConsoleInput:
    """Console input on fake handle "in" (fd 0): batches of key records, or an error."""

    def __init__(self, monkeypatch, batches) -> None:
        self.batches = list(batches)
        self.reads = 0
        monkeypatch.setattr(pty, "_console_handle", lambda fd: "in" if fd == 0 else None)
        monkeypatch.setattr(pty, "_wait_console_input", self._wait)
        monkeypatch.setattr(pty, "_read_console_input", self._read)

    def _wait(self, handle: str, timeout_ms: int) -> bool:
        assert handle == "in"
        time.sleep(0.005)
        return True

    def _read(self, handle: str):
        assert handle == "in"
        self.reads += 1
        batch = self.batches.pop(0) if self.batches else []
        if isinstance(batch, BaseException):
            raise batch
        return batch


def _new_reader_threads(before: set[threading.Thread]) -> list[threading.Thread]:
    return [thread for thread in threading.enumerate()
            if thread.name == "stdin-reader" and thread not in before]


@pytest.mark.windows_only(reason="exercises the Windows console reader")
@pytest.mark.asyncio
async def test_console_keys_arrive_as_utf8_and_only_an_error_is_eof(monkeypatch):
    console = _FakeConsoleInput(monkeypatch, [
        [_key("a"), _key("\x1a")],                                   # ^Z is a key, not EOF
        [_key("x"), _key("\x1a"), _key("y")],
        [_key("\ud83d")],                                            # 😀 split across two reads
        [_key("\ude00"), _key("\u4e2d")],
        [_key("b", repeat=3), _key("b", down=False, vk=0x42)],        # a held key, its key-up
        [_key("\0", vk=0x10), _key("\0", down=False, vk=0x26)],      # Shift alone, Up's key-up
        [_key("\0", vk=0x12, state=0x2), _key("\u00e9", down=False, vk=0x12)],  # Alt+numpad
        [_key("\0", vk=0x20, state=0x8), _key("\0", vk=0x32, state=0x18)],      # Ctrl+Space, Ctrl+@
        OSError(6, "The handle is invalid"),
    ])
    reader = pty.InputReader(0)
    chunks = []
    while True:
        chunk = await asyncio.wait_for(reader.read(), 5)
        chunks.append(chunk)
        if not chunk:
            break
    assert chunks == [b"a\x1a", b"x\x1ay", "😀中".encode(), b"bbb", "é".encode(), b"\0\0", b""]
    assert console.batches == []


@pytest.mark.windows_only(reason="exercises the Windows console reader")
@pytest.mark.asyncio
async def test_close_leaves_no_console_read_behind(monkeypatch):
    console = _FakeConsoleInput(monkeypatch, [[_key("a")]])
    before = set(threading.enumerate())
    reader = pty.InputReader(0)
    assert await asyncio.wait_for(reader.read(), 5) == b"a"
    [thread] = _new_reader_threads(before)
    reader.close()
    reads = console.reads
    console.batches.append([_key("x")])
    await asyncio.sleep(0.2)
    assert console.reads == reads, "the console was read after close()"
    thread.join(5)
    assert not thread.is_alive()
    assert reader._queue.empty()
    reader.close()  # idempotent
    assert await asyncio.wait_for(reader.read(), 5) == b""


@pytest.mark.windows_only(reason="the Windows reader is a thread")
@pytest.mark.parametrize("source", ["console", "pipe"])
def test_the_input_thread_ends_when_its_loop_is_closed(monkeypatch, source):
    before = set(threading.enumerate())
    read_fd, write_fd = os.pipe()
    try:
        if source == "console":
            console = _FakeConsoleInput(monkeypatch, [])
            reader = pty.InputReader(0)
        else:
            reader = pty.InputReader(read_fd)
        loop = asyncio.new_event_loop()
        task = loop.create_task(reader.read())
        loop.run_until_complete(asyncio.sleep(0.1))
        [thread] = _new_reader_threads(before)
        task.cancel()
        loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
        loop.close()
        if source == "console":
            console.batches.append([_key("x")])
        else:
            os.write(write_fd, b"late")
        thread.join(5)
        assert not thread.is_alive(), "the reader outlived its closed loop"
    finally:
        os.close(write_fd)
        os.close(read_fd)
