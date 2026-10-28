"""Local PTY broker for the official Claude Code terminal client.

This package is deliberately separate from :mod:`cc_remote.tui`.  It never
installs or impersonates a ``claude`` executable: the broker owns a PTY and
executes the real Claude Code binary inside it, while ``claude-remote`` clients
attach over a same-user local endpoint (a Unix-domain socket on POSIX).
"""

from importlib import import_module
from typing import Any

from .paths import SOCKET_ENV, default_socket_path

# The PTY server and its client import POSIX-only terminal APIs.  Resolve them
# on first use so importing the portable ``paths`` submodule (e.g. from
# ``cc_remote.config``) does not require a PTY-capable platform.
_LAZY_EXPORTS = {
    "BrokerClient": ".client",
    "BrokerClientError": ".client",
    "BrokerConfig": ".server",
    "BrokerServer": ".server",
}


def __getattr__(name: str) -> Any:
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


__all__ = [
    "BrokerClient",
    "BrokerClientError",
    "BrokerConfig",
    "BrokerServer",
    "SOCKET_ENV",
    "default_socket_path",
]
