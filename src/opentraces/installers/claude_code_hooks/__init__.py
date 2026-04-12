"""Compatibility shim. Moved to :mod:`opentraces.capture.claude_code.hooks`."""

import warnings

warnings.warn(
    "opentraces.installers.claude_code_hooks is deprecated; "
    "use opentraces.capture.claude_code.hooks",
    DeprecationWarning,
    stacklevel=2,
)

from ...capture.claude_code.hooks import (  # noqa: F401,E402
    on_compact,
    on_stop,
    on_tool_use,
    intent_adapter,
)
