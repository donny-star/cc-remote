# P2 / P3 并行协作约定（controller 裁定，2026-10-27）

P2（Wrapper 核心）和 P3（IPC + Claude service/broker + ConPTY）的计划同时编写，实现时也并行进行。
为避免冲突，两份计划都必须遵守本文。

## 1. 文件归属

| 区域 | 归属 | 另一方能否修改 |
|---|---|---|
| `cc_remote/platform/` 下已有的文件（`__init__`、`locks`、`files`、`identity`、`perms`、`win32_process`、`procs`、`executables`、`paths`、`signals`、`_win32err`） | P2 | P3 不能改 |
| 新文件 `cc_remote/platform/ipc.py`、`cc_remote/platform/pty.py`（及其测试） | P3 | P2 不能改 |
| `tests/conftest.py`、`pytest.ini` | P2（只在阶段 A 改） | P3 不能改 |
| `cc_remote/wrapper/machine.py` | P2 | P3 不能改；P3 需要 machine 侧配合时，在计划里写成"需要 P2 配合"的接口说明，不直接修改 |
| `cc_remote/claude_service/**`、`cc_remote/claude_broker/**`、`cc_remote/claude_remote*`、`cc_remote/wrapper/claude_broker_handle.py` | P3 | P2 不能改 |
| `cc_remote/session_messages.py`、`cc_remote/wrapper/session_messages.py`、TUI、viewer、timed/async tasks | P4（以后） | P2、P3 都不改 |
| `deploy/**`、`cc_remote/update*.py` | P5（以后） | P2、P3 都不改 |
| 其余 `cc_remote/wrapper/**`、`cc_remote/config.py`、`cc_remote/codex_*.py` 等 | P2 | P3 不能改 |

计划写完后，如果确实还需要碰另一方的文件，在计划末尾"跨子项目依赖"一节列出，由 controller 统一裁定。

## 2. P2 阶段 A：共享平台改动（P2 计划的前几个任务，单独开 PR，先合并）

签名在这里预先定死，P3 可以直接按这些签名调用：

1. `procs.killpg` 墓碑修复（见 `2026-10-27-windows-compat-p1-handoff.md`）：Job 被清理后，
   在模块级保留 `{pid: creation_time}` 墓碑；对墓碑中的 pid 调用 `killpg` 时抛
   `ProcessLookupError`，绝不再退到 `taskkill`。接口签名不变。
2. `cc_remote/platform/files.py` 新增：
   - `is_reparse_point(path) -> bool`
     - POSIX：等价于 `os.path.islink(path)`；
     - Windows：只要是 symlink、junction 或任何 reparse point，就返回 True；
     - 路径不存在时抛 `FileNotFoundError`。
   - `open_nofollow(path, flags: int, mode: int = 0o600) -> int`
     - POSIX：`os.open(path, flags | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), mode)`；
     - Windows：打开时带 `FILE_FLAG_OPEN_REPARSE_POINT`。如果打开的对象是 reparse point，
       先关闭句柄，再抛 `OSError(errno.ELOOP, ...)`；
     - 其余语义与 `open_with_share_delete` 一致（share-delete、二进制模式）。
3. `tests/conftest.py` 新增标记 `requires_symlink(reason=...)`：当前进程无法创建符号链接时
   （Windows 未开开发者模式、也不是管理员），该测试 skip 并给出原因；POSIX 上恒执行。
   同时把 `windows_only`/`posix_only` 的 reason 必填校验扩展到这个新标记。
   ——裁定：测试夹具里需要创建 symlink 的用例一律用这个标记；产品代码中运行时会创建
   symlink 的路径，必须给出 Windows 替代方案（例如 junction 或复制），不能依赖开发者模式。

阶段 A 合入 `windows-compat-v2` 之后，P2 的后续任务和 P3 的实现都从新的
`windows-compat-v2` rebase。

## 3. 共同的全局约束（两份计划的 Global Constraints 都要逐字包含）

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
