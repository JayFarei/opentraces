"""Compatibility shim. Moved to :mod:`opentraces.capture.claude_code.hooks.on_compact`."""

import sys
import warnings

warnings.warn(
    "opentraces.installers.claude_code_hooks.on_compact is deprecated; "
    "use opentraces.capture.claude_code.hooks.on_compact",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.capture.claude_code.hooks import on_compact as _target  # noqa: E402

sys.modules[__name__] = _target
