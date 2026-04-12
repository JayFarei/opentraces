"""Compatibility shim. Moved to :mod:`opentraces.capture.claude_code.hooks.intent_adapter`."""

import sys
import warnings

warnings.warn(
    "opentraces.installers.claude_code_hooks.intent_adapter is deprecated; "
    "use opentraces.capture.claude_code.hooks.intent_adapter",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.capture.claude_code.hooks import intent_adapter as _target  # noqa: E402

sys.modules[__name__] = _target
