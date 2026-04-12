"""Deprecated shim. Use `opentraces.publish.agent_trace` instead."""

from __future__ import annotations

import sys
import warnings

warnings.warn(
    "opentraces.exporters.agent_trace is deprecated, use opentraces.publish.agent_trace",
    DeprecationWarning,
    stacklevel=2,
)

from opentraces.publish import agent_trace as _mod  # noqa: E402

sys.modules[__name__] = _mod
