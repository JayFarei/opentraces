"""Deprecated shim. Use `opentraces.publish` instead."""

from __future__ import annotations

import warnings

warnings.warn(
    "opentraces.exporters is deprecated, use opentraces.publish",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.publish import get_exporters  # noqa: F401,E402
from opentraces.publish import _EXPORTERS as EXPORTERS  # noqa: F401,E402
