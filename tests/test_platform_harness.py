"""Self-checks for the cross-platform test harness and package import side effects."""
from __future__ import annotations

import os
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
