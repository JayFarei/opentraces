"""Compatibility shim. Moved to opentraces.core.inbox."""
import warnings
import sys

warnings.warn(
    "opentraces.inbox is deprecated; use opentraces.core.inbox",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.core import inbox as _mod

sys.modules[__name__] = _mod
