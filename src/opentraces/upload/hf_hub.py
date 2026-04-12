"""Deprecated shim. Use `opentraces.publish.huggingface.upload` instead.

Aliases this module to the real module in sys.modules so that
``monkeypatch.setattr('opentraces.upload.hf_hub.X', ...)`` mutates the
same underlying module as ``opentraces.publish.huggingface.upload``.
"""

from __future__ import annotations

import sys
import warnings

warnings.warn(
    "opentraces.upload.hf_hub is deprecated, use opentraces.publish.huggingface.upload",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.publish.huggingface import upload as _mod  # noqa: E402

sys.modules[__name__] = _mod
