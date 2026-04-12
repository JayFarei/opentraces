"""Deprecated shim. Use `opentraces.publish.huggingface.schema` instead."""

from __future__ import annotations

import sys
import warnings

warnings.warn(
    "opentraces.upload.hf_schema is deprecated, use opentraces.publish.huggingface.schema",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.publish.huggingface import schema as _mod  # noqa: E402

sys.modules[__name__] = _mod
