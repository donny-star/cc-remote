"""Suite-wide isolation from a developer's real engine account registries."""

from __future__ import annotations

import functools
import os
import sys
import tempfile

import pytest


_ACCOUNT_PROFILE_ENV = (
    "CODEX_HOME",
    "CC_REMOTE_CODEX_PROFILES_JSON",
    "CC_REMOTE_CODEX_PROFILES_FILE",
    "CC_REMOTE_CLAUDE_PROFILES_JSON",
    "CC_REMOTE_CLAUDE_PROFILES_FILE",
    # A test launched from an installed Wrapper inherits launchd's identity.
    # That enables legacy Claude profile discovery from the real user's home.
    "XPC_SERVICE_NAME",
)


@pytest.fixture(autouse=True)
def _isolate_account_profile_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Make every test opt in to account/profile configuration explicitly.

    ``WrapperConfig`` and account registries intentionally inherit the
    launching shell in production.  Unit tests must not inherit those values:
    otherwise a developer running pytest from a managed Wrapper/``CODEX_HOME`` gets
    a different registry and dozens of unrelated mock-signature failures.
    """
    for key in _ACCOUNT_PROFILE_ENV:
        monkeypatch.delenv(key, raising=False)


@functools.cache
def _symlink_unavailable() -> str | None:
    """Why this process cannot create symbolic links, or None when it can.

    Windows needs Developer Mode or an elevated token (WinError 1314
    otherwise). POSIX always can.
    """
    if sys.platform != "win32":
        return None
    with tempfile.TemporaryDirectory() as directory:
        target = os.path.join(directory, "target")
        with open(target, "w", encoding="utf-8"):
            pass
        try:
            os.symlink(target, os.path.join(directory, "link"))
        except OSError as exc:
            return f"cannot create symbolic links: {exc.strerror or exc}"
    return None


def pytest_configure(config: pytest.Config) -> None:
    if sys.platform != "win32":
        return
    if not sys.flags.utf8_mode:
        raise pytest.UsageError(
            "On Windows run the suite in UTF-8 mode: "
            "set PYTHONUTF8=1 (or python -X utf8 -m pytest)"
        )
    # ``-X utf8`` is not inherited by pytest-xdist workers; the variable is.
    os.environ.setdefault("PYTHONUTF8", "1")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item],
) -> None:
    excluded = "posix_only" if sys.platform == "win32" else "windows_only"
    for item in items:
        for name in ("posix_only", "windows_only", "requires_symlink"):
            marker = item.get_closest_marker(name)
            if marker is not None and not marker.kwargs.get("reason"):
                raise pytest.UsageError(f"{item.nodeid}: @{name} requires reason=")
        marker = item.get_closest_marker(excluded)
        if marker is not None:
            item.add_marker(pytest.mark.skip(
                reason=f"{excluded}: {marker.kwargs['reason']}"))
        marker = item.get_closest_marker("requires_symlink")
        if marker is not None:
            unavailable = _symlink_unavailable()
            if unavailable is not None:
                item.add_marker(pytest.mark.skip(
                    reason=f"requires_symlink: {marker.kwargs['reason']} ({unavailable})"))
