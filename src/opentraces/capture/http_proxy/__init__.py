"""HTTP proxy capture source for the Context Tree substrate (prototype).

This module is the second capture source for Context Tree alongside the
JSONL-driven reconstruction in ``capture/claude_code/context_tree_capture.py``.
It reads a structured proxy capture log (one JSON line per intercepted
Anthropic ``/v1/messages`` POST), assembles the four canonical
ContextLayer types from the **actual bytes the model saw on the wire**,
and appends ``context_layer_captured`` / ``context_node_observed`` /
``context_tree_reconciled`` events into the same canonical event log
the JSONL pipeline writes to (``refs/opentraces/local/events/v1``).

Capture-method tagging: every layer this module emits carries
``capture_method: proxy``. Because the proxy sees the exact bytes
shipped to ``api.anthropic.com``, layers it produces are labeled
``completeness: full`` rather than ``approximated``.

Prototype scope (per plan 078 evidence brief):
- One pure-stdlib + httpx proxy at ``proxy.py``.
- A canned-response fake Anthropic backend at ``fake_backend.py``.
- A log-reader / event-emitter at ``capture.py``.
- A comparison-script entry point at ``compare.py``.

What this module is NOT:
- Not a production capture path; no auth, no rate-limiting, no streaming
  response coalescing, no MITM TLS interception.
- Not wired into ``core/ingest.py`` — the prototype runs as a standalone
  fixture that invokes ``emit_context_tree_events_from_http_log`` directly.
- Not validated against real ``api.anthropic.com`` traffic; the
  comparison report uses a fully deterministic local backend.
"""
from __future__ import annotations

from .capture import (
    HTTP_LOG_FILENAME,
    emit_context_tree_events_from_http_log,
    read_http_capture_log,
)
from .proxy import (
    ProxyConfig,
    run_proxy,
)

__all__ = [
    "HTTP_LOG_FILENAME",
    "ProxyConfig",
    "emit_context_tree_events_from_http_log",
    "read_http_capture_log",
    "run_proxy",
]
