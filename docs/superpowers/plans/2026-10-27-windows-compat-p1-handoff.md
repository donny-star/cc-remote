# P1 交接说明（平台兼容层与测试基线）

日期：2026-10-27　合并：PR #1 → `windows-compat-v2`（`51bcda9`）
来源：P1 执行过程中的裁定记录（SDD ledger）与最终全分支评审。写 P2～P5 计划前请先读本文。

## 必须先做（P2 第 1 个任务，迁移任何 killpg 调用之前）

- **`procs.killpg` 升级终止的残留漏洞**：另一次 `popen_group` 触发的 `_prune_locked`
  可能先把已经跑完的 Job 清掉，之后对同一个 pid 的 `killpg` 会走到 `taskkill /T /F`。
  如果这时 pid 已被系统复用，就会杀掉一棵无关的进程树。
  修法：清理 Job 时保留墓碑 `{pid: creation_time}`，对墓碑里的 pid 调用 `killpg` 时
  抛 `ProcessLookupError`；或者在 `taskkill` 之前先校验 `ProcessIdentity`。
  目前生产代码还没有调用 `procs.killpg`，所以合并 P1 是安全的。

## 写 P2～P5 计划之前

- 本地 `.venv` 是按未锁版本的 `requirements-dev.txt` 装的（`mcp 2.2.0`，lock 里是 `1.28.1`）。
  基线里 62 条 `Server.list_tools` 失败是这个原因造成的。先用 lock 文件重建 venv，再刷新这些条目。
- 297 条 WinError 1314（创建符号链接需要开发者模式）：P2/P5 要统一决定是跳过、改代码，
  还是把开发者模式列为环境要求，并共用一个 helper。
- Windows 上不要把整个测试套件放在一个 pytest 进程里跑（约 69% 时会卡住：
  `test_claude_autocompact`、`test_codex_controls`、`test_codex_session_delete`）。
  按文件逐个跑，每个文件 240 s 超时。
- 测试命令用 `PYTHONUTF8=1 python -m pytest …`（conftest 会把 UTF-8 模式传给 xdist worker）。

## 平台层 API 的约定（下游直接用，不要另写 `IS_WINDOWS` 分支）

- 异常：平台层对外只抛 `OSError` 家族（`BlockingIOError`、`ProcessLookupError`、
  `FileNotFoundError`、`PermissionError`），`pywintypes.error` 不会漏出来。
- 私有/属主校验：
  - 按路径用 `perms.is_private(path)`，按 fd 用 `perms.is_private_fd(fd)`；
  - 属主校验用 `perms.owned_by_current_user(fd|path)`；
  - 这些校验都是 fail-closed（出任何异常都返回 False）。
  - Windows 上 `current_uid()` 是 0，所有 `st_uid` 也是 0，所以 `st_uid != current_uid()`
    这类判断在 Windows 上恒为假，必须换成上面这些 API。
  - Windows 上不要把 `current_euid() == 0` 当成"root"。
- 锁：没有 `LOCK_SH`；同一个句柄重复加 `LOCK_EX` 会死锁；Windows 上锁属于加锁的那个进程，
  子进程拿到继承来的 fd 也不能复核或接管父进程的锁。这一点影响
  `deploy/install_lock.verify_install_lock`，以及 `async_tasks`/`timed_tasks` 里用 `pass_fds`
  交接锁的写法。
- 进程：
  - Windows 上 `killpg(SIGTERM)` 等同于立即强杀。需要优雅退出时，先走引擎自己的协议
    （关闭 stdin 或发 shutdown RPC），再调 `killpg`。
  - `killpg` 本身不校验进程身份，调用方要在调用前比对 `ProcessIdentity`。
  - `taskkill` 回退是阻塞调用，在异步代码里要放进 `asyncio.to_thread`。
  - wrapper 重启后 Job 表是空的，`taskkill /T` 够不到已经变成孤儿的孙进程。
- `rlimit_exec.py` 在 Windows 上会直接退出：codex 的启动路径（`codex_handle.py`、
  `codex_daemon.py`、`codex_rpc.py`）在 Windows 上不能用它包一层。
- `O_NOFOLLOW`/`O_DIRECTORY` 在 Windows 上取值为 0，`dir_fd` 也不支持。防符号链接、
  openat 风格的防护需要显式检查 reparse point，平台层目前还没有这个 helper。
- 目录私有不能保护目录里的文件：每个密钥文件要单独设置权限，或者在 `make_private`
  过的目录里新建（这样会继承 ACL）。从 `%TEMP%` 重命名进来的文件会保留它原来的 ACL。
- 以管理员权限运行的同用户进程（比如在管理员终端里启动的 claude）可能无法用
  `OpenProcess(QUERY_LIMITED|SYNCHRONIZE)` 打开，这时 `process_identity` 返回 None，
  外部占用检测会失效放行（fail-open）。
- `pass_fds` 在 Windows 上还没有对应实现：P4（`async_tasks.py`）和 `codex_desktop.py`
  需要在 `procs` 里补一个"继承句柄列表"的 helper。
- 不要写 `from cc_remote import platform`（会和标准库 `platform` 混淆），也不要把
  `cc_remote/` 目录本身加到 `sys.path`。

## 已推迟的小问题

- `tests/test_markdown_preview.py:1620`：竞态测试在 Windows 上吞掉了所有 `OSError`，
  应收窄为只吞 winerror 32/5（P2）。
- `tests/test_markdown_preview.py:1197`：遇到 WinError 1314 时在运行中直接 skip，
  归到上面符号链接的统一决定里处理。
- `update_relay.py:188`：为了拿文件路径而 import 一个 POSIX-only 模块，改用
  `importlib.util.find_spec(...).origin`（P5）。
- `requirements.lock` 没有直接包含 pywin32（它是经 mcp 间接引入的）：P5 写安装器时复查。
- 没有测试覆盖 Win32 非竞争类错误的异常转换（可以和 `_win32err` 的测试合并补上）。
