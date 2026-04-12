"""Compatibility shim. Moved to opentraces.core.workflow."""
import warnings
import sys

warnings.warn(
    "opentraces.workflow is deprecated; use opentraces.core.workflow",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.core import workflow as _mod

sys.modules[__name__] = _mod
