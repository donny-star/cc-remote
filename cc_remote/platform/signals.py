"""Graceful-shutdown hooks: SIGTERM/SIGINT on POSIX, Ctrl+C/Break/close on Windows."""
from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable

from cc_remote.platform import IS_WINDOWS


def install_shutdown_handler(
    loop: asyncio.AbstractEventLoop, callback: Callable[[], None],
) -> None:
    if not IS_WINDOWS:
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, callback)
        return
    import win32api

    def on_signal(_signum: int, _frame: object) -> None:
        loop.call_soon_threadsafe(callback)

    for signum in (signal.SIGINT, signal.SIGBREAK):
        signal.signal(signum, on_signal)

    def on_console_event(event: int) -> bool:
        if event in (2, 5, 6):  # CTRL_CLOSE, CTRL_LOGOFF, CTRL_SHUTDOWN
            loop.call_soon_threadsafe(callback)
            return True
        return False  # let SIGINT/SIGBREAK handlers see Ctrl+C / Ctrl+Break

    try:
        win32api.SetConsoleCtrlHandler(on_console_event, True)
    except Exception:  # pythonw: no console attached, nothing to hook
        pass
