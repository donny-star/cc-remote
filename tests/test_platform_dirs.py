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


def test_replace_renames_a_link_at_the_source_not_its_target(tmp_path) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "secret").write_bytes(b"victim")
    project = tmp_path / "project"
    project.mkdir()
    (project / "note.md").write_bytes(b"note")
    _link_directory(victim, project / ".tmp")
    with DirHandle.open_root(project) as root:
        try:
            root.replace(".tmp", "new.md")
        except OSError:
            pass  # refusing is as good as renaming the link itself
    assert (victim / "secret").read_bytes() == b"victim"
    assert (project / "note.md").read_bytes() == b"note"


def test_unlink_removes_a_read_only_file(tmp_path) -> None:
    (tmp_path / "f").write_bytes(b"x")
    os.chmod(tmp_path / "f", stat.S_IREAD)
    with DirHandle.open_root(tmp_path) as root:
        root.unlink("f")
    assert not os.path.lexists(tmp_path / "f")


def test_unlink_refuses_a_read_only_directory(tmp_path) -> None:
    (tmp_path / "d").mkdir()
    os.chmod(tmp_path / "d", stat.S_IREAD | stat.S_IEXEC)
    try:
        with DirHandle.open_root(tmp_path) as root:
            with pytest.raises(OSError):
                root.unlink("d")
        assert (tmp_path / "d").is_dir()
    finally:
        os.chmod(tmp_path / "d", stat.S_IRWXU)


def test_unlink_removes_a_directory_link_not_its_target(tmp_path) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "secret").write_bytes(b"victim")
    os.chmod(victim / "secret", stat.S_IREAD)
    _link_directory(victim, tmp_path / "link")
    with DirHandle.open_root(tmp_path) as root:
        root.unlink("link")
    assert not os.path.lexists(tmp_path / "link")
    assert (victim / "secret").read_bytes() == b"victim"
    os.chmod(victim / "secret", stat.S_IREAD | stat.S_IWRITE)


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


@pytest.mark.windows_only(reason="POSIX dir_fd resolves children against the descriptor")
def test_windows_junction_swapped_above_root_is_not_followed(tmp_path) -> None:
    good, evil, junction = tmp_path / "good", tmp_path / "evil", tmp_path / "J"
    for base, content in ((good, b"GOOD"), (evil, b"OUTSIDE-SECRET")):
        (base / "ws" / "sub").mkdir(parents=True)
        (base / "ws" / "f.txt").write_bytes(content)
        (base / "ws" / "sub" / "g.txt").write_bytes(content)
    (evil / "ws" / "evil-only").write_bytes(b"x")
    _link_directory(good, junction)
    with DirHandle.open_root(junction / "ws") as root:
        assert root.path == str(junction / "ws")
        os.rmdir(junction)
        _link_directory(evil, junction)
        assert (junction / "ws" / "f.txt").read_bytes() == b"OUTSIDE-SECRET"

        try:
            fd = root.open_file("f.txt", os.O_RDONLY)
        except OSError:
            pass
        else:
            try:
                assert os.read(fd, 64) == b"GOOD"
            finally:
                os.close(fd)
        with root.scandir() as entries:
            assert "evil-only" not in {entry.name for entry in entries}
        with pytest.raises(FileNotFoundError):
            root.lstat("evil-only")
        with root.open_dir("sub") as sub:
            fd = sub.open_file("g.txt", os.O_RDONLY)
            try:
                assert os.read(fd, 64) == b"GOOD"
            finally:
                os.close(fd)
            assert sub.child_path("g.txt") == str(junction / "ws" / "sub" / "g.txt")
        root.mkdir("made")
        assert (good / "ws" / "made").is_dir()
        assert not (evil / "ws" / "made").exists()
        assert root.child_path("f.txt") == str(junction / "ws" / "f.txt")


@pytest.mark.windows_only(reason="the private volume path exists only on Windows")
def test_windows_plain_root_keeps_the_caller_path(tmp_path) -> None:
    with DirHandle.open_root(tmp_path) as root:
        assert root.path == os.path.abspath(str(tmp_path))
        fd = root.open_file("f", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        os.close(fd)
        assert root.child_path("f") == os.path.join(str(tmp_path), "f")
        with root.scandir() as entries:
            assert [entry.name for entry in entries] == ["f"]
        assert (tmp_path / "f").is_file()


@pytest.mark.windows_only(reason="Win32 name normalization and devices do not exist on POSIX")
@pytest.mark.parametrize("name", [
    ".. ", "...", ". ", "....", "foo.", "foo ", "foo. ",
    "a:b", "f.txt:stream", "f.txt::$DATA",
    "CON", "con", "Prn", "AUX", "NUL", "nul.txt", "COM1", "com9.log", "LPT1", "lpt9",
    "nul .txt",
])
def test_windows_rejects_aliasing_and_device_names(tmp_path, name) -> None:
    (tmp_path / "foo").write_bytes(b"x")
    with DirHandle.open_root(tmp_path) as root:
        for operation in (
                lambda: root.open_file(name, os.O_RDONLY),
                lambda: root.open_file(name, os.O_WRONLY | os.O_CREAT),
                lambda: root.open_dir(name),
                lambda: root.lstat(name),
                lambda: root.mkdir(name)):
            with pytest.raises(OSError) as caught:
                operation()
            assert caught.value.errno == errno.EINVAL


@pytest.mark.windows_only(reason="Win32 name validation exists only on Windows")
@pytest.mark.parametrize("name", [".hidden", "a.b", "COM10", "console", "nulx.txt", "LPT0x"])
def test_windows_accepts_ordinary_names(tmp_path, name) -> None:
    with DirHandle.open_root(tmp_path) as root:
        os.close(root.open_file(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL))
        assert stat.S_ISREG(root.lstat(name).st_mode)


def test_unclosed_handle_warns_and_closes(tmp_path) -> None:
    handle = DirHandle.open_root(tmp_path)
    fd = handle.fd
    with pytest.warns(ResourceWarning):
        handle.__del__()
    with pytest.raises(OSError):
        os.fstat(fd)
    handle.close()


def test_partially_initialised_handle_is_silent() -> None:
    import warnings

    handle = DirHandle.__new__(DirHandle)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        handle.__del__()


@pytest.mark.windows_only(reason="the private volume path exists only on Windows")
@pytest.mark.parametrize(("dos", "private"), [
    (r"\\?\C:\w", r"\\?\C:\w"),
    (r"\\?\UNC\srv\share\w", r"\\?\UNC\srv\share\w"),
    (r"\\srv\share\w", r"\\?\UNC\srv\share\w"),
    (r"C:\w", r"\\?\C:\w"),
])
def test_windows_private_path_falls_back_to_a_prefixed_dos_path(
        monkeypatch, dos, private) -> None:
    from cc_remote.platform import dirs

    def final_path(handle, flags):
        if flags & 0x1:  # VOLUME_NAME_GUID: none on a network share
            raise FileNotFoundError(errno.ENOENT, "no volume GUID path")
        return dos

    monkeypatch.setattr(dirs, "_win32_final_path", final_path)
    assert dirs._win32_private_path(0) == private


@pytest.mark.windows_only(reason="the private volume path exists only on Windows")
def test_windows_root_works_through_the_dos_fallback(tmp_path, monkeypatch) -> None:
    from cc_remote.platform import dirs

    real = dirs._win32_final_path

    def final_path(handle, flags):
        if flags & 0x1:
            raise OSError(errno.ENOTSUP, "no volume GUID path")
        return real(handle, flags)

    monkeypatch.setattr(dirs, "_win32_final_path", final_path)
    (tmp_path / "f").write_bytes(b"x")
    with DirHandle.open_root(tmp_path) as root:
        assert root._os_path.startswith("\\\\?\\")
        assert stat.S_ISREG(root.lstat("f").st_mode)


@pytest.mark.windows_only(reason="the private volume path exists only on Windows")
def test_windows_failed_private_path_releases_the_pin(tmp_path, monkeypatch) -> None:
    from cc_remote.platform import dirs

    def final_path(handle, flags):
        raise OSError(errno.EIO, "final path unavailable")

    monkeypatch.setattr(dirs, "_win32_final_path", final_path)
    (tmp_path / "d").mkdir()
    with pytest.raises(OSError):
        DirHandle.open_root(tmp_path / "d")
    os.rename(tmp_path / "d", tmp_path / "moved")  # the handle was closed
