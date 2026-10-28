from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import re
import socket
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio

import cc_remote.claude_broker.cli as broker_cli
import cc_remote.claude_broker.session as broker_session_module
from cc_remote.claude_broker.cli import _claude_args, _parser
from cc_remote.claude_broker.client import (
    BrokerClient, BrokerClientError, _decode_response,
)
from cc_remote.claude_broker.paths import SOCKET_ENV, default_socket_path
from cc_remote.claude_broker.protocol import (
    BROKER_PROTOCOL_VERSION,
    FrameType,
    HEADER,
    MAX_FRAME_BYTES,
    ProtocolError,
    decode_json,
    encode_json,
    read_frame,
    write_json,
)
from cc_remote.claude_broker.server import (
    BrokerConfig,
    BrokerSecurityError,
    BrokerServer,
)
from cc_remote.claude_broker.session import (
    PTYSession, SessionError, _launch_controls, _model_from_command_stdout,
    _parse_context_markdown,
)
from cc_remote.platform import IS_WINDOWS, ipc, perms
from cc_remote.protocol import (
    AnswerQuestion, AskUser, Model, Perm, SetEffort, SetModel, SetPerm,
)
from cc_remote.wrapper.claude_broker_handle import ClaudeBrokerHandle
from tests.ipc_helpers import SHORT_TMP, share_with_everyone
from tests.test_multisession import _mk_ctx, _mk_machine


RAW_TERMINAL = r'''
def raw_terminal():
    """Raw keyboard input on a POSIX tty or a Windows console (ConPTY)."""
    if os.name != "nt":
        import tty

        tty.setraw(0)

        def read_posix():
            return os.read(0, 65536)

        return read_posix
    import ctypes
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32")
    handle = msvcrt.get_osfhandle(0)
    mode = ctypes.c_uint32()
    kernel32.GetConsoleMode(handle, ctypes.byref(mode))
    # Clear processed/line/echo input; VT input delivers keys as bytes.
    kernel32.SetConsoleMode(handle, (mode.value & ~0x7) | 0x200)

    def read_console():
        return sys.stdin.buffer.raw.read(65536)

    return read_console
'''


def _install_fake(directory: Path, name: str, source: str) -> Path:
    """An executable fake Claude: a script on POSIX, a .cmd shim on Windows."""
    if not IS_WINDOWS:
        executable = directory / name
        executable.write_text(source, encoding="utf-8")
        executable.chmod(0o700)
        return executable
    script = directory / f"{name}.py"
    script.write_text(source, encoding="utf-8")
    shim = directory / f"{name}.cmd"
    shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    return shim


def _set_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    # Path.home() reads USERPROFILE on Windows and HOME elsewhere.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def _args(output: bytes) -> list[str]:
    return json.loads(re.search(rb"ARGS:(\[[^\r\n]*\])", output).group(1))


def _echoed(output: bytes) -> bytes:
    """Concatenate the fake's escaped echoes; a terminal may split one write."""
    return "".join(
        part.decode("unicode_escape") for part in re.findall(rb"OUT<(.*?)>", output)
    ).encode("utf-8")


@pytest.fixture
def fake_claude(tmp_path: Path) -> Path:
    return _install_fake(tmp_path, "fake-claude", r'''#!/usr/bin/env python3
import json
import os
import sys
''' + RAW_TERMINAL + r'''
read = raw_terminal()
os.write(1, b"ARGS:" + json.dumps(sys.argv[1:]).encode() + b"\n")
os.write(1, b"\x1b[31mREADY\x1b[0m\n")
while True:
    data = read()
    if not data or data == b"\x04":
        break
    # Escape control bytes so a terminal emulator (ConPTY) passes them through.
    os.write(1, b"OUT<" + data.decode("utf-8", "replace").encode("unicode_escape") + b">")
''')


@pytest.fixture
def control_fake_claude(tmp_path: Path) -> Path:
    """Tiny raw TUI that records the same durable control rows as Claude."""
    source = (
        r'''#!/usr/bin/env python3
import json
import os
import sys
''' + RAW_TERMINAL + r'''

def arg(name, default=None):
    if name in sys.argv:
        index = sys.argv.index(name)
        if index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    prefix = name + "="
    for value in sys.argv[1:]:
        if value.startswith(prefix):
            return value[len(prefix):]
    return default

sid = arg("--session-id") or arg("--resume")
# Claude dashes separators and a Windows drive colon in the project key.
project = os.getcwd().replace(os.sep, "-").replace(":", "-")
directory = os.path.join(os.path.expanduser("~/.claude/projects"), project)
os.makedirs(directory, exist_ok=True)
transcript = os.path.join(directory, sid + ".jsonl")
open(transcript, "ab").close()

dangerous = "--dangerously-skip-permissions" in sys.argv
allowed = dangerous or "--allow-dangerously-skip-permissions" in sys.argv
cycle = ["default", "acceptEdits", "plan"]
if allowed:
    cycle.append("bypassPermissions")
supports_auto = os.environ.get("FAKE_CLAUDE_AUTO") == "1"
if supports_auto:
    cycle.append("auto")
permission = "bypassPermissions" if dangerous else arg("--permission-mode", "default")
model = arg("--model", "claude-sonnet-5")
pending_model = None
model_confirm_attempts = 0
context_requests = 0

def emit(record):
    with open(transcript, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())

def command(name, value, stdout):
    emit({
        "type": "user",
        "entrypoint": "cli",
        "message": {"content": (
            f"<command-name>/{name}</command-name>"
            f"<command-args>{value}</command-args>"
        )},
    })
    emit({
        "type": "user",
        "entrypoint": "cli",
        "message": {"content": f"<local-command-stdout>{stdout}</local-command-stdout>"},
    })

read = raw_terminal()
os.write(1, b"READY\n")
pending = b""
while True:
    data = read()
    if not data or data == b"\x04":
        break
    pending += data
    while pending:
        if pending.startswith(b"\x1b[Z"):
            pending = pending[3:]
            permission = cycle[(cycle.index(permission) + 1) % len(cycle)]
            labels = {
                "default": "manual mode",
                "acceptEdits": "accept edits on",
                "plan": "plan mode on",
                "bypassPermissions": "bypass permissions on",
                "auto": "auto mode on",
            }
            # Claude 2.1.211 reliably repaints this native footer but does not
            # append a permission-mode row for every Shift+Tab transition.
            os.write(1, ("\r" + labels[permission] + "\n").encode())
            if os.environ.get("FAKE_CLAUDE_PERMISSION_JSONL") == "1":
                reported = (
                    "manual" if permission == "default"
                    and os.environ.get("FAKE_CLAUDE_MANUAL_DEFAULT") == "1"
                    else permission
                )
                emit({"type": "permission-mode", "permissionMode": reported})
            continue
        if b"\r" not in pending:
            break
        raw, pending = pending.split(b"\r", 1)
        line = raw.decode("utf-8", errors="replace")
        if pending_model is not None:
            if line == "":
                model_confirm_attempts += 1
                if (os.environ.get("FAKE_CLAUDE_IGNORE_FIRST_MODEL_CONFIRM") == "1"
                        and model_confirm_attempts == 1):
                    continue
                value = pending_model
                pending_model = None
                model = value
                command("model", value, f"Set model to {value} and saved as your default for new sessions")
            continue
        if line.startswith("/model "):
            value = line[len("/model "):]
            if value == model:
                command("model", value, f"Set model to {value} and saved as your default for new sessions")
            else:
                pending_model = value
                if os.environ.get("FAKE_CLAUDE_UNKNOWN_MODEL_PROMPT") == "1":
                    os.write(1, b"Switch model?\nMODEL CONFIRMATION UI\n")
                else:
                    os.write(1, (
                        "\x1b[93mSwitch model?\x1b[39m\n"
                        "Your next response will be slower and use more tokens\n"
                        "This conversation is cached\x1b[32Gfor\x1b[36Gthe"
                        "\x1b[40Gcurrent\x1b[48Gmodel. "
                        f"Switching\x1b[65Gto\x1b[68G{value} means the full history "
                        "gets re-read on your next message.\n"
                        f"1. Yes, switch\x1b[24Gto {value}\n"
                        "2. No, go back\n"
                    ).encode())
        elif line.startswith("/effort "):
            value = line[len("/effort "):]
            command("effort", value, f"Set effort level to {value}")
        elif line == "/context":
            context_requests += 1
            if (os.environ.get("FAKE_CLAUDE_DROP_FIRST_CONTEXT") == "1"
                    and context_requests == 1):
                continue
            command("context", "", "Context Usage")
            emit({
                "type": "user",
                "isMeta": True,
                "message": {"content": (
                    "## Context Usage\n\n"
                    f"**Model:** {model}  \n"
                    "**Tokens:** 32.4k / 1m (3%)\n\n"
                    "| Category | Tokens | Percentage |\n"
                    "|----------|--------|------------|\n"
                    "| System prompt | 2.3k | 0.2% |\n"
                    "| Messages | 7.6k | 0.8% |\n"
                )},
            })
'''
    )
    return _install_fake(tmp_path, "control-fake-claude", source)


@pytest.fixture
def short_socket_dir():
    # Darwin's sockaddr_un path is only 104 bytes; pytest's TMPDIR path is much
    # longer than a realistic XDG/home endpoint.
    path = Path(tempfile.mkdtemp(prefix="ccrb-", dir=SHORT_TMP))
    path.chmod(0o700)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest_asyncio.fixture
async def broker(tmp_path: Path, fake_claude: Path, short_socket_dir: Path):
    socket_path = short_socket_dir / "broker.sock"
    server = BrokerServer(BrokerConfig(
        socket_path=str(socket_path),
        claude_binary=str(fake_claude),
        max_sessions=4,
        history_bytes=512 * 1024,
    ))
    await server.start()
    try:
        yield server, BrokerClient(str(socket_path))
    finally:
        await server.close()


@pytest_asyncio.fixture(
    params=[(False, False), (True, False), (False, True)],
    ids=["without-auto", "with-auto", "manual-default-alias"],
)
async def control_broker(
    tmp_path: Path,
    control_fake_claude: Path,
    short_socket_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
):
    home = tmp_path / "home"
    home.mkdir()
    _set_home(monkeypatch, home)
    supports_auto, reports_manual = request.param
    monkeypatch.setenv("FAKE_CLAUDE_AUTO", "1" if supports_auto else "0")
    monkeypatch.setenv(
        "FAKE_CLAUDE_MANUAL_DEFAULT", "1" if reports_manual else "0")
    socket_path = short_socket_dir / "control-broker.sock"
    server = BrokerServer(BrokerConfig(
        socket_path=str(socket_path),
        claude_binary=str(control_fake_claude),
        max_sessions=2,
        history_bytes=128 * 1024,
    ))
    await server.start()
    try:
        yield server, BrokerClient(str(socket_path)), request.param
    finally:
        await server.close()


async def _output_until(attachment, needle: bytes, timeout: float = 10.0) -> bytes:
    output = bytearray()
    async with asyncio.timeout(timeout):
        while needle not in output:
            frame_type, payload = await attachment.read()
            if frame_type is FrameType.OUTPUT:
                assert isinstance(payload, bytes)
                output.extend(payload)
            elif frame_type is FrameType.ERROR:
                pytest.fail(f"attachment error: {payload}")
            elif frame_type is FrameType.EXIT:
                pytest.fail(f"session exited before {needle!r}: {payload}")
    return bytes(output)


@pytest.mark.asyncio
async def test_context_query_retries_one_ignored_startup_command(
    tmp_path: Path,
    control_fake_claude: Path,
    short_socket_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "retry-home"
    home.mkdir()
    _set_home(monkeypatch, home)
    monkeypatch.setenv("FAKE_CLAUDE_DROP_FIRST_CONTEXT", "1")
    monkeypatch.setattr(
        broker_session_module, "CONTEXT_CONFIRM_TIMEOUT_SECONDS", 0.2)
    socket_path = short_socket_dir / "retry-broker.sock"
    server = BrokerServer(BrokerConfig(
        socket_path=str(socket_path),
        claude_binary=str(control_fake_claude),
        max_sessions=1,
        history_bytes=128 * 1024,
    ))
    await server.start()
    client = BrokerClient(str(socket_path))
    try:
        project = tmp_path / "retry-project"
        project.mkdir()
        created = await client.new(cwd=str(project))
        sid = created["session"]["id"]
        terminal = await client.attach(sid, keyboard=False)
        await _output_until(terminal, b"READY")
        response = await client.get_context_usage(sid)
        assert response["context_usage"]["model"] == "claude-sonnet-5"
        await terminal.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_model_control_confirms_changed_native_prompt_after_fresh_output(
    tmp_path: Path,
    control_fake_claude: Path,
    short_socket_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "model-confirm-home"
    home.mkdir()
    _set_home(monkeypatch, home)
    monkeypatch.setenv("FAKE_CLAUDE_UNKNOWN_MODEL_PROMPT", "1")
    monkeypatch.setenv("FAKE_CLAUDE_IGNORE_FIRST_MODEL_CONFIRM", "1")
    monkeypatch.setattr(
        broker_session_module, "MODEL_CONFIRM_MIN_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(
        broker_session_module, "MODEL_CONFIRM_OUTPUT_QUIET_SECONDS", 0.01)
    monkeypatch.setattr(
        broker_session_module, "MODEL_CONFIRM_RETRY_SECONDS", 0.05)
    socket_path = short_socket_dir / "model-confirm-broker.sock"
    server = BrokerServer(BrokerConfig(
        socket_path=str(socket_path),
        claude_binary=str(control_fake_claude),
        max_sessions=1,
        history_bytes=128 * 1024,
    ))
    await server.start()
    client = BrokerClient(str(socket_path))
    try:
        project = tmp_path / "model-confirm-project"
        project.mkdir()
        created = await client.new(cwd=str(project))
        sid = created["session"]["id"]
        terminal = await client.attach(sid, keyboard=False)
        await _output_until(terminal, b"READY")

        response = await client.set_model(sid, "claude-opus-4-8")

        assert response["session"]["model"] == "claude-opus-4-8"
        await terminal.close()
    finally:
        await server.close()


def test_native_context_markdown_and_interactive_model_are_parsed():
    report = _parse_context_markdown("""## Context Usage

**Model:** claude-opus-4-8[1m]
**Tokens:** 32.4k / 1m (3%)

| Category | Tokens | Percentage |
|----------|--------|------------|
| System prompt | 2.3k | 0.2% |
| Messages | 7.6k | 0.8% |
""")
    assert report is not None
    assert report["totalTokens"] == 32_400
    assert report["maxTokens"] == 1_000_000
    assert report["model"] == "claude-opus-4-8[1m]"
    assert [item["tokens"] for item in report["categories"]] == [2300, 7600]

    session = object.__new__(PTYSession)
    session._native_pending_control = None
    session.model = "claude-sonnet-5"
    session._on_control_change = None
    session._on_change = lambda: 1
    session.revision = 0
    session._adopt_transcript_records([
        {
            "message": {"content": (
                "<command-name>/model</command-name>"
                "<command-args></command-args>"
            )},
        },
        {
            "message": {"content": (
                "<local-command-stdout>Set model to Opus 4.8 (1M context)"
                "</local-command-stdout>"
            )},
        },
    ])
    assert session.model == "claude-opus-4-8[1m]"

    live_stdout = (
        "<local-command-stdout>\x1b[32mSet model to Sonnet 5\x1b[0m\n"
        "  settings pins this model for the current session\n"
        "</local-command-stdout>"
    )
    assert _model_from_command_stdout(live_stdout) == "claude-sonnet-5"
    assert _model_from_command_stdout(live_stdout.replace(
        "Sonnet 5", "Opus 4.8 (1M context)")) == "claude-opus-4-8[1m]"


def test_client_rejects_a_stale_broker_protocol():
    with pytest.raises(BrokerClientError) as error:
        _decode_response(FrameType.RESPONSE, encode_json({
            "ok": True,
            "generation": "old-broker",
        }))
    assert error.value.code == "broker_upgrade_required"

    response = _decode_response(FrameType.RESPONSE, encode_json({
        "ok": True,
        "broker_protocol": BROKER_PROTOCOL_VERSION,
    }))
    assert response["ok"] is True


@pytest.mark.asyncio
async def test_new_attach_atomic_send_detach_and_resume(broker, tmp_path: Path):
    _server, client = broker
    created = await client.new(cwd=str(tmp_path))
    sid = created["session"]["id"]
    assert str(uuid.UUID(sid)) == sid
    assert created["session"]["generation"] == created["generation"]
    assert created["session"]["revision"] == created["revision"]
    assert created["session"]["pid"] > 0
    assert created["session"]["start_ticks"] > 0
    assert created["session"]["cwd"] == str(tmp_path)

    primary = await client.attach(sid)
    mirror = await client.attach(sid)
    assert primary.keyboard is True
    assert mirror.keyboard is False
    initial = await _output_until(primary, b"READY")
    assert b"\x1b[31mREADY\x1b[0m" in initial
    argv = _args(initial)
    assert argv[:2] == ["--session-id", sid]
    assert not any(value.startswith("--remote-control") for value in argv)

    status = await client.status(sid)
    assert status["session"]["attached_count"] == 2
    assert status["session"]["keyboard_attached"] is True

    await primary.write(b"unfinished")
    await _output_until(primary, b"OUT<unfinished>")
    with pytest.raises(BrokerClientError, match="unfinished input line") as blocked:
        await client.send(sid, "must not interleave")
    assert blocked.value.code == "input_busy"

    # Dropping the keyboard socket must not kill Claude or forget bytes already
    # sitting in its terminal edit buffer.
    await primary.close()
    detached = await client.status(sid)
    assert detached["session"]["running"] is True
    assert detached["session"]["attached_count"] == 1
    assert detached["session"]["keyboard_attached"] is False
    assert detached["session"]["terminal_composing"] is True
    with pytest.raises(BrokerClientError) as still_blocked:
        await client.send(sid, "still must not interleave")
    assert still_blocked.value.code == "input_busy"

    replacement = await client.attach(sid)
    assert replacement.keyboard is True
    interrupted = await client.interrupt(sid)
    assert interrupted["session"]["terminal_composing"] is False
    assert b"\x03" in _echoed(await _output_until(replacement, b"OUT<\\x03>"))
    submitted = await client.send(sid, "remote prompt")
    assert submitted["session"]["id"] == sid
    assert b"remote prompt\r" in _echoed(await _output_until(replacement, b"\\r>"))
    with pytest.raises(BrokerClientError) as read_only:
        await mirror.write(b"nope")
    assert read_only.value.code == "input_read_only"

    await replacement.close()
    replay_attachment = await client.attach(sid)
    replay = await _output_until(replay_attachment, b"remote prompt")
    assert b"\x1b[31mREADY\x1b[0m" in replay
    await replay_attachment.close()
    await mirror.close()

    stopped = await client.stop(sid)
    assert stopped["session"]["running"] is False
    resumed = await client.resume(sid, cwd=str(tmp_path))
    assert resumed["session"]["id"] == sid
    assert resumed["session"]["kind"] == "resume"
    resumed_attachment = await client.attach(sid)
    resumed_output = await _output_until(resumed_attachment, b"READY")
    assert _args(resumed_output)[:2] == ["--resume", sid]
    await resumed_attachment.close()
    await client.stop(sid)


@pytest.mark.asyncio
async def test_terminal_focus_input_waits_for_remote_control_without_detaching(
    broker, tmp_path: Path,
):
    server, client = broker
    created = await client.new(cwd=str(tmp_path))
    sid = created["session"]["id"]
    terminal = await client.attach(sid)
    await _output_until(terminal, b"READY")
    session = server.manager.get(sid)

    await session._control_lock.acquire()
    session._control_in_progress = True
    try:
        # Claude enables xterm focus reporting. Switching from the terminal to
        # Remote sends ESC[O at exactly the same time as a Remote control.
        # ConPTY consumes the focus report it requested itself; the trailing bytes prove the frame was serialized, not rejected.
        await terminal.write(b"\x1b[Ofocus")
        await asyncio.sleep(0.05)
        status = await client.status(sid)
        assert status["session"]["attached_count"] == 1
    finally:
        session._control_in_progress = False
        session._control_lock.release()

    assert b"focus" in _echoed(await _output_until(terminal, b"focus>"))
    status = await client.status(sid)
    assert status["session"]["running"] is True
    assert status["session"]["keyboard_attached"] is True
    await terminal.close()
    await client.stop(sid)


@pytest.mark.asyncio
async def test_machine_broker_controls_reach_native_tui_and_use_footer_confirmation(
    control_broker, tmp_path: Path,
):
    _server, client, (_supports_auto, _reports_manual) = control_broker
    project = tmp_path / "control-project"
    project.mkdir()
    created = await client.new(cwd=str(project))
    metadata = created["session"]
    sid = metadata["id"]
    assert metadata["permission_mode"] == "bypassPermissions"
    handle = ClaudeBrokerHandle(client, sid, metadata)
    await handle.connect(resume_id=sid, cwd=str(project))
    terminal = await client.attach(sid, keyboard=False)
    await _output_until(terminal, b"READY")

    machine, transport = _mk_machine()
    ctx = _mk_ctx(sid, sid)
    ctx.cwd = str(project)
    ctx.engine = "claude"
    ctx.space = "code"
    ctx.sdk = handle
    ctx.announced_perm = "bypassPermissions"
    machine.sessions[sid] = ctx

    await machine._process_command(SetPerm(
        sid=sid, mode="default", cmd_id="perm-native", client_id="client-1"))
    model_task = asyncio.create_task(machine._process_command(SetModel(
        sid=sid, model="claude-opus-4-1",
        cmd_id="model-native", client_id="client-1")))
    async with asyncio.timeout(1.0):
        while True:
            question = next(
                (event for event in reversed(transport.sent)
                 if isinstance(event, AskUser)), None)
            if question is not None:
                break
            await asyncio.sleep(0.01)
    assert "重新读取完整历史" in question.question
    assert question.to == "client-1"
    await machine._process_command(AnswerQuestion(
        sid=sid,
        ask_id=question.ask_id,
        answer=question.options[0]["label"],
        client_id="client-1",
    ))
    await model_task
    await machine._process_command(SetEffort(
        sid=sid, effort="max", cmd_id="effort-native", client_id="client-1"))

    assert handle.permission_mode == "default"
    assert handle.model == "claude-opus-4-1"
    assert handle.effort == handle.applied_effort == "max"
    usage = await handle.get_context_usage()
    assert usage["totalTokens"] == 32_400
    assert usage["model"] == "claude-opus-4-1"
    assert [event.mode for event in transport.sent
            if isinstance(event, Perm)] == ["default"]
    assert [event.model for event in transport.sent
            if isinstance(event, Model)] == ["claude-opus-4-1"]
    transcript = Path(os.path.expanduser(
        f"~/.claude/projects/{str(project).replace(os.sep, '-').replace(':', '-')}/{sid}.jsonl"))
    records = [json.loads(line) for line in transcript.read_text().splitlines()]
    permission_records = [
        record.get("permissionMode") for record in records
        if record.get("type") == "permission-mode"
    ]
    assert permission_records == []
    assert any(
        "<command-args>claude-opus-4-1</command-args>"
        in record.get("message", {}).get("content", "")
        for record in records
    )
    assert any(
        "<command-args>max</command-args>"
        in record.get("message", {}).get("content", "")
        for record in records
    )
    await terminal.close()
    await client.stop(sid)


@pytest.mark.asyncio
async def test_native_terminal_permission_footer_updates_broker_state(
    control_broker, tmp_path: Path,
):
    _server, client, (supports_auto, _reports_manual) = control_broker
    project = tmp_path / "terminal-permission-project"
    project.mkdir()
    created = await client.new(cwd=str(project))
    sid = created["session"]["id"]
    terminal = await client.attach(sid)
    await _output_until(terminal, b"READY")

    await terminal.write(b"\x1b[Z")
    expected = "auto" if supports_auto else "default"
    await _output_until(
        terminal, b"auto mode" if supports_auto else b"manual mode")
    async with asyncio.timeout(1.0):
        while True:
            status = await client.status(sid)
            if status["session"]["permission_mode"] == expected:
                break
            await asyncio.sleep(0.02)

    await terminal.close()
    await client.stop(sid)


@pytest.mark.asyncio
async def test_broker_persists_sdk_controls_into_next_native_resume(
    broker, tmp_path: Path,
):
    _server, client = broker
    sid = str(uuid.uuid4())
    await client.set_preferences(
        sid,
        model="claude-fable-5",
        effort="max",
        permission_mode="plan",
        auto_compact="250000",
    )

    resumed = await client.resume(sid, cwd=str(tmp_path))
    assert resumed["session"]["model"] == "claude-fable-5"
    assert resumed["session"]["effort"] == "max"
    assert resumed["session"]["permission_mode"] == "plan"
    assert resumed["session"]["auto_compact"] == "250000"
    assert resumed["session"]["bypass_allowed"] is True
    attachment = await client.attach(sid)
    output = await _output_until(attachment, b"READY")
    args = _args(output)
    assert args[:2] == ["--resume", sid]
    assert args[args.index("--model") + 1] == "claude-fable-5"
    assert args[args.index("--effort") + 1] == "max"
    assert args[args.index("--permission-mode") + 1] == "plan"
    assert args[args.index("--autocompact") + 1] == "250000"
    assert "--allow-dangerously-skip-permissions" in args
    await attachment.close()
    await client.stop(sid)

    store = Path(client.socket_path).with_name("session-controls.json")
    assert perms.is_private(store)


@pytest.mark.asyncio
async def test_status_list_limits_and_forbidden_claude_flags(
    tmp_path: Path, fake_claude: Path, short_socket_dir: Path,
):
    socket_path = short_socket_dir / "broker.sock"
    server = BrokerServer(BrokerConfig(
        socket_path=str(socket_path), claude_binary=str(fake_claude), max_sessions=1,
    ))
    await server.start()
    client = BrokerClient(str(socket_path))
    try:
        assert perms.is_private(socket_path)
        status = await client.status()
        assert status["status"]["max_sessions"] == 1
        assert status["status"]["session_count"] == 0
        assert uuid.UUID(status["generation"])

        first = await client.new(cwd=str(tmp_path))
        sid = first["session"]["id"]
        listing = await client.list()
        assert [item["id"] for item in listing["sessions"]] == [sid]
        with pytest.raises(BrokerClientError) as limited:
            await client.new(cwd=str(tmp_path))
        assert limited.value.code == "session_limit"
        await client.stop(sid)
        replacement = await client.new(cwd=str(tmp_path))
        assert replacement["session"]["id"] != sid

        for forbidden in (
            ["--session-id", str(uuid.uuid4())],
            ["--resume=deadbeef"],
            ["--continue"],
            ["--fork-session"],
            ["--remote-control"],
            ["--remote-control-port=9999"],
        ):
            await client.stop(replacement["session"]["id"])
            with pytest.raises(BrokerClientError) as rejected:
                await client.new(cwd=str(tmp_path), args=forbidden)
            assert rejected.value.code == "bad_args"
            replacement = await client.new(cwd=str(tmp_path))
    finally:
        await server.close()
    assert not socket_path.exists()


@pytest.mark.asyncio
async def test_second_server_and_unsafe_socket_path_are_rejected(
    tmp_path: Path, fake_claude: Path, short_socket_dir: Path,
):
    socket_path = short_socket_dir / "broker.sock"
    first = BrokerServer(BrokerConfig(
        socket_path=str(socket_path), claude_binary=str(fake_claude),
    ))
    await first.start()
    second = BrokerServer(BrokerConfig(
        socket_path=str(socket_path), claude_binary=str(fake_claude),
    ))
    try:
        with pytest.raises(BrokerSecurityError, match="already running"):
            await second.start()
    finally:
        await first.close()

    socket_path.write_text("do not replace", encoding="utf-8")
    third = BrokerServer(BrokerConfig(
        socket_path=str(socket_path), claude_binary=str(fake_claude),
    ))
    with pytest.raises(BrokerSecurityError, match="refusing to replace"):
        await third.start()
    assert socket_path.read_text(encoding="utf-8") == "do not replace"


@pytest.mark.asyncio
async def test_close_completes_while_a_client_is_still_connected(
    fake_claude: Path, short_socket_dir: Path,
):
    socket_path = short_socket_dir / "broker.sock"
    server = BrokerServer(BrokerConfig(
        socket_path=str(socket_path), claude_binary=str(fake_claude),
    ))
    await server.start()
    reader, writer = await ipc.open_connection(socket_path)
    try:
        for _ in range(500):
            if server._connections:
                break
            await asyncio.sleep(0.01)
        assert server._connections  # the idle client waits for its first frame
        # Since Python 3.12 Server.wait_closed() also waits for open
        # connections, so close() must drop its clients before waiting.
        await asyncio.wait_for(server.close(), 5)
        assert await asyncio.wait_for(reader.read(), 5) == b""
        assert not socket_path.exists()
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        await server.close()


async def _read_to_eof(reader: asyncio.StreamReader) -> bytes:
    try:
        return await asyncio.wait_for(reader.read(), 5)
    except ConnectionError:
        return b""  # a reset is the Windows form of the broker hanging up


@pytest.mark.asyncio
async def test_a_peer_admitted_while_closing_is_dropped_unserved(
    fake_claude: Path, short_socket_dir: Path, monkeypatch: pytest.MonkeyPatch,
):
    # The peer finishes proving itself only after close() has begun: the
    # endpoint's guard hands it over late, past close()'s client snapshot.
    admitted = asyncio.Event()
    proven = asyncio.Event()
    original = BrokerServer._handle_client

    async def late_handshake(self, reader, writer):
        admitted.set()
        await proven.wait()
        await original(self, reader, writer)

    monkeypatch.setattr(BrokerServer, "_handle_client", late_handshake)
    socket_path = short_socket_dir / "broker.sock"
    server = BrokerServer(BrokerConfig(
        socket_path=str(socket_path), claude_binary=str(fake_claude),
    ))
    served: list[object] = []
    serving: list[object] = []
    original_dispatch = server._dispatch

    async def dispatch(reader, writer, request):
        served.append(request.get("op"))
        serving.append(request)
        try:
            await original_dispatch(reader, writer, request)
        finally:
            serving.remove(request)

    server._dispatch = dispatch
    serving_at_manager_close: list[int] = []
    original_manager_close = server.manager.close

    async def manager_close():
        serving_at_manager_close.append(len(serving))
        await original_manager_close()

    server.manager.close = manager_close
    await server.start()
    reader, writer = await ipc.open_connection(socket_path)
    closing = None
    try:
        await asyncio.wait_for(admitted.wait(), 5)
        closing = asyncio.create_task(server.close())
        await asyncio.sleep(0)  # close() has begun
        proven.set()
        with contextlib.suppress(ConnectionError):
            await write_json(writer, FrameType.COMMAND, {"op": "status"})
        assert await _read_to_eof(reader) == b""
        await asyncio.wait_for(closing, 5)
        assert served == []
        assert serving_at_manager_close == [0]
        assert not server._connections
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        if closing is not None and not closing.done():
            await asyncio.gather(closing, return_exceptions=True)
        await server.close()


@pytest.mark.asyncio
async def test_a_failed_endpoint_withdrawal_still_closes_clients_and_sessions(
    fake_claude: Path, short_socket_dir: Path, monkeypatch: pytest.MonkeyPatch,
):
    socket_path = short_socket_dir / "broker.sock"
    server = BrokerServer(BrokerConfig(
        socket_path=str(socket_path), claude_binary=str(fake_claude),
    ))
    manager_closes: list[int] = []
    original_manager_close = server.manager.close

    async def manager_close():
        manager_closes.append(len(server._connections))
        await original_manager_close()

    server.manager.close = manager_close
    await server.start()
    real_withdraw = ipc._withdraw
    withdrawals: list[Path] = []

    def withdraw(target, *args, **kwargs):
        withdrawals.append(target)
        if len(withdrawals) == 1:
            raise PermissionError(13, "sharing violation")
        return real_withdraw(target, *args, **kwargs)

    monkeypatch.setattr(ipc, "_withdraw", withdraw)
    reader, writer = await ipc.open_connection(socket_path)
    try:
        for _ in range(500):
            if server._connections:
                break
            await asyncio.sleep(0.01)
        assert server._connections
        with pytest.raises(PermissionError, match="sharing violation"):
            await asyncio.wait_for(server.close(), 5)
        assert await _read_to_eof(reader) == b""
        assert manager_closes == [0]
        assert socket_path.exists()  # the record is still published...
        await asyncio.wait_for(server.close(), 5)  # ...so the retry withdraws it
        assert len(withdrawals) == 2
        assert not socket_path.exists()
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        monkeypatch.setattr(ipc, "_withdraw", real_withdraw)
        with contextlib.suppress(OSError):
            await server.close()


@pytest.mark.asyncio
async def test_a_shared_socket_directory_is_refused(
    fake_claude: Path, short_socket_dir: Path,
):
    shared = short_socket_dir / "shared"
    shared.mkdir()
    share_with_everyone(shared)
    server = BrokerServer(BrokerConfig(
        socket_path=str(shared / "broker.sock"), claude_binary=str(fake_claude),
    ))
    with pytest.raises(BrokerSecurityError, match="broker socket directory"):
        await server.start()
    assert not (shared / "broker.sock").exists()


@pytest.mark.asyncio
async def test_frame_header_limit_is_checked_before_payload_allocation():
    reader = asyncio.StreamReader()
    reader.feed_data(HEADER.pack(int(FrameType.INPUT), MAX_FRAME_BYTES + 1))
    reader.feed_eof()
    with pytest.raises(ProtocolError, match="exceeds"):
        await read_frame(reader)
    with pytest.raises(ProtocolError, match="JSON object"):
        decode_json(b"[]")


@pytest.mark.posix_only(reason="SO_PEERCRED/getpeereid is the POSIX same-user proof")
def test_peer_uid_reads_the_connected_user():
    left, right = socket.socketpair()
    try:
        assert ipc._peer_uid(left) == os.getuid()
        assert ipc._peer_uid(right) == os.getuid()
    finally:
        left.close()
        right.close()


def test_client_import_leaves_the_pty_host_unloaded():
    # The wrapper imports the client; the PTY host (pywinpty) is broker-only.
    probe = (
        "import sys, cc_remote.claude_broker.client; "
        "print([m for m in ('cc_remote.claude_broker.server', "
        "'cc_remote.claude_broker.session', 'cc_remote.platform.pty', 'winpty') "
        "if m in sys.modules])"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=60, check=True,
    )
    assert result.stdout.strip() == "[]"


def test_default_socket_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv(SOCKET_ENV, raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    if IS_WINDOWS:
        # MSYS2/Cygwin/WSL shells export an XDG_RUNTIME_DIR outside the
        # profile, which the private-endpoint check would refuse.
        home = tmp_path / "profile"
        _set_home(monkeypatch, home)
        assert default_socket_path() == str(home / ".cc-remote" / "claude-broker.sock")
    else:
        assert default_socket_path() == str(tmp_path / "cc-remote" / "claude-broker.sock")
    override = tmp_path / "override.sock"
    monkeypatch.setenv(SOCKET_ENV, str(override))
    assert default_socket_path() == str(override)


def test_explicit_cli_keeps_its_options_out_of_official_claude_argv():
    sid = "11111111-1111-4111-8111-111111111111"
    args = _parser().parse_args([
        "resume", sid, "--cwd", "/tmp/project", "--no-attach",
        "--", "--model", "sonnet",
    ])
    assert args.command == "resume"
    assert args.session_id == sid
    assert args.cwd == "/tmp/project"
    assert args.no_attach is True
    assert _claude_args(args.claude_args) == ["--model", "sonnet"]
    assert "--remote-control" not in args.claude_args


def test_launch_controls_normalize_official_manual_permission_alias():
    (_model, _effort, permission, bypass_allowed,
     auto_compact) = _launch_controls([
        "--permission-mode", "manual",
        "--allow-dangerously-skip-permissions",
    ])
    assert permission == "default"
    assert bypass_allowed is True
    assert auto_compact is None


def test_launch_controls_observe_only_bounded_autocompact_values():
    assert _launch_controls(["--autocompact", "auto"])[-1] == "auto"
    assert _launch_controls(["--autocompact", "200000"])[-1] == "200000"
    assert _launch_controls(["--autocompact", "99999"])[-1] is None
    assert _launch_controls(["--autocompact", "200k"])[-1] is None


@pytest.mark.asyncio
async def test_control_confirmation_window_rejects_remote_send_and_interrupt():
    session = object.__new__(PTYSession)
    session._control_in_progress = True
    session._terminal_composing = False

    with pytest.raises(SessionError) as remote_send:
        await session.submit_text("hello")
    with pytest.raises(SessionError) as interrupt:
        await session.interrupt()
    assert remote_send.value.code == "input_busy"
    assert interrupt.value.code == "input_busy"


def test_transcript_fallback_fails_closed_for_duplicate_sid_across_cwds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _set_home(monkeypatch, tmp_path)
    sid = "11111111-1111-4111-8111-111111111111"
    root = tmp_path / ".claude" / "projects"
    for project in ("-old-cwd", "-copied-cwd"):
        directory = root / project
        directory.mkdir(parents=True)
        (directory / f"{sid}.jsonl").write_text("", encoding="utf-8")
    session = object.__new__(PTYSession)
    session.id = sid
    session.cwd = "/cwd-without-a-direct-transcript"
    session._transcript_path_cache = None
    assert session._find_transcript() is None


def test_transcript_lookup_uses_configured_claude_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    home = tmp_path / "home"
    active = tmp_path / "profiles" / "claude"
    _set_home(monkeypatch, home)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(active))
    sid = "11111111-1111-4111-8111-111111111111"
    cwd = "/configured-root"
    source = active / "projects" / "-configured-root" / f"{sid}.jsonl"
    decoy = home / ".claude" / "projects" / "-configured-root" / f"{sid}.jsonl"
    source.parent.mkdir(parents=True)
    decoy.parent.mkdir(parents=True)
    source.write_text("", encoding="utf-8")
    decoy.write_text("", encoding="utf-8")

    session = object.__new__(PTYSession)
    session.id = sid
    session.cwd = cwd
    session._transcript_path_cache = None

    assert session._find_transcript() == str(source.resolve())


@pytest.mark.asyncio
async def test_cli_new_freezes_the_invoking_process_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
):
    calls: list[tuple[str, str | None, list[str] | None]] = []

    class Client:
        def __init__(self, _socket_path: str):
            pass

        async def new(self, *, cwd=None, args=None):
            calls.append(("new", cwd, args))
            return {"ok": True}

    async def broker_ready(_client, _socket_path):
        return None

    monkeypatch.setattr(broker_cli, "BrokerClient", Client)
    monkeypatch.setattr(broker_cli, "_ensure_broker", broker_ready)
    monkeypatch.chdir(tmp_path)

    assert await broker_cli.async_main(["--json", "new", "--no-attach"]) == 0
    assert calls == [("new", os.path.realpath(tmp_path), [])]
    assert json.loads(capsys.readouterr().out) == {"ok": True}


@pytest.mark.requires_symlink(reason="the test resolves a symlinked cwd alias")
@pytest.mark.asyncio
async def test_cli_resume_normalizes_an_explicit_cwd_before_broker_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
):
    sid = "11111111-1111-4111-8111-111111111111"
    project = tmp_path / "project"
    project.mkdir()
    alias = tmp_path / "project-link"
    alias.symlink_to(project, target_is_directory=True)
    calls: list[tuple[str, str, str | None, list[str] | None]] = []

    class Client:
        def __init__(self, _socket_path: str):
            pass

        async def resume(self, session_id, *, cwd=None, args=None):
            calls.append(("resume", session_id, cwd, args))
            return {"ok": True}

    async def broker_ready(_client, _socket_path):
        return None

    monkeypatch.setattr(broker_cli, "BrokerClient", Client)
    monkeypatch.setattr(broker_cli, "_ensure_broker", broker_ready)

    argv = [
        "--json", "resume", sid, "--cwd", str(alias), "--no-attach",
        "--", "--model", "sonnet",
    ]
    assert await broker_cli.async_main(argv) == 0
    assert calls == [("resume", sid, str(project.resolve()), ["--model", "sonnet"])]
    assert json.loads(capsys.readouterr().out) == {"ok": True}


@pytest.mark.asyncio
async def test_cli_attachment_treats_control_input_busy_as_recoverable(
    monkeypatch: pytest.MonkeyPatch,
):
    written = bytearray()

    class Attachment:
        keyboard = False

        def __init__(self):
            self.closed = False
            self.frames = [
                (FrameType.ERROR, {
                    "error": {"code": "input_busy", "message": "control pending"},
                }),
                (FrameType.OUTPUT, b"still-attached"),
                (FrameType.EXIT, {"returncode": 7}),
            ]

        async def read(self):
            return self.frames.pop(0)

        async def close(self):
            self.closed = True

        async def resize(self, *_args):
            return None

    attachment = Attachment()

    class Client:
        async def attach(self, _session_id, *, keyboard=True):
            assert keyboard is False
            return attachment

    class Stream:
        def __init__(self, fd):
            self.fd = fd

        def fileno(self):
            return self.fd

    monkeypatch.setattr(
        broker_cli, "_write_all", lambda _fd, data: written.extend(data))
    monkeypatch.setattr(broker_cli.sys, "stdin", Stream(0))
    monkeypatch.setattr(broker_cli.sys, "stdout", Stream(1))

    result = await broker_cli._interactive_attach(
        Client(), "11111111-1111-4111-8111-111111111111", keyboard=False)

    assert result == 7
    assert written == b"still-attached"
    assert attachment.closed is True


@pytest.mark.asyncio
async def test_cli_resume_reattaches_an_exact_running_broker_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    sid = "11111111-1111-4111-8111-111111111111"
    calls: list[tuple[str, object]] = []

    class Client:
        def __init__(self, _socket_path: str):
            pass

        async def resume(self, session_id, *, cwd=None, args=None):
            calls.append(("resume", (session_id, cwd, args)))
            raise BrokerClientError(
                "session_exists", f"session is already running: {session_id}")

        async def status(self, session_id=None):
            calls.append(("status", session_id))
            return {
                "ok": True,
                "generation": "generation-1",
                "session": {"id": sid, "running": True, "pid": 1234},
            }

    async def broker_ready(_client, _socket_path):
        return None

    async def attach(_client, session_id, *, keyboard):
        calls.append(("attach", (session_id, keyboard)))
        return 23

    monkeypatch.setattr(broker_cli, "BrokerClient", Client)
    monkeypatch.setattr(broker_cli, "_ensure_broker", broker_ready)
    monkeypatch.setattr(broker_cli, "_interactive_attach", attach)
    monkeypatch.chdir(tmp_path)

    assert await broker_cli.async_main(["resume", sid]) == 23
    assert calls == [
        ("resume", (sid, str(tmp_path.resolve()), [])),
        ("status", sid),
        ("attach", (sid, True)),
    ]


@pytest.mark.posix_only(reason="peer uid is the POSIX proof; Windows HMAC rejection is covered in test_platform_ipc")
@pytest.mark.asyncio
async def test_server_rejects_a_peer_uid_mismatch(
    broker, monkeypatch: pytest.MonkeyPatch,
):
    _server, client = broker
    real = ipc._peer_uid
    # Accepted sockets are bound to the endpoint path; the client side is not,
    # so the client's own check still passes and only the accept side fails.
    monkeypatch.setattr(
        ipc, "_peer_uid", lambda sock: real(sock) + (1 if sock.getsockname() else 0))
    with pytest.raises(BrokerClientError) as rejected:
        await client.status()
    assert rejected.value.code == "broker_disconnected"


async def _events_until(subscription, needle: bytes, timeout: float = 10.0) -> bytes:
    output = bytearray()
    async with asyncio.timeout(timeout):
        while needle not in output:
            event = await subscription.queue.get()
            if event.kind == "exit":
                pytest.fail(f"session exited before {needle!r}: {event.data}")
            output.extend(event.data)
    return bytes(output)


@pytest.mark.asyncio
async def test_session_manager_hosts_the_tui_in_a_portable_pty(tmp_path: Path, fake_claude: Path):
    manager = broker_session_module.SessionManager(
        claude_binary=str(fake_claude), max_sessions=2, history_bytes=64 * 1024)
    try:
        session = await manager.create(cwd=str(tmp_path), args=[])
        metadata = session.metadata()
        assert metadata["pid"] == session.terminal.pid > 0
        assert metadata["start_ticks"] > 0
        subscription, snapshot, returncode = session.subscribe(owner="test", want_keyboard=True)
        assert returncode is None
        output = b"".join(snapshot)
        if b"READY" not in output:
            output += await _events_until(subscription, b"READY")
        assert _args(output)[:2] == ["--session-id", session.id]
        await session.write_terminal_input(subscription, "héllo".encode())
        await _events_until(subscription, b"OUT<h\\xe9llo>")
        session.resize(30, 100)
        await session.stop()
        assert session.running is False
        assert session.returncode is not None
    finally:
        await manager.close()


@pytest.mark.windows_only(reason="the explicit claude-remote launcher is a .cmd file on Windows (P5)")
def test_resolve_executable_refuses_the_claude_remote_launcher(tmp_path: Path):
    launcher = tmp_path / "claude-remote.cmd"
    launcher.write_text("@echo off\r\n", encoding="utf-8")
    with pytest.raises(broker_session_module.SessionError, match="refusing to launch claude-remote"):
        broker_session_module.resolve_executable(str(launcher))


class _StuckWriteTerminal:
    """A PTY whose write, like a Windows ConPTY worker thread, outlasts cancellation."""

    pid = 4242
    start_ticks = 1

    def __init__(self) -> None:
        self.writing = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.exited = asyncio.Event()

    def start(self, on_output) -> None:
        pass

    def stop_reading(self) -> None:
        pass

    async def wait(self) -> int:
        await self.exited.wait()
        return 0

    def drain(self) -> list[bytes]:
        return []

    async def write(self, data: bytes) -> None:
        self.writing.set()
        cancelled = False
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_close_does_not_wait_past_the_bound_for_a_stuck_pty_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    _set_home(monkeypatch, tmp_path)
    monkeypatch.setattr(broker_session_module, "WRITER_STOP_TIMEOUT_SECONDS", 0.2)
    terminal = _StuckWriteTerminal()
    session = PTYSession(
        session_id=str(uuid.uuid4()), terminal=terminal, cwd=str(tmp_path), kind="new",
        resume_id=None, history_limit=1024, generation="g", on_change=lambda: 0,
        on_control_change=None, model=None, effort=None, permission_mode="default",
        bypass_allowed=False, auto_compact=None,
    )
    session.start()
    sending = asyncio.create_task(session._enqueue_input(b"x"))
    await asyncio.wait_for(terminal.writing.wait(), 5)
    writer = session._writer_task
    started = asyncio.get_running_loop().time()
    with caplog.at_level("WARNING", logger=broker_session_module.__name__):
        await asyncio.wait_for(session.close(stop=False), 5)
    assert asyncio.get_running_loop().time() - started < 1.0
    assert terminal.closed is True
    assert writer is not None and not writer.done()
    assert "did not stop" in caplog.text

    terminal.release.set()
    terminal.exited.set()
    sending.cancel()
    await asyncio.gather(writer, sending, session._wait_task, return_exceptions=True)
    assert writer.cancelled()


class _ExitTerminal(_StuckWriteTerminal):
    """A PTY whose exit wait or group kill fails, like a broken ConPTY poll."""

    def __init__(self, *, wait_error: OSError | None = None,
                 kill_error: OSError | None = None) -> None:
        super().__init__()
        self.wait_error = wait_error
        self.kill_error = kill_error

    async def wait(self) -> int:
        if self.wait_error is not None:
            raise self.wait_error
        return await super().wait()

    def kill_group(self, sig: int) -> None:
        if self.kill_error is not None:
            raise self.kill_error


def _fake_session(terminal, cwd: Path) -> PTYSession:
    return PTYSession(
        session_id=str(uuid.uuid4()), terminal=terminal, cwd=str(cwd), kind="new",
        resume_id=None, history_limit=1024, generation="g", on_change=lambda: 0,
        on_control_change=None, model=None, effort=None, permission_mode="default",
        bypass_allowed=False, auto_compact=None,
    )


@pytest.mark.asyncio
async def test_a_failed_exit_wait_ends_the_session_instead_of_leaving_it_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    _set_home(monkeypatch, tmp_path)
    terminal = _ExitTerminal(wait_error=OSError("ConPTY poll failed: broken handle"))
    session = _fake_session(terminal, tmp_path)
    subscription, _history, _returncode = session.subscribe(owner="mirror", want_keyboard=False)
    assert subscription is not None
    with caplog.at_level("WARNING", logger=broker_session_module.__name__):
        session.start()
        await asyncio.wait_for(asyncio.shield(session._wait_task), 5)
    assert session.running is False
    assert session.returncode == 1  # the real status is unknown
    assert "ConPTY poll failed" in caplog.text
    events = []
    while not subscription.queue.empty():
        events.append(subscription.queue.get_nowait())
    assert [event.kind for event in events] == ["exit"]
    await asyncio.wait_for(session.close(stop=True), 5)
    assert terminal.closed is True


@pytest.mark.asyncio
async def test_close_finishes_teardown_when_stopping_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    _set_home(monkeypatch, tmp_path)
    terminal = _ExitTerminal(kill_error=PermissionError(1, "Operation not permitted"))
    session = _fake_session(terminal, tmp_path)
    session.start()
    writer = session._writer_task
    with pytest.raises(PermissionError):
        await asyncio.wait_for(session.close(stop=True), 5)
    assert terminal.closed is True
    assert writer is not None and writer.cancelled()
    terminal.exited.set()
    await asyncio.wait_for(session._wait_task, 5)


def test_control_store_round_trips_privately_and_rejects_a_shared_file(tmp_path: Path):
    directory = tmp_path / "broker"
    directory.mkdir()
    perms.make_private(directory, directory=True)
    path = directory / "session-controls.json"
    sid = str(uuid.uuid4())
    store = broker_session_module.ControlStore(str(path))
    assert store.update(sid, model="claude-fable-5") == {"model": "claude-fable-5"}
    assert perms.is_private(path)
    assert broker_session_module.ControlStore(str(path)).get(sid) == {"model": "claude-fable-5"}

    share_with_everyone(path)
    with pytest.raises(broker_session_module.ControlStoreError, match="not a private"):
        broker_session_module.ControlStore(str(path)).load()


@pytest.mark.asyncio
async def test_cli_keyboard_attachment_forwards_piped_stdin(monkeypatch: pytest.MonkeyPatch):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"typed")
    os.close(write_fd)
    received = bytearray()
    typed = asyncio.Event()

    class Attachment:
        keyboard = True

        def __init__(self):
            self.closed = False

        async def read(self):
            await typed.wait()
            return FrameType.EXIT, {"returncode": 5}

        async def write(self, data):
            received.extend(data)
            if bytes(received) == b"typed":
                typed.set()

        async def resize(self, *_args):
            return None

        async def close(self):
            self.closed = True

    attachment = Attachment()

    class Client:
        async def attach(self, _session_id, *, keyboard=True):
            assert keyboard is True
            return attachment

    class Stream:
        def __init__(self, fd):
            self.fd = fd

        def fileno(self):
            return self.fd

    monkeypatch.setattr(broker_cli, "_write_all", lambda _fd, _data: None)
    monkeypatch.setattr(broker_cli.sys, "stdin", Stream(read_fd))
    monkeypatch.setattr(broker_cli.sys, "stdout", Stream(1))
    try:
        result = await broker_cli._interactive_attach(
            Client(), "11111111-1111-4111-8111-111111111111", keyboard=True)
    finally:
        os.close(read_fd)
    assert result == 5
    assert bytes(received) == b"typed"
    assert attachment.closed is True


class _FakeBrokerProcess:
    def __init__(self, returncode: int | None = None):
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


class _UnavailableBroker:
    """A client whose broker is never reachable."""

    async def status(self):
        raise BrokerClientError("broker_unavailable", "not running")


@pytest.mark.asyncio
async def test_ensure_broker_starts_one_detached_private_broker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    started = []

    def popen_group(args, **kwargs):
        started.append((args, kwargs))
        return _FakeBrokerProcess()

    monkeypatch.setattr(broker_cli.procs, "popen_group", popen_group)

    class Client:
        attempts = 0

        async def status(self):
            Client.attempts += 1
            if Client.attempts == 1:
                raise BrokerClientError("broker_unavailable", "not running")
            return {"ok": True}

    socket_path = tmp_path / "broker" / "claude-broker.sock"
    await broker_cli._ensure_broker(Client(), str(socket_path))
    assert perms.is_private(socket_path.parent)
    assert len(started) == 1
    args, kwargs = started[0]
    assert args[1:] == ["-m", "cc_remote.claude_remote", "--socket", str(socket_path), "serve"]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["creationflags"] == broker_cli.pty.DETACHED_CREATIONFLAGS
    assert (socket_path.parent / "claude-broker.log").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["exit", "detach", "error", "cancel"])
async def test_cli_attach_closes_its_one_input_reader_before_leaving_raw_mode(
    monkeypatch: pytest.MonkeyPatch, ending: str,
):
    events: list[str] = []
    remote_done = asyncio.Event()
    reading = asyncio.Event()

    class RawTerminal:
        def __init__(self, _stdin_fd, _stdout_fd, *, keyboard):
            assert keyboard is True

        def __enter__(self):
            events.append("raw")
            return self

        def __exit__(self, *_exc):
            events.append("restored")

    class InputReader:
        def __init__(self, _fd):
            events.append("reader")

        async def read(self):
            reading.set()
            if ending == "detach":
                return b""
            await asyncio.Future()

        def close(self):
            events.append("reader-closed")

    class Attachment:
        keyboard = True

        async def read(self):
            await reading.wait()
            if ending == "exit":
                return FrameType.EXIT, {"returncode": 3}
            if ending == "error":
                return FrameType.ERROR, {"error": {"code": "boom", "message": "lost"}}
            await remote_done.wait()

        async def write(self, _data):
            return None

        async def resize(self, *_args):
            return None

        async def close(self):
            events.append("detached")

    class Client:
        async def attach(self, _session_id, *, keyboard=True):
            return Attachment()

    class Stream:
        def __init__(self, fd):
            self.fd = fd

        def fileno(self):
            return self.fd

    monkeypatch.setattr(broker_cli.pty, "RawTerminal", RawTerminal)
    monkeypatch.setattr(broker_cli.pty, "InputReader", InputReader)
    monkeypatch.setattr(broker_cli.os, "isatty", lambda _fd: False)
    monkeypatch.setattr(broker_cli.sys, "stdin", Stream(0))
    monkeypatch.setattr(broker_cli.sys, "stdout", Stream(1))
    attach = asyncio.create_task(broker_cli._interactive_attach(
        Client(), "11111111-1111-4111-8111-111111111111", keyboard=True))
    if ending == "exit":
        assert await attach == 3
    elif ending == "detach":
        assert await attach == 0
    elif ending == "error":
        with pytest.raises(BrokerClientError, match="lost"):
            await attach
    else:
        await reading.wait()
        attach.cancel()
        with pytest.raises(asyncio.CancelledError):
            await attach
    assert events == ["reader", "raw", "reader-closed", "restored", "detached"]


@pytest.mark.asyncio
async def test_ensure_broker_waits_for_a_slow_cold_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    # A cold Windows start (venv launcher, pywin32 imports, a Defender scan)
    # can take several seconds before the endpoint answers.
    monkeypatch.setattr(
        broker_cli.procs, "popen_group", lambda *_args, **_kwargs: _FakeBrokerProcess())
    loop = asyncio.get_running_loop()
    ready_at = loop.time() + 3.5

    class Client:
        async def status(self):
            if loop.time() < ready_at:
                raise BrokerClientError("broker_unavailable", "starting")
            return {"ok": True}

    await broker_cli._ensure_broker(Client(), str(tmp_path / "broker" / "claude-broker.sock"))
    assert loop.time() >= ready_at


@pytest.mark.asyncio
async def test_ensure_broker_stops_waiting_once_the_broker_exited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    monkeypatch.setattr(
        broker_cli.procs, "popen_group", lambda *_args, **_kwargs: _FakeBrokerProcess(1))
    socket_path = tmp_path / "broker" / "claude-broker.sock"
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(BrokerClientError) as error:
        await broker_cli._ensure_broker(_UnavailableBroker(), str(socket_path))
    assert loop.time() - started < 2.0
    assert error.value.code == "broker_start_failed"
    assert "exited" in str(error.value)
    assert str(socket_path.parent / "claude-broker.log") in str(error.value)


@pytest.mark.asyncio
async def test_ensure_broker_leaves_the_launching_job_when_it_may(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    flags = []

    def popen_group(_args, **kwargs):
        flags.append(kwargs["creationflags"])
        return _FakeBrokerProcess()

    class Client:
        attempts = 0

        async def status(self):
            Client.attempts += 1
            if Client.attempts == 1:
                raise BrokerClientError("broker_unavailable", "not running")
            return {"ok": True}

    monkeypatch.setattr(broker_cli.procs, "popen_group", popen_group)
    await broker_cli._ensure_broker(Client(), str(tmp_path / "broker" / "claude-broker.sock"))
    assert flags == [broker_cli.pty.DETACHED_CREATIONFLAGS]
    if IS_WINDOWS:
        # A kill-on-close Job of the launching terminal must not end the broker.
        assert flags[0] & subprocess.CREATE_BREAKAWAY_FROM_JOB


@pytest.mark.windows_only(reason="Job breakaway is a Windows concept")
@pytest.mark.asyncio
async def test_ensure_broker_stays_in_a_job_that_forbids_breakaway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    flags = []

    def popen_group(_args, **kwargs):
        flags.append(kwargs["creationflags"])
        if kwargs["creationflags"] & subprocess.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError(13, "Access is denied")  # no JOB_OBJECT_LIMIT_BREAKAWAY_OK
        return _FakeBrokerProcess()

    class Client:
        attempts = 0

        async def status(self):
            Client.attempts += 1
            if Client.attempts == 1:
                raise BrokerClientError("broker_unavailable", "not running")
            return {"ok": True}

    monkeypatch.setattr(broker_cli.procs, "popen_group", popen_group)
    await broker_cli._ensure_broker(Client(), str(tmp_path / "broker" / "claude-broker.sock"))
    assert flags == [
        broker_cli.pty.DETACHED_CREATIONFLAGS,
        broker_cli.pty.DETACHED_CREATIONFLAGS & ~subprocess.CREATE_BREAKAWAY_FROM_JOB,
    ]


@pytest.mark.asyncio
async def test_ensure_broker_reports_why_its_directory_cannot_be_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    monkeypatch.setattr(
        broker_cli.procs, "popen_group",
        lambda *_args, **_kwargs: pytest.fail("nothing may start without a directory"))
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    with pytest.raises(BrokerClientError) as error:
        await broker_cli._ensure_broker(
            _UnavailableBroker(), str(blocker / "broker" / "claude-broker.sock"))
    assert error.value.code == "broker_start_failed"
    assert "private" not in str(error.value)
    assert str(blocker / "broker") in str(error.value)


@pytest.mark.asyncio
async def test_serve_runs_without_shutdown_signals_where_the_loop_has_none(
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[str] = []

    class Server:
        socket_path = "fake.sock"

        def __init__(self, _config):
            pass

        async def start(self):
            events.append("start")

        async def close(self):
            events.append("close")

    def unsupported(_loop, _callback):
        raise NotImplementedError  # e.g. a loop that has no add_signal_handler

    monkeypatch.setattr(broker_cli, "BrokerServer", Server)
    monkeypatch.setattr(broker_cli.signals, "install_shutdown_handler", unsupported)
    args = _parser().parse_args(["serve"])
    serve = asyncio.create_task(broker_cli._serve(args))
    await asyncio.sleep(0.05)
    assert not serve.done(), "serve must keep running without signal handlers"
    serve.cancel()
    with pytest.raises(asyncio.CancelledError):
        await serve
    assert events == ["start", "close"]


@pytest.mark.asyncio
@pytest.mark.parametrize("keyboard", [True, False], ids=["keyboard", "mirror"])
async def test_cli_attach_detaches_cleanly_on_a_console_break(
    monkeypatch: pytest.MonkeyPatch, keyboard: bool,
):
    # Raw console input cannot stop Ctrl+Break on Windows; without a handler it
    # kills claude-remote and leaves the user's console raw.
    events: list[str] = []
    breaks: list[Callable[[], None]] = []

    class RawTerminal:
        def __init__(self, _stdin_fd, _stdout_fd, *, keyboard):
            pass

        def __enter__(self):
            events.append("raw")
            return self

        def __exit__(self, *_exc):
            events.append("restored")

    class InputReader:
        def __init__(self, _fd):
            pass

        async def read(self):
            await asyncio.Future()

        def close(self):
            events.append("reader-closed")

    class Attachment:
        def __init__(self, keyboard):
            self.keyboard = keyboard

        async def read(self):
            await asyncio.Future()

        async def write(self, _data):
            return None

        async def resize(self, *_args):
            return None

        async def close(self):
            events.append("detached")

    class Client:
        async def attach(self, _session_id, *, keyboard=True):
            return Attachment(keyboard)

    class Stream:
        def __init__(self, fd):
            self.fd = fd

        def fileno(self):
            return self.fd

    def watch_break(callback):
        events.append("guarded")
        breaks.append(callback)
        return lambda: events.append("unguarded")

    monkeypatch.setattr(broker_cli.pty, "RawTerminal", RawTerminal)
    monkeypatch.setattr(broker_cli.pty, "InputReader", InputReader)
    monkeypatch.setattr(broker_cli.pty, "watch_break", watch_break, raising=False)
    monkeypatch.setattr(broker_cli.os, "isatty", lambda _fd: False)
    monkeypatch.setattr(broker_cli.sys, "stdin", Stream(0))
    monkeypatch.setattr(broker_cli.sys, "stdout", Stream(1))
    attach = asyncio.create_task(broker_cli._interactive_attach(
        Client(), "11111111-1111-4111-8111-111111111111", keyboard=keyboard))
    async with asyncio.timeout(5):
        while not breaks:
            await asyncio.sleep(0.01)
    breaks[0]()  # what the platform handler runs on the loop for Ctrl+Break
    assert await asyncio.wait_for(attach, 5) == 130
    expected = ["guarded", "raw", "restored", "unguarded", "detached"]
    if keyboard:
        expected.insert(2, "reader-closed")
    assert events == expected
