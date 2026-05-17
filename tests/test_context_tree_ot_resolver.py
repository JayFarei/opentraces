"""Unit tests for the ``ot://context-*`` branches of ``resolve_resource``.

One test per new URI branch, covering valid + invalid id paths.
Per the ``ctx-resolve-empty-state`` journey: unknown ids return a
standard ``limitations: ["context_layer_unavailable"]`` envelope
instead of raising.

See ``kb/plans/077-context-tree-substrate.md`` §"ot:// extensions".
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from opentraces.capture.claude_code.context_tree_capture import (
    emit_context_tree_events_from_record,
)
from opentraces.core.context_tree.contract import CONTEXT_RESOLVE_SCHEMA_VERSION
from opentraces.core.context_tree.query import build_context_tree_projection
from opentraces.core.trails.resources import resolve_resource
from opentraces_schema.models import Agent, TraceRecord


FIXTURES_DIR = Path(__file__).parent / "otbox" / "fixtures" / "sessions"


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _run_fixture(name: str, tmp_path: Path) -> Path:
    base = FIXTURES_DIR / name
    spec = importlib.util.spec_from_file_location(f"_res_{name}", base / "session.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    transcript = tmp_path / "transcript.jsonl"
    mod.run(tmp_path, transcript)
    return transcript


def _ingest_and_project(fixture_name: str, tmp_path: Path):
    _init_git_repo(tmp_path)
    transcript = _run_fixture(fixture_name, tmp_path)
    record = TraceRecord(
        trace_id=f"trace-{fixture_name}",
        session_id=f"sess-{fixture_name}",
        agent=Agent(name="claude-code", version="otbox-fake-1.0"),
        steps=[],
    )
    emit_context_tree_events_from_record(
        project_dir=tmp_path,
        final_record=record,
        transcript_path=transcript,
    )
    return build_context_tree_projection(tmp_path)


class TestContextNodeResource:

    def test_valid_node_returns_inlined_layers(self, tmp_path):
        projection = _ingest_and_project("context-tree-linear", tmp_path)
        node_ids = projection.nodes_by_trace["trace-context-tree-linear"]
        target = node_ids[0]
        uri = f"ot://context-node/sha256/{target.split(':', 1)[1]}"
        result = resolve_resource(tmp_path, uri)
        assert result["schema_version"] == CONTEXT_RESOLVE_SCHEMA_VERSION
        assert result["resource_type"] == "context_node"
        assert result["node_id"] == target
        # All four layers inlined under ``layers`` key
        assert "layers" in result
        for key in ("system_layer", "messages_layer", "tool_registry_layer", "runtime_state_layer"):
            assert key in result["layers"]

    def test_unknown_node_returns_limitations(self, tmp_path):
        _ingest_and_project("context-tree-linear", tmp_path)
        bogus_hex = "0" * 64
        uri = f"ot://context-node/sha256/{bogus_hex}"
        result = resolve_resource(tmp_path, uri)
        assert result["schema_version"] == CONTEXT_RESOLVE_SCHEMA_VERSION
        assert result["limitations"] == ["context_layer_unavailable"]
        # Must not raise
        assert "node_id" in result


class TestContextLayerResource:

    def test_valid_layer_returns_content(self, tmp_path):
        projection = _ingest_and_project("context-tree-linear", tmp_path)
        # Pick any layer that was captured
        layer_ids = list(projection.layers_by_id.keys())
        assert layer_ids, "fixture should produce at least one layer"
        target = layer_ids[0]
        uri = f"ot://context-layer/sha256/{target.split(':', 1)[1]}"
        result = resolve_resource(tmp_path, uri)
        assert result["schema_version"] == CONTEXT_RESOLVE_SCHEMA_VERSION
        assert result["resource_type"] == "context_layer"
        assert result["layer_id"] == target
        assert "content" in result
        assert "completeness" in result
        assert "capture_method" in result

    def test_unknown_layer_returns_limitations(self, tmp_path):
        _ingest_and_project("context-tree-linear", tmp_path)
        uri = "ot://context-layer/sha256/" + ("f" * 64)
        result = resolve_resource(tmp_path, uri)
        assert result["limitations"] == ["context_layer_unavailable"]


class TestContextTreeWholeResource:

    def test_whole_tree_returns_nodes_layers_active_leaf(self, tmp_path):
        _ingest_and_project("context-tree-linear", tmp_path)
        uri = "ot://context-tree/trace-context-tree-linear"
        result = resolve_resource(tmp_path, uri)
        assert result["schema_version"] == CONTEXT_RESOLVE_SCHEMA_VERSION
        assert result["resource_type"] == "context_tree"
        assert result["trace_id"] == "trace-context-tree-linear"
        for key in ("nodes", "layers", "active_leaf", "compactions", "orphans"):
            assert key in result
        assert isinstance(result["nodes"], list)
        assert isinstance(result["layers"], list)
        assert result["nodes"], "linear trace should produce at least one node"

    def test_unknown_trace_returns_limitations(self, tmp_path):
        _ingest_and_project("context-tree-linear", tmp_path)
        uri = "ot://context-tree/trace-does-not-exist"
        result = resolve_resource(tmp_path, uri)
        assert result["limitations"] == ["context_layer_unavailable"]


class TestContextTreeActivePathResource:

    def test_active_path_returns_node_list(self, tmp_path):
        _ingest_and_project("context-tree-linear", tmp_path)
        uri = "ot://context-tree/trace-context-tree-linear/active-path"
        result = resolve_resource(tmp_path, uri)
        assert result["schema_version"] == CONTEXT_RESOLVE_SCHEMA_VERSION
        assert result["resource_type"] == "context_tree_active_path"
        assert isinstance(result["active_path"], list)
        # Active path nodes carry inlined layers too
        if result["active_path"]:
            first = result["active_path"][0]
            assert "layers" in first

    def test_active_path_unknown_trace(self, tmp_path):
        _ingest_and_project("context-tree-linear", tmp_path)
        uri = "ot://context-tree/no-such-trace/active-path"
        result = resolve_resource(tmp_path, uri)
        assert result["limitations"] == ["context_layer_unavailable"]


class TestContextTreeCompactionResource:

    def test_valid_compaction_returns_loss_diff(self, tmp_path):
        _ingest_and_project("context-tree-compacted", tmp_path)
        uri = "ot://context-tree/trace-context-tree-compacted/compaction/0"
        result = resolve_resource(tmp_path, uri)
        assert result["schema_version"] == CONTEXT_RESOLVE_SCHEMA_VERSION
        assert result["resource_type"] == "context_tree_compaction"
        assert result["index"] == 0
        assert "loss_diff" in result
        # The loss_diff carries the compaction's pre/post node ids
        loss = result["loss_diff"]
        assert "pre_node_id" in loss
        assert "post_node_id" in loss

    def test_out_of_range_compaction_returns_limitations(self, tmp_path):
        _ingest_and_project("context-tree-compacted", tmp_path)
        uri = "ot://context-tree/trace-context-tree-compacted/compaction/9999"
        result = resolve_resource(tmp_path, uri)
        assert result["limitations"] == ["context_layer_unavailable"]

    def test_unknown_trace_compaction_returns_limitations(self, tmp_path):
        _ingest_and_project("context-tree-linear", tmp_path)
        # Trace exists but has no compactions
        uri = "ot://context-tree/trace-context-tree-linear/compaction/0"
        result = resolve_resource(tmp_path, uri)
        assert result["limitations"] == ["context_layer_unavailable"]

    def test_invalid_compaction_index_raises(self, tmp_path):
        _ingest_and_project("context-tree-compacted", tmp_path)
        # Non-integer index is a malformed URI, not an empty-state condition
        uri = "ot://context-tree/trace-context-tree-compacted/compaction/abc"
        with pytest.raises(ValueError):
            resolve_resource(tmp_path, uri)
