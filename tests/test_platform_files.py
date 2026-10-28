from __future__ import annotations

import errno
import os
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from cc_remote.platform import files, perms
from tests.platform_assertions import assert_private_file, foreign_readable_dir


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


def _link_directory(target, link) -> None:
    """A directory link every platform can create without privileges."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_is_reparse_point_regular_file_and_directory(tmp_path) -> None:
    regular = tmp_path / "regular"
    regular.write_bytes(b"x")
    assert files.is_reparse_point(regular) is False
    assert files.is_reparse_point(tmp_path) is False


def test_is_reparse_point_directory_link(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    _link_directory(target, link)
    assert files.is_reparse_point(link) is True


@pytest.mark.requires_symlink(reason="checks a file symbolic link")
def test_is_reparse_point_file_symlink(tmp_path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"x")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert files.is_reparse_point(link) is True


def test_is_reparse_point_missing_path_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        files.is_reparse_point(tmp_path / "missing")


def test_open_nofollow_reads_and_creates_regular_files(tmp_path) -> None:
    path = tmp_path / "f"
    fd = files.open_nofollow(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        os.write(fd, b"a\nb\x1ac")
    finally:
        os.close(fd)
    fd = files.open_nofollow(path, os.O_RDONLY)
    try:
        assert os.read(fd, 16) == b"a\nb\x1ac"
        assert stat.S_ISREG(os.fstat(fd).st_mode)
    finally:
        os.close(fd)


def test_open_nofollow_keeps_share_delete(tmp_path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"old")
    fd = files.open_nofollow(path, os.O_RDONLY)
    try:
        replacement = tmp_path / "new"
        replacement.write_bytes(b"new")
        files.replace(replacement, path)
        assert path.read_bytes() == b"new"
    finally:
        os.close(fd)


def test_open_nofollow_exclusive_create_rejects_existing(tmp_path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x")
    with pytest.raises(FileExistsError):
        files.open_nofollow(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)


def test_open_nofollow_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        files.open_nofollow(tmp_path / "missing", os.O_RDONLY)


@pytest.mark.requires_symlink(reason="opens through a file symbolic link")
def test_open_nofollow_refuses_file_symlink(tmp_path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"secret")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OSError) as caught:
        files.open_nofollow(link, os.O_RDONLY)
    assert caught.value.errno == errno.ELOOP


@pytest.mark.requires_symlink(reason="creates through a dangling symbolic link")
def test_open_nofollow_refuses_to_create_through_dangling_symlink(tmp_path) -> None:
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(OSError) as caught:
        files.open_nofollow(link, os.O_WRONLY | os.O_CREAT)
    assert caught.value.errno in (errno.ELOOP, errno.EEXIST)
    assert not (tmp_path / "elsewhere").exists()


def test_open_nofollow_truncates_regular_files(tmp_path) -> None:
    path = tmp_path / "f"
    for flags in (os.O_WRONLY | os.O_TRUNC, os.O_WRONLY | os.O_CREAT | os.O_TRUNC):
        path.write_bytes(b"old content")
        fd = files.open_nofollow(path, flags)
        try:
            os.write(fd, b"n")
        finally:
            os.close(fd)
        assert path.read_bytes() == b"n"


def test_open_nofollow_refuses_a_directory_link(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    _link_directory(target, link)
    # POSIX: ELOOP from O_NOFOLLOW. Windows: a directory (junction) cannot be
    # opened as a file at all (PermissionError); either way nothing is opened.
    with pytest.raises(OSError):
        files.open_nofollow(link, os.O_RDONLY)
    assert files.is_reparse_point(link) is True


@pytest.fixture
def socket_dir():
    from tests.unix_socket import short_temp_root

    with tempfile.TemporaryDirectory(prefix="cc-p2a-", dir=short_temp_root()) as root:
        yield Path(root)


@pytest.mark.windows_only(reason="AF_UNIX socket files are reparse points only on Windows")
@pytest.mark.parametrize("flags", [
    os.O_RDONLY,
    os.O_WRONLY | os.O_CREAT,
    os.O_WRONLY | os.O_TRUNC,
    os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
], ids=["read", "create", "truncate", "create-truncate"])
def test_open_nofollow_refuses_a_reparse_point_without_superseding_it(socket_dir, flags) -> None:
    from tests.unix_socket import unix_listener

    path = socket_dir / "s.sock"
    with unix_listener(path):
        assert files.is_reparse_point(path) is True
        with pytest.raises(OSError) as caught:
            files.open_nofollow(path, flags)
        assert caught.value.errno == errno.ELOOP
        # CREATE_ALWAYS would have replaced the socket with a regular file.
        assert files.is_reparse_point(path) is True


def test_is_unix_socket_rejects_regular_files_and_directories(tmp_path) -> None:
    regular = tmp_path / "f"
    regular.write_bytes(b"")
    assert files.is_unix_socket(regular.lstat()) is False
    assert files.is_unix_socket(tmp_path.lstat()) is False


def test_is_unix_socket_accepts_a_bound_listener(socket_dir) -> None:
    from tests.unix_socket import unix_listener

    path = socket_dir / "s.sock"
    with unix_listener(path):
        assert files.is_unix_socket(path.lstat()) is True


def test_create_private_temp_creates_a_new_private_file(tmp_path) -> None:
    fd, name = files.create_private_temp(tmp_path, prefix=".p-", suffix=".json")
    try:
        assert os.get_inheritable(fd) is False
        os.write(fd, b"secret")
    finally:
        os.close(fd)
    path = Path(name)
    assert path.is_absolute() and path.parent == tmp_path
    assert path.name.startswith(".p-") and path.name.endswith(".json")
    assert path.read_bytes() == b"secret"
    assert_private_file(path)


def test_create_private_temp_is_private_in_a_foreign_readable_directory(tmp_path) -> None:
    foreign_readable_dir(tmp_path)
    plain = tmp_path / "plain"
    plain.write_bytes(b"")
    if sys.platform == "win32":
        assert not perms.is_private(plain)  # control: inherited Everyone read
    fd, name = files.create_private_temp(tmp_path)
    try:
        assert perms.is_private_fd(fd)
    finally:
        os.close(fd)
    assert_private_file(name)


def test_create_private_temp_names_are_unique_and_share_delete(tmp_path) -> None:
    first_fd, first = files.create_private_temp(tmp_path)
    second_fd, second = files.create_private_temp(tmp_path)
    try:
        assert first != second
        os.unlink(first)
        assert not os.path.exists(first)
    finally:
        os.close(first_fd)
        os.close(second_fd)


def test_create_private_temp_missing_directory_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        files.create_private_temp(tmp_path / "missing")


@pytest.mark.windows_only(reason="exercises the Windows name-collision retry")
def test_create_private_temp_retries_a_taken_name(tmp_path, monkeypatch) -> None:
    names = iter(["a" * 16, "b" * 16, "c" * 16])
    monkeypatch.setattr(files.secrets, "token_hex", lambda _n: next(names))
    (tmp_path / ("t" + "a" * 16)).write_bytes(b"taken")
    (tmp_path / ("t" + "b" * 16)).mkdir()
    fd, name = files.create_private_temp(tmp_path, prefix="t")
    os.close(fd)
    assert Path(name).name == "t" + "c" * 16
    assert (tmp_path / ("t" + "a" * 16)).read_bytes() == b"taken"
