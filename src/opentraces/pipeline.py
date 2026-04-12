"""Compatibility shim. Moved to opentraces.core.pipeline."""
import warnings
import sys

warnings.warn(
    "opentraces.pipeline is deprecated; use opentraces.core.pipeline",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.core import pipeline as _mod

sys.modules[__name__] = _mod
