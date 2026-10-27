"""Graceful-shutdown hooks: SIGTERM/SIGINT on POSIX, Ctrl+C/Break/close on Windows."""
from __future__ import annotations

import asyncio
import signal
import threading
from collections.abc import Callable

from cc_remote.log import logger
from cc_remote.platform import IS_WINDOWS

log = logger("cc_remote.platform.signals")

# Windows terminates the process roughly 5 s after CTRL_CLOSE; stay under it.
_CONSOLE_CALLBACK_TIMEOUT = 4.5


def install_shutdown_handler(
    loop: asyncio.AbstractEventLoop, callback: Callable[[], None],
) -> Callable[[], None]:
    """Run ``callback`` on ``loop`` when the process is asked to shut down.

    Returns an ``uninstall()`` callable that removes every hook installed here.

    On Windows, console close/logoff/shutdown events arrive on a separate OS
    thread and the process is terminated once that handler returns. The handler
    therefore blocks (up to 4.5 s) until ``callback`` itself has run on the
    loop. Only the callback is guaranteed to finish; any asynchronous work it
    schedules may still be cut short.
    """
    if not IS_WINDOWS:
        posix_signals = (signal.SIGTERM, signal.SIGINT)
        for signum in posix_signals:
            loop.add_signal_handler(signum, callback)

        def uninstall_posix() -> None:
            for signum in posix_signals:
                loop.remove_signal_handler(signum)

        return uninstall_posix
    import pywintypes
    import win32api

    def on_signal(_signum: int, _frame: object) -> None:
        loop.call_soon_threadsafe(callback)

    windows_signals = (signal.SIGINT, signal.SIGBREAK)
    previous = {signum: signal.getsignal(signum) for signum in windows_signals}
    for signum in windows_signals:
        signal.signal(signum, on_signal)

    def on_console_event(event: int) -> bool:
        if event not in (2, 5, 6):  # CTRL_CLOSE, CTRL_LOGOFF, CTRL_SHUTDOWN
            return False  # let SIGINT/SIGBREAK handlers see Ctrl+C / Ctrl+Break
        done = threading.Event()

        def run_callback() -> None:
            try:
                callback()
            finally:
                done.set()

        try:
            loop.call_soon_threadsafe(run_callback)
        except RuntimeError:  # loop already closed: nothing left to run
            return True
        done.wait(_CONSOLE_CALLBACK_TIMEOUT)
        return True

    console_registered = False
    try:
        win32api.SetConsoleCtrlHandler(on_console_event, True)
        console_registered = True
    except pywintypes.error as exc:  # e.g. pythonw: no console attached
        log.warning("console control handler unavailable", error=str(exc))

    def uninstall_windows() -> None:
        if console_registered:
            win32api.SetConsoleCtrlHandler(on_console_event, False)
        for signum, handler in previous.items():
            if handler is not None:  # None: installed outside Python, can't restore
                signal.signal(signum, handler)

    return uninstall_windows
