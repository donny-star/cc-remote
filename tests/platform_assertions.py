"""Privacy assertions that mean the same thing on POSIX and Windows."""
from __future__ import annotations

import os
import stat
import sys

from cc_remote.platform import perms


def assert_private_file(path: os.PathLike[str] | str) -> None:
    if sys.platform == "win32":
        assert perms.is_private(path), f"{path} is readable by other principals"
        return
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def assert_private_dir(path: os.PathLike[str] | str) -> None:
    if sys.platform == "win32":
        assert perms.is_private(path), f"{path} is accessible to other principals"
        return
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o700
