"""Deprecated shim. Use `opentraces.publish._base` instead."""

from __future__ import annotations

import sys
import warnings

warnings.warn(
    "opentraces.exporters.base is deprecated, use opentraces.publish._base",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.publish import _base as _mod  # noqa: E402

sys.modules[__name__] = _mod
