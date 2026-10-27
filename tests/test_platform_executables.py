from __future__ import annotations

import asyncio
import sys

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
