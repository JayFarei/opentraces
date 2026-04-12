"""Compatibility shim. Moved to :mod:`opentraces.capture.claude_code`."""

import warnings

warnings.warn(
    "opentraces.agents.claude_code is deprecated; use opentraces.capture.claude_code",
    DeprecationWarning,
    stacklevel=2,
)

from ...capture.claude_code import ClaudeCodeParser  # noqa: F401,E402

__all__ = ["ClaudeCodeParser"]
