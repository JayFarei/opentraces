"""Compatibility shim. Moved to :mod:`opentraces.capture._base`."""

import warnings

warnings.warn(
    "opentraces.parsers.base is deprecated; use opentraces.capture._base",
    DeprecationWarning,
    stacklevel=2,
)

from ..capture._base import *  # noqa: F401,F403,E402
from ..capture._base import FormatImporter, ParseOutcome, SessionParser  # noqa: F401,E402
