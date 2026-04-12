"""Compatibility shim. Moved to opentraces.core.processors."""
import warnings
import sys

warnings.warn(
    "opentraces.processors is deprecated; use opentraces.core.processors",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.core import processors as _mod

sys.modules[__name__] = _mod
