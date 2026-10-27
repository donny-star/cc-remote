"""Suite-wide isolation from a developer's real engine account registries."""

from __future__ import annotations

import sys

import pytest


# Release activation, the relay updater and the systemd/launchd installers are
# macOS/Linux only by design (``update.host_platform`` rejects Windows), and
# their modules import fcntl/pwd at module scope.
collect_ignore = [
    "test_deploy_cleanup.py",
    "test_install_lock.py",
    "test_linux_service.py",
    "test_relay_update_job.py",
    "test_release_retention.py",
    "test_update.py",
    "test_update_diagnostics.py",
    "test_update_installer_relay.py",
    "test_update_relay.py",
] if sys.platform == "win32" else []


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
