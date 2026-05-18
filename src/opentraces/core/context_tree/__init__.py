"""Context Tree substrate, third alongside Trace and Trail.

Captures what the LLM saw at each step of an agent session. Rides on
Trail's append-only event log (``refs/opentraces/local/events/v1``)
and the content-addressed ``TrailEvent`` envelope; adds four new event
types, a ContextLayer + ContextNode model pair, and a navigation surface
exposed via the ``opentraces ctx`` CLI.

See ``kb/plans/077-context-tree-substrate.md`` for the spec.
"""
from __future__ import annotations

from .contract import (
    BRANCH_TYPES,
    CAPTURE_METHOD_LIVE,
    CAPTURE_METHOD_OTEL,
    CAPTURE_METHOD_PROXY,
    CAPTURE_METHOD_TEMPLATE,
    CAPTURE_METHOD_TRANSCRIPT,
    CAPTURE_METHOD_VALUES,
    COMPLETENESS_VALUES,
    CONTEXT_CAPTURE_LIMITATIONS,
    CONTEXT_COMPACTION_OBSERVED,
    CONTEXT_EVENT_TYPES,
    CONTEXT_LAYER_CAPTURED,
    CONTEXT_LAYER_SCHEMA_VERSION,
    CONTEXT_NODE_OBSERVED,
    CONTEXT_NODE_SCHEMA_VERSION,
    CONTEXT_READS_SCHEMA_VERSION,
    CONTEXT_RESOLVE_SCHEMA_VERSION,
    CONTEXT_RESUME_SCHEMA_VERSION,
    CONTEXT_TREE_RECONCILED,
    CONTEXT_TREE_SCHEMA_VERSION,
    CONTEXT_TREE_VERSION,
    CONTEXT_WRITES_SCHEMA_VERSION,
    LAYER_TYPES,
)
from .models import (
    HASH_PREFIX,
    ContextLayer,
    ContextNode,
    build_layer,
    build_node,
    canonical_json,
    canonical_layer_material,
    canonical_node_material,
    compute_layer_id,
    compute_node_id,
    sha256_hex,
    sha256_id,
)

__all__ = [
    # contract / schema versions
    "CONTEXT_TREE_VERSION",
    "CONTEXT_TREE_SCHEMA_VERSION",
    "CONTEXT_NODE_SCHEMA_VERSION",
    "CONTEXT_LAYER_SCHEMA_VERSION",
    "CONTEXT_READS_SCHEMA_VERSION",
    "CONTEXT_WRITES_SCHEMA_VERSION",
    "CONTEXT_RESOLVE_SCHEMA_VERSION",
    "CONTEXT_RESUME_SCHEMA_VERSION",
    # event type constants
    "CONTEXT_LAYER_CAPTURED",
    "CONTEXT_NODE_OBSERVED",
    "CONTEXT_COMPACTION_OBSERVED",
    "CONTEXT_TREE_RECONCILED",
    "CONTEXT_EVENT_TYPES",
    # vocabularies
    "LAYER_TYPES",
    "BRANCH_TYPES",
    "COMPLETENESS_VALUES",
    "CAPTURE_METHOD_VALUES",
    "CAPTURE_METHOD_LIVE",
    "CAPTURE_METHOD_OTEL",
    "CAPTURE_METHOD_PROXY",
    "CAPTURE_METHOD_TEMPLATE",
    "CAPTURE_METHOD_TRANSCRIPT",
    "CONTEXT_CAPTURE_LIMITATIONS",
    # models
    "ContextLayer",
    "ContextNode",
    # helpers
    "HASH_PREFIX",
    "canonical_json",
    "canonical_layer_material",
    "canonical_node_material",
    "compute_layer_id",
    "compute_node_id",
    "sha256_hex",
    "sha256_id",
    "build_layer",
    "build_node",
]
