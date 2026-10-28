# Windows 适配 · 子项目 3b：ConPTY、Claude broker 与 claude-remote Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `cc_remote/platform/pty.py`（POSIX = 原 openpty/setsid/TIOCSCTTY；Windows = pywinpty ConPTY），
把 `cc_remote/claude_broker/**` 与 `claude-remote` 迁到 `pty` + `ipc` 上，使 broker 在 Windows 上能托管官方
Claude TUI、`claude-remote` 能以原始模式附着。

**Architecture:** `pty.py` 两部分：(1) 宿主侧 `PtyProcess`（`spawn`/`start`/`write`/`resize`/`wait`/`drain`/
`kill_group`/`close`），POSIX 分支是从 `claude_broker/session.py` 原样搬来的代码，Windows 分支用 pywinpty
`PTY(backend=ConPTY)`：后台线程阻塞读、`call_soon_threadsafe` 交回事件循环，退出后等输出静默 100 ms（最多 1 s）
让 ConPTY 渲染完最后一帧，再反复 `cancel_io` 直到读线程确认退出；子进程启动后立即放进独立 Job 供 `kill_group`
整树终止，子进程一退出（或 `close()`）就在后台线程里释放伪控制台，结束漏在 Job 外、仍挂在该控制台上的后代；
(2) 附着侧 `RawTerminal`（termios / `SetConsoleMode` VT 输入输出 + UTF-8 输出代码页，退出时恢复）、
`terminal_size`、`watch_resize`（SIGWINCH / 250 ms 轮询）、`InputReader`（`add_reader` / 读线程 +
`ReadConsoleW`）。broker 的 server/client 改用 3a 的 `ipc`，control store 改用 `perms`/`files`。

**Tech Stack:** Python 3.13、pywinpty 3.1.0（ConPTY）、pywin32 312（`win32job`）、ctypes（控制台模式）、
asyncio、pytest 9 + pytest-asyncio。

**Spec:** `docs/superpowers/specs/2026-10-27-windows-compat-design.md`（§5 PTY 为准，另见 §4、§3.1、§7、§9）。
另读 `docs/superpowers/plans/2026-10-27-windows-compat-p2-p3-coordination.md`、
`docs/superpowers/plans/2026-10-27-windows-compat-p1-handoff.md`、
`docs/superpowers/plans/2026-10-27-windows-compat-p3a-ipc.md`（本计划使用其 `ipc` 接口）。

## Global Constraints

协作约定 §3 的共同约束（逐字）：

- POSIX 行为完全不变；Linux CI（fork 内 PR 自动跑 ubuntu-24.04）不能出现新失败。
- 只能通过 `cc_remote/platform/` 的 API 处理平台差异，业务代码里不新增散落的 `sys.platform` 分支。
- 不使用 Python 3.11+ 独有的语法；锁文件仍按 `--python-version 3.10 --universal --generate-hashes` 生成。
- Windows 上测试用 `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest <files> -p no:xdist -o addopts="" -q`；
  不要在一个进程里跑全量测试，按文件逐个跑，每个文件 240 s 超时。
- 每个任务的验收：任务所列的基线用例（`docs/superpowers/windows-baseline.json` 里的
  nodeid，或 root_cause_key + 文件）在 Windows 上通过；受影响测试文件里没有新失败；
  对应 PR 的 Linux CI 通过。
- 提交信息以空行加 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` 结尾；
  PR 描述以空行加 `🤖 Generated with [Claude Code](https://claude.com/claude-code)` 结尾。
- PR 一律开在 donny-star/cc-remote 内部，目标分支为 `windows-compat-v2`；不向上游提 PR。

子项目 3 追加的约束：

- 文件归属：P3 在 `cc_remote/platform/` 下只新建 `ipc.py`（3a）和 `pty.py`（本计划）；不改其它平台文件、
  `tests/conftest.py`、`pytest.ini`、`machine.py`、`config.py`。
- 新依赖只加 `pywinpty==3.1.0 ; sys_platform == "win32"`（`requirements.txt` 与 `requirements-wrapper.txt`；
  relay 不需要）。锁文件按各自文件头里的命令重新生成；pypi.org TLS 失败时加
  `--index-url https://pypi.tuna.tsinghua.edu.cn/simple`，但生成结果里不能出现任何 index URL。
- 平台层对外只抛 `OSError` 家族；`winpty.WinptyError` 不得漏出 `pty.py`。
- 提交信息：英文 Conventional Commit 主题，多行用 `git commit -F <file>`。

## 前置条件

1. P2 阶段 A 已合入（同 3a 的前置检查）。
2. 3a 的 PR 已合入 `windows-compat-v2`，本分支从它切出：

```bash
cd /c/Users/xuhaodong/work/cc-remote-p3
git fetch origin
git switch -c win/3b-broker origin/windows-compat-v2
.venv/Scripts/python.exe -c "from cc_remote.platform import ipc; print(ipc.start_server, ipc.open_connection)"
```

Expected：打印两个函数对象。否则停下，报告"3a 尚未合入"。

## 写计划时的实测（Win10 19044，Python 3.13.15，pywinpty 3.1.0）

- `winpty.PTY(cols, rows, backend=ConPTY)`：`spawn(appname, cmdline=" "+list2cmdline(args), cwd, env="k=v\0…\0")`
  会把 `appname + cmdline` 拼成命令行；路径含空格、空参数、引号、Unicode 参数都原样到达子进程 `sys.argv`。
- `read()` 返回 `str`（UTF-8 解码，测试 8 万个多字节字符 0 个 U+FFFD）；`write(str)` 返回值恒为 0，
  但 200 KB 数据完整到达。
- 子进程退出后阻塞的 `read(blocking=True)` 不会返回，`cancel_io()` 可令其抛 `WinptyError`；退出后 0–100 ms 内
  最后的输出全部可读。
- 释放 `PTY` 对象（ClosePseudoConsole）会结束挂在该控制台上的进程。
- ConPTY 会先输出 `ESC[1t ESC[c ESC[?1004h ESC[?9001h`；它自己吞掉焦点报告 `ESC[O`/`ESC[I`，其余输入字节
  （`\x03`、`\r`、`ESC[Z`、`\x7f`、方向键）原样到达处于原始模式的子进程；子进程写出的 SGR 原样透传。
- 启动后立即 `AssignProcessToJobObject`：venv `python.exe` 启动器拉起的真实解释器 10/10 次都在 Job 里。
- 附着侧：清掉 `ENABLE_PROCESSED_INPUT|LINE|ECHO`、置 `ENABLE_VIRTUAL_TERMINAL_INPUT` 后，`_WindowsConsoleIO.read`
  逐键返回 UTF-8（`a`→`61`，`中`→`e4b8ad`，Ctrl-C→`03`，↑→`1b5b41`）；输出侧置 VT 处理 + 65001 代码页；退出后
  模式与代码页原样恢复。控制台输入句柄没有尺寸，`os.get_terminal_size(0)` 恒为默认值，必须用
  `shutil.get_terminal_size()`（stdout 屏幕缓冲区）——这正是规范 §5 的要求。

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `requirements.txt`、`requirements-wrapper.txt`、`requirements.lock`、`requirements-wrapper.lock` | 修改 | pywinpty 依赖 |
| `cc_remote/platform/pty.py` | 新建 | 宿主侧 `PtyProcess`/`spawn`；附着侧 `RawTerminal`/`terminal_size`/`watch_resize`/`InputReader` |
| `tests/test_platform_pty.py` | 新建 | 依赖、宿主侧、附着侧测试 |
| `cc_remote/claude_broker/session.py` | 修改 | PTY 生命周期改走 `pty.PtyProcess` |
| `cc_remote/claude_broker/control_store.py` | 修改 | 私有判定用 `perms`，替换用 `files.replace` |
| `cc_remote/claude_broker/server.py` | 修改 | 监听与守门改走 `ipc`；删除 `_peer_uid`/`_remove_stale_socket` |
| `cc_remote/claude_broker/client.py` | 修改 | 连接改走 `ipc.open_connection` |
| `cc_remote/claude_broker/cli.py` | 修改 | 附着侧改走 `pty`；`serve` 用 `signals`；自动拉起 broker 用 `procs` + `ipc` |
| `tests/test_claude_broker.py` | 修改 | 可移植的假 Claude、`SHORT_TMP`、私有判定、平台专有用例标记 |

`cc_remote/wrapper/claude_broker_handle.py` 只用 `BrokerClient`/`BrokerClientError`，接口不变，本计划不改它
（`tests/test_claude_broker_handle.py` 作为回归跑）。

## `pty` 接口总览

```python
READ_CHUNK_BYTES: int = 64 * 1024

class PtyProcess:
    pid: int
    start_ticks: int            # POSIX: /proc starttime or monotonic_ns; Windows: creation FILETIME
    def start(self, on_output: Callable[[bytes], None]) -> None   # deliver output on the running loop
    def stop_reading(self) -> None
    def poll(self) -> int | None
    async def wait(self) -> int        # exit status; Windows also waits for the last frame,
                                       # then releases the pseudo console (close() is idempotent)
    def drain(self) -> list[bytes]     # bytes still buffered after wait() (always [] on Windows)
    async def write(self, data: bytes) -> None          # all bytes or OSError
    def resize(self, rows: int, cols: int, xpixel: int = 0, ypixel: int = 0) -> None   # OSError
    def kill_group(self, sig: int) -> None              # ProcessLookupError once the tree is gone
    def close(self) -> None                             # never blocks the loop

def spawn(argv: Sequence[str], *, cwd: str, env: dict[str, str],
          rows: int = 24, cols: int = 80) -> PtyProcess          # OSError on failure

class RawTerminal:  # context manager
    def __init__(self, stdin_fd: int, stdout_fd: int, *, keyboard: bool)
def terminal_size(fd: int) -> tuple[int, int, int, int]        # rows, cols, xpixel, ypixel
def watch_resize(fd: int, callback: Callable[[], None]) -> Callable[[], None]   # returns unwatch
class InputReader:
    def __init__(self, fd: int)
    async def read(self) -> bytes      # b"" at EOF
DETACHED_CREATIONFLAGS: int   # Windows CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB, POSIX 0: auto-started servers leave the caller's console and Job
JOB_BREAKAWAY_CREATIONFLAG: int   # Windows CREATE_BREAKAWAY_FROM_JOB, POSIX 0: dropped on retry when the Job forbids breakaway
def watch_break(callback: Callable[[], None]) -> Callable[[], None]   # Windows Ctrl+Break during an attach; POSIX no-op
```

---

### Task 1: pywinpty 依赖

**Files:**
- Modify: `requirements.txt`、`requirements-wrapper.txt`（各追加一行）
- Modify: `requirements.lock`、`requirements-wrapper.lock`（重新生成）
- Create: `tests/test_platform_pty.py`

**Interfaces:**
- Consumes: 无。
- Produces: Windows 上可 `import winpty`（`winpty.PTY`、`winpty.Backend.ConPTY == 0`、`winpty.WinptyError`）。

- [ ] **Step 1: 写失败的测试**

`tests/test_platform_pty.py`：

```python
"""cc_remote.platform.pty: pseudo-terminals for the broker and claude-remote."""
from __future__ import annotations

from pathlib import Path

import pytest

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
```

- [ ] **Step 2: 运行，确认失败**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_pty.py -p no:xdist -o addopts="" -q`
Expected: `test_pywinpty_is_pinned_for_windows_wrappers_only` FAIL（`assert ... in ...`）。

- [ ] **Step 3: 加依赖并重新生成锁文件**

```bash
printf 'pywinpty==3.1.0 ; sys_platform == "win32"\n' >> requirements.txt
printf 'pywinpty==3.1.0 ; sys_platform == "win32"\n' >> requirements-wrapper.txt
uv pip compile requirements.txt --universal --generate-hashes --python-version 3.10 --output-file requirements.lock
uv pip compile requirements-wrapper.txt --constraint requirements.lock --universal --generate-hashes --python-version 3.10 --output-file requirements-wrapper.lock
uv pip compile requirements-relay.txt --constraint requirements.lock --universal --generate-hashes --python-version 3.10 --output-file requirements-relay.lock
git diff --stat
git diff --exit-code -- requirements-relay.txt requirements-relay.lock && echo "relay unchanged"
grep -c -E "index-url|tuna|--find-links" requirements.lock requirements-wrapper.lock requirements-relay.lock
uv pip install --python .venv/Scripts/python.exe "pywinpty==3.1.0"
```

（pypi.org TLS 失败时三条 `compile` 都加 `--index-url https://pypi.tuna.tsinghua.edu.cn/simple`；
uv 不会把它写进输出。）

Expected：`git diff --stat` 只列出 4 个文件，`requirements.lock` +19 行、`requirements-wrapper.lock` +21 行
（写计划时实测如此，评审时在副本里用同样三条命令复现过）；打印 `relay unchanged`；`grep -c` 三个文件都是 `0`（命令
退出码 1 是 grep 的"没有匹配"，属预期）。`pywinpty==3.1.0 ; sys_platform == 'win32'` 段落带 17 个 `--hash=sha256:`。
锁文件头里的命令行必须与上面三条逐字相同（uv 不把 `--index-url` 写进文件头）。

- [ ] **Step 4: 运行，确认通过**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_pty.py tests/test_engine_versions.py tests/test_release_distribution.py -p no:xdist -o addopts="" -q`
Expected: `test_platform_pty.py` 2 passed；另两个文件的结果与基线一致（基线里它们的失败条目都不归子项目 3，
数量不增加）。

- [ ] **Step 5: 提交**

```bash
git add requirements.txt requirements-wrapper.txt requirements.lock requirements-wrapper.lock tests/test_platform_pty.py
cat > /tmp/msg-p3b-1.txt <<'EOF'
build(deps): pin pywinpty for Windows wrappers

- Add pywinpty 3.1.0 (ConPTY) for win32 only to the full and wrapper requirements
- Regenerate hashed locks; the relay lock is unchanged

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg-p3b-1.txt
```

---

### Task 2: `pty` 宿主侧 —— `PtyProcess`、`spawn` 与 ConPTY 基本生命周期

**Files:**
- Create: `cc_remote/platform/pty.py`
- Modify: `tests/test_platform_pty.py`

**Interfaces:**
- Consumes: `procs.killpg(pid, sig)`（POSIX）；`executables.command_for(path) -> list[str]`；
  `win32_process.creation_time(pid) -> int | None`；`_win32err.translated()`；pywinpty。
- Produces: `READ_CHUNK_BYTES`、`PtyProcess`、`spawn(...)`（见接口总览）。POSIX 后端的语义与原
  `PTYSession.spawn/_on_readable/_wait_for_exit/_write_bytes/_wait_writable/resize/stop/close` 逐行一致。
  本任务的 Windows `kill_group` 只结束被托管的子进程本身；整棵进程树（per-child Job）与"子进程退出即释放伪控制台"
  在 Task 3 补上。pywinpty 的四个坑在这里处理并各有用例：阻塞读在子进程退出后不返回（`_stop_reader`/`_dispose`
  反复 `cancel_io` 直到读线程确认退出）、`write()` 恒返回 0（不看返回值，100 KB 用例证明完整送达）、只收发 `str`
  （输入按 UTF-8 增量解码、输出按 UTF-8 编码，拆开的多字节字符用例）、ConPTY 吞掉焦点报告（专门用例记录该行为）。

- [ ] **Step 1: 写失败的测试**

`tests/test_platform_pty.py` 的 import 区改为：

```python
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
```

文件末尾追加：

```python
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
```

- [ ] **Step 2: 运行，确认失败**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_pty.py -p no:xdist -o addopts="" -q`
Expected: 收集错误 `ImportError: cannot import name 'pty' from 'cc_remote.platform'`。

- [ ] **Step 3: 实现**

`cc_remote/platform/pty.py`（本任务只写宿主侧；Task 3 追加附着侧）：

```python
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
```

- [ ] **Step 4: 运行，确认通过**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_pty.py -p no:xdist -o addopts="" -q`
Expected（Windows）：`10 passed`；POSIX：`7 passed, 3 skipped`。连跑 3 次都通过：

```bash
for i in 1 2 3; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_pty.py -p no:xdist -o addopts="" -q | tail -1; done
```

Run: `uvx --from ruff==0.15.13 ruff check cc_remote/platform/pty.py tests/test_platform_pty.py`
Expected: `All checks passed!`

- [ ] **Step 5: 提交**

```bash
git add cc_remote/platform/pty.py tests/test_platform_pty.py
cat > /tmp/msg-p3b-2.txt <<'EOF'
feat(platform): add portable pseudo-terminal processes

- POSIX: move the broker's openpty/setsid/TIOCSCTTY lifecycle behind pty unchanged
- Windows: host children in ConPTY with a reader thread and an exit settle window
- Release a pseudo console off the event loop, cancelling the reader until it really left read()

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg-p3b-2.txt
```

---

### Task 3: ConPTY 进程树 —— per-child Job 与"退出即释放伪控制台"

**Files:**
- Modify: `cc_remote/platform/pty.py`
- Modify: `tests/test_platform_pty.py`

**Interfaces:**
- Consumes: Task 2；pywin32 `win32job`、`win32api`；`_win32err.translated()`。
- Produces: `_windows_job(pid) -> Any | None`（模块内）；Windows `kill_group` 改为 `TerminateJobObject` 整树终止，
  树已空时抛 `ProcessLookupError`；Windows `wait()` 在子进程退出、读线程停下之后释放伪控制台（等同 `close()`，
  `close()` 幂等）。POSIX 不变。

残留竞态（必须写进 PR 描述）：pywinpty 在内部 `CreateProcess`，不能以 `CREATE_SUSPENDED` 启动，所以子进程在
`spawn()` 返回和 `AssignProcessToJobObject` 之间（微秒级）派生的后代不在 Job 里，`kill_group` 够不到它们。兜底：
(1) 挂在伪控制台上的进程会在伪控制台释放时结束（实测含孙进程），而 `wait()` 在被托管的子进程一退出就释放它，
`close()` 也会释放——效果对应 POSIX 上会话首进程退出时终端挂断；(2) 在那个窗口里派生、并且又主动脱离控制台
（`DETACHED_PROCESS`/`CREATE_NEW_CONSOLE`）的后代不在兜底范围内，这和 POSIX 上子进程自行 `setsid()` 逃出进程组
属于同一类，记录为已知残留。实测 venv `python.exe` 启动器立即拉起的真实解释器 10/10 次都在 Job 内。用例
`test_descendants_outside_the_job_end_with_the_pseudo_console` 关掉 Job 来模拟这个窗口，证明兜底生效。

- [ ] **Step 1: 写失败的测试**

`tests/test_platform_pty.py` 末尾追加：

```python
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
```

- [ ] **Step 2: 运行，确认失败**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_pty.py -p no:xdist -o addopts="" -q -k "kill_group or outside_the_job"`
Expected（Windows）：2 FAIL——`test_kill_group_ends_the_tree...` 在 `assert await _gone(grandchild)` 失败（只结束了子进程）；
另一个在 `monkeypatch.setattr` 处 `AttributeError: ... has no attribute '_windows_job'`。POSIX：`kill_group` 用例通过、
另一个 skip。

- [ ] **Step 3: 实现**

在 `_terminate_process` 之前加：

```python
_PROCESS_TERMINATE_AND_QUOTA = 0x0001 | 0x0100 | 0x1000  # TERMINATE|SET_QUOTA|QUERY_LIMITED


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
```

`_WindowsPty.spawn` 的 `return cls(terminal)` 改为 `return cls(terminal, _windows_job(int(terminal.pid)))`。

`_WindowsPty.wait` 改为：

```python
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
```

`_WindowsPty.kill_group` 整个替换为：

```python
    def kill_group(self, sig: int) -> None:
        del sig  # Windows has no graceful signal for a console-less tree
        import win32job

        from cc_remote.platform._win32err import translated

        if self._job is None:
            if self.poll() is not None:
                raise ProcessLookupError(self.pid)
            # pywinpty keeps the process handle open, so the pid is not reused.
            _terminate_process(self.pid)
            return
        with translated():
            accounting = win32job.QueryInformationJobObject(
                self._job, win32job.JobObjectBasicAccountingInformation)
            if int(accounting["ActiveProcesses"]) == 0:
                raise ProcessLookupError(self.pid)
            win32job.TerminateJobObject(self._job, 1)
```

（`close()` 已经把 `_job` 置为 `None` 并在释放线程里关闭 Job 句柄；Job 不设 `KILL_ON_JOB_CLOSE`，关句柄本身不杀进程。
`wait()` 之后再 `kill_group` 走 `_job is None` 分支，`poll()` 已有退出码 → `ProcessLookupError`，与 POSIX 的
`killpg` 对已回收进程组的行为一致。）

- [ ] **Step 4: 运行，确认通过**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_pty.py -p no:xdist -o addopts="" -q`
Expected（Windows）：`12 passed`；POSIX：`8 passed, 4 skipped`。连跑 3 次都通过。
Run: `uvx --from ruff==0.15.13 ruff check cc_remote/platform/pty.py tests/test_platform_pty.py`
Expected: `All checks passed!`

- [ ] **Step 5: 提交**

```bash
git add cc_remote/platform/pty.py tests/test_platform_pty.py
cat > /tmp/msg-p3b-3.txt <<'EOF'
feat(platform): end whole ConPTY process trees

- Put each ConPTY child in its own Job right after spawn; kill_group terminates the Job
- Release the pseudo console once the child exits so descendants that escaped the Job end too

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg-p3b-3.txt
```

---

### Task 4: `pty` 附着侧 —— 原始终端、尺寸、resize、输入

**Files:**
- Modify: `cc_remote/platform/pty.py`
- Modify: `tests/test_platform_pty.py`

**Interfaces:**
- Consumes: Task 2–3 的 `spawn`（Windows 上用 ConPTY 充当"用户的控制台"来测附着侧）。
- Produces: `RawTerminal`、`terminal_size`、`watch_resize`、`InputReader`（签名见接口总览）。POSIX 语义与原
  `cli._interactive_attach`（`tty.setraw` + `TCSAFLUSH` 恢复）、`cli._terminal_size`、SIGWINCH、`cli._read_fd` 一致。

- [ ] **Step 1: 写失败的测试**

`tests/test_platform_pty.py` 末尾追加：

```python
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
            received += await reader.read()
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
    output = _Collector()
    terminal = pty.spawn([sys.executable, str(script)], cwd=str(tmp_path), env=env)
    try:
        terminal.start(output)
        await output.until(lambda data: b"RAW raw=1 size=24x80" in data)
        for chunk in (b"a", "中".encode(), b"\x03", b"\x1b[A", b"\r"):
            await terminal.write(chunk)
        await output.until(lambda data: b"GOT 61e4b8ad031b5b410d" in data)
        terminal.resize(30, 100)
        await output.until(lambda data: b"RESIZED size=30x100" in data)
        assert await asyncio.wait_for(terminal.wait(), 10) == 0
        data = bytes(output.data) + b"".join(terminal.drain())
    finally:
        terminal.close()
    assert b"BEFORE raw=0" in data
    assert b"AFTER raw=0" in data


@pytest.mark.asyncio
async def test_input_reader_and_raw_terminal_accept_pipes():
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"piped")
        os.close(write_fd)
        write_fd = -1
        with pty.RawTerminal(read_fd, read_fd, keyboard=True):
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
```

- [ ] **Step 2: 运行，确认失败**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_pty.py -p no:xdist -o addopts="" -q`
Expected: 新用例 FAIL，`AttributeError: module 'cc_remote.platform.pty' has no attribute 'RawTerminal'`
（ConPTY 用例在子进程里报同样的错，表现为 15 s 内等不到 `RAW raw=1`）。

- [ ] **Step 3: 实现**

`pty.py` 的 import 区与常量区改为（新增 `stat`、`_RESIZE_POLL_SECONDS`、`DETACHED_CREATIONFLAGS`；Windows 追加
`ctypes`/`msvcrt`，POSIX 追加 `tty`；Task 2 的常量原样保留）：

```python
from __future__ import annotations

import asyncio
import codecs
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
# Windows: a console of its own without a window (not DETACHED_PROCESS, see
# decision 6), outside the launcher's Job; retried inside the Job on refusal.
JOB_BREAKAWAY_CREATIONFLAG = subprocess.CREATE_BREAKAWAY_FROM_JOB if IS_WINDOWS else 0
DETACHED_CREATIONFLAGS = (
    subprocess.CREATE_NO_WINDOW | JOB_BREAKAWAY_CREATIONFLAG if IS_WINDOWS else 0)

if IS_WINDOWS:
    import ctypes
    import msvcrt
else:
    import fcntl
    import termios
    import tty
```

文件末尾追加：

```python
# --- the attaching user's terminal --------------------------------------------

_ENABLE_PROCESSED_INPUT = 0x0001
_ENABLE_LINE_INPUT = 0x0002
_ENABLE_ECHO_INPUT = 0x0004
_ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
_DISABLE_NEWLINE_AUTO_RETURN = 0x0008
_UTF8_CODE_PAGE = 65001


def _console_handle(fd: int) -> int | None:
    """The console handle behind ``fd``, or None when it is not a console."""
    try:
        handle = msvcrt.get_osfhandle(fd)
    except OSError:
        return None
    mode = ctypes.c_uint32()
    if not ctypes.WinDLL("kernel32").GetConsoleMode(handle, ctypes.byref(mode)):
        return None
    return handle


def _console_mode(handle: int) -> int:
    mode = ctypes.c_uint32()
    if not ctypes.WinDLL("kernel32", use_last_error=True).GetConsoleMode(
            handle, ctypes.byref(mode)):
        raise ctypes.WinError(ctypes.get_last_error())
    return mode.value


def _set_console_mode(handle: int, mode: int) -> None:
    if not ctypes.WinDLL("kernel32", use_last_error=True).SetConsoleMode(handle, mode):
        raise ctypes.WinError(ctypes.get_last_error())


class RawTerminal:
    """Put the attaching terminal in raw mode; restore it on exit.

    POSIX: ``tty.setraw`` on stdin when it carries the keyboard, as before.
    Windows: VT output and UTF-8 output on the console screen buffer, and VT
    input with line/echo/Ctrl+C processing disabled when it carries the
    keyboard, so keys reach the remote TUI as the same byte sequences.
    """

    def __init__(self, stdin_fd: int, stdout_fd: int, *, keyboard: bool):
        self._stdin_fd = stdin_fd
        self._stdout_fd = stdout_fd
        self._keyboard = keyboard
        self._restore: list[Callable[[], object]] = []

    def __enter__(self) -> "RawTerminal":
        try:
            if IS_WINDOWS:
                self._enter_windows()
            elif self._keyboard and os.isatty(self._stdin_fd):
                original = termios.tcgetattr(self._stdin_fd)
                tty.setraw(self._stdin_fd)
                self._restore.append(lambda: termios.tcsetattr(
                    self._stdin_fd, termios.TCSAFLUSH, original))
        except BaseException:
            self.__exit__()
            raise
        return self

    def _enter_windows(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        output = _console_handle(self._stdout_fd)
        if output is not None:
            output_mode = _console_mode(output)
            code_page = kernel32.GetConsoleOutputCP()
            _set_console_mode(output, output_mode | _ENABLE_VIRTUAL_TERMINAL_PROCESSING
                              | _DISABLE_NEWLINE_AUTO_RETURN)
            self._restore.append(lambda: _set_console_mode(output, output_mode))
            kernel32.SetConsoleOutputCP(_UTF8_CODE_PAGE)
            self._restore.append(lambda: kernel32.SetConsoleOutputCP(code_page))
        console_input = _console_handle(self._stdin_fd) if self._keyboard else None
        if console_input is not None:
            input_mode = _console_mode(console_input)
            raw = input_mode & ~(_ENABLE_PROCESSED_INPUT | _ENABLE_LINE_INPUT
                                 | _ENABLE_ECHO_INPUT)
            _set_console_mode(console_input, raw | _ENABLE_VIRTUAL_TERMINAL_INPUT)
            self._restore.append(lambda: _set_console_mode(console_input, input_mode))

    def __exit__(self, *_exc: object) -> None:
        while self._restore:
            restore = self._restore.pop()
            try:
                restore()
            except OSError:
                pass


def terminal_size(fd: int) -> tuple[int, int, int, int]:
    """``(rows, cols, xpixel, ypixel)`` of the terminal on ``fd`` (24x80 default)."""
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

    POSIX uses SIGWINCH; Windows has none, so it polls every 250 ms. Returns
    an ``unwatch()`` callable.
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
                callback()

    task = loop.create_task(poll())
    return task.cancel


class InputReader:
    """Read chunks of keyboard input from ``fd`` without blocking the loop.

    POSIX waits for readiness with ``add_reader``. Windows reads on a daemon
    thread; a console is read with ReadConsoleW so input arrives as UTF-8.
    """

    def __init__(self, fd: int):
        self._fd = fd
        self._queue: asyncio.Queue[bytes] | None = None

    async def read(self) -> bytes:
        if not IS_WINDOWS:
            return await self._read_posix()
        if self._queue is None:
            self._queue = asyncio.Queue()
            threading.Thread(
                target=self._pump, args=(asyncio.get_running_loop(), self._queue),
                name="stdin-reader", daemon=True).start()
        return await self._queue.get()

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
        import io

        console = None
        if _console_handle(self._fd) is not None:
            console = io._WindowsConsoleIO(self._fd, "rb", closefd=False)  # type: ignore[attr-defined]
        while True:
            try:
                if console is not None:
                    data = console.read(READ_CHUNK_BYTES) or b""
                else:
                    data = os.read(self._fd, READ_CHUNK_BYTES)
            except OSError:
                data = b""
            try:
                loop.call_soon_threadsafe(queue.put_nowait, data)
            except RuntimeError:  # loop closed
                return
            if not data:
                return


__all__ = [
    "DETACHED_CREATIONFLAGS", "InputReader", "PtyProcess", "READ_CHUNK_BYTES", "RawTerminal",
    "spawn", "terminal_size", "watch_resize",
]
```

- [ ] **Step 4: 运行，确认通过**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_pty.py -p no:xdist -o addopts="" -q`
Expected（Windows）：`16 passed, 1 skipped`；POSIX：`12 passed, 5 skipped`。连跑 3 次都通过。
Run: `uvx --from ruff==0.15.13 ruff check cc_remote/platform/pty.py tests/test_platform_pty.py`
Expected: `All checks passed!`

- [ ] **Step 5: 提交**

```bash
git add cc_remote/platform/pty.py tests/test_platform_pty.py
cat > /tmp/msg-p3b-4.txt <<'EOF'
feat(platform): add raw terminal attachment helpers

- RawTerminal: termios raw mode on POSIX; VT console modes and UTF-8 output on Windows, restored on exit
- terminal_size, watch_resize (SIGWINCH or a 250 ms poll) and a thread-backed Windows InputReader
- DETACHED_CREATIONFLAGS for servers that must outlive the launching console

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg-p3b-4.txt
```

---

### Task 5: broker 会话迁到 `pty`；control store 改用平台权限

**Files:**
- Modify: `cc_remote/claude_broker/session.py`（import 区 1–31、常量 36、删除 365–385、`PTYSession` 387–530、
  537、623–637、700–731、1438–1500、1502–1523）
- Modify: `cc_remote/claude_broker/control_store.py:1-13, 44-56, 109-139`
- Modify: `tests/test_claude_broker.py`（假 Claude、新增会话级用例）

**Interfaces:**
- Consumes: `pty.spawn`、`pty.PtyProcess`、`procs.SIGTERM`/`SIGKILL`；`perms.is_private`、
  `perms.owned_by_current_user`、`perms.make_private`；`files.fchmod`、`files.replace`。
- Produces: `PTYSession.__init__(*, session_id, terminal: pty.PtyProcess, cwd, kind, resume_id, history_limit,
  generation, on_change, on_control_change, model, effort, permission_mode, bypass_allowed, auto_compact)`；
  `PTYSession.terminal`（替代原 `process`/`master_fd`）；`PTYSession.spawn(...)` 参数不变；
  `metadata()` 字段不变（`pid` 取 `terminal.pid`，`start_ticks` 取 `terminal.start_ticks`）。

- [ ] **Step 1: 测试侧——可移植的假 Claude**

`tests/test_claude_broker.py` 的 import 区加：

```python
import re
import sys

from cc_remote.platform import IS_WINDOWS, perms
from tests.ipc_helpers import SHORT_TMP
```

在 `fake_claude` fixture 之前加：

```python
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


def _install_fake(directory: Path, name: str, source: str) -> Path:
    """An executable fake Claude: a script on POSIX, a .cmd shim on Windows."""
    if not IS_WINDOWS:
        executable = directory / name
        executable.write_text(source, encoding="utf-8")
        executable.chmod(0o700)
        return executable
    script = directory / f"{name}.py"
    script.write_text(source, encoding="utf-8")
    shim = directory / f"{name}.cmd"
    shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    return shim


def _set_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    # Path.home() reads USERPROFILE on Windows and HOME elsewhere.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def _args(output: bytes) -> list[str]:
    return json.loads(re.search(rb"ARGS:(\[[^\r\n]*\])", output).group(1))


def _echoed(output: bytes) -> bytes:
    """Concatenate the fake's escaped echoes; a terminal may split one write."""
    return "".join(
        part.decode("unicode_escape") for part in re.findall(rb"OUT<(.*?)>", output)
    ).encode("utf-8")
```

把 `fake_claude` fixture 整体替换为：

```python
@pytest.fixture
def fake_claude(tmp_path: Path) -> Path:
    return _install_fake(tmp_path, "fake-claude", r'''#!/usr/bin/env python3
import json
import os
import sys
''' + RAW_TERMINAL + r'''
read = raw_terminal()
os.write(1, b"ARGS:" + json.dumps(sys.argv[1:]).encode() + b"\n")
os.write(1, b"\x1b[31mREADY\x1b[0m\n")
while True:
    data = read()
    if not data or data == b"\x04":
        break
    # Escape control bytes so a terminal emulator (ConPTY) passes them through.
    os.write(1, b"OUT<" + data.decode("utf-8", "replace").encode("unicode_escape") + b">")
''')
```

在 `control_fake_claude` fixture 里做四处精确替换（Edit，逐个唯一匹配）：
1. `executable = tmp_path / "control-fake-claude"\n    executable.write_text(\n        r'''#!/usr/bin/env python3\nimport json\nimport os\nimport sys\nimport tty\n`
   → `source = (\n        r'''#!/usr/bin/env python3\nimport json\nimport os\nimport sys\n''' + RAW_TERMINAL + r'''\n`
2. `tty.setraw(0)\nos.write(1, b"READY\n")` → `read = raw_terminal()\nos.write(1, b"READY\n")`
3. `    data = os.read(0, 65536)\n    if not data or data == b"\x04":\n        break\n    pending += data`
   → `    data = read()\n    if not data or data == b"\x04":\n        break\n    pending += data`
4. 该 fixture 结尾的
   `''',\n        encoding="utf-8",\n    )\n    executable.chmod(0o700)\n    return executable`
   → `'''\n    )\n    return _install_fake(tmp_path, "control-fake-claude", source)`

再把下列断言换成可移植写法：
- `_output_until(..., timeout: float = 3.0)` 默认值改为 `10.0`（ConPTY 下启动 Python 约 1 s）；
- `args_line = next(...)` / `argv = json.loads(args_line...)` 三处（`test_new_attach...` 两处、
  `test_broker_persists_sdk_controls...` 一处）改为 `argv = _args(initial)` / `_args(resumed_output)` / `args = _args(output)`；
- `assert b"OUT<\x03>" in await _output_until(replacement, b"OUT<\x03>")` →
  `assert b"\x03" in _echoed(await _output_until(replacement, b"OUT<\\x03>"))`；
- `assert b"OUT<remote prompt\r>" in await _output_until(replacement, b"remote prompt\r")` →
  `assert b"remote prompt\r" in _echoed(await _output_until(replacement, b"\\r>"))`；
- `replay = await _output_until(replay_attachment, b"remote prompt\r")` →
  `replay = await _output_until(replay_attachment, b"remote prompt")`；
- `test_terminal_focus_input_waits_for_remote_control_without_detaching` 里
  `await terminal.write(b"\x1b[O")` → `await terminal.write(b"\x1b[Ofocus")`，
  `assert b"OUT<\x1b[O>" in await _output_until(terminal, b"OUT<\x1b[O>")` →
  `assert b"focus" in _echoed(await _output_until(terminal, b"focus>"))`，并在该 write 上方加注释
  `# ConPTY consumes the focus report it requested itself; the trailing bytes prove the frame was serialized, not rejected.`；
- 所有 `monkeypatch.setenv("HOME", str(...))`（`control_broker`、`test_context_query...`、
  `test_model_control...`、`test_transcript_fallback...`、`test_transcript_lookup...`）改为 `_set_home(monkeypatch, ...)`；
- `test_broker_persists_sdk_controls_into_next_native_resume` 末尾
  `assert stat.S_IMODE(store.stat().st_mode) == 0o600` → `assert perms.is_private(store)`。

在文件末尾追加会话级用例：

```python
async def _events_until(subscription, needle: bytes, timeout: float = 10.0) -> bytes:
    output = bytearray()
    async with asyncio.timeout(timeout):
        while needle not in output:
            event = await subscription.queue.get()
            if event.kind == "exit":
                pytest.fail(f"session exited before {needle!r}: {event.data}")
            output.extend(event.data)
    return bytes(output)


@pytest.mark.asyncio
async def test_session_manager_hosts_the_tui_in_a_portable_pty(tmp_path: Path, fake_claude: Path):
    manager = broker_session_module.SessionManager(
        claude_binary=str(fake_claude), max_sessions=2, history_bytes=64 * 1024)
    try:
        session = await manager.create(cwd=str(tmp_path), args=[])
        metadata = session.metadata()
        assert metadata["pid"] == session.terminal.pid > 0
        assert metadata["start_ticks"] > 0
        subscription, snapshot, returncode = session.subscribe(owner="test", want_keyboard=True)
        assert returncode is None
        output = b"".join(snapshot)
        if b"READY" not in output:
            output += await _events_until(subscription, b"READY")
        assert _args(output)[:2] == ["--session-id", session.id]
        await session.write_terminal_input(subscription, "héllo".encode())
        await _events_until(subscription, b"OUT<h\\xe9llo>")
        session.resize(30, 100)
        await session.stop()
        assert session.running is False
        assert session.returncode is not None
    finally:
        await manager.close()


@pytest.mark.windows_only(reason="the explicit claude-remote launcher is a .cmd file on Windows (P5)")
def test_resolve_executable_refuses_the_claude_remote_launcher(tmp_path: Path):
    launcher = tmp_path / "claude-remote.cmd"
    launcher.write_text("@echo off\r\n", encoding="utf-8")
    with pytest.raises(broker_session_module.SessionError, match="refusing to launch claude-remote"):
        broker_session_module.resolve_executable(str(launcher))
```

- [ ] **Step 2: 运行，确认失败**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_claude_broker.py -p no:xdist -o addopts="" -q -k "portable_pty"`
Expected: FAIL，`AttributeError: 'PTYSession' object has no attribute 'terminal'`（Windows 上更早：
`AttributeError: module 'os' has no attribute 'openpty'` 被包装为 `SessionError: spawn_failed`）。

- [ ] **Step 3: 改 `session.py`**

import 区（第 3–31 行）改为：

```python
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
from typing import Callable, Literal
import uuid

from cc_remote.claude_paths import claude_projects_dir
from cc_remote.platform import procs, pty

from .control_store import ControlStore, ControlStoreError
```

删除常量 `OUTPUT_CHUNK_BYTES = 64 * 1024`，删除函数 `_process_start_ticks` 与 `_child_setup`（已搬到 `pty`）。

`PTYSession.__init__` 的前三个参数与对应赋值改为：

```python
    def __init__(
        self,
        *,
        session_id: str,
        terminal: pty.PtyProcess,
        cwd: str,
```

```python
        self.id = session_id
        self.terminal = terminal
        self.cwd = cwd
        self.kind = kind
        self.resume_id = resume_id
        self.generation = generation
        self.revision = 0
        self.start_ticks = terminal.start_ticks
```

并删除 `self._reader_active = False` 一行。

`PTYSession.spawn` 里从 `master_fd, slave_fd = os.openpty()` 到 `return cls(` 之前的代码替换为：

```python
        env = os.environ.copy()
        env["CLAUDE_REMOTE_BROKER"] = "1"
        if not env.get("TERM") or env.get("TERM") == "dumb":
            env["TERM"] = "xterm-256color"
        # The broker owns the outer lifecycle; an inherited marker from a
        # shell tool launched by Claude must not make the real child reject
        # itself as an accidental nested invocation.
        env.pop("CLAUDECODE", None)
        # Claude starts before a client necessarily attaches.  Give its TUI a
        # sane initial geometry; the first attached terminal replaces it.
        terminal = pty.spawn([executable, *args], cwd=cwd, env=env, rows=24, cols=80)
```

`return cls(` 的参数里 `process=process,\n            master_fd=master_fd,` 换成 `terminal=terminal,`。

`start()` 改为：

```python
    def start(self) -> None:
        if self._loop is not None:
            return
        self._loop = asyncio.get_running_loop()
        self.terminal.start(self._on_output)
        self._writer_task = asyncio.create_task(self._write_loop())
        self._wait_task = asyncio.create_task(self._wait_for_exit())
        self._transcript_watch_task = asyncio.create_task(
            self._watch_transcript_controls())
```

`metadata()` 里 `"pid": self.process.pid,` → `"pid": self.terminal.pid,`。

`_on_readable` 整个替换为：

```python
    def _on_output(self, data: bytes) -> None:
        self._observe_control_output(data)
        self._append_history(data)
        self._broadcast(SessionEvent("output", data))
```

删除 `_remove_reader`，`_wait_for_exit` 替换为：

```python
    async def _wait_for_exit(self) -> None:
        returncode = await self.terminal.wait()
        # Drain bytes that became readable immediately before wait() completed.
        for data in self.terminal.drain():
            self._append_history(data)
            self._broadcast(SessionEvent("output", data))
        self.terminal.stop_reading()
        self.returncode = returncode
        self.exited_at = time.time()
        self._set_terminal_composing(False)
        self._changed()
        self._broadcast(SessionEvent("exit", returncode))
```

`_write_bytes` 替换为（删除 `_wait_writable`，已搬到 `pty`）：

```python
    async def _write_bytes(self, data: bytes) -> None:
        try:
            await self.terminal.write(data)
        except OSError as exc:
            raise SessionError("pty_closed", f"PTY input failed: {exc}") from exc
```

`resize` 的 `try` 块改为：

```python
        try:
            self.terminal.resize(rows, cols, xpixel, ypixel)
        except OSError as exc:
            if self.running:
                raise SessionError("pty_closed", f"PTY resize failed: {exc}") from exc
```

`stop` 里两处 `os.killpg(self.process.pid, signal.SIGTERM)` / `os.killpg(self.process.pid, signal.SIGKILL)` 改为
`self.terminal.kill_group(procs.SIGTERM)` / `self.terminal.kill_group(procs.SIGKILL)`。

`close` 末尾

```python
        self._remove_reader()
        try:
            os.close(self.master_fd)
        except OSError:
            pass
```

替换为 `self.terminal.close()`。

`resolve_executable` 里防自递归的判断（`if os.path.basename(candidate) == "claude-remote":`）改为：

```python
    # The public wrapper must never recurse into itself.  A real official binary
    # may be a native executable or a script named `claude`; only our distinct
    # wrapper name is forbidden here (Windows launchers carry an extension).
    name = os.path.basename(candidate)
    stem, extension = os.path.splitext(name)
    if name == "claude-remote" or (
            stem.lower() == "claude-remote" and extension.lower() in {".bat", ".cmd", ".exe"}):
```

（原有的三行注释被这四行替换，`raise SessionError(...)` 不变。POSIX 上 `claude-remote` 的判定不变；新增拒绝的
`claude-remote.cmd/.bat/.exe` 在 POSIX 上本来就不可能是官方 Claude。P5 会把 `claude-remote.cmd` 放进 `bin\`，
用户若把它配成 Claude 可执行文件，broker 会无限自我拉起。）

- [ ] **Step 4: 改 `control_store.py`**

import 区：

```python
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid

from cc_remote.platform import files, perms
```

`load()` 的检查改为：

```python
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or not perms.owned_by_current_user(self.path)
                or not perms.is_private(self.path)
                or info.st_size > MAX_STORE_BYTES):
            raise ControlStoreError("Claude control store is not a private regular file")
```

`_write()` 的目录检查与写入改为：

```python
        parent = self.path.parent
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or not perms.owned_by_current_user(parent)
                or not perms.is_private(parent)):
            raise ControlStoreError("Claude control store directory is unsafe")
```

```python
        fd, temporary = tempfile.mkstemp(prefix=".controls-", dir=parent)
        try:
            files.fchmod(fd, temporary, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as stream:
                fd = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            files.replace(temporary, self.path)
            perms.make_private(self.path, directory=False)
        finally:
```

（`finally` 块不变。POSIX 上：`perms.is_private`=属主相同且 `mode & 0o077 == 0`，`files.fchmod`=`os.fchmod`，
`files.replace`=`os.replace`，`make_private`=`os.chmod(path, 0o600)`，与原代码逐条等价。）

- [ ] **Step 5: 运行，确认通过**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_claude_broker.py -p no:xdist -o addopts="" -q -k "portable_pty or claude_remote_launcher or launch_controls or native_context or explicit_cli or transcript or frame_header or stale_broker"`
Expected: 全部通过（使用 `broker`/`control_broker` fixture 的用例要等 Task 6 的 server 迁移，这里不选）。
Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_claude_broker_handle.py tests/test_claude_broker_history.py -p no:xdist -o addopts="" -q`
Expected: 全部通过（基线里无失败）。
Run: `uvx --from ruff==0.15.13 ruff check cc_remote/claude_broker tests/test_claude_broker.py`
Expected: `All checks passed!`

- [ ] **Step 6: 提交**

```bash
git add cc_remote/claude_broker/session.py cc_remote/claude_broker/control_store.py tests/test_claude_broker.py
cat > /tmp/msg-p3b-5.txt <<'EOF'
refactor(claude-broker): host Claude TUIs through platform pty

- Spawn, read, write, resize, stop and close sessions through pty.PtyProcess
- Check and replace the control store with platform permission and file helpers
- Make the broker test fakes run under a POSIX tty or ConPTY

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg-p3b-5.txt
```

---

### Task 6: broker server/client 迁到 `ipc`

**Files:**
- Modify: `cc_remote/claude_broker/server.py:1-133, 136-190, 211-225, 446-463`
- Modify: `cc_remote/claude_broker/client.py:1-25, 121-151`
- Modify: `tests/test_claude_broker.py`（`short_socket_dir`、socket 模式断言、peer uid 用例）

**Interfaces:**
- Consumes: `ipc.ensure_private_directory`、`ipc.start_server`、`ipc.open_connection`、`ipc.IpcServer`、
  `ipc.UnsafeEndpointError`、`ipc.AlreadyRunningError`、`ipc.AuthenticationError`、`ipc._peer_uid`（仅 POSIX 测试）。
- Produces: `BrokerServer.start()` 错误：已运行 → `BrokerSecurityError("broker is already running at <path>")`；
  路径被占 → `BrokerSecurityError("refusing to replace ...")`；目录不私有 → `BrokerSecurityError("broker socket directory ...")`。
  `BrokerClient` 错误码不变：缺失/过期/连不上 → `broker_unavailable`；不私有/对端不是本用户 → `unsafe_socket`。
  `cc_remote.claude_broker.client` 不再 import `server`（wrapper 侧 import client 不会拉起 pty/pywinpty）。

- [ ] **Step 1: 改测试**

- `short_socket_dir`：`Path(tempfile.mkdtemp(prefix="ccrb-", dir="/tmp"))` → `dir=SHORT_TMP`；
- import 区 `from cc_remote.claude_broker.server import (BrokerConfig, BrokerSecurityError, BrokerServer, _peer_uid,)`
  去掉 `_peer_uid`，并加 `from cc_remote.platform import ipc`（与 Task 5 加的 `IS_WINDOWS, perms` 合并为一行
  `from cc_remote.platform import IS_WINDOWS, ipc, perms`）；
- `test_status_list_limits_and_forbidden_claude_flags`：
  `mode = stat.S_IMODE(socket_path.stat().st_mode)\n        assert mode == 0o600` → `assert perms.is_private(socket_path)`；
- `test_peer_uid_and_default_socket_path` 拆成两个：

```python
@pytest.mark.posix_only(reason="SO_PEERCRED/getpeereid is the POSIX same-user proof")
def test_peer_uid_reads_the_connected_user():
    left, right = socket.socketpair()
    try:
        assert ipc._peer_uid(left) == os.getuid()
        assert ipc._peer_uid(right) == os.getuid()
    finally:
        left.close()
        right.close()


def test_default_socket_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv(SOCKET_ENV, raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert default_socket_path() == str(tmp_path / "cc-remote" / "claude-broker.sock")
    override = tmp_path / "override.sock"
    monkeypatch.setenv(SOCKET_ENV, str(override))
    assert default_socket_path() == str(override)
```

- `test_server_rejects_a_peer_uid_mismatch` 改为：

```python
@pytest.mark.posix_only(reason="peer uid is the POSIX proof; Windows HMAC rejection is covered in test_platform_ipc")
@pytest.mark.asyncio
async def test_server_rejects_a_peer_uid_mismatch(
    broker, monkeypatch: pytest.MonkeyPatch,
):
    _server, client = broker
    real = ipc._peer_uid
    # Accepted sockets are bound to the endpoint path; the client side is not,
    # so the client's own check still passes and only the accept side fails.
    monkeypatch.setattr(
        ipc, "_peer_uid", lambda sock: real(sock) + (1 if sock.getsockname() else 0))
    with pytest.raises(BrokerClientError) as rejected:
        await client.status()
    assert rejected.value.code == "broker_disconnected"
```

- `test_cli_resume_normalizes_an_explicit_cwd_before_broker_request` 上加
  `@pytest.mark.requires_symlink(reason="the test resolves a symlinked cwd alias")`（放在 `@pytest.mark.asyncio` 之上）。
- 删除不再使用的 `import stat`（若 ruff 报 F401）。

- [ ] **Step 2: 运行，确认失败**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_claude_broker.py -p no:xdist -o addopts="" -q`
Expected: 使用 `broker`/`control_broker` 的用例 ERROR，`BrokerSecurityError: broker socket directory must not be accessible by other users`
（Windows 上旧 `_prepare_parent` 的 `st_mode & 0o077`）；收集阶段若报 `ImportError: cannot import name '_peer_uid'`
也属预期（Step 1 已改 import，server 仍导出它，此时不会出现）。

- [ ] **Step 3: 改 `server.py`**

文件头 docstring 与 import 区改为：

```python
"""Same-user local endpoint server for persistent Claude Code PTYs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any
import uuid

from cc_remote.platform import ipc

from .paths import default_socket_path
```

（其后 `from .protocol import (...)`、`from .session import (...)` 不变。）删除 `_peer_uid` 与 `_remove_stale_socket`，
`_prepare_parent` 替换为：

```python
def _prepare_parent(path: str) -> Path:
    if not path or "\x00" in path:
        raise BrokerSecurityError("broker socket path is invalid")
    encoded = os.fsencode(path)
    if len(encoded) > MAX_SOCKET_PATH_BYTES:
        raise BrokerSecurityError(
            f"broker socket path exceeds {MAX_SOCKET_PATH_BYTES} bytes",
        )
    target = Path(path).expanduser().absolute()
    try:
        ipc.ensure_private_directory(target.parent)
    except ipc.UnsafeEndpointError as exc:
        raise BrokerSecurityError(f"broker socket {exc}") from exc
    return target
```

`BrokerServer.__init__` 里：`self._server: asyncio.AbstractServer | None = None` → `self._server: ipc.IpcServer | None = None`，
删除 `self._socket_identity: tuple[int, int] | None = None`。类 docstring 改为
`"""Owns Claude PTYs and exposes only an authenticated same-user local endpoint."""`。

`start()` 替换为：

```python
    async def start(self) -> None:
        if self._server is not None:
            return
        target = _prepare_parent(self.config.socket_path)
        self.manager.load_control_store()
        try:
            self._server = await ipc.start_server(target, self._handle_client)
        except ipc.AlreadyRunningError as exc:
            raise BrokerSecurityError(f"broker is already running at {target}") from exc
        except ipc.UnsafeEndpointError as exc:
            raise BrokerSecurityError(str(exc)) from exc
```

`_handle_client` 开头删除 peer 检查块（从 `peer_socket = writer.get_extra_info("socket")` 到其后的 `return`，共 10 行），
函数从 `if len(self._connections) >= MAX_CLIENTS:` 开始。

`close()` 删除末尾从 `target = Path(self.config.socket_path)...` 到 `target.unlink()` 的 7 行（`IpcServer.close()`
只在 endpoint 仍属于自己时删除它）。

- [ ] **Step 4: 改 `client.py`**

import 区改为：

```python
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from cc_remote.platform import ipc

from .paths import default_socket_path
from .protocol import (
    BROKER_PROTOCOL_VERSION,
    FrameType,
    MAX_CONTROL_BYTES,
    MAX_FRAME_BYTES,
    ProtocolError,
    decode_json,
    read_frame,
    write_frame,
    write_json,
)
```

`_connect` 替换为：

```python
    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        target = Path(self.socket_path).expanduser().absolute()
        try:
            return await ipc.open_connection(target)
        except FileNotFoundError as exc:
            raise BrokerClientError(
                "broker_unavailable", f"broker is not running at {target}",
            ) from exc
        except (ipc.UnsafeEndpointError, ipc.AuthenticationError) as exc:
            raise BrokerClientError("unsafe_socket", str(exc)) from exc
        except OSError as exc:
            raise BrokerClientError("broker_unavailable", f"cannot connect to broker: {exc}") from exc
```

- [ ] **Step 5: 运行，确认通过**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_claude_broker.py -p no:xdist -o addopts="" -q`
Expected（Windows，未开开发者模式）：除下列两类外全部通过——
(a) skip：`test_peer_uid_reads_the_connected_user`、`test_server_rejects_a_peer_uid_mismatch`（posix_only）、
`test_cli_resume_normalizes_an_explicit_cwd_before_broker_request`（requires_symlink）；
(b) 仍失败且首个仓库内异常是子项目 2 根因（`Claude profile state migration is incomplete` /
`rollback journal could not be persisted`）的经 `machine._process_command` 的用例
（`test_machine_broker_controls_reach_native_tui_and_use_footer_confirmation[*]` 若出现）。
`test_cli_attachment_treats_control_input_busy_as_recoverable` 此时仍可能因 `cli` 尚未迁移而失败，由 Task 7 修复。

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_claude_broker_handle.py tests/test_claude_broker_history.py tests/test_platform_ipc.py -p no:xdist -o addopts="" -q`
Expected: 全部通过（ipc 的 skip 数与 3a 相同）。
Run: `uvx --from ruff==0.15.13 ruff check cc_remote/claude_broker tests/test_claude_broker.py`
Expected: `All checks passed!`

- [ ] **Step 6: 提交**

```bash
git add cc_remote/claude_broker/server.py cc_remote/claude_broker/client.py tests/test_claude_broker.py
cat > /tmp/msg-p3b-6.txt <<'EOF'
refactor(claude-broker): serve and connect through platform ipc

- Prepare the private endpoint directory, reclaim stale endpoints and authenticate peers in ipc
- Keep broker error codes: broker_unavailable, unsafe_socket, already running, refusing to replace
- Stop importing the PTY server from the client module

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg-p3b-6.txt
```

---

### Task 7: `claude-remote` CLI —— 附着、前台服务、自动拉起

**Files:**
- Modify: `cc_remote/claude_broker/cli.py:1-30, 132-249, 252-276, 279-337`
- Modify: `tests/test_claude_broker.py`（新增 2 个 CLI 用例）

**Interfaces:**
- Consumes: `pty.RawTerminal`、`pty.terminal_size`、`pty.watch_resize`、`pty.InputReader`；
  `signals.install_shutdown_handler(loop, callback) -> Callable[[], None]`；`procs.popen_group(args, **kwargs)`；
  `ipc.ensure_private_directory`、`ipc.UnsafeEndpointError`。
- Produces: `_interactive_attach(client, session_id, *, keyboard) -> int`、`_serve(args) -> int`、
  `_ensure_broker(client, socket_path) -> None`、`_write_all(fd, data)` 签名不变；删除 `_terminal_size`、`_read_fd`
  （仓库内只有 cli 自己使用）。

- [ ] **Step 1: 写失败的测试**

`tests/test_claude_broker.py` 末尾追加（import 区补 `import subprocess`）：

```python
@pytest.mark.asyncio
async def test_cli_keyboard_attachment_forwards_piped_stdin(monkeypatch: pytest.MonkeyPatch):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"typed")
    os.close(write_fd)
    received = bytearray()
    typed = asyncio.Event()

    class Attachment:
        keyboard = True

        def __init__(self):
            self.closed = False

        async def read(self):
            await typed.wait()
            return FrameType.EXIT, {"returncode": 5}

        async def write(self, data):
            received.extend(data)
            if bytes(received) == b"typed":
                typed.set()

        async def resize(self, *_args):
            return None

        async def close(self):
            self.closed = True

    attachment = Attachment()

    class Client:
        async def attach(self, _session_id, *, keyboard=True):
            assert keyboard is True
            return attachment

    class Stream:
        def __init__(self, fd):
            self.fd = fd

        def fileno(self):
            return self.fd

    monkeypatch.setattr(broker_cli, "_write_all", lambda _fd, _data: None)
    monkeypatch.setattr(broker_cli.sys, "stdin", Stream(read_fd))
    monkeypatch.setattr(broker_cli.sys, "stdout", Stream(1))
    try:
        result = await broker_cli._interactive_attach(
            Client(), "11111111-1111-4111-8111-111111111111", keyboard=True)
    finally:
        os.close(read_fd)
    assert result == 5
    assert bytes(received) == b"typed"
    assert attachment.closed is True


@pytest.mark.asyncio
async def test_ensure_broker_starts_one_detached_private_broker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    started = []

    def popen_group(args, **kwargs):
        started.append((args, kwargs))
        return object()

    monkeypatch.setattr(broker_cli.procs, "popen_group", popen_group)

    class Client:
        attempts = 0

        async def status(self):
            Client.attempts += 1
            if Client.attempts == 1:
                raise BrokerClientError("broker_unavailable", "not running")
            return {"ok": True}

    socket_path = tmp_path / "broker" / "claude-broker.sock"
    await broker_cli._ensure_broker(Client(), str(socket_path))
    assert perms.is_private(socket_path.parent)
    assert len(started) == 1
    args, kwargs = started[0]
    assert args[1:] == ["-m", "cc_remote.claude_remote", "--socket", str(socket_path), "serve"]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["creationflags"] == broker_cli.pty.DETACHED_CREATIONFLAGS
    assert (socket_path.parent / "claude-broker.log").exists()
```

- [ ] **Step 2: 运行，确认失败**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_claude_broker.py -p no:xdist -o addopts="" -q -k "cli_"`
Expected: 新用例 FAIL（`AttributeError: module 'cc_remote.claude_broker.cli' has no attribute 'procs'`；Windows 上
附着用例 `NameError: name 'fcntl' is not defined` 或 `add_reader` 的 `NotImplementedError`）。

- [ ] **Step 3: 实现**

import 区（第 1–28 行）改为：

```python
"""Explicit ``claude-remote`` command line; never shadows official ``claude``."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from cc_remote.platform import ipc, procs, pty, signals

from .client import BrokerClient, BrokerClientError
from .paths import default_socket_path
from .protocol import FrameType
from .server import BrokerConfig, BrokerSecurityError, BrokerServer
from .session import DEFAULT_HISTORY_BYTES, DEFAULT_MAX_SESSIONS, SessionError
```

删除 `_terminal_size` 与 `_read_fd`。`_interactive_attach` 整个替换为：

```python
async def _interactive_attach(client: BrokerClient, session_id: str, *, keyboard: bool) -> int:
    attachment = await client.attach(session_id, keyboard=keyboard)
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    resize_event = asyncio.Event()
    unwatch: Callable[[], None] | None = None

    if keyboard and not attachment.keyboard:
        os.write(sys.stderr.fileno(), b"claude-remote: attached as read-only mirror (keyboard is in use)\n")

    async def output_loop() -> int:
        while True:
            frame_type, payload = await attachment.read()
            if frame_type is FrameType.OUTPUT:
                assert isinstance(payload, bytes)
                _write_all(stdout_fd, payload)
            elif frame_type is FrameType.EXIT:
                assert isinstance(payload, dict)
                return int(payload.get("returncode", 0))
            elif frame_type is FrameType.ERROR:
                assert isinstance(payload, dict)
                error = payload.get("error", {})
                # Older brokers reject a terminal focus/key frame with
                # input_busy while Remote is durably confirming a model,
                # effort, or permission change. That control window is
                # recoverable and the server keeps the attachment open; do not
                # turn the advisory frame into a terminal disconnect.
                if str(error.get("code", "")) == "input_busy":
                    continue
                raise BrokerClientError(
                    str(error.get("code", "broker_error")),
                    str(error.get("message", "attachment failed")),
                )

    async def input_loop() -> None:
        if not attachment.keyboard:
            await asyncio.Future()
        reader = pty.InputReader(stdin_fd)
        while True:
            data = await reader.read()
            if not data:
                return
            await attachment.write(data)

    async def resize_loop() -> None:
        while True:
            await resize_event.wait()
            resize_event.clear()
            await attachment.resize(*pty.terminal_size(stdin_fd))

    try:
        with pty.RawTerminal(stdin_fd, stdout_fd, keyboard=attachment.keyboard):
            try:
                if os.isatty(stdin_fd):
                    await attachment.resize(*pty.terminal_size(stdin_fd))
                    unwatch = pty.watch_resize(stdin_fd, resize_event.set)
                output_task = asyncio.create_task(output_loop())
                input_task = asyncio.create_task(input_loop())
                resize_task = asyncio.create_task(resize_loop())
                done, pending = await asyncio.wait(
                    {output_task, input_task}, return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                resize_task.cancel()
                await asyncio.gather(*pending, resize_task, return_exceptions=True)
                if output_task in done:
                    return await output_task
                return 0  # stdin disconnected: detach, but leave Claude running
            finally:
                if unwatch is not None:
                    unwatch()
    finally:
        await attachment.close()
```

`_serve` 的信号部分替换为：

```python
    await server.start()
    print(f"claude-remote broker listening on {server.socket_path}", file=sys.stderr)
    stopped = asyncio.Event()
    uninstall = signals.install_shutdown_handler(asyncio.get_running_loop(), stopped.set)
    try:
        await stopped.wait()
    finally:
        uninstall()
        await server.close()
    return 0
```

`_ensure_broker` 从 `target = os.path.abspath(...)` 到 `os.close(log_fd)` 所在 `finally` 结束替换为：

```python
    target = os.path.abspath(os.path.expanduser(socket_path))
    parent = os.path.dirname(target)
    try:
        ipc.ensure_private_directory(parent)
    except ipc.UnsafeEndpointError as exc:
        raise BrokerSecurityError(
            "broker socket directory must be private and owned by the current uid") from exc
    log_path = os.path.join(parent, "claude-broker.log")
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        procs.popen_group(
            [
                sys.executable,
                "-m",
                "cc_remote.claude_remote",
                "--socket",
                target,
                "serve",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log_fd,
            stderr=log_fd,
            close_fds=True,
            # Windows: no console, so closing the launching terminal cannot end
            # the broker. POSIX gets 0, which subprocess accepts unchanged.
            creationflags=pty.DETACHED_CREATIONFLAGS,
        )
    finally:
        os.close(log_fd)
```

（等待就绪的循环与报错不变。POSIX 上 `procs.popen_group` 就是 `Popen(start_new_session=True, ...)`。）

- [ ] **Step 4: 运行，确认通过**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_claude_broker.py -p no:xdist -o addopts="" -q`
Expected：Task 6 Step 5 的结论成立，且所有 `test_cli_*` 通过（`test_cli_resume_normalizes...` 在未开开发者模式时 skip）。
Run: `.venv/Scripts/python.exe -m cc_remote.claude_remote --help`
Expected: 打印 `usage: claude-remote ...`，退出码 0（证明 Windows 上 CLI 可导入）。
Run: `uvx --from ruff==0.15.13 ruff check cc_remote/claude_broker tests/test_claude_broker.py`
Expected: `All checks passed!`

- [ ] **Step 5: 提交**

```bash
git add cc_remote/claude_broker/cli.py tests/test_claude_broker.py
cat > /tmp/msg-p3b-7.txt <<'EOF'
feat(claude-remote): attach and launch the broker on every platform

- Attach through pty RawTerminal, terminal_size, watch_resize and InputReader
- Stop the foreground broker on platform shutdown signals
- Launch a detached broker in its own process group inside a private directory

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg-p3b-7.txt
```

---

### Task 8: 基线验收、真机冒烟、门禁与 PR

**Files:**
- 无代码改动（发现 P3 问题时回到对应任务修复）。

**Interfaces:**
- Consumes: Task 1–7。
- Produces: 分支 `win/3b-broker` 与 fork 内 PR（目标 `windows-compat-v2`）。

- [ ] **Step 1: 逐文件跑并对照基线**

```bash
mkdir -p /tmp/p3b-junit
for f in test_platform_pty test_platform_ipc test_claude_broker test_claude_broker_machine \
         test_claude_broker_handle test_claude_broker_history test_engine_versions test_release_distribution; do
  PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/$f.py -p no:xdist -o addopts="" -q \
    --junitxml=/tmp/p3b-junit/$f.xml > /tmp/p3b-junit/$f.log 2>&1; echo "$f exit=$?"
done
.venv/Scripts/python.exe - <<'EOF'
import glob, json, xml.etree.ElementTree as ET
base = json.load(open("docs/superpowers/windows-baseline.json", encoding="utf-8"))
items = base if isinstance(base, list) else base["failures"]
mine = {x["nodeid"] for x in items if str(x["owner"]) == "3" and "broker" in x["nodeid"]}
status = {}
for path in glob.glob("/tmp/p3b-junit/*.xml"):
    for case in ET.parse(path).iter("testcase"):
        node = "tests/" + case.get("classname").split(".")[-1] + ".py::" + case.get("name")
        bad = case.find("failure") is not None or case.find("error") is not None
        status[node] = "fail" if bad else ("skip" if case.find("skipped") is not None else "pass")
left = sorted(n for n in mine if status.get(n) != "pass")
print("baseline P3 broker entries:", len(mine), "left:", len(left))
for n in left: print("LEFT", status.get(n), n)
EOF
```

Expected：`baseline P3 broker entries: 31`。`LEFT` 只允许：
- `test_claude_broker.py::test_peer_uid_and_default_socket_path`（已拆分改名，节点不再存在，状态为 `None`；新节点
  `test_default_socket_path` 通过、`test_peer_uid_reads_the_connected_user` skip）；
- `test_claude_broker.py::test_cli_resume_normalizes_an_explicit_cwd_before_broker_request`（未开开发者模式时 skip）；
- `test_claude_broker_machine.py` 的条目及任何经 `machine.py` 的条目，且其 `.log` 中首个仓库内异常属于子项目 2 根因
  （`Claude profile state migration is incomplete`、`rollback journal could not be persisted`）。
其它任何 `LEFT` = P3 问题。

验收口径（写进 PR 描述；基线里 31 条归 P3 的 broker 条目 = `test_claude_broker.py` 17 条 + `test_claude_broker_machine.py`
14 条）：

| 时间点 | 预期 |
|---|---|
| 只合入 P3（3a + 本 PR） | `test_claude_broker.py` 的 17 条：15 条通过；`test_peer_uid_and_default_socket_path` 拆分改名（新节点一过一 skip）；`test_cli_resume_normalizes_an_explicit_cwd_before_broker_request` 在没有符号链接权限时 skip（`requires_symlink`），否则通过。`test_claude_broker_machine.py` 的 14 条仍失败，首个仓库内异常全是 P2 根因（7 条 `profile_state_migration`，其余 7 条日志里先出现 `rollback journal could not be persisted` / `Claude profile state migration is incomplete`，评审时抽查过两条） |
| P2 的这两个根因也合入 `windows-compat-v2` 之后 | 31 条全部通过（符号链接那条按环境 skip）；在合并后的分支上重跑 Step 1，`LEFT` 只允许上面两个改名/skip 的节点 |

- [ ] **Step 2: 真机冒烟（Windows，需要已登录的官方 Claude Code）**

```bash
export CC_REMOTE_CLAUDE_BROKER_SOCKET="$LOCALAPPDATA/cc-remote-smoke/claude-broker.sock"
.venv/Scripts/python.exe -m cc_remote.claude_remote --json status || true
.venv/Scripts/python.exe -m cc_remote.claude_remote new --no-attach --json
.venv/Scripts/python.exe -m cc_remote.claude_remote --json list
```

然后在 Windows Terminal 里执行 `.venv/Scripts/python.exe -m cc_remote.claude_remote attach <id>`，人工核对：
Claude TUI 正常渲染、中文输入、方向键、Shift+Tab 切换权限、Ctrl-C 中断、拖动窗口后 TUI 重排、按 Ctrl-Z 之外的方式
（关闭窗口）断开后 `list` 显示会话仍在运行、再次 `attach` 恢复画面；最后
`.venv/Scripts/python.exe -m cc_remote.claude_remote stop <id>`，`list` 显示 `exited`。把结果（含任何渲染异常，
规范 §9 风险项）写进 PR 描述。冒烟结束后结束 broker 进程（`list` 里看不到它的 pid；用
`.venv/Scripts/python.exe -c "from cc_remote.platform import ipc; print(ipc.read_endpoint(r'%LOCALAPPDATA%\cc-remote-smoke\claude-broker.sock'))"`
取 pid 后 `taskkill /PID <pid>`）。

- [ ] **Step 3: 本地门禁**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote tests deploy
npm --prefix web run build
npm --prefix web run test:reliability
npm --prefix web run lint
git diff --check origin/windows-compat-v2...HEAD
```

Expected：每条退出码 0；`bash -n`/`shellcheck`/全量 POSIX pytest 交给 Linux CI，PR 中如实说明。

- [ ] **Step 4: 推送并开 PR**

```bash
git push -u origin win/3b-broker
cat > /tmp/pr-p3b.md <<'EOF'
## Summary
- Add `cc_remote/platform/pty.py`: POSIX keeps openpty/setsid/TIOCSCTTY; Windows hosts children in ConPTY (pywinpty 3.1.0) with a per-child Job, and attaches the user's console in raw VT mode
- Move the Claude broker session, server, client, control store and `claude-remote` onto `pty`, `ipc`, `procs`, `signals`, `perms`
- Pin `pywinpty==3.1.0 ; sys_platform == "win32"` for full and wrapper installs

## Validation
- Windows per-file pytest (plan Task 8) and a manual claude-remote smoke test (notes below)
- Linux: CI

## Remaining baseline entries (owned by P2)
<paste Task 8 Step 1 LEFT list>

## Smoke notes
<paste Step 2 observations>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
gh pr create --repo donny-star/cc-remote --base windows-compat-v2 --head win/3b-broker \
  --title "feat(platform): ConPTY broker and claude-remote on Windows" --body-file /tmp/pr-p3b.md
```

Expected：PR 创建成功；`gh pr checks --repo donny-star/cc-remote <number> --watch` 全绿。

---

## 跨子项目依赖（需要 controller 裁定 / 对方配合）

| 需要谁 | 什么 | 原因 |
|---|---|---|
| P3a | `ipc` 已合入 | Task 6、7 直接使用 |
| P2 | `machine.py` profile 迁移、`rollback_commands.py` 目录 fsync | `test_claude_broker_machine.py` 14 条基线（归 P3）都经 `machine.py`，首个异常是这两个 P2 根因 |
| P2 / controller | 依赖文件与锁文件的修改权 | 协作约定没列出 `requirements*.txt/.lock` 的归属；本计划按用户指示加 pywinpty。若 P2 同期也改锁文件，后合入的一方需按文件头命令重新生成 |
| P5 | 安装器把 `claude-remote.cmd` 放进 `bin\`，并保证 wrapper 的 Windows 安装带上 pywinpty（来自 `requirements-wrapper.lock`） | 规范 §6 |
| P4 | 无 | TUI/session_messages 只用 3a 的 `ipc` |

## 裁定记录

1. **ConPTY 子进程的 Job 由 `pty.py` 自己创建**（`OpenProcess` + `AssignProcessToJobObject`，紧接 `spawn` 之后；
   controller 已接受），不经 `procs`：pywinpty 内部调用 `CreateProcess`，无法以 `CREATE_SUSPENDED` 启动，`procs` 也没有
   "收养已运行进程"的公开 API（`procs.py` 归 P2）。残留竞态与兜底见 Task 3 开头（伪控制台在子进程退出或 `close()` 时
   释放，结束漏在 Job 外、仍挂在控制台上的后代；主动脱离控制台的后代是已知残留），用例
   `test_descendants_outside_the_job_end_with_the_pseudo_console` 证明兜底。Job 分配失败时 `kill_group` 直接
   `TerminateProcess`（pywinpty 持有进程句柄，pid 不会被复用）。
2. **`start_ticks` 放进 `PtyProcess`**：POSIX 保留原 `/proc` 起始 tick / `monotonic_ns` 实现（行为不变），Windows 用
   `win32_process.creation_time`。没有改成 `process_scan` 的 start_time，以免 macOS 上的数值语义变化（规范 §5 的
   目的——防 pid 复用——同样满足）。
3. **Windows 读取用"阻塞读线程 + 退出后等输出静默再 `cancel_io`"**，不用 10 ms 轮询：32 个会话时轮询每秒 3200 次唤醒。
   静默窗口 100 ms、上限 1 s（评审改：原来是固定 100 ms，ConPTY 渲染慢时会丢最后一帧）。`cancel_io` 只取消在途读，
   所以 `_stop_reader` 和释放线程 `_dispose` 都循环调用，直到读线程确认退出（最多 2 s）——评审发现原 `_dispose` 只调
   一次，读线程恰好在两次读之间时会永远卡在下一次读里，伪控制台和子进程都不会被释放。
4. **写入按 UTF-8 增量解码后再交给 pywinpty**（它只收 `str`，且 `write()` 恒返回 0，不看返回值）：被拆开的多字节字符
   等齐后再写；非法字节替换为 U+FFFD。100 KB 用例证明完整送达。
5. **终端尺寸在 Windows 上用 `shutil.get_terminal_size()`**（规范 §5 原文）：控制台输入句柄没有几何信息。
6. **broker 自动拉起时加 `DETACHED_PROCESS`**（controller 已接受）：否则关闭发起 `claude-remote` 的终端窗口会连带结束
   broker，与 POSIX `start_new_session` 语义不符。评审改为由 `pty.DETACHED_CREATIONFLAGS` 提供（POSIX 为 0），
   不在 `cli.py` 里写 `getattr(subprocess, "DETACHED_PROCESS", 0)` 这种隐式平台分支。评审实测 `DETACHED_PROCESS`
   进程里能正常创建 ConPTY。**最终取值（Task 7 与收尾修复）**：Windows 为 `CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB`，
   不再用 `DETACHED_PROCESS`——venv 的 `python.exe` 是会重新拉起真解释器的启动器，无控制台的启动器会让真解释器
   新开一个可见控制台窗口（关掉它就会结束 broker）；`CREATE_NO_WINDOW` 给 broker 一个自己的隐藏控制台。
   `CREATE_BREAKAWAY_FROM_JOB` 让 broker 脱离发起终端所在的 kill-on-close Job（IDE 终端、CI、计划任务）；
   该 Job 不允许脱离时 `CreateProcess` 报 `PermissionError`，`cli.py` 去掉 `pty.JOB_BREAKAWAY_CREATIONFLAG` 重试一次。
7. **测试里的假 Claude 改为转义回显**（`OUT<` + `unicode_escape` + `>`），并用 `_echoed` 拼接：ConPTY 会把一次写入拆成
   多次读，也会吞掉它自己请求的焦点报告（`ESC[O`/`ESC[I`，评审复测属实，`test_conpty_consumes_focus_reports` 记录该
   行为），焦点用例改为在报告后附带普通字节来证明帧被串行处理。已知影响：Windows 上被托管的 Claude TUI 收不到焦点事件。
   ConPTY 启动时输出的 `ESC[1t ESC[c ESC[?1004h ESC[?9001h` 会进入 broker 历史并重放给附着端，冒烟时重点看
   Windows Terminal 与 Web 端是否因此回送多余输入（规范 §9 风险项），结果写进 PR 描述。
8. **Windows 释放伪控制台的时机提前到子进程退出时**（评审新增，`wait()` 内）：已退出会话会保留到被淘汰，原来要等那时才
   释放，期间 conhost 与漏网后代一直存活。之后的 `write`/`resize` 抛 `OSError`，`PTYSession` 原本就对已退出会话把它们
   视为 `pty_closed` 或忽略。
9. **`resolve_executable` 也拒绝 `claude-remote.cmd/.bat/.exe`**（评审新增，Task 5）：P5 的启动器带扩展名，原来只比对
   裸名 `claude-remote`。收尾修复再加 `.com/.ps1`（不区分大小写）。
