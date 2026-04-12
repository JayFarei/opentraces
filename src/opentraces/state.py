"""Compatibility shim. Moved to opentraces.core.state."""
import warnings
import sys

warnings.warn(
    "opentraces.state is deprecated; use opentraces.core.state",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.core import state as _mod

sys.modules[__name__] = _mod
