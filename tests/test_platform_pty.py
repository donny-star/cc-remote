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
