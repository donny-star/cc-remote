# Windows 适配 · 子项目 2c：受控目录遍历、预览与 Work 上传、平台路径测试数据 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用一个平台层 `DirHandle` 取代业务代码里 POSIX 专有的 `os.open(<目录>)` + `dir_fd=` 遍历，让文件预览、Markdown
保存、Work 附件暂存和工作区浏览在 Windows 上工作且保持"每一级都不跟随链接"的防护；同时把测试里写死的 POSIX
绝对路径改成平台正确的绝对路径。

**Architecture:** 新文件 `cc_remote/platform/dirs.py` 提供 `DirHandle`。POSIX 分支持有 `O_DIRECTORY|O_NOFOLLOW` fd，
子项用 `dir_fd` 解析——与现有代码逐字等价。Windows 分支持有用 `FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT`
打开、**不带** `FILE_SHARE_DELETE` 的目录句柄：实测（Win10 19044）这样钉住的目录及其所有祖先都不能被改名
（`WinError 32`/`5`），因此"按拼接路径解析子项 + 子项自身拒绝 reparse point"与 openat 等价；目录内的
创建/替换/删除不受影响。测试数据改用 `tests/platform_paths.py` 的 `native_path()`。

**Tech Stack:** Python 3.13、ctypes（kernel32）、pytest 9。

**Spec:** `docs/superpowers/specs/2026-10-27-windows-compat-design.md`（§3、§3.1）；
`docs/superpowers/plans/2026-10-27-windows-compat-p1-handoff.md`（"`O_NOFOLLOW`/`O_DIRECTORY` 在 Windows 上取值为 0，`dir_fd`
也不支持……需要显式检查 reparse point"；"已推迟的小问题"第 1、2 条）。

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
- 前置：P2a、P2b 已合入 `windows-compat-v2`；本计划从其切分支 `win/2c-dir-walks`。
- 文件归属同 P2b：不得修改 P3/P4/P5 的文件；`tests/conftest.py`、`pytest.ini` 不改。
- 业务模块写 `from cc_remote.platform import files as platform_files`、`from cc_remote.platform.dirs import DirHandle`
  （`machine.py` 有名为 `files`、`dirs` 的局部变量）。
- 测试里的 skip 只能用 `posix_only(reason=…)`、`windows_only(reason=…)`、`requires_symlink(reason=…)`，且只用于测的本来就是
  平台专有行为或夹具必须建符号链接的用例。

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `cc_remote/platform/dirs.py` | 新建 | `DirHandle`：不跟随链接、可钉住的目录句柄与相对子项操作 |
| `tests/test_platform_dirs.py` | 新建 | `DirHandle` 测试 |
| `cc_remote/wrapper/machine.py` | 修改 | `_read_session_file`、`_write_markdown_file`、`_write_staged_attachment`、`_open_work_upload_dir`、`_stash_files`、`_stash_images`、`_stash_work_attachments` |
| `cc_remote/workspaces.py` | 修改 | `WorkRegistry._is_managed_upload_root` |
| `cc_remote/wrapper/workspace_browser.py` | 修改 | `browse_workspace` |
| `tests/test_markdown_preview.py`、`tests/test_wrapper_core_fixes.py`、`tests/test_attachments.py` | 修改 | mkfifo 用例标 `posix_only`；收窄 OSError 容忍；符号链接夹具；`dir_fd` 改名 |
| `tests/platform_paths.py` | 新建 | `native_path()`：把 POSIX 风格夹具路径变成本平台绝对路径 |
| `tests/test_claude_forks.py`、`test_claude_session_fork.py`、`test_dsh_mutations.py`、`test_dsh_attachments.py`、`test_dsh_runtime.py`、`test_dsh_features.py`、`test_dsh_export_scheduling.py`、`test_dsh_terminal.py`、`test_history_turn_changes.py`、`test_turn_changes.py`、`test_command_reliability.py`、`test_session_artifacts.py`、`test_link_preview_images.py` | 修改 | 平台正确的路径与期望值；过长参数 ID |

## 准备工作

```bash
cd /c/Users/xuhaodong/work/cc-remote-p2
git fetch origin
git switch -c win/2c-dir-walks origin/windows-compat-v2
PYTHONUTF8=1 .venv/Scripts/python.exe -c "from cc_remote.platform import files, perms; print(files.open_nofollow, perms.owned_info)"
```

---

### Task 1: `cc_remote/platform/dirs.py` 的 `DirHandle`

基线：无直接 nodeid；Task 2、3 依赖。验收：`tests/test_platform_dirs.py` 在 Windows 与 Linux 全部通过。

**Files:**
- Create: `cc_remote/platform/dirs.py`
- Test: `tests/test_platform_dirs.py`

**Interfaces:**
- Consumes（P2a）：`files.open_nofollow`、`files.replace`、`files._kernel32()`、`files._win32_handle_attributes(handle) -> int`、
  `files._FILE_FLAG_OPEN_REPARSE_POINT`、`files._FILE_ATTRIBUTE_REPARSE_POINT`。
- Produces:

```python
class DirHandle:
    path: str          # absolute path of the opened directory
    fd: int            # POSIX directory fd; Windows CRT fd over the directory HANDLE (os.fstat works)
    @classmethod
    def open_root(cls, path: StrPath) -> "DirHandle": ...
    def open_dir(self, name: str) -> "DirHandle": ...
    def open_file(self, name: str, flags: int, mode: int = 0o600) -> int: ...
    def mkdir(self, name: str, mode: int = 0o700) -> None: ...
    def lstat(self, name: str) -> os.stat_result: ...
    def unlink(self, name: str) -> None: ...
    def replace(self, src: str, dst: str) -> None: ...
    def fsync(self) -> None: ...
    def scandir(self) -> Iterator[os.DirEntry]: ...   # context manager like os.scandir
    def child_path(self, name: str) -> str: ...
    def close(self) -> None: ...                      # idempotent
    def __enter__(self) -> "DirHandle": ...
    def __exit__(self, *exc) -> None: ...
```

  语义：`open_root`/`open_dir` 拒绝最后一级是链接（POSIX `ELOOP`/`ENOTDIR`，Windows `OSError(errno.ELOOP)`），不是目录时抛
  `NotADirectoryError`；`open_file` 在调用方 `flags` 上加 no-follow（POSIX `O_NOFOLLOW|O_CLOEXEC` + `dir_fd`，Windows
  `files.open_nofollow(child_path(name), …)`）。Windows 上 `name` 必须是单个路径分量（不能为空、`.`、`..`、含分隔符或盘符），
  否则抛 `OSError(errno.EINVAL)`；POSIX 不做额外校验（与原 `dir_fd` 调用完全一致）。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_platform_dirs.py`：

```python
from __future__ import annotations

import errno
import os
import stat
import sys

import pytest

from cc_remote.platform import files
from cc_remote.platform.dirs import DirHandle


def _link_directory(target, link) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_walk_open_and_read(tmp_path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "f.txt").write_bytes(b"a\nb\x1a")
    with DirHandle.open_root(tmp_path) as root:
        assert stat.S_ISDIR(os.fstat(root.fd).st_mode)
        with root.open_dir("a") as a, a.open_dir("b") as b:
            fd = b.open_file("f.txt", os.O_RDONLY)
            try:
                assert os.read(fd, 16) == b"a\nb\x1a"
            finally:
                os.close(fd)
            assert b.child_path("f.txt") == os.path.join(str(tmp_path), "a", "b", "f.txt")


def test_create_replace_lstat_unlink_and_scandir(tmp_path) -> None:
    with DirHandle.open_root(tmp_path) as root:
        fd = root.open_file("tmp", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, b"new")
            os.fsync(fd)
        finally:
            os.close(fd)
        (tmp_path / "target").write_bytes(b"old")
        root.replace("tmp", "target")
        root.fsync()
        assert (tmp_path / "target").read_bytes() == b"new"
        assert stat.S_ISREG(root.lstat("target").st_mode)
        root.mkdir("sub", 0o700)
        with root.scandir() as entries:
            assert sorted(entry.name for entry in entries) == ["sub", "target"]
        root.unlink("target")
        assert not (tmp_path / "target").exists()
        with pytest.raises(FileExistsError):
            root.mkdir("sub", 0o700)


def test_directory_link_is_refused_at_every_hop(tmp_path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "f").write_bytes(b"x")
    _link_directory(real, tmp_path / "link")
    with DirHandle.open_root(tmp_path) as root:
        with pytest.raises(OSError) as caught:
            root.open_dir("link")
        assert caught.value.errno in (errno.ELOOP, errno.ENOTDIR)
    with pytest.raises(OSError):
        DirHandle.open_root(tmp_path / "link")


@pytest.mark.requires_symlink(reason="opens a file symbolic link through a directory handle")
def test_file_symlink_is_refused(tmp_path) -> None:
    (tmp_path / "target").write_bytes(b"secret")
    (tmp_path / "link").symlink_to(tmp_path / "target")
    with DirHandle.open_root(tmp_path) as root:
        with pytest.raises(OSError) as caught:
            root.open_file("link", os.O_RDONLY)
        assert caught.value.errno == errno.ELOOP


def test_regular_file_is_not_a_directory(tmp_path) -> None:
    (tmp_path / "f").write_bytes(b"x")
    with DirHandle.open_root(tmp_path) as root:
        with pytest.raises(NotADirectoryError):
            root.open_dir("f")


def test_close_is_idempotent(tmp_path) -> None:
    handle = DirHandle.open_root(tmp_path)
    handle.close()
    handle.close()


@pytest.mark.windows_only(reason="POSIX dir_fd needs no component validation")
@pytest.mark.parametrize("name", ["", ".", "..", "a\\b", "a/b", "C:x"])
def test_windows_rejects_non_component_names(tmp_path, name) -> None:
    with DirHandle.open_root(tmp_path) as root:
        with pytest.raises(OSError) as caught:
            root.open_file(name, os.O_RDONLY)
        assert caught.value.errno == errno.EINVAL


@pytest.mark.windows_only(reason="handle pinning is how Windows replaces openat")
def test_windows_pinned_directory_and_ancestors_cannot_be_renamed(tmp_path) -> None:
    (tmp_path / "parent" / "child").mkdir(parents=True)
    with DirHandle.open_root(tmp_path / "parent" / "child") as child:
        with pytest.raises(PermissionError):
            os.rename(tmp_path / "parent" / "child", tmp_path / "parent" / "moved")
        with pytest.raises(PermissionError):
            os.rename(tmp_path / "parent", tmp_path / "moved")
        fd = child.open_file("ok", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        os.close(fd)
        files.replace(child.child_path("ok"), child.child_path("ok2"))
    os.rename(tmp_path / "parent", tmp_path / "moved")  # released on close


def test_fstat_identity_is_stable(tmp_path) -> None:
    with DirHandle.open_root(tmp_path) as first, DirHandle.open_root(tmp_path) as second:
        a, b = os.fstat(first.fd), os.fstat(second.fd)
        assert (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_platform_dirs.py -p no:xdist -o addopts="" -q`
Expected: 收集失败 `ModuleNotFoundError: No module named 'cc_remote.platform.dirs'`。

- [ ] **Step 3: 实现**

新建 `cc_remote/platform/dirs.py`：

```python
"""Directory handles that never follow a final link, for openat-style walks.

POSIX keeps an ``O_DIRECTORY | O_NOFOLLOW`` descriptor and resolves children
with ``dir_fd`` exactly as the callers did before. Windows has no ``dir_fd``:
it keeps a directory HANDLE opened on the reparse point itself and without
FILE_SHARE_DELETE. Windows refuses to rename or delete a directory (or any
ancestor) while such a handle is open, so resolving children by joined path
cannot be redirected; every child open still refuses reparse points itself.
"""
from __future__ import annotations

import errno
import os
from os import PathLike
from typing import Iterator

from cc_remote.platform import IS_WINDOWS, files

StrPath = str | PathLike[str]

_POSIX_DIR_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _win32_open_directory(path: str) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    file_list_directory, file_read_attributes = 0x1, 0x80
    read_control, synchronize = 0x00020000, 0x00100000
    share_read_write = 0x1 | 0x2  # no FILE_SHARE_DELETE: pins the directory
    open_existing, backup_semantics = 3, 0x02000000
    kernel32 = files._kernel32()
    handle = kernel32.CreateFileW(
        path, file_list_directory | file_read_attributes | read_control | synchronize,
        share_read_write, None, open_existing,
        backup_semantics | files._FILE_FLAG_OPEN_REPARSE_POINT, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = files._win32_handle_attributes(handle)
        if attributes & files._FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(errno.ELOOP, "refusing to open a reparse point", path)
        if not attributes & 0x10:  # FILE_ATTRIBUTE_DIRECTORY
            raise NotADirectoryError(errno.ENOTDIR, "not a directory", path)
        return msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except BaseException:
        kernel32.CloseHandle(wintypes.HANDLE(handle))
        raise


class DirHandle:
    """An open directory whose children are resolved without following links."""

    def __init__(self, path: str, fd: int) -> None:
        self.path = path
        self.fd = fd
        self._closed = False

    @classmethod
    def open_root(cls, path: StrPath) -> "DirHandle":
        absolute = os.path.abspath(os.fspath(path))
        if IS_WINDOWS:
            return cls(absolute, _win32_open_directory(absolute))
        return cls(absolute, os.open(absolute, _POSIX_DIR_FLAGS))

    def child_path(self, name: str) -> str:
        if IS_WINDOWS:
            drive, _ = os.path.splitdrive(name)
            if (not name or name in (".", "..") or drive
                    or "\\" in name or "/" in name):
                raise OSError(errno.EINVAL, "not a single path component", name)
        return os.path.join(self.path, name)

    def open_dir(self, name: str) -> "DirHandle":
        child = self.child_path(name)
        if IS_WINDOWS:
            return DirHandle(child, _win32_open_directory(child))
        return DirHandle(child, os.open(name, _POSIX_DIR_FLAGS, dir_fd=self.fd))

    def open_file(self, name: str, flags: int, mode: int = 0o600) -> int:
        if IS_WINDOWS:
            return files.open_nofollow(self.child_path(name), flags, mode)
        return os.open(
            name, flags | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), mode, dir_fd=self.fd)

    def mkdir(self, name: str, mode: int = 0o700) -> None:
        if IS_WINDOWS:
            os.mkdir(self.child_path(name), mode)
        else:
            os.mkdir(name, mode, dir_fd=self.fd)

    def lstat(self, name: str) -> os.stat_result:
        if IS_WINDOWS:
            return os.lstat(self.child_path(name))
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False)

    def unlink(self, name: str) -> None:
        if IS_WINDOWS:
            os.unlink(self.child_path(name))
        else:
            os.unlink(name, dir_fd=self.fd)

    def replace(self, src: str, dst: str) -> None:
        if IS_WINDOWS:
            files.replace(self.child_path(src), self.child_path(dst))
        else:
            os.replace(src, dst, src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def fsync(self) -> None:
        """Persist directory entries (POSIX); Windows has no directory fsync."""
        if not IS_WINDOWS:
            os.fsync(self.fd)

    def scandir(self) -> Iterator[os.DirEntry]:
        return os.scandir(self.path if IS_WINDOWS else self.fd)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            os.close(self.fd)

    def __enter__(self) -> "DirHandle":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = ["DirHandle"]
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_platform_dirs.py tests/test_platform_harness.py -p no:xdist -o addopts="" -q -rs`
Expected: 全部 PASS（`test_file_symlink_is_refused` 在未开开发者模式的 Windows 上 SKIPPED；
`test_platform_harness.py::test_every_module_imports_on_this_platform` 也通过，说明 `dirs` 在两个平台都能导入）。

- [ ] **Step 5: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote/platform/dirs.py tests/test_platform_dirs.py
git add cc_remote/platform/dirs.py tests/test_platform_dirs.py
git commit -F - <<'EOF'
feat(platform): add pinned no-follow directory handles for openat-style walks

- POSIX keeps O_DIRECTORY|O_NOFOLLOW descriptors and dir_fd operations unchanged
- Windows pins each directory with a reparse-point handle opened without FILE_SHARE_DELETE and resolves single-component children by path

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: 文件预览读取与 Markdown 保存走 `DirHandle`

基线：`tests/test_markdown_preview.py`（54 条，含 `ValueError: 没有权限读取该文件` 22、`Regex pattern did not match` 12、
`posix_os_calls` 的 `mkfifo` 1、`symlink_privilege` 2）、`tests/test_audio_preview.py`（24 + symlink 1）、
`tests/test_spreadsheet_preview.py`（1）、`tests/test_codex_generated_images.py` 中 `dir_fd unavailable` 的条目、
`tests/test_wrapper_core_fixes.py::test_diff_rejects_untracked_fifo_without_opening_it`（`mkfifo`）。
handoff 推迟项：`tests/test_markdown_preview.py:1620` 收窄 OSError 容忍为 winerror 32/5；`:1197` 改用 `requires_symlink`。

**Files:**
- Modify: `cc_remote/wrapper/machine.py`（`WrapperMachine._read_session_file`、`WrapperMachine._write_markdown_file`）
- Modify: `tests/test_markdown_preview.py`（`replace_path_once`、`test_preview_lock_refuses_symlink_without_chmodding_target`、
  `test_markdown_preview_rejects_special_files_without_blocking`）
- Modify: `tests/test_wrapper_core_fixes.py`（`test_diff_rejects_untracked_fifo_without_opening_it`）

**Interfaces:**
- Consumes: Task 1 `DirHandle`。
- Produces: 无（两个方法签名与返回值不变）。

- [ ] **Step 1: 确认失败基线**

```bash
FILES="tests/test_markdown_preview.py tests/test_audio_preview.py tests/test_spreadsheet_preview.py tests/test_codex_generated_images.py"
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=no 2>&1 | tail -1 | sed "s|^|$f: |"; done
```

Expected: `test_markdown_preview.py` 约 54 failed、`test_audio_preview.py` 约 25 failed。

- [ ] **Step 2: 测试侧的平台专有用例**

`tests/test_markdown_preview.py` 的 `replace_path_once` 里：

```python
        try:
            os.replace(replacement, outside)
        except OSError:
            # Windows denies replacing a path while another descriptor holds
            # it open without share-delete, so the race this test injects
            # cannot land there at all. That is consistent with (and even
            # stronger than) the invariant under test: the read snapshot is
            # never affected by a racing replace.
            if sys.platform != "win32":
                raise
```

改为只容忍共享冲突（32）和拒绝访问（5）：

```python
        try:
            os.replace(replacement, outside)
        except OSError as exc:
            # Windows denies replacing a path while another descriptor holds
            # it open without share-delete (ERROR_SHARING_VIOLATION) or while
            # the target is pinned (ERROR_ACCESS_DENIED), so the race this test
            # injects cannot land there at all. That is consistent with (and
            # even stronger than) the invariant under test.
            if getattr(exc, "winerror", None) not in (32, 5):
                raise
```

`test_preview_lock_refuses_symlink_without_chmodding_target`：删除其中捕获 `OSError` 后在 Windows 上 `pytest.skip(...)` 的
`try/except` 包装，直接调用 `(state / ".preview-capabilities.lock").symlink_to(target)`，并给函数加装饰器：

```python
@pytest.mark.requires_symlink(reason="the fixture plants a symbolic link lock file")
```

`test_markdown_preview_rejects_special_files_without_blocking` 与 `tests/test_wrapper_core_fixes.py` 的
`test_diff_rejects_untracked_fifo_without_opening_it` 各加：

```python
@pytest.mark.posix_only(reason="named pipes (os.mkfifo) only exist on POSIX file systems")
```

- [ ] **Step 3: `_read_session_file` 改用 `DirHandle`**

`machine.py` 顶部 import 区加入 `from cc_remote.platform.dirs import DirHandle`。在 `_read_session_file` 中，把从
`file_flags = os.O_RDONLY` 开始、到 `finally: if dir_fd is not None: os.close(dir_fd)` 结束的整段：

```python
        file_flags = os.O_RDONLY
        file_flags |= getattr(os, "O_CLOEXEC", 0)
        file_flags |= getattr(os, "O_NONBLOCK", 0)
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        dir_flags = os.O_RDONLY
        dir_flags |= getattr(os, "O_CLOEXEC", 0)
        dir_flags |= getattr(os, "O_DIRECTORY", 0)
        dir_flags |= getattr(os, "O_NOFOLLOW", 0)
        dir_fd: Optional[int] = None
        try:
            # Walk from an already-open cwd and refuse symlinks at every hop.
            # This closes the realpath/open race where a parent directory could
            # otherwise be replaced by an escaping symlink between both calls.
            dir_fd = os.open(access_root, dir_flags)
            for part in parts[:-1]:
                next_fd = os.open(part, dir_flags, dir_fd=dir_fd)
                os.close(dir_fd)
                dir_fd = next_fd
            fd = os.open(parts[-1], file_flags, dir_fd=dir_fd)
        except FileNotFoundError as exc:
            raise ValueError("文件不存在") from exc
        except PermissionError as exc:
            raise ValueError("没有权限读取该文件") from exc
        except OSError as exc:
            raise ValueError("无法打开该文件") from exc
        finally:
            if dir_fd is not None:
                os.close(dir_fd)
```

替换为：

```python
        directory: Optional[DirHandle] = None
        try:
            # Walk from an already-open cwd and refuse symlinks at every hop.
            # This closes the realpath/open race where a parent directory could
            # otherwise be replaced by an escaping symlink between both calls.
            directory = DirHandle.open_root(access_root)
            for part in parts[:-1]:
                child = directory.open_dir(part)
                directory.close()
                directory = child
            fd = directory.open_file(
                parts[-1], os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError as exc:
            raise ValueError("文件不存在") from exc
        except PermissionError as exc:
            raise ValueError("没有权限读取该文件") from exc
        except OSError as exc:
            raise ValueError("无法打开该文件") from exc
        finally:
            if directory is not None:
                directory.close()
```

（POSIX：`open_root`/`open_dir` 用的就是原 `dir_flags`；`open_file` 在 `O_RDONLY|O_NONBLOCK` 上加 `O_NOFOLLOW|O_CLOEXEC`，
与原 `file_flags` 相同。）

- [ ] **Step 4: `_write_markdown_file` 改用 `DirHandle`**

在 `_write_markdown_file` 中：

1. 删除 `dir_flags = ...` 四行和 `file_flags = ...` 四行；把 `dir_fd: Optional[int] = None` 改为
   `directory: Optional[DirHandle] = None`。
2. `read_current()` 里 `fd = os.open(parts[-1], file_flags, dir_fd=dir_fd)` 改为

```python
                fd = directory.open_file(
                    parts[-1], os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
```

3. 主体里的

```python
            dir_fd = os.open(root, dir_flags)
            for part in parts[:-1]:
                try:
                    next_fd = os.open(part, dir_flags, dir_fd=dir_fd)
                except OSError as exc:
                    raise ValueError("保存路径不能包含符号链接") from exc
                os.close(dir_fd)
                dir_fd = next_fd
```

改为

```python
            directory = DirHandle.open_root(root)
            for part in parts[:-1]:
                try:
                    child = directory.open_dir(part)
                except OSError as exc:
                    raise ValueError("保存路径不能包含符号链接") from exc
                directory.close()
                directory = child
```

4. 临时文件：

```python
            temp_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            temp_flags |= getattr(os, "O_CLOEXEC", 0)
            temp_flags |= getattr(os, "O_NOFOLLOW", 0)
            temp_fd = os.open(temp_name, temp_flags, 0o600, dir_fd=dir_fd)
```

改为

```python
            temp_fd = directory.open_file(
                temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
```

5. 发布与收尾：

```python
            os.replace(
                temp_name,
                parts[-1],
                src_dir_fd=dir_fd,
                dst_dir_fd=dir_fd,
            )
            temp_name = None
            os.fsync(dir_fd)
            saved_stat = os.stat(parts[-1], dir_fd=dir_fd, follow_symlinks=False)
```

改为

```python
            directory.replace(temp_name, parts[-1])
            temp_name = None
            directory.fsync()
            saved_stat = directory.lstat(parts[-1])
```

`finally` 块改为

```python
        finally:
            if temp_name is not None and directory is not None:
                try:
                    directory.unlink(temp_name)
                except FileNotFoundError:
                    pass
            if directory is not None:
                directory.close()
```

6. 更新该方法的 docstring 第二段首句为：`Every path component is opened relative to an already-open cwd without following links.`

- [ ] **Step 5: 确认 `dir_fd=` 只剩 Work 上传相关代码**

Run: `grep -n "dir_fd" cc_remote/wrapper/machine.py`
Expected: 只剩 `_stash_files`、`_write_staged_attachment`、`_open_work_upload_dir`、`_stash_work_attachments`、`_stash_images`
中的出现（Task 3 处理）。

- [ ] **Step 6: 复跑受影响文件**

用 Step 1 的 `FILES`，加上 `tests/test_wrapper_core_fixes.py`。
Expected: `test_markdown_preview.py`、`test_audio_preview.py`、`test_spreadsheet_preview.py` 中原因为 `没有权限读取该文件`、
`dir_fd unavailable`、`mkfifo`、`Regex pattern did not match` 的条目全部通过；
`grep -c "没有权限读取该文件\|dir_fd unavailable"` 对这些文件的 `--tb=line` 输出为 `0`。

- [ ] **Step 7: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote/wrapper/machine.py tests/test_markdown_preview.py tests/test_wrapper_core_fixes.py
git add cc_remote/wrapper/machine.py tests/test_markdown_preview.py tests/test_wrapper_core_fixes.py
git commit -F - <<'EOF'
fix(wrapper): walk preview and Markdown save paths with directory handles

- Read previews and save Markdown through DirHandle so every hop refuses links on Windows and POSIX keeps dir_fd semantics
- Narrow the Windows race-test tolerance to sharing violations and pinned targets
- Mark FIFO tests posix_only and the symlink lock fixture requires_symlink

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3: Work 附件暂存、Work 上传根判定与工作区浏览走 `DirHandle`

基线：`tests/test_attachments.py`（"other" 4 + symlink 1，含 `RuntimeError: secure Work upload directories are unsupported`）、
`tests/test_workspaces.py`（"other" 8 中的 `WinError 32` 2 条与 symlink 1）、`tests/test_workspace_browser.py`（`posix_open_flags` 3 + symlink 2）。

**Files:**
- Modify: `cc_remote/wrapper/machine.py`（`_stash_files`、`_write_staged_attachment`、`_open_work_upload_dir`、
  `_stash_work_attachments`、`_stash_images`）
- Modify: `cc_remote/workspaces.py`（`WorkRegistry._is_managed_upload_root`）
- Modify: `cc_remote/wrapper/workspace_browser.py`（`browse_workspace`）
- Modify: `tests/test_attachments.py`（`swap_then_stash` 包装函数）；该文件与 `tests/test_workspace_browser.py`、
  `tests/test_workspaces.py` 中建符号链接的用例加 `requires_symlink`

**Interfaces:**
- Consumes: `DirHandle`（Task 1）、`perms.owned_info`（P2b）、`platform_files.fchmod(fd, path, mode)`（P1）。
- Produces（签名变化，仅 machine 内部与测试使用）：
  - `WrapperMachine._stash_files(self, prompt, files, temp_dir, engine="claude", *, directory: DirHandle | None = None) -> str`
  - `WrapperMachine._stash_images(self, images, temp_dir, *, directory: DirHandle | None = None) -> list`
  - `WrapperMachine._write_staged_attachment(path: str, name: str, data: bytes, *, directory: DirHandle | None = None) -> None`
  - `WrapperMachine._open_work_upload_dir(cwd: str, msg_id: str)` 的上下文值变为 `tuple[str, DirHandle]`

- [ ] **Step 1: 测试侧改名并标注符号链接夹具**

`tests/test_attachments.py` 的 `swap_then_stash`：

```python
    def swap_then_stash(
        prompt, files, temp_dir, engine="claude", *, directory=None,
    ):
        Path(temp_dir).rename(relocated)
        Path(temp_dir).symlink_to(outside, target_is_directory=True)
        return original(
            prompt, files, temp_dir, engine, directory=directory)
```

并给它所在的测试函数加 `@pytest.mark.requires_symlink(reason="swaps the upload directory for a symbolic link")`。在
`tests/test_attachments.py`、`tests/test_workspace_browser.py`、`tests/test_workspaces.py` 中，对基线里
root_cause_key 为 `symlink_privilege` 的那几条 nodeid 对应的测试函数（用下面命令列出）逐个加
`@pytest.mark.requires_symlink(reason="<该夹具建的链接，如 'plants a symlinked workspace entry'>")`；
`unittest.TestCase` 方法（`test_workspaces.py`）同样可以直接用该 pytest 标记装饰：

```bash
.venv/Scripts/python.exe -c "import json;d=json.load(open('docs/superpowers/windows-baseline.json',encoding='utf-8'));[print(x['nodeid']) for x in d['failures'] if x['owner']==2 and x['root_cause_key']=='symlink_privilege' and x['nodeid'].split('::')[0] in ('tests/test_attachments.py','tests/test_workspace_browser.py','tests/test_workspaces.py')]"
```

- [ ] **Step 2: `_write_staged_attachment` 与两个 stash 方法**

```python
    @staticmethod
    def _write_staged_attachment(
        path: str,
        name: str,
        data: bytes,
        *,
        directory: DirHandle | None = None,
    ) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = (
            directory.open_file(name, flags, 0o600)
            if directory is not None
            else platform_files.open_nofollow(path, flags, 0o600)
        )
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or not perms.owned_info(descriptor, info)
                or info.st_nlink != 1
            ):
                raise RuntimeError("attachment target is not a private file")
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
        finally:
            os.close(descriptor)
```

`_stash_files` 和 `_stash_images` 的关键字参数 `dir_fd: int | None = None` 改为 `directory: DirHandle | None = None`，
调用 `self._write_staged_attachment(path, name, data, dir_fd=dir_fd)` 改为 `..., directory=directory)`。
`machine.py` import 区补 `from cc_remote.platform import files as platform_files, perms`（已有则合并）。

- [ ] **Step 3: `_open_work_upload_dir` 与 `_stash_work_attachments`**

把 `_open_work_upload_dir` 整体替换为：

```python
    @staticmethod
    @contextmanager
    def _open_work_upload_dir(cwd: str, msg_id: str):
        """Bind a Work upload directory without following mutable path links."""
        workspace = uploads = message = None
        try:
            try:
                workspace = DirHandle.open_root(cwd)
                workspace_info = os.fstat(workspace.fd)
                if (
                    not stat.S_ISDIR(workspace_info.st_mode)
                    or not perms.owned_info(workspace.fd, workspace_info)
                ):
                    raise RuntimeError(
                        "Work workspace is not a private directory")

                created_uploads = False
                try:
                    workspace.mkdir("uploads", 0o700)
                    created_uploads = True
                except FileExistsError:
                    pass
                uploads = workspace.open_dir("uploads")
                uploads_info = os.fstat(uploads.fd)
                if (
                    not stat.S_ISDIR(uploads_info.st_mode)
                    or not perms.owned_info(uploads.fd, uploads_info)
                ):
                    raise RuntimeError(
                        "Work uploads path is not a private directory")
                if created_uploads:
                    marker_fd = uploads.open_file(
                        WORK_UPLOADS_MARKER,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                    try:
                        with os.fdopen(
                            marker_fd, "wb", closefd=False,
                        ) as stream:
                            stream.write(WORK_UPLOADS_MARKER_PAYLOAD)
                            stream.flush()
                    finally:
                        os.close(marker_fd)

                marker_fd = uploads.open_file(WORK_UPLOADS_MARKER, os.O_RDONLY)
                try:
                    marker_info = os.fstat(marker_fd)
                    with os.fdopen(
                        marker_fd, "rb", closefd=False,
                    ) as stream:
                        marker_payload = stream.read(
                            len(WORK_UPLOADS_MARKER_PAYLOAD) + 1)
                    if (
                        not stat.S_ISREG(marker_info.st_mode)
                        or not perms.owned_info(marker_fd, marker_info)
                        or marker_info.st_nlink != 1
                        or marker_payload != WORK_UPLOADS_MARKER_PAYLOAD
                    ):
                        raise RuntimeError(
                            "Work uploads path conflicts with workspace content")
                finally:
                    os.close(marker_fd)
                platform_files.fchmod(uploads.fd, uploads.path, 0o700)

                uploads.mkdir(msg_id, 0o700)
                message = uploads.open_dir(msg_id)
                message_info = os.fstat(message.fd)
                if (
                    not stat.S_ISDIR(message_info.st_mode)
                    or not perms.owned_info(message.fd, message_info)
                ):
                    raise RuntimeError("Work message upload path is unsafe")
                platform_files.fchmod(message.fd, message.path, 0o700)
            except OSError as exc:
                raise RuntimeError("Work upload directory is unsafe") from exc
            yield str(Path(cwd) / "uploads" / msg_id), message
        finally:
            for handle in (message, uploads, workspace):
                if handle is not None:
                    handle.close()
```

（原来开头的 `if not hasattr(os, "O_NOFOLLOW") ...: raise RuntimeError("secure Work upload directories are unsupported")`
随之删除：`DirHandle` 在两个平台都提供该防护。POSIX 上 `platform_files.fchmod(fd, path, mode)` 就是 `os.fchmod(fd, mode)`。）

`_stash_work_attachments` 改为：

```python
        with self._open_work_upload_dir(cwd, msg_id) as (directory, handle):
            if files:
                prompt = self._stash_files(
                    prompt, files, directory, engine, directory=handle)
            image_paths = (
                self._stash_images(images, directory, directory=handle)
                if images else [])
        return prompt, image_paths, directory
```

- [ ] **Step 4: `workspaces._is_managed_upload_root`**

```python
    @staticmethod
    def _is_managed_upload_root(path: Path) -> bool:
        directory = None
        marker_fd = -1
        try:
            directory = DirHandle.open_root(path)
            directory_info = os.fstat(directory.fd)
            marker_fd = directory.open_file(WORK_UPLOADS_MARKER, os.O_RDONLY)
            marker = os.fstat(marker_fd)
            with os.fdopen(marker_fd, "rb", closefd=False) as stream:
                payload = stream.read(len(WORK_UPLOADS_MARKER_PAYLOAD) + 1)
            owned = perms.owned_info(marker_fd, marker)
        except OSError:
            return False
        finally:
            if marker_fd >= 0:
                os.close(marker_fd)
            if directory is not None:
                directory.close()
        return (stat.S_ISDIR(directory_info.st_mode)
                and stat.S_ISREG(marker.st_mode)
                and owned
                and marker.st_nlink == 1
                and payload == WORK_UPLOADS_MARKER_PAYLOAD)
```

`cc_remote/workspaces.py` import 区加 `from cc_remote.platform import perms` 与 `from cc_remote.platform.dirs import DirHandle`，
删除不再使用的 `current_euid` import。

- [ ] **Step 5: `workspace_browser.browse_workspace`**

把

```python
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(root, flags)
    try:
        for index, part in enumerate(parts):
            info = os.stat(part, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode) and index == len(parts) - 1:
                return {"root": root, "path": candidate, "kind": "file"}
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("不支持打开符号链接或特殊文件")
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
```

改为

```python
    directory = DirHandle.open_root(root)
    try:
        for index, part in enumerate(parts):
            info = directory.lstat(part)
            if stat.S_ISREG(info.st_mode) and index == len(parts) - 1:
                return {"root": root, "path": candidate, "kind": "file"}
            if not stat.S_ISDIR(info.st_mode) or platform_files.is_reparse_point(
                    directory.child_path(part)):
                raise ValueError("不支持打开符号链接或特殊文件")
            child = directory.open_dir(part)
            directory.close()
            directory = child
        info = os.fstat(directory.fd)
```

`with os.scandir(fd) as iterator:` 改为 `with directory.scandir() as iterator:`；函数末尾对应的 `finally: os.close(fd)`
改为 `finally: directory.close()`。import 区加 `from cc_remote.platform import files as platform_files` 和
`from cc_remote.platform.dirs import DirHandle`。（POSIX 上 `lstat` 已能看出符号链接，`is_reparse_point` 是 Windows 上识别
junction 的必要补充——junction 的 `lstat` 是 `S_IFDIR`。）

- [ ] **Step 6: 复跑受影响文件**

```bash
FILES="tests/test_attachments.py tests/test_workspaces.py tests/test_workspace_browser.py tests/test_work_context.py tests/test_markdown_preview.py"
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line -rs 2>&1 | tail -3 | sed "s|^|$f: |"; done
grep -n "dir_fd" cc_remote/wrapper/machine.py cc_remote/workspaces.py cc_remote/wrapper/workspace_browser.py
```

Expected: 前三个文件 0 failed（符号链接用例 SKIPPED 且原因含 `requires_symlink`）；`grep` 无输出。

- [ ] **Step 7: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote tests/test_attachments.py tests/test_workspaces.py tests/test_workspace_browser.py
git add cc_remote/wrapper/machine.py cc_remote/workspaces.py cc_remote/wrapper/workspace_browser.py \
  tests/test_attachments.py tests/test_workspaces.py tests/test_workspace_browser.py
git commit -F - <<'EOF'
fix(wrapper): stage Work uploads and browse workspaces through directory handles

- Replace dir_fd walks in Work upload staging, upload-root detection and workspace browsing with DirHandle
- Check upload ownership with perms.owned_info and refuse Windows junctions while browsing
- Mark symlink-planting fixtures requires_symlink

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: 测试里的 POSIX 绝对路径改为平台绝对路径

基线：root_cause_key `posix_abs_paths`（owner 2，156 条：`DshError: DSH 会话目录不可读取`135、
`ClaudeForkJournalError: fork cwd must be absolute`21）；"other" 中 `tests/test_history_turn_changes.py`（10）、
`tests/test_turn_changes.py`（3）、`tests/test_claude_session_fork.py`（路径相关）、`tests/test_command_reliability.py`
（`/repo/component`）。根因：夹具把 `"/tmp"`、`"/repo"` 当作绝对 cwd，Windows 上 `Path("/repo").is_absolute()` 为 False，
产品代码正确地拒绝了它们；产品侧的 `os.path.normpath` 在 Windows 上把 `/` 变成 `\`，期望值也要按平台算。

**Files:**
- Create: `tests/platform_paths.py`
- Modify: `tests/test_claude_forks.py`、`tests/test_claude_session_fork.py`、`tests/test_dsh_mutations.py`、
  `tests/test_dsh_attachments.py`、`tests/test_dsh_runtime.py`、`tests/test_dsh_features.py`、
  `tests/test_dsh_export_scheduling.py`、`tests/test_dsh_terminal.py`、`tests/test_history_turn_changes.py`、
  `tests/test_turn_changes.py`、`tests/test_command_reliability.py`

**Interfaces:**
- Produces: `tests.platform_paths.native_path(path: str) -> str`——POSIX 原样返回；Windows 返回 `os.path.abspath(path)`
  （`"/repo/src/code.py"` → `"C:\\repo\\src\\code.py"`）。不访问文件系统。

- [ ] **Step 1: 新建 helper**

```python
"""Fixture paths written POSIX-style, made absolute for the running platform."""
from __future__ import annotations

import os
import sys


def native_path(path: str) -> str:
    """``"/repo/x"`` on POSIX; ``"C:\\repo\\x"`` (current drive) on Windows."""
    if sys.platform != "win32":
        return path
    return os.path.abspath(path)
```

- [ ] **Step 2: 逐文件替换字面量**

每个文件 import 区加 `from tests.platform_paths import native_path`，在 import 之后定义所需常量，然后把字符串字面量替换为常量
（只替换下表列出的字面量；f-string 里以这些前缀开头的路径改为 `os.path.join(常量, "子路径"...)`）：

| 文件 | 常量定义 | 替换 |
|---|---|---|
| `tests/test_claude_forks.py` | `REPO = native_path("/repo")` | 全部 31 处 `"/repo"` → `REPO` |
| `tests/test_claude_session_fork.py` | `COMPONENT = native_path("/repo/component")` | `"/repo/component"` → `COMPONENT` |
| `tests/test_command_reliability.py` | `COMPONENT = native_path("/repo/component")` | 3 处 `"/repo/component"` → `COMPONENT` |
| `tests/test_dsh_mutations.py` | `TMP = native_path("/tmp")`；`FORK_CWD = native_path("/tmp/fork-cwd")` | `"/tmp"` → `TMP`；3 处 `"/tmp/fork-cwd"` → `FORK_CWD` |
| `tests/test_dsh_attachments.py` | `TMP = native_path("/tmp")` | 2 处 `cwd="/tmp"` → `cwd=TMP` |
| `tests/test_dsh_runtime.py` | `TMP = native_path("/tmp")` | 10 处 `"/tmp"` → `TMP` |
| `tests/test_dsh_features.py` | `TMP = native_path("/tmp")` | 2 处 `"/tmp"` → `TMP` |
| `tests/test_dsh_export_scheduling.py` | `TMP = native_path("/tmp")` | 1 处 `"/tmp"` → `TMP` |
| `tests/test_dsh_terminal.py` | `TMP = native_path("/tmp")` | 1 处 `"/tmp"` → `TMP` |
| `tests/test_history_turn_changes.py` | `REPO = native_path("/repo")` | `"/repo"` → `REPO`；`"/repo/README.md"` → `os.path.join(REPO, "README.md")`；`"/repo/src/code.py"` → `os.path.join(REPO, "src", "code.py")`；`"/repo/code.py"` → `os.path.join(REPO, "code.py")` |
| `tests/test_turn_changes.py` | `REPO = native_path("/repo")` | `"/repo"` → `REPO`；`"/repo/src/code.py"` → `os.path.join(REPO, "src", "code.py")`；`"/repo/src/renamed.py"` → `os.path.join(REPO, "src", "renamed.py")` |

`tests/test_turn_changes.py` 里断言 diff 头的 `assert "--- src/code.py" in update` 改为
`assert f"--- {os.path.normpath('src/code.py')}" in update`（产品用 `os.path.normpath` 规范化工具报告的路径；Windows 原生
Claude/Codex 报告的就是反斜杠路径，这是期望行为）。其余对相对路径 `"src/code.py"` 的期望值同样包一层 `os.path.normpath(...)`，
只在断言里改，夹具输入保持 `/` 写法（验证产品对两种分隔符都能规范化）。

用 grep 确认没有遗漏：

```bash
grep -nE "\"/(tmp|repo)(/[^\"]*)?\"" tests/test_claude_forks.py tests/test_claude_session_fork.py tests/test_dsh_mutations.py tests/test_dsh_attachments.py tests/test_dsh_runtime.py tests/test_dsh_features.py tests/test_dsh_export_scheduling.py tests/test_dsh_terminal.py tests/test_history_turn_changes.py tests/test_turn_changes.py tests/test_command_reliability.py | grep -v native_path
```

Expected: 无输出。

- [ ] **Step 3: 复跑受影响文件（含经 `from tests.test_dsh_mutations import prepared` 复用夹具的 DSH 文件）**

```bash
FILES="tests/test_claude_forks.py tests/test_claude_session_fork.py tests/test_command_reliability.py tests/test_history_turn_changes.py tests/test_turn_changes.py $(ls tests/test_dsh_*.py | tr '\n' ' ')"
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line 2>&1; done | grep -cE "DSH 会话目录不可读取|fork cwd must be absolute|== '/repo|== \['/tmp"
```

Expected: `0`；每个文件失败数不高于基线。

- [ ] **Step 4: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check tests
git add tests/platform_paths.py tests/test_claude_forks.py tests/test_claude_session_fork.py tests/test_command_reliability.py \
  tests/test_dsh_mutations.py tests/test_dsh_attachments.py tests/test_dsh_runtime.py tests/test_dsh_features.py \
  tests/test_dsh_export_scheduling.py tests/test_dsh_terminal.py tests/test_history_turn_changes.py tests/test_turn_changes.py
git commit -F - <<'EOF'
test(wrapper): build absolute fixture paths for the running platform

- Add tests.platform_paths.native_path and use it for /tmp and /repo fixture working directories
- Compare tool-reported paths against os.path.normpath expectations

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5: Artifact 发现的路径期望与过长参数 ID

基线：`tests/test_session_artifacts.py` 的 "other" 79 条（`assert ['C:\\tmp\\ou...'] == ['/tmp/output/report.pdf']` 等）与
symlink 1 条；`long_test_id_env`：`tests/test_link_preview_images.py::test_invalid_or_oversized_payloads_fail_closed[xxxx…]`
的 setup/teardown 两条（`ValueError: the environment variable is longer than 32767 characters`）。

**Files:**
- Modify: `tests/test_session_artifacts.py`
- Modify: `tests/test_link_preview_images.py`

**Interfaces:**
- Consumes: `tests.platform_paths.native_path`（Task 4）。

- [ ] **Step 1: Artifact 期望值按平台计算**

`local_links(text, cwd)` 用 `os.path.abspath` 规范化发现的路径（产品行为正确）。在 `tests/test_session_artifacts.py` import 区加
`from tests.platform_paths import native_path`，并把所有"传给 `local_links`/`store.text`/`store.history` 的 cwd 字面量"和
"期望列表里的绝对路径字面量"包一层 `native_path(...)`；输入文本（模型输出里的路径字符串）保持原样。示例（同一模式应用到
文件内所有 `local_links(..., "/workspace") == [...]` 断言）：

```python
    assert local_links(text, native_path("/workspace")) == [
        native_path("/project/a b.py"), native_path("/workspace/docs/report.md"),
        native_path("/tmp/report.pdf"), native_path("/output/page.html")]
```

```python
    text = f"Files in `/tmp/output/`:\n\n{table}"
    expected = [native_path("/tmp/output/report.pdf")]
    if "`chart.svg`" in table:
        expected.append(native_path("/tmp/output/chart.svg"))
    assert local_links(text, native_path("/workspace")) == expected
```

`native_path` 对相对路径拼接结果同样适用：`native_path("/workspace/output/报告 中文.pdf")` 等于
`os.path.abspath(os.path.join(native_path("/workspace"), "output", "报告 中文.pdf"))`。改完后用下面命令找出仍未包裹的绝对路径字面量：

```bash
grep -nE "\"/(tmp|workspace|project|output|repo)[^\"]*\"" tests/test_session_artifacts.py | grep -v native_path | grep -vE "text|f\"|'''|\"\"\""
```

Expected: 无输出（剩下的只应是模型文本里的字符串）。基线里该文件 `symlink_privilege` 的 1 条 nodeid 给对应函数加
`@pytest.mark.requires_symlink(reason="the fixture links an artifact path")`。

- [ ] **Step 2: 过长参数 ID**

`tests/test_link_preview_images.py` 的参数化：

```python
@pytest.mark.parametrize("content", [
    b"", b"\x89PNG\r\n\x1a\n", b"GIF89a", b"<svg/>",
    b"\0\0\1\0\xff\xff", b"RIFF\0\0\0\0WEBPVP8X", b"\xff\xd8\xff",
    b"x" * (256 * 1024 + 1),
])
```

改为带显式 `ids`（pytest 默认把 256 KiB 的 bytes 写进 `PYTEST_CURRENT_TEST`，超过 Windows 环境变量 32767 字符上限）：

```python
@pytest.mark.parametrize("content", [
    b"", b"\x89PNG\r\n\x1a\n", b"GIF89a", b"<svg/>",
    b"\0\0\1\0\xff\xff", b"RIFF\0\0\0\0WEBPVP8X", b"\xff\xd8\xff",
    b"x" * (256 * 1024 + 1),
], ids=["empty", "png-signature", "gif-signature", "svg", "ico-header",
        "webp-header", "jpeg-signature", "oversized"])
```

- [ ] **Step 3: 复跑**

```bash
for f in tests/test_session_artifacts.py tests/test_link_preview_images.py; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=line -rs 2>&1 | tail -2 | sed "s|^|$f: |"; done
```

Expected: 两个文件 0 failed、0 errors（`requires_symlink` 用例可为 SKIPPED）。

- [ ] **Step 4: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check tests/test_session_artifacts.py tests/test_link_preview_images.py
git add tests/test_session_artifacts.py tests/test_link_preview_images.py
git commit -F - <<'EOF'
test(wrapper): expect platform-absolute artifact paths and bound parametrized ids

- Compare discovered artifact paths with native_path expectations
- Give oversized-payload cases explicit ids so PYTEST_CURRENT_TEST fits Windows limits

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 6: P2c 收尾

**Files:** 无代码改动。

- [ ] **Step 1: 本计划涉及的全部测试文件逐个跑**

```bash
FILES="tests/test_platform_dirs.py tests/test_markdown_preview.py tests/test_audio_preview.py tests/test_spreadsheet_preview.py tests/test_codex_generated_images.py tests/test_wrapper_core_fixes.py tests/test_attachments.py tests/test_workspaces.py tests/test_workspace_browser.py tests/test_claude_forks.py tests/test_claude_session_fork.py tests/test_command_reliability.py tests/test_history_turn_changes.py tests/test_turn_changes.py tests/test_session_artifacts.py tests/test_link_preview_images.py $(ls tests/test_dsh_*.py | tr '\n' ' ')"
for f in $FILES; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="" -q --tb=no 2>&1 | tail -1 | sed "s|^|$f: |"; done
```

Expected: 与开工前相比每个文件失败数严格不增；本计划各任务列出的基线条目全部通过。剩余失败只能来自 P2d 的根因
（进程扫描、Codex daemon、可执行文件、`killpg`），在 PR 描述里按文件列出。

- [ ] **Step 2: lint、diff 检查、推送与 PR**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote tests deploy
git diff origin/windows-compat-v2 --check
git push -u origin win/2c-dir-walks
gh pr create --repo donny-star/cc-remote --base windows-compat-v2 --head win/2c-dir-walks \
  --title "Windows P2c: no-follow directory walks and platform fixture paths" --body-file - <<'EOF'
- cc_remote.platform.dirs.DirHandle: POSIX keeps O_DIRECTORY|O_NOFOLLOW + dir_fd; Windows pins reparse-point-refusing directory handles without FILE_SHARE_DELETE
- File previews, Markdown save, Work upload staging, upload-root detection and workspace browsing use it
- Test fixtures build platform-absolute paths; FIFO tests posix_only; symlink fixtures requires_symlink; bounded parametrize ids

Checks: Windows per-file pytest for every touched test file; ruff; git diff --check. Linux pytest runs in CI.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```
