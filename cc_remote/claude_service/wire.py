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


class ControllerLeaseConflict(RuntimeError):
    """An existing native worker still belongs to another controller."""


async def read_frame(reader: asyncio.StreamReader) -> dict:
    size = struct.unpack("!I", await reader.readexactly(4))[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError("invalid Claude service frame size")
    value = json.loads(await reader.readexactly(size))
    if not isinstance(value, dict) or value.get("v") != VERSION:
        raise ValueError("incompatible Claude service protocol")
    return value


async def write_frame(writer: asyncio.StreamWriter, value: dict) -> None:
    data = json.dumps({**value, "v": VERSION}, ensure_ascii=False).encode()
    if len(data) > MAX_FRAME:
        raise ValueError("Claude service frame too large")
    writer.write(struct.pack("!I", len(data)) + data)
    await writer.drain()


def private_directory(path: Path) -> None:
    try:
        ipc.ensure_private_directory(path)
    except ipc.UnsafeEndpointError as exc:
        raise PermissionError(
            "Claude service directory must be private and owned by this user") from exc


def encode_sdk(value):
    """Only SDK dataclasses cross this boundary; never pickle executable data."""
    if dataclasses.is_dataclass(value):
        return {"sdk_type": type(value).__name__, "fields": {
            field.name: encode_sdk(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }}
    if isinstance(value, dict):
        return {key: encode_sdk(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode_sdk(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported SDK value: {type(value).__name__}")


def decode_sdk(value):
    if isinstance(value, list):
        return [decode_sdk(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"sdk_type", "fields"}:
        from claude_agent_sdk import types

        cls = getattr(types, value["sdk_type"], None)
        if not isinstance(cls, type) or not dataclasses.is_dataclass(cls):
            raise ValueError("unknown SDK data type")
        return cls(**{key: decode_sdk(item) for key, item in value["fields"].items()})
    return {key: decode_sdk(item) for key, item in value.items()}
