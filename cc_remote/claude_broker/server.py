"""Same-user local endpoint server for persistent Claude Code PTYs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any
import uuid

from cc_remote.platform import ipc

from .paths import default_socket_path
from .protocol import (
    BROKER_PROTOCOL_VERSION,
    FrameType,
    MAX_CONTROL_BYTES,
    MAX_FRAME_BYTES,
    ProtocolError,
    decode_json,
    read_frame,
    write_frame,
    write_json,
)
from .session import (
    DEFAULT_HISTORY_BYTES,
    DEFAULT_MAX_SESSIONS,
    SessionError,
    SessionManager,
)


MAX_SOCKET_PATH_BYTES = 103
MAX_CLIENTS = 64
CLOSE_TIMEOUT_SECONDS = 3.0


class BrokerSecurityError(RuntimeError):
    """The local socket cannot be created or authenticated safely."""


@dataclass(frozen=True)
class BrokerConfig:
    socket_path: str = field(default_factory=default_socket_path)
    claude_binary: str = field(
        default_factory=lambda: os.environ.get("CLAUDE_REMOTE_CLAUDE_BIN", "claude")
    )
    max_sessions: int = DEFAULT_MAX_SESSIONS
    history_bytes: int = DEFAULT_HISTORY_BYTES
    control_store_path: str | None = None


def _prepare_parent(path: str) -> Path:
    if not path or "\x00" in path:
        raise BrokerSecurityError("broker socket path is invalid")
    encoded = os.fsencode(path)
    if len(encoded) > MAX_SOCKET_PATH_BYTES:
        raise BrokerSecurityError(
            f"broker socket path exceeds {MAX_SOCKET_PATH_BYTES} bytes",
        )
    target = Path(path).expanduser().absolute()
    try:
        ipc.ensure_private_directory(target.parent)
    except ipc.UnsafeEndpointError as exc:
        raise BrokerSecurityError(f"broker socket {exc}") from exc
    return target


class BrokerServer:
    """Owns Claude PTYs and exposes only an authenticated same-user local endpoint."""

    def __init__(self, config: BrokerConfig | None = None):
        self.config = config or BrokerConfig()
        self.manager = SessionManager(
            claude_binary=self.config.claude_binary,
            max_sessions=self.config.max_sessions,
            history_bytes=self.config.history_bytes,
            control_store_path=(
                self.config.control_store_path
                or str(Path(self.config.socket_path).with_name("session-controls.json"))
            ),
        )
        self._server: ipc.IpcServer | None = None
        self._connections: set[asyncio.StreamWriter] = set()

    @property
    def socket_path(self) -> str:
        return self.config.socket_path

    async def start(self) -> None:
        if self._server is not None:
            return
        target = _prepare_parent(self.config.socket_path)
        self.manager.load_control_store()
        try:
            self._server = await ipc.start_server(target, self._handle_client)
        except ipc.AlreadyRunningError as exc:
            raise BrokerSecurityError(f"broker is already running at {target}") from exc
        except ipc.UnsafeEndpointError as exc:
            raise BrokerSecurityError(str(exc)) from exc

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        await self._server.serve_forever()

    def _state(self) -> dict[str, object]:
        return {
            "broker_protocol": BROKER_PROTOCOL_VERSION,
            "generation": self.manager.generation,
            "revision": self.manager.revision,
        }

    async def _send_error(
        self, writer: asyncio.StreamWriter, code: str, message: str,
    ) -> None:
        try:
            await write_json(
                writer,
                FrameType.ERROR,
                {"ok": False, "error": {"code": code, "message": message}, **self._state()},
            )
        except (ConnectionError, BrokenPipeError, ProtocolError):
            pass

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        if len(self._connections) >= MAX_CLIENTS:
            await self._send_error(writer, "client_limit", "too many broker clients")
            writer.close()
            await writer.wait_closed()
            return
        self._connections.add(writer)
        try:
            try:
                frame_type, payload = await read_frame(reader, max_size=MAX_CONTROL_BYTES)
                if frame_type is not FrameType.COMMAND:
                    raise ProtocolError("first frame must be a command")
                request = decode_json(payload)
                await self._dispatch(reader, writer, request)
            except asyncio.IncompleteReadError:
                pass
            except ProtocolError as exc:
                await self._send_error(writer, "protocol_error", str(exc))
            except SessionError as exc:
                await self._send_error(writer, exc.code, str(exc))
            except Exception:
                await self._send_error(writer, "internal_error", "broker request failed")
        finally:
            self._connections.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, BrokenPipeError):
                pass

    async def _success(
        self, writer: asyncio.StreamWriter, **payload: object,
    ) -> None:
        await write_json(
            writer, FrameType.RESPONSE, {"ok": True, **self._state(), **payload},
        )

    async def _dispatch(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        request: dict[str, Any],
    ) -> None:
        op = request.get("op")
        if op == "status":
            session_id = request.get("session_id")
            if session_id is None:
                sessions = self.manager.list()
                await self._success(
                    writer,
                    status={
                        "socket_path": self.socket_path,
                        "session_count": len(sessions),
                        "running_count": sum(bool(item["running"]) for item in sessions),
                        "max_sessions": self.manager.max_sessions,
                    },
                )
            else:
                await self._success(
                    writer, session=self.manager.get(session_id).metadata(),
                )
            return
        if op == "list":
            await self._success(writer, sessions=self.manager.list())
            return
        if op in ("new", "resume"):
            session = await self.manager.create(
                cwd=request.get("cwd"),
                args=request.get("args"),
                resume_id=request.get("session_id") if op == "resume" else None,
            )
            await self._success(writer, session=session.metadata())
            return
        if op == "stop":
            session = self.manager.get(request.get("session_id"))
            await session.stop()
            await self._success(writer, session=session.metadata())
            return
        if op == "send":
            session = self.manager.get(request.get("session_id"))
            await session.submit_text(request.get("text"))
            await self._success(writer, session=session.metadata())
            return
        if op == "interrupt":
            session = self.manager.get(request.get("session_id"))
            await session.interrupt()
            await self._success(writer, session=session.metadata())
            return
        if op == "set_model":
            session = self.manager.get(request.get("session_id"))
            await session.set_model(request.get("model"))
            await self._success(writer, session=session.metadata())
            return
        if op == "set_effort":
            session = self.manager.get(request.get("session_id"))
            await session.set_effort(request.get("effort"))
            await self._success(writer, session=session.metadata())
            return
        if op == "set_permission_mode":
            session = self.manager.get(request.get("session_id"))
            await session.set_permission_mode(request.get("mode"))
            await self._success(writer, session=session.metadata())
            return
        if op == "get_context_usage":
            session = self.manager.get(request.get("session_id"))
            usage = await session.get_context_usage()
            await self._success(
                writer, session=session.metadata(), context_usage=usage)
            return
        if op == "set_preferences":
            preferences = self.manager.set_preferences(
                request.get("session_id"),
                model=request.get("model"),
                effort=request.get("effort"),
                permission_mode=request.get("permission_mode"),
                auto_compact=request.get("auto_compact"),
            )
            await self._success(writer, preferences=preferences)
            return
        if op == "attach":
            await self._attach(reader, writer, request)
            return
        raise SessionError("unknown_command", f"unknown broker command: {op!r}")

    async def _attach(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        request: dict[str, Any],
    ) -> None:
        session = self.manager.get(request.get("session_id"))
        want_keyboard = request.get("keyboard", True)
        if not isinstance(want_keyboard, bool):
            raise SessionError("bad_attach", "keyboard must be a boolean")
        owner = str(uuid.uuid4())
        subscription, snapshot, returncode = session.subscribe(
            owner=owner, want_keyboard=want_keyboard,
        )
        send_lock = asyncio.Lock()

        async def send_json_locked(frame_type: FrameType, value: dict[str, Any]) -> None:
            async with send_lock:
                await write_json(writer, frame_type, value)

        async def send_frame_locked(frame_type: FrameType, data: bytes) -> None:
            async with send_lock:
                await write_frame(writer, frame_type, data)

        await send_json_locked(
            FrameType.RESPONSE,
            {
                "ok": True,
                **self._state(),
                "session": session.metadata(),
                "keyboard": bool(subscription and subscription.keyboard),
            },
        )

        async def pump_output() -> None:
            for chunk in snapshot:
                await send_frame_locked(FrameType.OUTPUT, chunk)
            if returncode is not None:
                await send_json_locked(FrameType.EXIT, {"returncode": returncode})
                return
            assert subscription is not None
            while True:
                if subscription.overflowed:
                    await send_json_locked(
                        FrameType.ERROR,
                        {"ok": False, "error": {"code": "slow_client", "message": "output queue overflow"}},
                    )
                    return
                event = await subscription.queue.get()
                if event.kind == "output":
                    assert isinstance(event.data, bytes)
                    await send_frame_locked(FrameType.OUTPUT, event.data)
                else:
                    assert isinstance(event.data, int)
                    await send_json_locked(
                        FrameType.EXIT, {"returncode": event.data},
                    )
                    return

        async def receive_input() -> None:
            assert subscription is not None
            while True:
                frame_type, payload = await read_frame(reader, max_size=MAX_FRAME_BYTES)
                if frame_type is FrameType.INPUT:
                    try:
                        await session.write_terminal_input(subscription, payload)
                    except SessionError as exc:
                        await send_json_locked(
                            FrameType.ERROR,
                            {"ok": False, "error": {"code": exc.code, "message": str(exc)}},
                        )
                elif frame_type is FrameType.RESIZE:
                    size = decode_json(payload)
                    session.resize(
                        size.get("rows"), size.get("cols"),
                        size.get("xpixel", 0), size.get("ypixel", 0),
                    )
                elif frame_type is FrameType.DETACH:
                    return
                else:
                    raise ProtocolError("attached client sent an invalid frame type")

        output_task = asyncio.create_task(pump_output())
        input_task = (
            asyncio.create_task(receive_input())
            if subscription is not None else None
        )
        tasks = {output_task} | ({input_task} if input_task is not None else set())
        try:
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            session.unsubscribe(subscription)

    async def close(self) -> None:
        server = self._server
        if server is not None:
            server.close()  # withdraws the endpoint, then stops listening
            self._server = None
        for writer in tuple(self._connections):
            writer.close()
        # Since Python 3.12 Server.wait_closed() also waits for every open
        # connection, so clients are dropped first. A peer still proving
        # itself is closed by the endpoint's own handshake deadline; nothing
        # may hold the shutdown past this bound.
        closing = [writer.wait_closed() for writer in tuple(self._connections)]
        if server is not None:
            closing.append(server.wait_closed())
        try:
            await asyncio.wait_for(
                asyncio.gather(*closing, return_exceptions=True),
                CLOSE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            pass
        self._connections.clear()
        await self.manager.close()
