"""Compatibility shim. Moved to :mod:`opentraces.capture`."""

import warnings

warnings.warn(
    "opentraces.agents is deprecated; use opentraces.capture",
    DeprecationWarning,
    stacklevel=2,
)
