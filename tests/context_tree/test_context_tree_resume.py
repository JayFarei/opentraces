"""Unit tests for ``context_resume_packet``.

Covers happy path on a real fixture (linear and multi-turn), error
path on an unknown node id, schema_version stamping, and layer
inlining completeness.

See ``kb/plans/077-context-tree-substrate.md`` §"Resume packet".
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

from opentraces.capture.claude_code.context_tree_capture import (
    emit_context_tree_events_from_record,
)
from opentraces.core.context_tree.contract import CONTEXT_RESUME_SCHEMA_VERSION
from opentraces.core.context_tree.query import build_context_tree_projection
from opentraces.core.context_tree.resume import context_resume_packet
from opentraces_schema.models import Agent, TraceRecord


FIXTURES_DIR = Path(__file__).parent.parent / "otbox" / "fixtures" / "sessions"


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _run_fixture(name: str, tmp_path: Path) -> Path:
    base = FIXTURES_DIR / name
    spec = importlib.util.spec_from_file_location(f"_resume_{name}", base / "session.py")
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
    return build_context_tree_projection(tmp_path), transcript


class TestHappyPath:

    def test_linear_resume_packet(self, tmp_path):
        projection, _ = _ingest_and_project("context-tree-linear", tmp_path)
        node_ids = projection.nodes_by_trace.get("trace-context-tree-linear", [])
        assert node_ids, "linear fixture should produce nodes"
        target = node_ids[0]
        packet = context_resume_packet(tmp_path, target)
        assert packet["schema_version"] == CONTEXT_RESUME_SCHEMA_VERSION
        assert packet["node_id"] == target
        assert packet["trace_id"] == "trace-context-tree-linear"
        # All four layer slots are present (inlined or null)
        assert "system_layer" in packet
        assert "messages_layer" in packet
        assert "tool_registry" in packet
        assert "runtime_state" in packet
        assert "env" in packet
        assert "trail_anchor_hint" in packet
        assert "capture_limitations" in packet

    def test_layer_inlining_completeness(self, tmp_path):
        projection, _ = _ingest_and_project("context-tree-multi-turn", tmp_path)
        node_ids = projection.nodes_by_trace.get("trace-context-tree-multi-turn", [])
        assert node_ids
        target = node_ids[-1]
        packet = context_resume_packet(tmp_path, target)
        # Layer contents must be inlined for each of the four layer
        # types referenced by the node (none should be missing on the
        # happy path since the orchestrator emits all four).
        for key in ("system_layer", "messages_layer", "tool_registry", "runtime_state"):
            layer = packet[key]
            assert layer is not None, f"layer {key} should be inlined"
            assert "layer_id" in layer
            assert "layer_type" in layer
            assert "content" in layer
            assert "completeness" in layer
            assert "capture_method" in layer

    def test_messages_field_extracted(self, tmp_path):
        projection, _ = _ingest_and_project("context-tree-linear", tmp_path)
        node_ids = projection.nodes_by_trace.get("trace-context-tree-linear", [])
        target = node_ids[-1]
        packet = context_resume_packet(tmp_path, target)
        # The top-level ``messages`` field is a convenience extraction
        # from the messages layer's content. It must be a list (even
        # if empty).
        assert isinstance(packet["messages"], list)

    def test_env_block_extracted(self, tmp_path):
        projection, _ = _ingest_and_project("context-tree-linear", tmp_path)
        node_ids = projection.nodes_by_trace.get("trace-context-tree-linear", [])
        target = node_ids[0]
        packet = context_resume_packet(tmp_path, target)
        env = packet["env"]
        for key in ("cwd", "permission_mode", "model", "effort_level"):
            assert key in env, f"env should expose {key}"


class TestErrorPath:

    def test_unknown_node_returns_limitations(self, tmp_path):
        _ingest_and_project("context-tree-linear", tmp_path)
        bogus = "sha256:" + ("0" * 64)
        packet = context_resume_packet(tmp_path, bogus)
        assert packet["schema_version"] == CONTEXT_RESUME_SCHEMA_VERSION
        assert packet["node_id"] == bogus
        assert packet["error"] == "node not found"
        assert "context_layer_unavailable" in packet["limitations"]

    def test_unknown_node_does_not_raise(self, tmp_path):
        _ingest_and_project("context-tree-linear", tmp_path)
        # The contract: return a structured envelope; never raise.
        try:
            context_resume_packet(tmp_path, "sha256:" + ("a" * 64))
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"context_resume_packet must not raise on unknown id: {exc}"
            ) from exc


class TestSchemaStamping:

    def test_schema_version_present_on_success(self, tmp_path):
        projection, _ = _ingest_and_project("context-tree-linear", tmp_path)
        node_ids = projection.nodes_by_trace.get("trace-context-tree-linear", [])
        packet = context_resume_packet(tmp_path, node_ids[0])
        assert packet["schema_version"] == "opentraces.context_resume.v1"

    def test_schema_version_present_on_error(self, tmp_path):
        _ingest_and_project("context-tree-linear", tmp_path)
        packet = context_resume_packet(tmp_path, "sha256:" + ("b" * 64))
        assert packet["schema_version"] == "opentraces.context_resume.v1"
