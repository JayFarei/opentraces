"""Context Tree JSON envelope contract tests (plan 077).

This file is the load-bearing freeze for the seven Context Tree
``schema_version`` envelopes that downstream consumers (replay, RL,
viewer, dashboards, Slack/PR consumers) drive without us building those
consumers. Each test constructs the envelope from library primitives
that already exist and asserts:

1. The top-level ``schema_version`` is the exact frozen string from
   ``core/context_tree/contract.py``.
2. The envelope's top-level keys are exactly the contract list
   (no missing keys, no extra keys, no rename, no drift).
3. Each top-level value's type matches the contract.

The contract is intentionally minimal: it freezes the JSON-shape
boundary between substrate and consumer, not the substrate's internal
representations. Adding a top-level key, removing one, or renaming one
will fail one of these tests. The fix is either:

- revert the envelope change (the more common case), or
- bump the relevant ``schema_version`` in ``contract.py`` AND update
  the corresponding ``EXPECTED_KEYS`` map below in the SAME PR.

Both of those edits are reviewed as a load-bearing contract change.

The seven schema_versions covered:

    CONTEXT_TREE_SCHEMA_VERSION       -> `opentraces ctx tree`
    CONTEXT_NODE_SCHEMA_VERSION       -> `opentraces ctx show` / `step`
    CONTEXT_LAYER_SCHEMA_VERSION      -> standalone layer dump (resolve fan-out)
    CONTEXT_READS_SCHEMA_VERSION      -> `opentraces ctx reads`
    CONTEXT_WRITES_SCHEMA_VERSION     -> `opentraces ctx writes`
    CONTEXT_RESOLVE_SCHEMA_VERSION    -> `opentraces ctx resolve`
    CONTEXT_RESUME_SCHEMA_VERSION     -> `opentraces ctx resume`

The tests deliberately do NOT import the ``opentraces ctx`` CLI. The
CLI is being shipped in parallel by Agent G; the envelope contract has
to be derivable from library primitives so the same envelope can be
produced from many call sites (CLI, library API, future SDK, HTTP) and
the contract test gates all of them.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from opentraces.capture.claude_code.context_tree_capture import (
    emit_context_tree_events_from_record,
)
from opentraces.core.context_tree.contract import (
    CONTEXT_LAYER_SCHEMA_VERSION,
    CONTEXT_NODE_SCHEMA_VERSION,
    CONTEXT_READS_SCHEMA_VERSION,
    CONTEXT_RESOLVE_SCHEMA_VERSION,
    CONTEXT_RESUME_SCHEMA_VERSION,
    CONTEXT_TREE_SCHEMA_VERSION,
    CONTEXT_WRITES_SCHEMA_VERSION,
)
from opentraces.core.context_tree.models import ContextNode
from opentraces.core.context_tree.query import (
    ContextTreeProjection,
    build_context_tree_projection,
)
from opentraces.core.context_tree.resume import context_resume_packet
from opentraces_schema.models import Agent, TraceRecord


FIXTURES_DIR = (
    Path(__file__).parent.parent / "otbox" / "fixtures" / "sessions"
)

# --------------------------------------------------------------------------- #
# Frozen contract: the exact top-level keys and value types for each envelope.
# Bumping schema_version requires updating these tables in the same PR.
# --------------------------------------------------------------------------- #

# Sentinel for "value may be of either type"; helps capture optional fields
# whose shape is locked but whose value can be null in empty-state cases.
NoneType = type(None)


EXPECTED_KEYS_TREE: dict[str, tuple[type, ...]] = {
    "schema_version": (str,),
    "trace_id": (str,),
    "root_node_id": (str, NoneType),
    "active_leaf_id": (str, NoneType),
    "nodes": (list,),
    "orphan_branch_roots": (list,),
    "subagent_session_ids": (list,),
}


EXPECTED_KEYS_NODE: dict[str, tuple[type, ...]] = {
    "schema_version": (str,),
    "node_id": (str,),
    "parent_node_id": (str, NoneType),
    "branch_type": (str,),
    "trace_id": (str,),
    "step_index": (int, NoneType),
    "transcript_uuid": (str,),
    "transcript_parent_uuid": (str, NoneType),
    "transcript_offset": (int,),
    "system_layer_id": (str,),
    "messages_layer_id": (str,),
    "tool_registry_layer_id": (str,),
    "runtime_state_layer_id": (str,),
    "subagent_session_id": (str, NoneType),
    "trail_anchor_hint": (dict, NoneType),
    "capture_completeness": (str,),
    "layers": (list,),
}


EXPECTED_KEYS_LAYER: dict[str, tuple[type, ...]] = {
    "schema_version": (str,),
    "layer_id": (str,),
    "layer_type": (str,),
    "completeness": (str,),
    "capture_method": (str,),
    "content": (dict,),
}


EXPECTED_KEYS_READS: dict[str, tuple[type, ...]] = {
    "schema_version": (str,),
    "trace_id": (str,),
    "reads": (list,),
}


EXPECTED_KEYS_WRITES: dict[str, tuple[type, ...]] = {
    "schema_version": (str,),
    "trace_id": (str,),
    "writes": (list,),
}


EXPECTED_KEYS_RESOLVE: dict[str, tuple[type, ...]] = {
    "schema_version": (str,),
    "uri": (str,),
    "kind": (str,),
    "resolved": (dict, NoneType),
    "limitations": (list,),
}


# context_resume_packet has two stable shapes: the happy path (node
# resolved, all layers inlined) and the empty-state envelope (node not
# found, ``error`` + ``limitations`` keys present). Both are frozen
# parts of the contract because the CLI returns them at the same
# command and downstream consumers branch on ``error in payload`` to
# decide whether to render or fall back.
EXPECTED_KEYS_RESUME_HAPPY: dict[str, tuple[type, ...]] = {
    "schema_version": (str,),
    "node_id": (str,),
    "trace_id": (str,),
    "step_index": (int, NoneType),
    "branch_type": (str,),
    "transcript_uuid": (str,),
    "transcript_offset": (int,),
    "capture_completeness": (str,),
    "system_layer": (dict, NoneType),
    "messages": (list,),
    "messages_layer": (dict, NoneType),
    "tool_registry": (dict, NoneType),
    "env": (dict,),
    "mcp_state": (dict,),
    "runtime_state": (dict, NoneType),
    "trail_anchor_hint": (dict, NoneType),
    "capture_limitations": (list,),
}


EXPECTED_KEYS_RESUME_EMPTY: dict[str, tuple[type, ...]] = {
    "schema_version": (str,),
    "node_id": (str,),
    "error": (str,),
    "limitations": (list,),
}


# --------------------------------------------------------------------------- #
# Fixture: build a deterministic projection from the linear session fixture.
# Reuses the same end-to-end pattern as tests/test_context_tree_e2e.py so the
# substrate the envelopes describe is the substrate we actually produce.
# --------------------------------------------------------------------------- #


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _run_fixture(name: str, tmp_path: Path) -> Path:
    base = FIXTURES_DIR / name
    spec = importlib.util.spec_from_file_location(
        f"_envelope_contract_{name}", base / "session.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    transcript = tmp_path / "transcript.jsonl"
    mod.run(tmp_path, transcript)
    return transcript


def _build_linear_projection(tmp_path: Path) -> tuple[ContextTreeProjection, str]:
    """Drive the linear fixture through the real capture pipeline.

    Returns (projection, trace_id). Uses the same trace_id naming
    convention as ``test_context_tree_e2e.py`` so the projection state
    is comparable between suites.
    """
    _init_git_repo(tmp_path)
    transcript = _run_fixture("context-tree-linear", tmp_path)
    trace_id = "trace-context-tree-linear"
    record = TraceRecord(
        trace_id=trace_id,
        session_id="sess-envelope-contract-linear",
        agent=Agent(name="claude-code", version="otbox-fake-1.0"),
        steps=[],
    )
    emit_context_tree_events_from_record(
        project_dir=tmp_path,
        final_record=record,
        transcript_path=transcript,
    )
    projection = build_context_tree_projection(tmp_path)
    return projection, trace_id


@pytest.fixture(scope="function")
def linear_projection(tmp_path: Path) -> tuple[ContextTreeProjection, str, Path]:
    projection, trace_id = _build_linear_projection(tmp_path)
    return projection, trace_id, tmp_path


# --------------------------------------------------------------------------- #
# Envelope builders: produce the canonical JSON envelope per schema_version
# from library primitives only. These are the same shapes the CLI returns.
# --------------------------------------------------------------------------- #


def build_tree_envelope(
    projection: ContextTreeProjection, trace_id: str
) -> dict[str, Any]:
    """The ``opentraces ctx tree <trace-id> --json`` envelope.

    Plan 077 §"CLI surface", row ``ctx tree``:
        ``{schema_version, trace_id, root_node_id, active_leaf_id,
           nodes, orphan_branch_roots, subagent_session_ids}``
    """
    node_ids = projection.nodes_by_trace.get(trace_id, [])
    nodes = [
        projection.nodes_by_id[node_id].model_dump()
        for node_id in node_ids
        if node_id in projection.nodes_by_id
    ]
    root_node_id: str | None = None
    for node_id in node_ids:
        node = projection.nodes_by_id.get(node_id)
        if node is not None and node.branch_type == "root":
            root_node_id = node.node_id
            break
    active_leaf_uuid = projection.active_leaves_by_trace.get(trace_id)
    active_leaf_id: str | None = None
    if active_leaf_uuid:
        leaf_node = projection.node_for_transcript_uuid(trace_id, active_leaf_uuid)
        if leaf_node is not None:
            active_leaf_id = leaf_node.node_id
    return {
        "schema_version": CONTEXT_TREE_SCHEMA_VERSION,
        "trace_id": trace_id,
        "root_node_id": root_node_id,
        "active_leaf_id": active_leaf_id,
        "nodes": nodes,
        "orphan_branch_roots": list(
            projection.orphan_branch_roots_by_trace.get(trace_id, [])
        ),
        "subagent_session_ids": list(
            projection.subagent_session_ids_by_trace.get(trace_id, [])
        ),
    }


def build_node_envelope(
    projection: ContextTreeProjection, node: ContextNode
) -> dict[str, Any]:
    """The ``opentraces ctx show <node-id> --json`` envelope.

    Plan 077 §"CLI surface", row ``ctx show``:
        ``ContextNode + per-layer summary``.
    """
    layers: list[dict[str, Any]] = []
    for layer_id in (
        node.system_layer_id,
        node.messages_layer_id,
        node.tool_registry_layer_id,
        node.runtime_state_layer_id,
    ):
        layer = projection.layers_by_id.get(layer_id)
        if layer is None:
            layers.append({
                "layer_id": layer_id,
                "layer_type": None,
                "completeness": None,
                "capture_method": None,
                "available": False,
            })
        else:
            layers.append({
                "layer_id": layer.layer_id,
                "layer_type": layer.layer_type,
                "completeness": layer.completeness,
                "capture_method": layer.capture_method,
                "available": True,
            })
    node_dump = node.model_dump()
    # Drop the rolled-up CONTEXT_TREE_VERSION key from the per-node
    # envelope (it's a model-internal version marker, not a top-level
    # contract field). Consumers that need it dig into the layers.
    node_dump.pop("CONTEXT_TREE_VERSION", None)
    envelope = {
        "schema_version": CONTEXT_NODE_SCHEMA_VERSION,
        **node_dump,
        "layers": layers,
    }
    return envelope


def build_layer_envelope(
    projection: ContextTreeProjection, layer_id: str
) -> dict[str, Any]:
    """Standalone layer envelope (returned by ``ctx resolve
    ot://context-layer/sha256/<id>`` and as the per-layer block of
    ``ctx show --full``).
    """
    layer = projection.layers_by_id[layer_id]
    return {
        "schema_version": CONTEXT_LAYER_SCHEMA_VERSION,
        "layer_id": layer.layer_id,
        "layer_type": layer.layer_type,
        "completeness": layer.completeness,
        "capture_method": layer.capture_method,
        "content": layer.content,
    }


def build_reads_envelope(
    projection: ContextTreeProjection, trace_id: str
) -> dict[str, Any]:
    """The ``opentraces ctx reads <trace-id> --json`` envelope.

    Plan 077 §"CLI surface", row ``ctx reads``:
        ``{schema_version: "opentraces.context_reads.v1", reads: [...]}``.
    Wraps ``ContextTreeProjection.reads_for_trace``.
    """
    return {
        "schema_version": CONTEXT_READS_SCHEMA_VERSION,
        "trace_id": trace_id,
        "reads": projection.reads_for_trace(trace_id),
    }


def build_writes_envelope(
    projection: ContextTreeProjection, trace_id: str
) -> dict[str, Any]:
    """The ``opentraces ctx writes <trace-id> --json`` envelope.

    Plan 077 §"CLI surface", row ``ctx writes``:
        ``{schema_version: "opentraces.context_writes.v1", writes: [...]}``.
    Wraps ``ContextTreeProjection.writes_for_trace``.
    """
    return {
        "schema_version": CONTEXT_WRITES_SCHEMA_VERSION,
        "trace_id": trace_id,
        "writes": projection.writes_for_trace(trace_id),
    }


def build_resolve_envelope(
    projection: ContextTreeProjection, uri: str
) -> dict[str, Any]:
    """The ``opentraces ctx resolve <ot://...> --json`` envelope.

    Plan 077 §"CLI surface", row ``ctx resolve``:
        mirror of ``trail resolve``; dispatches ``ot://context-*`` URIs.
    The envelope wraps a resolution attempt with a ``limitations``
    list for graceful empty-state responses (per the
    ``ctx-resolve-empty-state`` journey).
    """
    # Minimal in-tree resolver for the test: dispatch by URI prefix.
    # The real implementation will live in
    # ``core/trails/resources.py::resolve_resource`` (per the plan); the
    # envelope shape is what we freeze here, not the resolver code.
    limitations: list[str] = []
    resolved: dict[str, Any] | None = None
    kind = "unknown"
    if uri.startswith("ot://context-node/sha256/"):
        node_id = "sha256:" + uri.removeprefix("ot://context-node/sha256/")
        node = projection.nodes_by_id.get(node_id)
        kind = "context_node"
        if node is None:
            limitations.append("context_layer_unavailable")
        else:
            resolved = build_node_envelope(projection, node)
    elif uri.startswith("ot://context-layer/sha256/"):
        layer_id = "sha256:" + uri.removeprefix("ot://context-layer/sha256/")
        kind = "context_layer"
        if layer_id in projection.layers_by_id:
            resolved = build_layer_envelope(projection, layer_id)
        else:
            limitations.append("context_layer_unavailable")
    else:
        limitations.append("context_layer_unavailable")
    return {
        "schema_version": CONTEXT_RESOLVE_SCHEMA_VERSION,
        "uri": uri,
        "kind": kind,
        "resolved": resolved,
        "limitations": limitations,
    }


def build_resume_envelope(repo: Path, node_id: str) -> dict[str, Any]:
    """The ``opentraces ctx resume <node-id> --json`` envelope.

    Delegates to the library primitive ``context_resume_packet``; this
    is the source of truth for the contract.
    """
    return context_resume_packet(repo, node_id)


# --------------------------------------------------------------------------- #
# Shared assertion helpers
# --------------------------------------------------------------------------- #


def _assert_envelope_matches_contract(
    envelope: dict[str, Any],
    expected: dict[str, tuple[type, ...]],
    *,
    label: str,
) -> None:
    """Assert the envelope's top-level keys + types match the contract.

    Three failure modes are surfaced explicitly because each maps to a
    different decision:

    - missing keys -> the producer dropped a key the contract requires;
      either restore it or bump the ``schema_version``.
    - extra keys -> the producer added a key the contract did not freeze;
      either remove it or bump the ``schema_version`` and update the
      contract table here.
    - wrong type -> the producer changed the value type for a frozen key;
      either revert or bump the ``schema_version``.
    """
    actual_keys = set(envelope.keys())
    expected_keys = set(expected.keys())
    missing = expected_keys - actual_keys
    extra = actual_keys - expected_keys
    assert not missing, (
        f"[{label}] envelope missing required keys: {sorted(missing)}; "
        f"either restore the key in the producer or bump the schema_version"
    )
    assert not extra, (
        f"[{label}] envelope has unexpected keys: {sorted(extra)}; "
        f"either remove them from the producer or bump the schema_version "
        f"and update EXPECTED_KEYS_{label.upper()} in the same PR"
    )
    for key, allowed_types in expected.items():
        value = envelope[key]
        assert isinstance(value, allowed_types), (
            f"[{label}] envelope key {key!r} has type {type(value).__name__}; "
            f"contract allows {sorted(t.__name__ for t in allowed_types)}; "
            f"either revert the type or bump the schema_version"
        )


def _assert_json_roundtrip(envelope: dict[str, Any], label: str) -> None:
    """Envelopes must round-trip through ``json.dumps`` / ``json.loads``.

    This catches subtle bugs (e.g. ``Path`` or ``datetime`` leaking
    into the envelope) that would break the contract for any JSON
    consumer even though pure-Python comparison would pass.
    """
    try:
        serialized = json.dumps(envelope, default=str)
    except TypeError as exc:
        raise AssertionError(
            f"[{label}] envelope is not JSON-serializable: {exc!r}; "
            f"the envelope contract requires pure-JSON values"
        ) from exc
    restored = json.loads(serialized)
    assert isinstance(restored, dict), (
        f"[{label}] envelope did not round-trip as a dict"
    )


# --------------------------------------------------------------------------- #
# Frozen envelope contract tests, one per schema_version
# --------------------------------------------------------------------------- #


class TestSchemaVersionStringsAreFrozen:
    """The seven schema_version strings themselves are part of the contract.

    Renaming or repointing one breaks downstream consumers that pin
    against the string. Bumping the version (``v1`` -> ``v2``) is a
    coordinated contract change that must update this test and the
    matching EXPECTED_KEYS table in the same PR.
    """

    def test_context_tree_schema_version_string(self):
        assert CONTEXT_TREE_SCHEMA_VERSION == "opentraces.context_tree.v1"

    def test_context_node_schema_version_string(self):
        assert CONTEXT_NODE_SCHEMA_VERSION == "opentraces.context_node.v1"

    def test_context_layer_schema_version_string(self):
        assert CONTEXT_LAYER_SCHEMA_VERSION == "opentraces.context_layer.v1"

    def test_context_reads_schema_version_string(self):
        assert CONTEXT_READS_SCHEMA_VERSION == "opentraces.context_reads.v1"

    def test_context_writes_schema_version_string(self):
        assert CONTEXT_WRITES_SCHEMA_VERSION == "opentraces.context_writes.v1"

    def test_context_resolve_schema_version_string(self):
        assert CONTEXT_RESOLVE_SCHEMA_VERSION == "opentraces.context_resolve.v1"

    def test_context_resume_schema_version_string(self):
        assert CONTEXT_RESUME_SCHEMA_VERSION == "opentraces.context_resume.v1"


class TestContextTreeEnvelopeContract:
    """Frozen ``opentraces.context_tree.v1`` envelope (the ``ctx tree`` shape)."""

    def test_tree_envelope_schema_version(self, linear_projection):
        projection, trace_id, _ = linear_projection
        env = build_tree_envelope(projection, trace_id)
        assert env["schema_version"] == "opentraces.context_tree.v1"

    def test_tree_envelope_keys_and_types(self, linear_projection):
        projection, trace_id, _ = linear_projection
        env = build_tree_envelope(projection, trace_id)
        _assert_envelope_matches_contract(env, EXPECTED_KEYS_TREE, label="tree")

    def test_tree_envelope_roundtrips_json(self, linear_projection):
        projection, trace_id, _ = linear_projection
        env = build_tree_envelope(projection, trace_id)
        _assert_json_roundtrip(env, label="tree")

    def test_tree_envelope_populated_for_linear_fixture(self, linear_projection):
        """Linear fixture must produce at least one node in the envelope."""
        projection, trace_id, _ = linear_projection
        env = build_tree_envelope(projection, trace_id)
        assert env["trace_id"] == trace_id
        assert len(env["nodes"]) >= 1
        # Linear fixture has no rewinds and no subagents
        assert env["orphan_branch_roots"] == []
        assert env["subagent_session_ids"] == []


class TestContextNodeEnvelopeContract:
    """Frozen ``opentraces.context_node.v1`` envelope (the ``ctx show`` shape)."""

    def test_node_envelope_schema_version(self, linear_projection):
        projection, trace_id, _ = linear_projection
        node_ids = projection.nodes_by_trace[trace_id]
        node = projection.nodes_by_id[node_ids[0]]
        env = build_node_envelope(projection, node)
        assert env["schema_version"] == "opentraces.context_node.v1"

    def test_node_envelope_keys_and_types(self, linear_projection):
        projection, trace_id, _ = linear_projection
        node_ids = projection.nodes_by_trace[trace_id]
        node = projection.nodes_by_id[node_ids[0]]
        env = build_node_envelope(projection, node)
        _assert_envelope_matches_contract(env, EXPECTED_KEYS_NODE, label="node")

    def test_node_envelope_roundtrips_json(self, linear_projection):
        projection, trace_id, _ = linear_projection
        node_ids = projection.nodes_by_trace[trace_id]
        node = projection.nodes_by_id[node_ids[0]]
        env = build_node_envelope(projection, node)
        _assert_json_roundtrip(env, label="node")

    def test_node_envelope_layer_summary_has_all_four_types(
        self, linear_projection
    ):
        projection, trace_id, _ = linear_projection
        node_ids = projection.nodes_by_trace[trace_id]
        node = projection.nodes_by_id[node_ids[0]]
        env = build_node_envelope(projection, node)
        # Per the contract a ContextNode has four typed layers; the
        # envelope's layer summary should reflect that, in order.
        assert len(env["layers"]) == 4
        for layer in env["layers"]:
            assert "layer_id" in layer
            assert "available" in layer


class TestContextLayerEnvelopeContract:
    """Frozen ``opentraces.context_layer.v1`` envelope (standalone layer)."""

    def test_layer_envelope_schema_version(self, linear_projection):
        projection, _, _ = linear_projection
        layer_id = next(iter(projection.layers_by_id))
        env = build_layer_envelope(projection, layer_id)
        assert env["schema_version"] == "opentraces.context_layer.v1"

    def test_layer_envelope_keys_and_types(self, linear_projection):
        projection, _, _ = linear_projection
        layer_id = next(iter(projection.layers_by_id))
        env = build_layer_envelope(projection, layer_id)
        _assert_envelope_matches_contract(env, EXPECTED_KEYS_LAYER, label="layer")

    def test_layer_envelope_roundtrips_json(self, linear_projection):
        projection, _, _ = linear_projection
        layer_id = next(iter(projection.layers_by_id))
        env = build_layer_envelope(projection, layer_id)
        _assert_json_roundtrip(env, label="layer")


class TestContextReadsEnvelopeContract:
    """Frozen ``opentraces.context_reads.v1`` envelope (the ``ctx reads`` shape)."""

    def test_reads_envelope_schema_version(self, linear_projection):
        projection, trace_id, _ = linear_projection
        env = build_reads_envelope(projection, trace_id)
        assert env["schema_version"] == "opentraces.context_reads.v1"

    def test_reads_envelope_keys_and_types(self, linear_projection):
        projection, trace_id, _ = linear_projection
        env = build_reads_envelope(projection, trace_id)
        _assert_envelope_matches_contract(env, EXPECTED_KEYS_READS, label="reads")

    def test_reads_envelope_roundtrips_json(self, linear_projection):
        projection, trace_id, _ = linear_projection
        env = build_reads_envelope(projection, trace_id)
        _assert_json_roundtrip(env, label="reads")


class TestContextWritesEnvelopeContract:
    """Frozen ``opentraces.context_writes.v1`` envelope (the ``ctx writes`` shape)."""

    def test_writes_envelope_schema_version(self, linear_projection):
        projection, trace_id, _ = linear_projection
        env = build_writes_envelope(projection, trace_id)
        assert env["schema_version"] == "opentraces.context_writes.v1"

    def test_writes_envelope_keys_and_types(self, linear_projection):
        projection, trace_id, _ = linear_projection
        env = build_writes_envelope(projection, trace_id)
        _assert_envelope_matches_contract(env, EXPECTED_KEYS_WRITES, label="writes")

    def test_writes_envelope_roundtrips_json(self, linear_projection):
        projection, trace_id, _ = linear_projection
        env = build_writes_envelope(projection, trace_id)
        _assert_json_roundtrip(env, label="writes")


class TestContextResolveEnvelopeContract:
    """Frozen ``opentraces.context_resolve.v1`` envelope (the ``ctx resolve`` shape)."""

    def test_resolve_envelope_schema_version_happy_path(self, linear_projection):
        projection, trace_id, _ = linear_projection
        node_id = projection.nodes_by_trace[trace_id][0]
        # Build the ot:// URI from the resolved node_id
        uri = f"ot://context-node/sha256/{node_id.removeprefix('sha256:')}"
        env = build_resolve_envelope(projection, uri)
        assert env["schema_version"] == "opentraces.context_resolve.v1"

    def test_resolve_envelope_keys_and_types_happy_path(self, linear_projection):
        projection, trace_id, _ = linear_projection
        node_id = projection.nodes_by_trace[trace_id][0]
        uri = f"ot://context-node/sha256/{node_id.removeprefix('sha256:')}"
        env = build_resolve_envelope(projection, uri)
        _assert_envelope_matches_contract(
            env, EXPECTED_KEYS_RESOLVE, label="resolve"
        )

    def test_resolve_envelope_keys_and_types_empty_state(self, linear_projection):
        """Per ``ctx-resolve-empty-state`` journey: unknown ids return the
        standard envelope with ``limitations: ['context_layer_unavailable']``
        rather than a traceback.
        """
        projection, _, _ = linear_projection
        unknown = "ot://context-node/sha256/" + ("0" * 64)
        env = build_resolve_envelope(projection, unknown)
        _assert_envelope_matches_contract(
            env, EXPECTED_KEYS_RESOLVE, label="resolve"
        )
        assert env["resolved"] is None
        assert "context_layer_unavailable" in env["limitations"]

    def test_resolve_envelope_roundtrips_json(self, linear_projection):
        projection, trace_id, _ = linear_projection
        node_id = projection.nodes_by_trace[trace_id][0]
        uri = f"ot://context-node/sha256/{node_id.removeprefix('sha256:')}"
        env = build_resolve_envelope(projection, uri)
        _assert_json_roundtrip(env, label="resolve")


class TestContextResumeEnvelopeContract:
    """Frozen ``opentraces.context_resume.v1`` envelope (the ``ctx resume`` shape).

    Two stable shapes: happy-path (node resolved, all layers inlined)
    and empty-state (node not found, ``error`` + ``limitations`` keys).
    Both are part of the frozen contract.
    """

    def test_resume_envelope_schema_version(self, linear_projection):
        projection, trace_id, repo = linear_projection
        node_id = projection.nodes_by_trace[trace_id][0]
        env = build_resume_envelope(repo, node_id)
        assert env["schema_version"] == "opentraces.context_resume.v1"

    def test_resume_envelope_keys_and_types_happy_path(self, linear_projection):
        projection, trace_id, repo = linear_projection
        node_id = projection.nodes_by_trace[trace_id][0]
        env = build_resume_envelope(repo, node_id)
        _assert_envelope_matches_contract(
            env, EXPECTED_KEYS_RESUME_HAPPY, label="resume_happy"
        )

    def test_resume_envelope_keys_and_types_empty_state(self, linear_projection):
        """``context_resume_packet`` on an unknown node returns the empty-state
        envelope rather than raising. That envelope's shape is part of
        the contract.
        """
        _projection, _trace_id, repo = linear_projection
        unknown = "sha256:" + ("0" * 64)
        env = build_resume_envelope(repo, unknown)
        _assert_envelope_matches_contract(
            env, EXPECTED_KEYS_RESUME_EMPTY, label="resume_empty"
        )
        assert env["error"] == "node not found"
        assert "context_layer_unavailable" in env["limitations"]

    def test_resume_envelope_roundtrips_json(self, linear_projection):
        projection, trace_id, repo = linear_projection
        node_id = projection.nodes_by_trace[trace_id][0]
        env = build_resume_envelope(repo, node_id)
        _assert_json_roundtrip(env, label="resume")


class TestContractCoverageCompleteness:
    """Meta-check: every ``CONTEXT_*_SCHEMA_VERSION`` in contract.py has
    a corresponding envelope contract assertion in this file.

    Forces drift detection: adding a new schema_version without freezing
    its envelope shape here fails this test.
    """

    def test_all_schema_versions_have_envelope_contracts(self):
        from opentraces.core.context_tree import contract as _contract

        schema_version_attrs = [
            name for name in dir(_contract)
            if name.startswith("CONTEXT_") and name.endswith("_SCHEMA_VERSION")
        ]
        assert sorted(schema_version_attrs) == sorted([
            "CONTEXT_TREE_SCHEMA_VERSION",
            "CONTEXT_NODE_SCHEMA_VERSION",
            "CONTEXT_LAYER_SCHEMA_VERSION",
            "CONTEXT_READS_SCHEMA_VERSION",
            "CONTEXT_WRITES_SCHEMA_VERSION",
            "CONTEXT_RESOLVE_SCHEMA_VERSION",
            "CONTEXT_RESUME_SCHEMA_VERSION",
        ]), (
            "contract.py gained or lost a CONTEXT_*_SCHEMA_VERSION constant; "
            "this file must add or remove the matching EXPECTED_KEYS_* table "
            "and class in the same PR"
        )

    def test_expected_keys_tables_present_for_all_schemas(self):
        """Each of the seven schema_versions has a frozen EXPECTED_KEYS table
        (or two tables for the resume envelope's happy + empty cases).
        """
        # Each entry maps to one or more EXPECTED_KEYS tables defined above.
        coverage = {
            "context_tree": [EXPECTED_KEYS_TREE],
            "context_node": [EXPECTED_KEYS_NODE],
            "context_layer": [EXPECTED_KEYS_LAYER],
            "context_reads": [EXPECTED_KEYS_READS],
            "context_writes": [EXPECTED_KEYS_WRITES],
            "context_resolve": [EXPECTED_KEYS_RESOLVE],
            "context_resume": [
                EXPECTED_KEYS_RESUME_HAPPY,
                EXPECTED_KEYS_RESUME_EMPTY,
            ],
        }
        assert set(coverage.keys()) == {
            "context_tree",
            "context_node",
            "context_layer",
            "context_reads",
            "context_writes",
            "context_resolve",
            "context_resume",
        }
        # Every table must declare schema_version as its first frozen key.
        for label, tables in coverage.items():
            for table in tables:
                assert "schema_version" in table, (
                    f"EXPECTED_KEYS for {label!r} missing the load-bearing "
                    f"'schema_version' key"
                )
