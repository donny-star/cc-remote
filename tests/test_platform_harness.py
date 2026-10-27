"""Self-checks for the cross-platform test harness and package import side effects."""
from __future__ import annotations

import importlib
import os
import pkgutil
import sys
import traceback

import pytest

import cc_remote
from cc_remote import platform as cc_platform


def test_is_windows_matches_interpreter() -> None:
    assert cc_platform.IS_WINDOWS is (sys.platform == "win32")


@pytest.mark.posix_only(reason="harness self-check: must not run on Windows")
def test_posix_only_marker_skips_on_windows() -> None:
    assert sys.platform != "win32"


@pytest.mark.windows_only(reason="harness self-check: must not run on POSIX")
def test_windows_only_marker_skips_on_posix() -> None:
    assert sys.platform == "win32"


def test_os_open_is_binary_after_importing_package(tmp_path) -> None:
    assert cc_remote.__version__
    path = tmp_path / "raw"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.write(fd, b"a\nb\x1ac")
    finally:
        os.close(fd)
    assert path.read_bytes() == b"a\nb\x1ac"
    fd = os.open(path, os.O_RDONLY)
    try:
        assert os.read(fd, 16) == b"a\nb\x1ac"
    finally:
        os.close(fd)


# Modules owned by later sub-projects that still import POSIX-only APIs.
_PENDING_PORTS = {
    "cc_remote.claude_broker.cli",      # termios/tty: sub-project 3
    "cc_remote.claude_broker.session",  # pty/termios: sub-project 3
    # Import the broker client/handle (-> session.py): unblocked by sub-project 3.
    "cc_remote.claude_remote",               # sub-project 3
    "cc_remote.wrapper.claude_broker_handle",  # sub-project 3
    "cc_remote.wrapper.machine",             # sub-project 2, after sub-project 3
}


def _raised_by_pending_port(exc: BaseException) -> bool:
    return any(
        frame.f_globals.get("__name__") in _PENDING_PORTS
        for frame, _lineno in traceback.walk_tb(exc.__traceback__)
    )


def test_every_module_imports_on_this_platform() -> None:
    failures = {}
    for module in pkgutil.walk_packages(cc_remote.__path__, "cc_remote."):
        name = module.name
        if name in _PENDING_PORTS or name.endswith("__main__"):
            continue
        if name == "cc_remote.platform.win32_process" and not cc_platform.IS_WINDOWS:
            continue  # Windows-only backend: binds ctypes.WinDLL at import
        loaded = set(sys.modules)
        try:
            importlib.import_module(name)
        except Exception as exc:
            # Drop submodules a failed import left behind, or a later
            # ``from pkg.sub import x`` skips the failing package __init__.
            for partial in set(sys.modules) - loaded:
                if partial.startswith("cc_remote."):
                    del sys.modules[partial]
            if name.startswith("cc_remote.claude_broker.") and _raised_by_pending_port(exc):
                continue  # broker internals that import the pending PTY session
            failures[name] = repr(exc)
    assert failures == {}
