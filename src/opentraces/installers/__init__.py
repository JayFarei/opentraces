"""Compatibility shim. Moved under :mod:`opentraces.capture`."""

import warnings

warnings.warn(
    "opentraces.installers is deprecated; use opentraces.capture",
    DeprecationWarning,
    stacklevel=2,
)
