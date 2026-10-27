# Windows 适配 · 子项目 2d：进程扫描与接管、引擎可执行文件、进程组、Codex daemon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Wrapper 在 Windows 上能发现外部 Claude/Codex 进程并正确判定会话归属（只读镜像/接管）、按 npm `.cmd` shim
正确启动引擎、按进程树终止子进程、识别 Codex 官方 daemon 的 AF_UNIX 控制 socket；把只在 Linux/macOS 存在的功能的测试
标注为平台专有；最后对子项目 2 的剩余基线做逐条分诊并刷新基线。

**Architecture:**
- 进程扫描沿用 `process_scan.py` 已有的 Darwin "快照后端"模式（spec §3.1）：`win32_process` 新增 PEB 读取
  （当前目录、环境变量），`process_scan` 新增 `win32_process_snapshot()`/`win32_process_info()`/`win32_process_cwds()`，
  行结构与 `DarwinProcessInfo` 相同。Claude 复用 Darwin 的快照判定函数（参数化快照与 cwd 读取器）；Codex 移植旧分支
  `b7eafef` 的 argv 判定，但排除规则改用上游的"精确自有进程 + npm launcher 的原生子进程"，不再排除整棵后代树。
- 可执行文件：`platform.executables` 新增 `default_cli_path()`、`command_name()`；所有 `[codex_bin, …]` 启动点改为
  `[*executables.command_for(codex_bin), …]`（POSIX 上 `command_for` 返回 `[path]`，argv 不变）。
- 进程组：所有 `start_new_session=True`/`os.killpg` 改为 `procs.popen_group`/`create_subprocess_exec_group`/`killpg`；
  Windows 上 `SIGTERM` 等于立即强杀，所以需要优雅退出的地方先关 stdin 等待（`procs.SIGTERM_IS_GRACEFUL`）。
- Codex daemon：`socket_identity` 用 `files.is_unix_socket` 与 `perms.*_info`；测试夹具用 P2b 的 `tests/unix_socket.py`
  绑定真实 Windows AF_UNIX 监听；只有"Python 自己起 AF_UNIX 服务端"的用例标 `posix_only`。

**Tech Stack:** Python 3.13、ctypes（kernel32/ntdll/ws2_32）、pywin32 312、pytest 9。

**Spec:** `docs/superpowers/specs/2026-10-27-windows-compat-design.md`（§1 非目标、§3、§3.1"进程扫描""进程属主"、§7 skip 规则、§8、§9）；
`docs/superpowers/plans/2026-10-27-windows-compat-p1-handoff.md`（"进程"一节、`rlimit_exec` 约定）；
旧分支参考：`git show b7eafef`（Codex rollout holder 扫描）、`git show e136a75`。

## Global Constraints

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
- 前置：P2a、P2b、P2c 已合入 `windows-compat-v2`；本计划从其切分支 `win/2d-processes`。
- 文件归属同 P2b：不得修改 P3/P4/P5 的文件；`tests/conftest.py`、`pytest.ini` 不改。
- 平台分派点只允许出现在已有的分派位置：`process_scan.py`、`claude_external.claude_session_holders`、
  `codex_external.writable_rollout_holders` 中紧跟现有 `sys.platform == "darwin" and proc_root == "/proc"` 分支之后、
  形如 `if IS_WINDOWS and proc_root == "/proc":` 的分支（spec §3.1 指定沿用 Darwin 后端模式）。其余业务代码不新增平台分支。
- Codex 启动在 Windows 上绝不经过 `rlimit_exec.py`（handoff）；已有的 `os.name == "posix"` 守卫保持。
- `killpg` 不校验身份：调用方只对自己用 `popen_group`/`create_subprocess_exec_group` 启动的 pid 调用它。
- 业务模块写 `from cc_remote.platform import files as platform_files`；`procs`、`perms`、`executables` 直接导入模块。
- 测试 skip 只能用 `posix_only(reason=…)`、`windows_only(reason=…)`、`requires_symlink(reason=…)`；每个新 skip 都要能对应到
  spec §1 非目标、§7 允许的 POSIX 专有行为（`SO_PEERCRED`、`/proc`、systemd、launchd、cgroup、launch barrier），或"夹具必须建符号链接"。

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `cc_remote/platform/win32_process.py` | 修改 | 新增 `process_parameters(pid)`：从 PEB 读当前目录和环境块 |
| `cc_remote/platform/executables.py` | 修改 | 新增 `default_cli_path(name)`、`command_name(arg)` |
| `cc_remote/platform/procs.py` | 修改 | 新增 `SIGTERM_IS_GRACEFUL` |
| `cc_remote/platform/files.py` | 修改 | 新增 `create_symlink_entry(target, path)`（Git 风格的符号链接回退） |
| `cc_remote/wrapper/process_scan.py` | 修改 | Windows 快照、单进程信息、cwd 批量读取、环境变量读取 |
| `cc_remote/wrapper/codex_external.py` | 修改 | `command_name`；`_win32_writable_rollout_holders`；分派 |
| `cc_remote/wrapper/claude_external.py` | 修改 | 快照后端参数化；分派 |
| `cc_remote/config.py`、`cc_remote/wrapper/{codex_handle,codex_models,codex_rpc,codex_readiness,codex_daemon,codex_runtime,claude_runtime}.py`、`cc_remote/codex_daemon_restart.py` | 修改 | 默认 CLI 路径；`command_for` 启动；`nofile` 在 Windows 上为 no-op |
| `cc_remote/wrapper/{claude_usage,git_diff,codex_handle,codex_readiness}.py`、`cc_remote/wrapper/machine.py`（`_run_office_conversion`）、`cc_remote/codex_daemon_restart.py` | 修改 | 进程组启动与终止 |
| `cc_remote/wrapper/codex_daemon.py` | 修改 | `socket_identity` 平台化 |
| `cc_remote/wrapper/codex_checkpoints.py` | 修改 | 恢复符号链接条目走 `files.create_symlink_entry` |
| `tests/test_platform_win32_process.py`、`test_platform_executables.py`、`test_platform_procs.py`、`test_platform_files.py`、`test_process_scan_windows.py`（新建） | 修改/新建 | 平台 helper 与快照测试 |
| `tests/test_codex_external.py`、`test_claude_external.py`、`test_codex_npm_ownership.py`、`test_child_env.py`、`test_wrapper_core_fixes.py`、`test_codex_daemon.py`、`test_codex_readiness.py`、`test_codex_rpc.py`、`test_codex_service.py`、`test_codex_launch_barrier.py`、`test_codex_app_tools.py`、`test_codex_desktop.py` 及 Task 7/8 列出的文件 | 修改 | 新行为测试、平台标记、夹具 |
| `docs/superpowers/windows-baseline.md`、`docs/superpowers/windows-baseline.json` | 修改 | Task 8 刷新子项目 2 部分 |

## 准备工作

```bash
cd /c/Users/xuhaodong/work/cc-remote-p2
git fetch origin
git switch -c win/2d-processes origin/windows-compat-v2
PYTHONUTF8=1 .venv/Scripts/python.exe -c "from cc_remote.platform.dirs import DirHandle; from tests.unix_socket import unix_listener; print('ok')"
```

---

### Task 1: PEB 读取与 Windows 进程快照

基线：本任务为 Task 2、3 提供接口；直接相关的是 `tests/test_claude_external.py`、`tests/test_codex_external.py`、
`tests/test_codex_npm_ownership.py` 中因"Windows 走 /proc 分支、扫描恒不完整"失败的条目（Task 2、3 验收）。
验收：新测试在 Windows 通过、在 Linux 跳过或通过。

**Files:**
- Modify: `cc_remote/platform/win32_process.py`
- Modify: `cc_remote/wrapper/process_scan.py`（新增函数；`process_command_environment_value` 加 Windows 分支）
- Test: `tests/test_platform_win32_process.py`、`tests/test_process_scan_windows.py`（新建）

**Interfaces:**
- Consumes: `win32_process.creation_time`、`argv`、`parent_map`（P1）。
- Produces:
  - `win32_process.process_parameters(pid: int) -> tuple[str, tuple[str, ...]] | None`——
    `(normpath 后的当前目录, 环境变量 "K=V" 条目)`；进程不可打开、是 WOW64、读取失败或越界时返回 None。
    读取上限：当前目录 32 KiB，环境块 `process_scan.MAX_ENVIRON_BYTES`（256 KiB）。
  - `process_scan.win32_tty(args: tuple[bytes, ...]) -> int`——Windows 无控制终端可查：argv 含 `app-server`、`mcp-server`、
    `exec`、`execpolicy` 之一记 0（headless），否则记 1（交互）。纯函数，各平台可测。
  - `process_scan.win32_process_info(pid: int) -> DarwinProcessInfo | None`——`(ProcessIdentity, ppid, win32_tty(args), args)`，
    前后两次 `creation_time` 一致才返回。
  - `process_scan.win32_process_snapshot() -> tuple[list[DarwinProcessInfo], bool]`——Toolhelp32 全量快照；快照失败或
    超过 `MAX_PROC_SCAN` 时 `([], False)`；不能打开的受保护/他人进程跳过且不使扫描不完整（与旧分支和 handoff 一致）。
  - `process_scan.win32_process_cwds(pids: Collection[int]) -> tuple[dict[int, str], bool]`——与
    `claude_external._darwin_process_cwds` 契约相同：某个仍存活的进程读不到 cwd 时 `complete=False`。
  - `process_scan.process_command_environment_value(...)`：Windows（`proc_root == "/proc"`）从 PEB 读环境，契约不变。

- [ ] **Step 1: 写失败测试**

`tests/test_platform_win32_process.py` 末尾追加：

```python
@pytest.mark.windows_only(reason="the PEB only exists on Windows")
def test_process_parameters_reads_child_cwd_and_environment(tmp_path) -> None:
    import os
    import subprocess

    from cc_remote.platform import win32_process

    child = subprocess.Popen(
        ["ping", "-n", "30", "127.0.0.1"], cwd=tmp_path,
        env={**os.environ, "CC_REMOTE_PEB_PROBE": "yes"},
        stdout=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        params = None
        while params is None and time.monotonic() < deadline:
            params = win32_process.process_parameters(child.pid)
            time.sleep(0.05)
        assert params is not None
        cwd, environment = params
        assert os.path.normcase(os.path.realpath(cwd)) == os.path.normcase(os.path.realpath(tmp_path))
        assert "CC_REMOTE_PEB_PROBE=yes" in environment
    finally:
        child.kill()
        child.wait(timeout=10)


@pytest.mark.windows_only(reason="the PEB only exists on Windows")
def test_process_parameters_of_missing_pid_is_none() -> None:
    from cc_remote.platform import win32_process

    assert win32_process.process_parameters(0) is None
    assert win32_process.process_parameters(0x7FFFFFF0) is None
```

（若该文件顶部尚未 `import time`，补上。）新建 `tests/test_process_scan_windows.py`：

```python
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from cc_remote.wrapper import process_scan


@pytest.mark.parametrize(("args", "expected"), [
    ((b"C:/npm/codex.exe", b"resume", b"sid"), 1),
    ((b"C:/npm/codex.exe", b"app-server", b"--stdio"), 0),
    ((b"node.exe", b"codex.js", b"app-server", b"proxy"), 0),
    ((b"codex.exe", b"exec", b"hi"), 0),
    ((b"codex.exe", b"mcp-server"), 0),
    ((), 1),
])
def test_win32_tty_classifies_headless_codex_commands(args, expected) -> None:
    assert process_scan.win32_tty(args) == expected


@pytest.mark.windows_only(reason="Toolhelp32 snapshots only exist on Windows")
def test_snapshot_contains_a_live_child_with_argv_and_parent(tmp_path) -> None:
    child = subprocess.Popen(["ping", "-n", "30", "127.0.0.1"], cwd=tmp_path,
                             stdout=subprocess.DEVNULL)
    try:
        time.sleep(0.3)
        rows, complete = process_scan.win32_process_snapshot()
        assert complete is True
        by_pid = {row[0].pid: row for row in rows}
        identity, ppid, tty, args = by_pid[child.pid]
        assert ppid == os.getpid()
        assert os.path.basename(os.fsdecode(args[0])).lower().startswith("ping")
        assert tty == 1
        assert process_scan.win32_process_info(child.pid) == by_pid[child.pid]
        cwds, cwd_complete = process_scan.win32_process_cwds([child.pid])
        assert cwd_complete is True
        assert os.path.normcase(cwds[child.pid]) == os.path.normcase(os.path.realpath(tmp_path))
    finally:
        child.kill()
        child.wait(timeout=10)


@pytest.mark.windows_only(reason="PEB environment reads only exist on Windows")
def test_environment_value_reads_one_key_from_exact_identity(tmp_path) -> None:
    child = subprocess.Popen(
        ["ping", "-n", "30", "127.0.0.1"],
        env={**os.environ, "CODEX_HOME": str(tmp_path)}, stdout=subprocess.DEVNULL)
    try:
        time.sleep(0.3)
        identity = process_scan.process_identity(child.pid)
        assert identity is not None
        complete, args, value = process_scan.process_command_environment_value(
            identity, "CODEX_HOME")
        assert complete is True and args and value == str(tmp_path)
        complete, _args, missing = process_scan.process_command_environment_value(
            identity, "CC_REMOTE_DEFINITELY_ABSENT")
        assert complete is True and missing is None
        stale = process_scan.ProcessIdentity(identity.pid, identity.start_ticks + 1)
        assert process_scan.process_command_environment_value(stale, "CODEX_HOME") == (
            False, None, None)
    finally:
        child.kill()
        child.wait(timeout=10)


@pytest.mark.windows_only(reason="Toolhelp32 snapshots only exist on Windows")
def test_cwds_of_exited_process_stay_complete() -> None:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=10)
    cwds, complete = process_scan.win32_process_cwds([child.pid])
    assert cwds == {} and complete is True
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_platform_win32_process.py tests/test_process_scan_windows.py -p no:xdist -o addopts="" -q`
Expected: FAIL：`AttributeError: module 'cc_remote.platform.win32_process' has no attribute 'process_parameters'`、
`module 'cc_remote.wrapper.process_scan' has no attribute 'win32_tty'`。

- [ ] **Step 3: 实现 `win32_process.process_parameters`**

`cc_remote/platform/win32_process.py` 中在 `_kernel32.LocalFree...` 之后补充原型，并在文件末尾追加函数：

```python
_PROCESS_VM_READ = 0x0010
_PROCESS_BASIC_INFORMATION = 0
_MAX_CWD_BYTES = 32 * 1024
_MAX_ENVIRONMENT_BYTES = 256 * 1024

_kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
_kernel32.ReadProcessMemory.restype = wintypes.BOOL
_kernel32.IsWow64Process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
_kernel32.IsWow64Process.restype = wintypes.BOOL


class _ProcessBasicInformation(ctypes.Structure):
    _fields_ = [
        ("ExitStatus", ctypes.c_long),
        ("PebBaseAddress", ctypes.c_void_p),
        ("AffinityMask", ctypes.c_size_t),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_size_t),
        ("InheritedFromUniqueProcessId", ctypes.c_size_t),
    ]


def _read(handle: int, address: int, size: int) -> bytes | None:
    if address <= 0 or size < 0:
        return None
    buffer = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    if not _kernel32.ReadProcessMemory(
            handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(read)):
        return None
    return buffer.raw if read.value == size else None


def process_parameters(pid: int) -> tuple[str, tuple[str, ...]] | None:
    """Current directory and environment of a 64-bit process, from its PEB.

    Offsets are those of the x64 PEB and RTL_USER_PROCESS_PARAMETERS
    (ProcessParameters +0x20; CurrentDirectory.DosPath +0x38; Environment
    +0x80; EnvironmentSize +0x3F0). WOW64 targets, unreadable memory and
    oversized blocks return None; callers recheck the process identity.
    """
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return None
    handle = _open(pid, _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_VM_READ)
    if handle is None:
        return None
    try:
        wow64 = wintypes.BOOL(False)
        if not _kernel32.IsWow64Process(handle, ctypes.byref(wow64)) or wow64.value:
            return None
        basic = _ProcessBasicInformation()
        returned = wintypes.ULONG(0)
        if _ntdll.NtQueryInformationProcess(
                handle, _PROCESS_BASIC_INFORMATION, ctypes.byref(basic),
                ctypes.sizeof(basic), ctypes.byref(returned)) != 0:
            return None
        raw = _read(handle, int(basic.PebBaseAddress or 0) + 0x20, 8)
        if raw is None:
            return None
        parameters = int.from_bytes(raw, "little")
        raw = _read(handle, parameters + 0x38, 16)
        if raw is None:
            return None
        length = int.from_bytes(raw[0:2], "little")
        if length % 2 or length > _MAX_CWD_BYTES:
            return None
        text = _read(handle, int.from_bytes(raw[8:16], "little"), length)
        raw_env = _read(handle, parameters + 0x80, 8)
        raw_size = _read(handle, parameters + 0x3F0, 8)
        if text is None or raw_env is None or raw_size is None:
            return None
        size = int.from_bytes(raw_size, "little")
        if size % 2 or size > _MAX_ENVIRONMENT_BYTES:
            return None
        block = _read(handle, int.from_bytes(raw_env, "little"), size)
        if block is None:
            return None
        cwd = os.path.normpath(text.decode("utf-16-le"))
        environment = tuple(
            item for item in block.decode("utf-16-le", "replace").split("\0") if item)
        return cwd, environment
    finally:
        _kernel32.CloseHandle(handle)
```

- [ ] **Step 4: 实现 `process_scan` 的 Windows 函数**

`cc_remote/wrapper/process_scan.py` 顶部 `from typing` 若无则加 `from collections.abc import Collection`。在
`darwin_process_snapshot` 之后加入：

```python
_WIN32_HEADLESS_ARGS = frozenset({b"app-server", b"mcp-server", b"exec", b"execpolicy"})


def win32_tty(args: tuple[bytes, ...]) -> int:
    """Stand-in for tty_nr on Windows, which has no controlling terminal to read.

    Codex helpers that never own a terminal (app-server, proxies, exec, MCP)
    count as headless (0); every other process counts as interactive (1).
    The ownership predicates only compare tty_nr with 0, so this reproduces
    their POSIX classification from argv alone.
    """
    return 0 if _WIN32_HEADLESS_ARGS.intersection(args) else 1


def _win32_row(pid: int, ppid: int) -> DarwinProcessInfo | None:
    from cc_remote.platform import win32_process

    before = win32_process.creation_time(pid)
    if before is None:
        return None
    args = win32_process.argv(pid)
    if args is None or win32_process.creation_time(pid) != before:
        return None
    if sum(len(arg) + 1 for arg in args) > MAX_CMDLINE_BYTES:
        return None
    return ProcessIdentity(pid, before), ppid, win32_tty(args), args


def win32_process_info(pid: int) -> DarwinProcessInfo | None:
    """One exact Windows process row, or None once it exited or is unreadable."""
    from cc_remote.platform import win32_process

    parents = win32_process.parent_map()
    if parents is None or pid not in parents:
        return None
    return _win32_row(pid, parents[pid])


def win32_process_snapshot() -> tuple[list[DarwinProcessInfo], bool]:
    """Windows counterpart of :func:`darwin_process_snapshot` (same row shape).

    Processes that cannot be opened (protected, elevated or another user's)
    are skipped without making the scan incomplete: they are not this login's
    Claude/Codex clients, and failing closed on them would make every session
    read-only forever.
    """
    from cc_remote.platform import win32_process

    parents = win32_process.parent_map()
    if parents is None or len(parents) > MAX_PROC_SCAN:
        return [], False
    rows: list[DarwinProcessInfo] = []
    for pid, ppid in parents.items():
        if pid in (0, 4):  # System Idle Process, System
            continue
        row = _win32_row(pid, ppid)
        if row is not None:
            rows.append(row)
    return rows, True


def win32_process_cwds(pids: Collection[int]) -> tuple[dict[int, str], bool]:
    """Current directories by pid; incomplete when a live process hides its cwd."""
    from cc_remote.platform import win32_process

    result: dict[int, str] = {}
    complete = True
    for pid in pids:
        before = win32_process.creation_time(pid)
        if before is None:
            continue
        params = win32_process.process_parameters(pid)
        if params is None or win32_process.creation_time(pid) != before:
            if win32_process.creation_time(pid) == before:
                complete = False
            continue
        result[pid] = os.path.realpath(params[0])
    return result, complete
```

在 `process_command_environment_value` 中，参数校验之后、`proc_dir = Path(proc_root) / str(identity.pid)` 之前插入：

```python
    if IS_WINDOWS and proc_root == "/proc":
        from cc_remote.platform import win32_process

        if win32_process.creation_time(identity.pid) != identity.start_ticks:
            return False, None, None
        args = win32_process.argv(identity.pid)
        params = win32_process.process_parameters(identity.pid)
        if (args is None or params is None
                or win32_process.creation_time(identity.pid) != identity.start_ticks):
            return False, None, None
        environment = tuple(os.fsencode(item) for item in params[1])
        if sum(len(item) + 1 for item in environment) > MAX_ENVIRON_BYTES:
            return False, None, None
        complete, value = _environment_value(environment, key)
        return complete, args, value
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_platform_win32_process.py tests/test_process_scan_windows.py tests/test_platform_harness.py -p no:xdist -o addopts="" -q`
Expected: 全部 PASS（Linux 上 windows_only 用例 SKIPPED，`test_win32_tty_*` PASS）。

- [ ] **Step 6: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote/platform/win32_process.py cc_remote/wrapper/process_scan.py tests/test_platform_win32_process.py tests/test_process_scan_windows.py
git add cc_remote/platform/win32_process.py cc_remote/wrapper/process_scan.py tests/test_platform_win32_process.py tests/test_process_scan_windows.py
git commit -F - <<'EOF'
feat(wrapper): add Windows process snapshots with PEB cwd and environment

- win32_process.process_parameters reads a 64-bit process's current directory and environment from its PEB
- process_scan exposes Darwin-shaped Windows snapshots, single-process rows and batched cwd reads
- process_command_environment_value reads account roots from the PEB on Windows

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: Codex rollout 归属扫描（Windows 后端）

基线：`tests/test_codex_external.py`（"other" 5 条：`assert 1 == 2`、`(False, None) == (True, '...control.sock')`、
`None == 'C:\\tmp\\codex-stack.sock'`）、`tests/test_codex_npm_ownership.py`、以及 Wrapper 在 Windows 上 Codex 会话永远只读
（旧分支 `b7eafef` 提交说明中的现象）。

**Files:**
- Modify: `cc_remote/platform/executables.py`（`command_name`）
- Modify: `cc_remote/wrapper/codex_external.py`
- Test: `tests/test_platform_executables.py`、`tests/test_codex_external.py`

**Interfaces:**
- Consumes: Task 1 的 `win32_process_snapshot`、`win32_process_info`。
- Produces:
  - `executables.command_name(arg: bytes) -> bytes`——POSIX：`arg.rsplit(b"/", 1)[-1]`（与上游逐字等价）；Windows：按 `\` 和
    `/` 取最后一段并转小写。
  - `codex_external._win32_writable_rollout_holders(paths: Mapping[str, str], own_processes: Iterable[ProcessIdentity], *,
    process_snapshot: tuple[list[DarwinProcessInfo], bool] | None = None,
    read_process: Callable[[int], DarwinProcessInfo | None] | None = None) -> HolderScan`
  - `writable_rollout_holders(...)` 签名不变；Windows 且 `proc_root == "/proc"` 时分派到上面的函数，`darwin_snapshot` 参数作为
    `process_snapshot` 传入（machine 已有的跨 profile 快照复用因此也适用于 Windows）。

- [ ] **Step 1: 写失败测试**

`tests/test_platform_executables.py` 末尾追加：

```python
@pytest.mark.parametrize("arg", [b"codex", b"/usr/bin/codex", b"./codex"])
def test_command_name_takes_the_last_posix_component(arg) -> None:
    assert executables.command_name(arg) == b"codex"


@pytest.mark.windows_only(reason="backslash separators and case folding are Windows path rules")
def test_command_name_handles_windows_paths() -> None:
    assert executables.command_name(b"C:\\Program Files\\nodejs\\node.EXE") == b"node.exe"
    assert executables.command_name(b"C:/npm/node_modules/@openai/codex/bin/codex.js") == b"codex.js"
```

（若该文件没有 `import pytest`，补上。）`tests/test_codex_external.py` 末尾追加（行用 `/` 分隔，两个平台都能运行）：

```python
def _row(pid, ppid, args, start=100):
    from cc_remote.wrapper.process_scan import ProcessIdentity, win32_tty

    args = tuple(args)
    return ProcessIdentity(pid, start), ppid, win32_tty(args), args


def _win32_scan(paths, rows, own=()):
    from cc_remote.wrapper import codex_external

    by_pid = {row[0].pid: row for row in rows}
    return codex_external._win32_writable_rollout_holders(
        paths, own, process_snapshot=(rows, True), read_process=by_pid.get)


def test_win32_scan_binds_resume_tui_as_logical_holder(tmp_path):
    rollout = tmp_path / "rollout-2026-10-27T00-00-00-sid-1.jsonl"
    rollout.write_text("{}\n")
    tui = _row(20, 1, [b"C:/npm/codex.exe", b"resume", b"sid-1"])
    scan = _win32_scan({"sid-1": str(rollout)}, [tui])
    assert scan.complete is True
    assert scan.holders["sid-1"] == {tui[0]}
    assert scan.logical_holders["sid-1"] == {tui[0]}
    assert tui[0] in scan.client_proxies


def test_win32_scan_ignores_non_codex_processes_naming_the_rollout(tmp_path):
    rollout = tmp_path / "rollout-x-sid-1.jsonl"
    rollout.write_text("{}\n")
    editor = _row(21, 1, [b"C:/Windows/notepad.exe", os.fsencode(rollout)])
    scan = _win32_scan({"sid-1": str(rollout)}, [editor])
    assert scan.holders["sid-1"] == set()


def test_win32_scan_excludes_wrapper_owned_npm_server(tmp_path):
    rollout = tmp_path / "rollout-x-sid-1.jsonl"
    rollout.write_text("{}\n")
    launcher = _row(30, 1, [b"C:/nodejs/node.exe", b"C:/npm/codex.js",
                            b"app-server", b"--stdio", b"sid-1"], start=100)
    native = _row(31, 30, [b"C:/npm/codex.exe", b"app-server", b"--stdio", b"sid-1"],
                  start=101)
    scan = _win32_scan({"sid-1": str(rollout)}, [launcher, native], own=(launcher[0],))
    assert scan.holders["sid-1"] == set()


def test_win32_scan_marks_foreign_headless_server_passive(tmp_path):
    rollout = tmp_path / "rollout-x-sid-1.jsonl"
    rollout.write_text("{}\n")
    server = _row(40, 1, [b"C:/npm/codex.exe", b"app-server", b"--stdio",
                          os.fsencode(rollout)])
    scan = _win32_scan({"sid-1": str(rollout)}, [server])
    assert scan.holders["sid-1"] == {server[0]}
    assert scan.passive_holders["sid-1"] == {server[0]}
    assert scan.private_holders["sid-1"] == {server[0]}


def test_win32_scan_reports_incomplete_snapshot(tmp_path):
    from cc_remote.wrapper import codex_external

    scan = codex_external._win32_writable_rollout_holders(
        {"sid-1": str(tmp_path / "r.jsonl")}, (), process_snapshot=([], False),
        read_process=lambda _pid: None)
    assert scan.complete is False
```

（若文件顶部没有 `import os`，补上。）

- [ ] **Step 2: 运行测试，确认失败**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_platform_executables.py tests/test_codex_external.py -p no:xdist -o addopts="" -q -k "command_name or win32"`
Expected: FAIL：`AttributeError: ... has no attribute 'command_name'` / `'_win32_writable_rollout_holders'`。

- [ ] **Step 3: 实现**

`cc_remote/platform/executables.py` 末尾追加：

```python
_WINDOWS_SEPARATORS = re.compile(rb"[\\/]")


def command_name(arg: bytes) -> bytes:
    """The executable name in one argv element, comparable across platforms.

    POSIX keeps the historical ``rsplit(b"/")`` exactly. Windows paths use
    either separator and are case-insensitive, so they are split on both and
    lower-cased (``...\\node.EXE`` -> ``node.exe``).
    """
    if not IS_WINDOWS:
        return arg.rsplit(b"/", 1)[-1]
    return _WINDOWS_SEPARATORS.split(arg)[-1].lower()
```

`cc_remote/wrapper/codex_external.py`：import 区加 `from cc_remote.platform import IS_WINDOWS, executables`，并在从
`process_scan` 的导入列表里加入 `win32_process_info`、`win32_process_snapshot`。把文件中全部 8 处
`X.rsplit(b"/", 1)[-1]`（`_codex_resume_sids`、`_is_interactive_codex_tui`、`_fold_npm_tui_launchers` 3 处、
`_is_owned_npm_app_server_child` 3 处）替换为 `executables.command_name(X)`，例如：

```python
    command_names = {executables.command_name(arg) for arg in args[:2]}
```

```python
        if (args and executables.command_name(args[0]) in {b"codex", b"codex.exe"}
                and _is_interactive_codex_tui(args, tty_nr)):
```

在 `_darwin_writable_rollout_holders` 之后加入：

```python
_CODEX_COMMANDS = frozenset({b"codex", b"codex.exe", b"codex.js"})


def _win32_writable_rollout_holders(
    paths: Mapping[str, str],
    own_processes: Iterable[ProcessIdentity],
    *,
    process_snapshot: tuple[list[DarwinProcessInfo], bool] | None = None,
    read_process: Callable[[int], DarwinProcessInfo | None] | None = None,
) -> HolderScan:
    """Attribute rollouts to foreign Codex processes from argv on Windows.

    Windows has no cheap way to list another process's open handles, so the
    writable-FD evidence of Linux/macOS is unavailable. A foreign Codex
    process naming a watched thread id or rollout file in argv owns it; an
    interactive ``codex resume SID`` TUI is logical evidence, as elsewhere.
    Exactly the wrapper-owned processes and the native server behind a
    wrapper-owned npm launcher are excluded; model-spawned tools are scanned.
    """
    result = {sid: set() for sid in paths}
    passive = {sid: set() for sid in paths}
    private = {sid: set() for sid in paths}
    logical = {sid: set() for sid in paths}
    client_proxies: dict[ProcessIdentity, int] = {}
    sid_by_arg = {sid.encode(): sid for sid in paths}
    rollout_names = {
        sid: os.fsencode(os.path.basename(path))
        for sid, path in paths.items() if path
    }
    snapshot = process_snapshot or win32_process_snapshot()
    reader = read_process or win32_process_info
    processes, complete = snapshot
    own = set(own_processes)
    own_roots = {identity.pid: identity for identity in own}
    for row in processes:
        if row[0] not in own and _is_owned_npm_app_server_child(
            row, own_roots, read_process=reader,
        ):
            own.add(row[0])
    for identity, _ppid, tty_nr, args in processes:
        if identity in own or not args:
            continue
        if not {executables.command_name(arg) for arg in args[:2]} & _CODEX_COMMANDS:
            continue
        interactive_tui = _is_interactive_codex_tui(args, tty_nr)
        if _is_app_server_proxy(args, tty_nr) or interactive_tui:
            client_proxies[identity] = identity.start_ticks
        if interactive_tui:
            for sid in _codex_resume_sids(args, sid_by_arg):
                result[sid].add(identity)
                logical[sid].add(identity)
        matched: set[str] = set()
        for arg in args:
            sid = sid_by_arg.get(arg)
            if sid is not None:
                matched.add(sid)
                continue
            for watched, name in rollout_names.items():
                if name and name in arg:
                    matched.add(watched)
        if interactive_tui:
            matched.difference_update(_codex_resume_sids(args, sid_by_arg))
        for sid in matched:
            result[sid].add(identity)
            if _is_passive_app_server(Path(), tty_nr, args):
                passive[sid].add(identity)
                if not _is_managed_shared_app_server(args):
                    private[sid].add(identity)
    scan = HolderScan(
        result, complete, passive, client_proxies, private,
        logical_holders=logical,
        darwin_snapshot=snapshot,
    )
    _fold_npm_tui_launchers(
        scan, (row for row in processes if row[0] not in own),
        read_process=reader,
    )
    return scan
```

`writable_rollout_holders` 中，在 `if sys.platform == "darwin" and proc_root == "/proc": ...` 分支之后插入：

```python
    if IS_WINDOWS and proc_root == "/proc":
        return _win32_writable_rollout_holders(
            paths, own_processes, process_snapshot=darwin_snapshot)
```

- [ ] **Step 4: 运行测试**

Run: `for f in tests/test_platform_executables.py tests/test_codex_external.py tests/test_codex_npm_ownership.py tests/test_codex_shared_machine.py; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1 | tail -3 | sed "s|^|$f: |"; done`
Expected: 新用例 PASS；`tests/test_codex_external.py` 中构造假 procfs 并传 `proc_root=` 的用例行为不变。仍失败的、依赖真实 Linux
`/proc`（不传 `proc_root`、断言 `/proc` 专有结果）的用例，在其函数上加
`@pytest.mark.posix_only(reason="asserts the Linux /proc writable-FD scan")`；需要建符号链接的加 Task 7 的 `requires_symlink`。
其余失败必须修到通过。

- [ ] **Step 5: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote/platform/executables.py cc_remote/wrapper/codex_external.py tests/test_platform_executables.py tests/test_codex_external.py
git add cc_remote/platform/executables.py cc_remote/wrapper/codex_external.py tests/test_platform_executables.py tests/test_codex_external.py
git commit -F - <<'EOF'
feat(wrapper): attribute Codex rollouts to foreign processes on Windows

- Add executables.command_name so npm launcher and native Codex argv match Windows paths
- Scan Windows snapshots for resume TUIs and Codex processes naming a watched thread or rollout, excluding exactly the wrapper-owned servers
- Dispatch writable_rollout_holders to the Windows backend and share its snapshot across profiles

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3: Claude 会话归属扫描（Windows 后端）

基线：`tests/test_claude_external.py`（"other" 8：`assert 0 == 1` 6 等；`posix_os_calls` 1：
`test_claude_takeover_signals_only_same_identity_and_uid`）。

**Files:**
- Modify: `cc_remote/wrapper/claude_external.py`
- Test: `tests/test_claude_external.py`

**Interfaces:**
- Consumes: Task 1 的 `win32_process_snapshot`、`win32_process_cwds`。
- Produces: `claude_external._snapshot_claude_session_holders(paths, cwds, *, wrapper_pid, continue_bindings,
  continue_candidates, continue_resolver, config_dirs, native_session_ids, default_config_dir, owner_identities=(),
  snapshot_reader=None, cwd_reader=None) -> HolderScan`（由 `_darwin_claude_session_holders` 改名；两个 reader 为 None 时
  在调用时取模块级 `darwin_process_snapshot`/`_darwin_process_cwds`，保持现有 Darwin 测试的 monkeypatch 有效）。

- [ ] **Step 1: 写失败测试**

`tests/test_claude_external.py` 末尾追加：

```python
def test_claude_scan_on_windows_snapshot_binds_explicit_session(monkeypatch):
    from cc_remote.wrapper.process_scan import ProcessIdentity, win32_tty

    sid = "11111111-1111-4111-8111-111111111111"
    args = (b"C:/Users/u/.local/bin/claude.exe", b"--resume", sid.encode())
    row = (ProcessIdentity(50, 7), 1, win32_tty(args), args)
    monkeypatch.setattr(claude_external_module, "process_identity",
                        lambda pid: row[0] if pid == 50 else None)
    scan = claude_external_module._snapshot_claude_session_holders(
        {sid: "transcript.jsonl"}, {sid: "C:/work"},
        wrapper_pid=9,
        continue_bindings={}, continue_candidates={}, continue_resolver=None,
        config_dirs=None, native_session_ids=None, default_config_dir=None,
        snapshot_reader=lambda: ([row], True),
        cwd_reader=lambda _pids: ({50: "C:/work"}, True),
    )
    assert scan.complete is True
    assert scan.holders[sid] == {row[0]}


def test_claude_scan_on_windows_excludes_wrapper_descendants(monkeypatch):
    from cc_remote.wrapper.process_scan import ProcessIdentity, win32_tty

    sid = "11111111-1111-4111-8111-111111111111"
    args = (b"C:/sdk/claude.exe", b"--resume", sid.encode())
    child = (ProcessIdentity(60, 7), 9, win32_tty(args), args)
    monkeypatch.setattr(claude_external_module, "process_identity",
                        lambda pid: child[0] if pid == 60 else None)
    scan = claude_external_module._snapshot_claude_session_holders(
        {sid: "t.jsonl"}, {sid: "C:/work"}, wrapper_pid=9,
        continue_bindings={}, continue_candidates={}, continue_resolver=None,
        config_dirs=None, native_session_ids=None, default_config_dir=None,
        snapshot_reader=lambda: ([child], True),
        cwd_reader=lambda _pids: ({60: "C:/work"}, True),
    )
    assert scan.holders[sid] == set()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_claude_external.py -p no:xdist -o addopts="" -q -k windows`
Expected: FAIL：`AttributeError: module ... has no attribute '_snapshot_claude_session_holders'`。

- [ ] **Step 3: 实现**

1. 把 `def _darwin_claude_session_holders(` 改名为 `def _snapshot_claude_session_holders(`，参数表末尾（`owner_identities` 之后）加：

```python
    snapshot_reader: Callable[[], tuple[list[DarwinProcessInfo], bool]] | None = None,
    cwd_reader: Callable[[Collection[int]], tuple[dict[int, str], bool]] | None = None,
```

2. 函数体内 `snapshot, complete = darwin_process_snapshot()` 改为
   `snapshot, complete = (snapshot_reader or darwin_process_snapshot)()`；
   `process_cwds, cwd_scan_complete = _darwin_process_cwds(` 改为
   `process_cwds, cwd_scan_complete = (cwd_reader or _darwin_process_cwds)(`。
3. `claude_session_holders` 里原 `return _darwin_claude_session_holders(` 改为 `return _snapshot_claude_session_holders(`；在该
   darwin 分支之后插入：

```python
    if IS_WINDOWS and proc_root == "/proc":
        return _snapshot_claude_session_holders(
            paths,
            cwds,
            wrapper_pid=wrapper_pid,
            continue_bindings=bindings,
            continue_candidates=candidates,
            continue_resolver=continue_resolver,
            config_dirs=config_dirs,
            native_session_ids=native_session_ids,
            default_config_dir=default_config_dir,
            owner_identities=owner_identities,
            snapshot_reader=win32_process_snapshot,
            cwd_reader=win32_process_cwds,
        )
```

4. import：`from cc_remote.platform import IS_WINDOWS`；`process_scan` 导入列表加 `DarwinProcessInfo`、`win32_process_cwds`、
   `win32_process_snapshot`。

`test_claude_takeover_signals_only_same_identity_and_uid` 的失败在测试代码里调用了 `os.getuid()`：把该测试里的
`os.getuid()` 替换为 `current_uid()`（`from cc_remote.platform.identity import current_uid`）。

- [ ] **Step 4: 运行测试**

Run: `for f in tests/test_claude_external.py tests/test_claude_broker_machine.py tests/test_multisession.py; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1 | tail -3 | sed "s|^|$f: |"; done`
Expected: 新用例 PASS；`test_claude_external.py` 基线里的 "other"/`posix_os_calls` 条目通过。直接读取真实 Linux `/proc`
（不传 `proc_root`）并断言 procfs 结果的用例加 `@pytest.mark.posix_only(reason="asserts the Linux /proc Claude scan")`；
建符号链接的由 Task 7 处理。

- [ ] **Step 5: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote/wrapper/claude_external.py tests/test_claude_external.py
git add cc_remote/wrapper/claude_external.py tests/test_claude_external.py
git commit -F - <<'EOF'
feat(wrapper): detect foreign Claude CLI sessions on Windows

- Parameterize the snapshot-based Claude holder scan by snapshot and cwd readers
- Dispatch Windows scans to Toolhelp32 snapshots with PEB current directories

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: 引擎可执行文件解析与启动

基线：`tests/test_child_env.py` 的 "other" 5（`RuntimeError: CLAUDE_BIN must be an absolute path` 4、默认路径断言 1）；
`tests/test_codex_rpc.py` 的 "other" 1（`ValueError: invalid POSIX Codex RPC nofile limit`——产品缺陷：
`_CODEX_ARCHIVE_NOFILE_SOFT_LIMIT` 在所有平台都会传入，Windows 上归档因此失败）。

**Files:**
- Modify: `cc_remote/platform/executables.py`（`default_cli_path`）
- Modify: `cc_remote/config.py`（`_claude_bin`）
- Modify: `cc_remote/wrapper/codex_handle.py`（`_open_process`）、`codex_models.py`（`_rpc_model_list`）、
  `codex_rpc.py`（`_rpc_connection`、第二处 `create_subprocess_exec`、`nofile` 校验）、`codex_readiness.py`（`probe_proxy`）、
  `codex_daemon.py`（`_run_command`）、`codex_runtime.py`（`codex_version`）、`claude_runtime.py`（`probe_claude_cli_version`）、
  `cc_remote/codex_daemon_restart.py`（`subprocess.run([codex_bin, ...])` 与 `subprocess.Popen(argv, ...)`）
- Test: `tests/test_platform_executables.py`、`tests/test_child_env.py`、`tests/test_codex_rpc.py`

**Interfaces:**
- Consumes: `executables.find`、`executables.command_for`（P1）。
- Produces: `executables.default_cli_path(name: str) -> str`——POSIX：`str(Path.home() / ".local/bin" / name)`
  （与原 `str(Path.home() / ".local/bin/claude")` 相同）；Windows：依次取 `~/.local/bin/<name>.exe`（存在时）、PATH 上的安装
  （npm `.cmd` shim 能解成单个原生 exe 时返回该 exe，否则返回 shim 路径）、最后返回 `~/.local/bin/<name>.exe`。
  启动约定：所有引擎 argv 写成 `[*executables.command_for(binary), *rest]`。

- [ ] **Step 1: 写失败测试**

`tests/test_platform_executables.py` 末尾追加：

```python
@pytest.mark.posix_only(reason="the POSIX daily CLI path is fixed to ~/.local/bin")
def test_default_cli_path_is_the_local_bin_on_posix(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert executables.default_cli_path("claude") == str(tmp_path / ".local/bin/claude")


@pytest.mark.windows_only(reason="Windows prefers the native installer's .exe and unwraps npm shims")
def test_default_cli_path_on_windows(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "npm"))
    native = tmp_path / ".local" / "bin" / "claude.exe"
    assert executables.default_cli_path("claude") == str(native)  # nothing installed yet
    npm = tmp_path / "npm"
    (npm / "node_modules" / "pkg" / "bin").mkdir(parents=True)
    target = npm / "node_modules" / "pkg" / "bin" / "claude.exe"
    target.write_bytes(b"MZ")
    (npm / "claude.cmd").write_text(
        '@ECHO off\r\n"%dp0%\\node_modules\\pkg\\bin\\claude.exe"   %*\r\n', encoding="utf-8")
    assert executables.default_cli_path("claude") == str(target)
    native.parent.mkdir(parents=True)
    native.write_bytes(b"MZ")
    assert executables.default_cli_path("claude") == str(native)
```

`tests/test_child_env.py`：import 区加 `import sys` 与 `from tests.platform_paths import native_path`。
`WrapperConfig(claude_bin="/fake/claude")` 全部改为 `WrapperConfig(claude_bin=native_path("/fake/claude"))`。把
`test_claude_bin_defaults_to_daily_local_cli_and_can_be_configured` 开头改为：

```python
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    expected = str(home / ".local/bin/claude") + (".exe" if sys.platform == "win32" else "")
    assert WrapperConfig().claude_bin == expected

    # An explicitly empty dotenv value must not silently restore the SDK bundle.
    monkeypatch.setenv("CLAUDE_BIN", "   ")
    assert WrapperConfig().claude_bin == expected
```

`tests/test_codex_rpc.py`：给 `test_codex_rpc_can_raise_only_the_isolated_child_nofile_limit` 加
`@pytest.mark.posix_only(reason="RLIMIT_NOFILE and the rlimit_exec wrapper are POSIX-only")`，并追加：

```python
@pytest.mark.windows_only(reason="Windows has no RLIMIT_NOFILE; Codex starts unwrapped")
def test_codex_rpc_nofile_limit_is_a_no_op_on_windows(monkeypatch):
    seen = []

    async def fake_exec(*argv, **kwargs):
        seen.append(argv)
        raise OSError("stop after argv capture")

    monkeypatch.setattr(codex_rpc_module.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(codex_rpc_module, "_resolve_codex_bin", lambda: "C:/npm/codex.exe")
    with pytest.raises(OSError, match="stop after argv capture"):
        asyncio.run(codex_rpc_module.codex_rpc(
            "thread/archive", {"threadId": "t"}, nofile_soft_limit=4096))
    assert seen and seen[0][:3] == ("C:/npm/codex.exe", "app-server", "--stdio")
```

（`codex_rpc_module` 是该测试文件已有的 `from cc_remote.wrapper import codex_rpc as codex_rpc_module`。）

- [ ] **Step 2: 运行测试，确认失败**

Run: `for f in tests/test_platform_executables.py tests/test_child_env.py tests/test_codex_rpc.py; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1 | tail -4; done`
Expected: `default_cli_path` 不存在；`test_child_env.py` 默认路径断言失败；`nofile` 用例抛 `ValueError: invalid POSIX Codex RPC nofile limit`。

- [ ] **Step 3: 实现**

`cc_remote/platform/executables.py` 追加：

```python
def default_cli_path(name: str) -> str:
    """The user's daily ``name`` CLI as an absolute path.

    POSIX keeps the fixed ``~/.local/bin/<name>``. Windows prefers the native
    installer's ``~/.local/bin/<name>.exe``, then a PATH install (an npm
    ``.cmd`` shim is unwrapped when it launches one native executable), and
    otherwise reports the native installer location.
    """
    if not IS_WINDOWS:
        return str(Path.home() / ".local/bin" / name)
    native = Path.home() / ".local" / "bin" / f"{name}.exe"
    if native.is_file():
        return str(native)
    found = shutil.which(name)
    if found:
        command = command_for(found)
        return command[0] if len(command) == 1 else found
    return str(native)
```

`cc_remote/config.py` 的 `_claude_bin`：

```python
def _claude_bin() -> str:
    """Use the user's normal Claude Code install unless explicitly overridden."""
    configured = _env("CLAUDE_BIN", "").strip()
    return configured or executables.default_cli_path("claude")
```

（import：`from cc_remote.platform import executables`。）

启动点逐个改为 `command_for`：

| 文件/函数 | 原 | 新 |
|---|---|---|
| `codex_handle._open_process` | `process_argv = argv` | `process_argv = [*executables.command_for(argv[0]), *argv[1:]]`（随后 POSIX 的 rlimit 包装改为包在 `*process_argv` 外：`[sys.executable, _RLIMIT_EXEC, str(...), *process_argv]`） |
| `codex_models._rpc_model_list` | `binp, "app-server", "--stdio",` | `*executables.command_for(binp), "app-server", "--stdio",` |
| `codex_rpc._rpc_connection` | `app_server_argv = (bin_path, "app-server", "--stdio")` | `app_server_argv = (*executables.command_for(bin_path), "app-server", "--stdio")` |
| `codex_rpc` 第二处 `create_subprocess_exec(` | `bin_path,` `"app-server",` `"--stdio",` | `*executables.command_for(bin_path),` `"app-server",` `"--stdio",` |
| `codex_readiness.probe_proxy` | `binary, "app-server", "proxy", "--sock", socket_path,` | `*executables.command_for(binary), "app-server", "proxy", "--sock", socket_path,` |
| `codex_daemon._run_command` | `command_argv = argv` | `command_argv = (*executables.command_for(argv[0]), *argv[1:])`；POSIX lifecycle 分支的 rlimit 包装改为 `(sys.executable, _RLIMIT_EXEC, str(_DAEMON_NOFILE_SOFT_LIMIT), *command_argv)` |
| `codex_runtime.codex_version` | `[path, "--version"]` | `[*executables.command_for(path), "--version"]` |
| `claude_runtime.probe_claude_cli_version` | `[path, "--version"]` | `[*executables.command_for(path), "--version"]` |
| `codex_daemon_restart`（restart 命令） | `[codex_bin, "app-server", "daemon", "restart"]` | `[*executables.command_for(codex_bin), "app-server", "daemon", "restart"]` |
| `codex_daemon_restart`（后台 `Popen(argv, ...)`） | `argv` | `[*executables.command_for(argv[0]), *argv[1:]]` |

`codex_rpc` 的 `nofile` 校验：把条件里的 `or os.name != "posix"` 删除，并在该 `if ...: raise ValueError(...)` 之后加：

```python
    if os.name != "posix":
        # Windows has no RLIMIT_NOFILE and must not run Codex through
        # rlimit_exec; the isolated child simply starts unwrapped.
        nofile_soft_limit = None
```

（这是对已有平台判断的收窄，不是新增分支。）各文件补 `from cc_remote.platform import executables`。

- [ ] **Step 4: 运行测试**

Run: `for f in tests/test_platform_executables.py tests/test_child_env.py tests/test_codex_rpc.py tests/test_codex_daemon_restart.py tests/test_codex_models.py; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1 | tail -2 | sed "s|^|$f: |"; done`
Expected: 全部 0 failed（Linux 上 `command_for` 返回原路径，argv 断言不变）。

- [ ] **Step 5: 本机冒烟（只读、不调用模型）**

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe - <<'EOF'
from cc_remote.platform import executables
from cc_remote.wrapper.codex_runtime import resolve_codex_bin, codex_version
from cc_remote.wrapper.claude_runtime import probe_claude_cli_version
claude = executables.default_cli_path("claude")
print("claude:", claude, probe_claude_cli_version(claude))
codex = resolve_codex_bin()
print("codex:", codex, executables.command_for(codex), codex_version(codex))
EOF
```

Expected: 打印本机 Claude（2.1.278）与 Codex（0.164.0）的版本；`command_for(codex)` 为 `[node.exe, ...codex.js]`。

- [ ] **Step 6: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote tests/test_platform_executables.py tests/test_child_env.py tests/test_codex_rpc.py
git add cc_remote/platform/executables.py cc_remote/config.py cc_remote/wrapper/codex_handle.py cc_remote/wrapper/codex_models.py \
  cc_remote/wrapper/codex_rpc.py cc_remote/wrapper/codex_readiness.py cc_remote/wrapper/codex_daemon.py \
  cc_remote/wrapper/codex_runtime.py cc_remote/wrapper/claude_runtime.py cc_remote/codex_daemon_restart.py \
  tests/test_platform_executables.py tests/test_child_env.py tests/test_codex_rpc.py
git commit -F - <<'EOF'
fix(wrapper): resolve and launch engine CLIs through npm shims on Windows

- Default CLAUDE_BIN to the native .exe or the executable behind the npm shim on Windows; POSIX keeps ~/.local/bin/claude
- Launch every Codex and Claude CLI argv through executables.command_for, never through cmd.exe
- Treat the Codex RPC nofile limit as a no-op on Windows instead of rejecting archive requests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5: 进程组启动与终止（全部 `killpg`/`start_new_session` 调用点）

基线：`posix_os_calls` 中 `tests/test_codex_daemon.py` 的 7 条（`<module 'os' (frozen)> has no attribute 'killpg'`）、
`tests/test_wrapper_core_fixes.py` 的 `test_bounded_subprocess_discards_residual_output_without_communicate`、
`test_bounded_subprocess_output_has_wall_clock_timeout`、`test_git_diff_output_is_streamed_to_a_hard_limit`。

**Files:**
- Modify: `cc_remote/platform/procs.py`（`SIGTERM_IS_GRACEFUL`）
- Modify: `cc_remote/wrapper/claude_usage.py`、`codex_readiness.py`（`probe_proxy`）、`git_diff.py`（`bounded_process_output`）、
  `codex_handle.py`（`_open_process`、断开时的 `stop`）、`machine.py`（`_run_office_conversion`）、`cc_remote/codex_daemon_restart.py`
- Test: `tests/test_platform_procs.py`、`tests/test_wrapper_core_fixes.py`、`tests/test_codex_daemon.py`

**Interfaces:**
- Consumes: `procs.popen_group`、`procs.create_subprocess_exec_group`、`procs.killpg`、`procs.SIGTERM`、`procs.SIGKILL`（P1/P2a）。
- Produces: `procs.SIGTERM_IS_GRACEFUL: bool`——POSIX True（进程组收到可处理的 SIGTERM）；Windows False（`killpg` 任何信号都是
  `TerminateJobObject`）。需要优雅退出的调用方在 False 时先走引擎协议（关 stdin / shutdown RPC）并等待，再 `killpg`。

- [ ] **Step 1: 写失败测试**

`tests/test_platform_procs.py` 追加：

```python
def test_sigterm_grace_flag_matches_platform() -> None:
    assert procs.SIGTERM_IS_GRACEFUL is (sys.platform != "win32")
```

`tests/test_wrapper_core_fixes.py`：

- `test_bounded_subprocess_output_has_wall_clock_timeout` 里 `("sh", "-c", "sleep 10")` 改为
  `(sys.executable, "-c", "import time; time.sleep(10)")`（若无 `import sys` 则补上）。
- `test_bounded_subprocess_discards_residual_output_without_communicate` 里两处 monkeypatch 改为打在 `git_diff.procs` 上，并更新断言：

```python
    from cc_remote.platform import procs
    from cc_remote.wrapper import git_diff

    monkeypatch.setattr(git_diff.procs, "create_subprocess_exec_group", fake_spawn)
    monkeypatch.setattr(
        git_diff.procs, "killpg",
        lambda pgid, sig: signals.append((pgid, sig)),
    )
```

```python
    assert signals[0] == (process.pid, procs.SIGTERM)
```

`tests/test_codex_daemon.py`：三处 `monkeypatch.setattr(handle_module.os, "killpg", lambda *_args: None)` 改为
`monkeypatch.setattr(handle_module.procs, "killpg", lambda *_args: None)`。

- [ ] **Step 2: 运行测试，确认失败**

Run: `for f in tests/test_platform_procs.py tests/test_wrapper_core_fixes.py tests/test_codex_daemon.py; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line -k "grace or bounded_subprocess or git_diff_output or proxy" 2>&1 | tail -3; done`
Expected: `SIGTERM_IS_GRACEFUL` 不存在；`git_diff` 无 `procs` 属性；`handle_module` 无 `procs` 属性。

- [ ] **Step 3: 实现**

`cc_remote/platform/procs.py` 在 `SIGKILL = ...` 之后加：

```python
# POSIX groups receive a catchable SIGTERM; on Windows every killpg is an
# immediate TerminateJobObject, so callers that need a graceful exit must use
# the engine's own protocol (close stdin, shutdown RPC) before killpg.
SIGTERM_IS_GRACEFUL = not IS_WINDOWS
```

并把 `"SIGTERM_IS_GRACEFUL"` 加入 `__all__`。

`cc_remote/wrapper/claude_usage.py`：

```python
            process = await procs.create_subprocess_exec_group(
                *executables.command_for(command[0]), *command[1:],
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                cwd=Path.home(), env=env,
            )
```

`finally` 中 `os.killpg(process.pid, signal.SIGKILL)` 改为 `procs.killpg(process.pid, procs.SIGKILL)`（pid 是本函数刚用
`create_subprocess_exec_group` 启动的组长；Windows 上走 Job/墓碑，不会落到阻塞的 `taskkill`）。

`cc_remote/wrapper/codex_readiness.py` 的 `probe_proxy`：`asyncio.create_subprocess_exec(` 改为
`procs.create_subprocess_exec_group(` 并删去 `start_new_session=True,`；`os.killpg(process.pid, signal.SIGKILL)` 改为
`procs.killpg(process.pid, procs.SIGKILL)`（stdin 已先关闭，满足"优雅优先"）。

`cc_remote/wrapper/git_diff.py` 的 `bounded_process_output`：

```python
    proc = await procs.create_subprocess_exec_group(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
```

```python
    def stop_group(sig: int) -> None:
        try:
            procs.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
```

其后的 `stop_group(signal.SIGKILL)`（4 处）改为 `stop_group(procs.SIGKILL)`，`stop_group(signal.SIGTERM)` 改为
`stop_group(procs.SIGTERM)`。（截断时 diff 子进程不需要优雅退出，Windows 上直接终止 Job 是正确的。）

`cc_remote/wrapper/machine.py` 的 `_run_office_conversion`：`subprocess.Popen(` 改为 `procs.popen_group(` 并删去
`start_new_session=True,`；`os.killpg(process.pid, signal.SIGKILL)` 改为 `procs.killpg(process.pid, procs.SIGKILL)`。

`cc_remote/codex_daemon_restart.py` 的后台 `subprocess.Popen(...)`：改为 `procs.popen_group(...)` 并删去
`start_new_session=True,`（它要比父进程活得久——Job 不设 `KILL_ON_JOB_CLOSE`，与 POSIX 新会话语义一致）。

`cc_remote/wrapper/codex_handle.py`：

1. `_open_process` 里 `proc = await asyncio.create_subprocess_exec(` 改为 `proc = await procs.create_subprocess_exec_group(`，
   删去 `start_new_session=(os.name == "posix"),`；`self._process_group = proc.pid if os.name == "posix" else None` 改为
   `self._process_group = proc.pid`。
2. 断开流程中的 `stop`：

```python
            def stop(sig: int, *, force: bool = False) -> None:
                try:
                    if process_group is not None:
                        procs.killpg(process_group, sig)
                    elif proc.returncode is None:
                        (proc.kill() if force else proc.terminate())
                except ProcessLookupError:
                    pass
                except Exception as exc:
                    log.warning("codex process stop failed",
                                signal=getattr(sig, "name", str(sig)), error=str(exc))

            if proc.returncode is None and not procs.SIGTERM_IS_GRACEFUL:
                # Windows: SIGTERM would hard-kill the Job immediately. Let the
                # app-server/proxy exit on stdin EOF first, as it does on POSIX
                # after SIGTERM.
                if proc.stdin is not None:
                    proc.stdin.close()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    pass
            if proc.returncode is None:
                stop(signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    stop(procs.SIGKILL, force=True)
                    await proc.wait()
            # For stdio this also cleans tool descendants.  For daemon mode the
            # process group contains only this connection's proxy; the shared
            # app-server was started independently by the official manager.
            if process_group is not None:
                stop(procs.SIGKILL, force=True)
```

各文件补 `from cc_remote.platform import procs`（`claude_usage.py` 还要 `executables`），删除不再使用的 `signal` import。

- [ ] **Step 4: 确认 P2 文件已无直接调用**

Run: `bash -c 'grep -rnE "os\.killpg|start_new_session|signal\.SIGKILL" cc_remote --include=*.py | grep -vE "cc_remote/(platform|claude_service|claude_broker|tui|viewer|update|timed_tasks|async_tasks|task_)|claude_remote|claude_broker_handle|session_messages|codex_desktop|codex_app_tools"'`
Expected: 无输出（`codex_desktop`/`codex_app_tools` 是 macOS App 专有、spec §1 非目标，保持原样）。

- [ ] **Step 5: 复跑**

```bash
for f in tests/test_platform_procs.py tests/test_wrapper_core_fixes.py tests/test_codex_daemon.py tests/test_claude_usage.py tests/test_codex_rich_stream.py tests/test_codex_runtime_events.py tests/test_turn_changes.py; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1 | tail -2 | sed "s|^|$f: |"; done
```

Expected: 本任务列出的基线条目全部通过；`killpg`/`SIGKILL` 相关 `AttributeError` 不再出现。

- [ ] **Step 6: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote tests/test_platform_procs.py tests/test_wrapper_core_fixes.py tests/test_codex_daemon.py
git add cc_remote/platform/procs.py cc_remote/wrapper/claude_usage.py cc_remote/wrapper/codex_readiness.py cc_remote/wrapper/git_diff.py \
  cc_remote/wrapper/codex_handle.py cc_remote/wrapper/machine.py cc_remote/codex_daemon_restart.py \
  tests/test_platform_procs.py tests/test_wrapper_core_fixes.py tests/test_codex_daemon.py
git commit -F - <<'EOF'
fix(wrapper): start and stop helper and engine processes as platform groups

- Replace start_new_session and os.killpg with procs group spawns and killpg in every wrapper call site
- Let Codex app-servers exit on stdin EOF before the Windows Job is terminated
- Add procs.SIGTERM_IS_GRACEFUL for callers that need a graceful stop

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 6: Codex daemon 控制 socket 在 Windows 上的识别

背景：Windows 版 Codex 0.164 的 daemon 在 `$CODEX_HOME/app-server-control/app-server-control.sock` 上创建 Winsock AF_UNIX
socket（二进制字符串可见 `app-server-control.sock`、`app-server control socket path exists and is not a socket`）。实测该文件
`lstat` 为 `S_IFREG` + reparse tag `0x80000023`。Wrapper 只对它做 stat 探测，连接由官方 `codex app-server proxy` 完成。

基线：`af_unix`（owner 2，74 条：`tests/test_codex_daemon.py` 32、`tests/test_codex_readiness.py` 28、
`tests/test_codex_app_tools.py` 14——后者见 Task 7）；`tests/test_codex_daemon.py` 的 "other" 13（含
`None == ['/bin/codex', 'app-server', 'proxy', '--sock', '/tmp/codex.sock']` 6）；`tests/test_codex_readiness.py` 中属于
`cc_remote/wrapper/codex_readiness.py` 与 `codex_daemon.py` 的条目。不在本任务：`tests/test_codex_readiness.py` 中驱动
`deploy/check_codex_readiness.py`（`installer.*`）的条目（P5，见 Task 8 跨子项目依赖）。

**Files:**
- Modify: `cc_remote/wrapper/codex_daemon.py`（`socket_identity`）
- Modify: `tests/test_codex_daemon.py`（`standalone_listener` 夹具、所有 `socket.socket(socket.AF_UNIX)` 替换监听、POSIX 路径字面量）
- Modify: `tests/test_codex_readiness.py`（`account_socket` 夹具、替换监听、`unix_serve` 用例标记）

**Interfaces:**
- Consumes: `files.is_unix_socket`、`perms.owned_info`、`perms.private_mode_info`、`perms.no_foreign_write_info`（P2b）、
  `tests.unix_socket.unix_listener`、`tests.unix_socket.short_temp_root`（P2b）、`tests.platform_paths.native_path`（P2c）。
- Produces: `socket_identity(path: str, *, owner_uid: int | None = None) -> tuple[int, int, int]` 签名与 POSIX 语义不变。

- [ ] **Step 1: 改夹具（它们是失败测试）**

`tests/test_codex_daemon.py` import 区加 `from tests.unix_socket import short_temp_root, unix_listener` 与
`from tests.platform_paths import native_path`。`standalone_listener` 改为：

```python
@pytest.fixture(params=[
    False,
    pytest.param(True, marks=pytest.mark.requires_symlink(
        reason="Codex 0.156 protects long socket paths behind a symbolic link alias")),
], ids=["socket", "native-alias"])
def standalone_listener(request, monkeypatch):
    # Keep the path below the 104/108-byte Unix socket path limits.
    with (tempfile.TemporaryDirectory(prefix="cc-sock-", dir=short_temp_root()) as root,
          tempfile.TemporaryDirectory(prefix="ca-", dir=short_temp_root()) as protected):
        home = Path(root).resolve()
        control = home / "app-server-control"
        control.mkdir(mode=0o700)
        path = control / "app-server-control.sock"
        with unix_listener(path):
            path.chmod(0o600)
            if request.param:
                directory = Path(protected).resolve()
                monkeypatch.setattr(daemon_module, "_protected_socket_directory", lambda _uid: directory)
                target = directory / hashlib.sha256(os.fsencode(path)).hexdigest()
                path.rename(target)
                path.symlink_to(target)
            yield home, path
```

文件中其余 `with socket.socket(socket.AF_UNIX) as replacement:` + `replacement.bind(str(path))` 两行改为
`with unix_listener(path):`（`listen()` 已含在 helper 内）。把 `"/bin/codex"` 与 `"/tmp/codex.sock"` 字面量（含期望 argv 列表里的）
替换为模块常量 `CODEX_BIN = native_path("/bin/codex")`、`CODEX_SOCK = native_path("/tmp/codex.sock")`。

`tests/test_codex_readiness.py` 的 `account_socket` 夹具做同样改动（`dir="/tmp"` → `dir=short_temp_root()`、
`socket.socket(socket.AF_UNIX)` → `unix_listener(path)`、`native-alias` 参数加 `requires_symlink`），`test_receipt_fences_physical_replacement_behind_native_alias`
里的替换监听同样改为 `with unix_listener(path):`。
`test_proxy_probe_only_initializes_and_reaps_child` 与 `test_live_config_probe_rejects_healthy_handshake_but_unusable_config`
（用 `websockets.asyncio.server.unix_serve` 在 Python 里起假 daemon）各加：

```python
@pytest.mark.posix_only(reason="serves a fake app-server over a Python AF_UNIX listener; CPython on Windows has no socket.AF_UNIX")
```

`from websockets.asyncio.server import unix_serve` 在 Windows 上可以导入（只在调用时失败），保持不变。

- [ ] **Step 2: 运行测试，确认失败点转移到产品代码**

Run: `for f in tests/test_codex_daemon.py tests/test_codex_readiness.py; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1 | grep -E "not private|passed|failed" | tail -3; done`
Expected: 不再有 `module 'socket' has no attribute 'AF_UNIX'`；`standalone_listener[socket]` 相关用例失败于
`ValueError: Codex socket is not private to its owner` 或 `... parent is not private ...`（`S_ISSOCK` 与 `st_mode` 判断）。

- [ ] **Step 3: 实现 `socket_identity`**

`cc_remote/wrapper/codex_daemon.py` 在 `socket_identity` 之前加：

```python
def _owned_by(target: str | Path, info: os.stat_result, uid: int) -> bool:
    """``st_uid == uid``, with the current user checked through the platform."""
    if uid == current_uid():
        return perms.owned_info(target, info)
    return info.st_uid == uid
```

`socket_identity` 的父目录判断改为：

```python
    parent_info = parent.lstat()
    if (not address.is_absolute() or str(parent.resolve()) != str(parent)
            or not stat.S_ISDIR(parent_info.st_mode)
            or not _owned_by(parent, parent_info, uid)
            or not perms.no_foreign_write_info(parent, parent_info)):
        raise ValueError("Codex socket parent is not private to its owner")
```

把函数后半段（从 `info = address.lstat()` 到 `return`）整体替换为：

```python
    info = address.lstat()
    socket_path: Path = address
    if stat.S_ISLNK(info.st_mode):
        directory = _protected_socket_directory(uid)
        target = directory / hashlib.sha256(os.fsencode(address)).hexdigest()
        if info.st_uid != uid or os.readlink(address) != str(target):
            raise ValueError("Codex socket alias does not match its account address")
        directory_info = directory.lstat()
        if (not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid != uid
                or stat.S_IMODE(directory_info.st_mode) != 0o700):
            raise ValueError("Codex protected socket directory is not private")
        info = target.lstat()
        socket_path = target
    if (not platform_files.is_unix_socket(info)
            or not _owned_by(socket_path, info, uid)
            or not perms.private_mode_info(socket_path, info)):
        raise ValueError("Codex socket is not private to its owner")
    return info.st_dev, info.st_ino, info.st_ctime_ns
```

alias 分支内部（`os.readlink`、`stat.S_IMODE(...) != 0o700`）是 Codex 在 Linux 上的专有布局，逐字保留。POSIX 上
`is_unix_socket` 即 `S_ISSOCK`，`_owned_by(...)` 即 `st_uid == uid`，`private_mode_info` 即 `not st_mode & 0o077`，与原条件等价。
import：`from cc_remote.platform import files as platform_files, perms`。

- [ ] **Step 4: 复跑**

```bash
for f in tests/test_codex_daemon.py tests/test_codex_readiness.py tests/test_codex_shared_machine.py tests/test_codex_external.py; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line -rs 2>&1 | tail -3 | sed "s|^|$f: |"; done
```

Expected: `tests/test_codex_daemon.py` 0 failed（`native-alias` 参数在未开开发者模式的 Windows 上 SKIPPED）；
`tests/test_codex_readiness.py` 只剩驱动 `deploy/check_codex_readiness.py` 的失败（首行在 `deploy\check_codex_readiness.py`），
其余 0 failed。

- [ ] **Step 5: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote/wrapper/codex_daemon.py tests/test_codex_daemon.py tests/test_codex_readiness.py
git add cc_remote/wrapper/codex_daemon.py tests/test_codex_daemon.py tests/test_codex_readiness.py
git commit -F - <<'EOF'
fix(wrapper): recognise the official Codex daemon socket on Windows

- Validate the control socket with files.is_unix_socket and platform ownership/privacy checks
- Bind real Windows AF_UNIX listeners in daemon and readiness fixtures; only Python-served fake daemons stay posix_only

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 7: 平台专有功能的测试标记与运行时符号链接

基线：`posix_os_calls` 中 `tests/test_codex_service.py` 23、`tests/test_codex_launch_barrier.py` 14、`tests/test_codex_desktop.py` 6、
`tests/test_codex_app_tools.py` 3、`tests/test_codex_readiness.py::test_live_installer_check_is_fresh_and_runs_only_as_service_owner[*]`
2（`os.geteuid`，测的是 deploy 安装器，P5）；`af_unix` 中 `tests/test_codex_app_tools.py` 14；"other" 中 `tests/test_codex_service.py` 4、
`tests/test_codex_desktop.py` 7、`tests/test_codex_launch_barrier.py` 2、`tests/test_codex_app_tools.py` 1；`symlink_privilege` 中除 P2c 已处理文件
（markdown_preview、workspace_browser、attachments、workspaces、session_artifacts）外的全部 owner-2 条目；
`tests/test_codex_checkpoints.py` 中恢复符号链接条目的用例。

**Files:**
- Modify: `cc_remote/platform/files.py`（`create_symlink_entry`）、`cc_remote/wrapper/codex_checkpoints.py`
- Modify tests: `tests/test_codex_service.py`、`tests/test_codex_launch_barrier.py`、`tests/test_codex_app_tools.py`、
  `tests/test_codex_desktop.py`，以及下面 Step 3 命令列出的 `symlink_privilege` 文件
- Test: `tests/test_platform_files.py`

**Interfaces:**
- Produces: `files.create_symlink_entry(target: bytes, path: StrPath) -> None`——POSIX：`os.symlink(target, path)`；Windows：先试
  `os.symlink`，若因缺少特权失败（`winerror == 1314`），改为把链接目标写成普通文件（Git for Windows 在 `core.symlinks=false`
  时检出符号链接条目的行为）。

- [ ] **Step 1: 模块级平台标记（spec §1 非目标与 §7 允许的 POSIX 专有行为）**

`tests/test_codex_service.py` 在 import 之后加：

```python
pytestmark = pytest.mark.posix_only(
    reason="systemd user-service isolation and the cgroup lifetime guard exist only on Linux")
```

Windows 侧的行为测试放在新文件 `tests/test_codex_service_windows.py`（模块级 `pytestmark` 会叠加到同文件的所有用例上，
所以不能放进原文件）：

```python
import pytest

from cc_remote.wrapper import codex_service


@pytest.mark.windows_only(reason="Windows has no systemd: Codex never needs a transient unit")
def test_windows_never_needs_isolation():
    assert codex_service.needs_isolation() is False
```

`tests/test_codex_launch_barrier.py`：

```python
pytestmark = pytest.mark.posix_only(
    reason="the launch barrier is a Linux abstract AF_UNIX SEQPACKET socket checked with SO_PEERCRED")
```

新建 `tests/test_codex_launch_barrier_windows.py`：

```python
import pytest

from cc_remote.wrapper import codex_launch_barrier


@pytest.mark.windows_only(reason="Windows Wrappers never serve the Linux launch barrier")
def test_serve_is_a_no_op_without_systemd_isolation():
    with codex_launch_barrier.serve():
        with codex_launch_barrier.barrier.launch():
            pass
```

`tests/test_codex_app_tools.py` 与 `tests/test_codex_desktop.py`：

```python
pytestmark = pytest.mark.posix_only(
    reason="macOS Codex Desktop App integration (Info.plist, /bin/ps, codesign); spec §1 non-goal")
```

- [ ] **Step 2: `files.create_symlink_entry` 与 checkpoint 恢复**

`tests/test_platform_files.py` 追加：

```python
def test_create_symlink_entry_links_or_writes_the_target(tmp_path) -> None:
    path = tmp_path / "entry"
    files.create_symlink_entry(b"target.txt", path)
    if path.is_symlink():
        assert os.readlink(path) == "target.txt"
    else:
        assert sys.platform == "win32"
        assert path.read_bytes() == b"target.txt"
```

`cc_remote/platform/files.py` 追加：

```python
_ERROR_PRIVILEGE_NOT_HELD = 1314


def create_symlink_entry(target: bytes, path: StrPath) -> None:
    """Materialize a repository symlink entry.

    Windows without Developer Mode cannot create symbolic links; like Git for
    Windows with ``core.symlinks=false`` the entry becomes a regular file
    whose content is the link target.
    """
    try:
        os.symlink(target, os.fsencode(path))
    except OSError as exc:
        if not IS_WINDOWS or getattr(exc, "winerror", None) != _ERROR_PRIVILEGE_NOT_HELD:
            raise
        fd = open_with_share_delete(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            os.write(fd, target)
            os.fsync(fd)
        finally:
            os.close(fd)
```

`cc_remote/wrapper/codex_checkpoints.py` 中 `if entry.mode == "120000": os.symlink(content, os.fsencode(temporary))` 改为
`platform_files.create_symlink_entry(content, temporary)`。

- [ ] **Step 3: 测试夹具里的符号链接用 `requires_symlink`**

列出要标注的用例：

```bash
.venv/Scripts/python.exe - <<'EOF'
import json
d = json.load(open("docs/superpowers/windows-baseline.json", encoding="utf-8"))
done = {"tests/test_markdown_preview.py", "tests/test_workspace_browser.py", "tests/test_attachments.py",
        "tests/test_workspaces.py", "tests/test_session_artifacts.py", "tests/test_codex_app_tools.py",
        "tests/test_codex_desktop.py", "tests/test_codex_daemon.py", "tests/test_codex_readiness.py"}
for x in d["failures"]:
    if x["owner"] == 2 and x["root_cause_key"] == "symlink_privilege" and x["nodeid"].split("::")[0] not in done:
        print(x["nodeid"])
EOF
```

对输出的每个测试函数（参数化用例标在函数上；夹具建链接的标在使用该夹具的函数上）加
`@pytest.mark.requires_symlink(reason="<该夹具建的链接，例如 'builds a fake npm bin symlink tree'>")`。
`tests/test_codex_checkpoints.py` 里自己调用 `os.symlink` 建仓库内容的用例
（`test_dirty_baseline_round_trip_preserves_index_and_file_kinds`、`test_finish_accepts_commit_with_unchanged_untracked_baseline`）
加 `requires_symlink(reason="commits a symbolic link into the fixture repository")`；
`test_finish_rejects_commit_with_changed_untracked_baseline` 的参数表里把 `"symlink"` 改为
`pytest.param("symlink", marks=pytest.mark.requires_symlink(reason="leaves an untracked symbolic link"))`。
无特权时的恢复回退由 Step 2 的 `test_create_symlink_entry_links_or_writes_the_target` 覆盖。

- [ ] **Step 4: 复跑**

```bash
FILES="tests/test_codex_service.py tests/test_codex_service_windows.py tests/test_codex_launch_barrier.py tests/test_codex_launch_barrier_windows.py tests/test_codex_app_tools.py tests/test_codex_desktop.py tests/test_codex_checkpoints.py tests/test_platform_files.py tests/test_codex_npm_ownership.py tests/test_claude_external.py tests/test_codex_external.py tests/test_profile_rollback.py tests/test_child_env.py tests/test_claude_controls.py tests/test_codex_agents.py tests/test_codex_missing_sessions.py tests/test_codex_task_activity.py tests/test_engine_capabilities.py tests/test_rollback_commands.py tests/test_session_plans.py tests/test_session_presentation.py tests/test_audio_preview.py"
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line -rs 2>&1 | tail -2 | sed "s|^|$f: |"; done
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1; done | grep -c "WinError 1314"
```

Expected: 最后计数 `0`；被 skip 的用例原因都以 `posix_only:` 或 `requires_symlink:` 开头。

- [ ] **Step 5: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote tests
git add cc_remote/platform/files.py cc_remote/wrapper/codex_checkpoints.py tests
git diff --cached --stat
git commit -F - <<'EOF'
test(wrapper): mark Linux and macOS-only features and symlink fixtures

- Mark systemd isolation, the Linux launch barrier and macOS Desktop App tests posix_only, with Windows behaviour tests
- Mark fixtures that plant symbolic links requires_symlink
- Restore repository symlink entries as target files when Windows cannot create links

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

Expected: `--stat` 只含 `cc_remote/platform/files.py`、`cc_remote/wrapper/codex_checkpoints.py` 和 `tests/` 下的文件。

---

### Task 8: 剩余基线分诊、已知小修复、基线刷新与 PR

本任务处理 P2b～P2d 之后仍属子项目 2 的失败。下表是用"模拟 `fsync_directory` 的诊断 shim"跑完 owner-2 全部 112 个文件后
确认的剩余具体原因与修法；执行时先按表修，再跑 Step 3 的汇总脚本，任何不在表中的新签名都要定位到根因再修（不得 skip）。

| 签名（首行） | 文件 | 原因 | 修法 |
|---|---|---|---|
| `assert 647 == 642`、`assert 245 == 242` | `tests/test_codex_history.py`（2） | 夹具用 `Path.write_text` 写 JSONL，Windows 把 `\n` 写成 `\r\n`，按 `len(row.encode())` 算的偏移错位 | 这两个用例里写 rollout 的 `write_text(X)` 改为 `write_text(X, newline="\n")`（3.10 起支持） |
| `WindowsPath('C:/Users/…/.claude') == WindowsPath('…/.claude')` | `tests/test_claude_storage_root.py`、`tests/test_claude_profiles.py`、`tests/test_claude_permission_state.py`、`tests/test_wrapper_startup.py`、`tests/test_wrapper_core_fixes.py`、`tests/test_workspace_browser.py` | 测试只 `setenv("HOME")`；Windows 的 `Path.home()` 读 `USERPROFILE` | 每个 `monkeypatch.setenv("HOME", X)` 旁加 `monkeypatch.setenv("USERPROFILE", X)` |
| `'http://old.example:7890' == 'http://new.example:8080'`、`None == 'http://explicit.example:8080'` | `tests/test_transport_bounds.py`（2） | `test_connect_honors_explicit_http_proxy_and_bypass` 中有两组参数同时设置 `HTTPS_PROXY` 与 `https_proxy`（或 `HTTP_PROXY` 与 `http_proxy`）并期望小写优先；Windows 环境变量名不区分大小写，两者是同一个变量 | 把这两组参数（期望值分别为 `"http://new.example:8080"`、`"http://explicit.example:8080"` 的那两条）改写成 `pytest.param(..., marks=pytest.mark.posix_only(reason="POSIX environment names are case-sensitive; Windows has a single HTTPS_PROXY/https_proxy variable"))` |
| `TypeError: 'NoneType' object is not subscriptable`（`tests/test_turn_file_pages.py:77,100`） | `tests/test_turn_file_pages.py`（2） | P2b/P2c 合入后复测；若仍失败，按该行所读的 `cwd`/文件路径用 `native_path` 修夹具 | 同 P2c Task 4 的 `native_path` 模式 |
| `FileNotFoundError: [WinError 3] …\projects\-workspace-long-project-…` | `tests/test_claude_catalog_cwd.py` | ① 夹具 cwd 为 POSIX 路径（`/workspace/...`、`/original-project`）；② 长 cwd 用例的项目目录超过 `MAX_PATH`，本机 `LongPathsEnabled=0` | ① 用 `native_path`；② 见"跨子项目依赖/裁定"第 4 条，在该裁定落地前这些长路径用例保持失败并在 PR 中列出 nodeid |
| `CheckpointError: Unable to retire Codex checkpoint journal`（`WinError 5`） | `tests/test_codex_checkpoints.py` | `cleanup_codex_checkpoint_session` 在会话目录里持有 `journal.lock` 句柄时把目录 `os.replace` 成墓碑；Windows 不允许重命名含打开句柄的目录 | 见"跨子项目依赖/裁定"第 5 条（需要 controller 裁定锁位置），落地前在 PR 中列出 |
| `Claude service directory must be private and owned by this user` | `tests/test_claude_native_permissions.py`、`tests/test_manual_compaction.py`、`tests/test_claude_live_context.py` | 来自 `cc_remote/claude_service/wire.py`（P3） | 跨子项目依赖第 1 条 |
| `Task state directory must be private and owned by this user`、`Custom task MCP launcher` | `tests/test_codex_task_activity.py`、`tests/test_codex_agent_activity.py`、`tests/test_task_refresh.py` | 来自 `cc_remote/task_store.py`、`cc_remote/task_refresh.py`（基线归属规则 `task_*` → P4） | 跨子项目依赖第 2 条 |
| 首行在 `deploy\check_codex_readiness.py` | `tests/test_codex_readiness.py` | `deploy/`（P5） | 跨子项目依赖第 3 条 |

**Files:**
- Modify: 上表"修法"列中的测试文件
- Modify: `docs/superpowers/windows-baseline.md`（子项目 2 一节与汇总表）、`docs/superpowers/windows-baseline.json`（owner 2 条目）

- [ ] **Step 1: 按表修复测试**

逐行执行上表"修法"列。`USERPROFILE` 一项可以先列出全部位置：

```bash
grep -nE "setenv\(\"HOME\"" tests/test_claude_storage_root.py tests/test_claude_profiles.py tests/test_claude_permission_state.py tests/test_wrapper_startup.py tests/test_wrapper_core_fixes.py tests/test_workspace_browser.py tests/test_child_env.py
```

对每一行在其下一行插入同缩进的 `monkeypatch.setenv("USERPROFILE", <同一个值>)`。

- [ ] **Step 2: 复跑上表涉及的文件**

```bash
FILES="tests/test_codex_history.py tests/test_claude_storage_root.py tests/test_claude_profiles.py tests/test_claude_permission_state.py tests/test_wrapper_startup.py tests/test_wrapper_core_fixes.py tests/test_workspace_browser.py tests/test_transport_bounds.py tests/test_turn_file_pages.py tests/test_claude_catalog_cwd.py"
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1 | tail -2 | sed "s|^|$f: |"; done
```

Expected: 除 `tests/test_claude_catalog_cwd.py` 的长路径用例外 0 failed。

- [ ] **Step 3: owner-2 全量逐文件汇总**

```bash
mkdir -p ~/p2-final/junit
.venv/Scripts/python.exe -c "import json;d=json.load(open('docs/superpowers/windows-baseline.json',encoding='utf-8'));print('\n'.join(sorted({x['nodeid'].split('::')[0].replace(' (whole module)','') for x in d['failures'] if x['owner']==2})))" > ~/p2-final/files.txt
W=$(cygpath -w ~/p2-final)
while read -r f; do
  n=$(basename "$f" .py)
  PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -p no:cacheprovider -o addopts="" \
    -q --tb=line --basetemp="$W\\bt\\$n" --junitxml="$W\\junit\\$n.xml" > ~/p2-final/junit/$n.log 2>&1
  echo "$n $?"
done < ~/p2-final/files.txt > ~/p2-final/summary.txt
.venv/Scripts/python.exe - <<'EOF'
import collections, glob, json, os, xml.etree.ElementTree as ET
base = json.load(open("docs/superpowers/windows-baseline.json", encoding="utf-8"))
owner2 = {x["nodeid"] for x in base["failures"] if x["owner"] == 2}
failing = {}
for path in glob.glob(os.path.expanduser("~/p2-final/junit/*.xml")):
    for case in ET.parse(path).getroot().iter("testcase"):
        bad = [c for c in case if c.tag in ("failure", "error")]
        if bad:
            nodeid = case.get("classname").replace(".", "/") + ".py::" + case.get("name")
            failing[nodeid] = (bad[0].get("message") or "").splitlines()[0][:160]
print("baseline owner-2:", len(owner2), "still failing:", len(failing),
      "new (not in baseline):", len(set(failing) - owner2))
for nodeid in sorted(set(failing) - owner2):
    print("NEW", nodeid, failing[nodeid])
counts = collections.Counter(message.split(":")[0] for message in failing.values())
print(counts.most_common(20))
EOF
grep -v " 0$\| 1$" ~/p2-final/summary.txt || echo "no timeouts"
```

Expected: `new (not in baseline): 0`；最后一行 `no timeouts`；剩余失败只能是本任务表中标注为跨子项目依赖/待裁定的签名。
出现 `NEW` 行时必须修复后重跑。

- [ ] **Step 4: 刷新基线文档**

用 Step 3 的结果更新 `docs/superpowers/windows-baseline.json`：删除已通过的 owner-2 条目；仍失败的条目保留并把
`root_cause_key`/`first_error_line` 更新为当前首行；在 `docs/superpowers/windows-baseline.md` 顶部"运行方式说明"之后加一段
"P2 合并后（`<本分支 HEAD 短 SHA>`）"小节：owner-2 剩余条数、按文件的表、以及每条剩余原因指向的跨子项目依赖编号。
不改动其它子项目的条目。

- [ ] **Step 5: 门禁、推送与 PR**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote tests deploy
git diff origin/windows-compat-v2 --check
git add tests docs/superpowers/windows-baseline.md docs/superpowers/windows-baseline.json
git commit -F - <<'EOF'
test(wrapper): fix remaining Windows fixture assumptions and refresh the P2 baseline

- Write byte-offset fixtures with LF newlines and mirror HOME into USERPROFILE
- Record the remaining sub-project 2 failures and their cross-project owners

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git push -u origin win/2d-processes
gh pr create --repo donny-star/cc-remote --base windows-compat-v2 --head win/2d-processes \
  --title "Windows P2d: process ownership, engine launch, process groups and Codex daemon" --body-file - <<'EOF'
- Windows process snapshots (Toolhelp32 + PEB cwd/environment) drive Claude and Codex ownership scans
- Engine CLIs resolve through npm shims (no cmd.exe); every helper/engine runs in a platform process group
- The official Codex daemon's Windows AF_UNIX control socket is recognised by its reparse tag
- Linux/macOS-only features are marked posix_only with Windows behaviour tests; symlink fixtures requires_symlink
- Baseline refreshed for sub-project 2; remaining items list their cross-project owners

Checks: Windows per-file pytest for all owner-2 baseline files (summary in the refreshed baseline); ruff; git diff --check.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

## 跨子项目依赖与待裁定事项

1. **P3**：`cc_remote/claude_service/wire.py:55` 以 POSIX 权限位判断 service 目录私有，Windows 上恒失败。影响
   `tests/test_claude_native_permissions.py`、`tests/test_manual_compaction.py`、`tests/test_claude_live_context.py` 中走 Claude
   service 的用例。请 P3 改用 `perms.owned_info`/`perms.private_mode_info`（P2b 提供）。
2. **P4**：`cc_remote/task_store.py`（`TaskStore.db` 的 `st_uid`/`st_mode & 0o077` 与 `os.O_NOFOLLOW`）、`cc_remote/task_refresh.py`。
   影响 `tests/test_codex_task_activity.py`（14+1）、`tests/test_codex_agent_activity.py`（4）、`tests/test_task_refresh.py`。
   基线把这些 nodeid 记在 owner 2 名下，但按基线自身的归属规则 `task_*` 属于 P4；P2 不修改这些文件。
3. **P5**：`deploy/check_codex_readiness.py`（`os.O_NOFOLLOW`、`os.geteuid`）被 `tests/test_codex_readiness.py` 的
   `installer.*` 用例驱动（含 `test_live_installer_check_is_fresh_and_runs_only_as_service_owner[*]`）。
4. **裁定（长路径）**：Claude 项目目录名最长约 200 字符，`~/.claude/projects/<名>/<uuid>.jsonl` 在 Windows 上可超过 260 字符；
   本机 `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled = 0`。建议把 `LongPathsEnabled=1` 列为 Windows 环境要求，
   由 P5 安装器检测并提示（修改注册表需要管理员，必须征得用户同意）；`tests/test_claude_catalog_cwd.py` 的长路径用例在开启后应通过。
5. **裁定（checkpoint 墓碑）**：`codex_checkpoints.cleanup_codex_checkpoint_session` 需要在持锁时重命名会话目录，Windows 不允许。
   可选方案：(a) 把 `journal.lock` 移到会话目录之外（`<repo>/<session>.lock`），两平台一致但改变了 POSIX 的锁位置；
   (b) Windows 上在锁内逐个删除 journal 文件后释放锁再删目录。P2 倾向 (a) 并需要 controller 确认"锁文件位置变化"不违反
   "POSIX 行为完全不变"。
6. **已知限制（记录，不阻塞）**：Windows 上无法廉价枚举他进程句柄，Codex 归属只能依据 argv（`resume SID`、命名的线程或 rollout）
   与 turn marker；以管理员身份运行的同用户 CLI 打不开时视为不存在（handoff 已记录的 fail-open）。Windows 环境变量名不区分大小写，
   `process_command_environment_value` 仍按精确键名匹配。
