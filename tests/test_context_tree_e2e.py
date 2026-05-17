"""End-to-end Phase 3 test: orchestrator → event log → query projection.

These tests are the meat of "the substrate actually works": they
initialize a real git repo, run the orchestrator, append events to
``refs/opentraces/local/events/v1``, then read them back through the
``ContextTreeProjection`` and assert the round trip preserves
identity. Without these, Phase 3's CLI verbs are flying blind.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from opentraces.capture.claude_code.context_tree_capture import (
    emit_context_tree_events_from_record,
)
from opentraces.core.context_tree.query import (
    ContextTreeProjection,
    build_context_tree_projection,
    context_tree_summary,
)
from opentraces_schema.models import Agent, TraceRecord


FIXTURES_DIR = Path(__file__).parent / "otbox" / "fixtures" / "sessions"


def _init_git_repo(path: Path) -> None:
    """Initialize a git repo for the project_dir. append_event_batch needs this."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    # Need at least one commit on main for the ref namespace to behave
    (path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _run_fixture(name: str, tmp_path: Path) -> Path:
    base = FIXTURES_DIR / name
    spec = importlib.util.spec_from_file_location(f"_e2e_{name}", base / "session.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    transcript = tmp_path / "transcript.jsonl"
    mod.run(tmp_path, transcript)
    return transcript


def _ingest_and_project(fixture_name: str, tmp_path: Path):
    """End-to-end: init repo, capture, project."""
    _init_git_repo(tmp_path)
    transcript = _run_fixture(fixture_name, tmp_path)
    record = TraceRecord(
        trace_id=f"trace-{fixture_name}",
        session_id=f"sess-{fixture_name}",
        agent=Agent(name="claude-code", version="otbox-fake-1.0"),
        steps=[],
    )
    summary = emit_context_tree_events_from_record(
        project_dir=tmp_path,
        final_record=record,
        transcript_path=transcript,
    )
    projection = build_context_tree_projection(tmp_path)
    return summary, projection


class TestRoundTrip:

    def test_linear_round_trips(self, tmp_path):
        summary, projection = _ingest_and_project("context-tree-linear", tmp_path)
        # Summary's node_count must equal what we can read back
        nodes_for_trace = projection.nodes_by_trace.get("trace-context-tree-linear", [])
        assert len(nodes_for_trace) == summary["node_count"], \
            f"summary says {summary['node_count']} nodes; projection found {len(nodes_for_trace)}"
        # All layers should round-trip too
        assert len(projection.layers_by_id) == summary["layer_count"]

    def test_compacted_round_trips(self, tmp_path):
        summary, projection = _ingest_and_project("context-tree-compacted", tmp_path)
        assert summary["node_count"] >= 2
        # compaction_observed events should be queryable
        comps = projection.compaction_events_by_trace.get(
            "trace-context-tree-compacted", []
        )
        assert comps, "compacted fixture should produce at least one compaction event"

    def test_rewound_round_trips(self, tmp_path):
        summary, projection = _ingest_and_project("context-tree-rewound", tmp_path)
        # Rewind branch nodes should be queryable
        orphan_roots = projection.orphan_branch_roots_by_trace.get(
            "trace-context-tree-rewound", []
        )
        assert orphan_roots, "rewound fixture should produce orphan branch root(s)"

    def test_subagent_round_trips(self, tmp_path):
        summary, projection = _ingest_and_project("context-tree-subagent", tmp_path)
        subagent_ids = projection.subagent_session_ids_by_trace.get(
            "trace-context-tree-subagent", []
        )
        assert subagent_ids, "subagent fixture should produce subagent_session_id reference(s)"


class TestProjectionQueries:

    def test_active_path_walk(self, tmp_path):
        _, projection = _ingest_and_project("context-tree-multi-turn", tmp_path)
        nodes_for_trace = projection.nodes_by_trace.get("trace-context-tree-multi-turn", [])
        assert nodes_for_trace
        # active_path should produce the chain from root to leaf
        leaf_id = nodes_for_trace[-1]
        path = projection.active_path("trace-context-tree-multi-turn", leaf_id=leaf_id)
        assert len(path) >= 1
        # First node should be root (no parent)
        assert path[0].parent_node_id is None or path[0].branch_type == "root"

    def test_summary_aggregates(self, tmp_path):
        _, projection = _ingest_and_project("context-tree-rewound", tmp_path)
        summary = projection.summary()
        assert summary["trace_count"] >= 1
        assert summary["node_count"] >= 2
        assert summary["orphan_branch_count"] >= 1

    def test_context_tree_summary_helper(self, tmp_path):
        _ingest_and_project("context-tree-linear", tmp_path)
        s = context_tree_summary(tmp_path)
        assert s["trace_count"] == 1
        assert s["node_count"] >= 2
        assert s["layer_count"] == 4

    def test_layer_diff_returns_unchanged_for_same_node(self, tmp_path):
        _, projection = _ingest_and_project("context-tree-linear", tmp_path)
        nodes_for_trace = projection.nodes_by_trace.get("trace-context-tree-linear", [])
        first = nodes_for_trace[0]
        diff = projection.layer_diff(first, first)
        assert diff["changed"] == []

    def test_reads_writes_projection(self, tmp_path):
        _, projection = _ingest_and_project("context-tree-multi-turn", tmp_path)
        reads = projection.reads_for_trace("trace-context-tree-multi-turn")
        writes = projection.writes_for_trace("trace-context-tree-multi-turn")
        # Multi-turn fixture has both user prompts (reads) and assistant
        # turns with Edits (writes). The v1 projection uses the
        # session-level messages layer; the projection dedups by
        # layer_id so we get one batch per trace.
        assert reads or writes, "expected at least one read or write entry"
