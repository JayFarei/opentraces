"""Compatibility shim. Moved to :mod:`opentraces.quality.parse_gate`."""

import warnings

warnings.warn(
    "opentraces.parsers.quality is deprecated; use opentraces.quality.parse_gate",
    DeprecationWarning,
    stacklevel=2,
)

from ..quality.parse_gate import *  # noqa: F401,F403,E402
from ..quality.parse_gate import meets_quality_threshold  # noqa: F401,E402
