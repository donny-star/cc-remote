"""cc_remote.platform.pty: pseudo-terminals for the broker and claude-remote."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_pywinpty_is_pinned_for_windows_wrappers_only():
    for name in ("requirements.txt", "requirements-wrapper.txt"):
        assert 'pywinpty==3.1.0 ; sys_platform == "win32"' in (ROOT / name).read_text(encoding="utf-8")
    for name in ("requirements.lock", "requirements-wrapper.lock"):
        lock = (ROOT / name).read_text(encoding="utf-8")
        assert "pywinpty==3.1.0 ; sys_platform == 'win32'" in lock
        assert "index-url" not in lock and "tuna" not in lock
    for name in ("requirements-relay.txt", "requirements-relay.lock"):
        assert "pywinpty" not in (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.windows_only(reason="ConPTY is the Windows pseudo-terminal")
def test_conpty_backend_is_available():
    import winpty

    assert winpty.Backend.ConPTY == 0
