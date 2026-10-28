# Windows 适配设计（windows-compat-v2）

日期：2026-10-27
目标仓库：`donny-star/cc-remote`（fork，不向上游 `muggle-stack/cc-remote` 提 PR）
集成分支：`windows-compat-v2`，基于 `upstream/master`（v5.3.0，`7fe90f5`）

## 1. 背景与目标

旧分支 `windows-compat` 基于 `41434f1`，比上游落后 388 个提交，只有 4 个 Windows
提交，主要改动在 `wrapper/machine.py`、`os_compat.py`、`file_lock_compat.py`、
`codex_external.py`。在这期间上游新增了 Claude service、Claude broker（PTY）、TUI、
viewer、session_messages、timed/async tasks 等子系统，它们都依赖 POSIX 专有能力：
`fcntl` 用在 15 个文件里，`killpg`/`setsid` 用在 11 个，AF_UNIX 用在 5 个组件里，
`pty`/`termios` 用在 2 个。

目标：在 Windows 10/11 上跑通下列组件。

- Wrapper 核心（驱动本机 claude 和 codex、进程扫描、接管、锁）
- Claude service 和 Claude broker
- TUI 客户端、viewer、session_messages
- 安装和部署（安装、自启、升级、回滚、清理）

非目标：relay 服务端在 Windows 上的部署（setup-vps、Docker、Caddy）、macOS Codex
App 接入（`codex_desktop`、`desktop_remote_ui`）、多用户和 supervisor 安装选项、
向上游提交。

约束：
- POSIX 下的行为必须保持不变。平台差异都收进兼容层，业务代码改动越少越好，这样
  以后同步上游时冲突才少。
- Python 跟随上游，固定为 3.13.9（由 uv 管理）。Windows 上的 CPython 没有
  `socket.AF_UNIX`，也没有 `asyncio.open_unix_connection`。
- 本机 claude（2.1.278）和 codex（0.164.0）都是 npm 安装的 `.cmd` shim。

## 2. 总体做法

基于最新上游重新适配（方案 A）。旧分支的兼容代码和已验证的修复手动移植过来，
不 rebase、也不 merge 旧分支。全部完成后，旧分支打 tag `windows-compat-legacy`
存档，再用 `windows-compat-v2` 覆盖 `windows-compat`（强推前要再次征得用户确认）。

## 3. 兼容层 `cc_remote/platform/`

业务代码只调用这里的函数，不再新增散落的 `sys.platform` 分支。

| 模块 | POSIX | Windows |
|---|---|---|
| `locks.py` | `fcntl.flock`（支持 LOCK_NB） | `msvcrt.locking`，移植自旧分支的 `file_lock_compat`，LOCK_NB 只尝试一次 `LK_NBLCK` |
| `files.py` | `os.open` / `os.replace` | 打开时带 `FILE_SHARE_DELETE`（移植 `open_with_share_delete`）；`replace` 遇到短暂的 `PermissionError` 时有限次退避重试 |
| `perms.py` | `chmod 0o600/0o700`、属主和权限位校验 | 用 pywin32 给当前用户设独占 DACL，校验时检查 ACL 没有对其他主体开放；无法设置时抛 `OSError`（见 §3.1，不再降级为 no-op） |
| `identity.py` | `getuid` / `pwd` | 当前用户 SID 加 `USERNAME`；凡是比对 uid 的地方都改用 `current_identity()` |
| `procs.py` | `start_new_session` + `killpg` | `CREATE_NEW_PROCESS_GROUP` 加 Job Object（`KILL_ON_JOB_CLOSE`）；先发 `CTRL_BREAK_EVENT` 温和终止，宽限期过后用 `TerminateJobObject` 强杀 |
| `proc_scan.py` | `/proc`、`ps`、`lsof` | EnumProcesses、Toolhelp32（父子链）、PEB 读命令行，移植自旧分支；提供统一的 `ProcessIdentity(pid, start_time)`，防止 PID 复用后误杀 |
| `executables.py` | `~/.local/bin/claude` 等 | 依次查找 `~/.local/bin/claude.exe` 和 `PATH` 里的 `.exe`/`.cmd`；`.cmd` 形式的 npm shim 解析成 `node <入口.js>`，中间不经过 `cmd.exe` |
| `signals.py` | SIGTERM/SIGINT 处理器 | SIGINT、SIGBREAK，以及 `SetConsoleCtrlHandler`（关闭、注销事件） |
| `paths.py` | `.venv/bin/python` | `venv_python(root)` → `.venv\Scripts\python.exe`；`venv_pythonw(root)` |

进程管理语义：
- 所有 `killpg`、`setsid`、`start_new_session` 的调用点都改成
  `procs.spawn_group()` / `procs.terminate_group(handle, grace=…)`。
- 在 Windows 上，Job Object 会连同 claude 和 codex 启动的工具子进程一起终止，
  这和 POSIX 下 `killpg` 的行为一致（已确认）。
- 用到 `pass_fds` 的地方（`async_tasks`、`codex_desktop`），Windows 下改成可继承的
  管道句柄，把句柄值通过参数传给子进程。
- 发信号前先校验 `ProcessIdentity`：拿 pid 加 start_time 比对，不一致就拒绝操作。

### 3.1 实施前验证带来的修订（2026-10-27）

下面几条是写计划时在本机实测后的修订，与上表冲突的，以这里为准：

- **锁**：不用旧分支的 `msvcrt.locking`（锁第 0 字节）。Windows 的字节锁是强制锁，
  会让其他句柄读不到被锁的数据。改用 `win32file.LockFileEx` 锁定偏移量
  `0x7FFFFFFF_FFFFFFFE` 处的 1 个字节。实测：冲突时报 `ERROR_LOCK_VIOLATION`
  （33）；锁住期间其他句柄仍能正常读数据；阻塞等待由系统原生支持，关闭句柄即
  释放锁。只提供 `LOCK_EX`、`LOCK_UN`、`LOCK_NB`，因为上游没用到 `LOCK_SH`。
- **二进制 fd**：Windows 上 `os.open` 默认以文本模式打开，实测 `\n` 会被改写成
  `\r\n`，读到 `\x1a` 就截断。上游有 138 处 `os.open` 都会受影响。处理办法：
  `cc_remote.platform` 被导入时调用 `ucrtbase._set_fmode(_O_BINARY)`（实测有效），
  把整个进程的默认模式改成二进制，不再逐个修改调用点。
- **UTF-8 模式**：Windows 进程一律以 `PYTHONUTF8=1` 运行，包括启动器、计划任务和
  测试。conftest 在 Windows 上检测到不是 UTF-8 模式时会直接报错退出。
- **Job Object**：先启动子进程再加入 Job 存在竞态。实测 venv 的 `python.exe` 会
  立刻拉起真正的解释器，这个子进程会漏到 Job 外面。改为以 `CREATE_SUSPENDED` 启动，
  加入 Job 后再调用 `NtResumeProcess` 恢复运行。实测这样能把整棵进程树（4 个进程）
  全部纳入 Job，并一次终止干净。
- **不设置 `KILL_ON_JOB_CLOSE`**：上游部分子进程本来就要比父进程活得久
  （`start_new_session` 的语义），所以 Job 只用来终止整棵进程树。Job 句柄在进程
  树全部退出后自动回收。
- **Windows 上 `SIGTERM` 和 `SIGKILL` 都映射为 `TerminateJobObject`**：服务模式下
  没有控制台，`CTRL_BREAK_EVENT` 发不到子进程。不由 `popen_group` 启动的进程，
  退回用 `taskkill /T /F` 终止。
- **wrapper 本身已经在某个 Job 里**（实测当前环境就是这样）：Windows 8 以后支持
  嵌套 Job，不需要 `CREATE_BREAKAWAY_FROM_JOB`。§9 的对应风险作废。
- **进程扫描**：底层的 Win32 原语放在 `cc_remote/platform/win32_process.py`，由
  现有的 `cc_remote/wrapper/process_scan.py` 按平台分派调用，沿用它现有的 Darwin
  后端模式。`ProcessIdentity.start_ticks` 在 Windows 上取进程创建时间（FILETIME，
  单位 100ns）。命令行通过 `NtQueryInformationProcess(ProcessCommandLineInformation)`
  读取，实测 Unicode 参数无误。
- **进程属主**：Windows 上 `current_uid()` 返回 0，和 `os.stat().st_uid` 的占位值
  一致。`process_owner_uid()` 在属主 SID 与当前用户相同时返回 0，不同时返回
  `FOREIGN_UID`。
- **私有权限判定**：所有生效的 ACCESS_ALLOWED ACE 的 SID 都必须属于
  {当前用户、SYSTEM、Administrators、OWNER RIGHTS}，并且属主是当前用户或
  Administrators。`%LOCALAPPDATA%`、`~/.claude` 的默认 ACL 实测都满足；`%TEMP%`
  下 `mkdtemp` 建出的目录只带 OWNER RIGHTS，按上面的规则也判定为私有。
- **替换文件**：实测表明，只要目标文件还有句柄开着（即使带了 FILE_SHARE_DELETE），
  `os.replace`（MoveFileEx）就会返回拒绝访问，只有 unlink 能成功。因此
  `files.replace` 在 Windows 上改用 `SetFileInformationByHandle(FileRenameInfoEx,
  REPLACE_IF_EXISTS | POSIX_SEMANTICS)`。实测可以替换以 share-delete 打开着的目标，
  旧句柄读到的仍是旧内容，与 POSIX 的 inode 语义一致。如果占用方没给 share-delete，
  会报错误码 32，此时退避重试。文件系统不支持 POSIX 重命名时（FAT），退回
  `os.replace`。
- **`make_private` 失败时抛错**（P1 最终评审后的裁定）：给保存密钥的文件设置私有权限
  如果悄悄降级成 no-op，token 就会被其他用户读到，所以失败时一律抛 `OSError`（由
  `pywintypes.error` 转换而来）。平台层对外只抛 `OSError` 家族的异常，不让
  `pywintypes.error` 漏出去。另外新增 `perms.is_private_fd(fd)` 和
  `perms.owned_by_current_user(fd|path)`，后续子项目按 fd 做私有/属主校验时直接用
  这两个，不要自己再写 `IS_WINDOWS` 分支。
- **依赖**：`pywin32==312; sys_platform == "win32"` 显式加入
  `requirements-common.txt`。它原本是通过 mcp 间接引入的。

## 4. 本地 IPC `cc_remote/platform/ipc.py`

涉及的组件：claude_service、claude_broker、session_messages（包括 wrapper 和
CLI 两侧）、TUI 客户端。`codex_launch_barrier` 只在 Linux + systemd 下启用，不在
范围内。codex 自己的 `app-server-control.sock` 只做存在性和 `stat` 探测，wrapper
通过子进程（`app-server --stdio` / `proxy`）和 codex 通信，不需要改。

接口：
- `async start_server(path, handler, *, limit) -> Server`
- `async open_connection(path, *, limit, timeout) -> (StreamReader, StreamWriter)`
- `connect_sync(path, *, timeout) -> socket-like`（给同步 CLI 和 TUI 用）

POSIX：内部仍然是 Unix socket 加原有的 peer-uid、目录属主和 0700 校验，以及过期
socket 的处理逻辑，行为不变。

Windows（方案 A：127.0.0.1 TCP + 挑战-应答）：
- 原来 `.sock` 的路径改为存放 endpoint 文件，内容是 JSON
  `{"v":1,"port":…,"token":<32 字节 base64>,"pid":…,"start_time":…}`，文件用
  `perms` 设成当前用户独占的 ACL。环境变量和配置里的路径（例如
  `CC_REMOTE_CLAUDE_SERVICE_SOCKET`）都不用改。
- 服务端只绑 `127.0.0.1` 的随机端口。endpoint 文件先写到临时文件，再原子替换到
  目标路径。
- 连接前，客户端先读 endpoint 文件，按 `pid` + `start_time` 确认服务端进程还是
  原来那个，否则判定为过期，报错。
- 握手（token 从不上线路）：
  1. 客户端发 `C_nonce`（32 字节）
  2. 服务端回 `S_nonce` 和 `HMAC(token, "srv"‖C_nonce‖S_nonce)`
  3. 客户端用 `compare_digest` 校验后，发 `HMAC(token, "cli"‖S_nonce‖C_nonce)`
  4. 服务端校验通过后，才开始走原有的帧协议
  握手帧长度固定，整个握手有超时（默认 2 秒），任一步失败就断开连接；服务端对
  握手失败做计数，并按速率限制写日志。
- 过期清理：endpoint 文件里记录的进程已经不存在，或者端口连不上，就删除重建；
  如果对应进程还活着并且握手成功，就报"已在运行"。删除前先确认文件的 ACL 属于
  当前用户。
- 目录检查改为：目录位于当前用户 profile 下，不是 reparse point，ACL 没有对其他
  用户开放。
- 和 POSIX 的安全语义对比：同一用户的进程都能连上（两边一致）；其他普通用户读不
  到 endpoint 文件，也就连不上；管理员能连上（相当于 root）。

### 4.1 执行中的修订（p3a，2026-10-28）

p3a 实现和评审过程中对 §4 做了以下修订，与 §4 冲突的，以这里为准：

- **发布**：endpoint 记录先写到同目录的私有临时文件（先设 ACL 再写 token），再用
  不覆盖的重命名发布到目标路径。目标已经存在就等同于 POSIX 的 bind 冲突，报
  "已在运行"；从不覆盖别人的记录。
- **回收过期记录**：在 `<endpoint>.lock` 文件锁内串行进行，并且要做完整握手：
  - 握手成功：报"已在运行"；
  - 连接被拒绝、证明不对、超时或握手中断：只有记录里的 pid + start_time 对应的
    进程已经不存在时，才判定为过期；进程还在就报"无法验证"，保留记录；
  - 删除前重新读一次记录，内容变了就不删。
- **探测超时**：属主已死时连接超时 0.5 秒，属主还活着时 3 秒。原因是 Windows 对
  本机回环连接的拒绝要重试 SYN 约 2 秒才报出来，端口关闭和 backlog 满两种情况
  表现一样，所以拒绝本身不能作为证据。
- **监听**：只绑 `127.0.0.1`，并设置 `SO_EXCLUSIVEADDRUSE`，防止别的进程绑同一端口。
- **profile 边界**：profile 根目录从进程 token 取（`GetUserProfileDirectory`），不
  看 `HOME`/`USERPROFILE`；在 mkdir 之前就用非严格的 realpath 检查路径是否在
  profile 内，被拒绝的路径不会留下任何目录。新建的叶子目录会显式设为私有；已经
  存在的目录只检查、从不改写。
- **关闭**：`close()` 和 `serve_forever` 被取消时，都先撤回 endpoint 记录（只在记录
  仍然是自己的时候），再释放端口。撤回失败时监听照样停止，错误随后抛出，之后再调
  `close()` 会重试撤回。
- **客户端**：读记录时做 no-follow、属主、私有 ACL、不超过 4 KiB、字段严格匹配的
  检查；属主已死时在连接之前就报 `ConnectionRefusedError`；握手中任何失败都报
  `AuthenticationError`。异步客户端的 `timeout` 分别作用于连接和握手（最坏 2 倍
  `timeout`）；同步客户端的握手共用一个截止时间。
- **POSIX**：没有握手；客户端额外检查 socket 属主、0600 权限和对端 uid，macOS 通过
  `getpeereid` 取对端 uid。其余行为不变。
- **遗留风险**：
  - 握手之后的帧不加密，也不和握手绑定（只走本机回环；管理员本来就相当于 root）；
  - 无法检查的存活属主（例如提权进程）会被当作已死，等 P2 修好 `creation_time`
    后再处理；
  - 挂死但仍存活的属主会一直阻止重启，直到被杀掉；
  - 没有限制同时进行的半开握手数量。

## 5. PTY（claude_broker）

- 服务端会话：Windows 下用 `pywinpty`（ConPTY）替代 `os.openpty`、`setsid`、
  `TIOCSCTTY`、`TIOCSWINSZ`；窗口大小通过 `setwinsize` 调整。进程退出和回收交给
  `procs` 的 Job Object。`_process_start_ticks` 改用 `proc_scan` 的 start_time。
- `claude-remote` 附着端：Windows 下用 `SetConsoleMode` 开启
  `ENABLE_VIRTUAL_TERMINAL_INPUT` 和 `ENABLE_VIRTUAL_TERMINAL_PROCESSING`，退出时
  恢复原来的控制台模式。终端尺寸用 `shutil.get_terminal_size` 获取，每 250 毫秒
  轮询一次窗口变化（Windows 没有 SIGWINCH）。读 stdin 用一个后台线程，读到的
  数据交给事件循环处理。
- 依赖：requirements 里加 `pywinpty; sys_platform == "win32"`，并同步更新 lock
  文件。

## 6. 安装和部署

入口：
- 新增 `deploy/install-wrapper.ps1`（约 100 行），负责检查和安装 uv、准备 Python
  3.13.9，然后调用 `deploy/windows_install.py`。
- `windows_install.py` 对应 `install-wrapper.sh` 的流程：解析参数、配对、暂存、
  切换、注册和启动自启任务、做健康检查、失败时回滚。它复用 `release_manifest`、
  `prepare_wrapper_stage`、`install_cli`、`install_claude_service`、
  `release_retention`、`cleanup`、`install_lock`。

目录布局：
```
%LOCALAPPDATA%\cc-remote\
  releases\<版本>\.venv\Scripts\python.exe
  current   → 目录 junction，指向 releases\<版本>
  logs\     → wrapper.log、claude-service.log，按大小轮转
  bin\      → cc-remote.cmd、cc-remote-tui.cmd、claude-remote.cmd（加入用户 PATH）
```

切换流程：服务停止后，先建 `current.new`（junction），把 `current` 重命名为
`current.old`，再把 `current.new` 重命名为 `current`，最后删除 `current.old`。
任一步失败都按相反顺序恢复。`atomic_symlink.py` 增加 Windows 分支，实现这个流程。

进程托管：用 `schtasks` 注册两个任务，`cc-remote-wrapper` 和
`cc-remote-claude-service`：
- 触发：当前用户登录时；以当前用户身份运行；只在用户登录时运行（不保存密码）。
- 设置：失败后每分钟重启，最多 999 次；不限制运行时长；允许按需启动；已有实例在
  运行时不再启动新实例。
- 程序：`current\.venv\Scripts\pythonw.exe -m …`，工作目录为 `current`，日志由
  wrapper 自己写入 `logs\`。
- 启动和停止：`schtasks /Run`、`/End`，加上 `proc_scan` 确认进程身份；还要遵守
  CLAUDE.md 的约束——普通的 wrapper 升级不重启 claude service。
- 已知限制：用户没有登录时 wrapper 不运行，和 macOS 上 launchd 用户代理的行为
  一致（已确认）。

CLI：新增 `scripts/cc-remote.cmd`，调用 `current` 版本的 Python 入口。bash 版的
sudo 提权和 Darwin、Linux 分支在 Windows 上都不需要。`update`、`cleanup`、
`tasks`、`messages` 这些子命令走同一套 Python 实现。

## 7. 测试与验收

测试基线：在 Windows 上用 uv 建 3.13 环境，跑完整的 `pytest` 和 web 测试
（vitest、playwright），记录失败清单，存到 `docs/superpowers/windows-baseline.md`。

skip 规则：只有测的本来就是 POSIX 专有行为的用例（`SO_PEERCRED`、`/proc`、
systemd、launchd、cgroup、launch barrier）可以用 `@pytest.mark.posix_only`，并且
必须写明原因。skip 的用例要逐条评审，禁止为了让测试变绿而 skip。

新增测试：
- platform：锁竞争（LOCK_NB）、带 share-delete 的删除和替换、Job Object 杀进程树
  （包括孙进程）、ProcessIdentity 拒绝复用的 PID、解析 `.cmd` shim、ACL 设置和
  校验。
- ipc：握手成功；token 错误时断开；服务端被其他进程替换后，客户端拒绝连接（并且
  没有泄露 token）；过期 endpoint 的清理；"已在运行"的检测；帧大小上限和握手
  超时。
- pty：ConPTY 启动、输入输出往返、调整窗口大小、子进程退出后的回收。
- install：junction 切换和各个失败点的回滚；schtasks 的 XML 和参数生成（用 mock
  测，另外做一次真机注册和删除）。

验收：
1. Windows 上 pytest 和 web 测试全部通过（只允许评审过的 `posix_only` skip）。
2. Linux CI 维持上游现状，POSIX 下的测试不能出现新的失败。
3. 本机 Win10 端到端冒烟：在本机临时起一个 relay（绑 127.0.0.1），然后依次验证：
   用 `install-wrapper.ps1` 安装并配对；浏览器驱动 claude 跑一轮；驱动 codex 跑一
   轮；中断（drain 到终止的 ResultMessage）；接管外部 CLI 会话；TUI 附着；
   `claude-remote` 附着；`cc-remote update` 升级一次，再回滚一次；注销后重新登录，
   确认任务自动启动。

## 8. 子项目、分工与流程

| # | 子项目 | 负责方 | 依赖 |
|---|---|---|---|
| 1 | `cc_remote/platform/`（除 ipc 以外）、移植旧分支修复、测试基线 | 主会话（Claude） | 无 |
| 2 | Wrapper 核心：进程扫描和接管、codex daemon 和 external、所有 killpg 调用点、executables 接入 | Codex | 1 |
| 3 | `platform/ipc.py`、claude_service、claude_broker、ConPTY | Claude Code | 1 |
| 4 | TUI、viewer、session_messages、timed/async tasks | Codex | 3 |
| 5 | 安装和部署 | Claude Code | 1、2 |

流程：
- 工作目录在 `C:\Users\xuhaodong\work\` 下，每个子项目一个 git worktree，从
  `windows-compat-v2` 切出分支 `win/<编号>-<主题>`。
- 子项目分支推到 fork，开 fork 内 PR，目标分支是 `windows-compat-v2`。两个 agent
  交叉评审对方的 PR，主会话做最终评审并在 Windows 上跑测试，通过后合并。
- 全部合并后做端到端冒烟。通过后，给旧分支打 tag，再用新分支覆盖
  `windows-compat`（强推前再次征得用户确认）。

## 9. 风险

- （已作废，见 §3.1）Job Object 嵌套问题实测不存在。
- 计划任务以 Job 方式管理 wrapper：如果任务结束时连同子进程一起杀掉，需要长期
  存活的子进程（timed、async tasks）会受影响。由子项目 5 实测确认，必要时为这类
  子进程加 `CREATE_BREAKAWAY_FROM_JOB`。
- 防病毒软件或索引服务短暂占用文件，导致 `replace` 失败：用 `files.replace` 的
  退避重试兜底。
- `.cmd` shim 的解析依赖 npm shim 的固定格式：解析失败就退回到通过 `cmd.exe /c`
  启动，同时写一条警告日志，提示中断和进程树可能不完整。
- ConPTY 在 Win10 19044 上有些转义序列兼容问题：冒烟时重点看 claude 的 TUI 渲染。
- 以后同步上游时，新代码可能又引入 POSIX 调用：每次同步之后都在 Windows 上跑一遍
  测试，并 grep 检查 `fcntl|killpg|AF_UNIX|termios|getuid`。
