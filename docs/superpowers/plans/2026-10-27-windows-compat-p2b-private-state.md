# Windows 适配 · 子项目 2b：私有状态持久化（目录 fsync、私有校验、no-follow 打开）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Wrapper 的所有私有状态文件（profile topology、各类 journal/store、device 凭据）在 Windows 上能写入、校验并
重新读取，消掉基线里 `profile_state_migration`、`private_store_persist`、`private_check_st_mode`、
`posix_open_flags` 四类根因（owner 2 合计 643 条），以及由它们引起的 3 个整文件超时。

**Architecture:** 纯机械替换，全部走 `cc_remote/platform/`：目录 fsync 改用已有的 `files.fsync_directory`，原子发布的
`os.replace` 改用 `files.replace`；调用方手写的 `st_uid`/`st_mode & 0o077`/`& 0o022` 判断改用本计划新增的
`perms.owned_info` / `perms.private_mode_info` / `perms.no_foreign_write_info`（POSIX 分支只读调用方已有的
`stat_result`，与原表达式逐字等价、零额外 syscall）；直接写 `os.O_NOFOLLOW` 的打开改用 P2a 的 `files.open_nofollow`。

**Tech Stack:** Python 3.13、pywin32 312（`win32security`）、pytest 9。

**Spec:** `docs/superpowers/specs/2026-10-27-windows-compat-design.md`（§3、§3.1 "私有权限判定""替换文件"）；
`docs/superpowers/plans/2026-10-27-windows-compat-p1-handoff.md`（"私有/属主校验"约定）；
`docs/superpowers/windows-baseline.json`（owner == 2）。

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
- 前置：P2a（阶段 A）已合入 `windows-compat-v2`。本计划从合并后的 `windows-compat-v2` 切分支 `win/2b-private-state`。
- 文件归属：不得修改 P3（`cc_remote/claude_service/**`、`cc_remote/claude_broker/**`、`cc_remote/claude_remote*`、
  `cc_remote/wrapper/claude_broker_handle.py`、`cc_remote/platform/ipc.py`、`cc_remote/platform/pty.py`）、
  P4（`session_messages`、TUI、viewer、`timed_tasks`、`async_tasks`、`task_*`）、P5（`deploy/**`、`cc_remote/update*.py`）的文件；
  `tests/conftest.py`、`pytest.ini` 在本计划中不改。
- 机械替换只改所列表达式本身，不重排周围代码、不改注释以外的文字；`machine.py` 用函数名/锚点文本定位，不依赖行号。
- 平台层对外只抛 `OSError` 家族；新增的 `*_info` 校验在 Windows 上 fail-closed（任何异常都返回 False）。
- 业务模块一律写 `from cc_remote.platform import files as platform_files`（`machine.py`、`turn_changes.py`、`dsh_runtime.py`
  等有名为 `files` 的局部变量/参数，直接导入 `files` 会被遮蔽甚至触发 `UnboundLocalError`）；`perms` 无此问题，直接导入。
- MAX_PATH：本机 `LongPathsEnabled=0`。pytest 临时目录很深时（例如 TEMP 在 `…\Temp\codeg-acp\<id>\` 下），
  `tests/test_codex_checkpoints.py` 等会因路径超过 260 字符报 `FileNotFoundError`（基线里该文件 23 条 `'<tmp>'` 即此原因，
  已实测）。验收命令一律可加 `--basetemp=C:\t\<测试文件名>`；长路径支持属 P5 环境检查，P2 不改注册表。
- 机械替换任务（Task 3A/3B、4A/4B）一次只改同一种表达式，每处 1～6 行；按子系统拆分，保证每个任务可以在一次评审里看完。

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `cc_remote/platform/perms.py` | 修改 | 新增 `owned_info`、`private_mode_info`、`no_foreign_write_info`；`_descriptor_is_private` 增加 `deny_mask` |
| `cc_remote/platform/files.py` | 修改 | 新增 `is_unix_socket(info)` |
| `tests/test_platform_perms.py`、`tests/test_platform_files.py` | 修改 | 新 helper 的测试 |
| `tests/platform_assertions.py` | 新建 | 测试用 `assert_private_file` / `assert_private_dir`（POSIX 比较权限位，Windows 走 `perms.is_private`） |
| `cc_remote/claude_profiles.py`、`cc_remote/codex_profiles.py` | 修改 | Task 2：topology/pending 文件的目录 fsync |
| `cc_remote/wrapper/rollback_commands.py`、`cc_remote/wrapper/machine.py` | 修改 | Task 2：目录 fsync（machine 3 处） |
| `cc_remote/wrapper/{claude_client_messages,claude_controls,claude_forks,claude_rate_limits,dsh_mutations,engine_capabilities,session_pins,session_plans,session_presentation}.py` | 修改 | Task 3A：目录 fsync / 原子替换 |
| `cc_remote/wrapper/{codex_archives,codex_checkpoints,codex_client_messages,codex_controls,codex_daemon,codex_forks,codex_process_clocks,codex_provider_repair,codex_turn_leases}.py` | 修改 | Task 3B：目录 fsync / 原子替换（`codex_lifecycle.py` 已容错，不改） |
| `cc_remote/config.py`（`_load_device_config`）、`cc_remote/device.py`、`cc_remote/wrapper/dsh_pair.py`；`tests/{test_devices,test_claude_models,test_codex_daemon_restart,test_history_store,test_workspaces}.py` | 修改 | Task 4A：凭据私有化；权限位断言改用 `platform_assertions` |
| `cc_remote/wrapper/{claude_client_messages,claude_controls,codex_client_messages,codex_controls,codex_process_clocks,claude_usage,dsh_client}.py`、`cc_remote/config.py`（service registration） | 修改 | Task 4B：读取时的私有/属主校验 |
| `cc_remote/wrapper/{dsh_mutations,session_artifacts,codex_agent_activity}.py` | 修改 | Task 5A：`os.O_NOFOLLOW` → `platform_files.open_nofollow` |
| `cc_remote/wrapper/{codex_sessions,codex_provider_repair,codex_daemon}.py` | 修改 | Task 5B：`open_nofollow`；`os.pread/pwrite` → `platform_files.pread/pwrite` |

## 准备工作

```bash
cd /c/Users/xuhaodong/work/cc-remote-p2
git fetch origin
git switch -c win/2b-private-state origin/windows-compat-v2
git log --oneline -1   # 期望：包含 P2a 合并提交
PYTHONUTF8=1 .venv/Scripts/python.exe -c "from cc_remote.platform import files; print(files.open_nofollow, files.is_reparse_point)"
```

每个任务结束后的"受影响文件复跑"统一用这个循环（`FILES` 换成任务列出的测试文件）：

```bash
for f in $FILES; do
  PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1 | tail -1 | sed "s|^|$f: |"
done
```

---

### Task 1: 平台 helper：`perms.*_info` 与 `files.is_unix_socket`

基线：无直接 nodeid；Task 4A/4B/5A/5B、P2c、P2d 的消费方依赖它。验收：`tests/test_platform_perms.py`、`tests/test_platform_files.py`
在 Windows 和 Linux 上全部通过。

**Files:**
- Modify: `cc_remote/platform/perms.py`
- Modify: `cc_remote/platform/files.py`
- Test: `tests/test_platform_perms.py`、`tests/test_platform_files.py`

**Interfaces:**
- Consumes: `perms.is_private_fd`、`perms.owned_by_current_user`、`perms._named_security_info`、
  `perms._handle_security_info`、`perms._descriptor_is_private`（P1 已有）。
- Produces:
  - `perms.owned_info(target: int | StrPath, info: os.stat_result, *, effective: bool = False) -> bool`——POSIX：
    `info.st_uid == (current_euid() if effective else current_uid())`（调用方原来比对 `current_euid()` 的位置必须传
    `effective=True`，保证 POSIX 表达式逐字等价）；Windows：`owned_by_current_user(target)`，缺失路径返回 False
    （Windows 没有 uid/euid 之分，`effective` 被忽略）。
  - `perms.private_mode_info(target: int | StrPath, info: os.stat_result) -> bool`——POSIX：
    `not stat.S_IMODE(info.st_mode) & 0o077`；Windows：`target` 的 DACL 满足 `is_private` 规则。
  - `perms.no_foreign_write_info(target: int | StrPath, info: os.stat_result) -> bool`——POSIX：
    `not stat.S_IMODE(info.st_mode) & 0o022`；Windows：属主为当前用户/Administrators，且非受信 SID 的 ACE 不含任何写类权限。
  - `files.is_unix_socket(info: os.stat_result) -> bool`——POSIX：`stat.S_ISSOCK(info.st_mode)`；Windows：
    `getattr(info, "st_reparse_tag", 0) == 0x80000023`（`IO_REPARSE_TAG_AF_UNIX`；实测 Windows 的 AF_UNIX socket 文件
    `lstat` 为 `S_IFREG` + 该 reparse tag）。
  - 约定：`target` 是 `info` 的来源（fd 或路径）。业务代码里 `x.st_uid != current_uid()` → `not perms.owned_info(t, x)`；
    `x.st_uid != current_euid()` → `not perms.owned_info(t, x, effective=True)`；
    `stat.S_IMODE(x.st_mode) & 0o077`（或 `x.st_mode & 0o077`）→ `not perms.private_mode_info(t, x)`；
    `... & 0o022` → `not perms.no_foreign_write_info(t, x)`。

- [ ] **Step 1: 写失败测试**

`tests/test_platform_perms.py` 顶部 import 区加 `import stat`，文件末尾追加：

```python
def test_info_helpers_accept_a_private_file(tmp_path) -> None:
    path = tmp_path / "secret"
    path.write_bytes(b"x")
    perms.make_private(path, directory=False)
    info = path.lstat()
    assert perms.owned_info(path, info)
    assert perms.private_mode_info(path, info)
    assert perms.no_foreign_write_info(path, info)
    fd = os.open(path, os.O_RDONLY)
    try:
        opened = os.fstat(fd)
        assert perms.owned_info(fd, opened)
        assert perms.private_mode_info(fd, opened)
        assert perms.no_foreign_write_info(fd, opened)
    finally:
        os.close(fd)


@pytest.mark.posix_only(reason="mode bits are the POSIX privacy mechanism")
def test_info_helpers_read_only_the_given_mode_bits(tmp_path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"")
    os.chmod(path, 0o640)
    info = path.lstat()
    assert not perms.private_mode_info(path, info)
    assert perms.no_foreign_write_info(path, info)
    os.chmod(path, 0o620)
    info = path.lstat()
    assert not perms.no_foreign_write_info(path, info)
    # The decision comes from ``info``, not a fresh stat of ``target``.
    os.chmod(path, 0o600)
    assert not perms.private_mode_info(path, info)
    fake = os.stat_result((stat.S_IFREG | 0o600, 0, 0, 1, os.getuid() + 1, 0, 0, 0, 0, 0))
    assert not perms.owned_info(path, fake)
    assert not perms.owned_info(path, fake, effective=True)


@pytest.mark.posix_only(reason="real and effective uids only differ on POSIX")
def test_owned_info_effective_compares_the_effective_uid(tmp_path, monkeypatch) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"")
    info = path.lstat()
    monkeypatch.setattr(perms, "current_euid", lambda: info.st_uid + 1)
    assert perms.owned_info(path, info)
    assert not perms.owned_info(path, info, effective=True)


@pytest.mark.windows_only(reason="ACL entries are the Windows privacy mechanism")
def test_info_helpers_reject_everyone_write(tmp_path) -> None:
    import ntsecuritycon
    import win32security

    path = tmp_path / "f"
    path.write_bytes(b"")
    perms.make_private(path, directory=False)
    descriptor = win32security.GetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION)
    dacl = descriptor.GetSecurityDescriptorDacl()
    world = win32security.CreateWellKnownSid(win32security.WinWorldSid)
    dacl.AddAccessAllowedAce(
        win32security.ACL_REVISION, ntsecuritycon.FILE_GENERIC_READ, world)
    win32security.SetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION, None, None, dacl, None)
    info = path.lstat()
    assert not perms.private_mode_info(path, info)
    assert perms.no_foreign_write_info(path, info)  # read-only grant
    dacl.AddAccessAllowedAce(
        win32security.ACL_REVISION, ntsecuritycon.FILE_GENERIC_WRITE, world)
    win32security.SetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION, None, None, dacl, None)
    assert not perms.no_foreign_write_info(path, path.lstat())


@pytest.mark.windows_only(reason="ACL entries are the Windows privacy mechanism")
def test_info_helpers_fail_closed_for_missing_paths(tmp_path) -> None:
    path = tmp_path / "gone"
    path.write_bytes(b"")
    info = path.lstat()
    path.unlink()
    assert not perms.owned_info(path, info)
    assert not perms.private_mode_info(path, info)
    assert not perms.no_foreign_write_info(path, info)
```

`tests/test_platform_files.py` 末尾追加（复用 P2a 的 `tests/unix_socket.py` 与 `socket_dir` 夹具）：

```python
def test_is_unix_socket_rejects_regular_files_and_directories(tmp_path) -> None:
    regular = tmp_path / "f"
    regular.write_bytes(b"")
    assert files.is_unix_socket(regular.lstat()) is False
    assert files.is_unix_socket(tmp_path.lstat()) is False


def test_is_unix_socket_accepts_a_bound_listener(socket_dir) -> None:
    from tests.unix_socket import unix_listener

    path = socket_dir / "s.sock"
    with unix_listener(path):
        assert files.is_unix_socket(path.lstat()) is True
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_platform_perms.py tests/test_platform_files.py -p no:xdist -o addopts="" -q`
Expected: 新用例 FAIL：`AttributeError: module 'cc_remote.platform.perms' has no attribute 'owned_info'`、
`... files has no attribute 'is_unix_socket'`。

- [ ] **Step 3: 实现**

`cc_remote/platform/perms.py` 的 identity 导入改为
`from cc_remote.platform.identity import current_euid, current_sid, current_uid`，文件末尾追加（并把
`_descriptor_is_private` 的签名改为带 `deny_mask`，见下）：

```python
# Rights that let a principal change a file or a directory's entries.
_WRITE_RIGHTS = (
    0x0002 | 0x0004 | 0x0010 | 0x0040 | 0x0100  # data/add, append/subdir, EA, delete child, attributes
    | 0x00010000 | 0x00040000 | 0x00080000      # DELETE, WRITE_DAC, WRITE_OWNER
    | 0x10000000 | 0x40000000                    # GENERIC_ALL, GENERIC_WRITE
)


def _win32_descriptor(target: int | StrPath, info: int):
    if isinstance(target, int):
        return _handle_security_info(target, info)
    return _named_security_info(target, info)


def owned_info(
    target: int | StrPath, info: os.stat_result, *, effective: bool = False,
) -> bool:
    """``info.st_uid == current_uid()`` (or euid) on POSIX; the owner SID on Windows.

    ``target`` is the descriptor or path ``info`` came from. Windows stat
    results report st_uid 0 for every file, so the owner must come from the
    security descriptor; a missing target is not owned. ``effective`` keeps
    callers that compared against the effective uid exact on POSIX.
    """
    if not IS_WINDOWS:
        return info.st_uid == (current_euid() if effective else current_uid())
    try:
        return owned_by_current_user(target)
    except FileNotFoundError:
        return False


def private_mode_info(target: int | StrPath, info: os.stat_result) -> bool:
    """No group/other mode bits on POSIX; an owner-only DACL on Windows."""
    if not IS_WINDOWS:
        return not stat.S_IMODE(info.st_mode) & 0o077
    import win32security

    try:
        descriptor = _win32_descriptor(
            target, win32security.OWNER_SECURITY_INFORMATION
            | win32security.DACL_SECURITY_INFORMATION)
        if descriptor is None:
            return False
        return _descriptor_is_private(descriptor, directory=stat.S_ISDIR(info.st_mode))
    except Exception:
        return False  # unverifiable is not private


def no_foreign_write_info(target: int | StrPath, info: os.stat_result) -> bool:
    """No group/other write bits on POSIX; no foreign write ACE on Windows."""
    if not IS_WINDOWS:
        return not stat.S_IMODE(info.st_mode) & 0o022
    import win32security

    try:
        descriptor = _win32_descriptor(
            target, win32security.OWNER_SECURITY_INFORMATION
            | win32security.DACL_SECURITY_INFORMATION)
        if descriptor is None:
            return False
        return _descriptor_is_private(
            descriptor, directory=stat.S_ISDIR(info.st_mode), deny_mask=_WRITE_RIGHTS)
    except Exception:
        return False
```

把 `_descriptor_is_private` 改为：

```python
def _descriptor_is_private(descriptor, *, directory: bool, deny_mask: int | None = None) -> bool:
    """Owner is the user (or administrators); only trusted SIDs are granted access.

    A directory's inheritable ACEs decide who can read the files later created
    in it, so they count as grants; CREATOR OWNER is trusted there because it
    only ever resolves to each new child's owner. With ``deny_mask`` an
    untrusted grant fails only when it includes one of those rights.
    """
```

并把循环里的 `(ace_type, ace_flags), _mask, sid = ace` 改为 `(ace_type, ace_flags), mask, sid = ace`，在
`if inherit_only and sid == creator_owner: continue` 之后、`return False` 之前插入：

```python
        if deny_mask is not None and not mask & deny_mask:
            continue
```

（`deny_mask=None` 时循环与原来逐行相同，`is_private`/`is_private_fd` 的判定不变。）

`cc_remote/platform/files.py` 在 `is_reparse_point` 之后追加：

```python
_IO_REPARSE_TAG_AF_UNIX = 0x80000023


def is_unix_socket(info: os.stat_result) -> bool:
    """Whether an ``lstat`` result describes a Unix-domain socket file.

    Windows AF_UNIX sockets report S_IFREG plus the AF_UNIX reparse tag.
    """
    if not IS_WINDOWS:
        return stat.S_ISSOCK(info.st_mode)
    return getattr(info, "st_reparse_tag", 0) == _IO_REPARSE_TAG_AF_UNIX
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_platform_perms.py tests/test_platform_files.py -p no:xdist -o addopts="" -q`
Expected: 全部 PASS（符号链接相关用例可为 SKIPPED）。

- [ ] **Step 5: 新建测试断言 helper**

新建 `tests/platform_assertions.py`（Task 4A 使用）：

```python
"""Privacy assertions that mean the same thing on POSIX and Windows."""
from __future__ import annotations

import os
import stat
import sys

from cc_remote.platform import perms


def assert_private_file(path: os.PathLike[str] | str) -> None:
    if sys.platform == "win32":
        assert perms.is_private(path), f"{path} is readable by other principals"
        return
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def assert_private_dir(path: os.PathLike[str] | str) -> None:
    if sys.platform == "win32":
        assert perms.is_private(path), f"{path} is accessible to other principals"
        return
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o700
```

- [ ] **Step 6: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote/platform tests/test_platform_perms.py tests/test_platform_files.py tests/platform_assertions.py
git add cc_remote/platform/perms.py cc_remote/platform/files.py tests/test_platform_perms.py \
  tests/test_platform_files.py tests/platform_assertions.py
git commit -F - <<'EOF'
feat(platform): add stat-based owner/privacy checks and socket detection

- owned_info, private_mode_info and no_foreign_write_info reuse the caller's stat result on POSIX and inspect the DACL on Windows
- owned_info(effective=True) keeps effective-uid callers exact on POSIX
- is_unix_socket recognises Windows AF_UNIX socket files by their reparse tag
- Add a cross-platform privacy assertion test helper

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: profile topology 与 Wrapper 启动期 journal 的目录 fsync

基线：root_cause_key `profile_state_migration`（owner 2，226 条，全部 26 个文件）与 `module_timeout` 3 条中的
`tests/test_codex_controls.py`、`tests/test_claude_autocompact.py`、`tests/test_codex_session_delete.py`；
`timeout_error`（owner 2，37 条：`test_dsh_runtime.py` 12、`test_codex_shared_machine.py` 7、`test_claude_profiles.py` 6、
`test_wrapper_core_fixes.py` 6、`test_codex_missing_sessions.py` 4、`test_command_scheduling.py` 2）预计同样由迁移未完成引起，
本任务记录它们的结果；仍失败的按 P2d Task 8 分诊（`test_dsh_runtime.py` 的可能还依赖 P2c Task 4 的 `posix_abs_paths`）。
根因已实测：`ClaudeProfileTopologyStore`/`CodexProfileTopologyStore` 与 `RollbackCommandJournal` 用
`os.open(<目录>, os.O_RDONLY)` 做目录 fsync，在 Windows 上抛 `PermissionError`，Wrapper 启动时把 profile 迁移标记为
未完成，之后所有 `_claude_profile()`/`_codex_profile()` 抛 `RuntimeError: ... profile state migration is incomplete`；
三个"超时"文件里的用例因此在 `await event.wait()` 上永久等待（用诊断 shim 模拟 `fsync_directory` 后，三个文件都在
40 s 内跑完）。Windows 上 `fsync_directory` 是 no-op（NTFS 的目录项由元数据日志保证，spec §3 已定）；这个"no-op 足够"
的结论在 Step 2 先用最小改动实测确认，再铺开其余位置。

**Files:**
- Modify: `cc_remote/claude_profiles.py`（两处：topology 写入后的 `directory_fd = os.open(path.parent, os.O_RDONLY)`；
  pending 清理后的 `directory_fd = os.open(self.pending_path.parent, os.O_RDONLY)`）
- Modify: `cc_remote/codex_profiles.py`（同上两处）
- Modify: `cc_remote/wrapper/rollback_commands.py`（`_persist_payload`）
- Modify: `cc_remote/wrapper/machine.py`（模块级函数中 `# The rename already made the unsafe projection unreachable`
  注释之前那处；`os.replace(tmp, path)` 后紧跟的那处（约 8870 行）；`# Make the rename durable, not merely atomic in the
  page cache.` 注释下那处）

**Interfaces:**
- Consumes: `platform_files.fsync_directory(path: StrPath) -> None`（P1）、`platform_files.replace(src, dst, *, attempts=10) -> None`（P1）。
- Produces: 无新接口。

- [ ] **Step 1: 确认失败基线**

```bash
FILES="tests/test_ask_bounds.py tests/test_codex_profiles.py tests/test_claude_permission_state.py tests/test_claude_account_isolation.py tests/test_codex_session_archive.py tests/test_codex_external.py"
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=no 2>&1 | tail -1 | sed "s|^|$f: |"; done
```

Expected: 每个文件都有失败，失败信息含 `profile state migration is incomplete`
（`test_ask_bounds.py` 7 failed、`test_codex_profiles.py` 约 62 failed 等，与基线一致）。

- [ ] **Step 2: 可行性验证——只改两个 profile store，确认迁移完成、超时消失**

先只做下面 Step 3 表格里 `claude_profiles.py`、`codex_profiles.py`、`rollback_commands.py` 的 5 处替换（以及它们同块的
`os.replace`），然后：

```bash
for f in tests/test_ask_bounds.py tests/test_codex_controls.py; do
  start=$(date +%s)
  PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1 | tail -1
  echo "$f exit=$? seconds=$(( $(date +%s) - start ))"
done
```

Expected: 两个文件都在 240 s 内结束（`tests/test_codex_controls.py` 不再整文件超时），输出里没有
`profile state migration is incomplete`。如果仍超时，停止：说明 no-op 的 `fsync_directory` 不足以让迁移完成，回报
controller，不要继续铺开。

- [ ] **Step 3: 替换目录 fsync 块**

在上面列出的 8 个位置，把形如

```python
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
```

的 5 行替换为一行（缩进保持原样，参数保持原来的目录表达式）：

```python
            platform_files.fsync_directory(path.parent)
```

逐个位置的目录表达式：

| 文件 | 原 `os.open(...)` 的第一个参数 | 替换后 |
|---|---|---|
| `cc_remote/claude_profiles.py`（topology 写入） | `path.parent` | `platform_files.fsync_directory(path.parent)` |
| `cc_remote/claude_profiles.py`（pending 清理） | `self.pending_path.parent` | `platform_files.fsync_directory(self.pending_path.parent)` |
| `cc_remote/codex_profiles.py`（topology 写入） | `path.parent` | `platform_files.fsync_directory(path.parent)` |
| `cc_remote/codex_profiles.py`（pending 清理） | `self.pending_path.parent` | `platform_files.fsync_directory(self.pending_path.parent)` |
| `cc_remote/wrapper/rollback_commands.py` | `self.path.parent` | `platform_files.fsync_directory(self.path.parent)` |
| `cc_remote/wrapper/machine.py`（"rename already made the unsafe projection unreachable" 之前，外层有 `try/except OSError`，保留外层） | `path.parent` | `platform_files.fsync_directory(path.parent)` |
| `cc_remote/wrapper/machine.py`（约 8870 行，紧跟 `os.replace(tmp, path)`） | `path.parent` | `platform_files.fsync_directory(path.parent)` |
| `cc_remote/wrapper/machine.py`（"Make the rename durable" 注释下） | `path.parent` | `platform_files.fsync_directory(path.parent)` |

同一批位置里，紧挨在 fsync 块之前、发布同一个文件的 `os.replace(a, b)` 改为 `platform_files.replace(a, b)`
（`claude_profiles.py`、`codex_profiles.py` 的 `os.replace(temporary, path)`；`rollback_commands.py` 的
`os.replace(tmp, self.path)`；`machine.py` 约 8870 行的 `os.replace(tmp, path)`）。POSIX 上 `platform_files.replace` 就是
`os.replace`；Windows 上它能替换被 share-delete 句柄占用的目标，并在杀毒软件短暂占用时退避重试。

每个文件顶部按需补 import（已有则不加）：

```python
from cc_remote.platform import files as platform_files
```

`machine.py` 放在已有的 `from cc_remote.platform.identity import current_euid, current_uid` 旁边。

- [ ] **Step 4: 确认 `os.open(<目录>)` 已清零**

Run: `grep -nE "os\.open\((path\.parent|self\.pending_path\.parent|self\.path\.parent), os\.O_RDONLY\)" cc_remote/claude_profiles.py cc_remote/codex_profiles.py cc_remote/wrapper/rollback_commands.py cc_remote/wrapper/machine.py`
Expected: 无输出。

- [ ] **Step 5: 复跑受影响文件**

```bash
FILES="tests/test_ask_bounds.py tests/test_codex_profiles.py tests/test_claude_permission_state.py tests/test_claude_account_isolation.py tests/test_codex_session_archive.py tests/test_codex_missing_sessions.py tests/test_claude_btw_identity.py tests/test_codex_npm_ownership.py tests/test_multisession.py tests/test_reliability_edges.py tests/test_atomic_new_session.py tests/test_claude_rate_limits.py tests/test_history.py tests/test_claude_external.py tests/test_claude_usage.py tests/test_codex_shared_machine.py tests/test_claude_native_permissions.py tests/test_command_reliability.py tests/test_command_scheduling.py tests/test_wrapper_core_fixes.py tests/test_btw_lifecycle.py tests/test_codex_runtime_events.py tests/test_query_queue.py tests/test_question_batches.py tests/test_rollback_commands.py tests/test_codex_controls.py tests/test_claude_autocompact.py tests/test_codex_session_delete.py"
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=no 2>&1 | tail -1 | sed "s|^|$f: |"; done
```

Expected: 每个文件都在 240 s 内输出汇总行（三个原超时文件不再挂住）；`profile state migration is incomplete` 不再出现：

```bash
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1; done | grep -c "profile state migration is incomplete"
```

Expected: `0`。各文件剩余的失败只能属于其它 root_cause_key（由后续任务处理），失败总数不高于基线。再记录
`timeout_error` 的现状（只记录，不作为本任务的门槛）：

```bash
.venv/Scripts/python.exe -c "import json;d=json.load(open('docs/superpowers/windows-baseline.json',encoding='utf-8'));[print(x['nodeid']) for x in d['failures'] if x['owner']==2 and x['root_cause_key']=='timeout_error']" > ~/p2b-timeouts.txt
PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest $(grep -v test_dsh_runtime ~/p2b-timeouts.txt | tr '\n' ' ') -p no:xdist -o addopts="" -q --tb=line 2>&1 | tail -3
```

Expected: 除 `tests/test_dsh_runtime.py` 外的 25 条全部通过；若有失败，把 nodeid 与首行写进 PR 描述，交给 P2d Task 8 分诊。

- [ ] **Step 6: Linux 语义自查并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote/claude_profiles.py cc_remote/codex_profiles.py cc_remote/wrapper/rollback_commands.py cc_remote/wrapper/machine.py
git add cc_remote/claude_profiles.py cc_remote/codex_profiles.py cc_remote/wrapper/rollback_commands.py cc_remote/wrapper/machine.py
git diff --cached --stat
git commit -F - <<'EOF'
fix(wrapper): persist profile topology and startup journals on Windows

- Route directory fsync through platform.files.fsync_directory instead of opening directories
- Publish topology, rollback and projection files with platform.files.replace

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

Expected: `--stat` 只含 4 个文件。

---

### Task 3A: Claude 侧与会话级私有 store 的目录 fsync 与原子替换

基线：root_cause_key `private_store_persist`（owner 2，共 177 条，Task 3A+3B 合计）中属于本任务文件的部分：
`ClaudeRateLimitStoreError`、`ForkJournalError`（Claude fork）、`ClaudeControlStoreError`、`ClaudeClientMessageStoreError`、
`SessionPresentationStoreError`、`SessionPlanStoreError`、`SessionPinStoreError`、`private btw state` 的 "could not be persisted"；
以及 `tests/test_engine_capabilities.py` 中首行为 `PermissionError: [Errno 13] Permission denied: '<tmp>'` 的 "other" 2 条。

**Files:**
- Modify（每个文件一处）：`cc_remote/wrapper/claude_client_messages.py`、`claude_controls.py`、`claude_forks.py`、
  `claude_rate_limits.py`、`dsh_mutations.py`、`engine_capabilities.py`、`session_pins.py`、`session_plans.py`、
  `session_presentation.py`

**Interfaces:**
- Consumes: `platform_files.fsync_directory`、`platform_files.replace`。
- Produces: 无。

- [ ] **Step 1: 确认失败基线**

```bash
FILES="tests/test_claude_rate_limits.py tests/test_session_presentation.py tests/test_claude_controls.py tests/test_claude_client_messages.py tests/test_session_plans.py tests/test_session_pins.py tests/test_claude_btw_cleanup.py tests/test_engine_capabilities.py tests/test_dsh_mutations.py tests/test_claude_forks.py"
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1; done | grep -cE "could not be persisted|Permission denied"
```

Expected: 大于 0（记录数字，Step 4 对比）。

- [ ] **Step 2: 替换目录 fsync 块**

每处都把 `X = os.open(<DIR>, os.O_RDONLY)` + `try: os.fsync(X) finally: os.close(X)` 五行替换为
`platform_files.fsync_directory(<DIR>)`，并把紧挨其前发布同一文件的 `os.replace(a, b)` 改为 `platform_files.replace(a, b)`
（行号为 `2586f11` 时的位置，仅供定位）：

| 文件 | 定位（函数/锚点） | `<DIR>` | 同块的 `os.replace` |
|---|---|---|---|
| `claude_client_messages.py` | `:277` `directory_fd = os.open(self.directory, os.O_RDONLY)` | `self.directory` | `:275` `os.replace(temporary, path)` |
| `claude_controls.py` | `_persist`，`:641` `directory_fd = os.open(parent, os.O_RDONLY)` | `parent` | `:639` `os.replace(temporary, self.path)` |
| `claude_forks.py` | `:699`，紧跟 `os.replace(tmp, self.path)` | `self.path.parent` | `:698` `os.replace(tmp, self.path)` |
| `claude_rate_limits.py` | `:192` `directory_fd = os.open(parent, os.O_RDONLY)` | `parent` | `:190` `os.replace(temporary, self.path)` |
| `dsh_mutations.py` | `connection()` 里 `:43` `fd = os.open(self.path.parent, os.O_RDONLY)` | `self.path.parent` | 无 |
| `engine_capabilities.py` | `:987` `dir_fd = os.open(path.parent, os.O_RDONLY)` | `path.parent` | `:986` `os.replace(temp_name, path)` |
| `session_pins.py` | `:299`，紧跟 `os.replace(tmp, self.path)` | `self.path.parent` | `:298` `os.replace(tmp, self.path)` |
| `session_plans.py` | `:417`，紧跟 `os.replace(tmp, self.path)` | `self.path.parent` | `:416` `os.replace(tmp, self.path)` |
| `session_presentation.py` | `:676`，紧跟 `os.replace(tmp, self.path)` | `self.path.parent` | `:675` `os.replace(tmp, self.path)` |

每个文件按需补 `from cc_remote.platform import files as platform_files`。

- [ ] **Step 3: 确认已清零**

```bash
grep -nE "os\.open\((self\.directory|parent|self\.path\.parent|path\.parent), os\.O_RDONLY\)" \
  cc_remote/wrapper/claude_client_messages.py cc_remote/wrapper/claude_controls.py cc_remote/wrapper/claude_forks.py \
  cc_remote/wrapper/claude_rate_limits.py cc_remote/wrapper/dsh_mutations.py cc_remote/wrapper/engine_capabilities.py \
  cc_remote/wrapper/session_pins.py cc_remote/wrapper/session_plans.py cc_remote/wrapper/session_presentation.py
```

Expected: 无输出。

- [ ] **Step 4: 复跑受影响文件**

用 Step 1 的 `FILES` 和命令。Expected: 计数为 `0`（`tests/test_dsh_mutations.py` 剩下的 `O_NOFOLLOW` 失败属 Task 5A，
不含 `Permission denied`）；每个文件失败数不高于 Step 1。

- [ ] **Step 5: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote/wrapper
git add cc_remote/wrapper/claude_client_messages.py cc_remote/wrapper/claude_controls.py cc_remote/wrapper/claude_forks.py \
  cc_remote/wrapper/claude_rate_limits.py cc_remote/wrapper/dsh_mutations.py cc_remote/wrapper/engine_capabilities.py \
  cc_remote/wrapper/session_pins.py cc_remote/wrapper/session_plans.py cc_remote/wrapper/session_presentation.py
git commit -F - <<'EOF'
fix(wrapper): persist Claude and session stores with platform directory fsync

- Replace directory os.open/fsync blocks in Claude, DSH, capability and session stores with platform_files.fsync_directory
- Publish those store files with platform_files.replace so share-delete readers do not block updates on Windows

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3B: Codex 侧私有 store 的目录 fsync 与原子替换

基线：`private_store_persist` 中其余部分（`ForkJournalError`（Codex fork）、`CodexControlStoreError`、
`CodexClientMessageStoreError`、`CodexProcessClockStoreError`、`RollbackJournalError`、`CodexArchiveJournalError`），以及
`tests/test_codex_turn_leases.py`（4）、`tests/test_codex_provider_repair.py`（2）中首行为
`PermissionError: [Errno 13] Permission denied: '<tmp>'` 的 "other" 条目。
不在本任务：`tests/test_codex_checkpoints.py` 的 23 条 `FileNotFoundError ... '<tmp>'`（2026-10-28 实测：pytest 临时目录过深
触发 MAX_PATH，加短 `--basetemp` 即不复现，见 Global Constraints）与 `Unable to retire Codex checkpoint journal`（P2c Task 6）。

**Files:**
- Modify（每个文件一处，除注明外）：`cc_remote/wrapper/codex_archives.py`、`codex_checkpoints.py`（两处）、
  `codex_client_messages.py`、`codex_controls.py`、`codex_daemon.py`、`codex_forks.py`、`codex_process_clocks.py`、
  `codex_provider_repair.py`、`codex_turn_leases.py`
- 不改：`codex_lifecycle.py`（`:371`）——它把 `os.open(<目录>)` 包在 `try/except OSError: directory_fd = None` 里，
  Windows 上的 `PermissionError` 本来就被吞掉、跳过 fsync，已经可以工作；改动只会改变它在 POSIX 上对 `fsync` 错误的处理。

**Interfaces:**
- Consumes: `platform_files.fsync_directory`、`platform_files.replace`。
- Produces: 无。

- [ ] **Step 1: 确认失败基线**

```bash
FILES="tests/test_codex_forks.py tests/test_codex_worktree_fork.py tests/test_rollback_commands.py tests/test_codex_archives.py tests/test_codex_process_clocks.py tests/test_codex_client_messages.py tests/test_codex_context_settings.py tests/test_codex_control_store.py tests/test_profile_rollback.py tests/test_codex_checkpoints.py tests/test_codex_turn_leases.py tests/test_codex_provider_repair.py"
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line --basetemp="C:\\t\\$(basename $f .py)" 2>&1; done | grep -cE "could not be persisted|Permission denied"
```

Expected: 大于 0（记录数字，Step 4 对比）。

- [ ] **Step 2: 替换目录 fsync 块**

做法同 Task 3A Step 2：

| 文件 | 定位（函数/锚点；`2586f11` 行号） | `<DIR>` | 同块的 `os.replace` |
|---|---|---|---|
| `codex_archives.py` | `:326`，紧跟 `os.replace(tmp, self.path)` | `self.path.parent` | `:325` `os.replace(tmp, self.path)` |
| `codex_checkpoints.py`（manifest） | `migrate_codex_checkpoint_profiles` 里 `:367` `manifest_directory_fd = os.open(` 两行写法 | `manifest_path.parent` | 不改 |
| `codex_checkpoints.py`（迁移） | 同函数 `if staged:` 之下 `:388` | `repository_dir` | 不改（目录重命名） |
| `codex_client_messages.py` | `:510` `directory_fd = os.open(self.path.parent, os.O_RDONLY)` | `self.path.parent` | `:508` `os.replace(temporary, self.path)` |
| `codex_controls.py` | `:545` `directory_fd = os.open(parent, os.O_RDONLY)` | `parent` | `:543` `os.replace(temporary, self.path)` |
| `codex_daemon.py` | `_prepare_profile_standalone` 里 `:272` `directory_fd = os.open(destination.parent, os.O_RDONLY)`（外层 `try/except OSError` 保留） | `destination.parent` | 不改（`:264` 替换的是符号链接） |
| `codex_forks.py` | `:731`，紧跟 `os.replace(tmp, self.path)` | `self.path.parent` | `:730` `os.replace(tmp, self.path)` |
| `codex_process_clocks.py` | `:487` `directory_fd = os.open(self.path.parent, os.O_RDONLY)` | `self.path.parent` | `:485` `os.replace(temporary, self.path)` |
| `codex_provider_repair.py` | `_write_journal` 末尾 `:476` `directory = os.open(journal_dir, os.O_RDONLY)` | `journal_dir` | `:475` `os.replace(temp, path)` |
| `codex_turn_leases.py` | `:295`，紧跟 `os.replace(temporary, self.path)` | `self.path.parent` | `:294` `os.replace(temporary, self.path)` |

每个文件按需补 `from cc_remote.platform import files as platform_files`（`codex_checkpoints.py` 已有
`from cc_remote.platform import locks`，改成 `from cc_remote.platform import files as platform_files, locks`）。

- [ ] **Step 3: 确认已清零**

Run:

```bash
.venv/Scripts/python.exe - <<'EOF'
import pathlib, re
for p in sorted(pathlib.Path("cc_remote/wrapper").glob("*.py")):
    lines = p.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        m = re.search(r"(\w+)\s*=\s*os\.open\(", line)
        if m and re.search(r"os\.fsync\(\s*%s\s*\)" % m.group(1), "\n".join(lines[i:i + 8])) \
                and not re.search(r"O_WRONLY|O_RDWR|O_CREAT", "\n".join(lines[i:i + 4])):
            print(f"{p}:{i + 1}: {line.strip()}")
EOF
```

Expected: 只剩两处：`cc_remote/wrapper/codex_lifecycle.py` 中被 `except OSError` 保护、按上文刻意不改的那处，以及
`cc_remote/wrapper/machine.py` 中 `_write_markdown_file` 的 `temp_fd = os.open(temp_name, ...)`（文件 fsync，不是目录；
由 P2c 处理）。没有其它输出。

- [ ] **Step 4: 复跑受影响文件并按 nodeid 验收 `private_store_persist`**

用 Step 1 的同一个 `FILES` 和命令。Expected: 计数为 `0`；每个文件的失败数不高于基线。然后跑 Task 3A+3B 合计的
`private_store_persist` 全部 nodeid：

```bash
.venv/Scripts/python.exe - <<'EOF'
import json, subprocess, os
d = json.load(open("docs/superpowers/windows-baseline.json", encoding="utf-8"))
ids = [x["nodeid"] for x in d["failures"] if x["owner"] == 2 and x["root_cause_key"] == "private_store_persist"]
env = {**os.environ, "PYTHONUTF8": "1"}
by_file = {}
for nodeid in ids:
    by_file.setdefault(nodeid.split("::")[0], []).append(nodeid)
bad = 0
for path, nodeids in by_file.items():
    base = "C:\\t\\" + os.path.basename(path)[:-3]
    r = subprocess.run([".venv/Scripts/python.exe", "-m", "pytest", "-p", "no:xdist", "-o", "addopts=", "-q",
                        "--tb=no", f"--basetemp={base}", *nodeids], env=env, capture_output=True, text=True, timeout=240)
    bad += r.returncode != 0
    print(path, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.returncode)
print("files with failures:", bad)
EOF
```

Expected: 最后一行 `files with failures: 0`（如某条仍失败，看它的新首行错误：若属于 Task 4A/4B/5A/5B 的根因，记下 nodeid，
在对应任务验收时再确认）。

- [ ] **Step 5: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote/wrapper
git add cc_remote/wrapper/codex_archives.py cc_remote/wrapper/codex_checkpoints.py cc_remote/wrapper/codex_client_messages.py \
  cc_remote/wrapper/codex_controls.py cc_remote/wrapper/codex_daemon.py cc_remote/wrapper/codex_forks.py \
  cc_remote/wrapper/codex_process_clocks.py cc_remote/wrapper/codex_provider_repair.py cc_remote/wrapper/codex_turn_leases.py
git commit -F - <<'EOF'
fix(wrapper): persist Codex stores with platform directory fsync

- Replace directory os.open/fsync blocks in Codex journals and stores with platform_files.fsync_directory
- Publish Codex store files with platform_files.replace so share-delete readers do not block updates on Windows

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4A: 凭据文件私有化与测试里的权限位断言

基线：`private_check_st_mode` 中 `tests/test_devices.py` 的 `device credential file must not be accessible by group/others`（1）；
"other" 中首行为 `AssertionError: assert (33206 & 511) == 384` 的 `tests/test_claude_models.py`（4）、`tests/test_devices.py`（2）、
`tests/test_codex_readiness.py`（1，需 P2d 夹具后才可运行）、`tests/test_codex_daemon_restart.py`（1），
`tests/test_history_store.py` 的 `'0o666' == '0o600'`（1），`tests/test_workspaces.py` 的 `438 != 384`、`511 != 448`（4）。
不在本任务：`tests/test_codex_task_activity.py`/`test_codex_agent_activity.py` 的 `Task state directory must be private`
（18 条，源自 `cc_remote/task_store.py`，按基线归属规则 `task_*` 属 P4，见 P2d"跨子项目依赖"）。

**Files:**
- Modify: `cc_remote/config.py`（只改 `_load_device_config`）
- Modify: `cc_remote/device.py`（`_write_private_text`）
- Modify: `cc_remote/wrapper/dsh_pair.py`（`save_connection`）
- Modify tests: `tests/test_devices.py:92,335`、`tests/test_claude_models.py:116`、`tests/test_codex_daemon_restart.py:44`、
  `tests/test_history_store.py:596`、`tests/test_workspaces.py:250,251,577,632,691`

**Interfaces:**
- Consumes: Task 1 的 `perms.owned_info`、`perms.private_mode_info`、`perms.no_foreign_write_info`；
  `perms.make_private(path, *, directory: bool) -> None`（P1）；`tests/platform_assertions.py`（Task 1）。
- Produces: 无。

- [ ] **Step 1: 先把测试断言改成平台无关（它们是失败测试）**

每个文件 import 区加 `from tests.platform_assertions import assert_private_dir, assert_private_file`（只导入用到的），然后：

| 文件:行 | 原断言 | 新断言 |
|---|---|---|
| `tests/test_devices.py:92` | `assert os.stat(path).st_mode & 0o777 == 0o600` | `assert_private_file(path)` |
| `tests/test_devices.py:335` | `assert os.stat(target).st_mode & 0o777 == 0o600` | `assert_private_file(target)` |
| `tests/test_claude_models.py:116` | `assert path.stat().st_mode & 0o777 == 0o600` | `assert_private_file(path)` |
| `tests/test_codex_daemon_restart.py:44` | `assert os.stat(path).st_mode & 0o777 == 0o600` | `assert_private_file(path)` |
| `tests/test_history_store.py:596` | `assert oct(os.stat(store.path).st_mode & 0o777) == "0o600"` | `assert_private_file(store.path)` |
| `tests/test_workspaces.py:250` | `self.assertEqual(workspace.stat().st_mode & 0o777, 0o700)` | `assert_private_dir(workspace)` |
| `tests/test_workspaces.py:251` | `self.assertEqual((workspace / "WORK.md").stat().st_mode & 0o777, 0o600)` | `assert_private_file(workspace / "WORK.md")` |
| `tests/test_workspaces.py:577` | `self.assertEqual((workspace / "uploads").stat().st_mode & 0o777, 0o700)` | `assert_private_dir(workspace / "uploads")` |
| `tests/test_workspaces.py:632` | `self.assertEqual(policy.stat().st_mode & 0o777, 0o600)` | `assert_private_file(policy)` |
| `tests/test_workspaces.py:691` | `self.assertEqual(policy.stat().st_mode & 0o777, 0o600)` | `assert_private_file(policy)` |

（POSIX 上 `assert_private_file`/`assert_private_dir` 与原断言逐字等价。）

Run: `for f in tests/test_devices.py tests/test_claude_models.py tests/test_codex_daemon_restart.py tests/test_history_store.py tests/test_workspaces.py; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1 | grep -E "assert_private|device credential|33206|passed|failed"; done`
Expected: Windows 上原 `33206 & 511` 断言不再出现；`tests/test_devices.py` 仍有 `device credential file must not be
accessible by group/others`（下一步修产品代码）。

- [ ] **Step 2: `_load_device_config`**

`cc_remote/config.py` 的 `_load_device_config`：

```python
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ValueError(
            f"device credential file must not be accessible by group/others: {path}")
```

改为：

```python
    if not perms.private_mode_info(path, path.stat()):
        raise ValueError(
            f"device credential file must not be accessible by group/others: {path}")
```

补 `from cc_remote.platform import perms`。

- [ ] **Step 3: `device._write_private_text` 与 `dsh_pair.save_connection`**

`cc_remote/device.py` 的 `_write_private_text`：把 `os.replace(staged, path)` 和 `os.chmod(path, 0o600)` 两行改为：

```python
        platform_files.replace(staged, path)
        perms.make_private(path, directory=False)
```

并加 `from cc_remote.platform import files as platform_files, perms`。POSIX 上 `make_private(path, directory=False)` 就是
`os.chmod(path, 0o600)`，与原两行逐字等价；Windows 上它给凭据设独占 DACL（失败时抛 `OSError`，与 P1 裁定一致）。
（`os.fchmod(fd, 0o600)` 保持：Python 3.13 的 Windows 版有 `os.fchmod`，它只切换只读位。）

`cc_remote/wrapper/dsh_pair.py` 的 `save_connection`：

```python
    info = path.parent.stat()
    if info.st_uid != current_uid() or stat.S_IMODE(info.st_mode) & 0o022:
```

改为：

```python
    info = path.parent.stat()
    if not perms.owned_info(path.parent, info) or not perms.no_foreign_write_info(path.parent, info):
```

同函数里 `os.replace(staging, path)` 改为两行——先把暂存文件设为私有，再原子发布：

```python
        perms.make_private(staging, directory=False)
        platform_files.replace(staging, path)
```

（POSIX 上 `mkstemp` 建出的暂存文件本来就是 0600，`make_private` 的 `chmod(staging, 0o600)` 不改变任何结果，也不会作用于
已发布的 `path`；Windows 上配对 token 在发布前就有独占 DACL，不存在"已发布但仍可被他人读"的窗口。）补
`from cc_remote.platform import files as platform_files, perms`，删除不再使用的 `current_uid` import。

- [ ] **Step 4: 复跑受影响文件**

```bash
FILES="tests/test_devices.py tests/test_claude_models.py tests/test_codex_daemon_restart.py tests/test_history_store.py tests/test_workspaces.py tests/test_dsh_client.py"
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1; done | grep -cE "must not be accessible by group/others|33206 & 511|0o666|438 != 384|511 != 448"
```

Expected: `0`。

- [ ] **Step 5: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote tests/test_devices.py tests/test_claude_models.py tests/test_codex_daemon_restart.py tests/test_history_store.py tests/test_workspaces.py
git add cc_remote/config.py cc_remote/device.py cc_remote/wrapper/dsh_pair.py \
  tests/test_devices.py tests/test_claude_models.py tests/test_codex_daemon_restart.py tests/test_history_store.py tests/test_workspaces.py
git commit -F - <<'EOF'
fix(wrapper): make device and DSH pairing credentials private on every platform

- Check the device credential file with perms.private_mode_info
- Make credentials private with perms.make_private around an atomic platform_files.replace
- Assert file privacy in tests through a shared POSIX/Windows helper

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4B: 读取私有 store 时的属主与权限校验

基线：`private_check_st_mode` 中 owner 2 的 `ClaudeControlStoreError`（12）、`CodexControlStoreError`（8）、
`CodexProcessClockStoreError`（4）、`CodexClientMessageStoreError`（1）；"other" 中 `tests/test_claude_usage.py` 的
`usage helper configuration invalid`（4）。

**Files:**
- Modify: `cc_remote/wrapper/claude_client_messages.py`（`_load`，`:117`）、`claude_controls.py`（`_load`，`:525`）、
  `codex_client_messages.py`（`_load`，`:163`）、`codex_controls.py`（`_load`，`:471`）、`codex_process_clocks.py`（`_load`，`:190`）
- Modify: `cc_remote/wrapper/claude_usage.py`（`ClaudeUsageReader.command`，`:40`）、`dsh_client.py`（`DshConnection.load`，`:123`）
- Modify: `cc_remote/config.py`（`_claude_service_socket` 的 registration 读取，`:162`）

**Interfaces:**
- Consumes: Task 1 的 `perms.owned_info`、`perms.private_mode_info`；P2a 的 `platform_files.open_nofollow`。
- Produces: 无。

- [ ] **Step 1: 确认失败基线**

```bash
FILES="tests/test_claude_controls.py tests/test_codex_controls.py tests/test_codex_control_store.py tests/test_codex_process_clocks.py tests/test_codex_client_messages.py tests/test_claude_client_messages.py tests/test_claude_usage.py tests/test_dsh_client.py tests/test_manual_compaction.py tests/test_history.py tests/test_claude_compaction_history.py tests/test_codex_context_settings.py tests/test_codex_spontaneous_stream.py"
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1; done | grep -cE "not a private bounded file|usage helper configuration invalid"
```

Expected: 大于 0。

- [ ] **Step 2: store `_load` 的 lstat 校验**

五处结构相同。把条件里的两行

```python
            or info.st_uid != current_uid()
            or stat.S_IMODE(info.st_mode) & 0o077
```

（`claude_controls.py`/`codex_controls.py` 是同义的两行，缩进不同）替换为：

```python
            or not perms.owned_info(path, info)
            or not perms.private_mode_info(path, info)
```

其中 `path` 是该处 `lstat()` 的对象：`claude_client_messages.py` 用局部变量 `path`；其余四处用 `self.path`。各文件补
`from cc_remote.platform import perms`；若 `current_uid` 不再被使用，删除对应 import（ruff 会报 F401）。

- [ ] **Step 3: fstat 校验（`claude_usage.py`、`dsh_client.py`、`config.py`）**

`cc_remote/wrapper/claude_usage.py` 的 `ClaudeUsageReader.command`：

```python
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != current_uid()
                        or info.st_mode & 0o077 or info.st_size > MAX_BYTES):
```

改为：

```python
                if (not stat.S_ISREG(info.st_mode)
                        or not perms.owned_info(stream.fileno(), info)
                        or not perms.private_mode_info(stream.fileno(), info)
                        or info.st_size > MAX_BYTES):
```

`cc_remote/wrapper/dsh_client.py` 的 `DshConnection.load`（同时改 no-follow 打开；`O_NONBLOCK` 在 Windows 不存在）：

```python
            fd = platform_files.open_nofollow(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode)
                        or not perms.owned_info(stream.fileno(), info)
                        or not perms.private_mode_info(stream.fileno(), info)
                        or info.st_nlink != 1 or info.st_size > 8192):
                    raise ValueError
```

（POSIX：`open_nofollow` 在原 `O_RDONLY|O_NOFOLLOW|O_NONBLOCK` 上只多了 `O_CLOEXEC`，而 Python 的 `os.open` 本来就默认
不可继承，结果相同。）

`cc_remote/config.py` 的 `_claude_service_socket`（`path = directory / "claude-service.json"` 之后）：

```python
    try:
        fd = platform_files.open_nofollow(path, os.O_RDONLY)
    except FileNotFoundError:
        return ""
    with os.fdopen(fd, "r") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode)
                or not perms.owned_info(source.fileno(), info)
                or not perms.private_mode_info(source.fileno(), info)
                or info.st_size > 4096):
            raise ValueError("Claude service registration must be a private user-owned file")
```

三个文件补 `from cc_remote.platform import files as platform_files, perms`（按实际用到的名字），删除不再使用的
`current_uid` import。

- [ ] **Step 4: 复跑受影响文件**

用 Step 1 的 `FILES` 和命令。Expected: `0`。
`tests/test_manual_compaction.py` 里剩下的 `Claude service directory must be private` 来自 `cc_remote/claude_service/wire.py`（P3），
不在本任务（P2d"跨子项目依赖"第 1 条）。

- [ ] **Step 5: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote
git add cc_remote/config.py cc_remote/wrapper/claude_client_messages.py cc_remote/wrapper/claude_controls.py \
  cc_remote/wrapper/codex_client_messages.py cc_remote/wrapper/codex_controls.py cc_remote/wrapper/codex_process_clocks.py \
  cc_remote/wrapper/claude_usage.py cc_remote/wrapper/dsh_client.py
git commit -F - <<'EOF'
fix(wrapper): check private state through platform ownership helpers

- Replace caller-side st_uid and mode-bit privacy checks in store loaders with perms.owned_info/private_mode_info
- Open the DSH connection and Claude service registration without following links on every platform

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5A: DSH、Artifact 与 agent activity 的 no-follow 打开

基线：root_cause_key `posix_open_flags`（owner 2，共 196 条，Task 5A+5B 合计；`AttributeError: module 'os' has no attribute
'O_NOFOLLOW'` 等）中的：`tests/test_dsh_residency.py`(39)、`test_dsh_goal_recovery.py`(37)、`test_dsh_metadata_recovery.py`(27)、
`test_dsh_mutations.py`(26)、`test_session_artifacts.py`(23)、`test_dsh_artifact_reads.py`(17)、
`test_codex_agent_activity.py`(11)、`test_dsh_interrupt_recovery.py`(3)、`test_dsh_follow_recovery.py`(2)、
`test_dsh_runtime.py`(2)、`test_dsh_client.py`(1，Task 4B 已改)、`test_dsh_features.py`(1)。
`tests/test_codex_readiness.py`(4) 与 `tests/test_workspace_browser.py`(3) 的同类条目分别在 P2d（夹具）和 P2c（`DirHandle`）处理。

**Files:**
- Modify: `cc_remote/wrapper/dsh_mutations.py`（`connection()`）、`session_artifacts.py`（`SessionArtifactStore.__init__`、
  `uploads()` 两处）、`codex_agent_activity.py`（`_read`）

**Interfaces:**
- Consumes: `platform_files.open_nofollow(path, flags, mode=0o600) -> int`（P2a）、`perms.owned_info(..., effective=True)`（Task 1）。
- Produces: 无。

- [ ] **Step 1: 确认失败基线**

```bash
FILES="tests/test_dsh_residency.py tests/test_dsh_goal_recovery.py tests/test_dsh_metadata_recovery.py tests/test_dsh_mutations.py tests/test_session_artifacts.py tests/test_dsh_artifact_reads.py tests/test_codex_agent_activity.py tests/test_dsh_interrupt_recovery.py tests/test_dsh_follow_recovery.py tests/test_dsh_runtime.py tests/test_dsh_features.py"
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1; done | grep -cE "has no attribute '(O_NOFOLLOW|O_NONBLOCK|O_DIRECTORY)'"
```

Expected: 大于 0。

- [ ] **Step 2: 逐处替换**

`cc_remote/wrapper/dsh_mutations.py`（`connection()`）：

```python
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
```

→

```python
            fd = platform_files.open_nofollow(self.path, os.O_CREAT | os.O_RDWR, 0o600)
```

`cc_remote/wrapper/session_artifacts.py`（`uploads()`）：

```python
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
```

→

```python
                    fd = platform_files.open_nofollow(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
```

```python
                        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
```

→

```python
                        fd = platform_files.open_nofollow(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
```

同函数里 `or info.st_uid != current_euid()):` 改为 `or not perms.owned_info(existing.fileno(), info, effective=True)):`；
`SessionArtifactStore.__init__` 的 `if not stat.S_ISDIR(info.st_mode) or info.st_uid != current_euid():` 改为
`if not stat.S_ISDIR(info.st_mode) or not perms.owned_info(self.root, info, effective=True):`
（两处原来都比对 euid，必须传 `effective=True`，POSIX 才逐字等价）。

`cc_remote/wrapper/codex_agent_activity.py`（`_read`）：

```python
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
```

→

```python
        fd = platform_files.open_nofollow(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
```

各文件补 `from cc_remote.platform import files as platform_files`（`session_artifacts.py` 还要 `perms`），删除不再使用的
`current_euid` import。

- [ ] **Step 3: 确认不再有直接属性访问**

Run: `grep -nE "os\.O_(NOFOLLOW|NONBLOCK|DIRECTORY)" cc_remote/wrapper/dsh_mutations.py cc_remote/wrapper/dsh_client.py cc_remote/wrapper/session_artifacts.py cc_remote/wrapper/codex_agent_activity.py cc_remote/config.py | grep -v getattr`
Expected: 无输出。

- [ ] **Step 4: 复跑受影响文件**

用 Step 1 的 `FILES` 和命令。Expected: 计数 `0`；DSH 文件中剩余的 `DSH 会话目录不可读取` 属于 `posix_abs_paths`（P2c Task 4），
`tests/test_codex_agent_activity.py` 剩余的 `Task state directory must be private` 属 P4，均不计入本任务。

- [ ] **Step 5: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote
git add cc_remote/wrapper/dsh_mutations.py cc_remote/wrapper/session_artifacts.py cc_remote/wrapper/codex_agent_activity.py
git commit -F - <<'EOF'
fix(wrapper): open DSH, artifact and agent-activity files without following links

- Use platform_files.open_nofollow where Windows has no O_NOFOLLOW/O_NONBLOCK
- Check artifact ownership with perms.owned_info against the effective uid as before

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5B: Codex rollout、provider repair 与 daemon PID 记录的 no-follow 打开和定位读写

基线：`posix_open_flags` 中其余 owner-2 条目（Codex 侧），以及 `posix_os_calls` 中
`tests/test_codex_provider_repair.py::test_provider_repair_recovers_interrupted_first_line_from_journal`（`os.pread`）。

**Files:**
- Modify: `cc_remote/wrapper/codex_sessions.py`（读取 rollout 的 `flags` 构造处，`:521`）、
  `codex_provider_repair.py`（三处 `flags = os.O_RDWR` + `os.pread`、`_pwrite_all`）、`codex_daemon.py`（`_managed_pid`，`:337`）

**Interfaces:**
- Consumes: `platform_files.open_nofollow`（P2a）、`platform_files.pread(fd, length, offset) -> bytes`、
  `platform_files.pwrite(fd, data, offset) -> int`（P1）、`perms.owned_info`（Task 1）。
- Produces: 无。

- [ ] **Step 1: 确认失败基线**

```bash
FILES="tests/test_codex_provider_repair.py tests/test_codex_archived_rollout.py tests/test_codex_missing_sessions.py tests/test_codex_daemon.py"
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1; done | grep -cE "has no attribute '(O_NOFOLLOW|pread|pwrite)'"
```

Expected: 大于 0（至少 `os.pread` 那条）。

- [ ] **Step 2: 逐处替换**

`cc_remote/wrapper/codex_sessions.py`：

```python
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(expanded, flags)
```

→

```python
    try:
        descriptor = platform_files.open_nofollow(expanded, os.O_RDONLY)
```

`cc_remote/wrapper/codex_provider_repair.py` 的三处：

```python
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
```

（第二处变量是 `rollout`，第三处在 `for original, replacement, _journal, path in reversed(patched):` 循环里）分别改为

```python
    descriptor = platform_files.open_nofollow(path, os.O_RDWR)
```

（`rollout` 处为 `platform_files.open_nofollow(rollout, os.O_RDWR)`，缩进保持原样；POSIX 上只多了 `O_CLOEXEC`，Python
`os.open` 本来就默认不可继承）。文件内全部 `os.pread(descriptor, n, 0)` 改为 `platform_files.pread(descriptor, n, 0)`；
`_pwrite_all` 里的 `os.pwrite(descriptor, payload[written:], offset + written)` 改为
`platform_files.pwrite(descriptor, payload[written:], offset + written)`（POSIX 上二者就是 `os.pread`/`os.pwrite`）。

`cc_remote/wrapper/codex_daemon.py` 的 `_managed_pid`：

```python
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        return None
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        file_stat = os.fstat(descriptor)
        if (not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_uid != current_uid()
                or file_stat.st_size > _PID_RECORD_MAX):
```

→

```python
    descriptor: Optional[int] = None
    try:
        descriptor = platform_files.open_nofollow(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        file_stat = os.fstat(descriptor)
        if (not stat.S_ISREG(file_stat.st_mode)
                or not perms.owned_info(descriptor, file_stat)
                or file_stat.st_size > _PID_RECORD_MAX):
```

各文件补 `from cc_remote.platform import files as platform_files`（及用到的 `perms`；`codex_daemon.py` 在 Task 3B 已导入
`platform_files`）。`codex_daemon.py` 的 `current_uid` 仍被其它函数使用，保留。

- [ ] **Step 3: 确认不再有直接属性访问**

Run: `grep -nE "os\.O_(NOFOLLOW|NONBLOCK|DIRECTORY)|os\.p(read|write)\(" cc_remote/wrapper/codex_sessions.py cc_remote/wrapper/codex_provider_repair.py cc_remote/wrapper/codex_daemon.py | grep -v getattr`
Expected: 无输出。

- [ ] **Step 4: 复跑受影响文件并按 nodeid 验收 `posix_open_flags`**

用 Step 1 的 `FILES` 和命令。Expected: 计数 `0`。再跑 posix_open_flags 根因的全部 owner-2 nodeid（把 Task 3B Step 4 的脚本里
`"private_store_persist"` 换成 `"posix_open_flags"`，并从结果中排除 `tests/test_codex_readiness.py` 与
`tests/test_workspace_browser.py`）。Expected: 除这两个文件外 `files with failures: 0`；DSH 文件中剩余的
`DSH 会话目录不可读取` 属于 `posix_abs_paths`（P2c Task 4），不计入。

- [ ] **Step 5: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote
git add cc_remote/wrapper/codex_sessions.py cc_remote/wrapper/codex_provider_repair.py cc_remote/wrapper/codex_daemon.py
git commit -F - <<'EOF'
fix(wrapper): open Codex rollouts and daemon records without following links on every platform

- Use platform_files.open_nofollow for rollout, provider-repair and daemon PID record opens
- Use platform_files.pread/pwrite where Windows has no positional I/O

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 6: P2b 收尾：按根因验收、Linux 门禁、PR

**Files:** 无代码改动。

- [ ] **Step 1: 四个根因的 owner-2 nodeid 汇总验收**

```bash
.venv/Scripts/python.exe - <<'EOF'
import json, subprocess, os
d = json.load(open("docs/superpowers/windows-baseline.json", encoding="utf-8"))
keys = {"profile_state_migration", "private_store_persist", "posix_open_flags", "private_check_st_mode", "module_timeout"}
skip_files = {"tests/test_codex_readiness.py", "tests/test_workspace_browser.py",
              "tests/test_codex_task_activity.py"}  # P2d fixtures, P2c DirHandle, P4 task_store
env = {**os.environ, "PYTHONUTF8": "1"}
by_file = {}
for x in d["failures"]:
    if x["owner"] == 2 and x["root_cause_key"] in keys:
        path = x["nodeid"].split("::")[0].replace(" (whole module)", "")
        if path not in skip_files:
            by_file.setdefault(path, set()).add(x["nodeid"] if "::" in x["nodeid"] else path)
for path, nodeids in sorted(by_file.items()):
    base = "C:\\t\\" + os.path.basename(path)[:-3]
    r = subprocess.run([".venv/Scripts/python.exe", "-m", "pytest", "-p", "no:xdist", "-o", "addopts=", "-q",
                        "--tb=line", f"--basetemp={base}", *sorted(nodeids)],
                       env=env, capture_output=True, text=True, timeout=240)
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else f"rc={r.returncode}"
    print(f"{path}: {tail}")
EOF
```

Expected: 每行都是 `N passed`（可含 skipped）；任何 `failed` 行要看首行错误：若属于 P3（`claude_service`）或 P4（`task_store`）
的根因，记入 PR 描述的"已知遗留"；若是 `FileNotFoundError`/`WinError 3` 且路径超过 260 字符，加短 `--basetemp` 复跑
（MAX_PATH 属环境，见 Global Constraints）；否则回到对应任务修复。

- [ ] **Step 2: lint 与 diff 检查**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote tests deploy
git diff origin/windows-compat-v2 --check
```

Expected: `All checks passed!`；无输出。

- [ ] **Step 3: 推送并开 PR**

```bash
git push -u origin win/2b-private-state
gh pr create --repo donny-star/cc-remote --base windows-compat-v2 --head win/2b-private-state \
  --title "Windows P2b: persist and verify private wrapper state" --body-file - <<'EOF'
Fixes the owner-2 baseline root causes profile_state_migration, private_store_persist, posix_open_flags and
private_check_st_mode, plus the three whole-module timeouts they caused.

- Directory fsync and atomic replace through cc_remote.platform.files
- perms.owned_info(effective=) / private_mode_info / no_foreign_write_info replace caller-side st_uid/st_mode checks (POSIX expressions unchanged, euid callers keep euid)
- files.open_nofollow / pread / pwrite replace direct O_NOFOLLOW and positional I/O

Checks: Windows per-file pytest for every touched test file and the baseline nodeids of those root causes; ruff; git diff --check.
Known leftovers: Task state directory (cc_remote/task_store.py, P4); Claude service directory (cc_remote/claude_service, P3).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

## 跨子项目依赖

完整列表见 P2d 末尾"跨子项目依赖与待裁定事项"。与本计划直接相关的：

1. **P3**：`cc_remote/claude_service/wire.py:55` 的 service 目录私有判断（`tests/test_manual_compaction.py` 等的
   `Claude service directory must be private`）——请 P3 改用本计划 Task 1 的 `perms.owned_info`/`perms.private_mode_info`。
2. **P4**：`cc_remote/task_store.py`、`cc_remote/task_refresh.py`（`Task state directory must be private`）；基线把这些条目误记在
   owner 2 名下，P2 不修改，P2d Task 8 刷新基线时更正归属。
3. **P5**：MAX_PATH / `LongPathsEnabled` 环境检查（见 Global Constraints；P2 不改注册表）。
