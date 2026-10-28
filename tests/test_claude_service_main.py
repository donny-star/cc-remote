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
