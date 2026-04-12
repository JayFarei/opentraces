"""Deprecated shim. Use `opentraces.publish.huggingface` instead."""

from __future__ import annotations

import warnings

warnings.warn(
    "opentraces.upload is deprecated, use opentraces.publish.huggingface",
    DeprecationWarning,
    stacklevel=2,
)
