"""cc_remote.platform.pty: pseudo-terminals for the broker and claude-remote."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
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
