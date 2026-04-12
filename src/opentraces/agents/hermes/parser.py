"""Compatibility shim. Moved to :mod:`opentraces.capture.hermes`."""

import warnings

warnings.warn(
    "opentraces.agents.hermes.parser is deprecated; use opentraces.capture.hermes",
    DeprecationWarning,
    stacklevel=2,
)

from ...capture.hermes import *  # noqa: F401,F403,E402
from ...capture.hermes import HermesParser  # noqa: F401,E402
