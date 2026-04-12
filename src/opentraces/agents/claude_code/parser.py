"""Compatibility shim. Moved to :mod:`opentraces.capture.claude_code.parse`."""

import warnings

warnings.warn(
    "opentraces.agents.claude_code.parser is deprecated; "
    "use opentraces.capture.claude_code.parse",
    DeprecationWarning,
    stacklevel=2,
)

from ...capture.claude_code.parse import *  # noqa: F401,F403,E402
from ...capture.claude_code.parse import ClaudeCodeParser  # noqa: F401,E402
