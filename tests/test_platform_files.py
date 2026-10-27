from __future__ import annotations

import os
import stat
import threading
import time

import pytest

from cc_remote.platform import files


def test_share_delete_descriptor_allows_unlink_and_replace(tmp_path) -> None:
    target = tmp_path / "marker"
    target.write_bytes(b"old")
    fd = files.open_with_share_delete(target, os.O_RDWR)
    try:
        replacement = tmp_path / "new"
        replacement.write_bytes(b"new")
        files.replace(replacement, target)
        assert target.read_bytes() == b"new"
        assert not replacement.exists()
        assert files.pread(fd, 16, 0) == b"old"  # open handle keeps the old inode
        target.unlink()
        assert not target.exists()
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    ("flags", "exists", "expected"),
    [
        (os.O_RDWR | os.O_CREAT, False, b""),
        (os.O_RDWR | os.O_CREAT, True, b"keep"),
        (os.O_RDWR | os.O_CREAT | os.O_TRUNC, True, b""),
        (os.O_RDWR | os.O_TRUNC, True, b""),
    ],
)
def test_share_delete_open_follows_posix_dispositions(tmp_path, flags, exists, expected) -> None:
    path = tmp_path / "f"
    if exists:
        path.write_bytes(b"keep")
    os.close(files.open_with_share_delete(path, flags))
    assert path.read_bytes() == expected


def test_share_delete_exclusive_create_rejects_existing(tmp_path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x")
    with pytest.raises(FileExistsError):
        files.open_with_share_delete(path, os.O_RDWR | os.O_CREAT | os.O_EXCL)


def test_share_delete_missing_file_raises_file_not_found(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        files.open_with_share_delete(tmp_path / "missing", os.O_RDONLY)


@pytest.mark.windows_only(reason="transient sharing violations only exist on Windows")
def test_replace_retries_while_destination_is_briefly_held(tmp_path) -> None:
    dst = tmp_path / "dst"
    dst.write_bytes(b"old")
    src = tmp_path / "src"
    src.write_bytes(b"new")
    holder = open(dst, "rb")  # builtin open: no FILE_SHARE_DELETE
    threading.Timer(0.2, holder.close).start()
    files.replace(src, dst)
    assert dst.read_bytes() == b"new"


@pytest.mark.windows_only(reason="exercises the Windows retry loop")
def test_replace_raises_permanent_access_denied_without_backoff(tmp_path) -> None:
    dst = tmp_path / "dst"
    dst.mkdir()  # renaming a file over a directory is ERROR_ACCESS_DENIED, not a sharing violation
    src = tmp_path / "src"
    src.write_bytes(b"new")
    started = time.monotonic()
    with pytest.raises(PermissionError) as excinfo:
        files.replace(src, dst)
    assert excinfo.value.winerror == 5
    assert time.monotonic() - started < 0.5
    assert src.read_bytes() == b"new"


@pytest.mark.parametrize("attempts", [0, -1])
def test_replace_rejects_non_positive_attempts(tmp_path, attempts) -> None:
    src = tmp_path / "src"
    src.write_bytes(b"new")
    with pytest.raises(ValueError):
        files.replace(src, tmp_path / "dst", attempts=attempts)
    assert src.read_bytes() == b"new"
    assert not (tmp_path / "dst").exists()


def test_pread_pwrite_keep_file_position(tmp_path) -> None:
    fd = os.open(tmp_path / "f", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.write(fd, b"0123456789")
        files.pwrite(fd, b"AB", 2)
        assert files.pread(fd, 4, 1) == b"1AB4"
        assert os.lseek(fd, 0, os.SEEK_CUR) == 10
    finally:
        os.close(fd)


def test_force_rmtree_removes_readonly_files(tmp_path) -> None:
    tree = tmp_path / "tree" / "objects"
    tree.mkdir(parents=True)
    loose = tree / "pack"
    loose.write_bytes(b"x")
    os.chmod(loose, stat.S_IREAD)
    files.force_rmtree(tmp_path / "tree")
    assert not (tmp_path / "tree").exists()


def test_fsync_directory_accepts_existing_directory(tmp_path) -> None:
    files.fsync_directory(tmp_path)
