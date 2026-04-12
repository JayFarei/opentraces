"""Compatibility shim. Moved to :mod:`opentraces.capture.claude_code.hooks.on_stop`."""

import sys
import warnings

warnings.warn(
    "opentraces.installers.claude_code_hooks.on_stop is deprecated; "
    "use opentraces.capture.claude_code.hooks.on_stop",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.capture.claude_code.hooks import on_stop as _target  # noqa: E402

# Alias this module to the real one so monkeypatching "this" module is
# equivalent to monkeypatching the target, and all attrs (including
# underscore-prefixed helpers) are visible.
sys.modules[__name__] = _target
