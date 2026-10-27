"""Suite-wide isolation from a developer's real engine account registries."""

from __future__ import annotations

import os
import sys

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
        for name in ("posix_only", "windows_only"):
            marker = item.get_closest_marker(name)
            if marker is not None and not marker.kwargs.get("reason"):
                raise pytest.UsageError(f"{item.nodeid}: @{name} requires reason=")
        marker = item.get_closest_marker(excluded)
        if marker is not None:
            item.add_marker(pytest.mark.skip(
                reason=f"{excluded}: {marker.kwargs['reason']}"))
