from __future__ import annotations

import errno
import os
import stat
import sys

import pytest

from cc_remote.platform import files
from cc_remote.platform.dirs import DirHandle


def _link_directory(target, link) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_walk_open_and_read(tmp_path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "f.txt").write_bytes(b"a\nb\x1a")
    with DirHandle.open_root(tmp_path) as root:
        assert stat.S_ISDIR(os.fstat(root.fd).st_mode)
        with root.open_dir("a") as a, a.open_dir("b") as b:
            fd = b.open_file("f.txt", os.O_RDONLY)
            try:
                assert os.read(fd, 16) == b"a\nb\x1a"
            finally:
                os.close(fd)
            assert b.child_path("f.txt") == os.path.join(str(tmp_path), "a", "b", "f.txt")


def test_create_replace_lstat_unlink_and_scandir(tmp_path) -> None:
    with DirHandle.open_root(tmp_path) as root:
        fd = root.open_file("tmp", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, b"new")
            os.fsync(fd)
        finally:
            os.close(fd)
        (tmp_path / "target").write_bytes(b"old")
        root.replace("tmp", "target")
        root.fsync()
        assert (tmp_path / "target").read_bytes() == b"new"
        assert stat.S_ISREG(root.lstat("target").st_mode)
        root.mkdir("sub", 0o700)
        with root.scandir() as entries:
            assert sorted(entry.name for entry in entries) == ["sub", "target"]
        root.unlink("target")
        assert not (tmp_path / "target").exists()
        with pytest.raises(FileExistsError):
            root.mkdir("sub", 0o700)


def test_directory_link_is_refused_at_every_hop(tmp_path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "f").write_bytes(b"x")
    _link_directory(real, tmp_path / "link")
    with DirHandle.open_root(tmp_path) as root:
        with pytest.raises(OSError) as caught:
            root.open_dir("link")
        assert caught.value.errno in (errno.ELOOP, errno.ENOTDIR)
    with pytest.raises(OSError):
        DirHandle.open_root(tmp_path / "link")


@pytest.mark.requires_symlink(reason="opens a file symbolic link through a directory handle")
def test_file_symlink_is_refused(tmp_path) -> None:
    (tmp_path / "target").write_bytes(b"secret")
    (tmp_path / "link").symlink_to(tmp_path / "target")
    with DirHandle.open_root(tmp_path) as root:
        with pytest.raises(OSError) as caught:
            root.open_file("link", os.O_RDONLY)
        assert caught.value.errno == errno.ELOOP


def test_regular_file_is_not_a_directory(tmp_path) -> None:
    (tmp_path / "f").write_bytes(b"x")
    with DirHandle.open_root(tmp_path) as root:
        with pytest.raises(NotADirectoryError):
            root.open_dir("f")


def test_close_is_idempotent(tmp_path) -> None:
    handle = DirHandle.open_root(tmp_path)
    handle.close()
    handle.close()


@pytest.mark.windows_only(reason="POSIX dir_fd needs no component validation")
@pytest.mark.parametrize("name", ["", ".", "..", "a\\b", "a/b", "C:x"])
def test_windows_rejects_non_component_names(tmp_path, name) -> None:
    with DirHandle.open_root(tmp_path) as root:
        with pytest.raises(OSError) as caught:
            root.open_file(name, os.O_RDONLY)
        assert caught.value.errno == errno.EINVAL


@pytest.mark.windows_only(reason="handle pinning is how Windows replaces openat")
def test_windows_pinned_directory_and_ancestors_cannot_be_renamed(tmp_path) -> None:
    (tmp_path / "parent" / "child").mkdir(parents=True)
    with DirHandle.open_root(tmp_path / "parent" / "child") as child:
        with pytest.raises(PermissionError):
            os.rename(tmp_path / "parent" / "child", tmp_path / "parent" / "moved")
        with pytest.raises(PermissionError):
            os.rename(tmp_path / "parent", tmp_path / "moved")
        fd = child.open_file("ok", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        os.close(fd)
        files.replace(child.child_path("ok"), child.child_path("ok2"))
    os.rename(tmp_path / "parent", tmp_path / "moved")  # released on close


def test_fstat_identity_is_stable(tmp_path) -> None:
    with DirHandle.open_root(tmp_path) as first, DirHandle.open_root(tmp_path) as second:
        a, b = os.fstat(first.fd), os.fstat(second.fd)
        assert (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
