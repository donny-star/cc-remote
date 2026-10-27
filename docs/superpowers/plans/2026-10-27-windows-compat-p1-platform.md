# Windows 适配 · 子项目 1：平台兼容层与测试基线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 `cc_remote/platform/` 兼容层，修掉导致 Windows 上 145 个测试模块收集失败的
import 问题，并在 Windows 上跑出一份按子项目归属分类的测试失败基线。

**Architecture:** 平台差异都收进 `cc_remote/platform/` 下的小模块（locks、files、identity、
perms、win32_process、procs、executables、paths、signals）。POSIX 分支直接调用原生 API，
行为不变；Windows 分支用 pywin32 和 ctypes 实现。已有的 `wrapper/process_scan.py` 按平台
分派到 `win32_process`。业务代码只做机械替换（`fcntl.flock`→`locks.flock`，
`os.getuid()`→`current_uid()`），带判断的改动留给子项目 2～5。

**Tech Stack:** Python 3.13（uv 管理）、pywin32 312、ctypes、pytest 9 + pytest-xdist。

**Spec:** `docs/superpowers/specs/2026-10-27-windows-compat-design.md`（重点看 §3 和 §3.1）

## Global Constraints

- POSIX 下的行为必须完全不变；Linux CI（fork 内 PR 自动跑的 ubuntu-24.04 job）不能出现新的失败。
- Python 固定为 3.13（锁文件仍按 `--python-version 3.10 --universal` 生成，代码不能用 3.11+ 才有的语法）。
- 新依赖只加 `pywin32==312 ; sys_platform == "win32"`。
- Windows 上一律以 UTF-8 模式运行测试：`.venv/Scripts/python.exe -X utf8 -m pytest …`。
- skip 只能用 `@pytest.mark.posix_only(reason="…")` 或 `@pytest.mark.windows_only(reason="…")`，`reason` 必填。
- 工作目录：`C:\Users\xuhaodong\work\cc-remote-p1`（worktree），分支 `win/1-platform`，基于 `windows-compat-v2`。
- 提交信息结尾带：`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
- 依赖安装时，如果 pypi.org 的 TLS 握手失败，改用 `--index-url https://pypi.tuna.tsinghua.edu.cn/simple`。

## 准备工作（执行者开始前做一次）

```bash
cd /c/Users/xuhaodong/work/cc-remote
git push -u origin windows-compat-v2
git worktree add ../cc-remote-p1 -b win/1-platform windows-compat-v2
cd ../cc-remote-p1
uv venv -q --python 3.13 .venv
uv pip install -q --python .venv/Scripts/python.exe -r requirements-dev.txt
```

## 文件结构

| 文件 | 职责 |
|---|---|
| `cc_remote/platform/__init__.py` | 提供 `IS_WINDOWS`；在 Windows 上把进程的 fd 默认模式设为二进制 |
| `cc_remote/platform/locks.py` | 与 `fcntl.flock` 语义一致的建议锁 |
| `cc_remote/platform/files.py` | 带 share-delete 的打开、可重试的 replace、目录 fsync、强制删除目录树、`pread`/`pwrite`、`fchmod` |
| `cc_remote/platform/identity.py` | `current_uid`/`current_euid`/`current_sid`/`FOREIGN_UID` |
| `cc_remote/platform/perms.py` | `make_private`/`is_private` |
| `cc_remote/platform/win32_process.py` | Win32 进程原语：创建时间、命令行、父进程表、属主 SID、pid 列表 |
| `cc_remote/platform/procs.py` | `popen_group`/`create_subprocess_exec_group`/`killpg`/`SIGKILL`/`SIGTERM` |
| `cc_remote/platform/executables.py` | 解析 npm `.cmd` shim、在 Windows 上查找可执行文件 |
| `cc_remote/platform/paths.py` | `venv_python`/`venv_pythonw` |
| `cc_remote/platform/signals.py` | `install_shutdown_handler` |
| `cc_remote/__init__.py` | 加一行 `import cc_remote.platform`，让上面的二进制模式设置随包导入生效 |
| `cc_remote/wrapper/process_scan.py` | 新增 Windows 分派 |
| `tests/conftest.py`, `pytest.ini` | 平台标记、UTF-8 模式检查 |
| `tests/test_platform_*.py` | 新增测试 |
| `docs/superpowers/windows-baseline.md` | 基线清单 |

---

### Task 1: 平台包骨架、测试标记与 pywin32 依赖

**Files:**
- Create: `cc_remote/platform/__init__.py`
- Modify: `cc_remote/__init__.py`（在 `__version__` 之后加一行 import）
- Modify: `pytest.ini`（markers 段）
- Modify: `tests/conftest.py`（末尾追加两个 hook）
- Modify: `requirements-common.txt`，重新生成 `requirements.lock`、`requirements-wrapper.lock`、`requirements-relay.lock`
- Test: `tests/test_platform_harness.py`

**Interfaces:**
- Produces: `cc_remote.platform.IS_WINDOWS: bool`；pytest 标记 `posix_only(reason=...)`、`windows_only(reason=...)`；导入 `cc_remote` 后，Windows 上 `os.open` 默认以二进制模式打开。

- [ ] **Step 1: 写失败测试** `tests/test_platform_harness.py`

```python
"""Self-checks for the cross-platform test harness and package import side effects."""
from __future__ import annotations

import os
import sys

import pytest

import cc_remote
from cc_remote import platform as cc_platform


def test_is_windows_matches_interpreter() -> None:
    assert cc_platform.IS_WINDOWS is (sys.platform == "win32")


@pytest.mark.posix_only(reason="harness self-check: must not run on Windows")
def test_posix_only_marker_skips_on_windows() -> None:
    assert sys.platform != "win32"


@pytest.mark.windows_only(reason="harness self-check: must not run on POSIX")
def test_windows_only_marker_skips_on_posix() -> None:
    assert sys.platform == "win32"


def test_os_open_is_binary_after_importing_package(tmp_path) -> None:
    assert cc_remote.__version__
    path = tmp_path / "raw"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.write(fd, b"a\nb\x1ac")
    finally:
        os.close(fd)
    assert path.read_bytes() == b"a\nb\x1ac"
    fd = os.open(path, os.O_RDONLY)
    try:
        assert os.read(fd, 16) == b"a\nb\x1ac"
    finally:
        os.close(fd)
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/Scripts/python.exe -X utf8 -m pytest tests/test_platform_harness.py -p no:xdist -o addopts="" -q`
Expected: 失败，报 `ModuleNotFoundError: No module named 'cc_remote.platform'`；修好 import 之后，还会报 `'posix_only' not found in markers`。

- [ ] **Step 3: 实现**

`cc_remote/platform/__init__.py`：

```python
"""Platform seams: every POSIX/Windows difference cc-remote relies on lives here.

POSIX branches call the native API unchanged. Windows branches use pywin32 or
ctypes. Business modules import these helpers instead of branching on
``sys.platform`` themselves.
"""
from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes

    # The UCRT opens os.open() descriptors in text mode by default, which
    # rewrites "\n" as "\r\n" and treats 0x1A as EOF. Every cc-remote fd user
    # expects POSIX byte semantics, so switch the process-wide default once.
    _O_BINARY = 0x8000
    if ctypes.CDLL("ucrtbase")._set_fmode(_O_BINARY) != 0:
        raise ImportError("cannot switch the C runtime to binary file mode")
```

在 `cc_remote/__init__.py` 的 `__version__ = "5.3.0"` 之后追加：

```python

import cc_remote.platform  # noqa: E402,F401  (process-wide platform setup)
```

`pytest.ini` 的 `markers =` 段改为：

```ini
markers =
    e2e: live relay-wrapper-engine tests that may consume model tokens
    posix_only(reason): behavior that only exists on POSIX (reason is required)
    windows_only(reason): behavior that only exists on Windows (reason is required)
```

`tests/conftest.py` 末尾追加（如果文件顶部还没有 `import sys`，在 `import pytest` 前补上）：

```python
def pytest_configure(config: pytest.Config) -> None:
    if sys.platform == "win32" and not sys.flags.utf8_mode:
        raise pytest.UsageError(
            "On Windows run the suite in UTF-8 mode: "
            "python -X utf8 -m pytest (or set PYTHONUTF8=1)"
        )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item],
) -> None:
    excluded = "posix_only" if sys.platform == "win32" else "windows_only"
    for item in items:
        for name in ("posix_only", "windows_only"):
            marker = item.get_closest_marker(name)
            if marker is not None and not marker.kwargs.get("reason"):
                raise pytest.UsageError(f"{item.nodeid}: @{name} requires reason=")
        marker = item.get_closest_marker(excluded)
        if marker is not None:
            item.add_marker(pytest.mark.skip(
                reason=f"{excluded}: {marker.kwargs['reason']}"))
```

`requirements-common.txt` 末尾追加：

```
pywin32==312 ; sys_platform == "win32"
```

按各锁文件头注释里的命令重新生成三个锁文件：

```bash
uv pip compile requirements.txt --universal --generate-hashes --python-version 3.10 --output-file requirements.lock
uv pip compile requirements-wrapper.txt --constraint requirements.lock --universal --generate-hashes --python-version 3.10 --output-file requirements-wrapper.lock
uv pip compile requirements-relay.txt --constraint requirements.lock --universal --generate-hashes --python-version 3.10 --output-file requirements-relay.lock
```

预期 diff：`requirements.lock` 和 `requirements-wrapper.lock` 中 pywin32 的 `# via` 多出 `-r requirements-common.txt`；`requirements-relay.lock` 新增 pywin32 条目（带 win32 标记）。其他包的版本都不应变化，如果变了，检查 uv 版本后重新生成。

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/Scripts/python.exe -X utf8 -m pytest tests/test_platform_harness.py -p no:xdist -o addopts="" -q -rs`
Expected: 3 passed、1 skipped，skip 原因显示 `posix_only: harness self-check…`。
再运行一次不带 `-X utf8` 的：`.venv/Scripts/python.exe -m pytest tests/test_platform_harness.py -q`
Expected: 报 UsageError，提示改用 UTF-8 模式。

- [ ] **Step 5: 提交**

```bash
git add cc_remote/platform/__init__.py cc_remote/__init__.py pytest.ini tests/conftest.py tests/test_platform_harness.py requirements-common.txt requirements*.lock
git commit -m "feat(platform): add platform package, test markers and pywin32 dependency"
```

---

### Task 2: `locks` — 与 flock 语义一致的建议锁

**Files:**
- Create: `cc_remote/platform/locks.py`
- Test: `tests/test_platform_locks.py`

**Interfaces:**
- Produces: `locks.LOCK_EX`、`locks.LOCK_UN`、`locks.LOCK_NB`、`locks.flock(fd: int, operation: int) -> None`。语义与 `fcntl.flock` 相同：冲突时抛 `BlockingIOError`；关闭 fd 即释放锁；对没有加锁的 fd 执行 LOCK_UN 不报错。不支持 LOCK_SH。同一个 fd 不能重复加 EX 锁（上游代码不会这样用）。

- [ ] **Step 1: 写失败测试** `tests/test_platform_locks.py`

```python
from __future__ import annotations

import os
import threading
import time

import pytest

from cc_remote.platform import locks


def _open(path) -> int:
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o600)


def test_nonblocking_contention_raises_blocking_io_error(tmp_path) -> None:
    a, b = _open(tmp_path / "l"), _open(tmp_path / "l")
    try:
        locks.flock(a, locks.LOCK_EX)
        with pytest.raises(BlockingIOError):
            locks.flock(b, locks.LOCK_EX | locks.LOCK_NB)
    finally:
        os.close(a)
        os.close(b)


def test_blocking_lock_waits_for_release(tmp_path) -> None:
    a, b = _open(tmp_path / "l"), _open(tmp_path / "l")
    try:
        locks.flock(a, locks.LOCK_EX)
        threading.Timer(0.3, locks.flock, (a, locks.LOCK_UN)).start()
        started = time.monotonic()
        locks.flock(b, locks.LOCK_EX)
        assert time.monotonic() - started >= 0.2
    finally:
        os.close(a)
        os.close(b)


def test_close_releases_lock(tmp_path) -> None:
    a = _open(tmp_path / "l")
    locks.flock(a, locks.LOCK_EX)
    os.close(a)
    b = _open(tmp_path / "l")
    try:
        locks.flock(b, locks.LOCK_EX | locks.LOCK_NB)
    finally:
        os.close(b)


def test_lock_does_not_block_reading_file_content(tmp_path) -> None:
    path = tmp_path / "data"
    path.write_bytes(b"payload")
    a, b = _open(path), _open(path)
    try:
        locks.flock(a, locks.LOCK_EX)
        assert os.read(b, 16) == b"payload"
    finally:
        os.close(a)
        os.close(b)


def test_unlock_without_lock_is_noop(tmp_path) -> None:
    a = _open(tmp_path / "l")
    try:
        locks.flock(a, locks.LOCK_UN)
    finally:
        os.close(a)
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/Scripts/python.exe -X utf8 -m pytest tests/test_platform_locks.py -p no:xdist -o addopts="" -q`
Expected: 失败，报 `ImportError: cannot import name 'locks'`。

- [ ] **Step 3: 实现** `cc_remote/platform/locks.py`

```python
"""Advisory whole-file locks with ``fcntl.flock`` semantics on every platform.

Windows byte-range locks are mandatory, so locking real file content would make
other handles' reads fail. The Windows backend therefore locks one byte far
beyond any real data, which keeps file content readable while the lock is held.
"""
from __future__ import annotations

import errno

from cc_remote.platform import IS_WINDOWS

if IS_WINDOWS:
    import msvcrt

    import pywintypes
    import win32con
    import win32file

    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    _ERROR_LOCK_VIOLATION = 33
    _ERROR_NOT_LOCKED = 158
    _OFFSET_LOW = 0xFFFFFFFE
    _OFFSET_HIGH = 0x7FFFFFFF

    def _overlapped() -> pywintypes.OVERLAPPED:
        overlapped = pywintypes.OVERLAPPED()
        overlapped.Offset = _OFFSET_LOW
        overlapped.OffsetHigh = _OFFSET_HIGH
        return overlapped

    def flock(fd: int, operation: int) -> None:
        handle = msvcrt.get_osfhandle(fd)
        base = operation & ~LOCK_NB
        if base == LOCK_UN:
            try:
                win32file.UnlockFileEx(handle, 1, 0, _overlapped())
            except pywintypes.error as exc:
                if exc.winerror != _ERROR_NOT_LOCKED:
                    raise OSError(None, exc.strerror, None, exc.winerror) from None
            return
        if base != LOCK_EX:
            raise ValueError(f"unsupported lock operation: {operation}")
        flags = win32con.LOCKFILE_EXCLUSIVE_LOCK
        if operation & LOCK_NB:
            flags |= win32con.LOCKFILE_FAIL_IMMEDIATELY
        try:
            win32file.LockFileEx(handle, flags, 1, 0, _overlapped())
        except pywintypes.error as exc:
            if exc.winerror == _ERROR_LOCK_VIOLATION:
                raise BlockingIOError(errno.EWOULDBLOCK, "file is locked") from None
            raise OSError(None, exc.strerror, None, exc.winerror) from None
else:
    import fcntl

    LOCK_EX = fcntl.LOCK_EX
    LOCK_NB = fcntl.LOCK_NB
    LOCK_UN = fcntl.LOCK_UN
    flock = fcntl.flock

__all__ = ["LOCK_EX", "LOCK_NB", "LOCK_UN", "flock"]
```

- [ ] **Step 4: 运行，确认通过**

Run: 同 Step 2。Expected: 5 passed。

- [ ] **Step 5: 提交**

```bash
git add cc_remote/platform/locks.py tests/test_platform_locks.py
git commit -m "feat(platform): add flock-compatible advisory locks"
```

---

### Task 3: `files` — share-delete 打开、可重试 replace 及 POSIX 文件原语

**Files:**
- Create: `cc_remote/platform/files.py`（`force_rmtree`、`open_with_share_delete`、`pread`、`pwrite` 移植自旧分支 `git show origin/windows-compat:cc_remote/wrapper/os_compat.py`，并修复其中打开方式（disposition）的映射错误）
- Test: `tests/test_platform_files.py`

**Interfaces:**
- Produces:
  - `files.open_with_share_delete(path, flags: int, mode: int = 0o600) -> int`
  - `files.replace(src, dst, *, attempts: int = 10) -> None`
  - `files.fsync_directory(path) -> None`
  - `files.force_rmtree(path) -> None`
  - `files.pread(fd, length, offset) -> bytes`
  - `files.pwrite(fd, data, offset) -> int`
  - `files.fchmod(fd, path, mode) -> None`

- [ ] **Step 1: 写失败测试** `tests/test_platform_files.py`

```python
from __future__ import annotations

import os
import stat
import threading

import pytest

from cc_remote.platform import files


def test_share_delete_descriptor_allows_unlink_and_replace(tmp_path) -> None:
    target = tmp_path / "marker"
    target.write_bytes(b"old")
    fd = files.open_with_share_delete(target, os.O_RDWR)
    try:
        replacement = tmp_path / "new"
        replacement.write_bytes(b"new")
        files.replace(replacement, target)
        assert target.read_bytes() == b"new"
        assert not replacement.exists()
        assert files.pread(fd, 16, 0) == b"old"  # open handle keeps the old inode
        target.unlink()
        assert not target.exists()
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    ("flags", "exists", "expected"),
    [
        (os.O_RDWR | os.O_CREAT, False, b""),
        (os.O_RDWR | os.O_CREAT, True, b"keep"),
        (os.O_RDWR | os.O_CREAT | os.O_TRUNC, True, b""),
        (os.O_RDWR | os.O_TRUNC, True, b""),
    ],
)
def test_share_delete_open_follows_posix_dispositions(tmp_path, flags, exists, expected) -> None:
    path = tmp_path / "f"
    if exists:
        path.write_bytes(b"keep")
    os.close(files.open_with_share_delete(path, flags))
    assert path.read_bytes() == expected


def test_share_delete_exclusive_create_rejects_existing(tmp_path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x")
    with pytest.raises(FileExistsError):
        files.open_with_share_delete(path, os.O_RDWR | os.O_CREAT | os.O_EXCL)


def test_share_delete_missing_file_raises_file_not_found(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        files.open_with_share_delete(tmp_path / "missing", os.O_RDONLY)


@pytest.mark.windows_only(reason="transient sharing violations only exist on Windows")
def test_replace_retries_while_destination_is_briefly_held(tmp_path) -> None:
    dst = tmp_path / "dst"
    dst.write_bytes(b"old")
    src = tmp_path / "src"
    src.write_bytes(b"new")
    holder = open(dst, "rb")  # builtin open: no FILE_SHARE_DELETE
    threading.Timer(0.2, holder.close).start()
    files.replace(src, dst)
    assert dst.read_bytes() == b"new"


def test_pread_pwrite_keep_file_position(tmp_path) -> None:
    fd = os.open(tmp_path / "f", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.write(fd, b"0123456789")
        files.pwrite(fd, b"AB", 2)
        assert files.pread(fd, 4, 1) == b"1AB4"
        assert os.lseek(fd, 0, os.SEEK_CUR) == 10
    finally:
        os.close(fd)


def test_force_rmtree_removes_readonly_files(tmp_path) -> None:
    tree = tmp_path / "tree" / "objects"
    tree.mkdir(parents=True)
    loose = tree / "pack"
    loose.write_bytes(b"x")
    os.chmod(loose, stat.S_IREAD)
    files.force_rmtree(tmp_path / "tree")
    assert not (tmp_path / "tree").exists()


def test_fsync_directory_accepts_existing_directory(tmp_path) -> None:
    files.fsync_directory(tmp_path)
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/Scripts/python.exe -X utf8 -m pytest tests/test_platform_files.py -p no:xdist -o addopts="" -q`
Expected: 失败，报 `ImportError: cannot import name 'files'`。

- [ ] **Step 3: 实现** `cc_remote/platform/files.py`

```python
"""File primitives with POSIX semantics on Windows.

Windows refuses to unlink or rename a file while any handle lacks
FILE_SHARE_DELETE, has no directory fsync, and has no pread/pwrite. These
helpers keep the POSIX contract callers were written against.
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import time
from os import PathLike

from cc_remote.platform import IS_WINDOWS

StrPath = str | PathLike[str]


def fchmod(fd: int, path: StrPath, mode: int) -> None:
    native = getattr(os, "fchmod", None)
    if native is not None and not IS_WINDOWS:
        native(fd, mode)
    else:
        os.chmod(path, mode)


def fsync_directory(path: StrPath) -> None:
    """Persist a directory entry on POSIX; Windows has no directory fsync."""
    if IS_WINDOWS:
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


_ERROR_INVALID_PARAMETER = 87
_ERROR_NOT_SUPPORTED = 50


def _posix_rename(src: str, dst: str) -> None:
    """Rename with POSIX semantics (Windows 10 1809+, NTFS).

    Plain MoveFileEx refuses to replace a target that any handle keeps open,
    even with FILE_SHARE_DELETE. FILE_RENAME_FLAG_POSIX_SEMANTICS replaces it
    and leaves existing handles reading the old content, like a POSIX inode.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    delete_access, share_all, open_existing, backup_semantics = 0x00010000, 0x7, 3, 0x02000000
    handle = kernel32.CreateFileW(
        src, delete_access, share_all, None, open_existing, backup_semantics, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        target = os.path.abspath(dst)

        class _RenameInfo(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("RootDirectory", wintypes.HANDLE),
                ("FileNameLength", wintypes.DWORD),
                ("FileName", ctypes.c_wchar * (len(target) + 1)),
            ]

        replace_if_exists, posix_semantics, file_rename_info_ex = 0x1, 0x2, 22
        info = _RenameInfo(
            replace_if_exists | posix_semantics, None, len(target) * 2, target)
        if not kernel32.SetFileInformationByHandle(
                handle, file_rename_info_ex, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


def replace(src: StrPath, dst: StrPath, *, attempts: int = 10) -> None:
    """``os.replace`` with POSIX semantics on Windows.

    Replaces targets held open with FILE_SHARE_DELETE, and rides out the
    short-lived sharing violations antivirus scanners and indexers cause.
    Falls back to MoveFileEx where POSIX renames are unsupported (FAT, old OS).
    """
    if not IS_WINDOWS:
        os.replace(src, dst)
        return
    for attempt in range(attempts):
        try:
            try:
                _posix_rename(os.fspath(src), os.fspath(dst))
            except OSError as exc:
                if exc.winerror not in (_ERROR_INVALID_PARAMETER, _ERROR_NOT_SUPPORTED):
                    raise
                os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(min(0.02 * (2 ** attempt), 0.5))


def _win32_long_path(path: str) -> str:
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def force_rmtree(path: StrPath) -> None:
    """Remove a tree, clearing Windows read-only bits and long-path limits."""
    if not IS_WINDOWS:
        shutil.rmtree(path)
        return

    def clear_readonly_and_retry(func, target, _error) -> None:
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except FileNotFoundError:
            pass

    long_path = _win32_long_path(os.fspath(path))
    for attempt in range(5):
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(long_path, onexc=clear_readonly_and_retry)
            else:
                shutil.rmtree(long_path, onerror=clear_readonly_and_retry)
            return
        except OSError:
            if not os.path.exists(long_path):
                return
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def open_with_share_delete(path: StrPath, flags: int, mode: int = 0o600) -> int:
    """``os.open`` whose descriptor never blocks unlink/replace of ``path``."""
    if not IS_WINDOWS:
        return os.open(path, flags, mode)

    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read, generic_write = 0x80000000, 0x40000000
    share_all = 0x1 | 0x2 | 0x4
    create_new, create_always, open_existing, open_always, truncate_existing = 1, 2, 3, 4, 5
    file_attribute_normal = 0x80

    if flags & os.O_RDWR:
        access, access_flags = generic_read | generic_write, os.O_RDWR
    elif flags & os.O_WRONLY:
        access, access_flags = generic_write, os.O_WRONLY
    else:
        access, access_flags = generic_read, os.O_RDONLY
    if flags & os.O_CREAT and flags & os.O_EXCL:
        disposition = create_new
    elif flags & os.O_CREAT and flags & os.O_TRUNC:
        disposition = create_always
    elif flags & os.O_CREAT:
        disposition = open_always
    elif flags & os.O_TRUNC:
        disposition = truncate_existing
    else:
        disposition = open_existing

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        os.fspath(path), access, share_all, None, disposition,
        file_attribute_normal, None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return msvcrt.open_osfhandle(
        handle, access_flags | os.O_BINARY | (flags & os.O_APPEND))


def pread(fd: int, length: int, offset: int) -> bytes:
    native = getattr(os, "pread", None)
    if native is not None:
        return native(fd, length, offset)
    saved = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        return os.read(fd, length)
    finally:
        os.lseek(fd, saved, os.SEEK_SET)


def pwrite(fd: int, data: bytes, offset: int) -> int:
    native = getattr(os, "pwrite", None)
    if native is not None:
        return native(fd, data, offset)
    saved = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        return os.write(fd, data)
    finally:
        os.lseek(fd, saved, os.SEEK_SET)
```

注：`ctypes.WinError(2)` 生成的是 `FileNotFoundError`，`WinError(80)` 生成的是 `FileExistsError`，正好对应测试里期望的异常类型。

- [ ] **Step 4: 运行，确认通过**

Run: 同 Step 2。Expected: Windows 上全部通过（10 passed）。

- [ ] **Step 5: 提交**

```bash
git add cc_remote/platform/files.py tests/test_platform_files.py
git commit -m "feat(platform): add share-delete open, retrying replace and POSIX file primitives"
```

---

### Task 4: `identity` 与 `perms` — 当前用户与私有权限

**Files:**
- Create: `cc_remote/platform/identity.py`、`cc_remote/platform/perms.py`
- Test: `tests/test_platform_perms.py`

**Interfaces:**
- Produces:
  - `identity.current_uid() -> int`：POSIX 返回 `os.getuid()`，Windows 返回 0（与 `os.stat().st_uid` 的占位值一致）
  - `identity.current_euid() -> int`
  - `identity.FOREIGN_UID: int = 0x7FFFFFFF`
  - `identity.current_sid() -> str | None`：Windows 上返回字符串形式的 SID，POSIX 上返回 None
  - `perms.make_private(path, *, directory: bool) -> None`
  - `perms.is_private(path) -> bool`

- [ ] **Step 1: 写失败测试** `tests/test_platform_perms.py`

```python
from __future__ import annotations

import os
import sys

import pytest

from cc_remote.platform import identity, perms


def test_current_uid_matches_stat_owner(tmp_path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"")
    assert os.stat(path).st_uid == identity.current_uid()
    assert identity.FOREIGN_UID != identity.current_uid()


def test_make_private_file_and_directory(tmp_path) -> None:
    directory = tmp_path / "d"
    directory.mkdir()
    perms.make_private(directory, directory=True)
    child = directory / "secret"
    child.write_bytes(b"x")
    perms.make_private(child, directory=False)
    assert perms.is_private(directory)
    assert perms.is_private(child)


@pytest.mark.posix_only(reason="mode bits are the POSIX privacy mechanism")
def test_group_readable_file_is_not_private(tmp_path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"")
    os.chmod(path, 0o640)
    assert not perms.is_private(path)


@pytest.mark.windows_only(reason="ACL entries are the Windows privacy mechanism")
def test_everyone_readable_file_is_not_private(tmp_path) -> None:
    import ntsecuritycon
    import win32security

    path = tmp_path / "f"
    path.write_bytes(b"")
    perms.make_private(path, directory=False)
    descriptor = win32security.GetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION)
    dacl = descriptor.GetSecurityDescriptorDacl()
    dacl.AddAccessAllowedAce(
        win32security.ACL_REVISION, ntsecuritycon.FILE_GENERIC_READ,
        win32security.CreateWellKnownSid(win32security.WinWorldSid))
    win32security.SetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION, None, None, dacl, None)
    assert not perms.is_private(path)


@pytest.mark.windows_only(reason="SIDs only exist on Windows")
def test_current_sid_is_a_user_sid() -> None:
    sid = identity.current_sid()
    assert sid is not None and sid.startswith("S-1-5-")
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/Scripts/python.exe -X utf8 -m pytest tests/test_platform_perms.py -p no:xdist -o addopts="" -q`
Expected: 失败，报 ImportError。

- [ ] **Step 3: 实现**

`cc_remote/platform/identity.py`：

```python
"""The current user as POSIX uid (or its Windows placeholder) and Windows SID."""
from __future__ import annotations

import functools
import os

from cc_remote.platform import IS_WINDOWS

# Windows os.stat() reports st_uid == 0 for every file, so 0 is the only uid
# value that compares equal to on-disk ownership. Other users' processes map
# to FOREIGN_UID, which never equals current_uid().
FOREIGN_UID = 0x7FFFFFFF


def current_uid() -> int:
    return 0 if IS_WINDOWS else os.getuid()


def current_euid() -> int:
    return 0 if IS_WINDOWS else os.geteuid()


@functools.cache
def current_sid() -> str | None:
    if not IS_WINDOWS:
        return None
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    return win32security.ConvertSidToStringSid(sid)
```

`cc_remote/platform/perms.py`：

```python
"""Owner-only files and directories: POSIX mode bits or a Windows DACL."""
from __future__ import annotations

import os
import stat
from os import PathLike

from cc_remote.platform import IS_WINDOWS
from cc_remote.platform.identity import current_sid, current_uid

StrPath = str | PathLike[str]


def make_private(path: StrPath, *, directory: bool) -> None:
    if not IS_WINDOWS:
        os.chmod(path, 0o700 if directory else 0o600)
        return
    import ntsecuritycon
    import win32security

    user = win32security.ConvertStringSidToSid(current_sid())
    system = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)
    inherit = (
        win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
        if directory else 0
    )
    dacl = win32security.ACL()
    for sid in (user, system):
        dacl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION, inherit, ntsecuritycon.FILE_ALL_ACCESS, sid)
    win32security.SetNamedSecurityInfo(
        os.fspath(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, dacl, None)


def is_private(path: StrPath) -> bool:
    """True when only the current user (plus SYSTEM/administrators) has access."""
    if not IS_WINDOWS:
        info = os.stat(path)
        return info.st_uid == current_uid() and not stat.S_IMODE(info.st_mode) & 0o077
    import win32security

    user = win32security.ConvertStringSidToSid(current_sid())
    admins = win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid)
    trusted = (
        user,
        win32security.CreateWellKnownSid(win32security.WinLocalSystemSid),
        admins,
        win32security.ConvertStringSidToSid("S-1-3-4"),  # OWNER RIGHTS
    )
    descriptor = win32security.GetNamedSecurityInfo(
        os.fspath(path), win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION
        | win32security.DACL_SECURITY_INFORMATION)
    owner = descriptor.GetSecurityDescriptorOwner()
    if not (owner == user or owner == admins):
        return False
    dacl = descriptor.GetSecurityDescriptorDacl()
    if dacl is None:  # a NULL DACL grants everyone full access
        return False
    for index in range(dacl.GetAceCount()):
        (ace_type, ace_flags), _mask, sid = dacl.GetAce(index)
        if ace_flags & win32security.INHERIT_ONLY_ACE:
            continue
        if ace_type == win32security.ACCESS_DENIED_ACE_TYPE:
            continue
        if ace_type != win32security.ACCESS_ALLOWED_ACE_TYPE:
            return False
        if not any(sid == candidate for candidate in trusted):
            return False
    return True
```

- [ ] **Step 4: 运行，确认通过**

Run: 同 Step 2。Expected: Windows 上 4 passed、1 skipped。

- [ ] **Step 5: 提交**

```bash
git add cc_remote/platform/identity.py cc_remote/platform/perms.py tests/test_platform_perms.py
git commit -m "feat(platform): add current-user identity and owner-only permission helpers"
```

---

### Task 5: `win32_process` 原语与 `process_scan` 的 Windows 分派

**Files:**
- Create: `cc_remote/platform/win32_process.py`
- Modify: `cc_remote/wrapper/process_scan.py`（`process_identity`、`process_command`、`process_owner_uid` 三个函数）
- Test: `tests/test_platform_win32_process.py`

**Interfaces:**
- Consumes: `identity.current_uid`、`identity.current_sid`、`identity.FOREIGN_UID`
- Produces（仅 Windows）：
  - `win32_process.creation_time(pid: int) -> int | None`：FILETIME，单位 100ns；进程已退出时返回 None
  - `win32_process.command_line(pid: int) -> str | None`
  - `win32_process.argv(pid: int) -> tuple[bytes, ...] | None`：每个参数用 `os.fsencode` 编码
  - `win32_process.parent_map() -> dict[int, int] | None`
  - `win32_process.owner_sid(pid: int) -> str | None`
  - `win32_process.all_pids() -> tuple[int, ...]`
  - 在 Windows 上，`process_scan.process_identity`、`process_command`、`process_owner_uid` 返回有意义的值，其中 `ProcessIdentity.start_ticks` 取 `creation_time`。

- [ ] **Step 1: 写失败测试** `tests/test_platform_win32_process.py`

```python
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from cc_remote.platform import identity
from cc_remote.wrapper import process_scan

pytestmark = pytest.mark.windows_only(reason="Win32 process primitives")

MARKER = "cc-remote-probe-é中"


@pytest.fixture
def child():
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", MARKER])
    time.sleep(0.5)
    yield process
    process.kill()
    process.wait()


def test_identity_and_argv_of_live_child(child) -> None:
    from cc_remote.platform import win32_process

    ident = process_scan.process_identity(child.pid)
    assert ident is not None
    assert ident.start_ticks == win32_process.creation_time(child.pid)
    args = process_scan.process_command(ident)
    assert args is not None and os.fsencode(MARKER) == args[-1]
    assert process_scan.process_identity(child.pid, parent_pid=os.getpid()) == ident


def test_stale_identity_is_rejected(child) -> None:
    ident = process_scan.process_identity(child.pid)
    stale = process_scan.ProcessIdentity(ident.pid, ident.start_ticks + 1)
    assert process_scan.process_command(stale) is None


def test_exited_process_has_no_identity(child) -> None:
    pid = child.pid
    child.kill()
    child.wait()
    assert process_scan.process_identity(pid) is None


def test_owner_of_own_child_is_current_uid(child) -> None:
    assert process_scan.process_owner_uid(child.pid) == identity.current_uid()


def test_parent_map_and_pid_listing(child) -> None:
    from cc_remote.platform import win32_process

    assert win32_process.parent_map()[child.pid] == os.getpid()
    assert child.pid in win32_process.all_pids()


def test_protected_system_process_is_unreadable_not_an_error() -> None:
    from cc_remote.platform import win32_process

    assert win32_process.argv(4) is None  # PID 4 is the System process
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/Scripts/python.exe -X utf8 -m pytest tests/test_platform_win32_process.py -p no:xdist -o addopts="" -q`
Expected: 失败。identity 相关的用例会断言失败（`process_identity` 在 Windows 上返回 None），用到 `win32_process` 的用例报 ImportError。

- [ ] **Step 3a: 实现** `cc_remote/platform/win32_process.py`

```python
"""Win32 process primitives used by cross-platform process scans (Windows only)."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_PROCESS_COMMAND_LINE_INFORMATION = 60
_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)

_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_ntdll.NtQueryInformationProcess.argtypes = [
    wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]
_ntdll.NtQueryInformationProcess.restype = wintypes.LONG
_shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
_shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)


class _UnicodeString(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", ctypes.c_void_p),
    ]


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]


def _open(pid: int) -> int | None:
    if pid <= 0:
        return None
    return _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid) or None


def creation_time(pid: int) -> int | None:
    """Kernel creation time in 100ns FILETIME ticks, or None once exited."""
    handle = _open(pid)
    if handle is None:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        code = wintypes.DWORD()
        if (not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                or code.value != _STILL_ACTIVE):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        _kernel32.CloseHandle(handle)


def command_line(pid: int) -> str | None:
    handle = _open(pid)
    if handle is None:
        return None
    try:
        needed = wintypes.ULONG(0)
        _ntdll.NtQueryInformationProcess(
            handle, _PROCESS_COMMAND_LINE_INFORMATION, None, 0, ctypes.byref(needed))
        if not needed.value:
            return None
        buffer = ctypes.create_string_buffer(needed.value)
        status = _ntdll.NtQueryInformationProcess(
            handle, _PROCESS_COMMAND_LINE_INFORMATION, buffer, needed,
            ctypes.byref(needed))
        if status != 0:
            return None
        text = _UnicodeString.from_buffer(buffer)
        if not text.Buffer:
            return None
        return ctypes.wstring_at(text.Buffer, text.Length // 2)
    finally:
        _kernel32.CloseHandle(handle)


def argv(pid: int) -> tuple[bytes, ...] | None:
    line = command_line(pid)
    if line is None:
        return None
    if not line.strip():
        return ()  # CommandLineToArgvW("") would return *our* executable
    count = ctypes.c_int(0)
    array = _shell32.CommandLineToArgvW(line, ctypes.byref(count))
    if not array:
        return None
    try:
        return tuple(os.fsencode(array[i]) for i in range(count.value))
    finally:
        _kernel32.LocalFree(ctypes.cast(array, ctypes.c_void_p))


def parent_map() -> dict[int, int] | None:
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, _INVALID_HANDLE_VALUE):
        return None
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
        if not _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        parents: dict[int, int] = {}
        while True:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                return parents
    finally:
        _kernel32.CloseHandle(snapshot)


def owner_sid(pid: int) -> str | None:
    import pywintypes
    import win32api
    import win32con
    import win32security

    try:
        handle = win32api.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except pywintypes.error:
        return None
    try:
        token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
        try:
            sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        return win32security.ConvertSidToStringSid(sid)
    except pywintypes.error:
        return None
    finally:
        handle.Close()


def all_pids() -> tuple[int, ...]:
    import win32process

    return tuple(int(pid) for pid in win32process.EnumProcesses())
```

- [ ] **Step 3b: 在 `process_scan.py` 中加入 Windows 分派**

文件顶部的 import 区追加：

```python
from cc_remote.platform import IS_WINDOWS
from cc_remote.platform.identity import FOREIGN_UID, current_sid, current_uid
```

在 `process_identity` 函数体开头（`stat = _process_stat(...)` 之前）插入：

```python
    if IS_WINDOWS and proc_root == "/proc":
        from cc_remote.platform import win32_process

        ticks = win32_process.creation_time(pid)
        if ticks is None:
            return None
        if parent_pid is not None:
            parents = win32_process.parent_map()
            if parents is None or parents.get(pid) != parent_pid:
                return None
        return ProcessIdentity(pid, ticks)
```

在 `process_command` 函数体开头（`proc_dir = …` 之前）插入：

```python
    if IS_WINDOWS and proc_root == "/proc":
        from cc_remote.platform import win32_process

        if win32_process.creation_time(identity.pid) != identity.start_ticks:
            return None
        args = win32_process.argv(identity.pid)
        if win32_process.creation_time(identity.pid) != identity.start_ticks:
            return None
        return args
```

在 `process_owner_uid` 函数体开头插入：

```python
    if IS_WINDOWS and proc_root == "/proc":
        from cc_remote.platform import win32_process

        sid = win32_process.owner_sid(pid)
        if sid is None:
            return None
        return current_uid() if sid == current_sid() else FOREIGN_UID
```

- [ ] **Step 4: 运行，确认通过**

Run: 同 Step 2，再加上已有的 Linux 测试：`.venv/Scripts/python.exe -X utf8 -m pytest tests/test_platform_win32_process.py tests/test_process_scan.py -p no:xdist -o addopts="" -q -rs`（如果 `tests/test_process_scan.py` 不存在，就只跑前者）。
Expected: win32 的 6 个用例全部通过。如果已有用例里有依赖 `/proc` 的，把失败用例记下来留给 Task 10 的基线，**本任务不要去 skip 它们**。

- [ ] **Step 5: 提交**

```bash
git add cc_remote/platform/win32_process.py cc_remote/wrapper/process_scan.py tests/test_platform_win32_process.py
git commit -m "feat(platform): add Win32 process identity, argv and owner scanning"
```

---

### Task 6: `procs` — 进程组启动与整树终止

**Files:**
- Create: `cc_remote/platform/procs.py`
- Test: `tests/test_platform_procs.py`

**Interfaces:**
- Consumes: `process_scan.process_identity`（测试中使用）
- Produces:
  - `procs.SIGTERM: int`、`procs.SIGKILL: int`：Windows 上 `SIGKILL` 是哨兵值 9，`SIGTERM` 取 `signal.SIGTERM`
  - `procs.popen_group(args, **kwargs) -> subprocess.Popen`：POSIX 上等同于 `Popen(..., start_new_session=True)`；Windows 上以挂起方式启动，加入新的 Job 后再恢复运行。kwargs 里不能带 `start_new_session`
  - `async procs.create_subprocess_exec_group(*args, **kwargs) -> asyncio.subprocess.Process`：语义同上
  - `procs.killpg(pid: int, sig: int) -> None`：POSIX 上就是 `os.killpg`；Windows 上不区分信号，直接用 `TerminateJobObject` 终止整个 Job。如果该 pid 不是由上面两个函数启动的，退回用 `taskkill /T /F`；进程已不存在时抛 `ProcessLookupError`

- [ ] **Step 1: 写失败测试** `tests/test_platform_procs.py`

```python
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cc_remote.platform import procs
from cc_remote.wrapper.process_scan import process_identity

SPAWN_GRANDCHILD = (
    "import subprocess, sys, time, pathlib;"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
    "pathlib.Path(sys.argv[1]).write_text(str(g.pid));"
    "time.sleep(60)"
)


def _wait_for_pid(path: Path) -> int:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError("grandchild pid was never written")


def _wait_gone(pid: int) -> bool:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process_identity(pid) is None:
            return True
        time.sleep(0.05)
    return False


def test_killpg_terminates_the_whole_tree(tmp_path) -> None:
    pid_file = tmp_path / "grandchild"
    child = procs.popen_group([sys.executable, "-c", SPAWN_GRANDCHILD, str(pid_file)])
    grandchild = _wait_for_pid(pid_file)
    procs.killpg(child.pid, procs.SIGKILL)
    child.wait(timeout=10)
    assert _wait_gone(grandchild)


def test_async_group_spawn_runs_and_terminates(tmp_path) -> None:
    async def scenario() -> int:
        pid_file = tmp_path / "grandchild"
        child = await procs.create_subprocess_exec_group(
            sys.executable, "-c", SPAWN_GRANDCHILD, str(pid_file),
            stdout=asyncio.subprocess.DEVNULL)
        grandchild = await asyncio.to_thread(_wait_for_pid, pid_file)
        procs.killpg(child.pid, procs.SIGTERM)
        await asyncio.wait_for(child.wait(), 10)
        return grandchild

    assert _wait_gone(asyncio.run(scenario()))


def test_group_child_exits_normally_and_pipes_work() -> None:
    child = procs.popen_group(
        [sys.executable, "-c", "print('ok')"], stdout=subprocess.PIPE)
    out, _ = child.communicate(timeout=15)
    assert child.returncode == 0 and out.strip() == b"ok"


def test_killpg_of_unknown_pid_raises_process_lookup_error() -> None:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    with pytest.raises(ProcessLookupError):
        procs.killpg(child.pid, procs.SIGKILL)
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/Scripts/python.exe -X utf8 -m pytest tests/test_platform_procs.py -p no:xdist -o addopts="" -q`
Expected: 失败，报 ImportError。

- [ ] **Step 3: 实现** `cc_remote/platform/procs.py`

```python
"""Process groups that can be terminated as a whole tree on every platform.

POSIX uses sessions (``start_new_session``) and ``killpg``. Windows has no
process groups for signals, so each child starts suspended, joins a fresh Job
Object, and only then resumes; terminating the Job kills every descendant,
including ones spawned in the first microseconds (the venv python.exe
launcher re-spawns the real interpreter immediately). Jobs deliberately do
not use KILL_ON_JOB_CLOSE: like a POSIX session, the tree may outlive us.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import threading
from typing import Any

from cc_remote.platform import IS_WINDOWS

SIGTERM = int(signal.SIGTERM)
SIGKILL = int(getattr(signal, "SIGKILL", 9))

if IS_WINDOWS:
    import ctypes

    import pywintypes
    import win32api
    import win32con
    import win32job

    _CREATE_SUSPENDED = 0x00000004
    _CREATE_NEW_PROCESS_GROUP = 0x00000200
    _PROCESS_ACCESS = (
        win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE
        | 0x0800  # PROCESS_SUSPEND_RESUME
        | 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
    )
    _ntdll = ctypes.WinDLL("ntdll")
    _ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
    _ntdll.NtResumeProcess.restype = ctypes.c_long

    _jobs: dict[int, Any] = {}
    _jobs_lock = threading.Lock()

    def _spawn_flags(kwargs: dict[str, Any]) -> dict[str, Any]:
        if "start_new_session" in kwargs:
            raise TypeError("popen_group always starts a new group")
        flags = kwargs.pop("creationflags", 0)
        return {**kwargs, "creationflags": flags | _CREATE_SUSPENDED | _CREATE_NEW_PROCESS_GROUP}

    def _prune_locked() -> None:
        for pid, job in list(_jobs.items()):
            accounting = win32job.QueryInformationJobObject(
                job, win32job.JobObjectBasicAccountingInformation)
            if accounting["ActiveProcesses"] == 0:
                job.Close()
                del _jobs[pid]

    def _adopt(pid: int) -> None:
        """Place the suspended ``pid`` in a fresh Job, then resume it."""
        process = win32api.OpenProcess(_PROCESS_ACCESS, False, pid)
        try:
            job = win32job.CreateJobObject(None, "")
            try:
                win32job.AssignProcessToJobObject(job, process)
            except pywintypes.error:
                job.Close()
                win32api.TerminateProcess(process, 1)
                raise
            with _jobs_lock:
                _prune_locked()
                _jobs[pid] = job
            if _ntdll.NtResumeProcess(int(process)) != 0:
                win32job.TerminateJobObject(job, 1)
                raise OSError("cannot resume the suspended child process")
        finally:
            process.Close()

    def popen_group(args: Any, **kwargs: Any) -> subprocess.Popen:
        child = subprocess.Popen(args, **_spawn_flags(kwargs))
        try:
            _adopt(child.pid)
        except BaseException:
            child.kill()
            raise
        return child

    async def create_subprocess_exec_group(
        program: Any, *args: Any, **kwargs: Any,
    ) -> asyncio.subprocess.Process:
        child = await asyncio.create_subprocess_exec(
            program, *args, **_spawn_flags(kwargs))
        try:
            _adopt(child.pid)
        except BaseException:
            child.kill()
            raise
        return child

    def killpg(pid: int, sig: int) -> None:
        del sig  # Windows has no deliverable graceful signal for console-less trees
        with _jobs_lock:
            job = _jobs.pop(pid, None)
        if job is not None:
            try:
                win32job.TerminateJobObject(job, 1)
            finally:
                job.Close()
            return
        result = subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True, check=False)
        if result.returncode == 128:
            raise ProcessLookupError(pid)
        if result.returncode != 0:
            raise OSError(f"taskkill failed for pid {pid}: {result.returncode}")
else:
    def popen_group(args: Any, **kwargs: Any) -> subprocess.Popen:
        return subprocess.Popen(args, start_new_session=True, **kwargs)

    async def create_subprocess_exec_group(
        program: Any, *args: Any, **kwargs: Any,
    ) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            program, *args, start_new_session=True, **kwargs)

    def killpg(pid: int, sig: int) -> None:
        os.killpg(pid, sig)

__all__ = ["SIGKILL", "SIGTERM", "create_subprocess_exec_group", "killpg", "popen_group"]
```

注：`taskkill` 找不到进程时返回 128，这一点已在本机实测确认。

- [ ] **Step 4: 运行，确认通过**

Run: 同 Step 2。Expected: 4 passed。另外用 `tasklist | findstr python` 手动确认没有残留进程。

- [ ] **Step 5: 提交**

```bash
git add cc_remote/platform/procs.py tests/test_platform_procs.py
git commit -m "feat(platform): add job-backed process groups with whole-tree termination"
```

---

### Task 7: `executables`、`paths`、`signals`

**Files:**
- Create: `cc_remote/platform/executables.py`、`cc_remote/platform/paths.py`、`cc_remote/platform/signals.py`
- Test: `tests/test_platform_executables.py`

**Interfaces:**
- Produces:
  - `executables.find(name: str, preferred: Iterable[Path] = ()) -> str | None`：先依次检查 `preferred`（Windows 上会补试加 `.exe` 的路径），找不到再用 `shutil.which(name)`
  - `executables.command_for(path: str) -> list[str]`：`.cmd`/`.bat` 形式的 npm shim 解析成真实的 exe，或者 `[node, script.js]`；解析失败时退回 `[COMSPEC, "/d", "/c", path]` 并记录警告；其他路径原样返回 `[path]`
  - `paths.venv_python(root: Path) -> Path`、`paths.venv_pythonw(root: Path) -> Path`
  - `signals.install_shutdown_handler(loop: asyncio.AbstractEventLoop, callback: Callable[[], None]) -> None`

- [ ] **Step 1: 写失败测试** `tests/test_platform_executables.py`

```python
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from cc_remote.platform import executables, paths, signals

CLAUDE_SHIM = (
    "@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\n"
    "SETLOCAL\r\nCALL :find_dp0\r\n"
    '"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe"   %*\r\n'
)
CODEX_SHIM = (
    "@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\n"
    "SETLOCAL\r\nCALL :find_dp0\r\n\r\nIF EXIST \"%dp0%\\node.exe\" (\r\n"
    "  SET \"_prog=%dp0%\\node.exe\"\r\n) ELSE (\r\n  SET \"_prog=node\"\r\n"
    "  SET PATHEXT=%PATHEXT:;.JS;=;%\r\n)\r\n\r\n"
    "endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & \"%_prog%\"  "
    "\"%dp0%\\node_modules\\@openai\\codex\\bin\\codex.js\" %*\r\n"
)


def test_exe_shim_resolves_to_the_real_binary(tmp_path) -> None:
    shim = tmp_path / "claude.cmd"
    shim.write_text(CLAUDE_SHIM)
    assert executables.command_for(str(shim)) == [
        str(tmp_path / "node_modules/@anthropic-ai/claude-code/bin/claude.exe")]


def test_node_shim_prefers_bundled_node(tmp_path) -> None:
    shim = tmp_path / "codex.cmd"
    shim.write_text(CODEX_SHIM)
    (tmp_path / "node.exe").write_bytes(b"")
    assert executables.command_for(str(shim)) == [
        str(tmp_path / "node.exe"),
        str(tmp_path / "node_modules/@openai/codex/bin/codex.js"),
    ]


def test_unparseable_shim_falls_back_to_cmd(tmp_path) -> None:
    shim = tmp_path / "odd.cmd"
    shim.write_text("@echo off\r\nsomething else\r\n")
    command = executables.command_for(str(shim))
    assert command[-1] == str(shim) and "/c" in command


def test_plain_executable_is_returned_unchanged(tmp_path) -> None:
    assert executables.command_for(str(tmp_path / "tool")) == [str(tmp_path / "tool")]


@pytest.mark.windows_only(reason="Windows appends .exe to extensionless candidates")
def test_find_tries_exe_suffix_for_preferred_paths(tmp_path) -> None:
    (tmp_path / "claude.exe").write_bytes(b"")
    assert executables.find("claude", [tmp_path / "claude"]) == str(tmp_path / "claude.exe")


def test_venv_python_layout(tmp_path) -> None:
    expected = ("Scripts", "python.exe") if sys.platform == "win32" else ("bin", "python")
    assert paths.venv_python(tmp_path).parts[-2:] == expected


def test_shutdown_handler_runs_callback_on_sigint() -> None:
    import signal as stdlib_signal

    saved = {s: stdlib_signal.getsignal(s) for s in (stdlib_signal.SIGINT, stdlib_signal.SIGTERM)}

    async def scenario() -> bool:
        loop = asyncio.get_running_loop()
        fired = asyncio.Event()
        signals.install_shutdown_handler(loop, fired.set)
        try:
            stdlib_signal.raise_signal(stdlib_signal.SIGINT)
            await asyncio.wait_for(fired.wait(), 5)
            return fired.is_set()
        finally:
            if sys.platform != "win32":
                for signum in saved:
                    loop.remove_signal_handler(signum)

    try:
        assert asyncio.run(scenario())
    finally:
        for signum, handler in saved.items():
            stdlib_signal.signal(signum, handler)
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/Scripts/python.exe -X utf8 -m pytest tests/test_platform_executables.py -p no:xdist -o addopts="" -q`
Expected: 失败，报 ImportError。

- [ ] **Step 3: 实现**

`cc_remote/platform/executables.py`：

```python
"""Locate engine executables and unwrap npm ``.cmd`` shims on Windows.

Spawning a ``.cmd`` goes through cmd.exe, which breaks argument quoting and
puts an extra process between us and the engine. npm shims have two stable
shapes: a direct ``"%dp0%\\...\\x.exe" %*`` call and a ``"%_prog%" "%dp0%\\...\\x.js"``
node call. Both resolve to a direct argv; anything else falls back to cmd.exe.
"""
from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterable
from pathlib import Path

from cc_remote.log import logger
from cc_remote.platform import IS_WINDOWS

log = logger("cc_remote.platform.executables")

_EXE_CALL = re.compile(r'^"%dp0%\\(?P<target>[^"]+\.exe)"\s+%\*\s*$', re.MULTILINE)
_NODE_CALL = re.compile(r'"%_prog%"\s+"%dp0%\\(?P<target>[^"]+\.js)"\s+%\*')


def find(name: str, preferred: Iterable[Path] = ()) -> str | None:
    for candidate in preferred:
        options = [candidate]
        if IS_WINDOWS and not candidate.suffix:
            options.insert(0, candidate.with_suffix(".exe"))
        for option in options:
            if option.is_file():
                return str(option)
    return shutil.which(name)


def command_for(path: str) -> list[str]:
    if Path(path).suffix.lower() not in {".cmd", ".bat"}:
        return [path]
    shim = Path(path)
    base = shim.parent
    try:
        text = shim.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if match := _EXE_CALL.search(text):
        return [str(base / match["target"])]
    if match := _NODE_CALL.search(text):
        bundled = base / "node.exe"
        node = str(bundled) if bundled.is_file() else (shutil.which("node") or "node")
        return [node, str(base / match["target"])]
    log.warning("unrecognized command shim; running through cmd.exe", path=path)
    return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", path]
```

`cc_remote/platform/paths.py`：

```python
"""Virtual-environment interpreter locations per platform."""
from __future__ import annotations

from pathlib import Path

from cc_remote.platform import IS_WINDOWS


def venv_python(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def venv_pythonw(root: Path) -> Path:
    """Console-less interpreter for background services (same as python on POSIX)."""
    return root / ".venv" / ("Scripts/pythonw.exe" if IS_WINDOWS else "bin/python")
```

注意：`venv_python` 在 Windows 上返回的 `Path` 内部用 `\` 分隔，测试用 `.parts` 比较，不受分隔符影响。

`cc_remote/platform/signals.py`：

```python
"""Graceful-shutdown hooks: SIGTERM/SIGINT on POSIX, Ctrl+C/Break/close on Windows."""
from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable

from cc_remote.platform import IS_WINDOWS


def install_shutdown_handler(
    loop: asyncio.AbstractEventLoop, callback: Callable[[], None],
) -> None:
    if not IS_WINDOWS:
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, callback)
        return
    import win32api

    def on_signal(_signum: int, _frame: object) -> None:
        loop.call_soon_threadsafe(callback)

    for signum in (signal.SIGINT, signal.SIGBREAK):
        signal.signal(signum, on_signal)

    def on_console_event(event: int) -> bool:
        if event in (2, 5, 6):  # CTRL_CLOSE, CTRL_LOGOFF, CTRL_SHUTDOWN
            loop.call_soon_threadsafe(callback)
            return True
        return False  # let SIGINT/SIGBREAK handlers see Ctrl+C / Ctrl+Break

    try:
        win32api.SetConsoleCtrlHandler(on_console_event, True)
    except Exception:  # pythonw: no console attached, nothing to hook
        pass
```

- [ ] **Step 4: 运行，确认通过**

Run: 同 Step 2。Expected: Windows 上 7 passed。

- [ ] **Step 5: 提交**

```bash
git add cc_remote/platform/executables.py cc_remote/platform/paths.py cc_remote/platform/signals.py tests/test_platform_executables.py
git commit -m "feat(platform): add executable shim resolution, venv paths and shutdown hooks"
```

---

### Task 8: 修复 import 阶段的不可移植依赖（fcntl、resource、pwd）

**Files:**
- Modify（`fcntl.flock` → `locks.flock`）：`cc_remote/async_tasks.py`、`cc_remote/claude_service/__main__.py`、`cc_remote/codex_daemon_restart.py`、`cc_remote/codex_desktop.py`、`cc_remote/timed_tasks.py`、`cc_remote/tui_tab_store.py`、`cc_remote/viewer.py`、`cc_remote/viewer_pages.py`、`cc_remote/wrapper/codex_checkpoints.py`、`cc_remote/wrapper/engine_capabilities.py`、`cc_remote/wrapper/preview_capabilities.py`、`cc_remote/wrapper/rollback_commands.py`、`cc_remote/wrapper/session_messages.py`
- Modify：`cc_remote/wrapper/codex_daemon.py`（`resource` 改为可选导入）、`cc_remote/wrapper/rlimit_exec.py`、`cc_remote/update_relay.py`（`pwd` 改为延迟导入）
- Modify：`tests/test_markdown_preview.py:1209,1216`（monkeypatch 的目标从 `preview_capabilities.fcntl` 改为 `preview_capabilities.locks`）
- **不改** `cc_remote/claude_broker/cli.py`、`cc_remote/claude_broker/session.py`（`fcntl.ioctl`/`termios` 归子项目 3）

**Interfaces:**
- Consumes: `locks.flock/LOCK_EX/LOCK_NB/LOCK_UN`
- Produces: 在 Windows 上收集测试时，除 claude_broker 相关模块外不再有 import 错误。

- [ ] **Step 1: 写失败测试**（守护测试）追加到 `tests/test_platform_harness.py`

```python
import importlib
import pkgutil

import cc_remote

# Modules owned by later sub-projects that still import POSIX-only APIs.
_PENDING_PORTS = {
    "cc_remote.claude_broker.cli",      # termios/tty: sub-project 3
    "cc_remote.claude_broker.session",  # pty/termios: sub-project 3
}


def test_every_module_imports_on_this_platform() -> None:
    failures = {}
    for module in pkgutil.walk_packages(cc_remote.__path__, "cc_remote."):
        name = module.name
        if name in _PENDING_PORTS or name.endswith("__main__"):
            continue
        try:
            importlib.import_module(name)
        except ImportError as exc:
            if "claude_broker" in name and "termios" in str(exc):
                continue  # package __init__ pulls in the pending broker modules
            failures[name] = repr(exc)
    assert failures == {}
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/Scripts/python.exe -X utf8 -m pytest tests/test_platform_harness.py::test_every_module_imports_on_this_platform -p no:xdist -o addopts="" -q`
Expected: 失败，列出缺少 `fcntl`、`resource`、`pwd` 的模块。

- [ ] **Step 3a: 机械替换 fcntl.flock**（只处理上面列出的 13 个文件）

```bash
for f in cc_remote/async_tasks.py cc_remote/claude_service/__main__.py cc_remote/codex_daemon_restart.py \
         cc_remote/codex_desktop.py cc_remote/timed_tasks.py cc_remote/tui_tab_store.py cc_remote/viewer.py \
         cc_remote/viewer_pages.py cc_remote/wrapper/codex_checkpoints.py cc_remote/wrapper/engine_capabilities.py \
         cc_remote/wrapper/preview_capabilities.py cc_remote/wrapper/rollback_commands.py cc_remote/wrapper/session_messages.py; do
  sed -i -e 's/^import fcntl$/from cc_remote.platform import locks/' \
         -e 's/\bfcntl\.flock(/locks.flock(/g' \
         -e 's/\bfcntl\.LOCK_\(EX\|NB\|UN\)\b/locks.LOCK_\1/g' "$f"
done
grep -nE '\bfcntl\b' cc_remote --include=*.py -r | grep -v claude_broker
```

Expected: 最后的 grep 没有输出。如果某个文件原本是 `import fcntl, x` 这种合并写法，或者用了别的写法，按同样的意图手工修改。逐个文件看 diff，确认 import 位置符合该文件原有的排序风格。

`tests/test_markdown_preview.py` 第 1209、1216 行：`preview_capabilities.fcntl.LOCK_NB` 改为 `preview_capabilities.locks.LOCK_NB`，`monkeypatch.setattr(preview_capabilities.fcntl, "flock", busy_lock)` 改为 `monkeypatch.setattr(preview_capabilities.locks, "flock", busy_lock)`。

- [ ] **Step 3b: `codex_daemon.py` 中的 `resource`**

把 `import resource` 换成：

```python
try:
    import resource
except ImportError:  # Windows has no RLIMIT_NOFILE; daemons are not rlimit-raised there
    resource = None  # type: ignore[assignment]
```

在 `prlimit(pid, resource.RLIMIT_NOFILE)` 所在函数的 `try:` 之前（第 517 行附近）插入：

```python
    if resource is None:
        return False
```

（这个函数目前只会在 Linux 上走到；Windows 上 codex daemon 怎么处理，由子项目 2 决定。）

- [ ] **Step 3c: `rlimit_exec.py`**

把 `import resource` 换成：

```python
try:
    import resource
except ImportError:  # Windows
    resource = None  # type: ignore[assignment]
```

在 `main()` 开头插入：

```python
    if resource is None:
        # os.exec* on Windows spawns a new PID and exits this one, which would
        # break the caller's process tracking. Callers must not wrap on Windows.
        raise SystemExit("rlimit_exec is POSIX-only")
```

- [ ] **Step 3d: `update_relay.py`**

删掉第 9 行的 `import pwd`，在第 148 行 `pwd.getpwnam(user)` 所在 `try:` 的上一行插入 `import pwd  # relay hosts are POSIX-only`。

- [ ] **Step 4: 运行，确认通过**

Run: 同 Step 2，再跑受影响的已有测试：
`.venv/Scripts/python.exe -X utf8 -m pytest tests/test_platform_harness.py tests/test_markdown_preview.py tests/test_codex_checkpoints.py -p no:xdist -o addopts="" -q`
Expected: 守护测试通过。另外两个文件只要求能被收集；里面的业务用例失败与否，记入 Task 10 的基线。

- [ ] **Step 5: 提交**

```bash
git add -u cc_remote tests/test_markdown_preview.py tests/test_platform_harness.py
git commit -m "fix(platform): route flock through platform locks and guard POSIX-only imports"
```

---

### Task 9: uid 机械迁移，并移植旧分支的 preview_capabilities 修复

**Files:**
- Modify：`cc_remote/` 下所有包含 `os.getuid()` / `os.geteuid()` 的文件（46 个文件减去 `deploy/`，以执行时的 grep 结果为准）。**不改 `deploy/`**，它的测试会替换整个 `os` 模块，留给子项目 5。
- Modify：`cc_remote/wrapper/preview_capabilities.py`（对照 `git show 47e7674 -- cc_remote/wrapper/preview_capabilities.py` 移植：share-delete 打开、`pread`、`fchmod`、`fsync_directory`、`st_nlink`/模式位检查改走平台判断）
- Test：`tests/test_markdown_preview.py`（从旧分支 47e7674 移植其中的 share-delete 回归用例）

**Interfaces:**
- Consumes: `identity.current_uid/current_euid`、`files.open_with_share_delete/pread/fchmod/fsync_directory`、`perms.is_private`

- [ ] **Step 1: 写失败测试**：把旧分支的回归用例移植到 `tests/test_markdown_preview.py`

```bash
git show 47e7674 -- tests/test_markdown_preview.py
```

把 diff 里新增的用例（share-delete 场景：失效标记文件的 fd 保持打开时，对其执行 unlink/replace）原样加到现有测试文件末尾。fixture 名称如果与上游当前版本不一致，按当前文件里的 fixture 调整。

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/Scripts/python.exe -X utf8 -m pytest tests/test_markdown_preview.py -p no:xdist -o addopts="" -q -k "share_delete or invalidation"`
Expected: Windows 上失败，报 `PermissionError [WinError 32]` 或者 `os.geteuid` 的 AttributeError。

- [ ] **Step 3a: uid 机械替换**

```bash
files=$(grep -rlE 'os\.gete?uid\(\)' cc_remote --include=*.py)
for f in $files; do
  sed -i -e 's/\bos\.geteuid()/current_euid()/g' -e 's/\bos\.getuid()/current_uid()/g' "$f"
done
grep -rlE 'current_e?uid\(\)' cc_remote --include=*.py | grep -v '^cc_remote/platform/'
```

对输出的每个文件，在 import 区按需加 `from cc_remote.platform.identity import current_euid, current_uid`（只导入实际用到的名字），并保持原有的 import 排序风格。然后跑一遍：

`.venv/Scripts/python.exe -X utf8 -m pyflakes cc_remote 2>/dev/null || .venv/Scripts/python.exe -X utf8 -m compileall -q cc_remote`

确认没有未定义名称、语法也没有问题（pyflakes 不在依赖里时，就用 `uv pip install pyflakes` 临时装一下，不要写进 requirements）。

- [ ] **Step 3b: 移植 preview_capabilities 修复**

参照 `git show 47e7674 -- cc_remote/wrapper/preview_capabilities.py` 的 diff，按下面的对应关系改：

| 旧分支写法 | 改成 |
|---|---|
| `from cc_remote.wrapper.file_lock_compat import …` | 已由 Task 8 改为 `locks` |
| `from cc_remote.wrapper.os_compat import current_uid, fchmod, fsync_directory, open_with_share_delete, pread` | `from cc_remote.platform.files import fchmod, fsync_directory, open_with_share_delete, pread`（`current_uid` 已由 Step 3a 处理） |
| `os.fchmod(descriptor, 0o600)` | `fchmod(descriptor, self._lock_path, 0o600)` |
| 手写的目录 fsync 代码块 | `fsync_directory(self._path.parent)` |
| `info.st_nlink != 1` | `(not IS_WINDOWS and info.st_nlink != 1)` |
| `stat.S_IMODE(path_info.st_mode) & 0o077` | `not perms.is_private(self._invalidation_path)`（注意：这里改用 ACL 判断，不要照搬旧分支在 Windows 上直接跳过检查的做法） |
| `os.pread(...)` | `pread(...)` |
| 打开失效标记文件时的 `os.open(self._invalidation_path, flags)` | `open_with_share_delete(self._invalidation_path, flags)` |
| 发布临时文件时的 `os.replace(temporary, …)` | `replace(temporary, …)`（来自 `cc_remote.platform.files`，带 POSIX 语义，目标被其他句柄以 share-delete 打开着时也能替换成功） |

不要照搬旧分支给临时文件加 `O_TEMPORARY` 的做法：有了 POSIX 语义的 `replace` 之后就不需要了，
而且 `O_TEMPORARY` 会在 fd 关闭时删除文件，与上游"先 link 再发布"的流程相冲突。

旧分支 e136a75 中其余文件（`codex_checkpoints`、`git_diff`、`rollback_commands`、`session_pins`、
`claude_forks`、`codex_forks`、`codex_turn_leases`、`codex_provider_repair`、`engine_capabilities`、
`machine`、`config`、`device` 等）的改动不在本任务范围内。这些文件引发的失败会进入基线，由子项目 2
参照旧分支的 diff 移植。

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/Scripts/python.exe -X utf8 -m pytest tests/test_markdown_preview.py tests/test_platform_harness.py -p no:xdist -o addopts="" -q`
Expected: 新增的 share-delete 用例和守护测试都通过。

- [ ] **Step 5: 提交**

```bash
git add -u cc_remote tests/test_markdown_preview.py
git commit -m "fix(platform): use portable uid helpers and port preview capability fixes"
```

---

### Task 10: Windows 测试基线、推送与 fork 内 PR

**Files:**
- Create: `docs/superpowers/windows-baseline.md`

**Interfaces:**
- Produces: 基线文档。子项目 2～5 的实施计划都以它为输入，每条失败都标明归属的子项目。

- [ ] **Step 1: 在 Windows 上跑全量 pytest**

```bash
.venv/Scripts/python.exe -X utf8 -m pytest -o addopts="" -n 4 --dist worksteal -q -rfEs \
  -p no:cacheprovider --junitxml=/tmp/win-baseline.xml 2>&1 | tail -40
```

Expected: 不再有收集错误（claude_broker 的测试模块除外）。记录 passed、failed、error、skipped 的数量。

- [ ] **Step 2: 跑 web 测试**

```bash
cd web && npm ci && npx vitest run 2>&1 | tail -20; cd ..
```

记录失败用例。playwright 留到冒烟阶段再跑，这里不跑。

- [ ] **Step 3: 写 `docs/superpowers/windows-baseline.md`**

从 junit xml 中按测试文件聚合失败用例，并按以下规则归属：

| 归属 | 规则 |
|---|---|
| 子项目 2（Wrapper 核心） | `test_codex_*`、`test_claude_external*`、`test_machine*`、`test_wrapper_*`、`test_history*`、涉及 `process_scan`/`killpg`/codex daemon 的用例 |
| 子项目 3（IPC/service/broker） | `test_claude_broker*`、`test_claude_service*`、`test_claude_session_service*` |
| 子项目 4（TUI/viewer/messages） | `test_tui*`、`test_viewer*`、`test_session_messages*`、`test_timed_*`、`test_async_tasks*` |
| 子项目 5（安装部署） | `test_release_*`、`test_linux_service*`、`test_install*`、`test_update*`、`test_cleanup*`、`deploy` 相关 |
| 子项目 1 遗留 | 失败原因在 `cc_remote/platform/` 本身的，必须在本子项目内修掉，不能进基线 |

文档格式：

```markdown
# Windows 测试基线（windows-compat-v2 @ <commit>）

运行：`python -X utf8 -m pytest -o addopts="" -n 4`，Windows 10 19044，Python 3.13.x
汇总：N passed / N failed / N errors / N skipped；web vitest：N failed

## 子项目 2（Wrapper 核心）— N 个
| 测试 | 失败原因（第一行异常） |
|---|---|
| tests/test_x.py::test_y | AttributeError: module 'os' has no attribute 'killpg' |
…（子项目 3、4、5 同样格式）
```

- [ ] **Step 4: 推送分支，开 fork 内 PR，确认 Linux CI**

```bash
git add docs/superpowers/windows-baseline.md
git commit -m "docs: record Windows test baseline after platform layer"
git push -u origin win/1-platform
gh pr create --repo donny-star/cc-remote --base windows-compat-v2 --head win/1-platform \
  --title "Windows 适配 P1：平台兼容层与测试基线" \
  --body "实现 docs/superpowers/plans/2026-10-27-windows-compat-p1-platform.md。基线见 docs/superpowers/windows-baseline.md。

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
gh pr checks --repo donny-star/cc-remote --watch
```

Expected: ubuntu 上的 Python 和 web job 都通过。如果 Linux 上出现新的失败，一定是本子项目的回归，必须修掉再合并。

- [ ] **Step 5: 交叉评审后合并**

先请 Codex 评审这个 PR（通过 `delegate_to_agent`，`agent_type=codex`）。评审意见处理完之后，用 `gh pr merge --merge` 合并到 `windows-compat-v2`。

---

## 后续子项目

子项目 2～5 的详细计划在本计划合并后编写，输入是 `windows-baseline.md` 和本计划落地的接口。
它们的接口约定已经写在 spec 的 §4～§8，分工如下：

| 子项目 | 负责方 | 计划文件（待写） |
|---|---|---|
| 2 Wrapper 核心 | Codex | `2026-10-27-windows-compat-p2-wrapper.md` |
| 3 IPC + service/broker + ConPTY | Claude Code | `2026-10-27-windows-compat-p3-ipc.md` |
| 4 TUI/viewer/messages | Codex | `2026-10-27-windows-compat-p4-clients.md` |
| 5 安装部署 | Claude Code | `2026-10-27-windows-compat-p5-install.md` |
