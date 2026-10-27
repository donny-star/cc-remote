"""Self-checks for the cross-platform test harness and package import side effects."""
from __future__ import annotations

import importlib
import os
import pkgutil
import sys

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


# Modules owned by later sub-projects that still fail to import on this
# platform (name -> owning sub-project in a comment). The PTY broker guards its
# POSIX-only imports, so nothing is pending today.
_PENDING_PORTS: set[str] = set()


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
            failures[name] = repr(exc)
    assert failures == {}
