"""cc-remote: self-hosted remote control for Claude Code.

Two independent links:
  - model link:  cc -> local proxy (127.0.0.1:19191) -> z.AI GLM  (untouched)
  - control link: client <-> relay(WS) <-> wrapper <-> ClaudeSDKClient <-> cc
"""

import sys

if sys.platform == "win32":
    from cc_remote.wrapper.os_compat import use_binary_descriptors

    use_binary_descriptors()

__version__ = "5.3.0"
