"""Compatibility shim. Moved to opentraces.core.paths."""
import warnings
import sys

warnings.warn(
    "opentraces.paths is deprecated; use opentraces.core.paths",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.core import paths as _mod

sys.modules[__name__] = _mod
