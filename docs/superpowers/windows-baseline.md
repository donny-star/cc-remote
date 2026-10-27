# Windows 测试基线（windows-compat-v2 @ 8fe5746）

运行：逐个测试文件执行 `python -X utf8 -m pytest -o addopts="" -p no:cacheprovider -q tests/<file>.py`（每个文件 240 s 超时），Windows 10 19044，Python 3.13.15

汇总：4205 passed / 2181 failed / 155 errors / 64 skipped；另有 3 个测试文件整体超时；web 测试（`npm run test:reliability` 的 42 个文件）：1 failed

## 运行方式说明

- 基线所在提交：`win/1-platform` @ `8fe5746`（基于 `windows-compat-v2`）。
- 没有用一个 pytest 进程跑全量：在 Windows 上并行全量会在约 69% 处挂住
  （`test_claude_autocompact`、`test_codex_controls`、`test_codex_session_delete` 三个文件，
  在改动前的基线上同样挂住）。因此每个 `tests/test_*.py` 单独起一个 pytest 进程，240 s 超时，
  再从各自的 junit xml 聚合。超时的 3 个文件整体记为子项目 2 的基线条目。
- 收集错误只剩 2 个，都是 `deploy/` 模块的 POSIX 导入：`tests/test_relay_update_job.py`
  （`deploy/relay_update_job.py` 导入 `fcntl`）和 `tests/test_release_retention.py`
  （`deploy/release_retention.py` 导入 `pwd`），归子项目 5。
- 失败的 154 个文件又用 `--tb=native` 重跑了一遍取完整调用栈：没有任何失败的调用栈经过
  `cc_remote/platform/`，所以“子项目 1 遗留”为 0。两次运行之间只有
  `tests/test_codex_profiles.py` 的失败数不同（62 / 63），记为不稳定。
- 归属规则：先按测试文件名匹配计划中的表（`test_adopt_wrapper`、`test_prepare_wrapper_stage`、
  `test_relay_update_job`、`test_desktop_remote_ui` 直接测 `deploy/` 脚本，按“deploy 相关”归子项目 5）；文件名没有命中的，按调用栈里最深的仓库内模块归属
  （`deploy/` → 5，`cc_remote/claude_broker|claude_service` → 3，
  `cc_remote/tui*|viewer*|session_messages|timed_tasks|async_tasks|task_*` → 4），
  仍无法判断的（失败只在测试代码里的断言）默认归子项目 2（这类测试都在驱动 Wrapper）。
- 测试 ID 超过 150 字符的参数化用例做了截断（以 `…]` 结尾）。异常文本里的临时目录替换成 `<tmp>`。
- 本地 `.venv` 不是按 `requirements*.lock` 建的：`mcp` 为 2.2.0（lock 为 1.28.1，Linux CI 用 lock）。
  因此 `Server.list_tools` 相关的 62 条失败是本地环境偏差，留在基线里仅作记录。
- 一个用例同时有 call 失败和 teardown 错误时，两条都列出（标 `[error]` 的是 error）。

## 共性根因（跨子项目）

| 根因 | 用例数 | 涉及子项目 |
|---|---|---|
| `os.O_NOFOLLOW` / `O_DIRECTORY` / `O_NONBLOCK` 等 POSIX open 标志 | 273 | 2, 3, 4, 5 |
| `os.getuid` / `geteuid` / `killpg` / `mkfifo` / `pread` / `signal.SIGKILL` 直接调用 | 65 | 2, 4, 5 |
| `os.open(目录)` 做目录 fsync → 画像状态迁移失败（Claude/Codex profile state migration） | 235 | 2, 3 |
| 调用方直接比较 `st_uid`/`st_mode & 0o077` 判私有（未走 `platform.perms.is_private`） | 342 | 2, 3, 4, 5 |
| 私有状态文件持久化失败（journal/store could not be persisted） | 177 | 2 |
| 测试数据用 POSIX 绝对路径，Windows 上 `Path.is_absolute()` 为假 | 156 | 2 |
| 创建符号链接需要特权（WinError 1314，未开开发者模式） | 297 | 2, 3, 4, 5 |
| `socket.AF_UNIX` / `asyncio.start_unix_server` 不可用 | 75 | 2, 4 |
| MCP `Server.list_tools` 不存在（本地 venv 装的是 mcp 2.2.0，`requirements*.lock` 锁定 1.28.1；本地环境偏差，与平台无关，按 lock 重建 venv 后应消失） | 62 | 2, 4 |
| 参数化测试 ID 超过 Windows 环境变量 32767 上限（PYTEST_CURRENT_TEST） | 8 | 2, 4 |
| 超时（`TimeoutError`） | 57 | 2, 3, 4 |
| 其余（断言差异、路径分隔符、子进程/可执行文件等，见明细） | 592 | 2, 3, 4, 5 |

## 子项目 1 遗留 — 0 个

无。所有失败调用栈都不经过 `cc_remote/platform/`。

## 子项目 2（Wrapper 核心）— 1625 个

按文件汇总：

| 测试文件 | 失败数 | 主要原因 |
|---|---|---|
| tests/test_session_artifacts.py | 103 | AssertionError: assert ['C:\\project...t\\page.html'] == ['/project/a ...ut/page.html'] |
| tests/test_codex_daemon.py | 63 | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_profiles.py | 62 | RuntimeError: Codex profile state migration is incomplete |
| tests/test_markdown_preview.py | 54 | ValueError: 没有权限读取该文件 |
| tests/test_codex_readiness.py | 51 | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_dsh_attachments.py | 44 | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_residency.py | 42 | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_claude_permission_state.py | 40 | RuntimeError: Claude profile state migration is incomplete |
| tests/test_codex_external.py | 39 | RuntimeError: Codex profile state migration is incomplete |
| tests/test_claude_catalog_cwd.py | 37 | AssertionError: assert (SDKSessionInfo(session_id='11111111-1111-4111-8111-111111111111', summary='Image review', last_m |
| tests/test_dsh_goal_recovery.py | 37 | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_runtime.py | 37 | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_codex_app_tools.py | 35 | AttributeError: module 'socket' has no attribute 'AF_UNIX' |
| tests/test_dsh_metadata_recovery.py | 33 | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_history.py | 33 | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store could not be persisted |
| tests/test_codex_npm_ownership.py | 32 | RuntimeError: Codex profile state migration is incomplete |
| tests/test_dsh_mutations.py | 32 | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_claude_rate_limits.py | 30 | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_codex_worktree_fork.py | 28 | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_checkpoints.py | 27 | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_service.py | 27 | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_claude_external.py | 26 | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_audio_preview.py | 25 | AssertionError: Regex pattern did not match. |
| tests/test_codex_shared_machine.py | 25 | PermissionError: [Errno 13] Permission denied: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\cc |
| tests/test_claude_usage.py | 21 | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_codex_missing_sessions.py | 21 | RuntimeError: Codex profile state migration is incomplete |
| tests/test_claude_account_isolation.py | 20 | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_controls.py | 20 | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store is not a private bounded file |
| tests/test_claude_rewind.py | 20 | RuntimeError: coroutine raised StopIteration |
| tests/test_codex_session_archive.py | 20 | RuntimeError: Codex profile state migration is incomplete |
| tests/test_dsh_model_recovery.py | 19 | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_claude_profiles.py | 18 | TimeoutError |
| tests/test_codex_agent_activity.py | 18 | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py | 18 | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_claude_steering.py | 17 | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_wrapper_core_fixes.py | 17 | TimeoutError |
| tests/test_claude_btw_identity.py | 16 | RuntimeError: Claude profile state migration is incomplete |
| tests/test_codex_launch_barrier.py | 16 | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_dsh_upload_recovery.py | 16 | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_codex_task_activity.py | 15 | ValueError: Task state directory must be private and owned by this user |
| tests/test_claude_session_fork.py | 14 | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_codex_desktop.py | 14 | cc_remote.codex_desktop.LaunchError: 共享入口的状态目录不是当前用户的私有目录。 |
| tests/test_atomic_new_session.py | 13 | assert None is not None |
| tests/test_claude_forks.py | 13 | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_rollback_commands.py | 13 | cc_remote.wrapper.rollback_commands.RollbackJournalError: rollback journal could not be persisted |
| tests/test_presentation_sync.py | 11 | AssertionError: assert False |
| tests/test_codex_forks.py | 10 | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_spontaneous_stream.py | 10 | cc_remote.wrapper.codex_client_messages.CodexClientMessageStoreError: Codex client-message store is not a private bounde |
| tests/test_dsh_control_scheduling.py | 10 | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_history_turn_changes.py | 10 | AssertionError: assert 'src\\code.py' == '/repo/src/code.py' |
| tests/test_codex_rollback_integration.py | 9 | AssertionError: assert False |
| tests/test_reliability_edges.py | 9 | RuntimeError: Codex profile state migration is incomplete |
| tests/test_session_presentation.py | 9 | cc_remote.wrapper.session_presentation.SessionPresentationStoreError: session presentation store could not be persisted |
| tests/test_workspaces.py | 9 | AssertionError: 438 != 384 |
| tests/test_codex_session_migration.py | 8 | AssertionError: assert False |
| tests/test_command_reliability.py | 8 | RuntimeError: Claude profile state migration is incomplete |
| tests/test_dsh_interrupt_recovery.py | 8 | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_profile_rollback.py | 8 | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_ask_bounds.py | 7 | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_steer_attachments.py | 7 | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_codex_archives.py | 7 | cc_remote.wrapper.codex_archives.CodexArchiveJournalError: Codex archive journal could not be persisted |
| tests/test_codex_context_settings.py | 7 | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_process_clocks.py | 7 | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store could not be persisted |
| tests/test_manual_compaction.py | 7 | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store is not a private bounded file |
| tests/test_multisession.py | 7 | RuntimeError: Claude profile state migration is incomplete |
| tests/test_child_env.py | 6 | RuntimeError: CLAUDE_BIN must be an absolute path |
| tests/test_claude_models.py | 6 | AssertionError: assert (33206 & 511) == 384 |
| tests/test_dsh_follow_recovery.py | 6 | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_attachments.py | 5 | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_claude_btw_cleanup.py | 5 | RuntimeError: private btw state could not be persisted |
| tests/test_codex_client_messages.py | 5 | cc_remote.wrapper.codex_client_messages.CodexClientMessageStoreError: Codex client-message store could not be persisted |
| tests/test_codex_control_store.py | 5 | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_dsh_features.py | 5 | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_engine_capabilities.py | 5 | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_session_pins.py | 5 | cc_remote.wrapper.session_pins.SessionPinStoreError: session pin store could not be persisted |
| tests/test_session_plans.py | 5 | cc_remote.wrapper.session_plans.SessionPlanStoreError: session plan store could not be persisted |
| tests/test_workspace_browser.py | 5 | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_claude_client_messages.py | 4 | cc_remote.wrapper.claude_client_messages.ClaudeClientMessageStoreError: Claude client-message store could not be persist |
| tests/test_claude_live_context.py | 4 | AssertionError: assert False |
| tests/test_codex_turn_leases.py | 4 | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_command_scheduling.py | 4 | TimeoutError |
| tests/test_claude_native_permissions.py | 3 | RuntimeError: Claude profile state migration is incomplete |
| tests/test_codex_provider_repair.py | 3 | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_codex_runtime_events.py | 3 | RuntimeError: Codex profile state migration is incomplete |
| tests/test_devices.py | 3 | AssertionError: assert (33206 & 511) == 384 |
| tests/test_dsh_export_scheduling.py | 3 | AssertionError: assert None == 'dsh@root' |
| tests/test_question_batches.py | 3 | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_turn_changes.py | 3 | AssertionError: assert ('--- src/code.py' in '--- src\\code.py\n+++ src\\code.py\n@@ -1 +1 @@\n-A\n+B\n') |
| tests/test_btw_history.py | 2 | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_codex_history.py | 2 | assert 647 == 642 |
| tests/test_dsh_read_timeout.py | 2 | AssertionError: assert (None == 'dsh@other') |
| tests/test_dsh_sidechats.py | 2 | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_history_store.py | 2 | AttributeError: 'NoneType' object has no attribute 'rows' |
| tests/test_link_preview_images.py | 2 | failed on setup with "ValueError: the environment variable is longer than 32767 characters" |
| tests/test_task_refresh.py | 2 | AssertionError: Regex pattern did not match. |
| tests/test_transport_bounds.py | 2 | AssertionError: assert 'http://old.example:7890' == 'http://new.example:8080' |
| tests/test_turn_file_pages.py | 2 | TypeError: 'NoneType' object is not subscriptable |
| tests/test_btw_lifecycle.py | 1 | RuntimeError: Codex profile state migration is incomplete |
| tests/test_claude_autocompact.py | 1 | TIMEOUT: module did not finish within 240 s (also hangs on the pre-change base) |
| tests/test_claude_compaction_history.py | 1 | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store is not a private bounded file |
| tests/test_claude_storage_root.py | 1 | AssertionError: assert WindowsPath('C:/Users/xuhaodong/.claude') == WindowsPath('<tmp>') |
| tests/test_codex_agents.py | 1 | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_controls.py | 1 | TIMEOUT: module did not finish within 240 s (also hangs on the pre-change base) |
| tests/test_codex_daemon_restart.py | 1 | AssertionError: assert (33206 & 511) == 384 |
| tests/test_codex_generated_images.py | 1 | KeyError: 'preview_id' |
| tests/test_codex_rpc.py | 1 | ValueError: invalid POSIX Codex RPC nofile limit |
| tests/test_codex_session_delete.py | 1 | TIMEOUT: module did not finish within 240 s (also hangs on the pre-change base) |
| tests/test_codex_worktrees.py | 1 | AssertionError: assert 0 == 1 |
| tests/test_dsh_changes.py | 1 | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_client.py | 1 | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_history_continuity.py | 1 | assert False |
| tests/test_query_queue.py | 1 | RuntimeError: Claude profile state migration is incomplete |
| tests/test_spreadsheet_preview.py | 1 | ValueError: 没有权限读取该文件 |
| tests/test_work_context.py | 1 | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_wrapper_startup.py | 1 | AssertionError: assert [{'profile': ...ertionError'}] == [{'profile': ...us': 'ready'}] |

明细：

| 测试 | 失败原因（第一行异常） |
|---|---|
| tests/test_ask_bounds.py::test_cursor_after_original_ask_still_receives_authoritative_seed | RuntimeError: Claude profile state migration is incomplete |
| tests/test_ask_bounds.py::test_expired_question_is_closed_not_seeded_by_hello | RuntimeError: Claude profile state migration is incomplete |
| tests/test_ask_bounds.py::test_fresh_hello_recovers_pending_ask_after_ring_eviction[claude] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_ask_bounds.py::test_fresh_hello_recovers_pending_ask_after_ring_eviction[codex] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_ask_bounds.py::test_hello_racing_answer_never_reopens_closed_question | RuntimeError: Claude profile state migration is incomplete |
| tests/test_ask_bounds.py::test_superseded_question_closes_once_and_cannot_return_on_hello | RuntimeError: Claude profile state migration is incomplete |
| tests/test_ask_bounds.py::test_targeted_pending_ask_is_recovered_and_answered_only_by_target | RuntimeError: Claude profile state migration is incomplete |
| tests/test_atomic_new_session.py::test_blank_new_session_does_not_start_a_turn | AssertionError: assert ['error'] == ['snapshot', ...pact', 'perm'] |
| tests/test_atomic_new_session.py::test_invalid_new_session_cwd_is_single_correlated_error | RuntimeError: Codex profile state migration is incomplete |
| tests/test_atomic_new_session.py::test_new_session_starts_initial_query_on_the_new_ctx[None] | assert None is not None |
| tests/test_atomic_new_session.py::test_new_session_starts_initial_query_on_the_new_ctx[acceptEdits] | assert None is not None |
| tests/test_atomic_new_session.py::test_new_session_starts_initial_query_on_the_new_ctx[auto] | assert None is not None |
| tests/test_atomic_new_session.py::test_new_session_starts_initial_query_on_the_new_ctx[bypassPermissions] | assert None is not None |
| tests/test_atomic_new_session.py::test_new_session_starts_initial_query_on_the_new_ctx[default] | assert None is not None |
| tests/test_atomic_new_session.py::test_new_session_starts_initial_query_on_the_new_ctx[plan] | assert None is not None |
| tests/test_atomic_new_session.py::test_new_session_sync_failures_never_emit_to_focused_session[cap] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_atomic_new_session.py::test_new_session_sync_failures_never_emit_to_focused_session[connect] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_atomic_new_session.py::test_spawn_bootstrap_disconnects_failed_resume_before_fresh_retry | RuntimeError: Claude profile state migration is incomplete |
| tests/test_atomic_new_session.py::test_spawn_bootstrap_missing_session_still_falls_back_to_fresh | RuntimeError: Claude profile state migration is incomplete |
| tests/test_atomic_new_session.py::test_spawn_resume_missing_session_keeps_legacy_focused_error | RuntimeError: Claude profile state migration is incomplete |
| tests/test_attachments.py::test_rejected_work_steer_keeps_persistent_uploads | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_attachments.py::test_stashed_files_are_private_unique_and_path_safe | assert False |
| tests/test_attachments.py::test_work_attachment_write_stays_bound_after_directory_symlink_swap | RuntimeError: secure Work upload directories are unsupported |
| tests/test_attachments.py::test_work_turn_persists_files_and_images_inside_private_workspace | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_attachments.py::test_work_upload_refuses_symlinked_or_unmanaged_root | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_audio_preview.py::test_audio_preview_identifies_container_and_preserves_bytes[.aac-audio/aac] | ValueError: 没有权限读取该文件 |
| tests/test_audio_preview.py::test_audio_preview_identifies_container_and_preserves_bytes[.flac-audio/flac] | ValueError: 没有权限读取该文件 |
| tests/test_audio_preview.py::test_audio_preview_identifies_container_and_preserves_bytes[.m4a-audio/mp4] | ValueError: 没有权限读取该文件 |
| tests/test_audio_preview.py::test_audio_preview_identifies_container_and_preserves_bytes[.m4b-audio/mp4] | ValueError: 没有权限读取该文件 |
| tests/test_audio_preview.py::test_audio_preview_identifies_container_and_preserves_bytes[.mp3-audio/mpeg] | ValueError: 没有权限读取该文件 |
| tests/test_audio_preview.py::test_audio_preview_identifies_container_and_preserves_bytes[.oga-audio/ogg] | ValueError: 没有权限读取该文件 |
| tests/test_audio_preview.py::test_audio_preview_identifies_container_and_preserves_bytes[.ogg-audio/ogg] | ValueError: 没有权限读取该文件 |
| tests/test_audio_preview.py::test_audio_preview_identifies_container_and_preserves_bytes[.opus-audio/ogg] | ValueError: 没有权限读取该文件 |
| tests/test_audio_preview.py::test_audio_preview_identifies_container_and_preserves_bytes[.wav-audio/wav] | ValueError: 没有权限读取该文件 |
| tests/test_audio_preview.py::test_audio_preview_identifies_container_and_preserves_bytes[.wave-audio/wav] | ValueError: 没有权限读取该文件 |
| tests/test_audio_preview.py::test_audio_preview_identifies_container_and_preserves_bytes[.webm-audio/webm] | ValueError: 没有权限读取该文件 |
| tests/test_audio_preview.py::test_audio_preview_is_private_one_shot_and_protocol_roundtrips | AssertionError: assert (True and '没有权限读取该文件' is None) |
| tests/test_audio_preview.py::test_audio_preview_rejects_non_audio_bytes[.aac] | AssertionError: Regex pattern did not match. |
| tests/test_audio_preview.py::test_audio_preview_rejects_non_audio_bytes[.flac] | AssertionError: Regex pattern did not match. |
| tests/test_audio_preview.py::test_audio_preview_rejects_non_audio_bytes[.m4a] | AssertionError: Regex pattern did not match. |
| tests/test_audio_preview.py::test_audio_preview_rejects_non_audio_bytes[.m4b] | AssertionError: Regex pattern did not match. |
| tests/test_audio_preview.py::test_audio_preview_rejects_non_audio_bytes[.mp3] | AssertionError: Regex pattern did not match. |
| tests/test_audio_preview.py::test_audio_preview_rejects_non_audio_bytes[.oga] | AssertionError: Regex pattern did not match. |
| tests/test_audio_preview.py::test_audio_preview_rejects_non_audio_bytes[.ogg] | AssertionError: Regex pattern did not match. |
| tests/test_audio_preview.py::test_audio_preview_rejects_non_audio_bytes[.opus] | AssertionError: Regex pattern did not match. |
| tests/test_audio_preview.py::test_audio_preview_rejects_non_audio_bytes[.wav] | AssertionError: Regex pattern did not match. |
| tests/test_audio_preview.py::test_audio_preview_rejects_non_audio_bytes[.wave] | AssertionError: Regex pattern did not match. |
| tests/test_audio_preview.py::test_audio_preview_rejects_non_audio_bytes[.webm] | AssertionError: Regex pattern did not match. |
| tests/test_audio_preview.py::test_audio_preview_rejects_video_riff_and_does_not_truncate | AssertionError: Regex pattern did not match. |
| tests/test_audio_preview.py::test_work_audio_preview_retains_external_file_authorization_for_symlink_escapes | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_btw_history.py::test_claude_btw_managed_replay_ignores_pre_echo_background_owner[None] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_btw_history.py::test_claude_btw_managed_replay_ignores_pre_echo_background_owner[previous-input] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_btw_lifecycle.py::test_sync_destroyed_fork_keeps_history_scope_and_monotonic_readonly_state | RuntimeError: Codex profile state migration is incomplete |
| tests/test_child_env.py::test_claude_bin_defaults_to_daily_local_cli_and_can_be_configured | AssertionError: assert 'C:\\Users\\x...\\bin\\claude' == 'C:\\Users\\x...\\bin\\claude' |
| tests/test_child_env.py::test_claude_profiles_use_the_daily_cli_account_metadata[False-native] | RuntimeError: CLAUDE_BIN must be an absolute path |
| tests/test_child_env.py::test_claude_profiles_use_the_daily_cli_account_metadata[False-other] | RuntimeError: CLAUDE_BIN must be an absolute path |
| tests/test_child_env.py::test_claude_profiles_use_the_daily_cli_account_metadata[True-native] | RuntimeError: CLAUDE_BIN must be an absolute path |
| tests/test_child_env.py::test_claude_profiles_use_the_daily_cli_account_metadata[True-other] | RuntimeError: CLAUDE_BIN must be an absolute path |
| tests/test_child_env.py::test_native_claude_profile_symlink_uses_the_same_account_layout | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_claude_account_isolation.py::test_broker_exit_restores_the_selected_account[explicit-contracted] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_broker_exit_restores_the_selected_account[explicit-multi] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_broker_exit_restores_the_selected_account[explicit-single] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_broker_exit_restores_the_selected_account[implicit] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_capability_operations_use_the_same_account_boundary[explicit-contracted-hook] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_capability_operations_use_the_same_account_boundary[explicit-contracted-list] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_capability_operations_use_the_same_account_boundary[explicit-contracted-plugin] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_capability_operations_use_the_same_account_boundary[explicit-multi-hook] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_capability_operations_use_the_same_account_boundary[explicit-multi-list] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_capability_operations_use_the_same_account_boundary[explicit-multi-plugin] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_capability_operations_use_the_same_account_boundary[explicit-single-hook] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_capability_operations_use_the_same_account_boundary[explicit-single-list] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_capability_operations_use_the_same_account_boundary[explicit-single-plugin] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_capability_operations_use_the_same_account_boundary[implicit-hook] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_capability_operations_use_the_same_account_boundary[implicit-list] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_capability_operations_use_the_same_account_boundary[implicit-plugin] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_spawn_reconnect_and_btw_keep_the_selected_account[explicit-contracted] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_spawn_reconnect_and_btw_keep_the_selected_account[explicit-multi] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_spawn_reconnect_and_btw_keep_the_selected_account[explicit-single] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_account_isolation.py::test_spawn_reconnect_and_btw_keep_the_selected_account[implicit] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_autocompact.py (whole module) | TIMEOUT: module did not finish within 240 s (also hangs on the pre-change base) |
| tests/test_claude_btw_cleanup.py::test_broker_catalog_cannot_reintroduce_a_retired_private_id | RuntimeError: private btw state could not be persisted |
| tests/test_claude_btw_cleanup.py::test_late_full_transcript_stays_private_after_cleanup_and_restart[False] | RuntimeError: private btw state could not be persisted |
| tests/test_claude_btw_cleanup.py::test_late_full_transcript_stays_private_after_cleanup_and_restart[True] | RuntimeError: private btw state could not be persisted |
| tests/test_claude_btw_cleanup.py::test_receipt_compaction_failure_keeps_the_pending_privacy_guard | RuntimeError: private btw state could not be persisted |
| tests/test_claude_btw_cleanup.py::test_retired_capacity_refuses_new_ids_without_forgetting_old_ones | RuntimeError: private btw state could not be persisted |
| tests/test_claude_btw_identity.py::test_btw_launches_with_parent_model_and_reports_it_before_first_query[claude-opus-4-6[1m]-claude-opus-4-6[1m]-False…] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_btw_identity.py::test_btw_launches_with_parent_model_and_reports_it_before_first_query[claude-opus-4-6[1m]-claude-opus-4-6[1m]-True] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_btw_identity.py::test_btw_launches_with_parent_model_and_reports_it_before_first_query[claude-opus-5-claude-opus-5[1m]-False] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_btw_identity.py::test_btw_launches_with_parent_model_and_reports_it_before_first_query[claude-opus-5-claude-opus-5[1m]-True] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_btw_identity.py::test_btw_launches_with_parent_model_and_reports_it_before_first_query[provider-model-provider-model-False] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_btw_identity.py::test_btw_launches_with_parent_model_and_reports_it_before_first_query[provider-model-provider-model-True] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_btw_identity.py::test_close_cleans_reserved_identity_even_without_a_first_turn[False] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_btw_identity.py::test_close_cleans_reserved_identity_even_without_a_first_turn[True] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_btw_identity.py::test_closed_forks_do_not_exhaust_pending_or_resident_capacity | RuntimeError: private btw state could not be persisted |
| tests/test_claude_btw_identity.py::test_empty_btw_is_private_without_blocking_owner_or_other_client | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_btw_identity.py::test_failed_fork_retains_privacy_until_native_writer_is_stopped[False-False] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_btw_identity.py::test_failed_fork_retains_privacy_until_native_writer_is_stopped[False-True] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_btw_identity.py::test_failed_fork_retains_privacy_until_native_writer_is_stopped[True-False] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_btw_identity.py::test_failed_fork_retains_privacy_until_native_writer_is_stopped[True-True] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_btw_identity.py::test_failed_private_registration_never_starts_a_native_fork | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_btw_identity.py::test_reservation_is_not_counted_twice_against_private_fork_cap | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_catalog_cwd.py::test_catalog_hides_metadata_stub_without_removing_real_history[/original-project-False] | AssertionError: assert [] == ['11111111-11...111111111111'] |
| tests/test_claude_catalog_cwd.py::test_catalog_hides_metadata_stub_without_removing_real_history[/original-project-True] | AssertionError: assert [] == ['11111111-11...111111111111'] |
| tests/test_claude_catalog_cwd.py::test_catalog_limit_counts_real_sessions_after_metadata_filter[0-/original-project-False] | AssertionError: assert [] == ['00000000-00...000000000002'] |
| tests/test_claude_catalog_cwd.py::test_catalog_limit_counts_real_sessions_after_metadata_filter[0-/original-project-True] | AssertionError: assert [] == ['00000000-00...000000000002'] |
| tests/test_claude_catalog_cwd.py::test_catalog_limit_counts_real_sessions_after_metadata_filter[0-None-False] | assert False |
| tests/test_claude_catalog_cwd.py::test_catalog_limit_counts_real_sessions_after_metadata_filter[0-None-True] | assert False |
| tests/test_claude_catalog_cwd.py::test_catalog_limit_counts_real_sessions_after_metadata_filter[1-/original-project-False] | AssertionError: assert [] == ['00000000-00...000000000001'] |
| tests/test_claude_catalog_cwd.py::test_catalog_limit_counts_real_sessions_after_metadata_filter[1-/original-project-True] | AssertionError: assert [] == ['00000000-00...000000000001'] |
| tests/test_claude_catalog_cwd.py::test_catalog_limit_counts_real_sessions_after_metadata_filter[2-/original-project-False] | AssertionError: assert [] == ['00000000-00...111111111111'] |
| tests/test_claude_catalog_cwd.py::test_catalog_limit_counts_real_sessions_after_metadata_filter[2-/original-project-True] | AssertionError: assert [] == ['00000000-00...111111111111'] |
| tests/test_claude_catalog_cwd.py::test_catalog_limit_counts_real_sessions_after_metadata_filter[2-None-False] | assert False |
| tests/test_claude_catalog_cwd.py::test_catalog_limit_counts_real_sessions_after_metadata_filter[2-None-True] | assert False |
| tests/test_claude_catalog_cwd.py::test_catalog_limit_counts_real_sessions_after_metadata_filter[200-/original-project-False] | AssertionError: assert [] == ['00000000-00...000000000002'] |
| tests/test_claude_catalog_cwd.py::test_catalog_limit_counts_real_sessions_after_metadata_filter[200-/original-project-True] | AssertionError: assert [] == ['00000000-00...000000000002'] |
| tests/test_claude_catalog_cwd.py::test_catalog_limit_counts_real_sessions_after_metadata_filter[200-None-False] | assert False |
| tests/test_claude_catalog_cwd.py::test_catalog_limit_counts_real_sessions_after_metadata_filter[200-None-True] | assert False |
| tests/test_claude_catalog_cwd.py::test_cwd_recovery_rejects_invalid_buckets_and_later_prefix_collisions[-another-project-roject-component-long-project…] | FileNotFoundError: [WinError 3] 系统找不到指定的路径。: '<tmp>' |
| tests/test_claude_catalog_cwd.py::test_cwd_recovery_rejects_invalid_buckets_and_later_prefix_collisions[-workspace-long-project-component-long-project…] | FileNotFoundError: [WinError 3] 系统找不到指定的路径。: '<tmp>' |
| tests/test_claude_catalog_cwd.py::test_cwd_recovery_rejects_invalid_buckets_and_later_prefix_collisions[-workspace-long-project-component-long-project…] | FileNotFoundError: [WinError 3] 系统找不到指定的路径。: '<tmp>' |
| tests/test_claude_catalog_cwd.py::test_cwd_recovery_rejects_invalid_buckets_and_later_prefix_collisions[-workspace-long-project-component-long-project…] | FileNotFoundError: [WinError 3] 系统找不到指定的路径。: '<tmp>' |
| tests/test_claude_catalog_cwd.py::test_cwd_recovery_rejects_invalid_buckets_and_later_prefix_collisions[-workspace-long-project-component-long-project…] | FileNotFoundError: [WinError 3] 系统找不到指定的路径。: '<tmp>' |
| tests/test_claude_catalog_cwd.py::test_cwd_recovery_skips_oversized_rows_without_unbounded_allocation | AssertionError: assert (SDKSessionInfo(session_id='11111111-1111-4111-8111-111111111111', summary='Image review', last_modified=1793106830326, file_size=17044734, custom_title='Ima… |
| tests/test_claude_catalog_cwd.py::test_image_session_recovers_full_cwd_from_hashed_project_bucket[legacy-hash] | FileNotFoundError: [WinError 3] 系统找不到指定的路径。: '<tmp>' |
| tests/test_claude_catalog_cwd.py::test_image_session_recovers_full_cwd_from_hashed_project_bucket[native-ascii] | FileNotFoundError: [WinError 3] 系统找不到指定的路径。: '<tmp>' |
| tests/test_claude_catalog_cwd.py::test_image_session_recovers_full_cwd_from_hashed_project_bucket[native-emoji-hash] | FileNotFoundError: [WinError 3] 系统找不到指定的路径。: '<tmp>' |
| tests/test_claude_catalog_cwd.py::test_image_session_recovers_full_cwd_from_hashed_project_bucket[native-emoji-prefix] | FileNotFoundError: [WinError 3] 系统找不到指定的路径。: '<tmp>' |
| tests/test_claude_catalog_cwd.py::test_large_image_session_recovers_original_cwd_and_can_be_selected[base64-record-boundary-False] | AssertionError: assert (SDKSessionInfo(session_id='11111111-1111-4111-8111-111111111111', summary='Image review', last_modified=1793106825352, file_size=8389401, custom_title='Imag… |
| tests/test_claude_catalog_cwd.py::test_large_image_session_recovers_original_cwd_and_can_be_selected[base64-record-boundary-True] | AssertionError: assert (SDKSessionInfo(session_id='11111111-1111-4111-8111-111111111111', summary='Image review', last_modified=1793106825713, file_size=8389401, custom_title='Imag… |
| tests/test_claude_catalog_cwd.py::test_large_image_session_recovers_original_cwd_and_can_be_selected[count-and-total-limit-False] | AssertionError: assert (SDKSessionInfo(session_id='11111111-1111-4111-8111-111111111111', summary='Image review', last_modified=1793106828953, file_size=22371689, custom_title='Ima… |
| tests/test_claude_catalog_cwd.py::test_large_image_session_recovers_original_cwd_and_can_be_selected[count-and-total-limit-True] | AssertionError: assert (SDKSessionInfo(session_id='11111111-1111-4111-8111-111111111111', summary='Image review', last_modified=1793106829703, file_size=22371689, custom_title='Ima… |
| tests/test_claude_catalog_cwd.py::test_large_image_session_recovers_original_cwd_and_can_be_selected[sdk-window-False] | AssertionError: assert (SDKSessionInfo(session_id='11111111-1111-4111-8111-111111111111', summary='Image review', last_modified=1793106824798, file_size=267465, custom_title='Image… |
| tests/test_claude_catalog_cwd.py::test_large_image_session_recovers_original_cwd_and_can_be_selected[sdk-window-True] | AssertionError: assert (SDKSessionInfo(session_id='11111111-1111-4111-8111-111111111111', summary='Image review', last_modified=1793106825043, file_size=267465, custom_title='Image… |
| tests/test_claude_catalog_cwd.py::test_large_image_session_recovers_original_cwd_and_can_be_selected[single-limit-False] | AssertionError: assert (SDKSessionInfo(session_id='11111111-1111-4111-8111-111111111111', summary='Image review', last_modified=1793106826195, file_size=16778009, custom_title='Ima… |
| tests/test_claude_catalog_cwd.py::test_large_image_session_recovers_original_cwd_and_can_be_selected[single-limit-True] | AssertionError: assert (SDKSessionInfo(session_id='11111111-1111-4111-8111-111111111111', summary='Image review', last_modified=1793106826814, file_size=16778009, custom_title='Ima… |
| tests/test_claude_catalog_cwd.py::test_large_image_session_recovers_original_cwd_and_can_be_selected[total-limit-False] | AssertionError: assert (SDKSessionInfo(session_id='11111111-1111-4111-8111-111111111111', summary='Image review', last_modified=1793106827557, file_size=22370601, custom_title='Ima… |
| tests/test_claude_catalog_cwd.py::test_large_image_session_recovers_original_cwd_and_can_be_selected[total-limit-True] | AssertionError: assert (SDKSessionInfo(session_id='11111111-1111-4111-8111-111111111111', summary='Image review', last_modified=1793106828253, file_size=22370601, custom_title='Ima… |
| tests/test_claude_catalog_cwd.py::test_long_cwd_legacy_hash_requires_explicit_native_root | FileNotFoundError: [WinError 3] 系统找不到指定的路径。: '<tmp>' |
| tests/test_claude_client_messages.py::test_client_message_alias_is_bound_to_exact_transcript_inode | cc_remote.wrapper.claude_client_messages.ClaudeClientMessageStoreError: Claude client-message store could not be persisted |
| tests/test_claude_client_messages.py::test_client_message_alias_survives_reopen_and_append | cc_remote.wrapper.claude_client_messages.ClaudeClientMessageStoreError: Claude client-message store could not be persisted |
| tests/test_claude_client_messages.py::test_client_message_aliases_are_exact_bounded_and_deletable | cc_remote.wrapper.claude_client_messages.ClaudeClientMessageStoreError: Claude client-message store could not be persisted |
| tests/test_claude_client_messages.py::test_client_message_store_rejects_unsafe_or_malformed_records | cc_remote.wrapper.claude_client_messages.ClaudeClientMessageStoreError: Claude client-message store could not be persisted |
| tests/test_claude_compaction_history.py::test_compact_controls_survive_reload_in_the_same_account | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store is not a private bounded file |
| tests/test_claude_controls.py::test_legacy_valid_autocompact_was_already_applied[1] | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store is not a private bounded file |
| tests/test_claude_controls.py::test_legacy_valid_autocompact_was_already_applied[2] | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store is not a private bounded file |
| tests/test_claude_controls.py::test_missing_autocompact_delegates_to_native_default[1] | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store is not a private bounded file |
| tests/test_claude_controls.py::test_missing_autocompact_delegates_to_native_default[2] | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store is not a private bounded file |
| tests/test_claude_controls.py::test_missing_autocompact_delegates_to_native_default[3] | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store is not a private bounded file |
| tests/test_claude_controls.py::test_native_policy_explicit_500k_choice_is_preserved | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store is not a private bounded file |
| tests/test_claude_controls.py::test_native_policy_marker_is_written_without_breaking_v3_format | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_claude_controls.py::test_pending_autocompact_transaction_roundtrips_without_compact_proof | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_claude_controls.py::test_remote_control_store_is_private_bounded_and_roundtrips | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_claude_controls.py::test_remote_control_store_keeps_provider_native_model_and_drops_bad_controls | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_claude_controls.py::test_remote_control_store_rejects_boolean_schema_version | AssertionError: Regex pattern did not match. |
| tests/test_claude_controls.py::test_remote_control_store_rejects_public_or_symlink_state | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_claude_controls.py::test_remote_control_store_roundtrips_autocompact_without_losing_controls | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_claude_controls.py::test_remote_control_store_still_rejects_an_invalid_model | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_claude_controls.py::test_stale_compact_proof_is_discarded_on_restart | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store is not a private bounded file |
| tests/test_claude_controls.py::test_v3_explicit_inherit_remains_explicit | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store is not a private bounded file |
| tests/test_claude_controls.py::test_v3_forced_500k_default_migrates_to_native_behavior[custom-500000] | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store is not a private bounded file |
| tests/test_claude_controls.py::test_v3_forced_500k_default_migrates_to_native_behavior[inherit-None] | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store is not a private bounded file |
| tests/test_claude_controls.py::test_v3_missing_applied_value_cannot_bypass_lowering_transaction | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store is not a private bounded file |
| tests/test_claude_controls.py::test_work_autocompact_store_clears_stale_code_controls | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_claude_external.py::test_claude_continue_catalog_failure_is_incomplete_not_old_owner | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_claude_external.py::test_claude_continue_empty_catalog_retries_without_caching | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_claude_external.py::test_claude_continue_never_guesses_from_old_watched_subset | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_claude_external.py::test_claude_continue_uses_native_latest_and_sticks_until_process_exit | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_claude_external.py::test_claude_process_scan_assigns_unqualified_owner_for_unique_cwd | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_claude_external.py::test_claude_process_scan_bare_resume_uses_unique_cwd_fallback | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_claude_external.py::test_claude_process_scan_does_not_fan_out_ambiguous_cwd_owner | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_claude_external.py::test_claude_process_scan_never_reassigns_explicit_unknown_session | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_claude_external.py::test_claude_process_scan_recognizes_native_installer_and_ignores_daemon | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_claude_external.py::test_claude_process_scan_scopes_identical_uuid_by_config_dir | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_claude_external.py::test_claude_process_scan_tracks_explicit_owner_only | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_claude_external.py::test_claude_profile_scan_fails_closed_when_environment_is_unreadable | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_claude_external.py::test_claude_takeover_adopts_only_completed_native_controls | AssertionError: assert ClaudeControl...d_tokens=None) == ClaudeControl...tokens=500000) |
| tests/test_claude_external.py::test_claude_takeover_signals_only_same_identity_and_uid | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_claude_external.py::test_cost_state_during_effort_reconnect_does_not_cancel_query[False] | assert 0 == 1 |
| tests/test_claude_external.py::test_cost_state_during_effort_reconnect_does_not_cancel_query[True] | assert 0 == 1 |
| tests/test_claude_external.py::test_failed_claude_control_plane_forces_resume_before_next_query | assert 0 == 1 |
| tests/test_claude_external.py::test_failed_claude_message_pump_forces_resume_before_next_query | assert 0 == 1 |
| tests/test_claude_external.py::test_failed_claude_query_never_rebaselines_an_ambiguous_append | AssertionError: assert 0 > 0 |
| tests/test_claude_external.py::test_first_claude_session_id_capture_starts_owner_watch | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_external.py::test_managed_claude_alias_probe_rejects_transcript_inode_replacement | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_external.py::test_managed_claude_alias_probe_skips_tool_result_user_envelope | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_external.py::test_managed_claude_alias_probe_waits_for_complete_jsonl_row | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_external.py::test_managed_claude_transcript_user_binds_before_sdk_replay | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_external.py::test_short_external_append_is_reloaded_before_claude_query | assert 0 == 1 |
| tests/test_claude_external.py::test_unknown_growth_does_not_restore_stale_native_controls | assert 0 == 1 |
| tests/test_claude_forks.py::test_begin_rejects_invalid_controls_without_mutating_the_journal | AssertionError: Regex pattern did not match. |
| tests/test_claude_forks.py::test_capacity_compacts_terminal_alias_group_but_keeps_unresolved | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_forks.py::test_concurrent_refreshes_have_one_submission_owner | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_forks.py::test_find_claude_fork_fails_closed_on_ambiguous_marker | AssertionError: Regex pattern did not match. |
| tests/test_claude_forks.py::test_find_claude_fork_rejects_wrong_cwd_corrupt_tail_and_list_error | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_forks.py::test_find_claude_fork_requires_exact_marker_and_raw_boundary | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_forks.py::test_fork_journal_preserves_pending_autocompact_transaction | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_forks.py::test_get_canonical_follows_alias_and_returns_an_independent_copy | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_forks.py::test_journal_load_rejects_orphan_alias_and_oversized_file | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_forks.py::test_journal_persists_claim_uncertain_and_complete | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_forks.py::test_profile_migration_revision_prevents_swap_replay | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_forks.py::test_reject_resolves_whole_alias_group_and_is_durable | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_forks.py::test_same_unresolved_identity_aliases_to_canonical_marker | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_live_context.py::test_running_context_cannot_bypass_an_unavailable_writer[broker] | AssertionError: assert False |
| tests/test_claude_live_context.py::test_running_context_cannot_bypass_an_unavailable_writer[external] | AssertionError: assert False |
| tests/test_claude_live_context.py::test_running_context_cannot_bypass_an_unavailable_writer[poisoned] | AssertionError: assert False |
| tests/test_claude_live_context.py::test_running_context_cannot_bypass_an_unavailable_writer[stale] | AssertionError: assert False |
| tests/test_claude_models.py::test_code_catalog_reports_native_default_without_inventing_effort | AttributeError: 'Error' object has no attribute 'default_model' |
| tests/test_claude_models.py::test_probe_only_initializes_and_always_reaps_child[True-cancel] | AssertionError: assert (33206 & 511) == 384 |
| tests/test_claude_models.py::test_probe_only_initializes_and_always_reaps_child[True-error] | AssertionError: assert (33206 & 511) == 384 |
| tests/test_claude_models.py::test_probe_only_initializes_and_always_reaps_child[True-success] | AssertionError: assert (33206 & 511) == 384 |
| tests/test_claude_models.py::test_probe_only_initializes_and_always_reaps_child[True-timeout] | AssertionError: assert (33206 & 511) == 384 |
| tests/test_claude_models.py::test_work_catalog_never_resolves_code_defaults | AttributeError: 'Error' object has no attribute 'models' |
| tests/test_claude_native_permissions.py::test_late_native_consumer_and_control_ack_cannot_revert_newer_mode | AssertionError: assert None == 'default' |
| tests/test_claude_native_permissions.py::test_targeted_hello_cannot_acknowledge_pending_permission_broadcast[bypassPermissions-plan] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_native_permissions.py::test_targeted_hello_cannot_acknowledge_pending_permission_broadcast[plan-bypassPermissions] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_claude_btw_inherits_parent_permission_before_connect | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_claude_btw_launches_with_parent_applied_autocompact | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_claude_code_resume_migrates_saved_curated_model_to_native_1m_marker | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_claude_code_resume_recovers_native_curated_model_before_history | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_claude_code_resume_without_override_never_reads_fresh_default | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_claude_new_session_defaults_use_settings_without_session_probe | AttributeError: 'Error' object has no attribute 'models' |
| tests/test_claude_permission_state.py::test_claude_profile_work_btw_keeps_config_and_policy_account | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_claude_permission_state.py::test_claude_work_btw_reuses_registered_policy_and_work_identity | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_claude_work_resume_never_applies_code_fresh_default | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_claude_permission_state.py::test_client_hello_reseeds_claude_permission_authoritatively | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_cold_claude_resume_restores_private_remote_controls | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_claude_permission_state.py::test_cold_resume_keeps_pending_lower_window_out_of_launch | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_claude_permission_state.py::test_cold_resume_without_control_record_uses_native_autocompact | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_explicit_claude_model_failure_is_one_correlated_create_error | IndexError: list index out of range |
| tests/test_claude_permission_state.py::test_explicit_fresh_claude_model_wins_without_reading_default | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_explicit_new_session_normalizes_curated_1m_models[claude-fable-5-1-claude-fable-5-1[1m]] | assert 0 == 1 |
| tests/test_claude_permission_state.py::test_explicit_new_session_normalizes_curated_1m_models[claude-mythos-5-1-claude-mythos-5-1[1m]] | assert 0 == 1 |
| tests/test_claude_permission_state.py::test_explicit_new_session_normalizes_curated_1m_models[claude-opus-5-claude-opus-5[1m]] | assert 0 == 1 |
| tests/test_claude_permission_state.py::test_explicit_new_session_normalizes_curated_1m_models[claude-opus-5[1m]-claude-opus-5[1m]] | assert 0 == 1 |
| tests/test_claude_permission_state.py::test_explicit_new_session_normalizes_curated_1m_models[claude-sonnet-5-claude-sonnet-5] | assert 0 == 1 |
| tests/test_claude_permission_state.py::test_explicit_new_session_normalizes_curated_1m_models[opus-opus[1m]] | assert 0 == 1 |
| tests/test_claude_permission_state.py::test_explicit_new_session_normalizes_curated_1m_models[opus[1m]-opus[1m]] | assert 0 == 1 |
| tests/test_claude_permission_state.py::test_explicit_new_session_normalizes_curated_1m_models[provider-custom-model-provider-custom-model] | assert 0 == 1 |
| tests/test_claude_permission_state.py::test_fresh_claude_spawn_inherits_native_defaults[None-code] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_fresh_claude_spawn_inherits_native_defaults[None-work] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_fresh_claude_spawn_inherits_native_defaults[acceptEdits-code] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_fresh_claude_spawn_inherits_native_defaults[acceptEdits-work] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_fresh_claude_spawn_inherits_native_defaults[auto-code] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_fresh_claude_spawn_inherits_native_defaults[auto-work] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_fresh_claude_spawn_inherits_native_defaults[bypassPermissions-code] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_fresh_claude_spawn_inherits_native_defaults[bypassPermissions-work] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_fresh_claude_spawn_inherits_native_defaults[default-code] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_fresh_claude_spawn_inherits_native_defaults[default-work] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_fresh_claude_spawn_inherits_native_defaults[plan-code] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_fresh_claude_spawn_inherits_native_defaults[plan-work] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_permission_state.py::test_implicit_claude_default_and_probe_failure_emit_no_fake_model | assert 0 == 1 |
| tests/test_claude_permission_state.py::test_implicit_claude_default_failure_reports_probed_provider_model | assert 0 == 1 |
| tests/test_claude_permission_state.py::test_new_claude_session_emits_authoritative_permission | assert 0 == 1 |
| tests/test_claude_permission_state.py::test_provider_native_selection_roundtrips_the_private_store | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_claude_permission_state.py::test_switching_to_resident_claude_reseeds_its_actual_permission | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_profiles.py::test_claude_catalog_samples_activity_after_async_reads[None-broker] | TimeoutError |
| tests/test_claude_profiles.py::test_claude_catalog_samples_activity_after_async_reads[None-presentation] | TimeoutError |
| tests/test_claude_profiles.py::test_claude_catalog_samples_activity_after_async_reads[idle-broker] | TimeoutError |
| tests/test_claude_profiles.py::test_claude_catalog_samples_activity_after_async_reads[idle-presentation] | TimeoutError |
| tests/test_claude_profiles.py::test_claude_catalog_samples_activity_after_async_reads[running-broker] | TimeoutError |
| tests/test_claude_profiles.py::test_claude_catalog_samples_activity_after_async_reads[running-presentation] | TimeoutError |
| tests/test_claude_profiles.py::test_cold_viewer_scope_reads_the_owning_claude_catalog_without_resuming[False] | ValueError: session profile migration is incomplete |
| tests/test_claude_profiles.py::test_cold_viewer_scope_reads_the_owning_claude_catalog_without_resuming[True] | ValueError: session profile migration is incomplete |
| tests/test_claude_profiles.py::test_completed_topology_recovers_a_crash_before_pending_unlink | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_claude_profiles.py::test_explicit_catalog_can_exclude_git_worktrees_for_continue_resolution | AssertionError: assert set() == {'11111111-11...111111111111'} |
| tests/test_claude_profiles.py::test_initial_single_account_baseline_survives_bad_optional_controls | assert False is True |
| tests/test_claude_profiles.py::test_machine_lists_duplicate_native_ids_in_both_claude_accounts | StopIteration |
| tests/test_claude_profiles.py::test_macos_default_profile_file_survives_an_older_launchagent | assert '' == '{"personal":...\\\company"}}' |
| tests/test_claude_profiles.py::test_persisted_bootstrap_route_is_translated_before_current_resolution | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_claude_profiles.py::test_presentation_failure_keeps_claude_topology_replay_marker | assert False is True |
| tests/test_claude_profiles.py::test_private_btw_profile_revision_prevents_swap_replay | RuntimeError: private btw state could not be persisted |
| tests/test_claude_profiles.py::test_topology_rejects_reusing_an_id_for_another_account | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_claude_profiles.py::test_topology_tracks_account_identity_by_config_dir | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_claude_rate_limits.py::test_claude_allowed_without_reset_clears_rejection_state | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_claude_rate_limit_store_handles_specialized_expiry_and_bad_usage | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_claude_rate_limit_store_sanitizes_and_replays_windows | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_claude_rejection_without_reset_or_usage_remains_visible | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_hello_reseeds_unexpired_claude_limits_without_a_model_probe[False] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_rate_limits.py::test_hello_reseeds_unexpired_claude_limits_without_a_model_probe[True] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_rate_limits.py::test_machine_keeps_rate_limits_inside_claude_profile[False] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_rate_limits.py::test_machine_keeps_rate_limits_inside_claude_profile[True] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_rate_limits.py::test_machine_publishes_sdk_rate_limits_to_every_resident_claude_session[False] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_rate_limits.py::test_machine_publishes_sdk_rate_limits_to_every_resident_claude_session[True] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_rate_limits.py::test_malformed_unified_shapes_retain_legacy_fields[None] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_malformed_unified_shapes_retain_legacy_fields[invalid] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_malformed_unified_shapes_retain_legacy_fields[unified1] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_malformed_unified_shapes_retain_legacy_fields[unified3] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_native_unified_windows_survive_sdk_projection_and_cache_reload[False] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_native_unified_windows_survive_sdk_projection_and_cache_reload[True] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_unified_rejection_applies_only_to_its_own_window[five_hour] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_unified_rejection_applies_only_to_its_own_window[seven_day] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_unified_rejection_applies_only_to_its_own_window[seven_day_opus] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_unified_utilization_is_validated_without_guessing_units[-0.1] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_unified_utilization_is_validated_without_guessing_units[0.5] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_unified_utilization_is_validated_without_guessing_units[1.1] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_unified_utilization_is_validated_without_guessing_units[False] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_unified_utilization_is_validated_without_guessing_units[None] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_unified_utilization_is_validated_without_guessing_units[inf] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_unified_utilization_is_validated_without_guessing_units[nan] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_unified_windows_do_not_require_a_known_top_level_limit[None] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_unified_windows_do_not_require_a_known_top_level_limit[future-window] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_unified_windows_do_not_require_a_known_top_level_limit[overage] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rate_limits.py::test_unified_windows_expire_independently_and_keep_unmentioned_buckets | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_rewind.py::test_broker_owned_combined_rewind_fails_before_mutating_files | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_claude_combined_rewind_never_touches_files_after_stale_target | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_claude_combined_rewind_orders_conversation_before_files | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_claude_rewind_is_rejected_when_stale_context_reload_fails | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_claude_rewind_reloads_stale_context_before_native_mutation | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_claude_rewind_remains_successful_when_only_reconnect_fails | AssertionError: assert False |
| tests/test_claude_rewind.py::test_claude_stale_target_is_rewound_newest_to_selected_before_files | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_conversation_rewind_surfaces_structured_native_failure | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_guarded_rewind_is_correlated_idempotent_and_reports_partial_success[None-both] | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_guarded_rewind_is_correlated_idempotent_and_reports_partial_success[None-conversation] | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_guarded_rewind_is_correlated_idempotent_and_reports_partial_success[None-files] | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_guarded_rewind_is_correlated_idempotent_and_reports_partial_success[file_failure-both] | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_guarded_rewind_is_correlated_idempotent_and_reports_partial_success[file_failure-conversation] | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_guarded_rewind_is_correlated_idempotent_and_reports_partial_success[file_failure-files] | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_guarded_rewind_is_correlated_idempotent_and_reports_partial_success[missing_files-both] | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_guarded_rewind_is_correlated_idempotent_and_reports_partial_success[missing_files-conversation] | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_guarded_rewind_is_correlated_idempotent_and_reports_partial_success[missing_files-files] | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_guarded_rewind_is_correlated_idempotent_and_reports_partial_success[stale-both] | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_guarded_rewind_is_correlated_idempotent_and_reports_partial_success[stale-conversation] | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_rewind.py::test_guarded_rewind_is_correlated_idempotent_and_reports_partial_success[stale-files] | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_session_fork.py::test_ack_loss_retry_never_overwrites_user_renamed_child | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_session_fork.py::test_ambiguous_sdk_exception_is_not_acked_and_retry_only_reconciles | Failed: DID NOT RAISE _ForkOutcomeUncertain |
| tests/test_claude_session_fork.py::test_background_recovery_retries_after_one_journal_scan_error | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_session_fork.py::test_claude_fork_child_delete_lifecycle_survives_restart | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_session_fork.py::test_claude_fork_inherits_parent_model_and_permission_once | AssertionError: assert None == 'claude-opus-5[1m]' |
| tests/test_claude_session_fork.py::test_claude_fork_journal_never_compacts_deleted_replay_tombstone | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_session_fork.py::test_claude_handler_passes_exact_sdk_boundary_then_renames_and_lists | AssertionError: assert False |
| tests/test_claude_session_fork.py::test_cold_claude_source_uses_session_info_cwd_without_spawning | AssertionError: assert [] == [('aaaaaaaa-a...k:request-1')] |
| tests/test_claude_session_fork.py::test_invalid_cutoff_is_terminal_rejection_and_is_acked | assert 0 == 1 |
| tests/test_claude_session_fork.py::test_new_request_for_same_submitted_identity_aliases_without_refork | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_session_fork.py::test_recovery_does_not_require_parent_session_info[complete-raises] | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_session_fork.py::test_recovery_does_not_require_parent_session_info[submitted-missing] | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_session_fork.py::test_same_reliable_request_replays_result_without_second_sdk_fork | assert 0 == 1 |
| tests/test_claude_session_fork.py::test_submitted_marker_is_recovered_without_repeating_sdk_fork | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_claude_steer_attachments.py::test_abnormal_turn_files_are_removed_after_native_close[disconnect-False] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steer_attachments.py::test_abnormal_turn_files_are_removed_after_native_close[disconnect-True] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steer_attachments.py::test_abnormal_turn_files_are_removed_after_native_close[drain_reconnect-False] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steer_attachments.py::test_abnormal_turn_files_are_removed_after_native_close[drain_reconnect-True] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steer_attachments.py::test_abnormal_turn_files_are_removed_after_native_close[shutdown-False] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steer_attachments.py::test_abnormal_turn_files_are_removed_after_native_close[shutdown-True] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steer_attachments.py::test_failed_service_close_keeps_files_for_the_live_native_owner | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[after-False-origin0] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[after-False-origin1] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[after-True-origin0] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[after-True-origin1] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[before-False-origin0] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[before-False-origin1] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[before-True-origin0] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[before-True-origin1] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[before_request-False-origin0] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[before_request-False-origin1] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[before_request-True-origin0] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[before_request-True-origin1] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[none-False-origin0] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[none-False-origin1] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[none-True-origin0] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_autonomous_steer_waits_for_exact_echo_then_uses_normal_turn[none-True-origin1] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_steering.py::test_background_steering_cancellation_retires_only_pending_input | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_claude_storage_root.py::test_default_claude_root_remains_home_scoped | AssertionError: assert WindowsPath('C:/Users/xuhaodong/.claude') == WindowsPath('<tmp>') |
| tests/test_claude_usage.py::test_busy_session_read_and_parallel_sessions_share_one_get | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_usage.py::test_disabled_queries_never_inspect_or_run_existing_helper | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_usage.py::test_failed_or_unconfigured_read_retains_native_cache | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_usage.py::test_helper_bounds_output_time_and_private_errors[fail] | cc_remote.wrapper.claude_usage.ClaudeUsageError: usage helper configuration invalid |
| tests/test_claude_usage.py::test_helper_bounds_output_time_and_private_errors[large] | cc_remote.wrapper.claude_usage.ClaudeUsageError: usage helper configuration invalid |
| tests/test_claude_usage.py::test_helper_bounds_output_time_and_private_errors[timeout] | cc_remote.wrapper.claude_usage.ClaudeUsageError: usage helper configuration invalid |
| tests/test_claude_usage.py::test_invalid_usage_is_atomic[-1] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_usage.py::test_invalid_usage_is_atomic[101] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_usage.py::test_invalid_usage_is_atomic[5] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_usage.py::test_invalid_usage_is_atomic[None] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_usage.py::test_invalid_usage_is_atomic[True] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_usage.py::test_invalid_usage_is_atomic[nan] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_usage.py::test_monthly_clear_invalid_snapshot_and_native_updates | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_usage.py::test_monthly_usage_preserves_named_cycle_through_cache_and_protocol[windows0] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_usage.py::test_monthly_usage_preserves_named_cycle_through_cache_and_protocol[windows1] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_usage.py::test_monthly_usage_preserves_named_cycle_through_cache_and_protocol[windows2] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_usage.py::test_monthly_usage_preserves_named_cycle_through_cache_and_protocol[windows3] | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_usage.py::test_new_native_observation_wins_over_inflight_snapshot | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_claude_usage.py::test_opted_in_monthly_snapshot_is_account_scoped_and_read_only | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_usage.py::test_real_helper_receives_only_profile_binding | cc_remote.wrapper.claude_usage.ClaudeUsageError: usage helper configuration invalid |
| tests/test_claude_usage.py::test_usage_percentages_and_null_windows_are_not_sdk_fractions | cc_remote.wrapper.claude_rate_limits.ClaudeRateLimitStoreError: Claude rate-limit cache could not be persisted |
| tests/test_codex_agent_activity.py::test_bad_mcp_snapshot_does_not_block_native_agent_refresh | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_agent_activity.py::test_bad_native_catalog_does_not_block_mcp_refresh[False] | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_agent_activity.py::test_bad_native_catalog_does_not_block_mcp_refresh[True] | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_agent_activity.py::test_foreign_catalog_path_and_changed_identity_never_report_live | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_codex_agent_activity.py::test_incremental_budget_and_rewrite_do_not_reuse_old_terminal | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_codex_agent_activity.py::test_legacy_native_agent_excludes_parent_context[task_complete] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_codex_agent_activity.py::test_legacy_native_agent_excludes_parent_context[task_completed] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_codex_agent_activity.py::test_native_catalog_limit_is_per_parent | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_codex_agent_activity.py::test_native_lifecycle_ignores_inherited_turn_and_unrelated_terminals[task_complete] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_codex_agent_activity.py::test_native_lifecycle_ignores_inherited_turn_and_unrelated_terminals[task_completed] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_codex_agent_activity.py::test_only_direct_agents_in_same_account_and_not_ordinary_forks | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_codex_agent_activity.py::test_oversized_parent_does_not_block_other_sessions[False] | AssertionError: assert set() == {'codex-agent:second-new'} |
| tests/test_codex_agent_activity.py::test_oversized_parent_does_not_block_other_sessions[True] | AssertionError: assert set() == {'codex-agent:second-new'} |
| tests/test_codex_agent_activity.py::test_partial_record_missing_source_and_catalog_failure_are_not_completion | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_codex_agent_activity.py::test_partial_refresh_retains_other_resident_agent_until_its_terminal | AssertionError: assert set() == {'codex-agent:second-child'} |
| tests/test_codex_agent_activity.py::test_settled_child_removed_from_disk_does_not_reappear_as_unknown | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_codex_agent_activity.py::test_stale_catalog_without_child_source_does_not_create_a_background_task | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_codex_agent_activity.py::test_wrapper_merges_native_and_mcp_and_reseeds_after_parent_completion | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_agents.py::test_only_proven_descendants_in_same_account_can_be_read | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_app_tools.py::test_discovery_rejects_two_matching_apps_and_pid_reuse | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_app_tools.py::test_discovery_requires_exact_shared_profile[same-ws://127.0.0.1:1234/rpc-True-True] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_app_tools.py::test_discovery_validates_protected_daemon_alias[None] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-app-24i1__7a\\p\\6bcc9f2f060a2471be8648587be50315cec058b869ebe7ee29b216a10c7a3a31' -> 'C:\\tmp\\cc-app-24i1__7a\\app-server-contr… |
| tests/test_codex_app_tools.py::test_discovery_validates_protected_daemon_alias[cross_account] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-app-kleddi92\\p\\adb202e3d8ae2b0c389dde779added5d41052dfdb1f6e5c9a1a24346f40d417a' -> 'C:\\tmp\\cc-app-kleddi92\\app-server-contr… |
| tests/test_codex_app_tools.py::test_discovery_validates_protected_daemon_alias[shared_directory] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-app-hvg83iwb\\p\\08f1e7d25b110fa2f30b21382f381d4c64bfa320ffd713c11286ba4015d53349' -> 'C:\\tmp\\cc-app-hvg83iwb\\app-server-contr… |
| tests/test_codex_app_tools.py::test_discovery_validates_protected_daemon_alias[shared_socket] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-app-p_aev46s\\p\\ff15ef1419b4bd060a9b7e26db876d71573eb7f6ad9f95481c2c8fb03ddf9670' -> 'C:\\tmp\\cc-app-p_aev46s\\app-server-contr… |
| tests/test_codex_app_tools.py::test_open_log_marker_must_be_an_app_owned_socket | AssertionError: assert 'None' == '/tmp/codex-b...11111111.sock' |
| tests/test_codex_app_tools.py::test_private_socket_rejects_world_access_and_symlinks | AttributeError: module 'socket' has no attribute 'AF_UNIX' |
| tests/test_codex_app_tools.py::test_runtime_update_changes_generation_without_app_or_pipe_restart | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[False-None] | AttributeError: module 'socket' has no attribute 'AF_UNIX' |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[False-ambiguous_listener] | AttributeError: module 'socket' has no attribute 'AF_UNIX' |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[False-app_reused] | AttributeError: module 'socket' has no attribute 'AF_UNIX' |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[False-bridge_reused] | AttributeError: module 'socket' has no attribute 'AF_UNIX' |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[False-duplicate_profile] | AttributeError: module 'socket' has no attribute 'AF_UNIX' |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[False-foreign_owner] | AttributeError: module 'socket' has no attribute 'AF_UNIX' |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[False-linked_daemon] | AttributeError: module 'socket' has no attribute 'AF_UNIX' |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[False-missing_daemon] | AttributeError: module 'socket' has no attribute 'AF_UNIX' |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[False-missing_listener] | AttributeError: module 'socket' has no attribute 'AF_UNIX' |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[False-private_listener] | AttributeError: module 'socket' has no attribute 'AF_UNIX' |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[False-wrong_path] | AttributeError: module 'socket' has no attribute 'AF_UNIX' |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[False-wrong_peer] | AttributeError: module 'socket' has no attribute 'AF_UNIX' |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[False-wrong_profile] | AttributeError: module 'socket' has no attribute 'AF_UNIX' |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[True-None] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-bridge-34z0txrc\\p\\10b995dbaddf153862040abfaa7a13ed3f88fda450f73b6915265fbe656988e9' -> 'C:\\tmp\\cc-bridge-34z0txrc\\app-server… |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[True-ambiguous_listener] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-bridge-sr1y5uu2\\p\\d7184ff06ef6d41d39b31bb55edd2f3492347e591ea477068f412399d7d8ea18' -> 'C:\\tmp\\cc-bridge-sr1y5uu2\\app-server… |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[True-app_reused] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-bridge-bq975z7t\\p\\d9b853773453ac8edd85e993b72edec3aab80804fd3b796d9b9759dbba3ac364' -> 'C:\\tmp\\cc-bridge-bq975z7t\\app-server… |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[True-bridge_reused] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-bridge-xf8kz6o0\\p\\8dd90924f0c4a3a20ef8fd2d1b11136e319c30170b05defb6e41314e90b0876a' -> 'C:\\tmp\\cc-bridge-xf8kz6o0\\app-server… |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[True-duplicate_profile] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-bridge-qs4s2tav\\p\\b05e87f809d9600d9258ceaa5cdb97038f59ad27055e3c5eaef138fd8a284510' -> 'C:\\tmp\\cc-bridge-qs4s2tav\\app-server… |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[True-foreign_owner] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-bridge-21yct8bd\\p\\e5bc40950e5a4b89349f88a12575a4218d32183e1e61f2140ec272c52a3251c3' -> 'C:\\tmp\\cc-bridge-21yct8bd\\app-server… |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[True-linked_daemon] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-bridge-le1_rtl4\\p\\9473bdcfe94f063392668bc89d83347009a568626ed87916d61dbebec7edb07e' -> 'C:\\tmp\\cc-bridge-le1_rtl4\\app-server… |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[True-missing_daemon] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-bridge-8cwdnltp\\p\\6bba4a2863fc6c825cf2d176a18542f39175c0505a06b3501d086c1b0aebeb0f' -> 'C:\\tmp\\cc-bridge-8cwdnltp\\app-server… |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[True-missing_listener] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-bridge-gr1ea4w4\\p\\ea180ad4e113554b4bd0c62eefd8a812fb0e6c031aa8d94a2de6948eb827360f' -> 'C:\\tmp\\cc-bridge-gr1ea4w4\\app-server… |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[True-private_listener] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-bridge-7g2ept4o\\p\\d9e0a1ab837cd0660b3513eb509d6cacb6fc7ae330245a53cb07d498b29c28f8' -> 'C:\\tmp\\cc-bridge-7g2ept4o\\app-server… |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[True-wrong_path] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-bridge-js67zizy\\p\\568df0bf7284c3ccf156e2949233e82bdf972ae738ed7b0f60e5cf59a4eb032f' -> 'C:\\tmp\\cc-bridge-js67zizy\\app-server… |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[True-wrong_peer] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-bridge-5ur1igc0\\p\\7d0b10091d775b7b2c3a7ae6cc22abe35cd948e6a8fa1ea78eb05da6b77a031e' -> 'C:\\tmp\\cc-bridge-5ur1igc0\\app-server… |
| tests/test_codex_app_tools.py::test_shared_app_requires_profile_bound_bridge_and_both_connection_ends[True-wrong_profile] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-bridge-xakzk84l\\p\\1f58dce6ca3ab75b318c9b039a079e553acae6d7e5997326857282848d7766d6' -> 'C:\\tmp\\cc-bridge-xakzk84l\\app-server… |
| tests/test_codex_archives.py::test_byte_bound_batch_evicts_without_reserializing_whole_journal | cc_remote.wrapper.codex_archives.CodexArchiveJournalError: Codex archive journal could not be persisted |
| tests/test_codex_archives.py::test_byte_bound_evicts_oldest_terminal_entry_and_survives_restart | cc_remote.wrapper.codex_archives.CodexArchiveJournalError: Codex archive journal could not be persisted |
| tests/test_codex_archives.py::test_byte_bound_fails_without_mutating_when_no_terminal_entry_is_evictable | cc_remote.wrapper.codex_archives.CodexArchiveJournalError: Codex archive journal could not be persisted |
| tests/test_codex_archives.py::test_completed_archive_accepts_root_with_best_effort_descendant | cc_remote.wrapper.codex_archives.CodexArchiveJournalError: Codex archive journal could not be persisted |
| tests/test_codex_archives.py::test_completed_archive_must_include_root | cc_remote.wrapper.codex_archives.CodexArchiveJournalError: Codex archive journal could not be persisted |
| tests/test_codex_archives.py::test_submitted_archive_survives_restart_without_second_claim | cc_remote.wrapper.codex_archives.CodexArchiveJournalError: Codex archive journal could not be persisted |
| tests/test_codex_archives.py::test_terminal_transition_growth_batch_evicts_older_results | cc_remote.wrapper.codex_archives.CodexArchiveJournalError: Codex archive journal could not be persisted |
| tests/test_codex_checkpoints.py::test_chmod_only_turn_is_checkpointed_and_restored | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_cleanup_session_removes_journals_from_every_repository | cc_remote.wrapper.codex_checkpoints.CheckpointError: Unable to retire Codex checkpoint journal |
| tests/test_codex_checkpoints.py::test_crash_left_partial_restore_is_recovered_without_overwriting_third_state | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_crash_recovery_only_slots_durably_accepted_turns | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_dirty_baseline_round_trip_preserves_index_and_file_kinds | OSError: [WinError 1314] 客户端没有所需的特权。: 'target-a' -> '<tmp>' |
| tests/test_codex_checkpoints.py::test_finish_accepts_commit_with_unchanged_untracked_baseline[False] | OSError: [WinError 1314] 客户端没有所需的特权。: 'tracked.txt' -> '<tmp>' |
| tests/test_codex_checkpoints.py::test_finish_accepts_commit_with_unchanged_untracked_baseline[True] | OSError: [WinError 1314] 客户端没有所需的特权。: 'tracked.txt' -> '<tmp>' |
| tests/test_codex_checkpoints.py::test_finish_accepts_committed_untracked_file_with_other_baseline_leftovers | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_finish_classifies_clean_commit_as_conversation_only | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_finish_classifies_clean_empty_commit_as_conversation_only | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_finish_rejects_agent_index_changes_and_discards_active_record | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_finish_rejects_commit_followed_by_dirty_worktree | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_finish_rejects_commit_with_changed_untracked_baseline[content] | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_finish_rejects_commit_with_changed_untracked_baseline[delete] | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_finish_rejects_commit_with_changed_untracked_baseline[new] | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_finish_rejects_commit_with_changed_untracked_baseline[permission] | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_finish_rejects_commit_with_changed_untracked_baseline[stage] | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_finish_rejects_commit_with_changed_untracked_baseline[symlink] | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_finish_rejects_committed_deletion_that_leaves_a_tracked_file_untracked | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_finish_rejects_pure_staging_change_with_unchanged_worktree | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_git_boundary_abort_and_cleanup_are_explicit | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_non_consuming_restore_stays_aligned_until_conversation_discard | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_persistent_journal_supports_single_and_multi_turn_rollback | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_restore_manifest_failure_rolls_worktree_back_to_post_image | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_retention_bounds_turn_window_and_invalidates_oversize_objects | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_rollback_conflict_is_all_or_nothing_for_files_and_index | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_checkpoints.py::test_rollback_restores_exact_noncanonical_file_permissions | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_client_messages.py::test_codex_client_message_alias_accepts_live_and_history_native_ids | cc_remote.wrapper.codex_client_messages.CodexClientMessageStoreError: Codex client-message store could not be persisted |
| tests/test_codex_client_messages.py::test_codex_client_message_alias_delete_uses_captured_path | cc_remote.wrapper.codex_client_messages.CodexClientMessageStoreError: Codex client-message store could not be persisted |
| tests/test_codex_client_messages.py::test_codex_client_message_alias_is_source_inode_bound | cc_remote.wrapper.codex_client_messages.CodexClientMessageStoreError: Codex client-message store could not be persisted |
| tests/test_codex_client_messages.py::test_codex_client_message_alias_rejects_conflicting_identity | cc_remote.wrapper.codex_client_messages.CodexClientMessageStoreError: Codex client-message store could not be persisted |
| tests/test_codex_client_messages.py::test_codex_client_message_alias_round_trip_and_upgrade | cc_remote.wrapper.codex_client_messages.CodexClientMessageStoreError: Codex client-message store could not be persisted |
| tests/test_codex_context_settings.py::test_capacity_command_saves_session_limit_and_preserves_live_configuration[False-False] | AssertionError: assert (400000 == 300000) |
| tests/test_codex_context_settings.py::test_capacity_command_saves_session_limit_and_preserves_live_configuration[True-False] | AssertionError: assert (400000 == 300000) |
| tests/test_codex_context_settings.py::test_legacy_setting_migrates_capacity_without_changing_the_users_number | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store is not a private bounded file |
| tests/test_codex_context_settings.py::test_preferences_survive_restart_other_controls_and_reset | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_context_settings.py::test_repaired_window_persists_only_after_apply_without_overwriting_newer_choice[False-400000-421053] | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_context_settings.py::test_repaired_window_persists_only_after_apply_without_overwriting_newer_choice[False-None-315790] | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_context_settings.py::test_repaired_window_persists_only_after_apply_without_overwriting_newer_choice[True-None-333334] | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_control_store.py::test_codex_control_store_clears_only_the_expected_cwd_override | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_control_store.py::test_codex_control_store_loads_legacy_search_only_entry | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store is not a private bounded file |
| tests/test_codex_control_store.py::test_codex_control_store_preserves_explicit_cwd_across_control_updates | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_control_store.py::test_codex_control_store_restores_a_post_replace_failed_cwd_write | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_control_store.py::test_codex_control_store_round_trips_and_clears_override | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_controls.py (whole module) | TIMEOUT: module did not finish within 240 s (also hangs on the pre-change base) |
| tests/test_codex_daemon.py::test_daemon_capability_cache_invalidates_on_binary_change | assert False is True |
| tests/test_codex_daemon.py::test_daemon_manager_starts_enables_versions_and_reconnects | AssertionError: assert None == ['/bin/codex', 'app-server', 'proxy', '--sock', '/tmp/codex.sock'] |
| tests/test_codex_daemon.py::test_exact_zombie_daemon_updater_gets_sigterm_only | AssertionError: assert False is True |
| tests/test_codex_daemon.py::test_existing_official_app_server_is_exposed_for_proxy_validation | AssertionError: assert None == ['/bin/codex', 'app-server', 'proxy', '--sock', '/tmp/codex.sock'] |
| tests/test_codex_daemon.py::test_lagging_managed_daemon_restart_failure_never_uses_stdio | Failed: DID NOT RAISE CodexDaemonUpgradeRequired |
| tests/test_codex_daemon.py::test_lagging_managed_daemon_restarts_before_shared_proxy | AssertionError: assert None == ['/bin/codex', 'app-server', 'proxy', '--sock', '/tmp/codex.sock'] |
| tests/test_codex_daemon.py::test_lagging_managed_daemon_waits_for_async_replacement | AssertionError: assert None == ['/bin/codex', 'app-server', 'proxy', '--sock', '/tmp/codex.sock'] |
| tests/test_codex_daemon.py::test_linux_managed_daemon_nofile_fails_closed_on_low_hard_limit | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_daemon.py::test_linux_managed_daemon_nofile_is_applied_to_exact_pid[ambiguous-True-False] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_daemon.py::test_linux_managed_daemon_nofile_is_applied_to_exact_pid[default-True-True] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_daemon.py::test_linux_managed_daemon_nofile_is_applied_to_exact_pid[local-False-False] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_daemon.py::test_linux_managed_daemon_nofile_is_applied_to_exact_pid[local-True-True] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_daemon.py::test_linux_managed_daemon_nofile_is_applied_to_exact_pid[other-True-False] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_daemon.py::test_linux_managed_daemon_nofile_is_applied_to_exact_pid[remote-False-True] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_daemon.py::test_managed_daemon_identity_uses_same_user_pid_and_start_token | AssertionError: assert None == ProcessIdentity(pid=4321, start_ticks=987654) |
| tests/test_codex_daemon.py::test_managed_identity_cannot_downgrade_after_pid_disappears[native-alias] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_managed_identity_cannot_downgrade_after_pid_disappears[socket] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_oversized_http_resume_keeps_shared_affinity_and_thread_local_provider | AttributeError: <module 'os' (frozen)> has no attribute 'killpg' |
| tests/test_codex_daemon.py::test_profile_bootstrap_atomically_advances_owned_official_current | OSError: [WinError 1314] 客户端没有所需的特权。: 'bin/codex' -> '<tmp>' |
| tests/test_codex_daemon.py::test_profile_bootstrap_never_replaces_existing_managed_path | OSError: [WinError 1314] 客户端没有所需的特权。: 'bin/codex' -> '<tmp>' |
| tests/test_codex_daemon.py::test_profile_bootstrap_never_replaces_external_current_symlink | OSError: [WinError 1314] 客户端没有所需的特权。: 'bin/codex' -> '<tmp>' |
| tests/test_codex_daemon.py::test_profile_bootstrap_reuses_verified_managed_standalone | OSError: [WinError 1314] 客户端没有所需的特权。: 'bin/codex' -> '<tmp>' |
| tests/test_codex_daemon.py::test_proxy_connect_exposes_shared_state_and_disconnect_keeps_manager | AttributeError: <module 'os' (frozen)> has no attribute 'killpg' |
| tests/test_codex_daemon.py::test_proxy_connect_rebinds_resume_if_daemon_changes_after_thread_read | AttributeError: <module 'os' (frozen)> has no attribute 'killpg' |
| tests/test_codex_daemon.py::test_proxy_connect_resumes_created_thread_if_sticky_settings_disconnect | AttributeError: <module 'os' (frozen)> has no attribute 'killpg' |
| tests/test_codex_daemon.py::test_proxy_connect_retries_if_daemon_changes_during_initialize | AttributeError: <module 'os' (frozen)> has no attribute 'killpg' |
| tests/test_codex_daemon.py::test_proxy_handshake_failure_falls_back_to_stdio | AttributeError: <module 'os' (frozen)> has no attribute 'killpg' |
| tests/test_codex_daemon.py::test_real_manager_standalone_connect_and_restart_fences[native-alias-None] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_real_manager_standalone_connect_and_restart_fences[native-alias-initialize] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_real_manager_standalone_connect_and_restart_fences[native-alias-thread/resume] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_real_manager_standalone_connect_and_restart_fences[socket-None] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_real_manager_standalone_connect_and_restart_fences[socket-initialize] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_real_manager_standalone_connect_and_restart_fences[socket-thread/resume] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_required_profile_bootstraps_its_own_shared_daemon | cc_remote.wrapper.codex_daemon.CodexProfileDaemonUnavailable: Codex profile shared daemon commands are unavailable |
| tests/test_codex_daemon.py::test_required_profile_never_silently_falls_back_to_stdio | AssertionError: assert [] == [('app-server...ersion'), ...] |
| tests/test_codex_daemon.py::test_stale_darwin_updater_is_recovered_before_generic_restart | AssertionError: assert None == ['/bin/codex', 'app-server', 'proxy', '--sock', '/tmp/codex.sock'] |
| tests/test_codex_daemon.py::test_stale_managed_daemon_uses_official_restart_when_not_exact | AssertionError: assert None == ['/bin/codex', 'app-server', 'proxy', '--sock', '/tmp/codex.sock'] |
| tests/test_codex_daemon.py::test_standalone_generation_does_not_ignore_unverifiable_pid_record[native-alias-corrupt] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_does_not_ignore_unverifiable_pid_record[native-alias-fifo] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_does_not_ignore_unverifiable_pid_record[native-alias-symlink] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_does_not_ignore_unverifiable_pid_record[socket-corrupt] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_does_not_ignore_unverifiable_pid_record[socket-fifo] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_does_not_ignore_unverifiable_pid_record[socket-symlink] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_rejects_unsafe_or_cross_profile_socket[native-alias-other_profile] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_rejects_unsafe_or_cross_profile_socket[native-alias-override] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_rejects_unsafe_or_cross_profile_socket[native-alias-owner] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_rejects_unsafe_or_cross_profile_socket[native-alias-parent_permissions] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_rejects_unsafe_or_cross_profile_socket[native-alias-regular_file] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_rejects_unsafe_or_cross_profile_socket[native-alias-socket_permissions] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_rejects_unsafe_or_cross_profile_socket[native-alias-symlink] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_rejects_unsafe_or_cross_profile_socket[socket-other_profile] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_rejects_unsafe_or_cross_profile_socket[socket-override] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_rejects_unsafe_or_cross_profile_socket[socket-owner] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_rejects_unsafe_or_cross_profile_socket[socket-parent_permissions] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_rejects_unsafe_or_cross_profile_socket[socket-regular_file] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_rejects_unsafe_or_cross_profile_socket[socket-socket_permissions] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_rejects_unsafe_or_cross_profile_socket[socket-symlink] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_survives_invalidate_and_detects_replacement[native-alias] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_standalone_generation_survives_invalidate_and_detects_replacement[socket] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_unavailable_pid_is_not_reported_as_a_confirmed_restart[native-alias] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_unavailable_pid_is_not_reported_as_a_confirmed_restart[socket] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_daemon.py::test_unrecoverable_stale_daemon_still_falls_back_to_stdio | AssertionError: assert [] == [('app-server...', 'restart')] |
| tests/test_codex_daemon.py::test_verified_shared_proxy_failure_never_falls_back_to_stdio | AttributeError: <module 'os' (frozen)> has no attribute 'killpg' |
| tests/test_codex_daemon_restart.py::test_restart_state_round_trip_is_private_and_rejects_malformed | AssertionError: assert (33206 & 511) == 384 |
| tests/test_codex_desktop.py::test_bridge_peer_requires_exact_app_profile_and_process[other-app-same-same-False-False] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_desktop.py::test_bridge_peer_requires_exact_app_profile_and_process[self-app-other-same-False-False] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_desktop.py::test_bridge_peer_requires_exact_app_profile_and_process[self-app-same-other-False-False] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_desktop.py::test_bridge_peer_requires_exact_app_profile_and_process[self-app-same-same-False-True] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_desktop.py::test_bridge_peer_requires_exact_app_profile_and_process[self-app-same-same-True-False] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_desktop.py::test_bridge_peer_requires_exact_app_profile_and_process[self-other-same-same-False-False] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_desktop.py::test_dock_append_is_idempotent_and_preserves_other_preferences | cc_remote.codex_desktop.LaunchError: 共享入口的状态目录不是当前用户的私有目录。 |
| tests/test_codex_desktop.py::test_fresh_launch_then_quit_then_reopen | cc_remote.codex_desktop.LaunchError: 共享入口的状态目录不是当前用户的私有目录。 |
| tests/test_codex_desktop.py::test_install_separate_bundle_and_never_overwrites[arm64] | cc_remote.codex_desktop.LaunchError: 共享入口的状态目录不是当前用户的私有目录。 |
| tests/test_codex_desktop.py::test_install_separate_bundle_and_never_overwrites[x86_64] | cc_remote.codex_desktop.LaunchError: 共享入口的状态目录不是当前用户的私有目录。 |
| tests/test_codex_desktop.py::test_lock_rejects_symlink_and_writable_file | cc_remote.codex_desktop.LaunchError: 共享入口的状态目录不是当前用户的私有目录。 |
| tests/test_codex_desktop.py::test_lock_survives_stale_inode_and_serializes_profiles | cc_remote.codex_desktop.LaunchError: 共享入口的状态目录不是当前用户的私有目录。 |
| tests/test_codex_desktop.py::test_real_proxy_roundtrip_and_reject_self_after_preflight[False] | NotImplementedError |
| tests/test_codex_desktop.py::test_real_proxy_roundtrip_and_reject_self_after_preflight[True] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\cc-launch-6a7_n36y\\p\\319534deb2b7e6e4198baf24a3c0a367833dd188fe46eb765f5f8add5ec74610' -> 'C:\\tmp\\cc-launch-6a7_n36y\\app-server… |
| tests/test_codex_external.py::test_active_passive_writer_prevents_orphan_timeout_until_writer_exits | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_codex_app_server_client_socket_routes_implicit_profile_home | AssertionError: assert (False, None) == (True, 'C:\\U...control.sock') |
| tests/test_codex_external.py::test_codex_app_server_proxy_socket_requires_one_absolute_socket | AssertionError: assert None == 'C:\\tmp\\codex-stack.sock' |
| tests/test_codex_external.py::test_codex_holder_locks_and_unlocks_without_ttl | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_codex_resume_tui_matches_only_explicit_target_sid | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_external.py::test_codex_takeover_queues_until_external_turn_finishes | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_codex_takeover_relocks_for_new_holder | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_codex_takeover_relocks_when_captured_holder_turn_was_pending | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_codex_takeover_relocks_when_ignored_holder_starts_new_turn | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_completed_wrapper_turn_does_not_hide_external_short_turn | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_external_turn_mirrors_and_marks_context_stale | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_final_preflight_cancels_if_transcript_changes_during_reload | assert 1 == 2 |
| tests/test_codex_external.py::test_final_preflight_reloads_short_external_turn_before_query | assert 1 == 2 |
| tests/test_codex_external.py::test_foreign_marker_during_turn_start_is_not_claimed_by_local_response | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_headless_app_server_holder_is_classified_passive | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_external.py::test_headless_daemon_and_stdio_proxy_are_both_passive | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_external.py::test_idle_codex_resume_tui_is_a_logical_interactive_holder | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_external.py::test_interrupt_during_second_final_preflight_never_starts_query | assert 1 == 2 |
| tests/test_codex_external.py::test_interrupted_rollout_terminal_commits_after_quiet_grace_even_with_writer | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_interrupted_rollout_terminal_drops_proven_compaction_continuation | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_machine_merges_exact_tui_proxy_binding | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_newer_rollout_lifecycle_retires_provisional_interrupt | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_passive_app_server_locks_only_for_active_external_turn | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_pending_attribution_cap_fails_stale | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_pending_unknown_marker_resolves_external_when_wrapper_turn_ends | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_plain_codex_tui_snapshot_binding_fails_closed_when_ambiguous | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_external.py::test_plain_codex_tui_uses_exact_startup_shell_snapshot[False] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_external.py::test_plain_codex_tui_uses_exact_startup_shell_snapshot[True] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_external.py::test_prime_consumes_short_lived_external_growth_before_query | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_prime_publishes_holder_edges_and_marks_context_stale | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_queued_takeover_cancels_when_terminal_starts_another_turn | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_queued_takeover_never_captures_a_new_holder | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_queued_takeover_waits_for_complete_holder_scan | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_rotation_marks_resident_context_stale_and_mirrors | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_running_before_turn_start_does_not_hide_external_short_turn | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_short_lived_external_turn_mirrors_without_stale_lock | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_unknown_marker_during_active_turn_becomes_external_after_grace | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_external.py::test_writable_holder_matches_inode_and_excludes_own_process | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_external.py::test_writable_holder_uses_inode_not_path_text | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_forks.py::test_completed_children_never_overrides_a_stronger_child_tombstone | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_forks.py::test_fork_child_delete_lifecycle_survives_restart_and_can_abort | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_forks.py::test_fork_journal_aliases_same_unresolved_identity_to_one_canonical | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_forks.py::test_fork_journal_compacts_complete_alias_group_atomically | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_forks.py::test_fork_journal_lists_unique_completed_results_newest_first | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_forks.py::test_fork_journal_never_compacts_deleted_replay_tombstone | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_forks.py::test_fork_journal_persists_intent_and_result_atomically | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_forks.py::test_fork_journal_rejects_orphaned_alias_on_reload | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_forks.py::test_fork_journal_serializes_two_concurrent_requests | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_forks.py::test_worktree_fork_control_snapshot_survives_journal_reload | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_generated_images.py::test_generated_image_capture_uses_existing_scoped_snapshot_only_after_success | KeyError: 'preview_id' |
| tests/test_codex_history.py::test_legacy_rollout_user_pair_reuses_live_item_identity | assert 647 == 642 |
| tests/test_codex_history.py::test_native_user_page_cursor_accepts_previous_task_cursor | assert 245 == 242 |
| tests/test_codex_launch_barrier.py::test_in_flight_launch_refuses_upgrade_without_stopping | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_launch_barrier.py::test_installer_rollback_uses_durable_stop_intent[False] | AssertionError:  |
| tests/test_codex_launch_barrier.py::test_installer_rollback_uses_durable_stop_intent[True] | AssertionError:  |
| tests/test_codex_launch_barrier.py::test_launch_arriving_after_scan_cannot_cross_stop[bootstrap] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_launch_barrier.py::test_launch_arriving_after_scan_cannot_cross_stop[enable-remote-control] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_launch_barrier.py::test_launch_arriving_after_scan_cannot_cross_stop[restart] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_launch_barrier.py::test_launch_arriving_after_scan_cannot_cross_stop[start] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_launch_barrier.py::test_launch_arriving_after_scan_cannot_cross_stop[stop] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_launch_barrier.py::test_pre_stop_failure_does_not_stop_or_arm_rollback[generation] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_launch_barrier.py::test_pre_stop_failure_does_not_stop_or_arm_rollback[inherited] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_launch_barrier.py::test_pre_stop_failure_does_not_stop_or_arm_rollback[legacy] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_launch_barrier.py::test_pre_stop_failure_does_not_stop_or_arm_rollback[lost-quiesce-reply] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_launch_barrier.py::test_stopped_service_needs_no_barrier | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_launch_barrier.py::test_uncertain_stop_keeps_barrier_closed_even_on_next_abort[lost-commit-reply] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_launch_barrier.py::test_uncertain_stop_keeps_barrier_closed_even_on_next_abort[receipt-write] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_launch_barrier.py::test_uncertain_stop_keeps_barrier_closed_even_on_next_abort[stop-timeout] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_missing_sessions.py::test_delete_orphan_never_attempts_resume_or_native_delete | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_missing_sessions.py::test_deleted_resident_is_evicted_and_all_clients_invalidated | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_missing_sessions.py::test_failed_or_symlinked_scan_cannot_prove_deletion | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_missing_sessions.py::test_live_handle_must_confirm_exact_missing_identity | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_missing_sessions.py::test_loaded_or_uncertain_native_thread_is_retained[None] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_missing_sessions.py::test_loaded_or_uncertain_native_thread_is_retained[error1] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_missing_sessions.py::test_loaded_or_uncertain_native_thread_is_retained[error2] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_missing_sessions.py::test_native_activity_during_retirement_preserves_fork_replay | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_missing_sessions.py::test_queue_submission_and_orphan_retirement_are_atomic[queue-journal] | TimeoutError |
| tests/test_codex_missing_sessions.py::test_queue_submission_and_orphan_retirement_are_atomic[queue-scan] | TimeoutError |
| tests/test_codex_missing_sessions.py::test_queue_submission_and_orphan_retirement_are_atomic[replace-journal] | TimeoutError |
| tests/test_codex_missing_sessions.py::test_queue_submission_and_orphan_retirement_are_atomic[replace-scan] | TimeoutError |
| tests/test_codex_missing_sessions.py::test_refresh_does_not_overlay_deleted_resident | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_missing_sessions.py::test_shared_proxy_change_invalidates_orphan_retirement[False-journal] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_missing_sessions.py::test_shared_proxy_change_invalidates_orphan_retirement[False-probe] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_missing_sessions.py::test_shared_proxy_change_invalidates_orphan_retirement[False-scan] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_missing_sessions.py::test_shared_proxy_change_invalidates_orphan_retirement[True-journal] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_missing_sessions.py::test_shared_proxy_change_invalidates_orphan_retirement[True-probe] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_missing_sessions.py::test_shared_proxy_change_invalidates_orphan_retirement[True-scan] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_missing_sessions.py::test_shared_session_survives_proxy_reconnect_gap[None] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_missing_sessions.py::test_shared_session_survives_proxy_reconnect_gap[exited] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_npm_ownership.py::test_independent_unresolved_tui_still_fails_closed[darwin] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_codex_npm_ownership.py::test_independent_unresolved_tui_still_fails_closed[linux] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_codex_npm_ownership.py::test_npm_scan_rechecks_process_identity_before_folding[darwin-launcher-reused] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_codex_npm_ownership.py::test_npm_scan_rechecks_process_identity_before_folding[darwin-native-reused] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_codex_npm_ownership.py::test_npm_scan_rechecks_process_identity_before_folding[linux-launcher-reused] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_codex_npm_ownership.py::test_npm_scan_rechecks_process_identity_before_folding[linux-native-reused] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_codex_npm_ownership.py::test_npm_tui_does_not_lock_unrelated_session_without_daemon_logs[darwin-no-db] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_codex_npm_ownership.py::test_npm_tui_does_not_lock_unrelated_session_without_daemon_logs[darwin-no-socket-logs] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_codex_npm_ownership.py::test_npm_tui_does_not_lock_unrelated_session_without_daemon_logs[linux-no-db] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_codex_npm_ownership.py::test_npm_tui_does_not_lock_unrelated_session_without_daemon_logs[linux-no-socket-logs] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_codex_npm_ownership.py::test_npm_tui_keeps_profile_isolation_with_unreadable_launcher_environment[darwin] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_codex_npm_ownership.py::test_npm_tui_keeps_profile_isolation_with_unreadable_launcher_environment[linux] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_codex_npm_ownership.py::test_owned_npm_server_does_not_hide_other_clients[darwin-foreign-proxy] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_npm_ownership.py::test_owned_npm_server_does_not_hide_other_clients[darwin-independent-tui] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_npm_ownership.py::test_owned_npm_server_does_not_hide_other_clients[darwin-nested-tui] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_npm_ownership.py::test_owned_npm_server_does_not_hide_other_clients[linux-foreign-proxy] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_npm_ownership.py::test_owned_npm_server_does_not_hide_other_clients[linux-independent-tui] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_npm_ownership.py::test_owned_npm_server_does_not_hide_other_clients[linux-nested-tui] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_npm_ownership.py::test_owned_npm_server_does_not_pollute_any_profile[darwin] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_npm_ownership.py::test_owned_npm_server_does_not_pollute_any_profile[linux] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_npm_ownership.py::test_wrapper_owned_npm_server_does_not_become_external[darwin-codex-proxy] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_npm_ownership.py::test_wrapper_owned_npm_server_does_not_become_external[darwin-codex-stdio] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_npm_ownership.py::test_wrapper_owned_npm_server_does_not_become_external[darwin-codex-work] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_npm_ownership.py::test_wrapper_owned_npm_server_does_not_become_external[darwin-codex.js-proxy] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_npm_ownership.py::test_wrapper_owned_npm_server_does_not_become_external[darwin-codex.js-stdio] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_npm_ownership.py::test_wrapper_owned_npm_server_does_not_become_external[darwin-codex.js-work] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_npm_ownership.py::test_wrapper_owned_npm_server_does_not_become_external[linux-codex-proxy] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_npm_ownership.py::test_wrapper_owned_npm_server_does_not_become_external[linux-codex-stdio] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_npm_ownership.py::test_wrapper_owned_npm_server_does_not_become_external[linux-codex-work] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_npm_ownership.py::test_wrapper_owned_npm_server_does_not_become_external[linux-codex.js-proxy] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_npm_ownership.py::test_wrapper_owned_npm_server_does_not_become_external[linux-codex.js-stdio] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_npm_ownership.py::test_wrapper_owned_npm_server_does_not_become_external[linux-codex.js-work] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_process_clocks.py::test_process_clock_delete_path_removes_only_that_source | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store could not be persisted |
| tests/test_codex_process_clocks.py::test_process_clock_is_source_bound_and_monotonic | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store could not be persisted |
| tests/test_codex_process_clocks.py::test_process_clock_never_crosses_replaced_rollout | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store could not be persisted |
| tests/test_codex_process_clocks.py::test_process_clock_prunes_old_records_without_touching_newest | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store could not be persisted |
| tests/test_codex_process_clocks.py::test_process_clock_retains_logical_start_across_native_handoff | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store could not be persisted |
| tests/test_codex_process_clocks.py::test_process_clock_separates_steers_inside_one_native_turn | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store could not be persisted |
| tests/test_codex_process_clocks.py::test_process_clock_store_is_private_and_fails_closed | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store could not be persisted |
| tests/test_codex_profiles.py::test_active_restart_alias_is_not_hidden_by_other_ready_marker | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_bound_tui_does_not_depend_on_structured_log | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_cli_thread_catalog_hint_dedupe_is_profile_scoped | assert 0 == 1 |
| tests/test_codex_profiles.py::test_cli_thread_catalog_hint_survives_placeholder_ttl | AssertionError: assert {('primary', ...ong-running')} == {('primary', ... 'cli-newer')} |
| tests/test_codex_profiles.py::test_codex_id_capture_broadcasts_catalog_invalidation | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_codex_profile_catalog_has_one_fair_global_budget | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_codex_profile_catalog_stays_within_one_serialized_byte_budget | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_codex_work_schedule_mutation_freezes_selected_profile | AssertionError: assert False |
| tests/test_codex_profiles.py::test_codex_work_schedule_runs_only_on_its_bound_profile | KeyError: 'codex_profile_id' |
| tests/test_codex_profiles.py::test_codex_work_wire_id_cannot_bypass_work_guard_or_artifact_lookup | AssertionError: assert 'primary@native-work-id' in frozenset() |
| tests/test_codex_profiles.py::test_cold_profile_does_not_preserve_sibling_stale_holder | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_cold_profile_ignores_sibling_proxy_without_blocking_scan | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_corrupt_rebuildable_projection_does_not_pin_profile_transition[session-plans.json-_session_plans] | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_profiles.py::test_corrupt_rebuildable_projection_does_not_pin_profile_transition[session-presentation.json-_session_presentation] | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_profiles.py::test_default_reorder_keeps_restart_markers_bound_to_original_profiles | AssertionError: assert None == 'primary' |
| tests/test_codex_profiles.py::test_disabling_and_reenabling_profiles_preserves_account_state | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_profiles.py::test_duplicate_native_uuid_logical_tui_stays_in_its_profile | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_enabling_multiple_profiles_migrates_default_profile_preferences | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_profiles.py::test_enabling_profiles_keeps_legacy_state_with_original_home_when_default_changes | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_profiles.py::test_exact_catalog_rpc_fallback_is_bounded_and_provider_filtered | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_expired_newest_restart_alias_does_not_resurrect_older_ready | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_explicit_stack_model_is_rejected_before_handle_creation | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_hidden_cli_thread_hint_materializes_in_its_own_profile | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_hidden_resident_and_fork_catalog_rows_stay_profile_scoped | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_profiles.py::test_inactive_profile_turn_lease_is_preserved_during_single_profile_startup | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_codex_profiles.py::test_inflight_catalog_send_cannot_publish_newly_private_btw | AssertionError: assert False |
| tests/test_codex_profiles.py::test_legacy_custom_codex_home_reaches_profile_boundaries | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_legacy_restart_owner_remap_is_idempotent_during_pending_transition | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_codex_profiles.py::test_missing_profile_file_is_a_controlled_single_profile_downgrade | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_profiles.py::test_native_catalog_filters_exact_private_btw_identity | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_native_start_hint_without_catalog_does_not_publish_empty_session | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_new_codex_work_session_freezes_selected_profile_before_spawn | KeyError: 'codex_profile_id' |
| tests/test_codex_profiles.py::test_new_stack_session_applies_its_catalog_default_before_connect | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_old_schema_catalog_fallback_is_batched_and_profile_scoped | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_profiles.py::test_optional_presentation_migration_does_not_disable_codex_or_work[SessionPlanStore-migrate_profile_sessions-_session_p…] | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_profiles.py::test_optional_presentation_migration_does_not_disable_codex_or_work[SessionPresentationStore-migrate_codex_profile_sessi…] | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_profiles.py::test_profile_catalog_all_failure_without_cache_returns_empty_registry_rows | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_profile_catalog_failures_preserve_the_public_account_registry | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_profile_control_and_turn_lease_keys_do_not_collide | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_profiles.py::test_profile_holder_scan_maps_every_native_bucket_back_to_wire_sid | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_profile_holder_scan_uses_matching_shell_and_tui_stores | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_profile_home_replacement_fails_closed_before_state_is_reowned | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_profiles.py::test_profile_id_cannot_silently_replace_its_account_home | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_codex_profiles.py::test_profile_id_swap_keeps_same_native_uuid_state_with_its_home | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_profiles.py::test_profile_id_swap_moves_recovery_journals_without_path_collision | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_profiles.py::test_profile_list_failure_cache_merge_keeps_old_resident | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_profile_list_failure_still_returns_resident_exact_rows | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_profile_model_reads_use_catalog_default_from_matching_home | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_profile_model_resolution_isolated_and_fails_closed_for_explicit_choice | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_profile_remap_retries_do_not_apply_completed_store_twice | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_codex_profiles.py::test_profile_rename_displaces_removed_target_without_crossing_accounts | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_profiles.py::test_profile_transition_migrates_alias_fork_and_checkpoint_recovery_state | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_profiles.py::test_profile_tui_trackers_receive_only_their_daemon_proxy | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_reloaded_picker_discovers_new_model_before_internal_cache_expires[False] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_reloaded_picker_discovers_new_model_before_internal_cache_expires[True] | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_resident_catalog_repair_survives_the_global_row_bound | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_same_home_profile_id_rename_migrates_persisted_identity | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_profiles.py::test_same_native_uuid_contexts_remain_distinct | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_unreadable_client_profile_keeps_owner_scan_incomplete | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_unreadable_logical_tui_does_not_freeze_unrelated_profile | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_profiles.py::test_v1_topology_is_upgraded_before_old_checkpoint_is_opened | KeyError: 'profile_revision' |
| tests/test_codex_profiles.py::test_work_profile_migration_failure_does_not_disable_codex_code | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_provider_repair.py::test_provider_repair_recovers_interrupted_first_line_from_journal | AttributeError: module 'os' has no attribute 'pread'. Did you mean: 'read'? |
| tests/test_codex_provider_repair.py::test_provider_repair_rolls_back_db_and_rollout_together | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_codex_provider_repair.py::test_provider_repair_updates_db_and_patches_only_same_size_first_line | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_codex_readiness.py::test_installer_checks_listener_again_before_printing_ready[native-alias] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_installer_checks_listener_again_before_printing_ready[socket] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_installer_rejects_previous_activation_dead_or_reused_pid | AssertionError: assert (33206 & 511) == 384 |
| tests/test_codex_readiness.py::test_installer_rejects_symlink_and_bounds_receipt | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_readiness.py::test_installer_uses_service_state_root_without_executing_config[env-file] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_codex_readiness.py::test_installer_uses_service_state_root_without_executing_config[plist] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_codex_readiness.py::test_installer_uses_service_state_root_without_executing_config[state-dir] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_codex_readiness.py::test_live_config_probe_rejects_healthy_handshake_but_unusable_config[deleted_cwd] | NotImplementedError |
| tests/test_codex_readiness.py::test_live_config_probe_rejects_healthy_handshake_but_unusable_config[hang] | NotImplementedError |
| tests/test_codex_readiness.py::test_live_config_probe_rejects_healthy_handshake_but_unusable_config[invalid] | NotImplementedError |
| tests/test_codex_readiness.py::test_live_config_probe_rejects_healthy_handshake_but_unusable_config[ready] | NotImplementedError |
| tests/test_codex_readiness.py::test_live_config_probe_rejects_healthy_handshake_but_unusable_config[swapped] | NotImplementedError |
| tests/test_codex_readiness.py::test_live_installer_check_is_fresh_and_runs_only_as_service_owner[False] | AttributeError: module 'os' has no attribute 'geteuid'. Did you mean: 'getpid'? |
| tests/test_codex_readiness.py::test_live_installer_check_is_fresh_and_runs_only_as_service_owner[True] | AttributeError: module 'os' has no attribute 'geteuid'. Did you mean: 'getpid'? |
| tests/test_codex_readiness.py::test_missing_receipt_does_not_pass_acceptance | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_codex_readiness.py::test_native_alias_rejects_unsafe_targets[native-alias-directory] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_native_alias_rejects_unsafe_targets[native-alias-other_account] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_native_alias_rejects_unsafe_targets[native-alias-permissions] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_native_alias_rejects_unsafe_targets[native-alias-target_symlink] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_native_alias_rejects_unsafe_targets[socket-directory] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_native_alias_rejects_unsafe_targets[socket-other_account] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_native_alias_rejects_unsafe_targets[socket-permissions] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_native_alias_rejects_unsafe_targets[socket-target_symlink] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_non_disruptive_first_start_reuses_official_lifecycle | assert None is not None |
| tests/test_codex_readiness.py::test_non_disruptive_setup_never_restarts_or_enables_cloud_control[False-False] | assert 0 == 1 |
| tests/test_codex_readiness.py::test_non_disruptive_setup_never_restarts_or_enables_cloud_control[False-True] | assert (None is not None) == True |
| tests/test_codex_readiness.py::test_non_disruptive_setup_never_restarts_or_enables_cloud_control[True-False] | assert 0 == 1 |
| tests/test_codex_readiness.py::test_non_disruptive_setup_never_restarts_or_enables_cloud_control[True-True] | cc_remote.wrapper.codex_daemon.CodexProfileDaemonUnavailable: Codex profile shared daemon commands are unavailable |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[native-alias-denied-connection_failed] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[native-alias-missing-daily_cli_missing] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[native-alias-old_cli-version_mismatch] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[native-alias-old_server-version_mismatch] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[native-alias-other_account-daily_cli_mismatch] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[native-alias-ready-None] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[native-alias-swapped-daemon_changed] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[native-alias-wrong_wrapper-account_socket_mismatch] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[socket-denied-connection_failed] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[socket-missing-daily_cli_missing] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[socket-old_cli-version_mismatch] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[socket-old_server-version_mismatch] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[socket-other_account-daily_cli_mismatch] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[socket-ready-None] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[socket-swapped-daemon_changed] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_profile_checks_endpoint_versions_and_real_handshake[socket-wrong_wrapper-account_socket_mismatch] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_proxy_probe_only_initializes_and_reaps_child[eof] | NotImplementedError |
| tests/test_codex_readiness.py::test_proxy_probe_only_initializes_and_reaps_child[hang] | NotImplementedError |
| tests/test_codex_readiness.py::test_proxy_probe_only_initializes_and_reaps_child[oversized] | NotImplementedError |
| tests/test_codex_readiness.py::test_proxy_probe_only_initializes_and_reaps_child[reject] | NotImplementedError |
| tests/test_codex_readiness.py::test_proxy_probe_only_initializes_and_reaps_child[success] | NotImplementedError |
| tests/test_codex_readiness.py::test_receipt_fences_physical_replacement_behind_native_alias[native-alias] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_readiness.py::test_receipt_fences_physical_replacement_behind_native_alias[socket] [error] | failed on setup with "AttributeError: module 'socket' has no attribute 'AF_UNIX'" |
| tests/test_codex_rollback_integration.py::test_checkpoint_quarantine_failure_blocks_native_conversation_rollback | RuntimeError: coroutine raised StopIteration |
| tests/test_codex_rollback_integration.py::test_combined_rollback_retires_checkpoint_before_native_history_mutation | assert [] == [(2, False)] |
| tests/test_codex_rollback_integration.py::test_committed_turn_only_warns_when_untracked_baseline_changes[False] | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_rollback_integration.py::test_committed_turn_only_warns_when_untracked_baseline_changes[True] | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_codex_rollback_integration.py::test_conversation_rollback_retires_journal_before_native_submission | AssertionError: assert False |
| tests/test_codex_rollback_integration.py::test_files_only_restore_does_not_consume_conversation_alignment | AssertionError: assert False |
| tests/test_codex_rollback_integration.py::test_post_commit_refresh_failure_never_replays_native_rollback | assert [] == [1] |
| tests/test_codex_rollback_integration.py::test_post_rollback_discard_failure_quarantines_journal_and_reports_detail | RuntimeError: coroutine raised StopIteration |
| tests/test_codex_rollback_integration.py::test_wrapper_restart_never_replays_a_submitted_native_rollback | AttributeError: 'NoneType' object has no attribute 'begin' |
| tests/test_codex_rpc.py::test_codex_rpc_can_raise_only_the_isolated_child_nofile_limit | ValueError: invalid POSIX Codex RPC nofile limit |
| tests/test_codex_runtime_events.py::test_codex_work_list_recovers_one_unbound_exact_cwd_match | assert None is not None |
| tests/test_codex_runtime_events.py::test_new_codex_work_session_binds_native_id_and_invalidates_sidebar | AssertionError: assert None == 'work-thread-1' |
| tests/test_codex_runtime_events.py::test_new_session_flushes_pending_notice_after_client_runtime_exists | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_service.py::test_account_environment_is_private_and_exact | assert 3221226505 == 0 |
| tests/test_codex_service.py::test_guard_allows_read_only_and_private_children_before_exec[command0] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_allows_read_only_and_private_children_before_exec[command1] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_allows_read_only_and_private_children_before_exec[command2] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_allows_read_only_and_private_children_before_exec[command3] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_allows_read_only_and_private_children_before_exec[command4] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_blocks_lifecycle_before_listener_exists[native-bootstrap] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_blocks_lifecycle_before_listener_exists[native-disable-remote-control] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_blocks_lifecycle_before_listener_exists[native-enable-remote-control] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_blocks_lifecycle_before_listener_exists[native-future-mutating-operation] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_blocks_lifecycle_before_listener_exists[native-restart] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_blocks_lifecycle_before_listener_exists[native-start] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_blocks_lifecycle_before_listener_exists[native-stop] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_blocks_lifecycle_before_listener_exists[rlimit-helper-bootstrap] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_blocks_lifecycle_before_listener_exists[rlimit-helper-disable-remote-control] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_blocks_lifecycle_before_listener_exists[rlimit-helper-enable-remote-control] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_blocks_lifecycle_before_listener_exists[rlimit-helper-future-mutating-operation] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_blocks_lifecycle_before_listener_exists[rlimit-helper-restart] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_blocks_lifecycle_before_listener_exists[rlimit-helper-start] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_blocks_lifecycle_before_listener_exists[rlimit-helper-stop] | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_blocks_old_listeners_but_not_proxies_private_stdio_or_other_units | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_cli_refuses_pre_exec_launch_without_changing_the_service | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_does_not_parse_unrelated_python_arguments_as_a_launcher | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_guard_refuses_incomplete_inspection | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_codex_service.py::test_isolation_failure_never_launches_directly[missing-manager] | Failed: DID NOT RAISE DaemonIsolationError |
| tests/test_codex_service.py::test_isolation_failure_never_launches_directly[missing-runner] | Failed: DID NOT RAISE DaemonIsolationError |
| tests/test_codex_service.py::test_isolation_failure_never_launches_directly[timeout] | Failed: DID NOT RAISE DaemonIsolationError |
| tests/test_codex_session_archive.py::test_busy_hidden_subagent_does_not_block_root_archive | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_cold_archive_uses_shared_owner_context | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_cold_tree_archive_falls_back_to_active_writer_owner | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_cold_tree_archive_uses_isolated_high_nofile_child | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_completed_archive_replay_projects_current_native_state | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_hidden_subagent_descendant_is_preflighted | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_late_archive_commit_ignores_disappeared_descendant | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_late_archive_commit_is_finalized_without_native_replay | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_lost_archive_response_accepts_exact_committed_state | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_preexisting_archived_root_is_idempotent_with_active_descendant | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_queued_descendant_rejects_archive_without_losing_work | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_resident_archive_uses_owner_and_reconciles_descendants | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_resident_profile_archive_rejects_cross_home_without_owner_restart | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_root_success_keeps_unarchived_descendant_without_rollback | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_running_descendant_is_left_active_when_root_archives | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_running_root_archive_is_rejected_before_native_mutation | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_unknown_archive_result_retires_untrusted_writer | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_unknown_archive_retry_uses_durable_no_replay_fence | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_unknown_exception_after_descendant_commit_never_rolls_back | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_archive.py::test_visible_descendants_over_exact_limit_fail_before_archive | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_session_delete.py (whole module) | TIMEOUT: module did not finish within 240 s (also hangs on the pre-change base) |
| tests/test_codex_session_migration.py::test_codex_session_list_discards_a_missing_cwd_override | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_session_migration.py::test_codex_session_list_overlays_durable_migrated_cwd | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_session_migration.py::test_cold_session_is_loaded_and_migrated_without_changing_focus | AssertionError: assert False |
| tests/test_codex_session_migration.py::test_idle_codex_session_migrates_without_focus_or_queue_loss | AssertionError: assert False |
| tests/test_codex_session_migration.py::test_loaded_shared_thread_uses_confirmed_migrated_cwd_on_next_turn | AssertionError: assert False |
| tests/test_codex_session_migration.py::test_migration_preserves_native_reload_before_next_remote_turn | AssertionError: assert False |
| tests/test_codex_session_migration.py::test_migration_restores_override_after_a_post_replace_failure | AssertionError: assert '已恢复原工作目录' in '迁移状态无法保存且原目录状态未完全恢复，请重新打开会话' |
| tests/test_codex_session_migration.py::test_restart_clears_missing_override_before_runtime_and_sidebar_resume | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_shared_machine.py::test_account_switch_continues_running_turn_before_queue_can_drain | TimeoutError |
| tests/test_codex_shared_machine.py::test_account_switch_marker_wins_same_tick_proxy_close | TimeoutError |
| tests/test_codex_shared_machine.py::test_account_switch_resumes_already_spontaneous_goal_on_new_daemon | TimeoutError |
| tests/test_codex_shared_machine.py::test_account_switch_resumes_usage_limited_goal_without_competing_query | TimeoutError |
| tests/test_codex_shared_machine.py::test_generation_reconnect_does_not_revive_evicted_context | TimeoutError |
| tests/test_codex_shared_machine.py::test_generation_reconnect_drops_effort_resolved_after_eviction | TimeoutError |
| tests/test_codex_shared_machine.py::test_idle_status_reconnects_changed_daemon_generation | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_shared_machine.py::test_intentional_restart_reconnects_live_proxy_before_query | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_shared_machine.py::test_interrupt_during_spontaneous_account_handoff_never_starts_fallback | TimeoutError |
| tests/test_codex_shared_machine.py::test_lease_rebind_retry_uses_last_durable_message_id | AssertionError: assert None == 'message-a' |
| tests/test_codex_shared_machine.py::test_managed_terminal_retries_initial_alias_before_releasing_lease | AssertionError: assert CodexTurnLease(session_id='sid', turn_id='owned-turn', msg_id='logical-message', initial_msg_id='logical-message', daemon_epoch=None, automatic=False, stream… |
| tests/test_codex_shared_machine.py::test_query_waits_for_restart_ready_before_reconnecting | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_shared_machine.py::test_restarted_wrapper_binds_control_turn_to_exact_active_rollout_task | PermissionError: [Errno 13] Permission denied: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\cc-remote-test-6xubumej' |
| tests/test_codex_shared_machine.py::test_restarted_wrapper_continues_aborted_goal_with_distinct_rollout_id | PermissionError: [Errno 13] Permission denied: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\cc-remote-test-tt6f_slj' |
| tests/test_codex_shared_machine.py::test_restarted_wrapper_continues_leased_turn_aborted_by_account_switch | PermissionError: [Errno 13] Permission denied: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\cc-remote-test-i6cxhgqd' |
| tests/test_codex_shared_machine.py::test_restarted_wrapper_hands_active_old_generation_to_new_daemon | PermissionError: [Errno 13] Permission denied: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\cc-remote-test-ndkc5gux' |
| tests/test_codex_shared_machine.py::test_restarted_wrapper_keeps_lease_when_stream_witness_is_incomplete | PermissionError: [Errno 13] Permission denied: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\cc-remote-test-varybkh6' |
| tests/test_codex_shared_machine.py::test_restarted_wrapper_reclaims_only_leased_active_daemon_turn | PermissionError: [Errno 13] Permission denied: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\cc-remote-test-sockbau2' |
| tests/test_codex_shared_machine.py::test_restarted_wrapper_recovers_goal_with_distinct_rollout_turn_id | PermissionError: [Errno 13] Permission denied: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\cc-remote-test-3allgoig' |
| tests/test_codex_shared_machine.py::test_restarted_wrapper_recovers_large_steered_turn_from_durable_binding | PermissionError: [Errno 13] Permission denied: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\cc-remote-test-b3itejof' |
| tests/test_codex_shared_machine.py::test_restarted_wrapper_recovers_split_turn_without_tail_lifecycle_marker | PermissionError: [Errno 13] Permission denied: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\cc-remote-test-m7io5980' |
| tests/test_codex_shared_machine.py::test_stdio_work_keeps_legacy_terminal_mutex_and_read_only_state | RuntimeError: Codex profile state migration is incomplete |
| tests/test_codex_shared_machine.py::test_unmarked_daemon_generation_persists_a_recoverable_turn_lease | AssertionError: assert None == 'owned-turn' |
| tests/test_codex_shared_machine.py::test_unmarked_process_generation_swap_reconnects_before_query | AssertionError: assert 0 == 1 |
| tests/test_codex_shared_machine.py::test_wrapper_startup_restores_background_owned_turns | PermissionError: [Errno 13] Permission denied: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\cc-remote-test-pjj8z057' |
| tests/test_codex_spontaneous_stream.py::test_active_history_adds_official_alias_without_replacing_live_id | cc_remote.wrapper.codex_client_messages.CodexClientMessageStoreError: Codex client-message store could not be persisted |
| tests/test_codex_spontaneous_stream.py::test_cross_turn_identity_retries_when_rollout_path_appears_late | AssertionError: assert {'native-dela...022E88B7B1C0>} == {} |
| tests/test_codex_spontaneous_stream.py::test_machine_deduplicates_cli_thread_catalog_hints_across_handles | assert 0 == 1 |
| tests/test_codex_spontaneous_stream.py::test_machine_persists_cross_turn_steer_identity_and_refreshes_history | cc_remote.wrapper.codex_client_messages.CodexClientMessageStoreError: Codex client-message store is not a private bounded file |
| tests/test_codex_spontaneous_stream.py::test_machine_persists_split_initial_query_identity | assert False |
| tests/test_codex_spontaneous_stream.py::test_machine_projects_later_cli_user_item_as_turn_steer | AssertionError: assert 'cli-steered-turn' == 'cli-second' |
| tests/test_codex_spontaneous_stream.py::test_machine_reconciles_remote_steer_user_item_without_second_steer[False] | AssertionError: assert 'cli-remote-steered-turn' == 'remote-steer' |
| tests/test_codex_spontaneous_stream.py::test_machine_reconciles_remote_steer_user_item_without_second_steer[True] | AssertionError: assert 'cli-remote-steered-turn' == 'cli-first' |
| tests/test_codex_spontaneous_stream.py::test_official_history_adds_second_native_alias_for_known_message | cc_remote.wrapper.codex_client_messages.CodexClientMessageStoreError: Codex client-message store could not be persisted |
| tests/test_codex_spontaneous_stream.py::test_official_history_backfills_known_cross_turn_alias_while_cold | cc_remote.wrapper.codex_client_messages.CodexClientMessageStoreError: Codex client-message store could not be persisted |
| tests/test_codex_task_activity.py::test_activity_is_account_scoped_and_independent_of_parent_completion | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_task_activity.py::test_activity_read_cannot_apply_to_replaced_session | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_task_activity.py::test_actual_rollout_tool_output_keeps_native_receipt_identity | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_task_activity.py::test_callback_outcomes_remain_visible_without_exposing_output[accepted-pending-\u7b49\u5f85\u5904\u7406\u5b8c\u6210…] | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_task_activity.py::test_callback_outcomes_remain_visible_without_exposing_output[failed-failed-\u672a\u80fd\u5904\u7406\u901a\u77e5] | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_task_activity.py::test_callback_outcomes_remain_visible_without_exposing_output[rejected-failed-\u672a\u80fd\u5904\u7406\u901a\u77e5] | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_task_activity.py::test_callback_outcomes_remain_visible_without_exposing_output[unknown-failed-\u5904\u7406\u7ed3\u679c\u672a\u786e\u…] | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_task_activity.py::test_hello_reseeds_current_activity_even_after_replay_tail | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_task_activity.py::test_native_receipt_survives_summary_in_source_order[False] | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_task_activity.py::test_native_receipt_survives_summary_in_source_order[True] | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_task_activity.py::test_official_summary_recovers_receipts_between_exact_native_answers | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_task_activity.py::test_retained_warnings_do_not_hide_active_tasks_at_snapshot_limit | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_task_activity.py::test_task_activity_rejects_symlink_state | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_codex_task_activity.py::test_unreadable_task_state_is_not_an_empty_snapshot | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_task_activity.py::test_wrapper_sync_routes_activity_and_never_reopens_idle | ValueError: Task state directory must be private and owned by this user |
| tests/test_codex_turn_leases.py::test_codex_turn_lease_rebind_is_compare_and_swap | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_codex_turn_leases.py::test_codex_turn_lease_round_trip_and_matched_release | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_codex_turn_leases.py::test_codex_turn_lease_serializes_cross_thread_read_modify_write | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_codex_turn_leases.py::test_codex_turn_stream_binding_is_source_bound_and_compare_and_swap | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_codex_worktree_fork.py::test_archived_codex_parent_rejects_new_fork_before_rpc | AssertionError: assert ('error' == 'error' |
| tests/test_codex_worktree_fork.py::test_codex_cold_fork_does_not_restore_stale_named_over_granular | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_codex_worktree_fork.py::test_codex_fork_replaces_a_retired_parent_model | AttributeError: 'Error' object has no attribute 'session_id' |
| tests/test_codex_worktree_fork.py::test_codex_fork_submit_journal_failure_releases_intent_lock | KeyError: 'request-1' |
| tests/test_codex_worktree_fork.py::test_codex_same_cwd_fork_background_reconcile_acks_current_connection | Failed: DID NOT RAISE _ForkOutcomeUncertain |
| tests/test_codex_worktree_fork.py::test_codex_same_cwd_fork_does_not_ack_when_result_journal_fails | Failed: DID NOT RAISE _ForkOutcomeUncertain |
| tests/test_codex_worktree_fork.py::test_codex_same_cwd_fork_does_not_flatten_granular_approval | IndexError: list index out of range |
| tests/test_codex_worktree_fork.py::test_codex_same_cwd_fork_inherits_model_and_permissions_once | IndexError: list index out of range |
| tests/test_codex_worktree_fork.py::test_codex_same_cwd_fork_pre_submit_failure_is_terminal | KeyError: 'request-1' |
| tests/test_codex_worktree_fork.py::test_codex_same_cwd_fork_recovers_committed_rollout_intent | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_worktree_fork.py::test_codex_same_cwd_fork_restart_submitted_state_never_reforks | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_worktree_fork.py::test_codex_same_cwd_fork_unknown_rpc_outcome_only_reconciles_on_retry | Failed: DID NOT RAISE _ForkOutcomeUncertain |
| tests/test_codex_worktree_fork.py::test_codex_same_cwd_fork_uses_selected_turn_and_is_durable | AssertionError: assert [] == [('thread/for..., ...}, None)] |
| tests/test_codex_worktree_fork.py::test_codex_same_identity_new_request_aliases_submitted_child | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_worktree_fork.py::test_codex_worktree_background_reconcile_publishes_worktree_result | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_worktree_fork.py::test_codex_worktree_fork_recovers_existing_thread_without_refork | AttributeError: 'Error' object has no attribute 'session_id' |
| tests/test_codex_worktree_fork.py::test_codex_worktree_fork_restart_submitted_state_never_reforks | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_worktree_fork.py::test_codex_worktree_fork_retry_uses_its_frozen_permission_snapshot | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_worktree_fork.py::test_codex_worktree_fork_rolls_back_fresh_worktree_on_confirmed_failure | AssertionError: assert '无法记录派生请求: Co... be persisted' == 'Codex 会话派生未完成，请稍后重试。' |
| tests/test_codex_worktree_fork.py::test_codex_worktree_fork_uses_persistent_rpc_and_returns_correlated_result | RuntimeError: coroutine raised StopIteration |
| tests/test_codex_worktree_fork.py::test_codex_worktree_unknown_outcome_keeps_worktree_and_never_acks | Failed: DID NOT RAISE _ForkOutcomeUncertain |
| tests/test_codex_worktree_fork.py::test_completed_codex_fork_replays_after_parent_is_archived | AttributeError: 'Error' object has no attribute 'session_id' |
| tests/test_codex_worktree_fork.py::test_completed_fork_without_rollout_is_not_restored | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_worktree_fork.py::test_renamed_fork_keeps_title_while_native_catalog_omits_it | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_worktree_fork.py::test_same_cwd_fork_invalidates_catalog_before_publication | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_worktree_fork.py::test_same_cwd_fork_refresh_includes_child_after_fresh_parent_cache | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_worktree_fork.py::test_worktree_fork_invalidates_catalog_before_publication | cc_remote.wrapper.codex_forks.ForkJournalError: Codex fork journal could not be persisted |
| tests/test_codex_worktree_fork.py::test_worktree_retry_never_overwrites_name_after_journal_failure | Failed: DID NOT RAISE _ForkOutcomeUncertain |
| tests/test_codex_worktrees.py::test_prepare_worktree_preserves_session_subdirectory_and_is_idempotent | AssertionError: assert 0 == 1 |
| tests/test_command_reliability.py::test_btw_capture_keeps_live_guard_when_persist_and_delete_both_fail | AssertionError: Regex pattern did not match. |
| tests/test_command_reliability.py::test_btw_capture_persistence_failure_terminates_and_deletes_fork | AssertionError: Regex pattern did not match. |
| tests/test_command_reliability.py::test_claude_btw_real_id_is_hidden_and_cannot_be_cold_resumed | RuntimeError: coroutine raised StopIteration |
| tests/test_command_reliability.py::test_claude_btw_tombstone_survives_restart_and_successful_delete | RuntimeError: Claude profile state migration is incomplete |
| tests/test_command_reliability.py::test_claude_session_list_is_withheld_until_btw_real_id_is_tombstoned | AssertionError: assert (True and 'internal' == 'busy' |
| tests/test_command_reliability.py::test_deleted_claude_fork_retry_only_acks_without_resurrecting | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_command_reliability.py::test_duplicate_resident_switch_reseeds_current_state_with_new_sequence | RuntimeError: Claude profile state migration is incomplete |
| tests/test_command_reliability.py::test_failed_claude_fork_delete_restores_complete_journal | cc_remote.wrapper.claude_forks.ClaudeForkJournalError: fork cwd must be absolute |
| tests/test_command_scheduling.py::test_cli_preflight_does_not_block_another_sessions_stop_or_heartbeat[command0] | TimeoutError |
| tests/test_command_scheduling.py::test_cli_preflight_does_not_block_another_sessions_stop_or_heartbeat[command1] | TimeoutError |
| tests/test_command_scheduling.py::test_rekey_does_not_split_the_command_order_or_residency_pin | RuntimeError: Claude profile state migration is incomplete |
| tests/test_command_scheduling.py::test_waiting_query_is_not_evicted_before_it_can_take_query_lock | RuntimeError: Claude profile state migration is incomplete |
| tests/test_devices.py::test_pairing_cli_writes_private_config_without_printing_token | AssertionError: assert (33206 & 511) == 384 |
| tests/test_devices.py::test_pairing_code_is_single_use_and_plaintext_credential_is_not_stored | AssertionError: assert (33206 & 511) == 384 |
| tests/test_devices.py::test_wrapper_config_reads_private_paired_device_file | ValueError: device credential file must not be accessible by group/others: <tmp> |
| tests/test_dsh_artifact_reads.py::test_cold_artifacts_reject_invalid_scope_before_history_or_file_read[empty] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_artifacts_reject_invalid_scope_before_history_or_file_read[engine] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_artifacts_reject_invalid_scope_before_history_or_file_read[missing] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_artifacts_reject_invalid_scope_before_history_or_file_read[relative] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_artifacts_reject_invalid_scope_before_history_or_file_read[work] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_catalog_and_previews_ignore_full_resident_pool[False-False-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_catalog_and_previews_ignore_full_resident_pool[False-False-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_catalog_and_previews_ignore_full_resident_pool[False-False-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_catalog_and_previews_ignore_full_resident_pool[False-True-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_catalog_and_previews_ignore_full_resident_pool[False-True-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_catalog_and_previews_ignore_full_resident_pool[False-True-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_catalog_and_previews_ignore_full_resident_pool[True-False-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_catalog_and_previews_ignore_full_resident_pool[True-False-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_catalog_and_previews_ignore_full_resident_pool[True-False-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_catalog_and_previews_ignore_full_resident_pool[True-True-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_catalog_and_previews_ignore_full_resident_pool[True-True-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_cold_catalog_and_previews_ignore_full_resident_pool[True-True-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_artifact_reads.py::test_private_cold_artifacts_preserve_owner_without_restoring_context | AssertionError: assert not '文件已移动、删除或暂时无法预览，请刷新 Artifacts' |
| tests/test_dsh_attachments.py::test_empty_message_rejected_but_blank_session_creation_allowed[new] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_empty_message_rejected_but_blank_session_creation_allowed[query] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_empty_message_rejected_but_blank_session_creation_allowed[steer] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[base64-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[base64-new] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[base64-query] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[base64-slash] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[base64-steer] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[dimensions-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[dimensions-new] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[dimensions-query] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[dimensions-slash] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[dimensions-steer] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[mime-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[mime-new] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[mime-query] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[mime-slash] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[mime-steer] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[pixels-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[pixels-new] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[pixels-query] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[pixels-slash] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[pixels-steer] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[total_bytes-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[total_bytes-new] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[total_bytes-query] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[total_bytes-slash] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[total_bytes-steer] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[total_pixels-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[total_pixels-new] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[total_pixels-query] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[total_pixels-slash] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_attachment_never_activates_or_writes[total_pixels-steer] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_invalid_deferred_launch_is_not_admitted | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_valid_attachment_only_inputs_remain_supported[file-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_valid_attachment_only_inputs_remain_supported[file-new] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_valid_attachment_only_inputs_remain_supported[file-query] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_valid_attachment_only_inputs_remain_supported[file-slash] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_valid_attachment_only_inputs_remain_supported[file-steer] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_valid_attachment_only_inputs_remain_supported[image-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_valid_attachment_only_inputs_remain_supported[image-new] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_valid_attachment_only_inputs_remain_supported[image-query] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_valid_attachment_only_inputs_remain_supported[image-slash] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_attachments.py::test_valid_attachment_only_inputs_remain_supported[image-steer] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_changes.py::test_slow_native_diff_does_not_block_terminal_and_late_result_cannot_replace_newer | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_client.py::test_connection_repr_and_file_do_not_expose_control_token | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_control_scheduling.py::test_session_controls_precede_immediate_deferred_and_steering_prompts[immediate-command-/permission read-only-co…] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_control_scheduling.py::test_session_controls_precede_immediate_deferred_and_steering_prompts[immediate-effort-max-session/selectModel] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_control_scheduling.py::test_session_controls_precede_immediate_deferred_and_steering_prompts[immediate-permission-read-only-commands/ex…] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_control_scheduling.py::test_session_controls_precede_immediate_deferred_and_steering_prompts[queue-command-/permission read-only-comman…] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_control_scheduling.py::test_session_controls_precede_immediate_deferred_and_steering_prompts[queue-effort-max-session/selectModel] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_control_scheduling.py::test_session_controls_precede_immediate_deferred_and_steering_prompts[queue-permission-read-only-commands/execut…] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_control_scheduling.py::test_session_controls_precede_immediate_deferred_and_steering_prompts[steer-command-/permission read-only-comman…] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_control_scheduling.py::test_session_controls_precede_immediate_deferred_and_steering_prompts[steer-effort-max-session/selectModel] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_control_scheduling.py::test_session_controls_precede_immediate_deferred_and_steering_prompts[steer-permission-read-only-commands/execut…] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_control_scheduling.py::test_steer_waits_for_a_permission_command_not_yet_dispatched | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_export_scheduling.py::test_export_keeps_focus_create_and_owner_cancellation_responsive[next_command0-dsh@root] | AssertionError: assert None == 'dsh@root' |
| tests/test_dsh_export_scheduling.py::test_export_keeps_focus_create_and_owner_cancellation_responsive[next_command1-dsh@other] | AssertionError: assert None == 'dsh@other' |
| tests/test_dsh_export_scheduling.py::test_export_keeps_focus_create_and_owner_cancellation_responsive[next_command2-dsh@created] | AssertionError: assert None == 'dsh@created' |
| tests/test_dsh_features.py::test_archive_rejects_query_lock_even_before_runtime_turn_starts | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_features.py::test_background_jobs_are_scoped_and_clear_when_completed | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_features.py::test_cold_file_read_uses_snapshot_without_follow | AssertionError: assert (False) |
| tests/test_dsh_features.py::test_native_archive_and_unarchive_preserve_read_only_history | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_features.py::test_plan_review_returns_native_option_and_ignores_stale_projection | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_follow_recovery.py::test_follow_reconnect_drains_queue_only_after_native_idle[False-False] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_follow_recovery.py::test_follow_reconnect_drains_queue_only_after_native_idle[False-True] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_follow_recovery.py::test_follow_reconnect_drains_queue_only_after_native_idle[True-False] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_follow_recovery.py::test_follow_reconnect_drains_queue_only_after_native_idle[True-True] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_follow_recovery.py::test_rejected_cold_archive_observes_terminal_and_releases_capacity[False] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_follow_recovery.py::test_rejected_cold_archive_observes_terminal_and_releases_capacity[True] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[ack-clear] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[ack-complete] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[ack-create] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[ack-edit] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[ack-pause] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[ack-resume] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[delivery-clear] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[delivery-complete] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[delivery-create] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[delivery-edit] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[delivery-pause] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[delivery-resume] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[native_response-clear] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[native_response-complete] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[native_response-create] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[native_response-edit] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[native_response-pause] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[native_response-resume] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[receipt-clear] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[receipt-complete] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[receipt-create] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[receipt-edit] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[receipt-pause] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[receipt-resume] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[refresh-clear] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[refresh-complete] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[refresh-create] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[refresh-edit] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[refresh-pause] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_crash_never_repeats_native_mutation[refresh-resume] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_error_is_replayed_without_retrying_or_replacing_revision[False] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_error_is_replayed_without_retrying_or_replacing_revision[True] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_identity_cannot_be_reused_for_a_different_mutation[changed0] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_identity_cannot_be_reused_for_a_different_mutation[changed1] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_identity_cannot_be_reused_for_a_different_mutation[changed2] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_goal_identity_cannot_be_reused_for_a_different_mutation[changed3] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_goal_recovery.py::test_pending_goal_duplicate_does_not_race_the_first_write | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_interrupt_recovery.py::test_interrupt_crash_never_cancels_the_next_turn[ack] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_interrupt_recovery.py::test_interrupt_crash_never_cancels_the_next_turn[native_response] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_interrupt_recovery.py::test_interrupt_crash_never_cancels_the_next_turn[receipt] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_interrupt_recovery.py::test_interrupt_error_is_retained_without_repeating_cancel[False] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_interrupt_recovery.py::test_interrupt_error_is_retained_without_repeating_cancel[True] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_interrupt_recovery.py::test_interrupt_identity_cannot_be_retargeted[False] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_interrupt_recovery.py::test_interrupt_identity_cannot_be_retargeted[True] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_interrupt_recovery.py::test_pending_interrupt_duplicate_does_not_race_first_cancel | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_archive_guards_still_reject_busy_sessions[native-archive] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_metadata_recovery.py::test_archive_guards_still_reject_busy_sessions[native-unarchive] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_metadata_recovery.py::test_archive_guards_still_reject_busy_sessions[queue-archive] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_metadata_recovery.py::test_archive_guards_still_reject_busy_sessions[queue-unarchive] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_metadata_recovery.py::test_archive_guards_still_reject_busy_sessions[resident-archive] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_metadata_recovery.py::test_archive_guards_still_reject_busy_sessions[resident-unarchive] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_metadata_recovery.py::test_concurrent_metadata_recovery_issues_one_native_write[archive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_concurrent_metadata_recovery_issues_one_native_write[rename] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_concurrent_metadata_recovery_issues_one_native_write[unarchive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_native_error_is_retained_without_retry[False-archive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_native_error_is_retained_without_retry[False-rename] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_native_error_is_retained_without_retry[False-unarchive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_native_error_is_retained_without_retry[True-archive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_native_error_is_retained_without_retry[True-rename] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_native_error_is_retained_without_retry[True-unarchive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_recovery_identity_binds_target_and_choice[False-archive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_recovery_identity_binds_target_and_choice[False-rename] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_recovery_identity_binds_target_and_choice[False-unarchive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_recovery_identity_binds_target_and_choice[True-archive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_recovery_identity_binds_target_and_choice[True-rename] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_recovery_identity_binds_target_and_choice[True-unarchive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_replay_preserves_newer_native_state[ack-archive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_replay_preserves_newer_native_state[ack-rename] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_replay_preserves_newer_native_state[ack-unarchive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_replay_preserves_newer_native_state[native_response-archive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_replay_preserves_newer_native_state[native_response-rename] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_replay_preserves_newer_native_state[native_response-unarchive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_replay_preserves_newer_native_state[notification-archive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_replay_preserves_newer_native_state[notification-rename] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_replay_preserves_newer_native_state[notification-unarchive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_replay_preserves_newer_native_state[receipt-archive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_replay_preserves_newer_native_state[receipt-rename] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_metadata_recovery.py::test_metadata_replay_preserves_newer_native_state[receipt-unarchive] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_model_recovery.py::test_effort_only_change_uses_model_refreshed_by_native_follow[control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_effort_only_change_uses_model_refreshed_by_native_follow[effort] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_pending_selection_duplicate_does_not_race_first_write | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_replayed_selection_cannot_restore_an_older_choice[ack-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_replayed_selection_cannot_restore_an_older_choice[ack-effort] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_replayed_selection_cannot_restore_an_older_choice[ack-model] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_replayed_selection_cannot_restore_an_older_choice[native_response-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_replayed_selection_cannot_restore_an_older_choice[native_response-effort] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_replayed_selection_cannot_restore_an_older_choice[native_response-model] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_replayed_selection_cannot_restore_an_older_choice[projection-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_replayed_selection_cannot_restore_an_older_choice[projection-effort] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_replayed_selection_cannot_restore_an_older_choice[projection-model] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_replayed_selection_cannot_restore_an_older_choice[receipt-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_replayed_selection_cannot_restore_an_older_choice[receipt-effort] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_replayed_selection_cannot_restore_an_older_choice[receipt-model] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_selection_identity_rejects_a_different_target_or_value[changed0] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_selection_identity_rejects_a_different_target_or_value[changed1] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_selection_native_error_is_retained_without_reconfiguration[False] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_model_recovery.py::test_selection_native_error_is_retained_without_reconfiguration[True] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_mutations.py::test_command_replay_across_fresh_wrapper_never_executes_again[False-compact] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_mutations.py::test_command_replay_across_fresh_wrapper_never_executes_again[False-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_mutations.py::test_command_replay_across_fresh_wrapper_never_executes_again[False-query] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_mutations.py::test_command_replay_across_fresh_wrapper_never_executes_again[True-compact] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_mutations.py::test_command_replay_across_fresh_wrapper_never_executes_again[True-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_mutations.py::test_command_replay_across_fresh_wrapper_never_executes_again[True-query] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_mutations.py::test_completed_creation_recovers_without_reconfiguring_even_if_catalog_changed | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_completed_fork_replays_by_request_id_to_current_browser_without_native_reads | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_concurrent_creation_claims_only_one_native_write | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_concurrent_forks_with_one_request_id_create_only_one_child | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_corrupt_journal_blocks_creation | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_corrupt_journal_blocks_fork | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_creation_crash_never_creates_or_prompts_twice[ack] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_creation_crash_never_creates_or_prompts_twice[configure] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_creation_crash_never_creates_or_prompts_twice[native_response] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_fork_crash_recovers_known_child_without_repeating_native_write[ack] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_fork_crash_recovers_known_child_without_repeating_native_write[catalog] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_fork_crash_recovers_known_child_without_repeating_native_write[delivery] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_fork_crash_recovers_known_child_without_repeating_native_write[history] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_fork_crash_recovers_known_child_without_repeating_native_write[native_response] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_fork_crash_recovers_known_child_without_repeating_native_write[receipt] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_fork_request_cannot_be_reused_for_another_parent_or_turn[changed0] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_fork_request_cannot_be_reused_for_another_parent_or_turn[changed1] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_fork_retains_completed_boundary_validation | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_journal_serializes_claims_and_rejects_payload_reuse | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_request_identity_binds_payload_owner_and_native_authority | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_transient_post_fork_failure_retries_only_read_only_recovery[False-catalog] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_transient_post_fork_failure_retries_only_read_only_recovery[False-history] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_transient_post_fork_failure_retries_only_read_only_recovery[False-receipt] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_transient_post_fork_failure_retries_only_read_only_recovery[True-catalog] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_transient_post_fork_failure_retries_only_read_only_recovery[True-history] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_mutations.py::test_transient_post_fork_failure_retries_only_read_only_recovery[True-receipt] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_read_timeout.py::test_stalled_job_read_does_not_block_focus_and_external_cancel_is_preserved[False] | AssertionError: assert (None == 'dsh@other') |
| tests/test_dsh_read_timeout.py::test_stalled_job_read_does_not_block_focus_and_external_cancel_is_preserved[True] | AssertionError: assert (None == 'dsh@other') |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[btw-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[btw-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[btw-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[compact-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[compact-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[compact-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[control_lock-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[control_lock-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[control_lock-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[drain-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[drain-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[drain-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[focused-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[focused-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[focused-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[query_lock-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[query_lock-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[query_lock-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[question-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[question-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[question-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[queue-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[queue-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[queue-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[recovery-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[recovery-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[recovery-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[running-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[running-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[running-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[scheduled-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[scheduled-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[scheduled-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[turn-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[turn-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_capacity_keeps_protected_residents_and_rejects_before_native_creation[turn-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_dsh_uses_idle_capacity_from_any_engine[create-claude] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_dsh_uses_idle_capacity_from_any_engine[create-codex] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_dsh_uses_idle_capacity_from_any_engine[create-dsh] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_residency.py::test_dsh_uses_idle_capacity_from_any_engine[switch-claude] | AssertionError: assert ('dsh@existing' in {'focused': SessionContext(session_id='focused', sdk=namespace(disconnect=<AsyncMock id='2222164230896'>), buffer=<cc_...k object at 0x000… |
| tests/test_dsh_residency.py::test_dsh_uses_idle_capacity_from_any_engine[switch-codex] | AssertionError: assert ('dsh@existing' in {'focused': SessionContext(session_id='focused', sdk=namespace(disconnect=<AsyncMock id='2222160365952'>), buffer=<cc_...k object at 0x000… |
| tests/test_dsh_residency.py::test_dsh_uses_idle_capacity_from_any_engine[switch-dsh] | AssertionError: assert ('dsh@existing' in {'focused': SessionContext(session_id='focused', sdk=namespace(disconnect=<AsyncMock id='2222159177984'>), buffer=<cc_...k object at 0x000… |
| tests/test_dsh_runtime.py::test_absolute_path_prompt_is_not_a_native_command | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_cold_context_uses_native_projection_and_compaction_without_starting_an_agent | AttributeError: 'Error' object has no attribute 'available' |
| tests/test_dsh_runtime.py::test_cold_history_and_switch_do_not_follow_or_activate_agent | AssertionError: assert None == 'dsh@cold' |
| tests/test_dsh_runtime.py::test_command_rejects_unsupported_attachments_before_uploading | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_deferred_queries_remain_wrapper_owned_until_native_idle | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_follow_recovers_running_native_turn_without_false_idle_or_read_only[False] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_follow_recovers_running_native_turn_without_false_idle_or_read_only[True] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_goal_action_stale_ref_is_not_retried_or_replaced | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_goal_actions_forward_native_round_caps_and_exact_client_ref[clear] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_goal_actions_forward_native_round_caps_and_exact_client_ref[complete] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_goal_actions_forward_native_round_caps_and_exact_client_ref[create] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_goal_actions_forward_native_round_caps_and_exact_client_ref[edit] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_goal_actions_forward_native_round_caps_and_exact_client_ref[pause] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_goal_actions_forward_native_round_caps_and_exact_client_ref[resume] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_history_page_fence_excludes_live_events_arriving_during_read | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_native_permission_is_not_a_claude_mode_and_requires_catalog_membership | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_new_session_preserves_native_default_model | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_runtime.py::test_old_projection_cannot_restore_a_completed_goal_or_old_permission | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_pending_question_restores_cold_owner_without_focus_or_activation[False-0] | TimeoutError |
| tests/test_dsh_runtime.py::test_pending_question_restores_cold_owner_without_focus_or_activation[False-1] | TimeoutError |
| tests/test_dsh_runtime.py::test_pending_question_restores_cold_owner_without_focus_or_activation[False-32] | TimeoutError |
| tests/test_dsh_runtime.py::test_pending_question_restores_cold_owner_without_focus_or_activation[False-7] | TimeoutError |
| tests/test_dsh_runtime.py::test_pending_question_restores_cold_owner_without_focus_or_activation[False-8] | TimeoutError |
| tests/test_dsh_runtime.py::test_pending_question_restores_cold_owner_without_focus_or_activation[False-9] | TimeoutError |
| tests/test_dsh_runtime.py::test_pending_question_restores_cold_owner_without_focus_or_activation[True-0] | TimeoutError |
| tests/test_dsh_runtime.py::test_pending_question_restores_cold_owner_without_focus_or_activation[True-1] | TimeoutError |
| tests/test_dsh_runtime.py::test_pending_question_restores_cold_owner_without_focus_or_activation[True-32] | TimeoutError |
| tests/test_dsh_runtime.py::test_pending_question_restores_cold_owner_without_focus_or_activation[True-7] | TimeoutError |
| tests/test_dsh_runtime.py::test_pending_question_restores_cold_owner_without_focus_or_activation[True-8] | TimeoutError |
| tests/test_dsh_runtime.py::test_pending_question_restores_cold_owner_without_focus_or_activation[True-9] | TimeoutError |
| tests/test_dsh_runtime.py::test_question_owner_recovery_and_browser_focus_share_one_context | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_question_reconnect_preserves_batch_identity_without_partial_submission | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_reconnect_restores_exact_native_controls_and_completion_receipt | AssertionError: assert False |
| tests/test_dsh_runtime.py::test_skills_read_targets_cold_dsh_session_without_changing_other_clients_focus | AssertionError: assert 'error' == 'engine_capabilities' |
| tests/test_dsh_runtime.py::test_unavailable_and_unoffered_models_never_create_a_session | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_dsh_runtime.py::test_unknown_prompt_outcome_keeps_admission_receipt_and_is_never_retried | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_runtime.py::test_unknown_question_answer_waits_for_new_native_delivery | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_sidechats.py::test_retained_native_sidechat_exact_cut_private_catalog_and_release_without_stop | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_sidechats.py::test_sidechat_open_is_unavailable_without_native_io[False] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_completed_upload_batch_reused_after_prompt_rejection_and_changed_transport_id | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_concurrent_upload_replay_does_not_repeat_pending_native_write | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_failed_upload_stays_visible_without_queue_retries_until_removed[False] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_failed_upload_stays_visible_without_queue_retries_until_removed[True] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_partial_batch_is_not_reuploaded_after_restart[False] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_partial_batch_is_not_reuploaded_after_restart[True] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_upload_journal_failure_prevents_native_write | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_upload_unknown_is_not_repeated_after_wrapper_restart[crash-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_upload_unknown_is_not_repeated_after_wrapper_restart[crash-query] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_upload_unknown_is_not_repeated_after_wrapper_restart[crash-slash] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_upload_unknown_is_not_repeated_after_wrapper_restart[receipt-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_upload_unknown_is_not_repeated_after_wrapper_restart[receipt-query] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_upload_unknown_is_not_repeated_after_wrapper_restart[receipt-slash] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_upload_unknown_is_not_repeated_after_wrapper_restart[response-control] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_upload_unknown_is_not_repeated_after_wrapper_restart[response-query] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_dsh_upload_recovery.py::test_upload_unknown_is_not_repeated_after_wrapper_restart[response-slash] | cc_remote.wrapper.dsh_client.DshError: DSH 会话目录不可读取，请刷新会话目录。 |
| tests/test_engine_capabilities.py::test_claude_hook_create_remove_preserves_unrelated_settings | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_engine_capabilities.py::test_claude_user_hooks_follow_config_dir_outside_home | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_engine_capabilities.py::test_machine_forwards_configured_cli_to_capability_listing | AssertionError: assert [] == [('claude', '...laude', True)] |
| tests/test_engine_capabilities.py::test_machine_forwards_work_space_to_plugin_backend | AssertionError: assert [] == [('claude', '...ured/claude')] |
| tests/test_engine_capabilities.py::test_project_hook_rejects_symlinked_config_directory | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_history.py::test_background_process_membership_replays_and_clears_authoritatively | RuntimeError: Claude profile state migration is incomplete |
| tests/test_history.py::test_cached_codex_summary_observes_new_process_clock_without_source_change | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store could not be persisted |
| tests/test_history.py::test_claude_history_loads_durable_wrapper_alias_after_restart | cc_remote.wrapper.claude_client_messages.ClaudeClientMessageStoreError: Claude client-message store could not be persisted |
| tests/test_history.py::test_claude_history_retries_when_alias_lands_during_source_scan | cc_remote.wrapper.claude_client_messages.ClaudeClientMessageStoreError: Claude client-message store could not be persisted |
| tests/test_history.py::test_claude_resident_preload_keeps_recovered_answer_once | RuntimeError: Claude profile state migration is incomplete |
| tests/test_history.py::test_claude_switch_history_binds_active_prompt_before_projection | RuntimeError: Claude profile state migration is incomplete |
| tests/test_history.py::test_codex_next_user_boundary_retires_only_settled_plan_snapshot | AssertionError: assert None is not None |
| tests/test_history.py::test_codex_plan_snapshot_recovers_as_settled_after_native_terminal | assert False is True |
| tests/test_history.py::test_codex_process_clock_cannot_resurrect_exact_empty_steer[None] | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store could not be persisted |
| tests/test_history.py::test_codex_process_clock_cannot_resurrect_exact_empty_steer[native-turn] | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store could not be persisted |
| tests/test_history.py::test_codex_process_clock_overlay_requires_exact_logical_and_native_owner | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store could not be persisted |
| tests/test_history.py::test_codex_process_clock_waits_for_native_steer_input[False] | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store is not a private bounded file |
| tests/test_history.py::test_codex_process_clock_waits_for_native_steer_input[True] | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store is not a private bounded file |
| tests/test_history.py::test_codex_synthetic_terminal_cannot_poison_durable_plan_status | assert None is not None |
| tests/test_history.py::test_compacted_claude_history_pages_across_compact_boundary | cc_remote.wrapper.claude_client_messages.ClaudeClientMessageStoreError: Claude client-message store could not be persisted |
| tests/test_history.py::test_compacted_codex_tail_recovers_omitted_current_prompt | cc_remote.wrapper.codex_client_messages.CodexClientMessageStoreError: Codex client-message store could not be persisted |
| tests/test_history.py::test_completed_codex_plan_is_not_rebound_without_matching_owner[None] | cc_remote.wrapper.session_plans.SessionPlanStoreError: session plan store could not be persisted |
| tests/test_history.py::test_completed_codex_plan_is_not_rebound_without_matching_owner[native-1] | cc_remote.wrapper.session_plans.SessionPlanStoreError: session plan store could not be persisted |
| tests/test_history.py::test_fresh_hello_replays_only_current_inflight_turn_after_snapshot | RuntimeError: Claude profile state migration is incomplete |
| tests/test_history.py::test_hello_sends_snapshots_and_control_state_without_replay_flood | RuntimeError: Claude profile state migration is incomplete |
| tests/test_history.py::test_hello_with_cursor_replays_only_missing_tail | RuntimeError: Claude profile state migration is incomplete |
| tests/test_history.py::test_history_content_does_not_wait_for_external_ownership_scan | TimeoutError |
| tests/test_history.py::test_image_read_fallback_clears_provisional_preview_candidate | AssertionError: assert False |
| tests/test_history.py::test_incomplete_codex_plan_is_not_rebound_without_matching_owner | cc_remote.wrapper.session_plans.SessionPlanStoreError: session plan store could not be persisted |
| tests/test_history.py::test_live_codex_process_clock_starts_only_on_first_visible_process | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store is not a private bounded file |
| tests/test_history.py::test_official_codex_summary_backfills_oversized_pre_sidecar_turn | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store is not a private bounded file |
| tests/test_history.py::test_official_codex_summary_backfills_oversized_second_steer_clock | cc_remote.wrapper.codex_process_clocks.CodexProcessClockStoreError: Codex process-clock store could not be persisted |
| tests/test_history.py::test_official_codex_summary_overlays_source_bound_process_clock | cc_remote.wrapper.codex_client_messages.CodexClientMessageStoreError: Codex client-message store could not be persisted |
| tests/test_history.py::test_oversized_active_codex_steer_keeps_live_user_item_id | cc_remote.wrapper.codex_client_messages.CodexClientMessageStoreError: Codex client-message store could not be persisted |
| tests/test_history.py::test_requested_codex_summary_passes_source_bound_client_aliases | cc_remote.wrapper.codex_client_messages.CodexClientMessageStoreError: Codex client-message store could not be persisted |
| tests/test_history.py::test_scoped_empty_claude_history_retries_global_lookup | AssertionError: assert [None] == ['/stale/browser', None] |
| tests/test_history.py::test_truly_empty_claude_history_is_authoritative_after_global_retry | AssertionError: assert [None] == ['/listed/project', None] |
| tests/test_history.py::test_unpaired_history_agent_message_is_not_lost_at_snapshot_eof | AssertionError: assert [] == ['visible before pair'] |
| tests/test_history_continuity.py::test_additive_alias_revisions_keep_continuity_and_rollout_cursors | assert False |
| tests/test_history_store.py::test_compact_graph_cache_respects_snapshots_limits_and_partial_rows | AttributeError: 'NoneType' object has no attribute 'rows' |
| tests/test_history_store.py::test_history_index_is_bounded_and_invalidatable | AssertionError: assert '0o666' == '0o600' |
| tests/test_history_turn_changes.py::test_history_copies_exact_live_alias_but_not_shared_native_task | AssertionError: assert '\\repo\\README.md' == '/repo/README.md' |
| tests/test_history_turn_changes.py::test_missing_native_diff_keeps_markdown_list_and_storage_failure_keeps_paths | AssertionError: assert '\\repo\\README.md' == '/repo/README.md' |
| tests/test_history_turn_changes.py::test_official_idle_history_recovers_file_lists_without_expanding_tools[1-item_completed-False] | AssertionError: assert 'src\\code.py' == '/repo/src/code.py' |
| tests/test_history_turn_changes.py::test_official_idle_history_recovers_file_lists_without_expanding_tools[1-item_completed-True] | AssertionError: assert 'src\\code.py' == '/repo/src/code.py' |
| tests/test_history_turn_changes.py::test_official_idle_history_recovers_file_lists_without_expanding_tools[1-legacy-False] | AssertionError: assert 'src\\code.py' == '/repo/src/code.py' |
| tests/test_history_turn_changes.py::test_official_idle_history_recovers_file_lists_without_expanding_tools[1-legacy-True] | AssertionError: assert 'src\\code.py' == '/repo/src/code.py' |
| tests/test_history_turn_changes.py::test_official_idle_history_recovers_file_lists_without_expanding_tools[130-item_completed-False] | AssertionError: assert 'src\\code.py' == '/repo/src/code.py' |
| tests/test_history_turn_changes.py::test_official_idle_history_recovers_file_lists_without_expanding_tools[130-item_completed-True] | AssertionError: assert 'src\\code.py' == '/repo/src/code.py' |
| tests/test_history_turn_changes.py::test_official_idle_history_recovers_file_lists_without_expanding_tools[130-legacy-False] | AssertionError: assert 'src\\code.py' == '/repo/src/code.py' |
| tests/test_history_turn_changes.py::test_official_idle_history_recovers_file_lists_without_expanding_tools[130-legacy-True] | AssertionError: assert 'src\\code.py' == '/repo/src/code.py' |
| tests/test_link_preview_images.py::test_invalid_or_oversized_payloads_fail_closed[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx…] | failed on setup with "ValueError: the environment variable is longer than 32767 characters" |
| tests/test_link_preview_images.py::test_invalid_or_oversized_payloads_fail_closed[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx…] | failed on teardown with "ValueError: the environment variable is longer than 32767 characters" |
| tests/test_manual_compaction.py::test_codex_compact_history_is_account_scoped_and_survives_control_updates | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store could not be persisted |
| tests/test_manual_compaction.py::test_codex_manual_compact_keeps_native_lifecycle_and_command_after_reload[completed-False] | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store is not a private bounded file |
| tests/test_manual_compaction.py::test_codex_manual_compact_keeps_native_lifecycle_and_command_after_reload[completed-True] | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store is not a private bounded file |
| tests/test_manual_compaction.py::test_codex_manual_compact_keeps_native_lifecycle_and_command_after_reload[failed-False] | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store is not a private bounded file |
| tests/test_manual_compaction.py::test_codex_manual_compact_keeps_native_lifecycle_and_command_after_reload[failed-True] | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store is not a private bounded file |
| tests/test_manual_compaction.py::test_codex_manual_compact_keeps_native_lifecycle_and_command_after_reload[interrupted-False] | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store is not a private bounded file |
| tests/test_manual_compaction.py::test_codex_manual_compact_keeps_native_lifecycle_and_command_after_reload[interrupted-True] | cc_remote.wrapper.codex_controls.CodexControlStoreError: Codex control store is not a private bounded file |
| tests/test_markdown_preview.py::test_code_open_reads_os_readable_files_outside_session[False-claude] | AssertionError: assert '没有权限读取该文件' is None |
| tests/test_markdown_preview.py::test_code_open_reads_os_readable_files_outside_session[False-codex] | AssertionError: assert '没有权限读取该文件' is None |
| tests/test_markdown_preview.py::test_code_open_reads_os_readable_files_outside_session[True-claude] | AssertionError: assert '读取文件失败' is None |
| tests/test_markdown_preview.py::test_code_open_reads_os_readable_files_outside_session[True-codex] | AssertionError: assert '读取文件失败' is None |
| tests/test_markdown_preview.py::test_codex_image_view_process_uses_its_item_snapshot_not_reused_path | KeyError: 'preview_id' |
| tests/test_markdown_preview.py::test_history_write_replay_never_rebinds_a_replaced_external_file | AssertionError: assert False |
| tests/test_markdown_preview.py::test_machine_preview_rekey_and_delete_migrate_then_clear_state | RuntimeError: Claude profile state migration is incomplete |
| tests/test_markdown_preview.py::test_markdown_preview_reads_utf8_and_normalizes_paths | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_markdown_preview_rejects_absolute_and_symlink_escape | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_markdown_preview.py::test_markdown_preview_rejects_invalid_utf8 | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_markdown_preview_rejects_special_files_without_blocking | AttributeError: module 'os' has no attribute 'mkfifo' |
| tests/test_markdown_preview.py::test_markdown_preview_truncates_without_breaking_split_utf8 | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_markdown_save_conflict_never_overwrites_newer_content | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_markdown_preview.py::test_markdown_save_is_atomic_and_preserves_bom_crlf_and_mode | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_markdown_preview.py::test_markdown_save_rejects_non_markdown_and_symlink | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_markdown_preview.py::test_markdown_save_response_is_correlated_and_duplicate_is_not_reexecuted | assert False |
| tests/test_markdown_preview.py::test_mermaid_artifact_reads_exact_source_as_text[MERMAID] | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_mermaid_artifact_reads_exact_source_as_text[mmd] | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_misnamed_rasters_use_content_type_without_changing_file[GIF-image/gif] | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_misnamed_rasters_use_content_type_without_changing_file[JPEG-image/jpeg] | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_misnamed_rasters_use_content_type_without_changing_file[PNG-image/png] | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_misnamed_rasters_use_content_type_without_changing_file[WEBP-image/webp] | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_office_preview_converts_inside_ephemeral_sandbox | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_preview_asset_is_type_limited_and_bounded | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_preview_authorization_is_client_session_and_identity_bound | AssertionError: assert False |
| tests/test_markdown_preview.py::test_preview_responses_are_requester_routed_and_correlated | AssertionError: assert ('preview-1' == 'preview-1' |
| tests/test_markdown_preview.py::test_raster_sniffing_does_not_enable_active_or_unrecognized_formats[%PDF-1.7] | AssertionError: Regex pattern did not match. |
| tests/test_markdown_preview.py::test_raster_sniffing_does_not_enable_active_or_unrecognized_formats[<html>hi</html>] | AssertionError: Regex pattern did not match. |
| tests/test_markdown_preview.py::test_raster_sniffing_does_not_enable_active_or_unrecognized_formats[<svg></svg>] | AssertionError: Regex pattern did not match. |
| tests/test_markdown_preview.py::test_raster_sniffing_does_not_enable_active_or_unrecognized_formats[unknown] | AssertionError: Regex pattern did not match. |
| tests/test_markdown_preview.py::test_rendered_artifact_rejects_mismatched_content_type | AssertionError: Regex pattern did not match. |
| tests/test_markdown_preview.py::test_rendered_artifacts_read_html_images_and_pdf_without_persistence | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_rendered_preview_response_is_requester_routed | AssertionError: assert ('image' == 'image' |
| tests/test_markdown_preview.py::test_source_preview_reads_utf8_and_reports_text_format | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_source_preview_rejects_binary_content | AssertionError: Regex pattern did not match. |
| tests/test_markdown_preview.py::test_successful_codex_patch_grants_multiple_exact_cross_cwd_paths | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_successful_external_image_read_serves_an_immutable_snapshot[Read] | AttributeError: 'PreviewAuthorizationRequired' object has no attribute 'error' |
| tests/test_markdown_preview.py::test_successful_external_image_read_serves_an_immutable_snapshot[view_image] | AttributeError: 'PreviewAuthorizationRequired' object has no attribute 'error' |
| tests/test_markdown_preview.py::test_successful_write_grants_exact_cross_cwd_preview_and_edit | AssertionError: assert '没有权限读取该文件' is None |
| tests/test_markdown_preview.py::test_svg_preview_accepts_exporter_prologs_without_changing_bytes[<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 …] | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_svg_preview_accepts_exporter_prologs_without_changing_bytes[<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 …] | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_svg_preview_accepts_exporter_prologs_without_changing_bytes[<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 …] | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_svg_preview_accepts_exporter_prologs_without_changing_bytes[<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 …] | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_svg_preview_accepts_exporter_prologs_without_changing_bytes[<svg/>-<!-- Exported diagram -->\n] | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_svg_preview_accepts_exporter_prologs_without_changing_bytes[<svg/>-<!-- Text containing > and <svg> is only a com…] | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_svg_preview_accepts_exporter_prologs_without_changing_bytes[<svg/>-<?xml version="1.0"?>\n<!-- Before DTD -->\n<!…] | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_svg_preview_accepts_exporter_prologs_without_changing_bytes[<svg/>-\ufeff<?xml version="1.0" encoding="UTF-8" sta…] | ValueError: 没有权限读取该文件 |
| tests/test_markdown_preview.py::test_svg_preview_does_not_find_a_root_inside_comments_or_other_content[<!-- <svg/> --> <html/>] | AssertionError: Regex pattern did not match. |
| tests/test_markdown_preview.py::test_svg_preview_does_not_find_a_root_inside_comments_or_other_content[<!-- a comment --> <svg-script/>] | AssertionError: Regex pattern did not match. |
| tests/test_markdown_preview.py::test_svg_preview_does_not_find_a_root_inside_comments_or_other_content[<!-- a comment --> not xml <svg/>] | AssertionError: Regex pattern did not match. |
| tests/test_markdown_preview.py::test_svg_preview_does_not_find_a_root_inside_comments_or_other_content[<!-- only a comment -->] | AssertionError: Regex pattern did not match. |
| tests/test_markdown_preview.py::test_svg_preview_does_not_find_a_root_inside_comments_or_other_content[<!-- unfinished <svg/>] | AssertionError: Regex pattern did not match. |
| tests/test_markdown_preview.py::test_svg_preview_does_not_find_a_root_inside_comments_or_other_content[<!DOCTYPE html><!-- <svg/> --><html><svg/></htm…] | AssertionError: Regex pattern did not match. |
| tests/test_markdown_preview.py::test_work_external_preview_requires_explicit_read_only_authorization | AssertionError: assert '' == '# generated' |
| tests/test_multisession.py::test_capture_does_not_steal_focus_from_background_session | RuntimeError: Claude profile state migration is incomplete |
| tests/test_multisession.py::test_capture_follows_focus_when_captured_session_is_focused | RuntimeError: Claude profile state migration is incomplete |
| tests/test_multisession.py::test_capture_reparents_btw_catalog_before_session_rekey_is_published | RuntimeError: Claude profile state migration is incomplete |
| tests/test_multisession.py::test_emit_routes_by_temp_key_before_capture_then_real_sid | RuntimeError: Claude profile state migration is incomplete |
| tests/test_multisession.py::test_hello_restores_completed_parent_continuation[False] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_multisession.py::test_hello_restores_completed_parent_continuation[True] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_multisession.py::test_lost_rekey_is_replayed_before_cursor_catchup | RuntimeError: Claude profile state migration is incomplete |
| tests/test_presentation_sync.py::test_claude_completion_receipt_prefers_stable_checkpoint_identity | AssertionError: assert False |
| tests/test_presentation_sync.py::test_codex_catalog_claims_only_unambiguous_v1_receipt | AssertionError: assert None == 'legacy-turn' |
| tests/test_presentation_sync.py::test_codex_steer_segment_does_not_publish_completion_receipt | AssertionError: assert False |
| tests/test_presentation_sync.py::test_cold_session_catalog_carries_durable_completion_receipts | cc_remote.wrapper.session_presentation.SessionPresentationStoreError: session presentation store could not be persisted |
| tests/test_presentation_sync.py::test_cold_session_completion_can_be_acknowledged_without_resume | cc_remote.wrapper.session_presentation.SessionPresentationStoreError: session presentation store could not be persisted |
| tests/test_presentation_sync.py::test_completion_ack_retry_recomputes_newer_unread_after_lost_ack | AssertionError: assert None == 'turn-1' |
| tests/test_presentation_sync.py::test_goal_dismissal_broadcasts_and_cannot_hide_a_replacement | AssertionError: assert False |
| tests/test_presentation_sync.py::test_goal_dismissal_retry_recomputes_replacement_after_lost_ack | RuntimeError: coroutine raised StopIteration |
| tests/test_presentation_sync.py::test_main_completion_acknowledgement_broadcasts_and_seeds_reconnect | AssertionError: assert ['turn_end'] == ['turn_end', ...letion_state'] |
| tests/test_presentation_sync.py::test_profiled_codex_receipts_use_the_routed_session_id | AssertionError: assert {} == {'completion_..._revision': 1} |
| tests/test_presentation_sync.py::test_stale_completion_ack_never_clears_a_newer_turn | AssertionError: assert None == 'turn-2' |
| tests/test_profile_rollback.py::test_failed_claude_upgrade_rolls_back_and_retries_whole_migration[False-absent] | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_profile_rollback.py::test_failed_claude_upgrade_rolls_back_and_retries_whole_migration[False-multi] | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_profile_rollback.py::test_failed_claude_upgrade_rolls_back_and_retries_whole_migration[False-single] | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_profile_rollback.py::test_failed_claude_upgrade_rolls_back_and_retries_whole_migration[True-absent] | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_profile_rollback.py::test_failed_claude_upgrade_rolls_back_and_retries_whole_migration[True-multi] | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_profile_rollback.py::test_failed_claude_upgrade_rolls_back_and_retries_whole_migration[True-single] | cc_remote.wrapper.claude_controls.ClaudeControlStoreError: Claude control store could not be persisted |
| tests/test_profile_rollback.py::test_snapshot_refuses_symlinks[codex-checkpoints] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_profile_rollback.py::test_snapshot_refuses_symlinks[viewer-pages.json] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_query_queue.py::test_replacement_priority_cancel_and_hello_projection_are_authoritative | RuntimeError: Claude profile state migration is incomplete |
| tests/test_question_batches.py::test_batch_rejects_partial_invalid_and_wrong_client_without_advancing | RuntimeError: Claude profile state migration is incomplete |
| tests/test_question_batches.py::test_mcp_batch_runs_through_machine_and_returns_ordered_answers | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_question_batches.py::test_real_mcp_handler_accepts_legacy_and_batch_and_rejects_mixed_input | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_reliability_edges.py::test_cached_create_response_tracks_temp_to_real_rekey | RuntimeError: Claude profile state migration is incomplete |
| tests/test_reliability_edges.py::test_client_hello_does_not_prebind_frames_before_latest_steer | RuntimeError: Codex profile state migration is incomplete |
| tests/test_reliability_edges.py::test_client_hello_does_not_retarget_another_clients_ring_frame | RuntimeError: Claude profile state migration is incomplete |
| tests/test_reliability_edges.py::test_client_hello_preseeds_proven_current_suffix_after_binding_eviction | RuntimeError: Codex profile state migration is incomplete |
| tests/test_reliability_edges.py::test_client_hello_replays_only_cursor_sessions_and_routes_every_frame | RuntimeError: Claude profile state migration is incomplete |
| tests/test_reliability_edges.py::test_client_hello_reseeds_binding_before_tail_after_cursor_passed_owner | RuntimeError: Codex profile state migration is incomplete |
| tests/test_reliability_edges.py::test_duplicate_new_session_replays_snapshot_and_focus_without_creating_again | RuntimeError: coroutine raised StopIteration |
| tests/test_reliability_edges.py::test_fresh_hello_reseeds_owner_when_current_boundary_left_ring | RuntimeError: Codex profile state migration is incomplete |
| tests/test_reliability_edges.py::test_session_lists_echo_engine_and_are_unicast_to_each_requester | AssertionError: assert [] == [('claude', '...-codex-work')] |
| tests/test_rollback_commands.py::test_rollback_journal_atomic_replace_failure_preserves_previous_state | cc_remote.wrapper.rollback_commands.RollbackJournalError: rollback journal could not be persisted |
| tests/test_rollback_commands.py::test_rollback_journal_capacity_never_evicts_idempotency_evidence | cc_remote.wrapper.rollback_commands.RollbackJournalError: rollback journal could not be persisted |
| tests/test_rollback_commands.py::test_rollback_journal_corruption_fails_closed[invalid-identity] | cc_remote.wrapper.rollback_commands.RollbackJournalError: rollback journal could not be persisted |
| tests/test_rollback_commands.py::test_rollback_journal_corruption_fails_closed[invalid-json] | cc_remote.wrapper.rollback_commands.RollbackJournalError: rollback journal could not be persisted |
| tests/test_rollback_commands.py::test_rollback_journal_corruption_fails_closed[result-before-complete] | cc_remote.wrapper.rollback_commands.RollbackJournalError: rollback journal could not be persisted |
| tests/test_rollback_commands.py::test_rollback_journal_enforces_state_transitions_and_one_result | cc_remote.wrapper.rollback_commands.RollbackJournalError: rollback journal could not be persisted |
| tests/test_rollback_commands.py::test_rollback_journal_file_limit_failure_keeps_submitted_boundary | cc_remote.wrapper.rollback_commands.RollbackJournalError: rollback journal could not be persisted |
| tests/test_rollback_commands.py::test_rollback_journal_missing_after_initialization_fails_closed | cc_remote.wrapper.rollback_commands.RollbackJournalError: rollback journal could not be persisted |
| tests/test_rollback_commands.py::test_rollback_journal_rejects_identity_reuse_and_invalid_engine_target | cc_remote.wrapper.rollback_commands.RollbackJournalError: rollback journal could not be persisted |
| tests/test_rollback_commands.py::test_rollback_journal_rejects_symlink_state_file | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_rollback_commands.py::test_rollback_journal_serializes_cross_instance_submission_claim | cc_remote.wrapper.rollback_commands.RollbackJournalError: rollback journal could not be persisted |
| tests/test_rollback_commands.py::test_rollback_journal_stays_readable_when_wall_clock_moves_backwards | cc_remote.wrapper.rollback_commands.RollbackJournalError: rollback journal could not be persisted |
| tests/test_rollback_commands.py::test_rollback_journal_survives_restart_without_reclaiming_submission | cc_remote.wrapper.rollback_commands.RollbackJournalError: rollback journal could not be persisted |
| tests/test_session_artifacts.py::test_artifact_directory_context_does_not_escape_its_file_list[## Another section] | AssertionError: assert ['C:\\tmp\\di...s\\first.png'] == ['/tmp/diagrams/first.png'] |
| tests/test_session_artifacts.py::test_artifact_directory_context_does_not_escape_its_file_list[\u53e6\u4e00\u4e2a\u6bb5\u843d\u3002] | AssertionError: assert ['C:\\tmp\\di...s\\first.png'] == ['/tmp/diagrams/first.png'] |
| tests/test_session_artifacts.py::test_artifact_directory_context_does_not_escape_its_file_list[\u53ef\u80fd\u5728 `/tmp/one/` \u6216 `/tmp/two/`\uff1a…] | AssertionError: assert ['C:\\tmp\\di...s\\first.png'] == ['/tmp/diagrams/first.png'] |
| tests/test_session_artifacts.py::test_artifact_directory_context_does_not_escape_its_file_list[```sh\nls /tmp/diagrams\n```] | AssertionError: assert ['C:\\tmp\\di...s\\first.png'] == ['/tmp/diagrams/first.png'] |
| tests/test_session_artifacts.py::test_artifact_directory_context_does_not_escape_its_file_list[~~~text\nexample\n~~~] | AssertionError: assert ['C:\\tmp\\di...s\\first.png'] == ['/tmp/diagrams/first.png'] |
| tests/test_session_artifacts.py::test_artifact_directory_introduction_does_not_reinterpret_files_or_commands | AssertionError: assert ['C:\\tmp\\report.pdf'] == ['/tmp/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_introduction_does_not_require_trailing_slash[../output-/output] | AssertionError: assert ['C:\\output\...t\\chart.svg'] == ['/output/rep...ut/chart.svg'] |
| tests/test_session_artifacts.py::test_artifact_directory_introduction_does_not_require_trailing_slash[./output-/workspace/output] | AssertionError: assert ['C:\\workspa...t\\chart.svg'] == ['/workspace/...ut/chart.svg'] |
| tests/test_session_artifacts.py::test_artifact_directory_introduction_does_not_require_trailing_slash[/tmp/output-/tmp/output] | AssertionError: assert ['C:\\tmp\\ou...t\\chart.svg'] == ['/tmp/output...ut/chart.svg'] |
| tests/test_session_artifacts.py::test_artifact_directory_introduction_does_not_require_trailing_slash[\u8f93\u51fa\u76ee\u5f55-/workspace/\u8f93\u51fa…] | AssertionError: assert ['C:\\workspa...录\\chart.svg'] == ['/workspace/...目录/chart.svg'] |
| tests/test_session_artifacts.py::test_artifact_directory_introduction_does_not_require_trailing_slash[docs/output-/workspace/docs/output] | AssertionError: assert ['C:\\workspa...t\\chart.svg'] == ['/workspace/...ut/chart.svg'] |
| tests/test_session_artifacts.py::test_artifact_directory_introduction_does_not_require_trailing_slash[output-/workspace/output] | AssertionError: assert ['C:\\workspa...t\\chart.svg'] == ['/workspace/...ut/chart.svg'] |
| tests/test_session_artifacts.py::test_artifact_directory_introduction_does_not_require_trailing_slash[output.v1-/workspace/output.v1] | AssertionError: assert ['C:\\workspa...1\\chart.svg'] == ['/workspace/...v1/chart.svg'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| Description \| **\u6587\u4ef6\u540d** \| Size \|\n\|---…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| Description \| **\u6587\u4ef6\u540d** \| Size \|\n\|---…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| Description \| **\u6587\u4ef6\u540d** \| Size \|\n\|---…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| Description \| **\u6587\u4ef6\u540d** \| Size \|\n\|---…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| Description \| **\u6587\u4ef6\u540d** \| Size \|\n\|---…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| Description \| File name \|\n\|---\|---\|\n\| Uses `lef…] | AssertionError: assert ['C:\\tmp\\ou...t\\chart.svg'] == ['/tmp/output...ut/chart.svg'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| Description \| File name \|\n\|---\|---\|\n\| Uses `lef…] | AssertionError: assert ['C:\\tmp\\ou...t\\chart.svg'] == ['/tmp/output...ut/chart.svg'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| Description \| File name \|\n\|---\|---\|\n\| Uses `lef…] | AssertionError: assert ['C:\\tmp\\ou...t\\chart.svg'] == ['/tmp/output...ut/chart.svg'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| Description \| File name \|\n\|---\|---\|\n\| Uses `lef…] | AssertionError: assert ['C:\\tmp\\ou...t\\chart.svg'] == ['/tmp/output...ut/chart.svg'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| Description \| File name \|\n\|---\|---\|\n\| Uses `lef…] | AssertionError: assert ['C:\\tmp\\ou...t\\chart.svg'] == ['/tmp/output...ut/chart.svg'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| File \| Description \|\n\|---\|---\|\n\| `report.pdf` \…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| File \| Description \|\n\|---\|---\|\n\| `report.pdf` \…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| File \| Description \|\n\|---\|---\|\n\| `report.pdf` \…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| File \| Description \|\n\|---\|---\|\n\| `report.pdf` \…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| File \| Description \|\n\|---\|---\|\n\| `report.pdf` \…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| File \| Description \|\n\|---\|---\|\n\| \| From `confi…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| File \| Description \|\n\|---\|---\|\n\| \| From `confi…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| File \| Description \|\n\|---\|---\|\n\| \| From `confi…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| File \| Description \|\n\|---\|---\|\n\| \| From `confi…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| File \| Description \|\n\|---\|---\|\n\| \| From `confi…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| File \| Description\n\|:---\|---:\n\| `report.pdf` \| F…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| File \| Description\n\|:---\|---:\n\| `report.pdf` \| F…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| File \| Description\n\|:---\|---:\n\| `report.pdf` \| F…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| File \| Description\n\|:---\|---:\n\| `report.pdf` \| F…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_directory_table_only_discovers_filename_cell[\| File \| Description\n\|:---\|---:\n\| `report.pdf` \| F…] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_discovery_handles_directory_and_separate_delivery_names | AssertionError: assert ['C:\\tmp\\di...wen-call.png'] == ['/tmp/diagra...wen-call.png'] |
| tests/test_session_artifacts.py::test_artifact_discovery_only_follows_explicit_local_destinations | AssertionError: assert ['C:\\project...t\\page.html'] == ['/project/a ...ut/page.html'] |
| tests/test_session_artifacts.py::test_artifact_inline_discovery_is_bounded_and_deduplicated | AssertionError: assert 'C:\\tmp\\result-127.png' == '/tmp/result-127.png' |
| tests/test_session_artifacts.py::test_artifact_inline_discovery_skips_closed_and_unclosed_code_examples | AssertionError: assert ['C:\\tmp\\actual.png'] == ['/tmp/actual.png'] |
| tests/test_session_artifacts.py::test_artifact_inline_paths_are_explicit_and_do_not_reinterpret_link_labels | AssertionError: assert ['C:\\workspa...cs\\notes.md'] == ['/workspace/...ocs/notes.md'] |
| tests/test_session_artifacts.py::test_artifact_pipe_separated_prose_does_not_extend_directory_context | AssertionError: assert ['C:\\tmp\\ou...\config.yaml'] == ['/tmp/output.../config.yaml'] |
| tests/test_session_artifacts.py::test_artifact_root_symlink_is_rejected | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_session_artifacts.py::test_artifact_single_column_table_can_omit_body_and_delimiter_pipes | AssertionError: assert ['C:\\tmp\\ou...t\\chart.svg'] == ['/tmp/output...ut/chart.svg'] |
| tests/test_session_artifacts.py::test_artifact_table_columns_reset_between_directories | AssertionError: assert ['C:\\tmp\\on...\\second.pdf'] == ['/tmp/one/fi...o/second.pdf'] |
| tests/test_session_artifacts.py::test_artifact_table_context_ends_at_blank_lines_and_new_blocks[# Other files] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_table_context_ends_at_blank_lines_and_new_blocks[- Another list] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_table_context_ends_at_blank_lines_and_new_blocks[> Another topic] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_table_context_ends_at_blank_lines_and_new_blocks[] | AssertionError: assert ['C:\\tmp\\ou...\\report.pdf'] == ['/tmp/output/report.pdf'] |
| tests/test_session_artifacts.py::test_artifact_upload_survives_reopen_and_has_isolated_identity | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_automatic_thumbnail_does_not_grant_external_file_access[inline] | IndexError: list index out of range |
| tests/test_session_artifacts.py::test_automatic_thumbnail_does_not_grant_external_file_access[markdown] | IndexError: list index out of range |
| tests/test_session_artifacts.py::test_catalog_scope_mismatch_and_foreign_preview_are_rejected[full] | IndexError: list index out of range |
| tests/test_session_artifacts.py::test_catalog_scope_mismatch_and_foreign_preview_are_rejected[thumbnail] | IndexError: list index out of range |
| tests/test_session_artifacts.py::test_claude_steering_upload_outlives_its_temporary_prompt_files[False] | IndexError: list index out of range |
| tests/test_session_artifacts.py::test_cold_artifacts_validate_account_space_before_history_or_preview[claude] | IndexError: list index out of range |
| tests/test_session_artifacts.py::test_cold_artifacts_validate_account_space_before_history_or_preview[codex] | IndexError: list index out of range |
| tests/test_session_artifacts.py::test_concurrent_stores_cannot_overbook_upload_quota | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_dsh_uses_native_provenance_and_repairs_only_legacy_session_references | assert 0 == 1 |
| tests/test_session_artifacts.py::test_durable_upload_limits_survive_reopen_without_eviction[MAX_SESSION_UPLOAD_BYTES] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_durable_upload_limits_survive_reopen_without_eviction[MAX_SESSION_UPLOAD_FILES] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_durable_upload_limits_survive_reopen_without_eviction[MAX_TOTAL_UPLOAD_BYTES] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_durable_upload_limits_survive_reopen_without_eviction[MAX_TOTAL_UPLOAD_FILES] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_failed_upload_batch_removes_new_copies_and_rolls_back_index[fsync] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_failed_upload_batch_removes_new_copies_and_rolls_back_index[metadata] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_live_and_historical_delivery_lists_share_the_same_artifact_catalog[list--claude] | AssertionError: assert set() == {'delivery-fl...e-layers.png'} |
| tests/test_session_artifacts.py::test_live_and_historical_delivery_lists_share_the_same_artifact_catalog[list--codex] | AssertionError: assert set() == {'delivery-fl...e-layers.png'} |
| tests/test_session_artifacts.py::test_live_and_historical_delivery_lists_share_the_same_artifact_catalog[list-/-claude] | AssertionError: assert set() == {'delivery-fl...e-layers.png'} |
| tests/test_session_artifacts.py::test_live_and_historical_delivery_lists_share_the_same_artifact_catalog[list-/-codex] | AssertionError: assert set() == {'delivery-fl...e-layers.png'} |
| tests/test_session_artifacts.py::test_live_and_historical_delivery_lists_share_the_same_artifact_catalog[table--claude] | AssertionError: assert set() == {'delivery-fl...e-layers.png'} |
| tests/test_session_artifacts.py::test_live_and_historical_delivery_lists_share_the_same_artifact_catalog[table--codex] | AssertionError: assert set() == {'delivery-fl...e-layers.png'} |
| tests/test_session_artifacts.py::test_live_and_historical_delivery_lists_share_the_same_artifact_catalog[table-/-claude] | AssertionError: assert set() == {'delivery-fl...e-layers.png'} |
| tests/test_session_artifacts.py::test_live_and_historical_delivery_lists_share_the_same_artifact_catalog[table-/-codex] | AssertionError: assert set() == {'delivery-fl...e-layers.png'} |
| tests/test_session_artifacts.py::test_live_and_historical_delivery_lists_share_the_same_artifact_catalog[table-no-outer-pipes--claude] | AssertionError: assert set() == {'delivery-fl...e-layers.png'} |
| tests/test_session_artifacts.py::test_live_and_historical_delivery_lists_share_the_same_artifact_catalog[table-no-outer-pipes--codex] | AssertionError: assert set() == {'delivery-fl...e-layers.png'} |
| tests/test_session_artifacts.py::test_live_and_historical_delivery_lists_share_the_same_artifact_catalog[table-no-outer-pipes-/-claude] | AssertionError: assert set() == {'delivery-fl...e-layers.png'} |
| tests/test_session_artifacts.py::test_live_and_historical_delivery_lists_share_the_same_artifact_catalog[table-no-outer-pipes-/-codex] | AssertionError: assert set() == {'delivery-fl...e-layers.png'} |
| tests/test_session_artifacts.py::test_live_discovery_rekey_and_thinking_exclusion | assert (0 == 1) |
| tests/test_session_artifacts.py::test_long_name_replay_reuses_existing_legacy_copy | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_session_artifacts.py::test_long_named_artifact_can_be_previewed | IndexError: list index out of range |
| tests/test_session_artifacts.py::test_long_upload_names_fit_byte_limit_and_keep_preview_extension[\U00020000\U00020000\U00020000\U00020000\U00020000\U…] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_long_upload_names_fit_byte_limit_and_keep_preview_extension[\u754c\u754c\u754c\u754c\u754c\u754c\u754c\u754c\u75…] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_long_upload_names_fit_byte_limit_and_keep_preview_extension[\u754c\u754c\u754c\u754c\u754c\u754c\u754c\u754c\u75…] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_long_upload_names_fit_byte_limit_and_keep_preview_extension[\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9…] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_long_upload_names_fit_byte_limit_and_keep_preview_extension[aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa…] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_misnamed_artifact_image_full_preview_and_thumbnail[delivered] | AssertionError: assert (not '没有权限读取该文件') |
| tests/test_session_artifacts.py::test_misnamed_artifact_image_full_preview_and_thumbnail[upload] | IndexError: list index out of range |
| tests/test_session_artifacts.py::test_orphan_and_legacy_copies_count_towards_global_budget[MAX_TOTAL_UPLOAD_BYTES] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_orphan_and_legacy_copies_count_towards_global_budget[MAX_TOTAL_UPLOAD_FILES] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_oversized_upload_extensions_still_fit_byte_limit[\u754c.\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9…] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_oversized_upload_extensions_still_fit_byte_limit[\u754c.\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9…] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_oversized_upload_extensions_still_fit_byte_limit[a.\u754c\u754c\u754c\u754c\u754c\u754c\u754c\u754c\u754c\u754c\…] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_quota_rejects_entire_batch_before_copying | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_quota_tracks_rekey_and_session_deletion | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_readonly_btw_artifacts_keep_owner_isolation | AssertionError: assert (0 == 1) |
| tests/test_session_artifacts.py::test_rekey_and_delete_only_remove_owned_upload_copies | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_thumbnail_is_small_readonly_and_never_opens_full_preview[delivered] | AssertionError: assert (not '文件已移动、删除或暂时无法预览，请刷新 Artifacts') |
| tests/test_session_artifacts.py::test_thumbnail_is_small_readonly_and_never_opens_full_preview[upload] | IndexError: list index out of range |
| tests/test_session_artifacts.py::test_truncated_upload_names_keep_distinct_copies | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_upload_reserves_free_disk_before_creating_copy | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_artifacts.py::test_uploaded_file_previews_without_native_session_call | IndexError: list index out of range |
| tests/test_session_pins.py::test_claude_session_list_includes_durable_pin | cc_remote.wrapper.session_pins.SessionPinStoreError: session pin store could not be persisted |
| tests/test_session_pins.py::test_claude_session_list_uses_stable_title_not_latest_prompt | RuntimeError: coroutine raised StopIteration |
| tests/test_session_pins.py::test_pin_profile_revisions_round_trip_consistently | cc_remote.wrapper.session_pins.SessionPinStoreError: session pin store could not be persisted |
| tests/test_session_pins.py::test_relay_roundtrip_null_engine_does_not_reject_claude_pin | AssertionError: assert frozenset() == {'claude-1'} |
| tests/test_session_pins.py::test_session_pin_store_persists_and_unpins | cc_remote.wrapper.session_pins.SessionPinStoreError: session pin store could not be persisted |
| tests/test_session_plans.py::test_session_plan_profile_migration_is_replay_safe | cc_remote.wrapper.session_plans.SessionPlanStoreError: session plan store could not be persisted |
| tests/test_session_plans.py::test_session_plan_store_reads_v2_as_unsettled_and_upgrades_on_write | cc_remote.wrapper.session_plans.SessionPlanStoreError: session plan store could not be persisted |
| tests/test_session_plans.py::test_session_plan_store_rejects_symlinks | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_session_plans.py::test_session_plan_store_retires_only_settled_previous_turns | cc_remote.wrapper.session_plans.SessionPlanStoreError: session plan store could not be persisted |
| tests/test_session_plans.py::test_session_plan_store_round_trips_rekeys_and_deletes | cc_remote.wrapper.session_plans.SessionPlanStoreError: session plan store could not be persisted |
| tests/test_session_presentation.py::test_claiming_legacy_can_target_a_profile_scoped_codex_wire_id | cc_remote.wrapper.session_presentation.SessionPresentationStoreError: session presentation store could not be persisted |
| tests/test_session_presentation.py::test_claiming_legacy_does_not_replace_newer_scoped_receipt | cc_remote.wrapper.session_presentation.SessionPresentationStoreError: session presentation store could not be persisted |
| tests/test_session_presentation.py::test_completion_receipts_round_trip_and_reject_stale_acknowledgements | cc_remote.wrapper.session_presentation.SessionPresentationStoreError: session presentation store could not be persisted |
| tests/test_session_presentation.py::test_goal_dismissal_is_scoped_to_one_exact_generation | cc_remote.wrapper.session_presentation.SessionPresentationStoreError: session presentation store could not be persisted |
| tests/test_session_presentation.py::test_presentation_is_engine_scoped_and_v1_bare_ids_are_quarantined | cc_remote.wrapper.session_presentation.SessionPresentationStoreError: session presentation store could not be persisted |
| tests/test_session_presentation.py::test_presentation_profile_migration_is_replay_safe | cc_remote.wrapper.session_presentation.SessionPresentationStoreError: session presentation store could not be persisted |
| tests/test_session_presentation.py::test_session_presentation_rejects_symlinks | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_session_presentation.py::test_session_presentation_rekeys_clears_and_deletes | cc_remote.wrapper.session_presentation.SessionPresentationStoreError: session presentation store could not be persisted |
| tests/test_session_presentation.py::test_v3_quarantine_survives_codex_profile_migration | cc_remote.wrapper.session_presentation.SessionPresentationStoreError: session presentation store could not be persisted |
| tests/test_spreadsheet_preview.py::test_cached_values_multiple_sheets_and_formula_are_preserved_without_execution | ValueError: 没有权限读取该文件 |
| tests/test_task_refresh.py::test_catalog_success_does_not_hide_failed_thread_call | AssertionError: Regex pattern did not match. |
| tests/test_task_refresh.py::test_project_override_is_not_promoted_to_user_config | AssertionError: Regex pattern did not match. |
| tests/test_transport_bounds.py::test_connect_honors_explicit_http_proxy_and_bypass[wss://relay.example/ws-environment11-http://new.example:8080] | AssertionError: assert 'http://old.example:7890' == 'http://new.example:8080' |
| tests/test_transport_bounds.py::test_connect_honors_explicit_http_proxy_and_bypass[wss://relay.example/ws-environment20-http://explicit.example:8080] | AssertionError: assert None == 'http://explicit.example:8080' |
| tests/test_turn_changes.py::test_archived_diff_request_is_immutable_scoped_and_never_reads_worktree | AssertionError: assert (False) |
| tests/test_turn_changes.py::test_native_write_uses_proven_before_image_not_a_creation_guess | AssertionError: assert ('--- src/code.py' in '--- src\\code.py\n+++ src\\code.py\n@@ -1 +1 @@\n-A\n+B\n') |
| tests/test_turn_changes.py::test_relative_paths_are_cwd_scoped_and_renames_do_not_duplicate_files | ValueError: too many values to unpack (expected 1) |
| tests/test_turn_file_pages.py::test_file_pages_and_diffs_are_scoped_and_immutable_after_later_edits | TypeError: 'NoneType' object is not subscriptable |
| tests/test_turn_file_pages.py::test_pre_normalization_archives_still_support_pages_and_rekey_cleanup | TypeError: 'NoneType' object is not subscriptable |
| tests/test_work_context.py::test_machine_splits_context_only_for_work | FileNotFoundError: [Errno 2] No such file or directory: '<tmp>' |
| tests/test_workspace_browser.py::test_code_can_browse_parents_absolute_paths_home_and_external_files | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_workspace_browser.py::test_code_external_browsing_still_rejects_symlinks_and_special_files | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_workspace_browser.py::test_directory_handler_allows_code_parent_but_keeps_work_boundary[code] | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_workspace_browser.py::test_directory_navigation_paging_and_hidden_files | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_workspace_browser.py::test_escape_symlinks_special_files_and_changed_page_are_rejected | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_workspaces.py::WorkRegistryTests::test_artifacts_list_only_user_deliverables_inside_private_workspace | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\tmpt8wtbqr4\\outside.txt' -> '<tmp>' |
| tests/test_workspaces.py::WorkRegistryTests::test_claude_policy_copies_only_runtime_provider_settings | AssertionError: 438 != 384 |
| tests/test_workspaces.py::WorkRegistryTests::test_claude_policy_refreshes_a_rotated_oauth_token | AssertionError: 438 != 384 |
| tests/test_workspaces.py::WorkRegistryTests::test_deleting_running_schedule_defers_cleanup_until_completion | PermissionError: [WinError 32] 另一个程序正在使用此文件，进程无法访问。: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\tmpt6qcqeqq\\provider\\work\\registry.sqlite3' |
| tests/test_workspaces.py::WorkRegistryTests::test_legacy_schedule_profile_backfill_is_topology_independent | PermissionError: [WinError 32] 另一个程序正在使用此文件，进程无法访问。: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\tmp8lw3f3o3\\provider\\work\\legacy-codex\\registry.sqli… |
| tests/test_workspaces.py::WorkRegistryTests::test_legacy_schema_migration_is_safe_under_concurrent_initialize | PermissionError: [WinError 32] 另一个程序正在使用此文件，进程无法访问。: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\tmpt7y29tyx\\provider\\work\\registry.sqlite3' |
| tests/test_workspaces.py::WorkRegistryTests::test_new_session_creates_private_uploads_inside_workspace | AssertionError: 511 != 448 |
| tests/test_workspaces.py::WorkRegistryTests::test_project_sources_plugins_are_materialized_into_private_session | AssertionError: 511 != 448 |
| tests/test_workspaces.py::WorkRegistryTests::test_repeated_initialize_does_not_rebuild_profile_identity_index | PermissionError: [WinError 32] 另一个程序正在使用此文件，进程无法访问。: 'C:\\Users\\XUHAOD~1\\AppData\\Local\\Temp\\codeg-acp\\18072-98219f0c\\tmp0f0_6lma\\provider\\work\\registry.sqlite3' |
| tests/test_wrapper_core_fixes.py::test_background_job_scan_caps_entries_and_state_file_size | AssertionError: assert set() == {'session-valid'} |
| tests/test_wrapper_core_fixes.py::test_bounded_subprocess_discards_residual_output_without_communicate | AttributeError: <module 'os' (frozen)> has no attribute 'killpg' |
| tests/test_wrapper_core_fixes.py::test_bounded_subprocess_output_has_wall_clock_timeout | AttributeError: module 'signal' has no attribute 'SIGKILL'. Did you mean: 'SIGILL'? |
| tests/test_wrapper_core_fixes.py::test_claude_alias_binding_rejects_stale_turn_generation | RuntimeError: Claude profile state migration is incomplete |
| tests/test_wrapper_core_fixes.py::test_claude_alias_flush_never_enters_multi_profile_codex_history | RuntimeError: Claude profile state migration is incomplete |
| tests/test_wrapper_core_fixes.py::test_claude_interrupt_hands_remaining_background_work_the_original_deadline | TimeoutError |
| tests/test_wrapper_core_fixes.py::test_claude_live_replayed_user_uuid_is_persisted_as_browser_alias | AssertionError: assert {} == {'2259073b-76...21f509c951b0'} |
| tests/test_wrapper_core_fixes.py::test_claude_managed_interrupt_owns_recovery_with_background_activity[False] | TimeoutError |
| tests/test_wrapper_core_fixes.py::test_claude_managed_interrupt_owns_recovery_with_background_activity[True] | TimeoutError |
| tests/test_wrapper_core_fixes.py::test_claude_run_turn_binds_transcript_before_delayed_sdk_replay | TimeoutError |
| tests/test_wrapper_core_fixes.py::test_claude_silence_emits_neutral_notices_without_ending_or_reconnecting | TimeoutError |
| tests/test_wrapper_core_fixes.py::test_claude_silence_notice_is_suppressed_while_question_is_open | TimeoutError |
| tests/test_wrapper_core_fixes.py::test_codex_session_settings_reads_bounded_tail_of_oversized_source | AssertionError: assert {} == {'model': 'gp...: 'fast', ...} |
| tests/test_wrapper_core_fixes.py::test_diff_rejects_untracked_fifo_without_opening_it | AttributeError: module 'os' has no attribute 'mkfifo' |
| tests/test_wrapper_core_fixes.py::test_git_diff_output_is_streamed_to_a_hard_limit | AttributeError: module 'signal' has no attribute 'SIGKILL'. Did you mean: 'SIGILL'? |
| tests/test_wrapper_core_fixes.py::test_managed_finalizer_reconciles_background_result_race | assert False is True |
| tests/test_wrapper_core_fixes.py::test_new_claude_session_flushes_pending_browser_alias_after_rekey | AssertionError: assert 'tmp-client-alias' == 'fa800ca3-18e...-a33fe52e2f56' |
| tests/test_wrapper_startup.py::test_default_account_check_resolves_home_even_with_legacy_none_env | AssertionError: assert [{'profile': ...ertionError'}] == [{'profile': ...us': 'ready'}] |

## 子项目 3（IPC/service/broker）— 225 个

按文件汇总：

| 测试文件 | 失败数 | 主要原因 |
|---|---|---|
| tests/test_claude_steering.py | 46 | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py | 25 | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py | 23 | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_broker.py | 17 | failed on setup with "cc_remote.claude_broker.server.BrokerSecurityError: broker socket directory must not be accessible |
| tests/test_claude_broker_machine.py | 14 | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_service_lease.py | 13 | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_pending_query.py | 11 | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_delivery.py | 11 | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_restart.py | 11 | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_task_merge.py | 9 | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_permissions.py | 7 | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_callbacks.py | 7 | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_recovery.py | 7 | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_startup.py | 7 | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_manual_compaction.py | 7 | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_compaction_receipts.py | 5 | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steer_attachments.py | 4 | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_live_context.py | 1 | PermissionError: Claude service directory must be private and owned by this user |

明细：

| 测试 | 失败原因（第一行异常） |
|---|---|
| tests/test_claude_background_completion.py::test_activity_before_human_echo_settles_and_accepts_next_prompt[request-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_activity_before_human_echo_settles_and_accepts_next_prompt[request-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_activity_before_human_echo_settles_and_accepts_next_prompt[stream-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_activity_before_human_echo_settles_and_accepts_next_prompt[stream-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[False-False-False-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[False-False-False-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[False-False-True-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[False-False-True-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[False-True-False-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[False-True-False-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[False-True-True-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[False-True-True-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[True-False-False-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[True-False-False-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[True-False-True-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[True-False-True-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[True-True-False-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[True-True-False-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[True-True-True-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_batched_task_inputs_settle_and_background_job_does_not_block_next_prompt[True-True-True-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_failed_shared_boundary_retains_journal_for_recovery | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_shared_terminal_replays_without_resubmission_or_retiring_later_activity[False-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_shared_terminal_replays_without_resubmission_or_retiring_later_activity[False-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_shared_terminal_replays_without_resubmission_or_retiring_later_activity[True-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_background_completion.py::test_shared_terminal_replays_without_resubmission_or_retiring_later_activity[True-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_broker.py::test_broker_persists_sdk_controls_into_next_native_resume [error] | failed on setup with "cc_remote.claude_broker.server.BrokerSecurityError: broker socket directory must not be accessible by other users" |
| tests/test_claude_broker.py::test_cli_attachment_treats_control_input_busy_as_recoverable | NameError: name 'fcntl' is not defined. Did you forget to import 'fcntl'? |
| tests/test_claude_broker.py::test_cli_resume_normalizes_an_explicit_cwd_before_broker_request | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_claude_broker.py::test_context_query_retries_one_ignored_startup_command | cc_remote.claude_broker.server.BrokerSecurityError: broker socket directory must not be accessible by other users |
| tests/test_claude_broker.py::test_machine_broker_controls_reach_native_tui_and_use_footer_confirmation[manual-default-alias] [error] | failed on setup with "cc_remote.claude_broker.server.BrokerSecurityError: broker socket directory must not be accessible by other users" |
| tests/test_claude_broker.py::test_machine_broker_controls_reach_native_tui_and_use_footer_confirmation[with-auto] [error] | failed on setup with "cc_remote.claude_broker.server.BrokerSecurityError: broker socket directory must not be accessible by other users" |
| tests/test_claude_broker.py::test_machine_broker_controls_reach_native_tui_and_use_footer_confirmation[without-auto] [error] | failed on setup with "cc_remote.claude_broker.server.BrokerSecurityError: broker socket directory must not be accessible by other users" |
| tests/test_claude_broker.py::test_model_control_confirms_changed_native_prompt_after_fresh_output | cc_remote.claude_broker.server.BrokerSecurityError: broker socket directory must not be accessible by other users |
| tests/test_claude_broker.py::test_native_terminal_permission_footer_updates_broker_state[manual-default-alias] [error] | failed on setup with "cc_remote.claude_broker.server.BrokerSecurityError: broker socket directory must not be accessible by other users" |
| tests/test_claude_broker.py::test_native_terminal_permission_footer_updates_broker_state[with-auto] [error] | failed on setup with "cc_remote.claude_broker.server.BrokerSecurityError: broker socket directory must not be accessible by other users" |
| tests/test_claude_broker.py::test_native_terminal_permission_footer_updates_broker_state[without-auto] [error] | failed on setup with "cc_remote.claude_broker.server.BrokerSecurityError: broker socket directory must not be accessible by other users" |
| tests/test_claude_broker.py::test_new_attach_atomic_send_detach_and_resume [error] | failed on setup with "cc_remote.claude_broker.server.BrokerSecurityError: broker socket directory must not be accessible by other users" |
| tests/test_claude_broker.py::test_peer_uid_and_default_socket_path | TypeError: LoadLibrary() argument 1 must be str, not None |
| tests/test_claude_broker.py::test_second_server_and_unsafe_socket_path_are_rejected | cc_remote.claude_broker.server.BrokerSecurityError: broker socket directory must not be accessible by other users |
| tests/test_claude_broker.py::test_server_rejects_a_peer_uid_mismatch [error] | failed on setup with "cc_remote.claude_broker.server.BrokerSecurityError: broker socket directory must not be accessible by other users" |
| tests/test_claude_broker.py::test_status_list_limits_and_forbidden_claude_flags | cc_remote.claude_broker.server.BrokerSecurityError: broker socket directory must not be accessible by other users |
| tests/test_claude_broker.py::test_terminal_focus_input_waits_for_remote_control_without_detaching [error] | failed on setup with "cc_remote.claude_broker.server.BrokerSecurityError: broker socket directory must not be accessible by other users" |
| tests/test_claude_broker_machine.py::test_broker_attachment_paths_are_submitted_but_not_echoed | IndexError: list index out of range |
| tests/test_claude_broker_machine.py::test_broker_pre_submit_interrupt_materializes_before_terminal | AssertionError: assert ['turn_end'] == ['user_msg', 'turn_end'] |
| tests/test_claude_broker_machine.py::test_broker_reclaim_during_sdk_restore_discards_new_sdk_writer | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_broker_machine.py::test_broker_session_is_listed_before_claude_creates_a_transcript | RuntimeError: coroutine raised StopIteration |
| tests/test_claude_broker_machine.py::test_exited_broker_converges_orphaned_terminal_turn_before_sdk_restore[draining] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_broker_machine.py::test_exited_broker_converges_orphaned_terminal_turn_before_sdk_restore[interrupting] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_broker_machine.py::test_exited_broker_converges_orphaned_terminal_turn_before_sdk_restore[running] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_broker_machine.py::test_exited_broker_restores_sdk_in_place_without_switch_session | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_broker_machine.py::test_interrupt_terminal_boundary_stays_interrupted_not_success | TimeoutError |
| tests/test_claude_broker_machine.py::test_managed_broker_interrupt_uses_ctrl_c_and_unlocks_after_drain | TimeoutError |
| tests/test_claude_broker_machine.py::test_managed_broker_turn_finishes_on_engine_transcript_boundary | AssertionError: assert [] == ['managed prompt'] |
| tests/test_claude_broker_machine.py::test_spawn_uses_live_broker_cwd_without_transcript_metadata | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_broker_machine.py::test_submit_input_busy_race_does_not_publish_unsent_user_message | AssertionError: assert [] == ['losing race'] |
| tests/test_claude_broker_machine.py::test_switch_adopts_restarted_broker_generation_and_restores_write | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_compaction_receipts.py::test_autocompact_result_completes_the_original_turn[False-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_compaction_receipts.py::test_autocompact_result_completes_the_original_turn[False-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_compaction_receipts.py::test_autocompact_result_completes_the_original_turn[True-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_compaction_receipts.py::test_autocompact_result_completes_the_original_turn[True-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_compaction_receipts.py::test_compacted_completion_replays_after_detach_without_resubmitting | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_live_context.py::test_replacement_wrapper_recovers_running_service_context_without_restart | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_permissions.py::test_control_snapshot_fences_replay_after_an_explicit_switch | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_permissions.py::test_native_modes_publish_persist_and_reseed_without_changing_focus[False-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_permissions.py::test_native_modes_publish_persist_and_reseed_without_changing_focus[False-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_permissions.py::test_native_modes_publish_persist_and_reseed_without_changing_focus[True-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_permissions.py::test_native_modes_publish_persist_and_reseed_without_changing_focus[True-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_permissions.py::test_offline_permission_snapshot_survives_pruning_and_skips_older_replay | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_permissions.py::test_service_control_ack_preserves_native_fallback_and_failed_switch | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_task_merge.py::test_absorbed_notifications_keep_one_main_response_and_accept_next_prompt[False-None-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_task_merge.py::test_absorbed_notifications_keep_one_main_response_and_accept_next_prompt[False-None-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_task_merge.py::test_absorbed_notifications_keep_one_main_response_and_accept_next_prompt[False-end_turn-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_task_merge.py::test_absorbed_notifications_keep_one_main_response_and_accept_next_prompt[False-end_turn-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_task_merge.py::test_absorbed_notifications_keep_one_main_response_and_accept_next_prompt[True-None-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_task_merge.py::test_absorbed_notifications_keep_one_main_response_and_accept_next_prompt[True-None-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_task_merge.py::test_absorbed_notifications_keep_one_main_response_and_accept_next_prompt[True-end_turn-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_task_merge.py::test_absorbed_notifications_keep_one_main_response_and_accept_next_prompt[True-end_turn-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_native_task_merge.py::test_absorbed_notifications_recover_in_order_without_resubmitting | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_pending_query.py::test_buffered_continuation_cannot_finish_pending_query[assistant-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_pending_query.py::test_buffered_continuation_cannot_finish_pending_query[assistant-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_pending_query.py::test_buffered_continuation_cannot_finish_pending_query[request-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_pending_query.py::test_buffered_continuation_cannot_finish_pending_query[request-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_pending_query.py::test_buffered_continuation_cannot_finish_pending_query[stream-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_pending_query.py::test_buffered_continuation_cannot_finish_pending_query[stream-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_pending_query.py::test_pending_query_recovery_preserves_pre_echo_background_identity | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_pending_query.py::test_persistent_commands_can_finish_without_user_replay[compact] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_pending_query.py::test_persistent_commands_can_finish_without_user_replay[error] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_pending_query.py::test_unannounced_response_between_root_result_and_steer_echo[False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_pending_query.py::test_unannounced_response_between_root_result_and_steer_echo[True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_answered_question_page_is_not_asked_again_after_restart[False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_answered_question_page_is_not_asked_again_after_restart[True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_attached_summary_opt_in_keeps_running_turn_and_native_child[None] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_attached_summary_opt_in_keeps_running_turn_and_native_child[timeout] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_attached_summary_opt_in_keeps_running_turn_and_native_child[unsupported] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_duplicate_query_request_and_competing_controller_do_not_write_twice | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_explicit_worker_attach_checks_complete_identity[btw] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_explicit_worker_attach_checks_complete_identity[cwd] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_explicit_worker_attach_checks_complete_identity[profile_root] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_explicit_worker_attach_checks_complete_identity[session_id] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_explicit_worker_attach_checks_complete_identity[space] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_explicit_worker_attach_checks_complete_identity[work_id] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_interrupt_requires_terminal_ack_before_next_query | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_machine_reconstructs_offline_completion_without_resubmitting_or_duplicating_text | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_machine_recovers_background_followup_completion_without_resubmitting | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_mcp_tool_call_survives_controller_and_server_replacement | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_message_tool_refresh_marker_survives_controller_replacement | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_overlapping_buffered_background_turns_keep_each_unacknowledged_prefix | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_pending_permission_keeps_its_future_until_replacement_answers | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_private_socket_registration_and_explicit_override | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_claude_service.py::test_real_controller_process_exit_leaves_service_and_accepted_work_alive | TimeoutError |
| tests/test_claude_service.py::test_reattachment_schedules_ask_refresh_without_session_messaging | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service.py::test_running_query_and_offline_terminal_survive_wrapper_disconnect | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_callbacks.py::test_callback_answer_timeout_resends_same_result_without_rerunning_handler[mcp] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_callbacks.py::test_callback_answer_timeout_resends_same_result_without_rerunning_handler[mcp_notification] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_callbacks.py::test_callback_answer_timeout_resends_same_result_without_rerunning_handler[permission] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_callbacks.py::test_callback_handler_failure_retries_without_reconnecting[mcp] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_callbacks.py::test_callback_handler_failure_retries_without_reconnecting[permission] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_callbacks.py::test_callback_retry_does_not_block_other_callbacks_and_stops_when_closed | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_callbacks.py::test_detaching_during_callback_retry_preserves_pending_native_request | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_delivery.py::test_background_delivery_failure_retains_later_rows_for_reattachment[ack] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_delivery.py::test_background_delivery_failure_retains_later_rows_for_reattachment[ack_response] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_delivery.py::test_background_delivery_failure_retains_later_rows_for_reattachment[projection] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_delivery.py::test_deferred_commit_does_not_deadlock_on_the_managed_result_barrier | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_delivery.py::test_failed_background_terminal_retains_its_full_prefix | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_delivery.py::test_human_commit_waits_for_an_earlier_background_projection[False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_delivery.py::test_human_commit_waits_for_an_earlier_background_projection[True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_delivery.py::test_machine_reports_replay_requirement_without_restarting_native_work | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_delivery.py::test_mid_turn_background_burst_preserves_service_commit_boundary[False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_delivery.py::test_mid_turn_background_burst_preserves_service_commit_boundary[True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_delivery.py::test_waiting_query_rechecks_failed_delivery_without_hanging | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_lease.py::test_cancellation_during_lease_wait_closes_only_replacement_connection | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_lease.py::test_explicit_reconnect_retires_worker_identity_only_after_confirmed_close[False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_lease.py::test_explicit_reconnect_retires_worker_identity_only_after_confirmed_close[True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_lease.py::test_legacy_controller_can_reconnect_after_deliberate_close | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_lease.py::test_live_controller_is_not_replaced_when_retry_budget_expires | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_lease.py::test_recovery_does_not_retry_or_spawn_on_other_open_failures[identity] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_lease.py::test_recovery_does_not_retry_or_spawn_on_other_open_failures[legacy_missing] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_lease.py::test_recovery_does_not_retry_or_spawn_on_other_open_failures[missing] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_lease.py::test_recovery_does_not_retry_or_spawn_on_other_open_failures[other_error] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_lease.py::test_restore_retries_until_old_connection_finishes_cleanup[False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_lease.py::test_restore_retries_until_old_connection_finishes_cleanup[True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_lease.py::test_unknown_open_response_is_not_retried[ConnectionError] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_lease.py::test_unknown_open_response_is_not_retried[TimeoutError] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_recovery.py::test_live_prewarm_recovery_does_not_require_a_materialized_transcript | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_recovery.py::test_one_failed_session_does_not_strand_other_accepted_turns[exception] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_recovery.py::test_one_failed_session_does_not_strand_other_accepted_turns[none] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_recovery.py::test_startup_reuses_recovered_default_claude_resident[11111111-1111-4111-8111-111111111111] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_service_recovery.py::test_startup_reuses_recovered_default_claude_resident[None] | RuntimeError: Claude profile state migration is incomplete |
| tests/test_claude_service_recovery.py::test_unavailable_service_does_not_strand_other_services_turns[drain] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_recovery.py::test_unavailable_service_does_not_strand_other_services_turns[primary] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_restart.py::test_context_refresh_recovers_but_cached_read_does_not | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_restart.py::test_missing_worker_is_not_recreated_without_proven_restart[live] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_restart.py::test_missing_worker_is_not_recreated_without_proven_restart[unknown] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_restart.py::test_missing_worker_is_not_recreated_without_proven_restart[unreadable] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_restart.py::test_model_recovery_yields_to_active_or_external_work[active] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_restart.py::test_model_recovery_yields_to_active_or_external_work[callback] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_restart.py::test_model_recovery_yields_to_active_or_external_work[external] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_restart.py::test_model_switch_after_service_restart_resumes_exact_session | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_restart.py::test_new_query_recovers_after_service_died_during_ack | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_restart.py::test_restart_does_not_take_over_a_replacement_worker | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_restart.py::test_unavailable_service_keeps_restart_identity_for_later_retry | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_startup.py::test_cancelling_startup_joiner_does_not_cancel_creator | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_startup.py::test_concurrent_same_identity_starts_once_and_preserves_lease[False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_startup.py::test_concurrent_same_identity_starts_once_and_preserves_lease[True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_startup.py::test_failed_start_keeps_identity_reserved_until_cleanup_and_wakes_joiners[cancel] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_startup.py::test_failed_start_keeps_identity_reserved_until_cleanup_and_wakes_joiners[error] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_startup.py::test_slow_start_does_not_block_other_session_open_or_attach | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_service_startup.py::test_starting_session_counts_toward_capacity_and_checks_identity | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steer_attachments.py::test_service_retains_detached_files_then_retires_them[close-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steer_attachments.py::test_service_retains_detached_files_then_retires_them[close-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steer_attachments.py::test_service_retains_detached_files_then_retires_them[terminal-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steer_attachments.py::test_service_retains_detached_files_then_retires_them[terminal-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_implicit_response_absorbs_tasks_while_detached_then_accepts_next_query | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[assistant-absorbed_result-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[assistant-absorbed_result-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[assistant-result-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[assistant-result-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[assistant-steer-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[assistant-steer-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[assistant-stop-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[assistant-stop-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[request-absorbed_result-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[request-absorbed_result-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[request-result-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[request-result-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[request-steer-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[request-steer-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[request-stop-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[request-stop-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[stream-absorbed_result-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[stream-absorbed_result-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[stream-result-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[stream-result-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[stream-steer-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[stream-steer-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[stream-stop-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_native_continuation_without_replayed_user_is_visible_and_interactive[stream-stop-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_background_steer_recovery_does_not_resubmit[finished-False-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_background_steer_recovery_does_not_resubmit[finished-False-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_background_steer_recovery_does_not_resubmit[finished-True-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_background_steer_recovery_does_not_resubmit[finished-True-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_background_steer_recovery_does_not_resubmit[finished_before_request-False-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_background_steer_recovery_does_not_resubmit[finished_before_request-False-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_background_steer_recovery_does_not_resubmit[finished_before_request-True-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_background_steer_recovery_does_not_resubmit[finished_before_request-True-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_background_steer_recovery_does_not_resubmit[pending-False-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_background_steer_recovery_does_not_resubmit[pending-False-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_background_steer_recovery_does_not_resubmit[pending-True-False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_background_steer_recovery_does_not_resubmit[pending-True-True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_cancelled_guide_replays_before_terminal | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_multiple_background_inputs_cancel_one_and_keep_root_commit | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_preserves_input_boundary_and_root_commit_across_detach | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_rejects_stale_background_identity_and_old_capability | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_retried_steer_after_detach_does_not_submit_again | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_service_without_native_steering_rejects_before_mutation | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_steered_request_finishing_offline_retires_its_durable_background_claim | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_task_origin_result_consumes_exact_steer_and_commits_original_root[False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_claude_steering.py::test_task_origin_result_consumes_exact_steer_and_commits_original_root[True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_manual_compaction.py::test_internal_claude_compact_commits_success_and_error_without_human_turn[False] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_manual_compaction.py::test_internal_claude_compact_commits_success_and_error_without_human_turn[True] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_manual_compaction.py::test_legacy_uncommitted_compact_recovers_without_resubmitting_stale_prompt | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_manual_compaction.py::test_manual_claude_compact_commits_service_and_next_message_is_accepted[error] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_manual_compaction.py::test_manual_claude_compact_commits_service_and_next_message_is_accepted[interrupted] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_manual_compaction.py::test_manual_claude_compact_commits_service_and_next_message_is_accepted[missing-boundary] | PermissionError: Claude service directory must be private and owned by this user |
| tests/test_manual_compaction.py::test_manual_claude_compact_commits_service_and_next_message_is_accepted[success] | PermissionError: Claude service directory must be private and owned by this user |

## 子项目 4（TUI/viewer/messages）— 239 个

按文件汇总：

| 测试文件 | 失败数 | 主要原因 |
|---|---|---|
| tests/test_async_tasks.py | 72 | ValueError: Task state directory must be private and owned by this user |
| tests/test_session_messages.py | 66 | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_viewer.py | 45 | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_tab_store.py | 15 | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer_home.py | 11 | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_review_delivery.py | 8 | AttributeError: module 'os' has no attribute 'O_NONBLOCK' |
| tests/test_task_refresh.py | 4 | ValueError: Custom task MCP launcher; automatic refresh is not supported |
| tests/test_tui_mermaid.py | 4 | failed on setup with "ValueError: the environment variable is longer than 32767 characters" |
| tests/test_timed_tasks.py | 3 | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_tui_launcher.py | 3 | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\scripts\\cc-remote-tui' -> '<tmp>' |
| tests/test_tui_diagram_families.py | 2 | failed on setup with "ValueError: the environment variable is longer than 32767 characters" |
| tests/test_tui_markdown.py | 2 | AssertionError: assert ('╭' in '┌────────────────────────────────────────────────────────────┬───────┐\n│ Name           |
| tests/test_claude_profiles.py | 1 | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_clipboard_chrome.py | 1 | AssertionError: assert b'ok\r\n' == b'ok\n' |
| tests/test_tui_local.py | 1 | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_tui_parity.py | 1 | AttributeError: module 'os' has no attribute 'O_NONBLOCK' |

明细：

| 测试 | 失败原因（第一行异常） |
|---|---|
| tests/test_async_tasks.py::test_actual_command_then_native_tool_output[import sys; print('failed'); sys.exit(7)-failed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_actual_command_then_native_tool_output[print('done')-completed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_callback_acceptance_is_not_success_and_outcome_survives_recovery[completed-delivered] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_callback_acceptance_is_not_success_and_outcome_survives_recovery[failed-failed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_callback_acceptance_is_not_success_and_outcome_survives_recovery[interrupted-failed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_callback_checks_exact_turn_across_pages_not_latest_success | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_callback_startup_failure_snapshot_is_rechecked_without_resubmission[failed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_callback_startup_failure_snapshot_is_rechecked_without_resubmission[interrupted] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_callback_tracking_migrates_old_store_concurrently_without_reinterpreting_receipts | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_caller_metadata_and_owner_isolation | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_cancel_before_delivery_wins_even_after_connect | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_cancellation_stops_process_and_suppresses_wakeup | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_cold_resume_only_for_active_account_transcript[False] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_cold_resume_only_for_active_account_transcript[True] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_completion_ack_loss_is_not_replayed[lost-unknown] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_completion_ack_loss_is_not_replayed[reject-rejected] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_confirmed_callback_failure_keeps_tracking_and_can_recover[completed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_confirmed_callback_failure_keeps_tracking_and_can_recover[inProgress] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_detached_task_finishes_and_notifies_after_real_mcp_disconnect | NotImplementedError |
| tests/test_async_tasks.py::test_execution_budget_is_rechecked_at_native_send[0.25-connect] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_execution_budget_is_rechecked_at_native_send[0.25-schedule] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_execution_budget_is_rechecked_at_native_send[0.25-store] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_execution_budget_is_rechecked_at_native_send[0.25-thread] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_execution_budget_is_rechecked_at_native_send[1.25-connect] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_execution_budget_is_rechecked_at_native_send[1.25-schedule] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_execution_budget_is_rechecked_at_native_send[1.25-store] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_execution_budget_is_rechecked_at_native_send[1.25-thread] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_execution_cancellation_wins_at_send_boundary | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_failure_confirmation_requires_consistent_separated_observations[None] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_failure_confirmation_requires_consistent_separated_observations[failed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_failure_confirmation_requires_consistent_separated_observations[inProgress] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_legacy_callback_failure_is_reconciled_on_worker_start[failed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_legacy_callback_failure_is_reconciled_on_worker_start[unknown] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_legacy_reconciliation_has_one_deadline_and_final_read[None-failed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_legacy_reconciliation_has_one_deadline_and_final_read[None-unknown] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_legacy_reconciliation_has_one_deadline_and_final_read[completed-failed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_legacy_reconciliation_has_one_deadline_and_final_read[completed-unknown] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_legacy_reconciliation_has_one_deadline_and_final_read[inProgress-failed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_legacy_reconciliation_has_one_deadline_and_final_read[inProgress-unknown] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_legacy_reconciliation_has_one_deadline_and_final_read[interrupted-failed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_legacy_reconciliation_has_one_deadline_and_final_read[interrupted-unknown] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_lifetime_lock_and_detached_worker_survive_mcp_parent | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_mcp_tools_bind_native_caller_and_reject_target_override[app_server] | ImportError: cannot import name 'RequestContext' from 'mcp.server.lowlevel.server' (C:\Users\xuhaodong\work\cc-remote-p1\.venv\Lib\site-packages\mcp\server\lowlevel\server.py) |
| tests/test_async_tasks.py::test_mcp_tools_bind_native_caller_and_reject_target_override[legacy] | ImportError: cannot import name 'RequestContext' from 'mcp.server.lowlevel.server' (C:\Users\xuhaodong\work\cc-remote-p1\.venv\Lib\site-packages\mcp\server\lowlevel\server.py) |
| tests/test_async_tasks.py::test_mcp_tools_bind_native_caller_and_reject_target_override[model_call] | ImportError: cannot import name 'RequestContext' from 'mcp.server.lowlevel.server' (C:\Users\xuhaodong\work\cc-remote-p1\.venv\Lib\site-packages\mcp\server\lowlevel\server.py) |
| tests/test_async_tasks.py::test_native_task_receipt_projection | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_native_timeout_wins_before_local_deadline_poll[0-0.9995-False-completed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_native_timeout_wins_before_local_deadline_poll[124-0.2-False-failed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_native_timeout_wins_before_local_deadline_poll[124-0.9995-False-timed_out] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_native_timeout_wins_before_local_deadline_poll[124-0.9995-True-cancelled] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_native_timeout_wins_before_local_deadline_poll[7-0.9995-False-failed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_official_history_keeps_task_receipt_and_native_identity | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_pre_submission_disconnect_retains_pending_without_executing | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_production_socket_transport_uses_only_owned_existing_daemon[False] | ValueError: Codex socket parent is not private to its owner |
| tests/test_async_tasks.py::test_production_socket_transport_uses_only_owned_existing_daemon[True] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\crt-wire-3txcbrfz\\p\\5a8c08ba20c0d90fa02aeb73ec81b8f2d8ab91f29f1b053ab90e29aab3cc319b' -> 'C:\\tmp\\crt-wire-3txcbrfz\\app-server-c… |
| tests/test_async_tasks.py::test_real_stdio_mcp_protocol_and_native_call_metadata[app_server] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_real_stdio_mcp_protocol_and_native_call_metadata[legacy] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_real_stdio_mcp_protocol_and_native_call_metadata[model_call] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_receipt_retention_is_bounded_and_preserves_uncertain_tasks | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_reconciliation_never_guesses_receipt_identity_or_replays_delivery[delivered-native-turn] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_reconciliation_never_guesses_receipt_identity_or_replays_delivery[failed-None] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_reconciliation_never_guesses_receipt_identity_or_replays_delivery[rejected-native-turn] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_reconciliation_never_guesses_receipt_identity_or_replays_delivery[suppressed-native-turn] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_reconciliation_never_guesses_receipt_identity_or_replays_delivery[unknown-None] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_reconciliation_never_guesses_receipt_identity_or_replays_delivery[unknown-] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_recovery_does_not_reexecute_running_or_resend_unknown | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_submission_idempotency_limits_and_private_state | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_timeout_output_bound_and_explicit_rejection | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\tmp\\crt-nj1bpcrv\\rpc.sock' -> '<tmp>' |
| tests/test_async_tasks.py::test_unconfirmed_callback_is_retained_with_bounded_tracking[history] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_unconfirmed_callback_is_retained_with_bounded_tracking[malformed] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_unconfirmed_callback_is_retained_with_bounded_tracking[rpc] | ValueError: Task state directory must be private and owned by this user |
| tests/test_async_tasks.py::test_unconfirmed_callback_is_retained_with_bounded_tracking[socket] | ValueError: Task state directory must be private and owned by this user |
| tests/test_claude_profiles.py::test_claude_profile_transitions_migrate_viewer_scopes_once | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_session_messages.py::test_ask_schema_refresh_does_not_require_session_messaging | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_attached_claude_refreshes_actual_tool_list_and_preserves_ask[False] | ImportError: cannot import name 'RequestContext' from 'mcp.server.lowlevel.server' (C:\Users\xuhaodong\work\cc-remote-p1\.venv\Lib\site-packages\mcp\server\lowlevel\server.py) |
| tests/test_session_messages.py::test_attached_claude_refreshes_actual_tool_list_and_preserves_ask[True] | ImportError: cannot import name 'RequestContext' from 'mcp.server.lowlevel.server' (C:\Users\xuhaodong\work\cc-remote-p1\.venv\Lib\site-packages\mcp\server\lowlevel\server.py) |
| tests/test_session_messages.py::test_busy_message_waits_for_real_boundary_without_interrupt[autonomous-claude] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_busy_message_waits_for_real_boundary_without_interrupt[human_queue-claude] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_busy_message_waits_for_real_boundary_without_interrupt[human_queue-codex] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_busy_message_waits_for_real_boundary_without_interrupt[question-claude] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_busy_message_waits_for_real_boundary_without_interrupt[question-codex] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_busy_message_waits_for_real_boundary_without_interrupt[terminal_drain-claude] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_busy_message_waits_for_real_boundary_without_interrupt[terminal_drain-codex] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_busy_message_waits_for_real_boundary_without_interrupt[turn-claude] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_busy_message_waits_for_real_boundary_without_interrupt[turn-codex] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_claude_bound_mcp_and_live_echo | ImportError: cannot import name 'RequestContext' from 'mcp.server.lowlevel.server' (C:\Users\xuhaodong\work\cc-remote-p1\.venv\Lib\site-packages\mcp\server\lowlevel\server.py) |
| tests/test_session_messages.py::test_cli_ownership_adopted_during_preflight_rejects_without_sending | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_cold_target_resumes_exact_account_without_focus_or_bootstrap[claude] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_cold_target_resumes_exact_account_without_focus_or_bootstrap[codex] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_connected_message_tools_do_not_hide_a_missing_ask_tool[ask_user-False] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_connected_message_tools_do_not_hide_a_missing_ask_tool[ask_user-True] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_connected_message_tools_do_not_hide_a_missing_ask_tool[set_mode-False] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_connected_message_tools_do_not_hide_a_missing_ask_tool[set_mode-True] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_corrupt_ack_is_unknown_not_a_rejection | AttributeError: module 'asyncio' has no attribute 'start_unix_server'. Did you mean: 'start_server'? |
| tests/test_session_messages.py::test_disabled_ask_cannot_mask_missing_dedicated_message_tools | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_disabled_ask_does_not_block_independent_message_refresh[None] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_disabled_ask_does_not_block_independent_message_refresh[cc_remote_messages] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_dispatch_uses_existing_lane_and_exact_target_once[claude] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_dispatch_uses_existing_lane_and_exact_target_once[codex] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_failed_refresh_marker_does_not_disable_anything | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_failed_sdk_route_can_be_repaired_without_restarting_claude | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_inbox_claim_is_crash_safe_bounded_and_erases_delivered_payload | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_lost_native_ack_stays_unknown_and_never_repeats | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_lost_toggle_off_ack_always_restores_ask_and_remains_retryable | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_message_delivery_refreshes_independent_route_with_ask_disabled | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_missing_tools_retry_stays_queued_without_a_native_write | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_native_launch_fence_requeues_only_proven_unsent_input | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_no_send_for_ineligible_target[btw] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_no_send_for_ineligible_target[deleted] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_no_send_for_ineligible_target[external] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_no_send_for_ineligible_target[work] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_no_send_for_ineligible_target[wrong_engine] | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_optional_tool_refresh_does_not_block_ordinary_chat_with_background_work | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_ordinary_chat_recovers_pending_refresh_after_messaging_opt_out | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_pending_ask_toggle_is_restored_even_with_independent_messaging | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_pending_message_refresh_can_finish_after_messaging_is_disabled[absent] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_pending_message_refresh_can_finish_after_messaging_is_disabled[connected] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_pending_message_refresh_can_finish_after_messaging_is_disabled[disabled] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_pending_message_refresh_can_finish_after_messaging_is_disabled[failed] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_pending_message_refresh_can_finish_after_messaging_is_disabled[pending] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_queued_inbox_recovers_after_wrapper_restart_in_fifo_order | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_queued_target_is_revalidated_before_delivery | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_real_stdio_mcp_binds_codex_metadata_and_private_socket | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_refresh_does_not_enable_a_previously_disabled_or_unready_server[disabled] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_refresh_does_not_enable_a_previously_disabled_or_unready_server[pending] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_refresh_intent_survives_controller_loss_between_off_and_on | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_restart_only_replays_queued_never_inflight_even_with_remaining_body | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_restore_pending_message_toggle_before_refreshing_separate_ask | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_retiring_pending_message_route_retains_intent_until_confirmed[marker_ack] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_retiring_pending_message_route_retains_intent_until_confirmed[missing_status] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_retiring_pending_message_route_retains_intent_until_confirmed[off_ack] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_retiring_pending_message_route_retains_intent_until_confirmed[still_connected] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_sdk_injects_message_server_only_into_code | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_store_survives_restart_conflicts_and_caller_isolation | ValueError: Message state must be a private directory owned by this user |
| tests/test_session_messages.py::test_tool_refresh_never_crosses_native_turn_or_pending_callback[_autonomous_steer_root] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_tool_refresh_never_crosses_native_turn_or_pending_callback[_background_callbacks_pending] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_tool_refresh_never_crosses_native_turn_or_pending_callback[_turn_active] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_tool_refresh_never_crosses_native_turn_or_pending_callback[_turn_consumer_active] | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_session_messages.py::test_unknown_pending_route_is_not_cleared_or_toggled | AttributeError: 'Server' object has no attribute 'list_tools' |
| tests/test_task_refresh.py::test_native_version_conflict_does_not_overwrite_or_retry | ValueError: Custom task MCP launcher; automatic refresh is not supported |
| tests/test_task_refresh.py::test_no_thread_reports_only_requested_not_verified | ValueError: Custom task MCP launcher; automatic refresh is not supported |
| tests/test_task_refresh.py::test_same_release_does_not_rewrite_config_but_still_verifies | ValueError: Custom task MCP launcher; automatic refresh is not supported |
| tests/test_task_refresh.py::test_upgrade_replaces_stale_thread_pool_and_verifies_real_call | ValueError: Custom task MCP launcher; automatic refresh is not supported |
| tests/test_timed_tasks.py::test_queue_uses_existing_thread_exact_client_id_and_accepts_before_start | AttributeError: module 'os' has no attribute 'getuid'. Did you mean: 'getpid'? |
| tests/test_timed_tasks.py::test_read_missing_store_has_no_side_effects_and_symlinks_are_rejected | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_timed_tasks.py::test_worker_marks_unknown_once_without_resending | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_clipboard_chrome.py::test_clipboard_helper_output_exit_timeout_and_cancellation | AssertionError: assert b'ok\r\n' == b'ok\n' |
| tests/test_tui_diagram_families.py::test_limits_fail_closed[graph TB\nAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx…] | failed on setup with "ValueError: the environment variable is longer than 32767 characters" |
| tests/test_tui_diagram_families.py::test_limits_fail_closed[graph TB\nAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx…] | failed on teardown with "ValueError: the environment variable is longer than 32767 characters" |
| tests/test_tui_launcher.py::test_launcher_uses_own_checkout_from_unrelated_cwd | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\scripts\\cc-remote-tui' -> '<tmp>' |
| tests/test_tui_launcher.py::test_launcher_with_bsd_readlink_and_chained_symlinks[False] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\scripts\\cc-remote-tui' -> '<tmp> with spaces\\inner' |
| tests/test_tui_launcher.py::test_launcher_with_bsd_readlink_and_chained_symlinks[True] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\scripts\\cc-remote-tui' -> '<tmp> with spaces\\inner' |
| tests/test_tui_local.py::test_different_user_service_is_not_read | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_tui_markdown.py::test_folded_markdown_progress_keeps_tool_styles_and_navigation | AssertionError: assert ('progress explanation' in '── You ──  [1970-01-01 08:16:40 · completed · duration unavailable]\nquestion\n\n── Turn details ▾ ──  [Enter]\n\n── ...   │\n└──… |
| tests/test_tui_markdown.py::test_tables_keep_all_columns_values_and_link_targets[70] | AssertionError: assert ('╭' in '┌────────────────────────────────────────────────────────────┬───────┐\n│ Name                                       ...─────┴───────┘\nhttps://exam… |
| tests/test_tui_mermaid.py::test_unsupported_or_oversized_diagrams_keep_source[graph TB\nA["\u4e2d\u4e2d\u4e2d\u4e2d\u4e2d\u4e2d\u4e2d\u4e2d\u4e2d\u4e2…] | failed on setup with "ValueError: the environment variable is longer than 32767 characters" |
| tests/test_tui_mermaid.py::test_unsupported_or_oversized_diagrams_keep_source[graph TB\nA["\u4e2d\u4e2d\u4e2d\u4e2d\u4e2d\u4e2d\u4e2d\u4e2d\u4e2d\u4e2…] | failed on teardown with "ValueError: the environment variable is longer than 32767 characters" |
| tests/test_tui_mermaid.py::test_unsupported_or_oversized_diagrams_keep_source[graph TB\nA["xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx…] | failed on setup with "ValueError: the environment variable is longer than 32767 characters" |
| tests/test_tui_mermaid.py::test_unsupported_or_oversized_diagrams_keep_source[graph TB\nA["xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx…] | failed on teardown with "ValueError: the environment variable is longer than 32767 characters" |
| tests/test_tui_parity.py::test_attachment_validation_reuses_shared_limits | AttributeError: module 'os' has no attribute 'O_NONBLOCK' |
| tests/test_tui_review_delivery.py::test_authoritative_delivery_releases_only_sent_attachments[query_queue] | AttributeError: module 'os' has no attribute 'O_NONBLOCK' |
| tests/test_tui_review_delivery.py::test_authoritative_delivery_releases_only_sent_attachments[turn_steered] | AttributeError: module 'os' has no attribute 'O_NONBLOCK' |
| tests/test_tui_review_delivery.py::test_authoritative_delivery_releases_only_sent_attachments[user_msg] | AttributeError: module 'os' has no attribute 'O_NONBLOCK' |
| tests/test_tui_review_delivery.py::test_fast_rejection_during_send_does_not_erase_restored_files | AttributeError: module 'os' has no attribute 'O_NONBLOCK' |
| tests/test_tui_review_delivery.py::test_rejected_delivery_restores_files_after_ack[msg_id-False] | AttributeError: module 'os' has no attribute 'O_NONBLOCK' |
| tests/test_tui_review_delivery.py::test_rejected_delivery_restores_files_after_ack[msg_id-True] | AttributeError: module 'os' has no attribute 'O_NONBLOCK' |
| tests/test_tui_review_delivery.py::test_rejected_delivery_restores_files_after_ack[request_id-False] | AttributeError: module 'os' has no attribute 'O_NONBLOCK' |
| tests/test_tui_review_delivery.py::test_rejected_delivery_restores_files_after_ack[request_id-True] | AttributeError: module 'os' has no attribute 'O_NONBLOCK' |
| tests/test_tui_tab_store.py::test_corrupt_state_reported_without_overwriting | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_tab_store.py::test_empty_or_deleted_working_set_does_not_open_recent_session[tabs0] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_tab_store.py::test_empty_or_deleted_working_set_does_not_open_recent_session[tabs1] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_tab_store.py::test_explicit_scope_axes_override_saved_scope[False-True-expected1] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_tab_store.py::test_explicit_scope_axes_override_saved_scope[True-False-expected0] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_tab_store.py::test_explicit_scope_axes_override_saved_scope[True-True-expected2] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_tab_store.py::test_explicit_session_overrides_restored_focus | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_tab_store.py::test_open_close_rekey_and_exit_persist_without_model_commands | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_tab_store.py::test_private_atomic_tabs_exclude_transient_ids_and_secrets | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_tab_store.py::test_restore_waits_for_catalog_and_attaches_only_saved_active | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_tab_store.py::test_save_only_on_working_set_change | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_tab_store.py::test_scopes_do_not_restore_other_device_or_account[machine] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_tab_store.py::test_scopes_do_not_restore_other_device_or_account[url] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_tab_store.py::test_scopes_do_not_restore_other_device_or_account[username] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_tui_tab_store.py::test_two_terminals_merge_edits_without_resurrecting_closed_tabs | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_bridge_auth_origin_jti_duplicate_socket_and_path_boundary | TimeoutError |
| tests/test_viewer.py::test_bridge_head_releases_source_slot_before_completion_receipt | TimeoutError |
| tests/test_viewer.py::test_bridge_private_origin_binds_exact_target_and_renews_without_origin_header | TimeoutError |
| tests/test_viewer.py::test_bridge_range_head_cache_cancel_and_logout | TimeoutError |
| tests/test_viewer.py::test_bridge_real_binary_reads_no_unsolicited_bytes_and_revocation | TimeoutError |
| tests/test_viewer.py::test_cancelled_mesh_queue_releases_only_its_own_slot | TimeoutError |
| tests/test_viewer.py::test_cancelled_page_write_finishes_before_session_lifecycle[drop] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_cancelled_page_write_finishes_before_session_lifecycle[rekey] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_capacity_and_pull_backpressure_are_bounded | TimeoutError |
| tests/test_viewer.py::test_file_descriptor_is_pinned_after_open | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_viewer.py::test_grants_cannot_cross_login_device_or_challenge | TimeoutError |
| tests/test_viewer.py::test_live_page_observer_only_binds_published_html | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_logout_and_removal_revoke_previews | TimeoutError |
| tests/test_viewer.py::test_page_discovery_never_expands_publication_or_follows_symlinks | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_viewer.py::test_page_metadata_routes_to_source_device_and_does_not_guess | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_page_profile_metadata_does_not_consume_a_session_slot | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_page_profile_migration_failure_preserves_rows_and_revision[invalid_sid] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_page_profile_migration_failure_preserves_rows_and_revision[too_many_bytes] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_page_profile_migration_failure_preserves_rows_and_revision[too_many_pages] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_page_profile_migration_merges_tombstones_and_keeps_engine_revisions | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_page_store_persists_deduplicates_and_scopes | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_page_store_rejects_insecure_and_oversized_state | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_page_store_rejects_invalid_profile_revision_metadata[revisions0] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_page_store_rejects_invalid_profile_revision_metadata[revisions1] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_page_store_rejects_invalid_profile_revision_metadata[revisions2] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_page_store_rejects_invalid_profile_revision_metadata[revisions3] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_preview_lease_renewal_expiry_and_device_revocation | TimeoutError |
| tests/test_viewer.py::test_publication_stays_specific_and_hides_private_paths | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_real_resource_channel_multifile_range_cache_and_close | TimeoutError |
| tests/test_viewer.py::test_registry_edits_preserve_previous_snapshot_on_failure | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer.py::test_session_pages_api_persists_resolves_removes_and_opens_alternate_entry | TimeoutError |
| tests/test_viewer.py::test_static_byte_ranges[bytes=-0-expected4] | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_viewer.py::test_static_byte_ranges[bytes=-5-expected2] | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_viewer.py::test_static_byte_ranges[bytes=0-1,3-4-expected6] | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_viewer.py::test_static_byte_ranges[bytes=0-3-expected0] | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_viewer.py::test_static_byte_ranges[bytes=100--expected1] | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_viewer.py::test_static_byte_ranges[bytes=4-2-expected5] | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_viewer.py::test_static_byte_ranges[bytes=999999--expected3] | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_viewer.py::test_static_byte_ranges[invalid-expected7] | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_viewer.py::test_symlinks_hardlinks_and_root_replacement_fail_closed | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_viewer.py::test_utf16_navigation_completes_over_real_http[utf-16-be-\xfe\xff] | TimeoutError |
| tests/test_viewer.py::test_utf16_navigation_completes_over_real_http[utf-16-le-\xff\xfe] | TimeoutError |
| tests/test_viewer.py::test_viewer_migration_failure_preserves_chat_and_replay_marker[claude] | assert False |
| tests/test_viewer.py::test_viewer_migration_failure_preserves_chat_and_replay_marker[codex] | assert False |
| tests/test_viewer.py::test_viewer_parallel_mesh_burst_queues_without_dropping_files | TimeoutError |
| tests/test_viewer_home.py::test_auto_page_open_rebuilds_relay_cache_and_revokes | TimeoutError |
| tests/test_viewer_home.py::test_empty_catalog_can_discover_cross_device_url_and_not_guess | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer_home.py::test_existing_publications_win_and_ambiguity_is_preserved | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer_home.py::test_file_and_proven_url_share_the_server_namespace | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer_home.py::test_home_does_not_follow_paths_links_or_publish_other_homes | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_viewer_home.py::test_home_pages_are_on_demand_private_and_survive_restart | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer_home.py::test_html_directly_in_home_needs_no_registration | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_viewer_home.py::test_python_static_adapter_requires_exact_argv[argv0-/home/demo/my project] | AssertionError: assert '\\home\\demo\\my project' == '/home/demo/my project' |
| tests/test_viewer_home.py::test_python_static_adapter_requires_exact_argv[argv1-/home/demo/web] | AssertionError: assert '\\home\\demo\\web' == '/home/demo/web' |
| tests/test_viewer_home.py::test_python_static_adapter_requires_exact_argv[argv2-/home/demo] | AssertionError: assert '\\home\\demo' == '/home/demo' |
| tests/test_viewer_home.py::test_python_static_adapter_requires_exact_argv[argv3-/home/demo] | AssertionError: assert '\\home\\demo' == '/home/demo' |

## 子项目 5（安装部署）— 250 个

按文件汇总：

| 测试文件 | 失败数 | 主要原因 |
|---|---|---|
| tests/test_update.py | 66 | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_install_commit.py | 42 | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_deploy_cleanup.py | 41 | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy.py | 18 | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_profile_rollback.py | 17 | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_update_relay.py | 14 | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_release_distribution.py | 11 | deploy.build_release.BuildError: uv executable is missing or not executable |
| tests/test_update_installer_relay.py | 10 | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_adopt_wrapper.py | 9 | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_update_diagnostics.py | 9 | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_install_lock.py | 5 | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_claude_plans.py | 3 | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_prepare_wrapper_stage.py | 2 | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_desktop_remote_ui.py | 1 | ValueError: Unknown Desktop asset handler; refusing to patch |
| tests/test_relay_update_job.py | 1 | ModuleNotFoundError: No module named 'fcntl' |
| tests/test_release_retention.py | 1 | ModuleNotFoundError: No module named 'pwd' |

明细：

| 测试 | 失败原因（第一行异常） |
|---|---|
| tests/test_adopt_wrapper.py::test_explicit_migration_validates_without_modifying_legacy [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_adopt_wrapper.py::test_migrated_service_keeps_custom_policy_on_subsequent_upgrade [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_adopt_wrapper.py::test_migration_refuses_ambiguous_or_unrelated_installations[command] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_adopt_wrapper.py::test_migration_refuses_ambiguous_or_unrelated_installations[destination] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_adopt_wrapper.py::test_migration_refuses_ambiguous_or_unrelated_installations[dropin] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_adopt_wrapper.py::test_migration_refuses_ambiguous_or_unrelated_installations[env] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_adopt_wrapper.py::test_migration_refuses_ambiguous_or_unrelated_installations[ownership] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_adopt_wrapper.py::test_migration_refuses_ambiguous_or_unrelated_installations[symlink] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_adopt_wrapper.py::test_migration_refuses_ambiguous_or_unrelated_installations[user] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_claude_plans.py::test_activation_rollback_restores_plan_and_dismissal_receipt[False] | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_claude_plans.py::test_activation_rollback_restores_plan_and_dismissal_receipt[True] | deploy.work_registry_snapshot.WorkRegistrySnapshotError: Claude plans must be a private bounded database |
| tests/test_claude_plans.py::test_legacy_snapshot_does_not_claim_to_restore_new_plan_database | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_deploy.py::test_atomic_install_replaces_destination_with_requested_mode | subprocess.CalledProcessError: Command '['bash', '-c', 'source "$1"; atomic_install_file "$2" "$3" "$(id -un)" "$(id -gn)" 0640', 'atomic-install-test', 'C:\\Users\\xuhaodong\\work… |
| tests/test_deploy.py::test_claude_pty_broker_is_not_a_documented_or_installed_feature | OSError: [WinError 193] %1 不是有效的 Win32 应用程序。 |
| tests/test_deploy.py::test_failed_release_rollback_never_deletes_the_still_active_release | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_deploy.py::test_post_switch_activation_commits_or_restores_full_release_caddy_and_unit[readiness-failure] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_deploy.py::test_post_switch_activation_commits_or_restores_full_release_caddy_and_unit[registration-failure] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_deploy.py::test_post_switch_activation_commits_or_restores_full_release_caddy_and_unit[registration-success] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_deploy.py::test_post_switch_activation_commits_or_restores_full_release_caddy_and_unit[source-success] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_deploy.py::test_relay_retention_setup_failure_keeps_the_prepared_release[backup-error] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_deploy.py::test_relay_retention_setup_failure_keeps_the_prepared_release[interrupted-copy] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_deploy.py::test_release_permissions_remove_inherited_group_write_and_other_access | subprocess.CalledProcessError: Command '['bash', '-c', 'source "$1"; harden_release_permissions "$2"', 'permission-test', 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\deploy\\setup_t… |
| tests/test_deploy.py::test_release_snapshot_rejects_boolean_manifest_version | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_deploy.py::test_release_snapshot_rejects_non_private_claude_control_state | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_deploy.py::test_release_snapshot_restores_absent_claude_control_state | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_deploy.py::test_release_snapshot_restores_versioned_claude_control_state | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_deploy.py::test_setup_script_is_valid_shell_and_keeps_safe_install_order | subprocess.CalledProcessError: Command '['bash', '-n', 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\deploy\\setup-vps.sh']' returned non-zero exit status 1. |
| tests/test_deploy.py::test_work_registry_migration_verifier_rejects_unowned_profile_rows[claude] | OSError: [Errno 9] Bad file descriptor |
| tests/test_deploy.py::test_work_registry_migration_verifier_rejects_unowned_profile_rows[codex] | OSError: [Errno 9] Bad file descriptor |
| tests/test_deploy.py::test_work_registry_snapshot_captures_wal_and_restores_absent_database | OSError: [Errno 9] Bad file descriptor |
| tests/test_deploy_cleanup.py::test_check_timeout_never_discloses_private_arguments [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_config_failure_after_quarantine_restores_original_path [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_dependency_on_candidate_child_retains_whole_dependency_closure[False-False] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_dependency_on_candidate_child_retains_whole_dependency_closure[False-True] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_dependency_on_candidate_child_retains_whole_dependency_closure[True-False] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_dependency_on_candidate_child_retains_whole_dependency_closure[True-True] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_incomplete_dependency_scan_never_deletes[broken] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_incomplete_dependency_scan_never_deletes[limit] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_incomplete_lsof_never_becomes_empty_safe_result[exit] | AttributeError: module 'os' has no attribute 'geteuid'. Did you mean: 'getpid'? |
| tests/test_deploy_cleanup.py::test_incomplete_lsof_never_becomes_empty_safe_result[warning] | AttributeError: module 'os' has no attribute 'geteuid'. Did you mean: 'getpid'? |
| tests/test_deploy_cleanup.py::test_invalid_service_discovery_context_never_retires[None] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_invalid_service_discovery_context_never_retires[context1] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_invalid_service_discovery_context_never_retires[context2] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_invalid_service_discovery_context_never_retires[context3] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_nested_protection_keeps_sibling_dependency_during_single_candidate_rescan [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_new_live_reference_defers_and_restores_candidate[1] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_new_live_reference_defers_and_restores_candidate[2] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_new_live_reference_defers_and_restores_candidate[3] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_newly_busy_candidate_protects_its_dependency_before_other_candidate_moves [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_partial_delete_is_not_misreported_as_restored [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_post_delete_health_failure_never_reports_success [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_preview_never_moves_deletes_or_runs_acceptance [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_protected_artifact_cannot_be_retired[current] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_protected_artifact_cannot_be_retired[dependency] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_protected_artifact_cannot_be_retired[rollback] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_replaced_provenance_stops_cleanup[inventory] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_replaced_provenance_stops_cleanup[rollback] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_retained_venv_symlink_protects_an_older_release [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_shared_venv_dependencies_are_transitive_and_cycles_are_bounded [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_success_retains_complete_rollback_and_checks_after_removal [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_transaction_change_after_precheck_stops_before_retirement [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_uncertain_state_never_deletes[candidate] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_uncertain_state_never_deletes[current] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_uncertain_state_never_deletes[journal] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_uncertain_state_never_deletes[lock] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_uncertain_state_never_deletes[scan] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_uncertain_state_never_deletes[symlink] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_uncertain_state_never_deletes[transaction] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_unobservable_process_record_stops_cleanup[p42\x00\nfNOFD\x00nPermission denied\x00] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_unobservable_process_record_stops_cleanup[p42\x00\nfcwd\x00n/proc/42/cwd (readlink: Permission denied)\x00] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_deploy_cleanup.py::test_unobservable_process_record_stops_cleanup[p42\x00\nfrtd\x00n/proc/42/root (readlink: Operation not permitted)\x00] …] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>'" |
| tests/test_desktop_remote_ui.py::test_stage_preserves_installed_app_and_never_replaces_an_existing_stage | ValueError: Unknown Desktop asset handler; refusing to patch |
| tests/test_install_commit.py::test_supervisor_rollback_restores_registration[False-absent] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_supervisor_rollback_restores_registration[False-running] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_supervisor_rollback_restores_registration[False-stopped] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_supervisor_rollback_restores_registration[True-absent] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_supervisor_rollback_restores_registration[True-running] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_supervisor_rollback_restores_registration[True-stopped] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_cleanup_reports_only_recovery_that_actually_happened[preflight] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_cleanup_reports_only_recovery_that_actually_happened[restore-failed] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_cleanup_reports_only_recovery_that_actually_happened[service-stopped] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_cleanup_reports_only_recovery_that_actually_happened[snapshot-restored] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[fresh-adopt-supervisor-path-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[fresh-adopt-supervisor-system-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[fresh-darwin-path-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp> installation [test]\\releases\\new\\.venv\\bin\\python' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[fresh-darwin-system-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp> installation [test]\\releases\\new\\.venv\\bin\\python' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[fresh-supervisor-path-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[fresh-supervisor-system-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[fresh-systemd-path-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[fresh-systemd-system-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[same-release-adopt-supervisor-path-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[same-release-adopt-supervisor-system-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[same-release-darwin-path-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp> installation [test]\\releases\\new\\.venv\\bin\\python' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[same-release-darwin-system-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp> installation [test]\\releases\\new\\.venv\\bin\\python' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[same-release-supervisor-path-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[same-release-supervisor-system-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[same-release-systemd-path-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[same-release-systemd-system-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[upgrade-adopt-supervisor-path-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[upgrade-adopt-supervisor-system-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[upgrade-darwin-path-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp> installation [test]\\releases\\new\\.venv\\bin\\python' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[upgrade-darwin-system-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp> installation [test]\\releases\\new\\.venv\\bin\\python' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[upgrade-supervisor-path-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[upgrade-supervisor-system-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[upgrade-systemd-path-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_preflight_preserves_arguments_under_nounset[upgrade-systemd-system-bash] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp>' |
| tests/test_install_commit.py::test_wrapper_registration_matches_the_activation_commit[fail-output-False] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp> installation\\releases\\new\\.venv\\bin\\python' |
| tests/test_install_commit.py::test_wrapper_registration_matches_the_activation_commit[fail-output-True] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp> installation\\releases\\new\\.venv\\bin\\python' |
| tests/test_install_commit.py::test_wrapper_registration_matches_the_activation_commit[interrupt-readiness-False] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp> installation\\releases\\new\\.venv\\bin\\python' |
| tests/test_install_commit.py::test_wrapper_registration_matches_the_activation_commit[interrupt-readiness-True] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp> installation\\releases\\new\\.venv\\bin\\python' |
| tests/test_install_commit.py::test_wrapper_registration_matches_the_activation_commit[retention-failure-False] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp> installation\\releases\\new\\.venv\\bin\\python' |
| tests/test_install_commit.py::test_wrapper_registration_matches_the_activation_commit[retention-failure-True] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp> installation\\releases\\new\\.venv\\bin\\python' |
| tests/test_install_commit.py::test_wrapper_registration_matches_the_activation_commit[success-False] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp> installation\\releases\\new\\.venv\\bin\\python' |
| tests/test_install_commit.py::test_wrapper_registration_matches_the_activation_commit[success-True] | OSError: [WinError 1314] 客户端没有所需的特权。: 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\.venv\\Scripts\\python.exe' -> '<tmp> installation\\releases\\new\\.venv\\bin\\python' |
| tests/test_install_lock.py::test_direct_wrapper_install_cannot_enter_during_managed_update | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_install_lock.py::test_inherited_marker_for_another_file_cannot_bypass_wrapper_lock | AssertionError: pass_fds not supported on Windows. |
| tests/test_install_lock.py::test_lock_rejects_symlinks_and_wrong_inherited_file | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_install_lock.py::test_managed_update_and_second_install_cannot_enter_direct_install | OSError: [WinError 10038] 在一个非套接字上尝试了一个操作。 |
| tests/test_install_lock.py::test_wrapper_accepts_updater_descriptor_without_releasing_parent_lock | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_prepare_wrapper_stage.py::test_changed_contract_builds_a_fresh_copy_linked_venv | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_prepare_wrapper_stage.py::test_identical_nono_stage_defers_venv_copy_and_binds_manifest | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_profile_rollback.py::test_absent_checkpoint_tree_is_retired_without_deleting_data | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_profile_rollback.py::test_checkpoint_restore_rejects_unsafe_archive_even_with_matching_hash[../escape] | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_profile_rollback.py::test_checkpoint_restore_rejects_unsafe_archive_even_with_matching_hash[/escape] | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_profile_rollback.py::test_checkpoint_restore_rejects_unsafe_archive_even_with_matching_hash[codex-checkpoints/link] | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_profile_rollback.py::test_checkpoint_snapshot_cannot_follow_a_swapped_directory | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_profile_rollback.py::test_codex_checkpoint_directory_swap_and_bytes_roll_back_together[False] | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_profile_rollback.py::test_codex_checkpoint_directory_swap_and_bytes_roll_back_together[True] | AttributeError: module 'os' has no attribute 'O_DIRECTORY' |
| tests/test_profile_rollback.py::test_incomplete_transaction_is_rejected_before_any_restore[checksum] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_profile_rollback.py::test_incomplete_transaction_is_rejected_before_any_restore[directory] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_profile_rollback.py::test_incomplete_transaction_is_rejected_before_any_restore[extra] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_profile_rollback.py::test_incomplete_transaction_is_rejected_before_any_restore[missing] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_profile_rollback.py::test_incomplete_transaction_is_rejected_before_any_restore[path] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_profile_rollback.py::test_legacy_snapshots_restore_only_their_original_scope[1] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_profile_rollback.py::test_legacy_snapshots_restore_only_their_original_scope[2] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_profile_rollback.py::test_snapshot_accepts_real_store_limits_above_one_megabyte | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_profile_rollback.py::test_snapshot_restores_every_participant_and_absence[False] | PermissionError: [Errno 13] Permission denied: '<tmp>' |
| tests/test_profile_rollback.py::test_snapshot_restores_every_participant_and_absence[True] | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_relay_update_job.py (collection) [error] | ModuleNotFoundError: No module named 'fcntl' |
| tests/test_release_distribution.py::test_release_bootstrap_fails_closed_before_download | subprocess.CalledProcessError: Command '['bash', '-n', 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\deploy\\install.sh']' returned non-zero exit status 1. |
| tests/test_release_distribution.py::test_release_bootstrap_rejects_tampered_or_linked_archives[0000000000000000000000000000000000000000000000000000000…] | OSError: [WinError 193] %1 不是有效的 Win32 应用程序。 |
| tests/test_release_distribution.py::test_release_bootstrap_rejects_tampered_or_linked_archives[None-True-unsafe path] | OSError: [WinError 193] %1 不是有效的 Win32 应用程序。 |
| tests/test_release_distribution.py::test_release_bootstrap_verifies_before_invoking_role_installer | OSError: [WinError 193] %1 不是有效的 Win32 应用程序。 |
| tests/test_release_distribution.py::test_release_builder_rejects_symbolic_links | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_release_distribution.py::test_release_builder_rejects_unsupported_role_platform_and_bad_uv | AssertionError: Regex pattern did not match. |
| tests/test_release_distribution.py::test_release_bundles_are_deterministic_and_role_scoped[relay-linux-x86_64] | deploy.build_release.BuildError: uv executable is missing or not executable |
| tests/test_release_distribution.py::test_release_bundles_are_deterministic_and_role_scoped[wrapper-darwin-arm64] | deploy.build_release.BuildError: uv executable is missing or not executable |
| tests/test_release_distribution.py::test_release_bundles_are_deterministic_and_role_scoped[wrapper-linux-arm64] | deploy.build_release.BuildError: uv executable is missing or not executable |
| tests/test_release_distribution.py::test_release_manifest_and_atomic_switch_fail_closed | deploy.build_release.BuildError: uv executable is missing or not executable |
| tests/test_release_distribution.py::test_role_installers_keep_credentials_out_of_service_definitions | subprocess.CalledProcessError: Command '['bash', '-n', 'C:\\Users\\xuhaodong\\work\\cc-remote-p1\\deploy\\install-relay.sh']' returned non-zero exit status 1. |
| tests/test_release_retention.py (collection) [error] | ModuleNotFoundError: No module named 'pwd' |
| tests/test_update.py::test_changed_installation_is_not_activated_after_download | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_check_never_downloads_locks_or_activates | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_cli_registration_is_atomic_and_preserves_unrelated_commands | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_current_version_is_not_complete_while_activation_is_locked[False] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_current_version_is_not_complete_while_activation_is_locked[True] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_custom_installation_is_not_adopted | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_explicit_macos_registration_binds_existing_root_and_launch_agent | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_explicit_role_ignores_an_unrelated_broken_installation[current-relay] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> relay\\releases\\old' -> '<tmp> relay\\current' |
| tests/test_update.py::test_explicit_role_ignores_an_unrelated_broken_installation[current-wrapper] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> relay\\releases\\old' -> '<tmp> relay\\current' |
| tests/test_update.py::test_explicit_role_ignores_an_unrelated_broken_installation[manifest-relay] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> relay\\releases\\old' -> '<tmp> relay\\current' |
| tests/test_update.py::test_explicit_role_ignores_an_unrelated_broken_installation[manifest-wrapper] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> relay\\releases\\old' -> '<tmp> relay\\current' |
| tests/test_update.py::test_explicit_role_ignores_an_unrelated_broken_installation[metadata-relay] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> relay\\releases\\old' -> '<tmp> relay\\current' |
| tests/test_update.py::test_explicit_role_ignores_an_unrelated_broken_installation[metadata-wrapper] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> relay\\releases\\old' -> '<tmp> relay\\current' |
| tests/test_update.py::test_failed_registration_restores_the_previous_command | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_installer_failure_is_not_retried_or_reported_as_success | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_installer_keeps_lock_if_the_controller_disconnects | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_update.py::test_invalid_or_older_version_does_not_download[../4.0.1] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_invalid_or_older_version_does_not_download[04.0.1] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_invalid_or_older_version_does_not_download[3.0.0] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_invalid_or_older_version_does_not_download[4.0.1-beta] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_invalid_or_older_version_does_not_download[4.0.1;id] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_invalid_or_older_version_does_not_download[v4.0.1] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_launcher_does_not_fall_back_when_requested_runtime_is_missing[relay] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> roots\\cc-remote\\release' -> '<tmp> roots\\cc-remote\\current'" |
| tests/test_update.py::test_launcher_does_not_fall_back_when_requested_runtime_is_missing[wrapper] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> roots\\cc-remote\\release' -> '<tmp> roots\\cc-remote\\current'" |
| tests/test_update.py::test_launcher_rejects_missing_or_invalid_role[selection0] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> roots\\cc-remote\\release' -> '<tmp> roots\\cc-remote\\current'" |
| tests/test_update.py::test_launcher_rejects_missing_or_invalid_role[selection1] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> roots\\cc-remote\\release' -> '<tmp> roots\\cc-remote\\current'" |
| tests/test_update.py::test_launcher_rejects_missing_or_invalid_role[selection2] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> roots\\cc-remote\\release' -> '<tmp> roots\\cc-remote\\current'" |
| tests/test_update.py::test_launcher_selects_requested_runtime_before_loading_python[--ro-relay] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> roots\\cc-remote\\release' -> '<tmp> roots\\cc-remote\\current'" |
| tests/test_update.py::test_launcher_selects_requested_runtime_before_loading_python[--ro-wrapper] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> roots\\cc-remote\\release' -> '<tmp> roots\\cc-remote\\current'" |
| tests/test_update.py::test_launcher_selects_requested_runtime_before_loading_python[--ro=-relay] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> roots\\cc-remote\\release' -> '<tmp> roots\\cc-remote\\current'" |
| tests/test_update.py::test_launcher_selects_requested_runtime_before_loading_python[--ro=-wrapper] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> roots\\cc-remote\\release' -> '<tmp> roots\\cc-remote\\current'" |
| tests/test_update.py::test_launcher_selects_requested_runtime_before_loading_python[--role-relay] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> roots\\cc-remote\\release' -> '<tmp> roots\\cc-remote\\current'" |
| tests/test_update.py::test_launcher_selects_requested_runtime_before_loading_python[--role-wrapper] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> roots\\cc-remote\\release' -> '<tmp> roots\\cc-remote\\current'" |
| tests/test_update.py::test_launcher_selects_requested_runtime_before_loading_python[--role=-relay] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> roots\\cc-remote\\release' -> '<tmp> roots\\cc-remote\\current'" |
| tests/test_update.py::test_launcher_selects_requested_runtime_before_loading_python[--role=-wrapper] [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> roots\\cc-remote\\release' -> '<tmp> roots\\cc-remote\\current'" |
| tests/test_update.py::test_launcher_uses_last_explicit_role_without_consuming_arguments [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> roots\\cc-remote\\release' -> '<tmp> roots\\cc-remote\\current'" |
| tests/test_update.py::test_macos_upgrade_preserves_operator_environment_and_service_socket | AssertionError: assert 'C:\\Users\\x...nv/bin/python' == 'C:\\Users\\x...\\bin\\python' |
| tests/test_update.py::test_mismatched_bundle_never_reaches_installer[changes0] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_mismatched_bundle_never_reaches_installer[changes1] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_mismatched_bundle_never_reaches_installer[changes2] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_mismatched_bundle_never_reaches_installer[changes3] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_mismatched_bundle_never_reaches_installer[changes4] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_missing_download_never_reaches_activation | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_multiple_roles_need_an_explicit_selection | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> relay\\releases\\old' -> '<tmp> relay\\current' |
| tests/test_update.py::test_protocol_change_requires_explicit_coordinated_upgrade | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_same_version_retention_is_locked_and_check_stays_read_only[False-False-0] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_same_version_retention_is_locked_and_check_stays_read_only[False-True-0] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_same_version_retention_is_locked_and_check_stays_read_only[False-True-1] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_same_version_retention_is_locked_and_check_stays_read_only[True-True-0] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_sdk_change_does_not_restart_an_independent_service | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_selected_managed_root_cannot_impersonate_another_role | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_supervisor_update_downloads_bundle_and_preserves_full_binding | ModuleNotFoundError: No module named 'pwd' |
| tests/test_update.py::test_tampered_or_unsafe_archive_is_rejected[../outside] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_tampered_or_unsafe_archive_is_rejected[/absolute] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_tampered_or_unsafe_archive_is_rejected[checksum-encoding] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_tampered_or_unsafe_archive_is_rejected[checksum] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_tampered_or_unsafe_archive_is_rejected[duplicate-checksum] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_tampered_or_unsafe_archive_is_rejected[link] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_tampered_or_unsafe_archive_is_rejected[release-manifest.json] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_task_launcher_keeps_nonroot_identity_and_never_uses_relay [error] | failed on setup with "OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> roots\\cc-remote\\release' -> '<tmp> roots\\cc-remote\\current'" |
| tests/test_update.py::test_update_downloads_verified_bundle_and_preserves_install_identity[relay-linux] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> relay\\releases\\old' -> '<tmp> relay\\current' |
| tests/test_update.py::test_update_downloads_verified_bundle_and_preserves_install_identity[wrapper-darwin] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_update_downloads_verified_bundle_and_preserves_install_identity[wrapper-linux] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_update_lock_is_exclusive_and_survives_holder_failure | AttributeError: module 'os' has no attribute 'O_NOFOLLOW' |
| tests/test_update.py::test_version_is_refreshed_after_acquiring_the_activation_lock[False] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update.py::test_version_is_refreshed_after_acquiring_the_activation_lock[True] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_diagnostics.py::test_ssh_failure_explains_access_or_transport_problem[1-secret remote command output-remote command failed (exit 1)] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_diagnostics.py::test_ssh_failure_explains_access_or_transport_problem[1-sudo: a password is required; secret-sudo access denied] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_diagnostics.py::test_ssh_failure_explains_access_or_transport_problem[255-Connection reset by peer; secret-SSH connection/handshake …] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_diagnostics.py::test_ssh_failure_explains_access_or_transport_problem[255-Host key verification failed. secret-host key verification…] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_diagnostics.py::test_ssh_failure_explains_access_or_transport_problem[255-ssh: Could not resolve hostname x: secret-hostname lookup …] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_diagnostics.py::test_ssh_failure_explains_access_or_transport_problem[255-ssh: connect to host x port 22: Connection refused; secret…] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_diagnostics.py::test_ssh_failure_explains_access_or_transport_problem[255-ssh: connect to host x port 22: Connection timed out; secr…] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_diagnostics.py::test_ssh_failure_explains_access_or_transport_problem[255-user@host: Permission denied (publickey). secret-SSH authe…] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_diagnostics.py::test_ssh_operation_timeout_keeps_remote_outcome_unknown | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_installer_relay.py::test_bundle_preflight_updates_relay_before_touching_local_service[False-False] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_installer_relay.py::test_bundle_preflight_updates_relay_before_touching_local_service[False-True] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_installer_relay.py::test_bundle_preflight_updates_relay_before_touching_local_service[True-False] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_installer_relay.py::test_bundle_preflight_updates_relay_before_touching_local_service[True-True] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_installer_relay.py::test_explicit_migration_reads_old_contract_but_keeps_upstream_records_in_new_root[False] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_installer_relay.py::test_explicit_migration_reads_old_contract_but_keeps_upstream_records_in_new_root[True] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_installer_relay.py::test_first_upgrade_can_request_an_existing_ssh_target | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_installer_relay.py::test_real_installer_block_runs_new_bundle_preflight_before_local_stop[current] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_update_installer_relay.py::test_real_installer_block_runs_new_bundle_preflight_before_local_stop[protocol-change] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_update_installer_relay.py::test_real_installer_block_runs_new_bundle_preflight_before_local_stop[unreachable] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp>' -> '<tmp>' |
| tests/test_update_relay.py::test_current_device_still_checks_upstream_and_check_mode_never_runs_ssh | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_relay.py::test_device_update_updates_relay_first_or_skips_it_when_current[False] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_relay.py::test_device_update_updates_relay_first_or_skips_it_when_current[True] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_relay.py::test_failed_upstream_verification_never_activates_device[failed-job] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_relay.py::test_failed_upstream_verification_never_activates_device[no-access] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_relay.py::test_failed_upstream_verification_never_activates_device[unknown-job] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_relay.py::test_failed_upstream_verification_never_activates_device[wrong-host] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_relay.py::test_interrupted_upstream_update_rechecks_the_same_job_without_relaunch | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_relay.py::test_legacy_missing_job_is_not_guessed_to_have_never_started | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_relay.py::test_ssh_failure_before_remote_submission_is_retried_with_the_same_identity | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_relay.py::test_ssh_target_is_data_not_shell_syntax[-oProxyCommand=bad] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_relay.py::test_ssh_target_is_data_not_shell_syntax[host;touch /tmp/no] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_relay.py::test_ssh_target_is_data_not_shell_syntax[ssh://host] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |
| tests/test_update_relay.py::test_ssh_target_is_data_not_shell_syntax[user@host\ncmd] | OSError: [WinError 1314] 客户端没有所需的特权。: '<tmp> wrapper\\releases\\old' -> '<tmp> wrapper\\current' |

## Web 测试

仓库里没有 vitest（`web/package.json` 无此依赖），因此按本仓库的门禁跑 `npm --prefix web run test:reliability`。
该脚本用 `&&` 串联，第一处失败即停止，所以又把其中 42 个测试文件逐个执行：41 个通过，1 个失败。

| 测试 | 失败原因（第一行异常） | 归属 |
|---|---|---|
| web/tests/surface-restoration.test.ts | AssertionError [ERR_ASSERTION]: an authoritative empty list alone may create the scoped default New Chat draft | 子项目 4 |

原因：`core.autocrlf=true` 检出的 `web/src/App.tsx` 是 CRLF，测试中 `[\s\S]{0,500}` 这类有长度上限的源码正则多算了 `\r`。
同一正则在把 `\r\n` 规范成 `\n` 后的源码上匹配成功。可以用 `.gitattributes` 固定 LF，或让测试先规范化换行。

Playwright 测试按计划留到冒烟阶段，这里没有跑。
