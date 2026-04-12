"""Compatibility shim. Moved to :mod:`opentraces.capture.claude_code.hooks.on_tool_use`."""

import sys
import warnings

warnings.warn(
    "opentraces.installers.claude_code_hooks.on_tool_use is deprecated; "
    "use opentraces.capture.claude_code.hooks.on_tool_use",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.capture.claude_code.hooks import on_tool_use as _target  # noqa: E402

sys.modules[__name__] = _target
