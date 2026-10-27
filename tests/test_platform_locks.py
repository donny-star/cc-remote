from __future__ import annotations

import os
import threading
import time

import pytest

from cc_remote.platform import locks


def _open(path) -> int:
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o600)


def test_nonblocking_contention_raises_blocking_io_error(tmp_path) -> None:
    a, b = _open(tmp_path / "l"), _open(tmp_path / "l")
    try:
        locks.flock(a, locks.LOCK_EX)
        with pytest.raises(BlockingIOError):
            locks.flock(b, locks.LOCK_EX | locks.LOCK_NB)
    finally:
        os.close(a)
        os.close(b)


def test_blocking_lock_waits_for_release(tmp_path) -> None:
    a, b = _open(tmp_path / "l"), _open(tmp_path / "l")
    try:
        locks.flock(a, locks.LOCK_EX)
        threading.Timer(0.3, locks.flock, (a, locks.LOCK_UN)).start()
        started = time.monotonic()
        locks.flock(b, locks.LOCK_EX)
        assert time.monotonic() - started >= 0.2
    finally:
        os.close(a)
        os.close(b)


def test_close_releases_lock(tmp_path) -> None:
    a = _open(tmp_path / "l")
    locks.flock(a, locks.LOCK_EX)
    os.close(a)
    b = _open(tmp_path / "l")
    try:
        locks.flock(b, locks.LOCK_EX | locks.LOCK_NB)
    finally:
        os.close(b)


def test_lock_does_not_block_reading_file_content(tmp_path) -> None:
    path = tmp_path / "data"
    path.write_bytes(b"payload")
    a, b = _open(path), _open(path)
    try:
        locks.flock(a, locks.LOCK_EX)
        assert os.read(b, 16) == b"payload"
    finally:
        os.close(a)
        os.close(b)


def test_unlock_without_lock_is_noop(tmp_path) -> None:
    a = _open(tmp_path / "l")
    try:
        locks.flock(a, locks.LOCK_UN)
    finally:
        os.close(a)
