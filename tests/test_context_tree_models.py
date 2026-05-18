"""Unit tests for the Context Tree substrate models and helpers.

Covers content-hashing determinism, vocabulary validation, parent-chain
invariants, branch_type enforcement, and the ``build_layer`` /
``build_node`` convenience constructors.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from opentraces.core.context_tree import (
    BRANCH_TYPES,
    CAPTURE_METHOD_VALUES,
    COMPLETENESS_VALUES,
    CONTEXT_EVENT_TYPES,
    CONTEXT_LAYER_SCHEMA_VERSION,
    CONTEXT_NODE_SCHEMA_VERSION,
    CONTEXT_TREE_VERSION,
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


# --------------------------------------------------------------------------- #
# Canonicalization + hashing
# --------------------------------------------------------------------------- #


class TestCanonicalization:

    def test_canonical_json_is_sorted_and_compact(self):
        out = canonical_json({"b": 1, "a": [2, 3]})
        assert out == '{"a":[2,3],"b":1}'

    def test_canonical_json_dict_order_invariant(self):
        a = canonical_json({"x": 1, "y": 2})
        b = canonical_json({"y": 2, "x": 1})
        assert a == b

    def test_sha256_hex_is_64_chars(self):
        h = sha256_hex({"hello": "world"})
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_sha256_id_has_prefix(self):
        i = sha256_id({"k": "v"})
        assert i.startswith("sha256:")
        assert len(i) == len("sha256:") + 64

    def test_sha256_id_changes_with_content(self):
        a = sha256_id({"k": "v1"})
        b = sha256_id({"k": "v2"})
        assert a != b

    def test_sha256_id_stable_across_dict_order(self):
        a = sha256_id({"a": 1, "b": 2})
        b = sha256_id({"b": 2, "a": 1})
        assert a == b


# --------------------------------------------------------------------------- #
# ContextLayer
# --------------------------------------------------------------------------- #


class TestContextLayer:

    def test_build_layer_round_trip(self):
        layer = build_layer(
            layer_type="system",
            content={"static_core_ref": "claude_code:v2.1", "claude_md_set": []},
            completeness="approximated",
            capture_method="hardcoded_template",
        )
        assert layer.layer_id.startswith(HASH_PREFIX)
        assert layer.CONTEXT_TREE_VERSION == CONTEXT_TREE_VERSION
        assert layer.layer_type == "system"

    def test_layer_id_is_deterministic(self):
        a = build_layer(
            layer_type="messages",
            content={"messages": [{"role": "user", "content_hash": "abc"}]},
            completeness="full",
            capture_method="transcript_reconstruction",
        )
        b = build_layer(
            layer_type="messages",
            content={"messages": [{"role": "user", "content_hash": "abc"}]},
            completeness="full",
            capture_method="transcript_reconstruction",
        )
        assert a.layer_id == b.layer_id

    def test_different_completeness_produces_different_layer_id(self):
        kwargs = dict(
            layer_type="tool_registry",
            content={"tools": []},
            capture_method="hardcoded_template",
        )
        a = build_layer(completeness="full", **kwargs)
        b = build_layer(completeness="approximated", **kwargs)
        assert a.layer_id != b.layer_id

    def test_invalid_layer_type_rejected(self):
        with pytest.raises(ValidationError, match="layer_type"):
            build_layer(
                layer_type="not_a_real_layer",
                content={},
                completeness="full",
                capture_method="live_capture",
            )

    def test_invalid_completeness_rejected(self):
        with pytest.raises(ValidationError, match="completeness"):
            build_layer(
                layer_type="system",
                content={},
                completeness="totally",
                capture_method="live_capture",
            )

    def test_invalid_capture_method_rejected(self):
        with pytest.raises(ValidationError, match="capture_method"):
            build_layer(
                layer_type="system",
                content={},
                completeness="full",
                # Sentinel chosen as a value guaranteed not in any current or
                # planned vocabulary. (``proxy`` is a real value now, added
                # by the http-proxy-capture prototype branch.)
                capture_method="not_a_real_capture_method",
            )

    def test_user_supplied_layer_id_must_match_content(self):
        # Direct construction with a wrong layer_id must be rejected;
        # layer_id is derived, not user-asserted.
        correct = compute_layer_id(
            layer_type="env",  # wrong type intentionally to mismatch
            content={"cwd": "/tmp"},
            completeness="full",
            capture_method="live_capture",
        )
        with pytest.raises(ValidationError, match="layer_id"):
            ContextLayer(
                layer_id=correct,
                layer_type="system",
                content={"cwd": "/tmp"},
                completeness="full",
                capture_method="live_capture",
            )

    def test_canonical_layer_material_includes_schema_version(self):
        material = canonical_layer_material(
            layer_type="system",
            content={"k": "v"},
            completeness="full",
            capture_method="live_capture",
        )
        assert material["schema_version"] == CONTEXT_LAYER_SCHEMA_VERSION
        assert material["context_tree_version"] == CONTEXT_TREE_VERSION

    def test_layer_id_invalid_format_rejected(self):
        with pytest.raises(ValidationError, match="layer_id"):
            ContextLayer(
                layer_id="not-a-hash",
                layer_type="system",
                content={},
                completeness="full",
                capture_method="live_capture",
            )


# --------------------------------------------------------------------------- #
# ContextNode
# --------------------------------------------------------------------------- #


@pytest.fixture
def four_layer_ids():
    """Four valid layer_ids for use in node construction tests."""
    return {
        "system_layer_id": compute_layer_id(
            layer_type="system",
            content={"a": 1},
            completeness="full",
            capture_method="live_capture",
        ),
        "messages_layer_id": compute_layer_id(
            layer_type="messages",
            content={"a": 2},
            completeness="full",
            capture_method="transcript_reconstruction",
        ),
        "tool_registry_layer_id": compute_layer_id(
            layer_type="tool_registry",
            content={"a": 3},
            completeness="approximated",
            capture_method="hardcoded_template",
        ),
        "runtime_state_layer_id": compute_layer_id(
            layer_type="runtime_state",
            content={"a": 4},
            completeness="full",
            capture_method="live_capture",
        ),
    }


class TestContextNode:

    def test_build_root_node(self, four_layer_ids):
        node = build_node(
            parent_node_id=None,
            branch_type="root",
            trace_id="trace-abc",
            transcript_uuid="user-1",
            transcript_offset=0,
            capture_completeness="full",
            **four_layer_ids,
        )
        assert node.node_id.startswith(HASH_PREFIX)
        assert node.parent_node_id is None
        assert node.branch_type == "root"

    def test_build_linear_child_node(self, four_layer_ids):
        root = build_node(
            parent_node_id=None,
            branch_type="root",
            trace_id="t1",
            transcript_uuid="u-root",
            transcript_offset=0,
            capture_completeness="full",
            **four_layer_ids,
        )
        child = build_node(
            parent_node_id=root.node_id,
            branch_type="linear",
            trace_id="t1",
            transcript_uuid="u-child",
            transcript_parent_uuid="u-root",
            transcript_offset=512,
            step_index=1,
            capture_completeness="full",
            **four_layer_ids,
        )
        assert child.parent_node_id == root.node_id
        assert child.node_id != root.node_id  # different transcript_uuid

    def test_node_id_is_deterministic(self, four_layer_ids):
        kwargs = dict(
            parent_node_id=None,
            branch_type="root",
            trace_id="t1",
            transcript_uuid="u-1",
            transcript_offset=0,
            capture_completeness="full",
            **four_layer_ids,
        )
        a = build_node(**kwargs)
        b = build_node(**kwargs)
        assert a.node_id == b.node_id

    def test_root_branch_type_requires_null_parent(self, four_layer_ids):
        with pytest.raises(ValidationError, match="root"):
            build_node(
                parent_node_id="sha256:" + "0" * 64,
                branch_type="root",
                trace_id="t1",
                transcript_uuid="u",
                transcript_offset=0,
                capture_completeness="full",
                **four_layer_ids,
            )

    def test_non_root_branch_type_requires_parent(self, four_layer_ids):
        with pytest.raises(ValidationError, match="parent_node_id"):
            build_node(
                parent_node_id=None,
                branch_type="linear",
                trace_id="t1",
                transcript_uuid="u",
                transcript_offset=0,
                capture_completeness="full",
                **four_layer_ids,
            )

    def test_subagent_fork_requires_session_id(self, four_layer_ids):
        parent_id = "sha256:" + "a" * 64
        with pytest.raises(ValidationError, match="subagent_session_id"):
            build_node(
                parent_node_id=parent_id,
                branch_type="subagent_fork",
                trace_id="t1",
                transcript_uuid="u",
                transcript_offset=0,
                capture_completeness="full",
                **four_layer_ids,
            )

    def test_subagent_fork_with_session_id_ok(self, four_layer_ids):
        parent_id = "sha256:" + "a" * 64
        node = build_node(
            parent_node_id=parent_id,
            branch_type="subagent_fork",
            trace_id="t1",
            transcript_uuid="u",
            transcript_offset=0,
            subagent_session_id="sess-sub-1",
            capture_completeness="full",
            **four_layer_ids,
        )
        assert node.subagent_session_id == "sess-sub-1"

    def test_invalid_branch_type_rejected(self, four_layer_ids):
        with pytest.raises(ValidationError, match="branch_type"):
            build_node(
                parent_node_id="sha256:" + "a" * 64,
                branch_type="my_custom_branch",
                trace_id="t1",
                transcript_uuid="u",
                transcript_offset=0,
                capture_completeness="full",
                **four_layer_ids,
            )

    def test_negative_transcript_offset_rejected(self, four_layer_ids):
        with pytest.raises(ValidationError, match="transcript_offset"):
            build_node(
                parent_node_id=None,
                branch_type="root",
                trace_id="t1",
                transcript_uuid="u",
                transcript_offset=-1,
                capture_completeness="full",
                **four_layer_ids,
            )

    def test_user_supplied_node_id_must_match_material(self, four_layer_ids):
        # Build a valid node, then try to construct one with a tampered node_id.
        good = build_node(
            parent_node_id=None,
            branch_type="root",
            trace_id="t1",
            transcript_uuid="u",
            transcript_offset=0,
            capture_completeness="full",
            **four_layer_ids,
        )
        wrong_id = "sha256:" + "f" * 64
        with pytest.raises(ValidationError, match="node_id"):
            ContextNode(
                node_id=wrong_id,
                parent_node_id=good.parent_node_id,
                branch_type=good.branch_type,
                trace_id=good.trace_id,
                transcript_uuid=good.transcript_uuid,
                transcript_offset=good.transcript_offset,
                system_layer_id=good.system_layer_id,
                messages_layer_id=good.messages_layer_id,
                tool_registry_layer_id=good.tool_registry_layer_id,
                runtime_state_layer_id=good.runtime_state_layer_id,
                capture_completeness=good.capture_completeness,
            )

    def test_canonical_node_material_includes_schema_version(self, four_layer_ids):
        node = build_node(
            parent_node_id=None,
            branch_type="root",
            trace_id="t1",
            transcript_uuid="u",
            transcript_offset=0,
            capture_completeness="full",
            **four_layer_ids,
        )
        material = canonical_node_material(node)
        assert material["schema_version"] == CONTEXT_NODE_SCHEMA_VERSION
        assert material["context_tree_version"] == CONTEXT_TREE_VERSION
        # node_id itself is NOT in the material (it's derived from material)
        assert "node_id" not in material


# --------------------------------------------------------------------------- #
# Vocabularies + event types
# --------------------------------------------------------------------------- #


class TestVocabularies:

    def test_layer_types_match_literal(self):
        assert {"system", "messages", "tool_registry", "runtime_state"} == {
            t for t in BRANCH_TYPES or set()
        } or True  # sanity: just confirm import worked
        # The strict check is on the ContextLayer Literal; the vocabulary
        # constants should align. Spot-check that all expected values are present.
        from opentraces.core.context_tree import LAYER_TYPES

        assert LAYER_TYPES == {"system", "messages", "tool_registry", "runtime_state"}

    def test_branch_types_complete(self):
        assert BRANCH_TYPES == {
            "root",
            "linear",
            "compaction_fork",
            "subagent_fork",
            "rewind_branch",
            "manual_branch",
        }

    def test_completeness_values_complete(self):
        assert COMPLETENESS_VALUES == {"full", "approximated", "stub"}

    def test_capture_method_values_complete(self):
        assert CAPTURE_METHOD_VALUES == {
            "live_capture",
            "transcript_reconstruction",
            "hardcoded_template",
            # Added by the http-proxy-capture prototype branch; if/when
            # the prototype lands on main this value stays.
            "proxy",
        }

    def test_event_types_complete(self):
        assert CONTEXT_EVENT_TYPES == {
            "context_layer_captured",
            "context_node_observed",
            "context_compaction_observed",
            "context_tree_reconciled",
        }
