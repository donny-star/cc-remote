"""cc-remote: self-hosted remote control for Claude Code.

Two independent links:
  - model link:  cc -> local proxy (127.0.0.1:19191) -> z.AI GLM  (untouched)
  - control link: client <-> relay(WS) <-> wrapper <-> ClaudeSDKClient <-> cc
"""

__version__ = "5.3.0"

import cc_remote.platform  # noqa: E402,F401  (process-wide platform setup)
