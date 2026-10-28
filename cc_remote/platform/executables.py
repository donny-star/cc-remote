"""Locate engine executables and unwrap npm ``.cmd`` shims on Windows.

Spawning a ``.cmd`` goes through cmd.exe, which breaks argument quoting and
puts an extra process between us and the engine. npm shims have two stable
shapes: a direct ``"%dp0%\\...\\x.exe" %*`` call and a ``"%_prog%" "%dp0%\\...\\x.js"``
node call. Both resolve to a direct argv; anything else falls back to cmd.exe.
"""
from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterable
from pathlib import Path

from cc_remote.log import logger
from cc_remote.platform import IS_WINDOWS

log = logger("cc_remote.platform.executables")

_EXE_CALL = re.compile(r'^"%dp0%\\(?P<target>[^"]+\.exe)"\s+%\*\s*$', re.MULTILINE)
_NODE_CALL = re.compile(r'"%_prog%"\s+"%dp0%\\(?P<target>[^"]+\.js)"\s+%\*')


def find(name: str, preferred: Iterable[Path] = ()) -> str | None:
    for candidate in preferred:
        options = [candidate]
        if IS_WINDOWS and not candidate.suffix:
            options.insert(0, candidate.with_suffix(".exe"))
        for option in options:
            if option.is_file():
                return str(option)
    return shutil.which(name)


def command_for(path: str) -> list[str]:
    if Path(path).suffix.lower() not in {".cmd", ".bat"}:
        return [path]
    shim = Path(path)
    base = shim.parent
    try:
        text = shim.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if match := _EXE_CALL.search(text):
        return [str(base.joinpath(*match["target"].split("\\")))]
    if match := _NODE_CALL.search(text):
        bundled = base / "node.exe"
        node = str(bundled) if bundled.is_file() else (shutil.which("node") or "node")
        return [node, str(base.joinpath(*match["target"].split("\\")))]
    log.warning("unrecognized command shim; running through cmd.exe", path=path)
    return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", path]


_WINDOWS_SEPARATORS = re.compile(rb"[\\/]")


def command_name(arg: bytes) -> bytes:
    """The executable name in one argv element, comparable across platforms.

    POSIX keeps the historical ``rsplit(b"/")`` exactly. Windows paths use
    either separator and are case-insensitive, so they are split on both and
    lower-cased (``...\\node.EXE`` -> ``node.exe``).
    """
    if not IS_WINDOWS:
        return arg.rsplit(b"/", 1)[-1]
    return _WINDOWS_SEPARATORS.split(arg)[-1].lower()
