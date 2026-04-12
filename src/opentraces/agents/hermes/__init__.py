"""Compatibility shim. Moved to :mod:`opentraces.capture.hermes`."""

import warnings

warnings.warn(
    "opentraces.agents.hermes is deprecated; use opentraces.capture.hermes",
    DeprecationWarning,
    stacklevel=2,
)

from ...capture.hermes import HermesParser  # noqa: F401,E402

__all__ = ["HermesParser"]
