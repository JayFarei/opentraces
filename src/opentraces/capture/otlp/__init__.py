"""OTLP HTTP/JSON receiver capture source for the Context Tree substrate.

Third capture source for Context Tree alongside the JSONL parser
(``capture/claude_code/context_tree_capture.py``) and the prototype HTTP
proxy (``capture/http_proxy/``). Per plan 078 the receiver consumes
Claude Code's native OTel emission and, paired with the mapper, emitter,
and raw-body watcher (sibling modules), drives the same
``refs/opentraces/local/events/v1`` log with ``capture_method=otel``.

This package exposes only the receiver surface. The mapper / emitter /
raw-body watcher live in sibling modules and are wired together by the
CLI layer (``opentraces capture-otlp``) so each can evolve independently
without import-time coupling.

Capture-method tagging: layers built from raw API bodies surfaced via
this receiver carry ``capture_method=otel`` with ``completeness=full``
for system / messages / tool_registry content; lifecycle events
(plugin_loaded, mcp_server_connection, hook_registered) become
``tool_registry`` / ``runtime_state`` enrichments.
"""

from __future__ import annotations

from .receiver import OTLPReceiver, start_receiver

__all__ = [
    "OTLPReceiver",
    "start_receiver",
]
