"""Shared helpers for local-endpoint tests on POSIX and Windows."""
from __future__ import annotations

import subprocess
from pathlib import Path

from cc_remote.platform import IS_WINDOWS

# Darwin's sockaddr_un path is only 104 bytes, so POSIX tests keep endpoints in
# /tmp. Windows endpoints have no length limit but must stay inside the
# user's profile, which is where the default temporary directory lives.
SHORT_TMP: str | None = None if IS_WINDOWS else "/tmp"


def share_with_everyone(path: Path, *, inheritable: bool = False) -> None:
    """Make ``path`` readable by other users: the opposite of make_private.

    ``inheritable`` also passes the Windows grant on to new children.
    """
    if IS_WINDOWS:
        grant = "*S-1-1-0:(OI)(CI)(R)" if inheritable else "*S-1-1-0:(R)"
        subprocess.run(
            ["icacls", str(path), "/grant", grant],
            check=True, capture_output=True)
    else:
        path.chmod(0o755 if path.is_dir() else 0o644)
