"""Run the SDK service under its own LaunchAgent, systemd user unit or scheduled task."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from cc_remote.platform import files, ipc, locks, signals
from cc_remote.wrapper.process_scan import process_identity

from .server import Service
from .wire import private_directory


async def serve(directory: Path, *, stop: asyncio.Event | None = None) -> None:
    private_directory(directory)
    lock_fd = files.open_nofollow(directory / "service.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        locks.flock(lock_fd, locks.LOCK_EX | locks.LOCK_NB)
        path = directory / "service.sock"
        service = Service(directory)
        server = await ipc.start_server(path, service.connection)
        identity = process_identity(os.getpid())
        state = {
            "pid": os.getpid(), "start_ticks": identity.start_ticks if identity else None,
            "source": str(Path(__file__).resolve().parents[2]),
            "socket": str(path), "protocol": 1,
        }
        descriptor = directory / "service.json"
        fd = files.open_nofollow(descriptor, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump(state, out)
        stopped = stop if stop is not None else asyncio.Event()
        uninstall = signals.install_shutdown_handler(asyncio.get_running_loop(), stopped.set)
        try:
            await stopped.wait()
        finally:
            uninstall()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*(session.close() for session in service.sessions.values()))
            descriptor.unlink(missing_ok=True)
    finally:
        os.close(lock_fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(serve(arguments.state_dir.expanduser().absolute()))


if __name__ == "__main__":
    main()
