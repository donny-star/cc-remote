"""Shared helpers for local-endpoint tests on POSIX and Windows."""
from __future__ import annotations

import subprocess
from pathlib import Path

from cc_remote.platform import IS_WINDOWS

# Darwin's sockaddr_un path is only 104 bytes, so POSIX tests keep endpoints in
# /tmp. Windows endpoints have no length limit but must stay inside the
# user's profile, which is where the default temporary directory lives.
SHORT_TMP: str | None = None if IS_WINDOWS else "/tmp"


def share_with_everyone(path: Path) -> None:
    """Make ``path`` readable by other users: the opposite of make_private."""
    if IS_WINDOWS:
        subprocess.run(
            ["icacls", str(path), "/grant", "*S-1-1-0:(R)"],
            check=True, capture_output=True)
    else:
        path.chmod(0o755 if path.is_dir() else 0o644)
