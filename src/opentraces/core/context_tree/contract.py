"""Context Tree contract: schema versions, event types, vocabularies.

The Context Tree substrate captures what the LLM saw at each step of an
agent session. It rides on Trail's append-only event log
(``refs/opentraces/local/events/v1``) and the content-addressed
``TrailEvent`` envelope; this module pins the version strings, event
type vocabulary, and structural enumerations that downstream consumers
(replay, RL, viewer, dashboards) depend on as a frozen contract.

See ``kb/plans/077-context-tree-substrate.md`` §"Core Primitives" and
§"CLI surface" for the substrate spec.
"""
from __future__ import annotations

from typing import Final

# --------------------------------------------------------------------------- #
# Schema version strings (frozen v1 contract for downstream consumers)
# --------------------------------------------------------------------------- #

CONTEXT_TREE_VERSION: Final[str] = "0.1.0"

CONTEXT_TREE_SCHEMA_VERSION: Final[str] = "opentraces.context_tree.v1"
CONTEXT_NODE_SCHEMA_VERSION: Final[str] = "opentraces.context_node.v1"
CONTEXT_LAYER_SCHEMA_VERSION: Final[str] = "opentraces.context_layer.v1"
CONTEXT_READS_SCHEMA_VERSION: Final[str] = "opentraces.context_reads.v1"
CONTEXT_WRITES_SCHEMA_VERSION: Final[str] = "opentraces.context_writes.v1"
CONTEXT_RESOLVE_SCHEMA_VERSION: Final[str] = "opentraces.context_resolve.v1"
CONTEXT_RESUME_SCHEMA_VERSION: Final[str] = "opentraces.context_resume.v1"

# --------------------------------------------------------------------------- #
# Event type strings (TrailEvent.event_type values for Context Tree events)
# --------------------------------------------------------------------------- #

CONTEXT_LAYER_CAPTURED: Final[str] = "context_layer_captured"
CONTEXT_NODE_OBSERVED: Final[str] = "context_node_observed"
CONTEXT_COMPACTION_OBSERVED: Final[str] = "context_compaction_observed"
CONTEXT_TREE_RECONCILED: Final[str] = "context_tree_reconciled"

CONTEXT_EVENT_TYPES: Final[frozenset[str]] = frozenset({
    CONTEXT_LAYER_CAPTURED,
    CONTEXT_NODE_OBSERVED,
    CONTEXT_COMPACTION_OBSERVED,
    CONTEXT_TREE_RECONCILED,
})

# --------------------------------------------------------------------------- #
# Structural vocabularies (closed enumerations validated by models.py)
# --------------------------------------------------------------------------- #

LAYER_TYPES: Final[frozenset[str]] = frozenset({
    "system",
    "messages",
    "tool_registry",
    "runtime_state",
})

BRANCH_TYPES: Final[frozenset[str]] = frozenset({
    "root",
    "linear",
    "compaction_fork",
    "subagent_fork",
    "rewind_branch",
    "manual_branch",
})

COMPLETENESS_VALUES: Final[frozenset[str]] = frozenset({
    "full",
    "approximated",
    "stub",
})

CAPTURE_METHOD_VALUES: Final[frozenset[str]] = frozenset({
    "live_capture",
    "transcript_reconstruction",
    "hardcoded_template",
})

# --------------------------------------------------------------------------- #
# Capture limitation tags (additive; pre-registered for the security walker
# and the reconciler so events emitting these don't trip the cross-cutting
# `capture_limitations` validator in ``core/trails/models.py``).
# --------------------------------------------------------------------------- #

CONTEXT_CAPTURE_LIMITATIONS: Final[frozenset[str]] = frozenset({
    "transcript_modified",
    "static_system_prompt_core_unknown",
    "builtin_tool_schemas_inferred",
    "mcp_tool_inventory_not_probed",
    "skill_body_missing",
    "subagent_session_unreachable",
    "branch_depth_exceeded",
    "context_layer_unavailable",
    "context_tree_not_captured",
})
