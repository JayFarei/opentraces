"""Deprecated shim. Use `opentraces.publish.atif` instead."""

from __future__ import annotations

import sys
import warnings

warnings.warn(
    "opentraces.exporters.atif is deprecated, use opentraces.publish.atif",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.publish import atif as _mod  # noqa: E402

sys.modules[__name__] = _mod
