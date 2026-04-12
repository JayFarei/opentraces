"""Compatibility shim. Moved to opentraces.core.config."""
import warnings
import sys

warnings.warn(
    "opentraces.config is deprecated; use opentraces.core.config",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.core import config as _mod

sys.modules[__name__] = _mod
