# Windows 适配 · 子项目 2a：阶段 A 共享平台改动 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 P2/P3 协作约定 §2 规定的三项共享平台改动（`procs.killpg` 墓碑、`files.is_reparse_point` /
`files.open_nofollow`、`requires_symlink` 测试标记），单独开 PR 先合入 `windows-compat-v2`，供 P2 后续计划和 P3 直接调用。

**Architecture:** 只改 `cc_remote/platform/procs.py`、`cc_remote/platform/files.py`、`tests/conftest.py`、`pytest.ini`
和对应的平台测试。POSIX 分支保持原生调用；Windows 分支用 ctypes/pywin32。三个接口的签名按协作约定原样实现，
不增减参数。

**Tech Stack:** Python 3.13（uv 管理的 `.venv`）、pywin32 312、ctypes、pytest 9。

**Spec:** `docs/superpowers/specs/2026-10-27-windows-compat-design.md`（§3、§3.1、§7）；
`docs/superpowers/plans/2026-10-27-windows-compat-p2-p3-coordination.md`（§2 为本计划的签名权威）；
`docs/superpowers/plans/2026-10-27-windows-compat-p1-handoff.md`（"必须先做"一节）。

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
- 本计划的三个接口签名以协作约定 §2 为准，执行中不得改名、加参数或改变异常类型。
- 平台层对外只抛 `OSError` 家族异常（`pywintypes.error` 不得漏出）。
- 文件归属：只改本计划"文件结构"表里列出的文件；`cc_remote/platform/ipc.py`、`cc_remote/platform/pty.py` 属于 P3，不能碰。
- 工作目录 `C:\Users\xuhaodong\work\cc-remote-p2`，分支 `win/2-wrapper`（PR 用独立分支 `win/2a-shared-platform`，见收尾任务）。
- Windows 上 Bash 用 Git Bash；命令里的 `.venv/Scripts/python.exe` 在 Linux CI 上对应 `.venv/bin/python`。

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `cc_remote/platform/procs.py` | 修改 | Windows 分支增加 `{pid: creation_time}` 墓碑；`killpg` 对墓碑 pid 抛 `ProcessLookupError`，不再 `taskkill` |
| `tests/test_platform_procs.py` | 修改 | 墓碑行为的回归测试 |
| `pytest.ini` | 修改 | 注册 `requires_symlink(reason)` 标记 |
| `tests/conftest.py` | 修改 | `requires_symlink` 的 reason 必填校验和"无法建符号链接时 skip" |
| `tests/test_platform_harness.py` | 修改 | 标记自检 |
| `cc_remote/platform/files.py` | 修改 | 新增 `is_reparse_point`、`open_nofollow`；抽出 Windows `CreateFileW` 公共实现 |
| `tests/test_platform_files.py` | 修改 | 两个新接口的测试 |

## 准备工作（执行者开始前做一次）

```bash
cd /c/Users/xuhaodong/work/cc-remote-p2
git fetch origin
git status --short --branch        # 期望：## win/2-wrapper，工作区干净
git switch -c win/2a-shared-platform origin/windows-compat-v2
PYTHONUTF8=1 .venv/Scripts/python.exe -c "import win32job, cc_remote.platform; print('ok')"
```

Expected: 最后一行输出 `ok`。

---

### Task 1: `procs.killpg` 墓碑（handoff "必须先做"）

基线：无对应 nodeid（这是 P1 最终评审遗留的安全缺陷）。验收：`tests/test_platform_procs.py` 在 Windows、Linux 上全部通过。

**Files:**
- Modify: `cc_remote/platform/procs.py`（Windows 分支：`_jobs` 定义处、`_prune_locked`、`_register`、`_discard`、`_adopt`、`killpg`）
- Test: `tests/test_platform_procs.py`

**Interfaces:**
- Consumes: `cc_remote.platform.win32_process.creation_time(pid: int) -> int | None`（P1 已有）。
- Produces（签名不变）：`procs.killpg(pid: int, sig: int) -> None`——对 Windows 上已被清理的 Job 的 pid 抛
  `ProcessLookupError(pid)`，绝不调用 `taskkill`。模块内部新增（仅测试使用）：
  `procs._tombstones: collections.OrderedDict[int, int | None]`、`procs._created: dict[int, int | None]`、
  `procs._TOMBSTONE_LIMIT: int = 4096`、`procs._bury_locked(pid: int) -> None`、
  `procs._register(pid: int, job: Any, created: int | None) -> None`（新增第三个参数）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_platform_procs.py` 末尾追加：

```python
def _wait_job_drained(pid: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with procs._jobs_lock:
            job = procs._jobs.get(pid)
            if job is None or procs._active_processes(job) == 0:
                return
        time.sleep(0.05)
    raise AssertionError("job never drained")


@pytest.mark.windows_only(reason="Job Object tombstones only exist on Windows")
def test_killpg_of_pruned_group_never_falls_back_to_taskkill(monkeypatch) -> None:
    first = procs.popen_group([sys.executable, "-c", "pass"])
    first.wait(timeout=10)
    _wait_job_drained(first.pid)
    # Spawning another group prunes the drained Job of ``first``.
    second = procs.popen_group([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert first.pid not in procs._jobs
        assert first.pid in procs._tombstones

        def no_taskkill(*args, **kwargs):
            raise AssertionError(f"taskkill must not run for a tombstoned pid: {args}")

        monkeypatch.setattr(procs.subprocess, "run", no_taskkill)
        with pytest.raises(ProcessLookupError):
            procs.killpg(first.pid, procs.SIGKILL)
    finally:
        try:
            procs.killpg(second.pid, procs.SIGKILL)
        except ProcessLookupError:
            pass
        second.wait(timeout=10)


@pytest.mark.windows_only(reason="Job Object tombstones only exist on Windows")
def test_killpg_drained_job_is_tombstoned() -> None:
    child = procs.popen_group([sys.executable, "-c", "pass"])
    child.wait(timeout=10)
    _wait_job_drained(child.pid)
    with pytest.raises(ProcessLookupError):
        procs.killpg(child.pid, procs.SIGTERM)  # drains the Job here
    assert child.pid not in procs._jobs
    assert child.pid in procs._tombstones
    with pytest.raises(ProcessLookupError):
        procs.killpg(child.pid, procs.SIGKILL)  # tombstone, not taskkill


@pytest.mark.windows_only(reason="Job Object tombstones only exist on Windows")
def test_register_clears_tombstone_of_reused_pid() -> None:
    import win32job

    fake_pid = 0x7FFFFFF0
    job = win32job.CreateJobObject(None, "")
    with procs._jobs_lock:
        procs._tombstones[fake_pid] = 1
    try:
        procs._register(fake_pid, job, 2)
        assert fake_pid not in procs._tombstones
        assert procs._jobs[fake_pid] is job
        assert procs._created[fake_pid] == 2
    finally:
        with procs._jobs_lock:
            procs._jobs.pop(fake_pid, None)
            procs._created.pop(fake_pid, None)
            procs._tombstones.pop(fake_pid, None)
        job.Close()


@pytest.mark.windows_only(reason="Job Object tombstones only exist on Windows")
def test_tombstones_are_bounded(monkeypatch) -> None:
    from collections import OrderedDict

    monkeypatch.setattr(procs, "_TOMBSTONE_LIMIT", 2)
    monkeypatch.setattr(procs, "_tombstones", OrderedDict())
    monkeypatch.setattr(procs, "_created", {1: 10, 2: 20, 3: 30})
    with procs._jobs_lock:
        for pid in (1, 2, 3):
            procs._bury_locked(pid)
    assert list(procs._tombstones.items()) == [(2, 20), (3, 30)]
    assert procs._created == {}
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_platform_procs.py -p no:xdist -o addopts="" -q`
Expected: 4 个新用例 FAIL（`AttributeError: module 'cc_remote.platform.procs' has no attribute '_tombstones'`
或 `_bury_locked`、`_register() takes 2 positional arguments`），原有用例 PASS。

- [ ] **Step 3: 实现**

`cc_remote/platform/procs.py` 顶部 import 区加一行（放在 `import threading` 之后）：

```python
from collections import OrderedDict
```

Windows 分支里，把

```python
    _jobs: dict[int, Any] = {}
    _jobs_lock = threading.Lock()
```

替换为：

```python
    _jobs: dict[int, Any] = {}
    _jobs_lock = threading.Lock()
    # Creation time of each tracked group leader, and the same for groups whose
    # Job already drained. A tombstoned pid answers ProcessLookupError forever
    # (until a new group reuses it): falling back to ``taskkill /T`` could hit
    # an unrelated tree that inherited the recycled pid.
    _created: dict[int, int | None] = {}
    _tombstones: OrderedDict[int, int | None] = OrderedDict()
    _TOMBSTONE_LIMIT = 4096

    def _bury_locked(pid: int) -> None:
        _tombstones[pid] = _created.pop(pid, None)
        _tombstones.move_to_end(pid)
        while len(_tombstones) > _TOMBSTONE_LIMIT:
            _tombstones.popitem(last=False)
```

把 `_prune_locked`、`_register`、`_discard` 替换为：

```python
    def _prune_locked(keep: int) -> None:
        for pid, job in list(_jobs.items()):
            if pid != keep and _active_processes(job) == 0:
                job.Close()
                del _jobs[pid]
                _bury_locked(pid)

    def _register(pid: int, job: Any, created: int | None) -> None:
        """Own ``job`` in the table first; pruning is housekeeping only."""
        with _jobs_lock:
            stale = _jobs.get(pid)
            _jobs[pid] = job
            _created[pid] = created
            _tombstones.pop(pid, None)
            if stale is not None:
                stale.Close()  # the pid was reused after an unpruned tree
            try:
                _prune_locked(pid)
            except Exception:  # never fail a spawn on cleanup
                _log.exception("job_prune_failed")

    def _discard(pid: int, job: Any) -> None:
        with _jobs_lock:
            if _jobs.get(pid) is job:
                del _jobs[pid]
                _bury_locked(pid)
        try:
            win32job.TerminateJobObject(job, 1)
        finally:
            job.Close()
```

`_adopt` 里把 `_register(pid, job)` 改为（`creation_time` 在恢复进程之前读取，此时子进程挂起、必然存活）：

```python
                try:
                    from cc_remote.platform.win32_process import creation_time

                    _register(pid, job, creation_time(pid))
                    if _ntdll.NtResumeProcess(int(process)) != 0:
                        raise OSError("cannot resume the suspended child process")
                except BaseException:
                    _discard(pid, job)
                    raise
```

把 Windows 分支的 `killpg` 整体替换为：

```python
    def killpg(pid: int, sig: int) -> None:
        """Terminate the Job for ``pid``; the Job stays tracked until it is empty.

        A SIGTERM-then-SIGKILL escalation therefore reaches the same Job (or
        ProcessLookupError once it drained), and a pid whose Job was pruned is
        tombstoned: it never reaches ``taskkill``, which could kill whatever
        unrelated tree inherited the recycled pid.
        """
        del sig  # Windows has no deliverable graceful signal for console-less trees
        with _jobs_lock:
            job = _jobs.get(pid)
            if job is not None:
                with translated():
                    if _active_processes(job) == 0:
                        del _jobs[pid]
                        job.Close()
                        _bury_locked(pid)
                        raise ProcessLookupError(pid)
                    win32job.TerminateJobObject(job, 1)
                return
            if pid in _tombstones:
                raise ProcessLookupError(pid)
        result = subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True, check=False)
        if result.returncode == 128:
            raise ProcessLookupError(pid)
        if result.returncode != 0:
            raise OSError(f"taskkill failed for pid {pid}: {result.returncode}")
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_platform_procs.py tests/test_platform_win32_process.py -p no:xdist -o addopts="" -q`
Expected: 全部 PASS（`test_platform_procs.py` 比改动前多 4 个通过）。

- [ ] **Step 5: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote/platform/procs.py tests/test_platform_procs.py
git add cc_remote/platform/procs.py tests/test_platform_procs.py
git commit -F - <<'EOF'
fix(platform): tombstone drained Job pids so killpg never taskkills them

- Record each group leader's creation time and keep a bounded tombstone when its Job is pruned, drained or discarded
- killpg raises ProcessLookupError for tombstoned pids instead of running taskkill /T on a possibly recycled pid

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

Expected: ruff 输出 `All checks passed!`；`git log -1 --stat` 只含这两个文件。

---

### Task 2: `requires_symlink` 测试标记

基线：本任务不直接修基线用例；它是 P2b～P2d 处理 `symlink_privilege`（owner 2 共 93 条）用的统一标记。
验收：`tests/test_platform_harness.py` 全部通过；Windows 未开开发者模式时新自检用例显示为 skip 且带原因。

**Files:**
- Modify: `pytest.ini`（`markers =` 段）
- Modify: `tests/conftest.py`（`pytest_collection_modifyitems` 及新 helper）
- Test: `tests/test_platform_harness.py`

**Interfaces:**
- Produces: pytest 标记 `@pytest.mark.requires_symlink(reason="…")`。语义：POSIX 上恒执行；Windows 上当前进程不能
  `os.symlink`（未开开发者模式且非管理员）时 skip，skip 原因为
  `requires_symlink: <reason> (<探测到的错误>)`。缺少 `reason=` 时收集阶段报 `pytest.UsageError`，与
  `posix_only`/`windows_only` 一致。conftest 内部 helper：`_symlink_unavailable() -> str | None`（返回不可用原因，
  可用时返回 None，结果缓存）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_platform_harness.py` 的 `test_windows_only_marker_skips_on_posix` 之后插入：

```python
@pytest.mark.requires_symlink(reason="harness self-check: creates a symbolic link")
def test_requires_symlink_marker_runs_only_when_links_work(tmp_path) -> None:
    target = tmp_path / "target"
    target.write_text("x")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert link.is_symlink()


def test_requires_symlink_marker_needs_a_reason() -> None:
    from tests import conftest

    class _Item:
        nodeid = "tests/test_x.py::test_x"

        def get_closest_marker(self, name):
            if name == "requires_symlink":
                return pytest.mark.requires_symlink().mark
            return None

        def add_marker(self, marker):
            raise AssertionError("a reason-less marker must fail before skipping")

    with pytest.raises(pytest.UsageError, match="@requires_symlink requires reason="):
        conftest.pytest_collection_modifyitems(None, [_Item()])
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_platform_harness.py -p no:xdist -o addopts="--strict-markers" -q`
Expected: 收集失败 `'requires_symlink' not found in \`markers\` configuration option`。

- [ ] **Step 3: 实现**

`pytest.ini` 的 `markers =` 段末尾追加一行：

```ini
    requires_symlink(reason): needs permission to create symbolic links; skipped on Windows without it (reason is required)
```

`tests/conftest.py`：import 区补 `import functools` 和 `import tempfile`；在 `pytest_configure` 之前加入：

```python
@functools.cache
def _symlink_unavailable() -> str | None:
    """Why this process cannot create symbolic links, or None when it can.

    Windows needs Developer Mode or an elevated token (WinError 1314
    otherwise). POSIX always can.
    """
    if sys.platform != "win32":
        return None
    with tempfile.TemporaryDirectory() as directory:
        target = os.path.join(directory, "target")
        with open(target, "w", encoding="utf-8"):
            pass
        try:
            os.symlink(target, os.path.join(directory, "link"))
        except OSError as exc:
            return f"cannot create symbolic links: {exc.strerror or exc}"
    return None
```

把 `pytest_collection_modifyitems` 整体替换为：

```python
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item],
) -> None:
    excluded = "posix_only" if sys.platform == "win32" else "windows_only"
    for item in items:
        for name in ("posix_only", "windows_only", "requires_symlink"):
            marker = item.get_closest_marker(name)
            if marker is not None and not marker.kwargs.get("reason"):
                raise pytest.UsageError(f"{item.nodeid}: @{name} requires reason=")
        marker = item.get_closest_marker(excluded)
        if marker is not None:
            item.add_marker(pytest.mark.skip(
                reason=f"{excluded}: {marker.kwargs['reason']}"))
        marker = item.get_closest_marker("requires_symlink")
        if marker is not None:
            unavailable = _symlink_unavailable()
            if unavailable is not None:
                item.add_marker(pytest.mark.skip(
                    reason=f"requires_symlink: {marker.kwargs['reason']} ({unavailable})"))
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_platform_harness.py -p no:xdist -o addopts="--strict-markers" -q -rs`
Expected: 全部 PASS 或 SKIPPED；Windows 未开开发者模式时 `-rs` 摘要里出现
`requires_symlink: harness self-check: creates a symbolic link (cannot create symbolic links: …)`。

- [ ] **Step 5: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check tests/conftest.py tests/test_platform_harness.py
git add pytest.ini tests/conftest.py tests/test_platform_harness.py
git commit -F - <<'EOF'
test(platform): add requires_symlink marker for link-dependent fixtures

- Skip marked tests on Windows when the process cannot create symbolic links, with the probe error in the reason
- Require reason= for requires_symlink like posix_only and windows_only

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3: `files.is_reparse_point` 与 `files.open_nofollow`

基线：本任务提供接口，消费方在 P2b（`posix_open_flags`，owner 2 共 196 条）和 P3。验收：`tests/test_platform_files.py`
在 Windows、Linux 上全部通过。

**Files:**
- Modify: `cc_remote/platform/files.py`（新增 `is_reparse_point`、`open_nofollow`、Windows 私有 helper
  `_kernel32()`、`_win32_create(...)`；`open_with_share_delete` 改为调用 `_win32_create`，行为不变）
- Test: `tests/test_platform_files.py`

**Interfaces:**
- Produces（签名按协作约定 §2 原样）：
  - `files.is_reparse_point(path: StrPath) -> bool`——POSIX：`stat.S_ISLNK(os.lstat(path).st_mode)`（等价于
    `os.path.islink`，但不存在时抛 `FileNotFoundError`）；Windows：`os.lstat(path).st_file_attributes &
    FILE_ATTRIBUTE_REPARSE_POINT`（symlink、junction、AF_UNIX socket 等任何 reparse point 都为 True）。
  - `files.open_nofollow(path: StrPath, flags: int, mode: int = 0o600) -> int`——POSIX：
    `os.open(path, flags | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), mode)`；Windows：带
    `FILE_FLAG_OPEN_REPARSE_POINT` 打开，若打开的对象是 reparse point 则关闭句柄并抛
    `OSError(errno.ELOOP, ...)`；其余语义同 `open_with_share_delete`（share-delete、二进制、`mode` 在 Windows 上忽略）。
    只用于普通文件；目录请用 P2c 的 `DirHandle`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_platform_files.py` 顶部 import 区加入 `import errno`、`import sys`，然后在文件末尾追加：

```python
def _link_directory(target, link) -> None:
    """A directory link every platform can create without privileges."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_is_reparse_point_regular_file_and_directory(tmp_path) -> None:
    regular = tmp_path / "regular"
    regular.write_bytes(b"x")
    assert files.is_reparse_point(regular) is False
    assert files.is_reparse_point(tmp_path) is False


def test_is_reparse_point_directory_link(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    _link_directory(target, link)
    assert files.is_reparse_point(link) is True


@pytest.mark.requires_symlink(reason="checks a file symbolic link")
def test_is_reparse_point_file_symlink(tmp_path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"x")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert files.is_reparse_point(link) is True


def test_is_reparse_point_missing_path_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        files.is_reparse_point(tmp_path / "missing")


def test_open_nofollow_reads_and_creates_regular_files(tmp_path) -> None:
    path = tmp_path / "f"
    fd = files.open_nofollow(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        os.write(fd, b"a\nb\x1ac")
    finally:
        os.close(fd)
    fd = files.open_nofollow(path, os.O_RDONLY)
    try:
        assert os.read(fd, 16) == b"a\nb\x1ac"
        assert stat.S_ISREG(os.fstat(fd).st_mode)
    finally:
        os.close(fd)


def test_open_nofollow_keeps_share_delete(tmp_path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"old")
    fd = files.open_nofollow(path, os.O_RDONLY)
    try:
        replacement = tmp_path / "new"
        replacement.write_bytes(b"new")
        files.replace(replacement, path)
        assert path.read_bytes() == b"new"
    finally:
        os.close(fd)


def test_open_nofollow_exclusive_create_rejects_existing(tmp_path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x")
    with pytest.raises(FileExistsError):
        files.open_nofollow(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)


def test_open_nofollow_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        files.open_nofollow(tmp_path / "missing", os.O_RDONLY)


@pytest.mark.requires_symlink(reason="opens through a file symbolic link")
def test_open_nofollow_refuses_file_symlink(tmp_path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"secret")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OSError) as caught:
        files.open_nofollow(link, os.O_RDONLY)
    assert caught.value.errno == errno.ELOOP


@pytest.mark.requires_symlink(reason="creates through a dangling symbolic link")
def test_open_nofollow_refuses_to_create_through_dangling_symlink(tmp_path) -> None:
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(OSError) as caught:
        files.open_nofollow(link, os.O_WRONLY | os.O_CREAT)
    assert caught.value.errno in (errno.ELOOP, errno.EEXIST)
    assert not (tmp_path / "elsewhere").exists()


@pytest.mark.windows_only(reason="AF_UNIX socket files are reparse points only on Windows")
def test_open_nofollow_refuses_windows_af_unix_socket_file(tmp_path) -> None:
    import ctypes

    ws2 = ctypes.WinDLL("ws2_32")

    class _WsaData(ctypes.Structure):
        _fields_ = [("data", ctypes.c_byte * 512)]

    class _SockaddrUn(ctypes.Structure):
        _fields_ = [("family", ctypes.c_ushort), ("path", ctypes.c_char * 108)]

    assert ws2.WSAStartup(0x0202, ctypes.byref(_WsaData())) == 0
    ws2.socket.restype = ctypes.c_size_t
    ws2.bind.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
    ws2.closesocket.argtypes = [ctypes.c_size_t]
    sock = ws2.socket(1, 1, 0)  # AF_UNIX, SOCK_STREAM
    path = tmp_path / "s.sock"
    address = _SockaddrUn(1, os.fsencode(path))
    try:
        assert ws2.bind(sock, ctypes.byref(address), ctypes.sizeof(address)) == 0
        assert files.is_reparse_point(path) is True
        with pytest.raises(OSError) as caught:
            files.open_nofollow(path, os.O_RDONLY)
        assert caught.value.errno == errno.ELOOP
    finally:
        ws2.closesocket(sock)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_platform_files.py -p no:xdist -o addopts="" -q`
Expected: 新用例 FAIL：`AttributeError: module 'cc_remote.platform.files' has no attribute 'is_reparse_point'`
（以及 `open_nofollow`）；需要符号链接的 4 个用例在未开开发者模式的 Windows 上 SKIPPED。

- [ ] **Step 3: 实现**

`cc_remote/platform/files.py` import 区加入 `import errno` 和 `import functools`。在 `open_with_share_delete`
之前加入 Windows 公共实现，并把 `open_with_share_delete` 改为调用它：

```python
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_ATTRIBUTE_TAG_INFO = 9


@functools.cache
def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


def _win32_handle_attributes(handle: int) -> int:
    import ctypes
    from ctypes import wintypes

    class _AttributeTagInfo(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    info = _AttributeTagInfo()
    if not _kernel32().GetFileInformationByHandleEx(
            handle, _FILE_ATTRIBUTE_TAG_INFO, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(info.FileAttributes)


def _win32_create(path: StrPath, flags: int, *, refuse_reparse: bool) -> int:
    """CreateFileW with POSIX open() dispositions and FILE_SHARE_DELETE."""
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
    attributes = file_attribute_normal
    if refuse_reparse:
        attributes |= _FILE_FLAG_OPEN_REPARSE_POINT

    kernel32 = _kernel32()
    handle = kernel32.CreateFileW(
        os.fspath(path), access, share_all, None, disposition, attributes, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if refuse_reparse and _win32_handle_attributes(handle) & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(errno.ELOOP, "refusing to open a reparse point", os.fspath(path))
        return msvcrt.open_osfhandle(
            handle, access_flags | os.O_BINARY | (flags & os.O_APPEND))
    except BaseException:
        kernel32.CloseHandle(wintypes.HANDLE(handle))
        raise


def open_with_share_delete(path: StrPath, flags: int, mode: int = 0o600) -> int:
    """``os.open`` whose descriptor never blocks unlink/replace of ``path``.

    ``mode`` is ignored on Windows: a created file inherits its directory's
    ACL. Use :func:`cc_remote.platform.perms.make_private` to restrict it.
    """
    if not IS_WINDOWS:
        return os.open(path, flags, mode)
    return _win32_create(path, flags, refuse_reparse=False)


def is_reparse_point(path: StrPath) -> bool:
    """True when ``path`` itself is a link (POSIX) or any reparse point (Windows).

    Windows counts symlinks, junctions, AF_UNIX socket files and every other
    reparse tag. Raises FileNotFoundError when ``path`` does not exist.
    """
    info = os.lstat(path)
    if not IS_WINDOWS:
        return stat.S_ISLNK(info.st_mode)
    return bool(info.st_file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def open_nofollow(path: StrPath, flags: int, mode: int = 0o600) -> int:
    """Open a regular file without following a final link.

    POSIX adds ``O_NOFOLLOW | O_CLOEXEC``. Windows opens the reparse point
    itself and refuses it with ``OSError(ELOOP)``; otherwise the descriptor is
    the share-delete, binary one of :func:`open_with_share_delete` and
    ``mode`` is ignored. Directories are not supported on Windows.
    """
    if not IS_WINDOWS:
        return os.open(path, flags | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), mode)
    return _win32_create(path, flags, refuse_reparse=True)
```

删除旧 `open_with_share_delete` 函数体中的 `import ctypes`…`kernel32.CloseHandle(...)`（已被 `_win32_create` 取代）。

- [ ] **Step 4: 运行测试，确认通过**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_platform_files.py tests/test_platform_locks.py tests/test_markdown_preview.py -p no:xdist -o addopts="" -q -rs`
Expected: `test_platform_files.py`、`test_platform_locks.py` 全部 PASS（需要符号链接的 4 个可为 SKIPPED，原因含
`requires_symlink`）；`test_markdown_preview.py` 的失败数不多于基线的 54 条（本任务不改它，只确认没有新失败）。

- [ ] **Step 5: lint 并提交**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote/platform/files.py tests/test_platform_files.py
git add cc_remote/platform/files.py tests/test_platform_files.py
git commit -F - <<'EOF'
feat(platform): add reparse-point checks and no-follow file opens

- is_reparse_point reports links on POSIX and any reparse point on Windows, raising FileNotFoundError for missing paths
- open_nofollow adds O_NOFOLLOW|O_CLOEXEC on POSIX and refuses reparse points with ELOOP on Windows
- Share one CreateFileW helper between open_with_share_delete and open_nofollow

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: 阶段 A 收尾：Linux 门禁、推送与 PR

**Files:** 无代码改动。

- [ ] **Step 1: Windows 上复跑受影响文件**

```bash
for f in tests/test_platform_procs.py tests/test_platform_files.py tests/test_platform_harness.py \
         tests/test_platform_locks.py tests/test_platform_perms.py tests/test_platform_win32_process.py; do
  PYTHONUTF8=1 timeout 240 .venv/Scripts/python.exe -m pytest "$f" -p no:xdist -o addopts="--strict-markers" -q || echo "FAIL $f"
done
```

Expected: 没有 `FAIL` 行。

- [ ] **Step 2: 本地完整门禁中可在 Windows 上执行的部分**

```bash
uvx --from ruff==0.15.13 ruff check cc_remote tests deploy
git diff origin/windows-compat-v2 --check
```

Expected: ruff `All checks passed!`；`git diff --check` 无输出。Linux 专属的 pytest 全量和 shellcheck 由 PR 的
Linux CI 执行（CLAUDE.md 门禁里 Windows 不能执行的项在 PR 描述中如实列出）。

- [ ] **Step 3: 推送并开 PR**

```bash
git push -u origin win/2a-shared-platform
gh pr create --repo donny-star/cc-remote --base windows-compat-v2 --head win/2a-shared-platform \
  --title "Windows P2 phase A: shared platform helpers" --body-file - <<'EOF'
Phase A of sub-project 2 (coordination doc §2), merged before P2b–P2d and P3 rebase:

- procs.killpg tombstones drained Job pids and never taskkills them
- files.is_reparse_point / files.open_nofollow with the agreed signatures
- requires_symlink(reason=...) pytest marker

Checks: Windows per-file pytest for tests/test_platform_*.py, ruff, git diff --check. Linux pytest runs in CI.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

Expected: 输出 PR URL；Linux CI 通过后由 controller 合并。合并后通知 P3：可以 rebase。
