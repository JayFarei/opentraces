"""Context Tree models: ContextLayer and ContextNode Pydantic shapes.

A ``ContextLayer`` is one slice of what the model saw at a point in time
(system prompt assembly, message history, tool registry, runtime state).
It is content-addressed: layers with identical content collapse into one
record across the substrate.

A ``ContextNode`` is the immutable record of what the model saw at a
specific decision point. It points at one of each layer type via hashes,
plus tree-position metadata (parent node, branch type, source JSONL
offset) so consumers can walk the active path back to the root.

The substrate rides on Trail's append-only event log; this module
defines the in-memory shape that the parser materializes and the query
projection indexes. See ``kb/plans/077-context-tree-substrate.md``
§"Core Primitives" for the spec.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .contract import (
    BRANCH_TYPES,
    CAPTURE_METHOD_VALUES,
    COMPLETENESS_VALUES,
    CONTEXT_LAYER_SCHEMA_VERSION,
    CONTEXT_NODE_SCHEMA_VERSION,
    CONTEXT_TREE_VERSION,
    LAYER_TYPES,
)

HASH_PREFIX = "sha256:"

# --------------------------------------------------------------------------- #
# Canonicalization + hashing helpers
# --------------------------------------------------------------------------- #


def canonical_json(data: Any) -> str:
    """Stable JSON representation used for local content addresses.

    Matches Trail's ``core/trails/models.py::canonical_json``: sorted keys,
    no whitespace, UTF-8 safe. Two equal-but-differently-ordered dicts
    produce the same canonical string and therefore the same content hash.
    """
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def sha256_hex(data: Any) -> str:
    """Return the hex digest of ``canonical_json(data)`` without prefix."""
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def sha256_id(data: Any) -> str:
    """Return ``sha256:<hex>`` for use as ContextLayer / ContextNode IDs."""
    return f"{HASH_PREFIX}{sha256_hex(data)}"


# --------------------------------------------------------------------------- #
# ContextLayer
# --------------------------------------------------------------------------- #


class ContextLayer(BaseModel):
    """One content-addressed slice of what the model saw.

    Identity is the sha256 of the canonical material (layer_type + content
    + completeness + capture_method + CONTEXT_TREE_VERSION). Two layers
    with the same content collapse to one record across the substrate;
    consumers dedup by ``layer_id``.

    See ``kb/plans/077-context-tree-substrate.md`` §"Layer content shapes"
    for the per-``layer_type`` content schema.
    """

    layer_id: str
    layer_type: Literal["system", "messages", "tool_registry", "runtime_state"]
    content: dict[str, Any]
    completeness: Literal["full", "approximated", "stub"]
    capture_method: Literal[
        "live_capture", "transcript_reconstruction", "hardcoded_template", "proxy"
    ]
    CONTEXT_TREE_VERSION: str = CONTEXT_TREE_VERSION

    @field_validator("layer_id")
    @classmethod
    def _valid_layer_id(cls, value: str) -> str:
        if not value.startswith(HASH_PREFIX) or len(value) != len(HASH_PREFIX) + 64:
            raise ValueError(
                f"layer_id must be 'sha256:<64-hex>', got {value!r}"
            )
        return value

    @field_validator("layer_type")
    @classmethod
    def _layer_type_in_vocab(cls, value: str) -> str:
        if value not in LAYER_TYPES:
            raise ValueError(
                f"layer_type {value!r} not in vocabulary {sorted(LAYER_TYPES)}"
            )
        return value

    @field_validator("completeness")
    @classmethod
    def _completeness_in_vocab(cls, value: str) -> str:
        if value not in COMPLETENESS_VALUES:
            raise ValueError(
                f"completeness {value!r} not in {sorted(COMPLETENESS_VALUES)}"
            )
        return value

    @field_validator("capture_method")
    @classmethod
    def _capture_method_in_vocab(cls, value: str) -> str:
        if value not in CAPTURE_METHOD_VALUES:
            raise ValueError(
                f"capture_method {value!r} not in {sorted(CAPTURE_METHOD_VALUES)}"
            )
        return value

    @model_validator(mode="after")
    def _layer_id_matches_content(self) -> "ContextLayer":
        expected = compute_layer_id(
            layer_type=self.layer_type,
            content=self.content,
            completeness=self.completeness,
            capture_method=self.capture_method,
        )
        if self.layer_id != expected:
            raise ValueError(
                f"layer_id {self.layer_id!r} does not match content hash {expected!r}; "
                "layer_id is derived, not user-supplied"
            )
        return self


def canonical_layer_material(
    *,
    layer_type: str,
    content: dict[str, Any],
    completeness: str,
    capture_method: str,
) -> dict[str, Any]:
    """Fields that define a ContextLayer's content-addressed identity."""
    return {
        "schema_version": CONTEXT_LAYER_SCHEMA_VERSION,
        "context_tree_version": CONTEXT_TREE_VERSION,
        "layer_type": layer_type,
        "completeness": completeness,
        "capture_method": capture_method,
        "content": content,
    }


def compute_layer_id(
    *,
    layer_type: str,
    content: dict[str, Any],
    completeness: str,
    capture_method: str,
) -> str:
    """Derive the sha256:<hex> ``layer_id`` for a layer's canonical material."""
    return sha256_id(
        canonical_layer_material(
            layer_type=layer_type,
            content=content,
            completeness=completeness,
            capture_method=capture_method,
        )
    )


def build_layer(
    *,
    layer_type: str,
    content: dict[str, Any],
    completeness: str,
    capture_method: str,
) -> ContextLayer:
    """Convenience constructor that computes ``layer_id`` for you."""
    return ContextLayer(
        layer_id=compute_layer_id(
            layer_type=layer_type,
            content=content,
            completeness=completeness,
            capture_method=capture_method,
        ),
        layer_type=layer_type,  # type: ignore[arg-type]
        content=content,
        completeness=completeness,  # type: ignore[arg-type]
        capture_method=capture_method,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# ContextNode
# --------------------------------------------------------------------------- #


class ContextNode(BaseModel):
    """The model's view at one specific decision point.

    ``parent_node_id`` plus ``branch_type`` encode the tree structure that
    Claude Code's JSONL ``parentUuid`` graph produces; walking parents
    from a leaf back to the root reconstructs the active path.

    ``trail_anchor_hint`` cross-references Trail's snapshot at the
    moment this node was observed; consumers can join Context Tree to
    Trail in one hop via this field plus ``Step.context_node_id``.

    See ``kb/plans/077-context-tree-substrate.md`` §"Core Primitives".
    """

    node_id: str
    parent_node_id: str | None
    branch_type: Literal[
        "root",
        "linear",
        "compaction_fork",
        "subagent_fork",
        "rewind_branch",
        "manual_branch",
    ]
    trace_id: str
    step_index: int | None = None
    transcript_uuid: str
    transcript_parent_uuid: str | None = None
    transcript_offset: int
    system_layer_id: str
    messages_layer_id: str
    tool_registry_layer_id: str
    runtime_state_layer_id: str
    subagent_session_id: str | None = None
    trail_anchor_hint: dict[str, Any] | None = None
    capture_completeness: Literal["full", "approximated", "stub"]
    CONTEXT_TREE_VERSION: str = CONTEXT_TREE_VERSION

    @field_validator("node_id")
    @classmethod
    def _valid_node_id(cls, value: str) -> str:
        if not value.startswith(HASH_PREFIX) or len(value) != len(HASH_PREFIX) + 64:
            raise ValueError(
                f"node_id must be 'sha256:<64-hex>', got {value!r}"
            )
        return value

    @field_validator("parent_node_id")
    @classmethod
    def _valid_parent_node_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.startswith(HASH_PREFIX) or len(value) != len(HASH_PREFIX) + 64:
            raise ValueError(
                f"parent_node_id must be 'sha256:<64-hex>' or None, got {value!r}"
            )
        return value

    @field_validator(
        "system_layer_id",
        "messages_layer_id",
        "tool_registry_layer_id",
        "runtime_state_layer_id",
    )
    @classmethod
    def _valid_layer_id_ref(cls, value: str) -> str:
        if not value.startswith(HASH_PREFIX) or len(value) != len(HASH_PREFIX) + 64:
            raise ValueError(
                f"layer id reference must be 'sha256:<64-hex>', got {value!r}"
            )
        return value

    @field_validator("branch_type")
    @classmethod
    def _branch_type_in_vocab(cls, value: str) -> str:
        if value not in BRANCH_TYPES:
            raise ValueError(
                f"branch_type {value!r} not in vocabulary {sorted(BRANCH_TYPES)}"
            )
        return value

    @field_validator("capture_completeness")
    @classmethod
    def _completeness_in_vocab(cls, value: str) -> str:
        if value not in COMPLETENESS_VALUES:
            raise ValueError(
                f"capture_completeness {value!r} not in {sorted(COMPLETENESS_VALUES)}"
            )
        return value

    @field_validator("transcript_offset")
    @classmethod
    def _transcript_offset_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError(f"transcript_offset must be >= 0, got {value}")
        return value

    @model_validator(mode="after")
    def _root_has_no_parent(self) -> "ContextNode":
        if self.branch_type == "root" and self.parent_node_id is not None:
            raise ValueError(
                "branch_type='root' requires parent_node_id is None"
            )
        if self.branch_type != "root" and self.parent_node_id is None:
            raise ValueError(
                f"branch_type={self.branch_type!r} requires non-null parent_node_id"
            )
        return self

    @model_validator(mode="after")
    def _subagent_fork_has_session(self) -> "ContextNode":
        if self.branch_type == "subagent_fork" and self.subagent_session_id is None:
            raise ValueError(
                "branch_type='subagent_fork' requires non-null subagent_session_id"
            )
        return self

    @model_validator(mode="after")
    def _node_id_matches_material(self) -> "ContextNode":
        expected = compute_node_id(canonical_node_material(self))
        if self.node_id != expected:
            raise ValueError(
                f"node_id {self.node_id!r} does not match canonical material {expected!r}; "
                "node_id is derived, not user-supplied"
            )
        return self


def canonical_node_material(node: "ContextNode") -> dict[str, Any]:
    """Fields that define a ContextNode's content-addressed identity.

    Unlike ``TrailEvent.canonical_event_material`` (which excludes
    transport fields like batch_id/writer), ContextNode is purely
    content; every field except the derived ``node_id`` itself is part
    of identity.
    """
    return {
        "schema_version": CONTEXT_NODE_SCHEMA_VERSION,
        "context_tree_version": node.CONTEXT_TREE_VERSION,
        "parent_node_id": node.parent_node_id,
        "branch_type": node.branch_type,
        "trace_id": node.trace_id,
        "step_index": node.step_index,
        "transcript_uuid": node.transcript_uuid,
        "transcript_parent_uuid": node.transcript_parent_uuid,
        "transcript_offset": node.transcript_offset,
        "system_layer_id": node.system_layer_id,
        "messages_layer_id": node.messages_layer_id,
        "tool_registry_layer_id": node.tool_registry_layer_id,
        "runtime_state_layer_id": node.runtime_state_layer_id,
        "subagent_session_id": node.subagent_session_id,
        "trail_anchor_hint": node.trail_anchor_hint,
        "capture_completeness": node.capture_completeness,
    }


def compute_node_id(node_material: dict[str, Any]) -> str:
    """Derive the sha256:<hex> ``node_id`` for a node's canonical material."""
    return sha256_id(node_material)


def build_node(
    *,
    parent_node_id: str | None,
    branch_type: str,
    trace_id: str,
    transcript_uuid: str,
    transcript_offset: int,
    system_layer_id: str,
    messages_layer_id: str,
    tool_registry_layer_id: str,
    runtime_state_layer_id: str,
    capture_completeness: str,
    step_index: int | None = None,
    transcript_parent_uuid: str | None = None,
    subagent_session_id: str | None = None,
    trail_anchor_hint: dict[str, Any] | None = None,
) -> ContextNode:
    """Convenience constructor that computes ``node_id`` for you.

    Builds the canonical material first, derives the node_id, then
    constructs the validated Pydantic model.
    """
    material = {
        "schema_version": CONTEXT_NODE_SCHEMA_VERSION,
        "context_tree_version": CONTEXT_TREE_VERSION,
        "parent_node_id": parent_node_id,
        "branch_type": branch_type,
        "trace_id": trace_id,
        "step_index": step_index,
        "transcript_uuid": transcript_uuid,
        "transcript_parent_uuid": transcript_parent_uuid,
        "transcript_offset": transcript_offset,
        "system_layer_id": system_layer_id,
        "messages_layer_id": messages_layer_id,
        "tool_registry_layer_id": tool_registry_layer_id,
        "runtime_state_layer_id": runtime_state_layer_id,
        "subagent_session_id": subagent_session_id,
        "trail_anchor_hint": trail_anchor_hint,
        "capture_completeness": capture_completeness,
    }
    node_id = compute_node_id(material)
    return ContextNode(
        node_id=node_id,
        parent_node_id=parent_node_id,
        branch_type=branch_type,  # type: ignore[arg-type]
        trace_id=trace_id,
        step_index=step_index,
        transcript_uuid=transcript_uuid,
        transcript_parent_uuid=transcript_parent_uuid,
        transcript_offset=transcript_offset,
        system_layer_id=system_layer_id,
        messages_layer_id=messages_layer_id,
        tool_registry_layer_id=tool_registry_layer_id,
        runtime_state_layer_id=runtime_state_layer_id,
        subagent_session_id=subagent_session_id,
        trail_anchor_hint=trail_anchor_hint,
        capture_completeness=capture_completeness,  # type: ignore[arg-type]
    )
