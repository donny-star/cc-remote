"""Windows path-walk replacements for the POSIX directory-descriptor checks."""
from __future__ import annotations

import os
import sys

import pytest

from cc_remote.viewer import ViewerSite, is_absolute_reference, open_resource
from cc_remote.wrapper.machine import WrapperMachine
from cc_remote.wrapper.os_compat import is_redirect
from cc_remote.wrapper.workspace_browser import browse_workspace

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")


def _junction(link, target) -> None:
    import _winapi  # Junctions need no symlink privilege, unlike os.symlink.

    _winapi.CreateJunction(str(target), str(link))


def test_plain_entries_are_not_redirects(tmp_path):
    (tmp_path / "file.txt").write_text("x")
    assert not is_redirect(os.lstat(tmp_path))
    assert not is_redirect(os.lstat(tmp_path / "file.txt"))


@windows_only
def test_junction_is_a_redirect(tmp_path):
    (tmp_path / "target").mkdir()
    _junction(tmp_path / "link", tmp_path / "target")
    assert is_redirect(os.lstat(tmp_path / "link"))


@pytest.mark.parametrize(("platform", "raw", "expected"), [
    ("win32", r"C:\Users\me\page.html", True),
    ("win32", "c:/Users/me/page.html", True),
    ("win32", r"C:\Users\me\..\other\page.html", False),
    ("win32", r"C:\Users\.\page.html", False),
    ("win32", r"C:\Users\me\page.html:stream", False),
    ("win32", r"\\server\share\page.html", False),
    ("win32", "/Users/me/page.html", False),
    ("win32", "page.html", False),
    ("linux", "/home/me/page.html", True),
    ("linux", "//host/page.html", False),
    ("linux", "/home/me/../page.html", False),
    ("linux", r"C:\Users\me\page.html", False),
])
def test_absolute_reference_per_platform(monkeypatch, platform, raw, expected):
    monkeypatch.setattr(sys, "platform", platform)
    assert is_absolute_reference(raw) is expected


@pytest.mark.parametrize("separator", ["\\", "/"])
def test_attachment_paths_strip_windows_staging_directories(separator):
    staging = separator.join(["C:", "Users", "me", "AppData", "Local", "Temp", "cc-remote-turn-ab12_cd"])
    prompt = f"look at this\n\n[用户附件,请用工具读取以下文件]:\n{staging}{separator}01-report.pdf"
    original, attachments = WrapperMachine._strip_attachment_paths(prompt)
    assert original == "look at this"
    assert attachments == [{"filename": "report.pdf"}]


@windows_only
def test_workspace_browser_rejects_junctions_and_traversal(tmp_path):
    root = tmp_path / "root"
    (root / "folder").mkdir(parents=True)
    (root / "a.txt").write_text("a")
    (tmp_path / "outside.txt").write_text("outside")
    _junction(root / "link", tmp_path)
    listed = browse_workspace(str(root), ".")
    kinds = {entry["name"]: entry["kind"] for entry in listed["entries"]}
    assert kinds["link"] == "unsupported" and kinds["folder"] == "directory"
    assert browse_workspace(str(root), "a.txt")["kind"] == "file"
    for path in ("..", "../outside.txt", "link", "link/outside.txt"):
        with pytest.raises(ValueError):
            browse_workspace(str(root), path)


@windows_only
def test_viewer_resources_reject_junctions(tmp_path):
    root = tmp_path / "site"
    (root / "pages").mkdir(parents=True)
    (root / "pages/index.html").write_text("<p>ok</p>")
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret/index.html").write_text("secret")
    _junction(root / "escape", tmp_path / "secret")
    info = os.lstat(root)
    site = ViewerSite(id="site", label="Site", root=str(root), root_device=info.st_dev,
                      root_inode=info.st_ino, entry="/pages/index.html",
                      paths=["/pages/", "/escape/"])
    stream, _, mime = open_resource(site, "/pages/index.html")
    with stream:
        assert stream.read() == b"<p>ok</p>" and mime.startswith("text/html")
    with pytest.raises(PermissionError):
        open_resource(site, "/escape/index.html")


def test_upload_copy_names_honor_a_tighter_component_limit():
    from cc_remote.wrapper.session_artifacts import _upload_copy_name

    digest = "f" * 64
    for name in ("a" * 236 + ".txt", "界" * 80 + ".md", "界" * 80):
        copy = _upload_copy_name(name, digest, 120)
        assert len(copy.encode("utf-8")) <= 120 and copy.startswith(digest + "-")
        assert os.path.splitext(copy)[1] == os.path.splitext(name)[1]
    # A root too deep for any stem still yields one bounded, non-negative slice.
    assert _upload_copy_name("report.pdf", digest, 10) == f"{digest}-r"


@windows_only
def test_upload_copies_stay_under_max_path(tmp_path):
    from cc_remote.wrapper.session_artifacts import SessionArtifactStore

    store = SessionArtifactStore(tmp_path)
    store.uploads("session", "msg", [{"filename": "界" * 78 + ".txt", "data": "aGVsbG8="}])
    path = store.page("session")[0][0]["path"]
    assert len(os.path.abspath(path)) <= 259 and path.endswith(".txt")



def test_relay_routes_windows_page_references_to_devices():
    # The relay usually runs on Linux, yet must route a Windows device's paths.
    from cc_remote.relay.viewer_pages import _device_checked

    assert _device_checked(r"C:\work\site/index.html")
    assert _device_checked("C:/work/site/index.html")
    assert _device_checked("/srv/site/index.html")
    for raw in (r"C:\site\..\index.html", r"C:\site\index.html:stream",
                r"\\server\share\index.html", r"C:site\index.html", "C:/site/../index.html",
                r"site\index.html"):
        assert not _device_checked(raw)


@windows_only
def test_liveness_probe_never_terminates_the_process():
    # os.kill(pid, 0) is TerminateProcess on Windows; the probe must only observe.
    import subprocess

    from cc_remote.wrapper import process_scan_win32

    assert process_scan_win32.exists(os.getpid()) is True
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert process_scan_win32.exists(child.pid) is not True
    assert process_scan_win32.exists(0) is None


@windows_only
def test_service_owner_exit_proof_uses_the_non_destructive_probe(monkeypatch):
    from cc_remote.claude_service import client as client_module
    from cc_remote.wrapper import process_scan
    from cc_remote.wrapper.process_scan import ProcessIdentity

    monkeypatch.setattr(process_scan, "process_identity", lambda _pid: None)
    monkeypatch.setattr(client_module.os, "kill", lambda *_: pytest.fail("os.kill"))
    assert client_module.service_owner_exited(ProcessIdentity(os.getpid(), 1)) is False
