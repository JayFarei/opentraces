"""Deprecated shim. Use `opentraces.publish.huggingface.dataset_card` instead."""

from __future__ import annotations

import sys
import warnings

warnings.warn(
    "opentraces.upload.dataset_card is deprecated, use opentraces.publish.huggingface.dataset_card",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.publish.huggingface import dataset_card as _mod  # noqa: E402

sys.modules[__name__] = _mod
