# Windows 适配 · 子项目 3a：本地 IPC 与 Claude service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `cc_remote/platform/ipc.py`（POSIX = 原 Unix socket 契约；Windows = 127.0.0.1 TCP + 私有
endpoint 文件 + 双向 HMAC 挑战-应答），并把 `cc_remote/claude_service/**` 迁到它上面，使子项目 3
基线里 194 条 Claude service 相关用例中不依赖子项目 2 的部分在 Windows 上通过。

**Architecture:** 平台差异全部收进 `ipc.py`：`ensure_private_directory`、`start_server`、
`open_connection`、`connect_sync` 四个入口，加 `IpcServer` 包装。POSIX 分支把 broker 里已有的
私有目录检查、过期 socket 探测、0600 绑定、peer uid 校验原样搬进来（service 原来的 `same_user`
是同一语义）。Windows 分支在原 `.sock` 路径写私有 JSON endpoint 记录，服务端只监听
`127.0.0.1` 随机端口（`SO_EXCLUSIVEADDRUSE`），连接后先做固定长度、带超时的双向 HMAC
握手，token 从不上线路；客户端连接前按 `pid + start_time` 判定过期。claude_service 只改
调用点：目录检查、监听、连接、journal 权限、锁、信号。

**Tech Stack:** Python 3.13（uv）、asyncio streams、`hmac`/`hashlib`/`secrets`、pywin32 312
（`win32profile`、`win32security`）、pytest 9 + pytest-asyncio。

**Spec:** `docs/superpowers/specs/2026-10-27-windows-compat-design.md`（§4 本地 IPC 为准，另见 §3.1、§7、§9）。
另读 `docs/superpowers/plans/2026-10-27-windows-compat-p2-p3-coordination.md`（文件归属，约束性）和
`docs/superpowers/plans/2026-10-27-windows-compat-p1-handoff.md`（平台层 API 约定）。

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

- 文件归属：P3 在 `cc_remote/platform/` 下只新建 `ipc.py`（本计划）和 `pty.py`（3b 计划），
  不改其它平台文件、`tests/conftest.py`、`pytest.ini`、`cc_remote/wrapper/machine.py`、`cc_remote/config.py`。
- 平台层对外只抛 `OSError` 家族（`ipc` 自己的三个异常也都是 `OSError` 子类）。
- 私有/属主校验一律用 `perms.is_private`、`perms.is_private_fd`、`perms.owned_by_current_user`；
  不要再写 `st_uid != current_uid()` / `st_mode & 0o077`（Windows 上恒为假或恒为真）。
- token 只存在于私有 endpoint 文件和进程内存，绝不写进日志、线路、URL 或异常文本。
- 提交信息：英文 Conventional Commit 主题，多行用 `git commit -F <file>`，主题后空一行，`- ` 列表。

## 前置条件（执行者开始前做一次）

本计划使用 P2 阶段 A 预先定死的 helper：`files.is_reparse_point(path) -> bool`、
`files.open_nofollow(path, flags: int, mode: int = 0o600) -> int`、测试标记
`requires_symlink(reason=...)`。阶段 A 合入 `windows-compat-v2` 后再开始：

```bash
cd /c/Users/xuhaodong/work/cc-remote-p3
git fetch origin
git rebase origin/windows-compat-v2
.venv/Scripts/python.exe -c "from cc_remote.platform import files; print(files.is_reparse_point, files.open_nofollow)"
grep -n "requires_symlink" pytest.ini tests/conftest.py
```

Expected：打印两个函数对象；`grep` 至少命中 `pytest.ini` 的 marker 声明。任何一项缺失就停下，报告
"P2 阶段 A 尚未合入"。

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `cc_remote/platform/ipc.py` | 新建 | 同用户本地 endpoint：私有目录、Windows endpoint 记录、HMAC 握手、`start_server`/`open_connection`/`connect_sync`/`IpcServer` |
| `tests/ipc_helpers.py` | 新建 | 测试共用：`SHORT_TMP`、`share_with_everyone(path)` |
| `tests/test_platform_ipc.py` | 新建 | ipc 的契约、对抗和平台专有测试 |
| `cc_remote/claude_service/wire.py` | 修改 | `private_directory` 改走 `ipc.ensure_private_directory`；删除 `same_user` |
| `cc_remote/claude_service/server.py` | 修改 | 去掉连接处的 `same_user`（改由 ipc 守门）；journal 用 `perms.make_private` |
| `cc_remote/claude_service/client.py` | 修改 | `Connection.connect` 用 `ipc.open_connection` |
| `cc_remote/claude_service/__main__.py` | 修改 | `files.open_nofollow` 锁文件、`ipc.start_server`、`signals.install_shutdown_handler`、可注入 `stop` |
| `tests/test_claude_service.py` | 修改 | 测试环境改用 `ipc.start_server`、`SHORT_TMP`；子进程测试走 `procs` 组 |
| `tests/test_claude_service_main.py` | 新建 | `serve()` 的启动、发布、单实例、清理 |

## `ipc` 接口总览（后续任务和 3b 计划都按这些签名使用）

```python
DEFAULT_LIMIT: int = 2 ** 16
HANDSHAKE_TIMEOUT: float = 2.0
ENDPOINT_VERSION: int = 1
MAX_ENDPOINT_BYTES: int = 4096
TOKEN_BYTES = NONCE_BYTES = MAC_BYTES = 32

class UnsafeEndpointError(PermissionError): ...
class AlreadyRunningError(OSError): ...
class AuthenticationError(ConnectionError): ...

def ensure_private_directory(path: str | os.PathLike[str]) -> Path
async def start_server(path, handler, *, limit: int = DEFAULT_LIMIT) -> IpcServer
async def open_connection(path, *, limit: int = DEFAULT_LIMIT,
                          timeout: float = HANDSHAKE_TIMEOUT) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]
def connect_sync(path, *, timeout: float = HANDSHAKE_TIMEOUT) -> socket.socket

class IpcServer:
    path: Path                     # property
    auth_failures: int             # property
    def is_serving(self) -> bool
    def close(self) -> None        # stop listening; unlink the endpoint only if it is still ours
    async def wait_closed(self) -> None
    async def serve_forever(self) -> None
    async def __aenter__(self) -> IpcServer
    async def __aexit__(self, *exc) -> None

@dataclass(frozen=True)
class Endpoint: port: int; token: bytes; pid: int; start_time: int
    def encode(self) -> bytes
    @classmethod def decode(cls, raw: bytes) -> Endpoint      # ValueError on any deviation
def read_endpoint(path) -> Endpoint                          # Windows record reader
def write_endpoint(path, endpoint: Endpoint) -> tuple[int, int]
```

错误语义（调用方据此映射自己的错误码）：

| 情况 | 异常 |
|---|---|
| endpoint 不存在 | `FileNotFoundError` |
| 过期（POSIX 连接被拒；Windows 记录的进程已不在、start_time 不符或端口不通） | `ConnectionRefusedError` |
| 目录/endpoint 不私有、不属于当前用户、是链接、格式不对 | `UnsafeEndpointError` |
| 对端不是同一用户（peer uid 不符或 HMAC 不通过、握手超时） | `AuthenticationError` |
| `start_server` 时已有活的服务端 | `AlreadyRunningError`（消息含 `already running`） |
| `start_server` 时路径上是别的东西 | `UnsafeEndpointError`（消息含 `refusing to replace`） |

---

### Task 1: `ipc` 骨架与私有目录检查

**Files:**
- Create: `cc_remote/platform/ipc.py`
- Create: `tests/ipc_helpers.py`
- Create: `tests/test_platform_ipc.py`

**Interfaces:**
- Consumes: `files.is_reparse_point(path) -> bool`（P2 阶段 A）；`perms.is_private(path) -> bool`；
  `perms.owned_by_current_user(path) -> bool`；`identity.current_uid() -> int`。
- Produces: `UnsafeEndpointError`、`AlreadyRunningError`、`AuthenticationError`、
  `ensure_private_directory(path) -> Path`、`_profile_directory() -> str`（`functools.cache`）、
  测试 helper `tests.ipc_helpers.SHORT_TMP: str | None`、`share_with_everyone(path: Path) -> None`。

- [ ] **Step 1: 写测试 helper**

`tests/ipc_helpers.py`：

```python
"""Shared helpers for local-endpoint tests on POSIX and Windows."""
from __future__ import annotations

import subprocess
from pathlib import Path

from cc_remote.platform import IS_WINDOWS

# Darwin's sockaddr_un path is only 104 bytes, so POSIX tests keep endpoints in
# /tmp. Windows endpoints have no length limit but must stay inside the
# user's profile, which is where the default temporary directory lives.
SHORT_TMP: str | None = None if IS_WINDOWS else "/tmp"


def share_with_everyone(path: Path) -> None:
    """Make ``path`` readable by other users: the opposite of make_private."""
    if IS_WINDOWS:
        subprocess.run(
            ["icacls", str(path), "/grant", "*S-1-1-0:(R)"],
            check=True, capture_output=True)
    else:
        path.chmod(0o755 if path.is_dir() else 0o644)
```

- [ ] **Step 2: 写失败的测试**

`tests/test_platform_ipc.py`：

```python
"""cc_remote.platform.ipc: same-user local endpoints."""
from __future__ import annotations

import stat
import subprocess

import pytest

from cc_remote.platform import ipc, perms
from tests.ipc_helpers import share_with_everyone


def test_ensure_private_directory_creates_an_owner_only_tree(tmp_path):
    target = ipc.ensure_private_directory(tmp_path / "state" / "endpoint")
    assert target == (tmp_path / "state" / "endpoint").absolute()
    assert target.is_dir()
    assert perms.is_private(target)
    assert perms.owned_by_current_user(target)


def test_ensure_private_directory_accepts_an_existing_private_directory(tmp_path):
    first = ipc.ensure_private_directory(tmp_path / "again")
    assert ipc.ensure_private_directory(first) == first


def test_ensure_private_directory_rejects_a_shared_directory(tmp_path):
    shared = ipc.ensure_private_directory(tmp_path / "shared")
    share_with_everyone(shared)
    with pytest.raises(ipc.UnsafeEndpointError, match="must not be accessible by other users"):
        ipc.ensure_private_directory(shared)


def test_ensure_private_directory_rejects_a_regular_file(tmp_path):
    plain = tmp_path / "plain"
    plain.write_text("x", encoding="utf-8")
    with pytest.raises(ipc.UnsafeEndpointError):
        ipc.ensure_private_directory(plain)


def test_ipc_errors_stay_in_the_oserror_family():
    assert issubclass(ipc.UnsafeEndpointError, PermissionError)
    assert issubclass(ipc.AlreadyRunningError, OSError)
    assert issubclass(ipc.AuthenticationError, ConnectionError)


@pytest.mark.requires_symlink(reason="a symlinked endpoint directory must be refused")
def test_ensure_private_directory_rejects_a_symlinked_directory(tmp_path):
    real = ipc.ensure_private_directory(tmp_path / "real")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ipc.UnsafeEndpointError, match="real directory"):
        ipc.ensure_private_directory(link)


@pytest.mark.windows_only(reason="NTFS junctions are Windows reparse points")
def test_ensure_private_directory_rejects_a_junction(tmp_path):
    real = ipc.ensure_private_directory(tmp_path / "real")
    junction = tmp_path / "junction"
    subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(junction), str(real)],
        check=True, capture_output=True)
    with pytest.raises(ipc.UnsafeEndpointError, match="real directory"):
        ipc.ensure_private_directory(junction)


@pytest.mark.windows_only(reason="the profile boundary only exists for Windows endpoints")
def test_ensure_private_directory_requires_the_user_profile(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setattr(ipc, "_profile_directory", lambda: str(profile))
    assert ipc.ensure_private_directory(profile / "inside").is_dir()
    with pytest.raises(ipc.UnsafeEndpointError, match="profile"):
        ipc.ensure_private_directory(tmp_path / "outside")


@pytest.mark.windows_only(reason="the profile root comes from the process token")
def test_profile_directory_ignores_environment_overrides(monkeypatch):
    real = ipc._profile_directory()
    monkeypatch.setenv("USERPROFILE", "C:\\")
    ipc._profile_directory.cache_clear()
    try:
        assert ipc._profile_directory() == real
    finally:
        ipc._profile_directory.cache_clear()


@pytest.mark.posix_only(reason="POSIX endpoints keep the 0700 mode-bit contract")
def test_posix_directory_mode_is_0700(tmp_path):
    target = ipc.ensure_private_directory(tmp_path / "mode")
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
```

- [ ] **Step 3: 运行，确认失败**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_ipc.py -p no:xdist -o addopts="" -q`
Expected: 收集错误 `ImportError: cannot import name 'ipc' from 'cc_remote.platform'`。

- [ ] **Step 4: 写最小实现**

`cc_remote/platform/ipc.py`：

```python
"""Same-user local IPC endpoints for long-lived cc-remote services.

POSIX keeps the original Unix-socket contract: a private 0700 directory, a
0600 socket owned by the current uid, stale-socket probing, and peer-uid
checks on both ends. Windows CPython has no AF_UNIX, so the endpoint path holds
a private JSON record ``{v, port, token, pid, start_time}`` for a 127.0.0.1
listener instead. Both sides prove knowledge of the token with an HMAC
challenge-response before any application byte flows; the token itself never
crosses the socket.
"""
from __future__ import annotations

import functools
import os
import stat
from pathlib import Path

from cc_remote.platform import IS_WINDOWS, files, perms
from cc_remote.platform.identity import current_uid

StrPath = str | os.PathLike[str]


class UnsafeEndpointError(PermissionError):
    """The endpoint, its record or its directory is not private to this user."""


class AlreadyRunningError(OSError):
    """A live, authenticated server already owns the endpoint."""


class AuthenticationError(ConnectionError):
    """The peer did not prove it is the same user (peer uid or HMAC)."""


# --- private directories ----------------------------------------------------

def ensure_private_directory(path: StrPath) -> Path:
    """Create ``path`` (0700) when missing and require it to be user-private."""
    directory = Path(path).expanduser().absolute()
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise UnsafeEndpointError(f"directory cannot be prepared: {exc}") from exc
    if IS_WINDOWS:
        _check_windows_directory(directory)
    else:
        _check_posix_directory(directory)
    return directory


def _check_posix_directory(directory: Path) -> None:
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise UnsafeEndpointError("directory must be a real directory")
    if info.st_uid != current_uid():
        raise UnsafeEndpointError("directory must be owned by the current uid")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise UnsafeEndpointError("directory must not be accessible by other users")


@functools.cache
def _profile_directory() -> str:
    """The signed-in user's real profile root (independent of HOME/USERPROFILE)."""
    import win32api
    import win32con
    import win32profile
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        return win32profile.GetUserProfileDirectory(token)
    finally:
        token.Close()


def _check_windows_directory(directory: Path) -> None:
    if not directory.is_dir() or files.is_reparse_point(directory):
        raise UnsafeEndpointError("directory must be a real directory")
    profile = os.path.normcase(os.path.realpath(_profile_directory()))
    real = os.path.normcase(os.path.realpath(directory))
    if os.path.commonpath([profile, real]) != profile:
        raise UnsafeEndpointError("directory must be inside the current user's profile")
    if not perms.owned_by_current_user(directory):
        raise UnsafeEndpointError("directory must be owned by the current uid")
    if not perms.is_private(directory):
        raise UnsafeEndpointError("directory must not be accessible by other users")
```

说明（不要写进代码）：Python 3.13 在 Windows 上 `mkdir(mode=0o700)` 会给新目录设当前用户独占的
可继承 DACL（本机实测 `perms.is_private` 为真）；`realpath` 会把 `%TEMP%` 的 8.3 短名
（`XUHAOD~1`）展开成长名，所以与 profile 的比较不受短名影响。

- [ ] **Step 5: 运行，确认通过**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_ipc.py -p no:xdist -o addopts="" -q`
Expected（Windows，未开开发者模式）：`8 passed, 2 skipped`（skip 的是 `posix_only` 和 `requires_symlink`）。
开了开发者模式时 `9 passed, 1 skipped`。

Run: `uvx --from ruff==0.15.13 ruff check cc_remote/platform/ipc.py tests/ipc_helpers.py tests/test_platform_ipc.py`
Expected: `All checks passed!`

- [ ] **Step 6: 提交**

```bash
git add cc_remote/platform/ipc.py tests/ipc_helpers.py tests/test_platform_ipc.py
cat > /tmp/msg-p3a-1.txt <<'EOF'
feat(platform): add ipc private endpoint directories

- Add ipc error types in the OSError family
- Require a real, user-owned, private directory; on Windows also inside the token's profile

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg-p3a-1.txt
```

---

### Task 2: Windows endpoint 记录

**Files:**
- Modify: `cc_remote/platform/ipc.py`
- Modify: `tests/test_platform_ipc.py`

**Interfaces:**
- Consumes: `files.open_nofollow(path, flags, mode=0o600) -> int`（遇 reparse point 抛 `OSError(errno.ELOOP)`）；
  `perms.make_private(path, *, directory: bool) -> None`；`perms.is_private_fd(fd) -> bool`；
  `perms.owned_by_current_user(fd) -> bool`。
- Produces: `ENDPOINT_VERSION`、`MAX_ENDPOINT_BYTES`、`TOKEN_BYTES`、`Endpoint`（`encode() -> bytes`、
  `decode(raw: bytes) -> Endpoint`，任何偏差抛 `ValueError`）、`read_endpoint(path) -> Endpoint`、
  `write_endpoint(path, endpoint) -> tuple[int, int]`（返回已发布文件的 `(st_dev, st_ino)`；目标已存在时抛
  `AlreadyRunningError`，绝不覆盖）。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_platform_ipc.py` 的 import 区改成：

```python
from __future__ import annotations

import base64
import json
import os
import stat
import subprocess

import pytest

from cc_remote.platform import ipc, perms
from tests.ipc_helpers import share_with_everyone
```

文件末尾追加：

```python
TOKEN = bytes(range(32))


def _endpoint(**changes) -> ipc.Endpoint:
    values = {"port": 43210, "token": TOKEN, "pid": os.getpid(), "start_time": 123456789}
    values.update(changes)
    return ipc.Endpoint(**values)


def test_endpoint_record_round_trips():
    raw = _endpoint().encode()
    assert json.loads(raw) == {
        "v": 1, "port": 43210, "token": base64.b64encode(TOKEN).decode("ascii"),
        "pid": os.getpid(), "start_time": 123456789,
    }
    assert ipc.Endpoint.decode(raw) == _endpoint()


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(v=2),
    lambda value: value.update(extra=1),
    lambda value: value.pop("token"),
    lambda value: value.update(port=0),
    lambda value: value.update(port=65536),
    lambda value: value.update(port="43210"),
    lambda value: value.update(pid=True),
    lambda value: value.update(start_time=0),
    lambda value: value.update(token=base64.b64encode(b"short").decode("ascii")),
    lambda value: value.update(token="not base64!"),
], ids=["version", "extra", "missing", "port-zero", "port-high", "port-text",
        "pid-bool", "start-zero", "token-short", "token-text"])
def test_endpoint_record_rejects_deviations(mutate):
    value = json.loads(_endpoint().encode())
    mutate(value)
    with pytest.raises(ValueError):
        ipc.Endpoint.decode(json.dumps(value).encode("ascii"))


def test_endpoint_record_rejects_non_json():
    with pytest.raises(ValueError):
        ipc.Endpoint.decode(b"do not replace")


windows_records = pytest.mark.windows_only(
    reason="endpoint records stand in for AF_UNIX sockets only on Windows")


@windows_records
def test_write_endpoint_publishes_a_private_record(tmp_path):
    directory = ipc.ensure_private_directory(tmp_path / "ep")
    path = directory / "service.sock"
    identity = ipc.write_endpoint(path, _endpoint())
    info = path.lstat()
    assert identity == (info.st_dev, info.st_ino)
    assert perms.is_private(path)
    assert ipc.read_endpoint(path) == _endpoint()
    assert [entry.name for entry in directory.iterdir()] == ["service.sock"]


@windows_records
def test_write_endpoint_never_replaces_an_existing_record(tmp_path):
    directory = ipc.ensure_private_directory(tmp_path / "ep")
    path = directory / "service.sock"
    ipc.write_endpoint(path, _endpoint())
    with pytest.raises(ipc.AlreadyRunningError, match="already running"):
        ipc.write_endpoint(path, _endpoint(port=1))
    assert ipc.read_endpoint(path) == _endpoint()
    assert [entry.name for entry in directory.iterdir()] == ["service.sock"]


@windows_records
def test_read_endpoint_refuses_a_record_readable_by_others(tmp_path):
    path = ipc.ensure_private_directory(tmp_path / "ep") / "service.sock"
    ipc.write_endpoint(path, _endpoint())
    share_with_everyone(path)
    with pytest.raises(ipc.UnsafeEndpointError, match="private"):
        ipc.read_endpoint(path)


@windows_records
def test_read_endpoint_refuses_malformed_and_oversized_records(tmp_path):
    directory = ipc.ensure_private_directory(tmp_path / "ep")
    malformed = directory / "malformed.sock"
    malformed.write_bytes(b"do not replace")
    with pytest.raises(ipc.UnsafeEndpointError, match="malformed"):
        ipc.read_endpoint(malformed)
    oversized = directory / "oversized.sock"
    oversized.write_bytes(b" " * (ipc.MAX_ENDPOINT_BYTES + 1))
    with pytest.raises(ipc.UnsafeEndpointError, match="small regular file"):
        ipc.read_endpoint(oversized)


@windows_records
def test_read_endpoint_reports_a_missing_record(tmp_path):
    with pytest.raises(FileNotFoundError):
        ipc.read_endpoint(ipc.ensure_private_directory(tmp_path / "ep") / "absent.sock")


@windows_records
@pytest.mark.requires_symlink(reason="a symlinked endpoint record must be refused")
def test_read_endpoint_refuses_a_symlink(tmp_path):
    directory = ipc.ensure_private_directory(tmp_path / "ep")
    real = directory / "real.sock"
    ipc.write_endpoint(real, _endpoint())
    link = directory / "link.sock"
    link.symlink_to(real)
    with pytest.raises(ipc.UnsafeEndpointError, match="link"):
        ipc.read_endpoint(link)
```

- [ ] **Step 2: 运行，确认失败**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_ipc.py -p no:xdist -o addopts="" -q`
Expected: 新用例全部 FAIL，`AttributeError: module 'cc_remote.platform.ipc' has no attribute 'Endpoint'`。

- [ ] **Step 3: 实现**

把 `ipc.py` 的 import 区替换为：

```python
from __future__ import annotations

import base64
import contextlib
import dataclasses
import errno
import functools
import json
import os
import secrets
import stat
import time
from pathlib import Path

from cc_remote.platform import IS_WINDOWS, files, perms
from cc_remote.platform.identity import current_uid

StrPath = str | os.PathLike[str]

ENDPOINT_VERSION = 1
MAX_ENDPOINT_BYTES = 4096
TOKEN_BYTES = 32
_SHARING_VIOLATION = 32
```

在 `_check_windows_directory` 之后追加：

```python
# --- Windows endpoint records -------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Endpoint:
    """The private record a Windows server publishes at the endpoint path."""

    port: int
    token: bytes
    pid: int
    start_time: int

    def encode(self) -> bytes:
        return json.dumps({
            "v": ENDPOINT_VERSION, "port": self.port,
            "token": base64.b64encode(self.token).decode("ascii"),
            "pid": self.pid, "start_time": self.start_time,
        }, separators=(",", ":"), sort_keys=True).encode("ascii")

    @classmethod
    def decode(cls, raw: bytes) -> "Endpoint":
        """Parse a record; any deviation raises ``ValueError``."""
        value = json.loads(raw.decode("ascii"))
        if not isinstance(value, dict) or set(value) != {"v", "port", "token", "pid", "start_time"}:
            raise ValueError("endpoint record has unexpected fields")
        port, pid, start_time = value["port"], value["pid"], value["start_time"]
        if (value["v"] != ENDPOINT_VERSION
                or not all(type(item) is int for item in (port, pid, start_time))
                or not 0 < port < 65536 or pid <= 0 or start_time <= 0
                or not isinstance(value["token"], str)):
            raise ValueError("endpoint record is invalid")
        token = base64.b64decode(value["token"].encode("ascii"), validate=True)
        if len(token) != TOKEN_BYTES:
            raise ValueError("endpoint token has the wrong size")
        return cls(port=port, token=token, pid=pid, start_time=start_time)


def read_endpoint(path: StrPath) -> Endpoint:
    """Read a private Windows endpoint record without following links."""
    target = Path(path)
    try:
        fd = files.open_nofollow(target, os.O_RDONLY)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise UnsafeEndpointError("endpoint must not be a link") from exc
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ENDPOINT_BYTES:
            raise UnsafeEndpointError("endpoint must be a small regular file")
        if not (perms.owned_by_current_user(fd) and perms.is_private_fd(fd)):
            raise UnsafeEndpointError("endpoint file must be private to the current user")
        raw = os.read(fd, MAX_ENDPOINT_BYTES + 1)
    finally:
        os.close(fd)
    try:
        return Endpoint.decode(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise UnsafeEndpointError("endpoint record is malformed") from exc


def write_endpoint(path: StrPath, endpoint: Endpoint) -> tuple[int, int]:
    """Publish ``endpoint`` privately at ``path``; never overwrite another record.

    Returns the published file's ``(st_dev, st_ino)`` identity.
    """
    target = Path(path)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = files.open_nofollow(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        try:
            # Restrict the record before the token is written into it.
            perms.make_private(temporary, directory=False)
            data = memoryview(endpoint.encode())
            while data:
                data = data[os.write(fd, data):]
            os.fsync(fd)
        finally:
            os.close(fd)
        _publish(temporary, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        raise
    info = target.lstat()
    return info.st_dev, info.st_ino


def _publish(temporary: Path, target: Path, attempts: int = 10) -> None:
    """No-overwrite atomic rename: like a POSIX bind, a racing server loses."""
    for attempt in range(attempts):
        try:
            os.rename(temporary, target)
            return
        except FileExistsError as exc:
            raise AlreadyRunningError(f"endpoint is already running at {target}") from exc
        except PermissionError as exc:
            # Antivirus and indexers briefly hold freshly written files.
            if getattr(exc, "winerror", None) != _SHARING_VIOLATION or attempt == attempts - 1:
                raise
            time.sleep(min(0.02 * (2 ** attempt), 0.5))
```

- [ ] **Step 4: 运行，确认通过**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_ipc.py -p no:xdist -o addopts="" -q`
Expected（Windows，未开开发者模式）：`25 passed, 3 skipped`。
Run: `uvx --from ruff==0.15.13 ruff check cc_remote/platform/ipc.py tests/test_platform_ipc.py`
Expected: `All checks passed!`

- [ ] **Step 5: 提交**

```bash
git add cc_remote/platform/ipc.py tests/test_platform_ipc.py
cat > /tmp/msg-p3a-2.txt <<'EOF'
feat(platform): add private Windows endpoint records

- Encode {v, port, token, pid, start_time} strictly and reject any deviation
- Read records without following links and only when private to this user
- Publish with a private temporary file and a no-overwrite rename

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg-p3a-2.txt
```

---

### Task 3: 对端认证原语（HMAC 握手、peer uid、连接守门）

**Files:**
- Modify: `cc_remote/platform/ipc.py`
- Modify: `tests/test_platform_ipc.py`

**Interfaces:**
- Consumes: Task 1/2 的常量与异常；`cc_remote.log.logger(name)`（返回值有 `.warning(msg, **fields)`）。
- Produces:
  - `HANDSHAKE_TIMEOUT: float = 2.0`、`NONCE_BYTES = MAC_BYTES = 32`、`_FAILURE_LOG_INTERVAL = 60.0`、`log`
  - `_mac(token: bytes, label: bytes, first: bytes, second: bytes) -> bytes`（`HMAC-SHA256(token, label‖first‖second)`）
  - `async _client_handshake(reader, writer, token: bytes) -> None`（失败抛 `AuthenticationError`；对端提前断开抛 `asyncio.IncompleteReadError`）
  - `async _server_handshake(reader, writer, token: bytes) -> bool`
  - `_peer_uid(sock) -> int`（POSIX；失败抛 `AuthenticationError`）、`_posix_peer_allowed(writer) -> bool`
  - `class _Guard(handler, path: Path, token: bytes | None = None)`：可作为 asyncio 连接回调；`token is None` 时按
    peer uid 守门，否则按 HMAC 握手（总时长 `HANDSHAKE_TIMEOUT`）；拒绝时 `failures += 1`、关闭连接、
    每 60 s 至多写一条 warning；通过后 `await handler(reader, writer)`。

- [ ] **Step 1: 写失败的测试**

import 区追加 `asyncio`、`hashlib`、`hmac`、`socket`、`from pathlib import Path`：

```python
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import socket
import stat
import subprocess
from pathlib import Path

import pytest

from cc_remote.platform import ipc, perms
from tests.ipc_helpers import share_with_everyone
```

文件末尾追加：

```python
OTHER_TOKEN = bytes(range(1, 33))


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition was not reached in time")
        await asyncio.sleep(0.01)


async def _echo(reader, writer) -> None:
    try:
        while data := await reader.read(1024):
            writer.write(data.upper())
            await writer.drain()
    finally:
        writer.close()


async def _loopback(server_side):
    """One loopback TCP connection whose accepting side runs ``server_side``."""
    result = asyncio.get_running_loop().create_future()

    async def accept(reader, writer):
        try:
            result.set_result(await server_side(reader, writer))
        except BaseException as exc:  # surfaced to the test through ``result``
            result.set_exception(exc)
        finally:
            writer.close()

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", server.sockets[0].getsockname()[1])
    return server, reader, writer, result


async def _shutdown(server, writer) -> None:
    writer.close()
    server.close()
    await server.wait_closed()


def test_mac_binds_direction_and_nonce_order():
    first, second = os.urandom(32), os.urandom(32)
    expected = hmac.new(TOKEN, b"srv" + first + second, hashlib.sha256).digest()
    assert ipc._mac(TOKEN, b"srv", first, second) == expected
    assert ipc._mac(TOKEN, b"cli", first, second) != expected
    assert ipc._mac(TOKEN, b"srv", second, first) != expected


@pytest.mark.asyncio
async def test_handshake_proves_the_shared_token_both_ways():
    server, reader, writer, result = await _loopback(
        lambda r, w: ipc._server_handshake(r, w, TOKEN))
    try:
        await ipc._client_handshake(reader, writer, TOKEN)
        assert await asyncio.wait_for(result, 2) is True
    finally:
        await _shutdown(server, writer)


@pytest.mark.asyncio
async def test_client_rejects_a_server_with_another_token():
    server, reader, writer, result = await _loopback(
        lambda r, w: ipc._server_handshake(r, w, TOKEN))
    try:
        with pytest.raises(ipc.AuthenticationError):
            await ipc._client_handshake(reader, writer, OTHER_TOKEN)
        writer.close()
        with pytest.raises(asyncio.IncompleteReadError):
            await asyncio.wait_for(result, 2)
    finally:
        await _shutdown(server, writer)


@pytest.mark.asyncio
async def test_client_reveals_only_a_fresh_nonce_to_an_impostor():
    async def impostor(reader, writer):
        seen = await reader.readexactly(ipc.NONCE_BYTES)
        writer.write(os.urandom(ipc.NONCE_BYTES + ipc.MAC_BYTES))
        await writer.drain()
        return seen + await reader.read()

    server, reader, writer, result = await _loopback(impostor)
    try:
        with pytest.raises(ipc.AuthenticationError):
            await ipc._client_handshake(reader, writer, TOKEN)
        writer.close()
        seen = await asyncio.wait_for(result, 2)
    finally:
        await _shutdown(server, writer)
    assert len(seen) == ipc.NONCE_BYTES
    assert TOKEN not in seen


@pytest.mark.asyncio
async def test_server_rejects_its_own_proof_reflected_back():
    server, reader, writer, result = await _loopback(
        lambda r, w: ipc._server_handshake(r, w, TOKEN))
    try:
        writer.write(os.urandom(ipc.NONCE_BYTES))
        await writer.drain()
        reply = await reader.readexactly(ipc.NONCE_BYTES + ipc.MAC_BYTES)
        writer.write(reply[ipc.NONCE_BYTES:])
        await writer.drain()
        assert await asyncio.wait_for(result, 2) is False
    finally:
        await _shutdown(server, writer)


async def _guarded(handler):
    guard = ipc._Guard(handler, Path("endpoint"), TOKEN)
    server = await asyncio.start_server(guard, "127.0.0.1", 0)
    return guard, server, server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
async def test_guard_hands_authenticated_streams_to_the_handler():
    guard, server, port = await _guarded(_echo)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await ipc._client_handshake(reader, writer, TOKEN)
        writer.write(b"after")
        await writer.drain()
        assert await reader.readexactly(5) == b"AFTER"
    finally:
        await _shutdown(server, writer)
    assert guard.failures == 0


@pytest.mark.asyncio
async def test_guard_rejects_a_bad_proof_without_calling_the_handler():
    calls = []

    async def handler(reader, writer):
        calls.append(1)

    guard, server, port = await _guarded(handler)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(os.urandom(ipc.NONCE_BYTES))
        await writer.drain()
        await reader.readexactly(ipc.NONCE_BYTES + ipc.MAC_BYTES)
        writer.write(os.urandom(ipc.MAC_BYTES))
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        await _wait_until(lambda: guard.failures == 1)
    finally:
        await _shutdown(server, writer)
    assert calls == []


@pytest.mark.asyncio
async def test_guard_drops_a_silent_peer_after_the_handshake_timeout(monkeypatch):
    monkeypatch.setattr(ipc, "HANDSHAKE_TIMEOUT", 0.2)
    guard, server, port = await _guarded(_echo)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        assert await asyncio.wait_for(reader.read(), 2) == b""
        await _wait_until(lambda: guard.failures == 1)
    finally:
        await _shutdown(server, writer)


def test_guard_rate_limits_its_rejection_log(monkeypatch):
    warnings = []
    monkeypatch.setattr(ipc.log, "warning", lambda message, **fields: warnings.append(fields))
    guard = ipc._Guard(_echo, Path("endpoint"), TOKEN)
    for _ in range(3):
        guard._reject()
    assert guard.failures == 3
    assert warnings == [{"endpoint": "endpoint", "failures": 1}]
    guard._last_log -= ipc._FAILURE_LOG_INTERVAL
    guard._reject()
    assert warnings[-1] == {"endpoint": "endpoint", "failures": 4}


@pytest.mark.posix_only(reason="SO_PEERCRED/getpeereid is the POSIX same-user proof")
def test_peer_uid_reads_the_connected_user():
    left, right = socket.socketpair()
    try:
        assert ipc._peer_uid(left) == os.getuid()
        assert ipc._peer_uid(right) == os.getuid()
    finally:
        left.close()
        right.close()
```

- [ ] **Step 2: 运行，确认失败**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_ipc.py -p no:xdist -o addopts="" -q`
Expected: 新用例 FAIL，`AttributeError: module 'cc_remote.platform.ipc' has no attribute '_mac'`（或 `_Guard`/`NONCE_BYTES`）。

- [ ] **Step 3: 实现**

import 区替换为：

```python
from __future__ import annotations

import asyncio
import base64
import contextlib
import dataclasses
import errno
import functools
import hashlib
import hmac
import json
import os
import secrets
import socket
import stat
import struct
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from cc_remote.log import logger
from cc_remote.platform import IS_WINDOWS, files, perms
from cc_remote.platform.identity import current_uid

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]
StrPath = str | os.PathLike[str]

HANDSHAKE_TIMEOUT = 2.0
ENDPOINT_VERSION = 1
MAX_ENDPOINT_BYTES = 4096
TOKEN_BYTES = 32
NONCE_BYTES = 32
MAC_BYTES = 32
_FAILURE_LOG_INTERVAL = 60.0
_SHARING_VIOLATION = 32

log = logger("cc_remote.platform.ipc")
```

文件末尾追加：

```python
# --- peer authentication ------------------------------------------------------

def _mac(token: bytes, label: bytes, first: bytes, second: bytes) -> bytes:
    return hmac.new(token, label + first + second, hashlib.sha256).digest()


async def _client_handshake(reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter, token: bytes) -> None:
    client_nonce = secrets.token_bytes(NONCE_BYTES)
    writer.write(client_nonce)
    await writer.drain()
    reply = await reader.readexactly(NONCE_BYTES + MAC_BYTES)
    server_nonce, server_mac = reply[:NONCE_BYTES], reply[NONCE_BYTES:]
    # Verify the server before sending anything derived from the token.
    if not hmac.compare_digest(server_mac, _mac(token, b"srv", client_nonce, server_nonce)):
        raise AuthenticationError("endpoint did not prove the shared token")
    writer.write(_mac(token, b"cli", server_nonce, client_nonce))
    await writer.drain()


async def _server_handshake(reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter, token: bytes) -> bool:
    client_nonce = await reader.readexactly(NONCE_BYTES)
    server_nonce = secrets.token_bytes(NONCE_BYTES)
    writer.write(server_nonce + _mac(token, b"srv", client_nonce, server_nonce))
    await writer.drain()
    proof = await reader.readexactly(MAC_BYTES)
    return hmac.compare_digest(proof, _mac(token, b"cli", server_nonce, client_nonce))


def _peer_uid(sock: socket.socket) -> int:
    """Return an AF_UNIX peer uid on Linux or macOS, failing closed elsewhere."""
    if hasattr(socket, "SO_PEERCRED"):
        try:
            credentials = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _pid, uid, _gid = struct.unpack("3i", credentials)
            return int(uid)
        except (AttributeError, OSError, struct.error) as exc:
            raise AuthenticationError("cannot read Unix peer credentials") from exc

    import ctypes

    # macOS and the BSDs expose getpeereid(3), though Python does not wrap it.
    libc = ctypes.CDLL(None, use_errno=True)
    getpeereid = getattr(libc, "getpeereid", None)
    if getpeereid is None:
        raise AuthenticationError("Unix peer credentials are unsupported")
    uid = ctypes.c_uint()
    gid = ctypes.c_uint()
    getpeereid.argtypes = (
        ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint))
    getpeereid.restype = ctypes.c_int
    if getpeereid(sock.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise AuthenticationError(f"getpeereid failed: errno {ctypes.get_errno()}")
    return int(uid.value)


def _posix_peer_allowed(writer: asyncio.StreamWriter) -> bool:
    sock = writer.get_extra_info("socket")
    try:
        return sock is not None and _peer_uid(sock) == current_uid()
    except AuthenticationError:
        return False


class _Guard:
    """Authenticate each accepted connection before ``handler`` sees it."""

    def __init__(self, handler: Handler, path: Path, token: bytes | None = None):
        self._handler = handler
        self._path = path
        self._token = token
        self.failures = 0
        self._last_log = float("-inf")

    async def __call__(self, reader: asyncio.StreamReader,
                       writer: asyncio.StreamWriter) -> None:
        if self._token is None:
            allowed = _posix_peer_allowed(writer)
        else:
            try:
                allowed = await asyncio.wait_for(
                    _server_handshake(reader, writer, self._token), HANDSHAKE_TIMEOUT)
            except (OSError, EOFError, asyncio.TimeoutError):
                allowed = False
        if not allowed:
            self._reject()
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            return
        await self._handler(reader, writer)

    def _reject(self) -> None:
        self.failures += 1
        now = time.monotonic()
        if now - self._last_log >= _FAILURE_LOG_INTERVAL:
            self._last_log = now
            log.warning("local endpoint rejected an unauthenticated peer",
                        endpoint=str(self._path), failures=self.failures)
```

- [ ] **Step 4: 运行，确认通过**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_ipc.py -p no:xdist -o addopts="" -q`
Expected（Windows，未开开发者模式）：`35 passed, 4 skipped`。
Run: `uvx --from ruff==0.15.13 ruff check cc_remote/platform/ipc.py tests/test_platform_ipc.py`
Expected: `All checks passed!`（`base64`/`errno` 等此时都已被使用；`IS_WINDOWS` 在 Task 1 已被使用。）

- [ ] **Step 5: 提交**

```bash
git add cc_remote/platform/ipc.py tests/test_platform_ipc.py
cat > /tmp/msg-p3a-3.txt <<'EOF'
feat(platform): add ipc peer authentication

- Add a fixed-size mutual HMAC-SHA256 challenge-response; the token never crosses the socket
- Move the AF_UNIX peer uid lookup behind ipc
- Guard each connection with a bounded handshake and a rate-limited rejection log

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg-p3a-3.txt
```

---

### Task 4: `start_server` / `open_connection` / `connect_sync` 与 `IpcServer`

**Files:**
- Modify: `cc_remote/platform/ipc.py`
- Modify: `tests/test_platform_ipc.py`

**Interfaces:**
- Consumes: Task 1–3 全部；`win32_process.creation_time(pid) -> int | None`（Windows，`None` 表示已退出或无法查询）。
- Produces: `DEFAULT_LIMIT`、`IpcServer`、`start_server`、`open_connection`、`connect_sync`（签名见"接口总览"）。
  POSIX 行为与原 broker 实现逐条一致：父目录私有检查 → 过期 socket 探测（拒绝/不存在 → unlink；能连上 →
  `AlreadyRunningError`；非本人或非 socket → `UnsafeEndpointError`）→ `umask(0o177)` 绑定 → `chmod 0600` 复核 →
  `start_unix_server(sock=...)`；客户端先查 socket 属主/类型/0600，连上后核对对端 uid。

- [ ] **Step 1: 写失败的测试**

import 区再追加 `dataclasses`、`shutil`、`sys`、`tempfile`，以及 helper 的 `SHORT_TMP`：

```python
from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import hmac
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cc_remote.platform import IS_WINDOWS, ipc, perms
from tests.ipc_helpers import SHORT_TMP, share_with_everyone
```

文件末尾追加：

```python
@pytest.fixture
def endpoint_path():
    root = Path(tempfile.mkdtemp(prefix="ccipc-", dir=SHORT_TMP))
    try:
        yield root / "ep.sock"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _own_start_time() -> int:
    from cc_remote.platform import win32_process

    return win32_process.creation_time(os.getpid())


def _closed_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def _leave_stale_endpoint(path: Path) -> None:
    """What a killed server leaves behind: an owned endpoint nobody listens on."""
    ipc.ensure_private_directory(path.parent)
    if IS_WINDOWS:
        ipc.write_endpoint(path, ipc.Endpoint(
            port=_closed_port(), token=os.urandom(32),
            pid=os.getpid(), start_time=_own_start_time()))
        return
    old_umask = os.umask(0o177)
    try:
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(path))
        stale.close()
    finally:
        os.umask(old_umask)


async def _ping(path: Path, payload: bytes = b"ping") -> bytes:
    reader, writer = await ipc.open_connection(path)
    try:
        writer.write(payload)
        await writer.drain()
        return await reader.readexactly(len(payload))
    finally:
        writer.close()


@pytest.mark.asyncio
async def test_round_trip_and_endpoint_removed_on_close(endpoint_path):
    async with await ipc.start_server(endpoint_path, _echo) as server:
        assert server.path == endpoint_path
        assert server.is_serving()
        assert await _ping(endpoint_path) == b"PING"
    assert not endpoint_path.exists()


@pytest.mark.asyncio
async def test_second_server_is_refused_while_the_first_is_live(endpoint_path):
    first = await ipc.start_server(endpoint_path, _echo)
    try:
        with pytest.raises(ipc.AlreadyRunningError, match="already running"):
            await ipc.start_server(endpoint_path, _echo)
        assert await _ping(endpoint_path, b"x") == b"X"
    finally:
        first.close()
        await first.wait_closed()


@pytest.mark.asyncio
async def test_a_foreign_file_at_the_endpoint_is_never_replaced(endpoint_path):
    endpoint_path.write_text("do not replace", encoding="utf-8")
    with pytest.raises(ipc.UnsafeEndpointError, match="refusing to replace"):
        await ipc.start_server(endpoint_path, _echo)
    assert endpoint_path.read_text(encoding="utf-8") == "do not replace"


@pytest.mark.asyncio
async def test_a_stale_endpoint_refuses_clients_and_is_reclaimed(endpoint_path):
    _leave_stale_endpoint(endpoint_path)
    with pytest.raises(ConnectionRefusedError):
        await ipc.open_connection(endpoint_path)
    async with await ipc.start_server(endpoint_path, _echo):
        assert await _ping(endpoint_path) == b"PING"


@pytest.mark.asyncio
async def test_missing_endpoint_raises_file_not_found(endpoint_path):
    with pytest.raises(FileNotFoundError):
        await ipc.open_connection(endpoint_path)
    with pytest.raises(FileNotFoundError):
        ipc.connect_sync(endpoint_path)


@pytest.mark.asyncio
async def test_connect_sync_returns_a_blocking_authenticated_socket(endpoint_path):
    def talk() -> bytes:
        with ipc.connect_sync(endpoint_path) as sock:
            assert sock.gettimeout() is None
            sock.sendall(b"sync")
            data = b""
            while len(data) < 4:
                data += sock.recv(4 - len(data))
            return data

    async with await ipc.start_server(endpoint_path, _echo):
        assert await asyncio.to_thread(talk) == b"SYNC"


@pytest.mark.asyncio
async def test_reader_limit_applies_to_server_streams(endpoint_path):
    seen = []

    async def handler(reader, writer):
        try:
            await reader.readline()
        except ValueError:
            seen.append("limit")
        finally:
            writer.close()

    async with await ipc.start_server(endpoint_path, handler, limit=64):
        reader, writer = await ipc.open_connection(endpoint_path)
        writer.write(b"x" * 200 + b"\n")
        await writer.drain()
        await asyncio.wait_for(reader.read(), 2)
        writer.close()
    assert seen == ["limit"]


@pytest.mark.asyncio
async def test_close_leaves_a_newer_endpoint_alone(endpoint_path):
    server = await ipc.start_server(endpoint_path, _echo)
    endpoint_path.unlink()
    endpoint_path.write_text("newer", encoding="utf-8")
    server.close()
    await server.wait_closed()
    assert endpoint_path.read_text(encoding="utf-8") == "newer"


windows_endpoint = pytest.mark.windows_only(
    reason="the HMAC endpoint replaces AF_UNIX only on Windows")


@windows_endpoint
@pytest.mark.asyncio
async def test_wrong_token_is_rejected_before_the_handler(endpoint_path):
    calls = []

    async def handler(reader, writer):
        calls.append(1)
        writer.close()

    async with await ipc.start_server(endpoint_path, handler) as server:
        forged = endpoint_path.with_name("forged.sock")
        ipc.write_endpoint(forged, dataclasses.replace(
            ipc.read_endpoint(endpoint_path), token=os.urandom(32)))
        with pytest.raises(ipc.AuthenticationError):
            await ipc.open_connection(forged)
        await _wait_until(lambda: server.auth_failures == 1)
    assert calls == []


@windows_endpoint
@pytest.mark.asyncio
async def test_impostor_on_the_recorded_port_never_learns_the_token(endpoint_path):
    received = bytearray()
    finished = asyncio.Event()

    async def impostor(reader, writer):
        received.extend(await reader.readexactly(ipc.NONCE_BYTES))
        writer.write(os.urandom(ipc.NONCE_BYTES + ipc.MAC_BYTES))
        await writer.drain()
        received.extend(await reader.read())
        writer.close()
        finished.set()

    listener = await asyncio.start_server(impostor, "127.0.0.1", 0)
    token = os.urandom(32)
    ipc.ensure_private_directory(endpoint_path.parent)
    ipc.write_endpoint(endpoint_path, ipc.Endpoint(
        port=listener.sockets[0].getsockname()[1], token=token,
        pid=os.getpid(), start_time=_own_start_time()))
    try:
        with pytest.raises(ipc.AuthenticationError):
            await ipc.open_connection(endpoint_path)
        await asyncio.wait_for(finished.wait(), 2)
    finally:
        listener.close()
        await listener.wait_closed()
    assert len(received) == ipc.NONCE_BYTES
    assert token not in bytes(received)


@windows_endpoint
@pytest.mark.asyncio
async def test_dead_owner_is_refused_before_any_connection(endpoint_path):
    connections = []

    async def impostor(reader, writer):
        connections.append(1)
        writer.close()

    listener = await asyncio.start_server(impostor, "127.0.0.1", 0)
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    ipc.ensure_private_directory(endpoint_path.parent)
    ipc.write_endpoint(endpoint_path, ipc.Endpoint(
        port=listener.sockets[0].getsockname()[1], token=os.urandom(32),
        pid=finished.pid, start_time=1))
    try:
        with pytest.raises(ConnectionRefusedError):
            await ipc.open_connection(endpoint_path)
        with pytest.raises(ConnectionRefusedError):
            ipc.connect_sync(endpoint_path)
        await asyncio.sleep(0.1)
    finally:
        listener.close()
        await listener.wait_closed()
    assert connections == []


@windows_endpoint
@pytest.mark.asyncio
async def test_a_reused_pid_with_another_start_time_is_refused(endpoint_path):
    async with await ipc.start_server(endpoint_path, _echo):
        record = ipc.read_endpoint(endpoint_path)
        moved = endpoint_path.with_name("moved.sock")
        ipc.write_endpoint(moved, dataclasses.replace(record, start_time=record.start_time + 1))
        with pytest.raises(ConnectionRefusedError):
            await ipc.open_connection(moved)


@windows_endpoint
@pytest.mark.asyncio
async def test_a_non_private_endpoint_record_is_refused(endpoint_path):
    async with await ipc.start_server(endpoint_path, _echo):
        share_with_everyone(endpoint_path)
        with pytest.raises(ipc.UnsafeEndpointError, match="private"):
            await ipc.open_connection(endpoint_path)
        with pytest.raises(ipc.UnsafeEndpointError, match="private"):
            ipc.connect_sync(endpoint_path)


@windows_endpoint
@pytest.mark.asyncio
async def test_a_silent_peer_is_dropped_after_the_handshake_timeout(endpoint_path, monkeypatch):
    monkeypatch.setattr(ipc, "HANDSHAKE_TIMEOUT", 0.2)
    async with await ipc.start_server(endpoint_path, _echo) as server:
        record = ipc.read_endpoint(endpoint_path)
        reader, writer = await asyncio.open_connection("127.0.0.1", record.port)
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        await _wait_until(lambda: server.auth_failures == 1)


@windows_endpoint
@pytest.mark.asyncio
async def test_listener_is_loopback_only_and_exclusive(endpoint_path):
    async with await ipc.start_server(endpoint_path, _echo) as server:
        record = ipc.read_endpoint(endpoint_path)
        assert server._server.sockets[0].getsockname()[0] == "127.0.0.1"
        intruder = socket.socket()
        intruder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            with pytest.raises(OSError):
                intruder.bind(("127.0.0.1", record.port))
        finally:
            intruder.close()


posix_endpoint = pytest.mark.posix_only(reason="AF_UNIX peer credentials are POSIX-only")


@posix_endpoint
@pytest.mark.asyncio
async def test_posix_socket_is_0600_inside_a_0700_directory(endpoint_path):
    async with await ipc.start_server(endpoint_path, _echo):
        info = endpoint_path.lstat()
        assert stat.S_ISSOCK(info.st_mode)
        assert stat.S_IMODE(info.st_mode) == 0o600
        assert stat.S_IMODE(endpoint_path.parent.stat().st_mode) == 0o700


@posix_endpoint
@pytest.mark.asyncio
async def test_server_drops_a_peer_with_another_uid(endpoint_path, monkeypatch):
    calls = []

    async def handler(reader, writer):
        calls.append(1)
        writer.close()

    real = ipc._peer_uid
    # Accepted sockets are bound to the endpoint path; the client side is not.
    monkeypatch.setattr(
        ipc, "_peer_uid", lambda sock: real(sock) + (1 if sock.getsockname() else 0))
    async with await ipc.start_server(endpoint_path, handler) as server:
        reader, writer = await ipc.open_connection(endpoint_path)
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        await _wait_until(lambda: server.auth_failures == 1)
    assert calls == []


@posix_endpoint
@pytest.mark.asyncio
async def test_client_refuses_a_server_with_another_uid(endpoint_path, monkeypatch):
    async with await ipc.start_server(endpoint_path, _echo):
        monkeypatch.setattr(ipc, "_peer_uid", lambda sock: os.getuid() + 1)
        with pytest.raises(ipc.AuthenticationError):
            await ipc.open_connection(endpoint_path)
        with pytest.raises(ipc.AuthenticationError):
            ipc.connect_sync(endpoint_path)


@posix_endpoint
@pytest.mark.asyncio
async def test_client_refuses_a_socket_with_loose_permissions(endpoint_path):
    async with await ipc.start_server(endpoint_path, _echo):
        endpoint_path.chmod(0o666)
        with pytest.raises(ipc.UnsafeEndpointError):
            await ipc.open_connection(endpoint_path)
```

- [ ] **Step 2: 运行，确认失败**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_ipc.py -p no:xdist -o addopts="" -q`
Expected: 新用例 FAIL，`AttributeError: module 'cc_remote.platform.ipc' has no attribute 'start_server'`。

- [ ] **Step 3: 实现**

在 `ipc.py` 常量区 `HANDSHAKE_TIMEOUT` 上方加 `DEFAULT_LIMIT = 2 ** 16`，在 `_SHARING_VIOLATION` 下方加
`_PROBE_TIMEOUT = 0.5`。文件末尾追加：

```python
# --- server object ------------------------------------------------------------

class IpcServer:
    """An ``asyncio`` server plus the endpoint path it published."""

    def __init__(self, server: asyncio.AbstractServer, path: Path,
                 identity: tuple[int, int], guard: _Guard):
        self._server = server
        self._path = path
        self._identity = identity
        self._guard = guard
        self._removed = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def auth_failures(self) -> int:
        return self._guard.failures

    def is_serving(self) -> bool:
        return self._server.is_serving()

    def close(self) -> None:
        """Stop listening and remove the endpoint if it is still ours."""
        self._server.close()
        if self._removed:
            return
        self._removed = True
        try:
            info = self._path.lstat()
        except FileNotFoundError:
            return
        if (info.st_dev, info.st_ino) == self._identity:
            self._path.unlink()

    async def wait_closed(self) -> None:
        await self._server.wait_closed()

    async def serve_forever(self) -> None:
        await self._server.serve_forever()

    async def __aenter__(self) -> "IpcServer":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.close()
        await self.wait_closed()


# --- public API ---------------------------------------------------------------

async def start_server(path: StrPath, handler: Handler, *,
                       limit: int = DEFAULT_LIMIT) -> IpcServer:
    """Serve ``handler`` for same-user peers at ``path``.

    Raises :class:`AlreadyRunningError` when a live server owns ``path`` and
    :class:`UnsafeEndpointError` when ``path`` or its directory is unsafe.
    """
    target = Path(path).expanduser().absolute()
    ensure_private_directory(target.parent)
    if IS_WINDOWS:
        return await _windows_start_server(target, handler, limit)
    return await _posix_start_server(target, handler, limit)


async def open_connection(path: StrPath, *, limit: int = DEFAULT_LIMIT,
                          timeout: float = HANDSHAKE_TIMEOUT,
                          ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect to a same-user server; the returned streams are authenticated."""
    target = Path(path).expanduser().absolute()
    if IS_WINDOWS:
        return await _windows_open(target, limit, timeout)
    return await _posix_open(target, limit, timeout)


def connect_sync(path: StrPath, *, timeout: float = HANDSHAKE_TIMEOUT) -> socket.socket:
    """Blocking :func:`open_connection` for synchronous CLIs; returns a blocking socket."""
    target = Path(path).expanduser().absolute()
    if IS_WINDOWS:
        return _windows_connect_sync(target, timeout)
    return _posix_connect_sync(target, timeout)


async def _close_quietly(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()


# --- POSIX backend ------------------------------------------------------------

def _check_posix_socket(target: Path) -> None:
    info = target.lstat()  # FileNotFoundError: nothing is listening
    if (info.st_uid != current_uid() or not stat.S_ISSOCK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise UnsafeEndpointError("socket must be owned by this uid with mode 0600")


def _reclaim_posix(target: Path) -> None:
    try:
        info = target.lstat()
    except FileNotFoundError:
        return
    if info.st_uid != current_uid() or not stat.S_ISSOCK(info.st_mode):
        raise UnsafeEndpointError("refusing to replace a non-owned or non-socket path")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.2)
        probe.connect(str(target))
    except (ConnectionRefusedError, FileNotFoundError):
        target.unlink()
    except OSError as exc:
        raise UnsafeEndpointError(f"cannot verify existing socket: {exc}") from exc
    else:
        raise AlreadyRunningError(f"endpoint is already running at {target}")
    finally:
        probe.close()


async def _posix_start_server(target: Path, handler: Handler, limit: int) -> IpcServer:
    _reclaim_posix(target)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old_umask = os.umask(0o177)
    try:
        listener.bind(str(target))
    except BaseException:
        listener.close()
        raise
    finally:
        os.umask(old_umask)
    guard = _Guard(handler, target)
    try:
        os.chmod(target, 0o600)
        info = target.lstat()
        if info.st_uid != current_uid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise UnsafeEndpointError("socket permissions are not 0600")
        listener.listen(socket.SOMAXCONN)
        listener.setblocking(False)
        server = await asyncio.start_unix_server(guard, sock=listener, limit=limit)
    except BaseException:
        listener.close()
        with contextlib.suppress(FileNotFoundError):
            target.unlink()
        raise
    return IpcServer(server, target, (info.st_dev, info.st_ino), guard)


async def _posix_open(target: Path, limit: int, timeout: float,
                      ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    _check_posix_socket(target)
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(str(target), limit=limit), timeout)
    if not _posix_peer_allowed(writer):
        await _close_quietly(writer)
        raise AuthenticationError("endpoint peer uid does not match")
    return reader, writer


def _posix_connect_sync(target: Path, timeout: float) -> socket.socket:
    _check_posix_socket(target)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(str(target))
        if _peer_uid(sock) != current_uid():
            raise AuthenticationError("endpoint peer uid does not match")
        sock.settimeout(None)
    except BaseException:
        sock.close()
        raise
    return sock


# --- Windows backend ----------------------------------------------------------

def _process_start_time(pid: int) -> int | None:
    from cc_remote.platform import win32_process

    return win32_process.creation_time(pid)


def _require_live(endpoint: Endpoint) -> None:
    if _process_start_time(endpoint.pid) != endpoint.start_time:
        raise ConnectionRefusedError(errno.ECONNREFUSED, "endpoint owner is no longer running")


async def _probe(endpoint: Endpoint) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", endpoint.port), _PROBE_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        return False
    try:
        await asyncio.wait_for(
            _client_handshake(reader, writer, endpoint.token), _PROBE_TIMEOUT)
        return True
    except (OSError, EOFError, asyncio.TimeoutError):
        return False
    finally:
        await _close_quietly(writer)


async def _reclaim_windows(target: Path) -> None:
    try:
        endpoint = read_endpoint(target)
    except FileNotFoundError:
        return
    except OSError as exc:  # unsafe, malformed, a directory, unreadable
        raise UnsafeEndpointError(
            "refusing to replace a non-owned or non-endpoint path") from exc
    # A completed handshake is stronger evidence than pid liveness: it also
    # covers an owner whose process cannot be inspected (e.g. elevated).
    if await _probe(endpoint):
        raise AlreadyRunningError(f"endpoint is already running at {target}")
    target.unlink()


async def _windows_start_server(target: Path, handler: Handler, limit: int) -> IpcServer:
    await _reclaim_windows(target)
    token = secrets.token_bytes(TOKEN_BYTES)
    guard = _Guard(handler, target, token)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Without exclusive use another process could bind the same port.
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(socket.SOMAXCONN)
        listener.setblocking(False)
        server = await asyncio.start_server(guard, sock=listener, limit=limit)
    except BaseException:
        listener.close()
        raise
    try:
        start_time = _process_start_time(os.getpid())
        if start_time is None:
            raise OSError("cannot read this process's start time")
        identity = write_endpoint(target, Endpoint(
            port=listener.getsockname()[1], token=token,
            pid=os.getpid(), start_time=start_time))
    except BaseException:
        server.close()
        await server.wait_closed()
        raise
    return IpcServer(server, target, identity, guard)


async def _windows_open(target: Path, limit: int, timeout: float,
                        ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    endpoint = read_endpoint(target)
    _require_live(endpoint)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection("127.0.0.1", endpoint.port, limit=limit), timeout)
    try:
        await asyncio.wait_for(_client_handshake(reader, writer, endpoint.token), timeout)
    except (EOFError, asyncio.TimeoutError) as exc:
        await _close_quietly(writer)
        raise AuthenticationError("endpoint handshake failed") from exc
    except BaseException:
        await _close_quietly(writer)
        raise
    return reader, writer


def _recv_exactly(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise AuthenticationError("endpoint closed during the handshake")
        data += chunk
    return bytes(data)


def _windows_connect_sync(target: Path, timeout: float) -> socket.socket:
    endpoint = read_endpoint(target)
    _require_live(endpoint)
    sock = socket.create_connection(("127.0.0.1", endpoint.port), timeout=timeout)
    try:
        client_nonce = secrets.token_bytes(NONCE_BYTES)
        sock.sendall(client_nonce)
        reply = _recv_exactly(sock, NONCE_BYTES + MAC_BYTES)
        server_nonce, server_mac = reply[:NONCE_BYTES], reply[NONCE_BYTES:]
        if not hmac.compare_digest(
                server_mac, _mac(endpoint.token, b"srv", client_nonce, server_nonce)):
            raise AuthenticationError("endpoint did not prove the shared token")
        sock.sendall(_mac(endpoint.token, b"cli", server_nonce, client_nonce))
        sock.settimeout(None)
    except socket.timeout as exc:
        sock.close()
        raise AuthenticationError("endpoint handshake timed out") from exc
    except BaseException:
        sock.close()
        raise
    return sock


__all__ = [
    "AlreadyRunningError", "AuthenticationError", "DEFAULT_LIMIT", "Endpoint",
    "HANDSHAKE_TIMEOUT", "IpcServer", "UnsafeEndpointError", "connect_sync",
    "ensure_private_directory", "open_connection", "read_endpoint", "start_server",
    "write_endpoint",
]
```

注意：`IpcServer.__init__` 的 `guard: _Guard` 注解依赖 `from __future__ import annotations`（已在文件头），
`_Guard` 定义在 Task 3 的段落里、位于 `IpcServer` 之前，不需要字符串注解。

- [ ] **Step 4: 运行，确认通过**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_ipc.py -p no:xdist -o addopts="" -q`
Expected（Windows，未开开发者模式）：`50 passed, 8 skipped`（skip = 6 个 `posix_only` + 2 个 `requires_symlink`）。
连跑 3 次都通过（握手、超时类用例不得抖动）：

```bash
for i in 1 2 3; do PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_platform_ipc.py -p no:xdist -o addopts="" -q | tail -1; done
```

Run: `uvx --from ruff==0.15.13 ruff check cc_remote/platform/ipc.py tests/test_platform_ipc.py`
Expected: `All checks passed!`

- [ ] **Step 5: 提交**

```bash
git add cc_remote/platform/ipc.py tests/test_platform_ipc.py
cat > /tmp/msg-p3a-4.txt <<'EOF'
feat(platform): serve and connect same-user ipc endpoints

- POSIX: move the broker's stale-socket probe, 0600 bind and peer uid checks behind ipc unchanged
- Windows: listen on an exclusive 127.0.0.1 port, publish a private endpoint record, require the HMAC handshake
- Refuse dead or reused owners before connecting and reclaim stale endpoints on start
- Remove the endpoint on close only while it is still ours

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg-p3a-4.txt
```

---

### Task 5: Claude service 迁到 `ipc`

**Files:**
- Modify: `cc_remote/claude_service/wire.py:1-56`（import 区、删除 `same_user`、重写 `private_directory`）
- Modify: `cc_remote/claude_service/server.py:27-30, 43-47, 578-581`
- Modify: `cc_remote/claude_service/client.py:5-15, 53-55`
- Modify: `tests/test_claude_service.py:1-16, 70-80, 733-800`

**Interfaces:**
- Consumes: `ipc.ensure_private_directory`、`ipc.start_server`、`ipc.open_connection`、`ipc.UnsafeEndpointError`；
  `perms.make_private(path, *, directory: bool)`；`procs.create_subprocess_exec_group(program, *args, **kw)`、
  `procs.killpg(pid, sig)`、`procs.SIGKILL`、`procs.SIGTERM`；`win32_process.parent_map() -> dict[int, int] | None`。
- Produces（签名不变，供 deploy、wrapper 继续使用）：`wire.private_directory(path: Path) -> None`（不私有时抛
  `PermissionError("Claude service directory must be private and owned by this user")`）；
  `Connection.connect()` 现在返回前已完成同用户认证；`Service.connection(reader, writer)` 只接收已认证的流
  （peer 检查移到 `ipc` 守门，`wire.same_user` 删除——仓库内只有 `server.py` 用它）。

- [ ] **Step 1: 写失败的测试**

`tests/test_claude_service.py` 顶部 import 改为：

```python
"""Zero-model regression for SDK lifetime across controller replacement."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import sys
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions, PermissionResultAllow, ToolPermissionContext

from cc_remote.claude_service.client import RemoteClient
from cc_remote.claude_service.server import Service
from cc_remote.platform import IS_WINDOWS, ipc, perms, procs
from tests.ipc_helpers import SHORT_TMP, share_with_everyone
```

把 `environment()` 改为（只动临时目录与监听两行）：

```python
@contextlib.asynccontextmanager
async def environment():
    # Darwin sockaddr_un is short; pytest's normal temp hierarchy exceeds it.
    with tempfile.TemporaryDirectory(prefix="cc-sdk-", dir=SHORT_TMP) as root:
        directory = Path(root)
        service = Service(directory, factory=FakeClient)
        path = directory / "service.sock"
        server = await ipc.start_server(path, service.connection)
```

（`environment()` 其余内容保持原样。）在 `released()` 之后追加三个新测试：

```python
def test_service_directory_must_be_private_to_this_user(tmp_path):
    directory = tmp_path / "service"
    Service(directory, factory=FakeClient)
    assert perms.is_private(directory)
    shared = tmp_path / "shared"
    shared.mkdir()
    share_with_everyone(shared)
    with pytest.raises(PermissionError, match="must be private"):
        Service(shared, factory=FakeClient)


@pytest.mark.asyncio
async def test_service_journal_and_endpoint_are_private():
    async with environment() as (service, attach):
        client = await attach()
        assert perms.is_private(service.sessions[client.id].journal_path)
        assert perms.is_private(service.directory / "service.sock")


def _is_service_process(reported: int, spawned: int) -> bool:
    """A Windows venv python.exe is a launcher; the service is its child."""
    if reported == spawned:
        return True
    if not IS_WINDOWS:
        return False
    from cc_remote.platform import win32_process

    return (win32_process.parent_map() or {}).get(reported) == spawned
```

把 `test_real_controller_process_exit_leaves_service_and_accepted_work_alive` 的 `service_code` 里
`async def run():` 的两行改为：

```python
async def run():
    service = Service(Path(sys.argv[1]), factory=RunningClient)
    server = await ipc.start_server(sys.argv[2], service.connection)
```

并在 `service_code` 的 import 行之后加一行 `from cc_remote.platform import ipc`。把该测试的 `go()` 整体替换为：

```python
    async def go():
        with tempfile.TemporaryDirectory(prefix="cc-sdk-process-", dir=SHORT_TMP) as root:
            path = str(Path(root) / "service.sock")
            service = await procs.create_subprocess_exec_group(
                sys.executable, "-c", service_code, root, path, stdout=asyncio.subprocess.PIPE)
            controller = None
            connection = Connection(path)
            try:
                assert await asyncio.wait_for(service.stdout.readline(), 3) == b"ready\n"
                controller = await procs.create_subprocess_exec_group(
                    sys.executable, "-c", controller_code, path, stdout=asyncio.subprocess.PIPE)
                assert await asyncio.wait_for(controller.stdout.readline(), 3) == b"accepted\n"
                # Only the process tree created by this test (a Windows venv
                # python.exe is a launcher whose child holds the connection).
                procs.killpg(controller.pid, procs.SIGKILL)
                await controller.wait()
                await connection.connect()
                descriptions = await connection.call("list")
                assert len(descriptions) == 1
                description = descriptions[0]
                assert _is_service_process(description["pid"], service.pid)
                assert service.returncode is None
                assert description["turn"]["id"] == "only-query"
                await connection.call("open", {
                    "session": description["id"], "metadata": description["metadata"], "options": {},
                })
                events = await connection.call("events", {"session": description["id"], "after": 0})
                assert events["events"][-1]["data"]["result"] == "finished"
            finally:
                await connection.disconnect()
                if controller is not None and controller.returncode is None:
                    procs.killpg(controller.pid, procs.SIGKILL)
                    await controller.wait()
                if service.returncode is None:
                    procs.killpg(service.pid, procs.SIGTERM)
                    await service.wait()
```

（`controller_code` 不变；`asyncio.run(go())` 等外层不变。）

- [ ] **Step 2: 运行，确认失败**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_claude_service.py -p no:xdist -o addopts="" -q`
Expected: 大量 FAIL/ERROR，首行异常为 `PermissionError: Claude service directory must be private and owned by this user`
（`wire.private_directory` 仍用 `st_mode & 0o077`）。

- [ ] **Step 3: 改 `wire.py`**

import 区与常量改为：

```python
"""Bounded local framing. This is not the browser/relay wire protocol."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import struct
from pathlib import Path

from cc_remote.platform import ipc

VERSION = 1
MAX_FRAME = 32 * 1024 * 1024
```

删除整个 `same_user` 函数，把 `private_directory` 替换为：

```python
def private_directory(path: Path) -> None:
    try:
        ipc.ensure_private_directory(path)
    except ipc.UnsafeEndpointError as exc:
        raise PermissionError(
            "Claude service directory must be private and owned by this user") from exc
```

- [ ] **Step 4: 改 `server.py`**

import 区（第 27–30 行）改为：

```python
from cc_remote.platform import perms

from .wire import (
    ControllerLeaseConflict, decode_sdk, encode_sdk, private_directory,
    read_frame, write_frame,
)
```

`Journal.__init__` 里：

```python
        self.db = sqlite3.connect(path)
        perms.make_private(path, directory=False)
```

（原为 `os.chmod(path, 0o600)`；POSIX 上 `make_private` 就是 `os.chmod(path, 0o600)`。`os` 仍被本文件其它地方使用，保留 import。）

`Service.connection` 开头删掉这三行（同用户校验已在 `ipc.start_server` 的守门里完成）：

```python
        if not same_user(writer):
            writer.close()
            return
```

- [ ] **Step 5: 改 `client.py`**

import 区在 `from cc_remote.log import logger` 下加一行 `from cc_remote.platform import ipc`；
`Connection.connect` 改为：

```python
    async def connect(self) -> None:
        self.reader, self.writer = await ipc.open_connection(self.socket_path)
        self.task = asyncio.create_task(self._read())
```

- [ ] **Step 6: 运行，确认通过**

Run（逐文件）：

```bash
for f in test_claude_service test_claude_service_startup test_claude_service_lease test_claude_service_callbacks test_claude_service_delivery test_claude_pending_query test_claude_steer_attachments test_platform_ipc; do
  echo "== $f"; PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/$f.py -p no:xdist -o addopts="" -q 2>&1 | tail -2
done
```

Expected：
- `test_claude_service_startup`（7）、`test_claude_service_lease`（13）、`test_claude_service_callbacks`（7）、
  `test_claude_service_delivery`（11）、`test_claude_pending_query`（11）、`test_claude_steer_attachments`（4）全部通过；
- `test_claude_service`：只剩 2 个失败，且都属于子项目 2 —
  `test_private_socket_registration_and_explicit_override`（`cc_remote/config.py:162` 的 `os.O_NOFOLLOW`）、
  `test_machine_recovers_background_followup_completion_without_resubmitting`（经 `machine.py`，首个异常为
  `RollbackJournalError: rollback journal could not be persisted`）。用下面的命令确认残留失败只来自这些根因：

```bash
PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_claude_service.py -p no:xdist -o addopts="" -q --tb=line 2>&1 \
  | grep -E "^E |Error:" | grep -vE "O_NOFOLLOW|rollback journal|profile state migration|TimeoutError|CancelledError|IndexError" || echo "only P2 root causes remain"
```

Expected: `only P2 root causes remain`（`TimeoutError`/`CancelledError`/`IndexError` 是这些 machine 用例在
journal 失败后的连带症状；Step 7 的全量清单里逐条核对）。

Run: `uvx --from ruff==0.15.13 ruff check cc_remote/claude_service tests/test_claude_service.py`
Expected: `All checks passed!`

- [ ] **Step 7: 提交**

```bash
git add cc_remote/claude_service/wire.py cc_remote/claude_service/server.py cc_remote/claude_service/client.py tests/test_claude_service.py
cat > /tmp/msg-p3a-5.txt <<'EOF'
refactor(claude-service): serve controllers through platform ipc

- Check the service directory with ipc private-directory rules
- Authenticate peers in ipc instead of per-connection SO_PEERCRED
- Make journals private through platform permissions
- Run the process-lifetime regression through platform process groups

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg-p3a-5.txt
```

---

### Task 6: Claude service 进程入口（锁、监听、信号、注册文件）

**Files:**
- Modify: `cc_remote/claude_service/__main__.py`（整文件替换）
- Create: `tests/test_claude_service_main.py`

**Interfaces:**
- Consumes: `files.open_nofollow`、`locks.flock`/`LOCK_EX`/`LOCK_NB`（冲突抛 `BlockingIOError`）、
  `signals.install_shutdown_handler(loop, callback) -> Callable[[], None]`、`ipc.start_server`、
  `wrapper.process_scan.process_identity(pid) -> ProcessIdentity | None`。
- Produces: `async serve(directory: Path, *, stop: asyncio.Event | None = None) -> None`（`stop` 仅供测试注入；
  不给时由关机信号触发）；`main() -> None` 不变。`service.json` 字段不变：
  `{"pid", "start_ticks", "source", "socket", "protocol": 1}`。

- [ ] **Step 1: 写失败的测试**

`tests/test_claude_service_main.py`：

```python
"""The persistent Claude service process entry point."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from cc_remote.claude_service import __main__ as service_main
from cc_remote.claude_service.client import Connection
from cc_remote.platform import perms
from tests.ipc_helpers import SHORT_TMP


@pytest.fixture
def state_dir():
    root = Path(tempfile.mkdtemp(prefix="cc-svc-", dir=SHORT_TMP))
    try:
        yield root / "state"
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def _wait_for(path: Path) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while not path.exists():
        if loop.time() > deadline:
            raise AssertionError(f"{path} was never published")
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_serve_publishes_answers_and_cleans_up(state_dir):
    stop = asyncio.Event()
    task = asyncio.create_task(service_main.serve(state_dir, stop=stop))
    try:
        await _wait_for(state_dir / "service.json")
        state = json.loads((state_dir / "service.json").read_text())
        assert state["pid"] == os.getpid()
        assert state["socket"] == str(state_dir / "service.sock")
        assert state["protocol"] == 1
        assert perms.is_private(state_dir)
        assert perms.is_private(state_dir / "service.json")
        connection = Connection(state["socket"])
        await connection.connect()
        try:
            hello = await connection.call("hello")
        finally:
            await connection.disconnect()
        assert hello["pid"] == os.getpid()
        assert hello["strict_controller_leases"] is True
    finally:
        stop.set()
        await asyncio.wait_for(task, 5)
    assert not (state_dir / "service.sock").exists()
    assert not (state_dir / "service.json").exists()


@pytest.mark.asyncio
async def test_second_service_on_the_same_directory_is_refused(state_dir):
    stop = asyncio.Event()
    first = asyncio.create_task(service_main.serve(state_dir, stop=stop))
    try:
        await _wait_for(state_dir / "service.json")
        with pytest.raises(BlockingIOError):
            await service_main.serve(state_dir, stop=asyncio.Event())
        assert (state_dir / "service.json").exists()
    finally:
        stop.set()
        await asyncio.wait_for(first, 5)


@pytest.mark.asyncio
async def test_shutdown_handler_stops_the_service_and_is_uninstalled(state_dir, monkeypatch):
    installed = {}

    def install(loop, callback):
        installed["callback"] = callback
        installed["removed"] = False
        return lambda: installed.update(removed=True)

    monkeypatch.setattr(service_main.signals, "install_shutdown_handler", install)
    task = asyncio.create_task(service_main.serve(state_dir))
    await _wait_for(state_dir / "service.json")
    installed["callback"]()
    await asyncio.wait_for(task, 5)
    assert installed["removed"] is True
    assert not (state_dir / "service.sock").exists()
```

- [ ] **Step 2: 运行，确认失败**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_claude_service_main.py -p no:xdist -o addopts="" -q`
Expected: FAIL，`TypeError: serve() got an unexpected keyword argument 'stop'`（Windows 上未注入 `stop` 的用例为
`AttributeError: module 'os' has no attribute 'O_NOFOLLOW'`）。

- [ ] **Step 3: 实现**

`cc_remote/claude_service/__main__.py` 整文件替换为：

```python
"""Run the SDK service under its own LaunchAgent, systemd user unit or scheduled task."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from cc_remote.platform import files, ipc, locks, signals
from cc_remote.wrapper.process_scan import process_identity

from .server import Service
from .wire import private_directory


async def serve(directory: Path, *, stop: asyncio.Event | None = None) -> None:
    private_directory(directory)
    lock_fd = files.open_nofollow(directory / "service.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        locks.flock(lock_fd, locks.LOCK_EX | locks.LOCK_NB)
        path = directory / "service.sock"
        service = Service(directory)
        server = await ipc.start_server(path, service.connection)
        identity = process_identity(os.getpid())
        state = {
            "pid": os.getpid(), "start_ticks": identity.start_ticks if identity else None,
            "source": str(Path(__file__).resolve().parents[2]),
            "socket": str(path), "protocol": 1,
        }
        descriptor = directory / "service.json"
        fd = files.open_nofollow(descriptor, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump(state, out)
        stopped = stop if stop is not None else asyncio.Event()
        uninstall = signals.install_shutdown_handler(asyncio.get_running_loop(), stopped.set)
        try:
            await stopped.wait()
        finally:
            uninstall()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*(session.close() for session in service.sessions.values()))
            descriptor.unlink(missing_ok=True)
    finally:
        os.close(lock_fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(serve(arguments.state_dir.expanduser().absolute()))


if __name__ == "__main__":
    main()
```

POSIX 行为对照（写进 PR 描述，不写进代码）：`open_nofollow` = 原 `O_NOFOLLOW` 打开；旧 socket 由
`ipc` 按"属主 + S_ISSOCK → 探测 → unlink"处理（服务锁已保证不会有活的同目录服务端，所以与原来无条件
unlink 等价）；`install_shutdown_handler` 在 POSIX 上就是 SIGTERM/SIGINT 的 `add_signal_handler`；
endpoint 由 `IpcServer.close()` 在仍属于自己时删除。

- [ ] **Step 4: 运行，确认通过**

Run: `PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/test_claude_service_main.py tests/test_claude_service_install.py -p no:xdist -o addopts="" -q`
Expected: 全部通过（`test_claude_service_main.py` 3 passed；`test_claude_service_install.py` 在基线中没有失败条目，
它通过 `wire.private_directory` 间接覆盖本任务，必须保持全绿）。

Run: `uvx --from ruff==0.15.13 ruff check cc_remote/claude_service tests/test_claude_service_main.py`
Expected: `All checks passed!`

- [ ] **Step 5: 提交**

```bash
git add cc_remote/claude_service/__main__.py tests/test_claude_service_main.py
cat > /tmp/msg-p3a-6.txt <<'EOF'
feat(claude-service): run the service process on every platform

- Take the single-instance lock through a no-follow descriptor
- Publish the endpoint with platform ipc and stop on platform shutdown signals
- Accept an injected stop event for in-process tests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg-p3a-6.txt
```

---

### Task 7: 基线验收、本地门禁与 PR

**Files:**
- 无代码改动（若验收发现 P3 自身问题，回到对应任务修复并另起提交）。

**Interfaces:**
- Consumes: Task 1–6。
- Produces: 推送的分支 `win/3-ipc` 与 fork 内 PR（目标 `windows-compat-v2`）。

- [ ] **Step 1: Windows 上逐文件跑子项目 3a 范围**

```bash
for f in test_platform_ipc test_claude_service_main test_claude_service test_claude_service_startup \
         test_claude_service_lease test_claude_service_callbacks test_claude_service_delivery \
         test_claude_service_restart test_claude_service_recovery test_claude_pending_query \
         test_claude_steer_attachments test_claude_steering test_claude_background_completion \
         test_claude_compaction_receipts test_claude_live_context test_claude_native_permissions \
         test_claude_native_task_merge test_manual_compaction test_child_env test_tui_local; do
  PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest tests/$f.py -p no:xdist -o addopts="" -q \
    --junitxml=/tmp/p3a-junit/$f.xml > /tmp/p3a-junit/$f.log 2>&1; echo "$f exit=$?"
done
```

（先 `mkdir -p /tmp/p3a-junit`。）

- [ ] **Step 2: 对照基线，区分"P3 已修"和"等 P2"**

```bash
.venv/Scripts/python.exe - <<'EOF'
import glob, json, xml.etree.ElementTree as ET
base = json.load(open("docs/superpowers/windows-baseline.json", encoding="utf-8"))
items = base if isinstance(base, list) else base["failures"]
mine = {x["nodeid"] for x in items if str(x["owner"]) == "3" and "broker" not in x["nodeid"]}
status = {}
for path in glob.glob("/tmp/p3a-junit/*.xml"):
    for case in ET.parse(path).iter("testcase"):
        node = "tests/" + case.get("classname").split(".")[-1] + ".py::" + case.get("name")
        bad = case.find("failure") is not None or case.find("error") is not None
        status[node] = "fail" if bad else ("skip" if case.find("skipped") is not None else "pass")
fixed = sorted(n for n in mine if status.get(n) == "pass")
left = sorted(n for n in mine if status.get(n) != "pass")
print("baseline P3 service entries:", len(mine), "fixed:", len(fixed), "left:", len(left))
for n in left: print("LEFT", status.get(n), n)
EOF
```

Expected：`baseline P3 service entries: 194`，`fixed` 至少 135、`left` 至多 59。写计划时用等价的临时补丁实测为
134 / 60，残留按文件：`test_claude_background_completion` 20、`test_claude_steering` 15、
`test_claude_native_task_merge` 9、`test_manual_compaction` 4、`test_claude_native_permissions` 4、
`test_claude_service` 3（其中 `test_real_controller_process_exit_leaves_service_and_accepted_work_alive` 由 Task 5 修复）、
`test_claude_service_recovery` 3、`test_claude_service_restart` 1、`test_claude_compaction_receipts` 1。
`left` 里每一条，在对应 `.log` 中的第一个仓库内异常都属于下列
子项目 2 根因之一（逐条核对，写进 PR 描述）：
`RollbackJournalError: rollback journal could not be persisted`（`wrapper/rollback_commands.py` 的目录 `os.open`）、
`Claude profile state migration is incomplete`（`machine.py`）、
`CodexControlStoreError: Codex control store is not a private bounded file`（`wrapper/codex_controls.py`）、
`module 'os' has no attribute 'O_NOFOLLOW'`（`config.py:_claude_service_socket`）。
出现其它根因 = P3 问题，回到对应任务修复。

- [ ] **Step 3: 本地门禁（CLAUDE.md "Commit and PR gate"）**

```bash
.venv/Scripts/python.exe -m pytest tests/test_platform_ipc.py tests/test_claude_service_main.py -p no:xdist -o addopts="" -q
uvx --from ruff==0.15.13 ruff check cc_remote tests deploy
npm --prefix web run build
npm --prefix web run test:reliability
npm --prefix web run lint
git diff --check origin/windows-compat-v2...HEAD
```

Expected：每条命令退出码 0。Windows 上无法运行的 `bash -n`/`shellcheck` 两项与全量 POSIX pytest 交给
Linux CI；在 PR 描述里如实写明"本地 Windows 跑了哪些，哪些交 CI"。

- [ ] **Step 4: 推送并开 fork 内 PR**

```bash
git push -u origin win/3-ipc
cat > /tmp/pr-p3a.md <<'EOF'
## Summary
- Add `cc_remote/platform/ipc.py`: POSIX keeps the Unix-socket contract (private 0700 dir, 0600 socket, stale probe, peer uid); Windows uses an exclusive 127.0.0.1 listener, a private endpoint record and a mutual HMAC handshake
- Move `claude_service` (directory checks, listener, client, journal permissions, lock, shutdown signals) onto platform APIs

## Validation
- Windows: per-file pytest for the files listed in plan Task 7 (results and remaining P2-owned root causes below)
- Linux: CI

## Remaining baseline entries (owned by P2)
<paste Task 7 Step 2 LEFT list grouped by root cause>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
gh pr create --repo donny-star/cc-remote --base windows-compat-v2 --head win/3-ipc \
  --title "feat(platform): same-user local IPC and Claude service on Windows" --body-file /tmp/pr-p3a.md
```

Expected：PR 创建成功并输出 URL；之后等待 Linux CI 全绿（`gh pr checks --repo donny-star/cc-remote <number> --watch`）。

---

## 跨子项目依赖（需要 controller 裁定 / 对方配合）

| 需要谁 | 什么 | 原因 |
|---|---|---|
| P2（阶段 A，已定签名） | `files.is_reparse_point`、`files.open_nofollow`、`requires_symlink` 标记 | Task 1、2、6 直接调用 |
| P2 | `cc_remote/config.py:_claude_service_socket` 改用 `files.open_nofollow` + `perms.is_private_fd`/`owned_by_current_user` | 基线 `test_claude_service.py::test_private_socket_registration_and_explicit_override`（root_cause `posix_open_flags`）归 P3 但代码在 P2 的 `config.py` |
| P2 | `machine.py` 的 Claude profile 迁移、`wrapper/rollback_commands.py` 目录 fsync、`wrapper/codex_controls.py` 私有判定 | 约 60 条归 P3 的 service 用例经 `machine.py` 驱动，在这些 P2 根因修好前仍失败（Task 7 Step 2 逐条列出） |
| P4 | `session_messages`（wrapper 与 CLI 两侧）与 TUI 客户端改用 `ipc.start_server` / `ipc.open_connection` / `ipc.connect_sync` | 规范 §4 范围内，但文件归 P4；签名见本计划"接口总览" |
| P5 | `deploy/install_claude_service.py` 继续用 `wire.private_directory`（签名不变）；Windows 计划任务以 `pythonw -m cc_remote.claude_service --state-dir …` 启动 | `serve()` 已可在无控制台进程里运行（`install_shutdown_handler` 会退化为仅记录警告） |

## 裁定记录（本计划作出的决定）

1. **endpoint 发布用"不覆盖"的原子重命名**（`os.rename` → `MoveFileExW` 不带 `REPLACE_EXISTING`），而不是
   `files.replace`：这与 POSIX `bind()` 在路径已存在时 `EADDRINUSE` 的语义一致，两个同时启动的服务端里
   后到者失败退出，不会出现"被覆盖的旧服务端仍在运行但没人能连上"。同样是原子写；共享冲突（错误码 32）按
   `files.replace` 的退避节奏有限重试。
2. **服务端回收旧 endpoint 时以"握手是否成功"判定在运行**（而不是先看 pid）：握手成功比 pid 存活更强，也覆盖
   以管理员权限运行、无法 `OpenProcess` 的同用户服务端。客户端连接前仍严格按 `pid + start_time` 判定过期（§4），
   防止旧端口被复用后的中继。
3. **profile 边界取进程令牌的真实 profile 目录**（`GetUserProfileDirectory`），不读 `USERPROFILE`/`HOME`：
   测试和用户都可能改这些环境变量。
4. **POSIX 客户端统一做 broker 原有的检查**（socket 属主 + 0600 + 对端 uid）：service 客户端原来不检查，
   新增检查只会拒绝"不是本用户的服务端"，不改变任何原本能成功的连接。
5. **监听 socket 设 `SO_EXCLUSIVEADDRUSE`**：Windows 上不设时，其它进程可用 `SO_REUSEADDR` 抢占同一端口。
6. **握手失败计数暴露为 `IpcServer.auth_failures`**，日志每 60 s 至多一条，不含任何 token 或握手字节。
7. 测试临时目录：POSIX 继续用 `/tmp`（macOS `sockaddr_un` 长度限制），Windows 用默认 `%TEMP%`（在 profile 内）。
   `/tmp` 的取舍集中在 `tests/ipc_helpers.SHORT_TMP`。
