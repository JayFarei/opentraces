"""Unit tests for the Context Tree parentUuid walker.

These cover only the coordinator-owned pieces: read_jsonl_records,
build_parent_graph, active-path identification. The layer builders
and detectors are stubbed; their tests land with agents D and E.
"""
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

import pytest

from opentraces.capture.claude_code.context_tree_capture import (
    build_parent_graph,
    read_jsonl_records,
)


FIXTURES_DIR = Path(__file__).parent.parent / "otbox" / "fixtures" / "sessions"


def _run_fixture(name: str, tmp_path: Path) -> Path:
    """Run one of Agent B's fixtures end-to-end and return the transcript path."""
    base = FIXTURES_DIR / name
    spec = importlib.util.spec_from_file_location(f"_fx_{name}", base / "session.py")
    assert spec is not None and spec.loader is not None, f"missing session.py in {base}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    transcript = tmp_path / "transcript.jsonl"
    mod.run(tmp_path, transcript)
    return transcript


class TestReadJsonlRecords:

    def test_reads_all_lines(self, tmp_path):
        transcript = _run_fixture("context-tree-linear", tmp_path)
        records = read_jsonl_records(transcript)
        assert len(records) > 0
        # Every record should have an offset >= 0 and a valid record_type
        for r in records:
            assert r.offset >= 0
            assert r.record_type

    def test_missing_transcript_returns_empty(self, tmp_path):
        records = read_jsonl_records(tmp_path / "nonexistent.jsonl")
        assert records == []

    def test_offsets_are_monotonic(self, tmp_path):
        transcript = _run_fixture("context-tree-linear", tmp_path)
        records = read_jsonl_records(transcript)
        offsets = [r.offset for r in records]
        assert offsets == sorted(offsets)
        assert len(set(offsets)) == len(offsets)


class TestBuildParentGraph:

    def test_linear_fixture_has_single_active_path(self, tmp_path):
        transcript = _run_fixture("context-tree-linear", tmp_path)
        records = read_jsonl_records(transcript)
        graph = build_parent_graph(records)
        assert graph.active_leaf_uuid is not None
        assert len(graph.active_path_uuids) >= 2  # at least user prompt + assistant reply
        # Every uuid on the active path should resolve in by_uuid
        for u in graph.active_path_uuids:
            assert u in graph.by_uuid

    def test_multi_turn_fixture_has_longer_path(self, tmp_path):
        transcript = _run_fixture("context-tree-multi-turn", tmp_path)
        records = read_jsonl_records(transcript)
        graph = build_parent_graph(records)
        linear = build_parent_graph(read_jsonl_records(_run_fixture("context-tree-linear", tmp_path / "linear")))
        assert len(graph.active_path_uuids) > len(linear.active_path_uuids)

    def test_rewound_fixture_has_orphan(self, tmp_path):
        transcript = _run_fixture("context-tree-rewound", tmp_path)
        records = read_jsonl_records(transcript)
        graph = build_parent_graph(records)
        # At least one record should have a parent_uuid pointing into the
        # tree at a position with multiple children (the rewind anchor).
        multi_child_parents = [
            parent
            for parent, kids in graph.children_of.items()
            if len(kids) >= 2
        ]
        assert multi_child_parents, "rewound fixture should produce a parent with multiple children"
        # The orphan branch's leaf should NOT be on the active path
        all_leaves = [
            u for u, kids in graph.children_of.items()
            if u and not kids and u in graph.by_uuid
        ]
        orphan_leaves = [u for u in all_leaves if u != graph.active_leaf_uuid]
        assert orphan_leaves, "rewound fixture should produce at least one orphan leaf"

    def test_empty_records_returns_empty_graph(self):
        graph = build_parent_graph([])
        assert graph.records == []
        assert graph.active_leaf_uuid is None
        assert graph.active_path_uuids == []

    def test_active_path_walks_parents_in_order(self, tmp_path):
        transcript = _run_fixture("context-tree-multi-turn", tmp_path)
        records = read_jsonl_records(transcript)
        graph = build_parent_graph(records)
        # Walking parents from active leaf should reach a root (no parent_uuid)
        path = graph.active_path_uuids
        assert path  # non-empty
        # First uuid in path should have no parent (it's the root), OR its
        # parent should not be in by_uuid (broken chain, also acceptable)
        first_rec = graph.by_uuid[path[0]]
        assert first_rec.parent_uuid is None or first_rec.parent_uuid not in graph.by_uuid


class TestOrchestratorSmoke:
    """The emit_context_tree_events_from_record orchestrator with stubs.

    Confirms the scaffold runs end-to-end against all five fixtures
    without raising. Stubs return empty/minimal data; node count is
    derived from active_path message-role records.
    """

    @pytest.mark.parametrize("fixture_name", [
        "context-tree-linear",
        "context-tree-multi-turn",
        "context-tree-rewound",
        "context-tree-compacted",
    ])
    def test_orchestrator_runs_on_fixture(self, tmp_path, fixture_name):
        from opentraces.capture.claude_code.context_tree_capture import (
            emit_context_tree_events_from_record,
        )
        from opentraces_schema.models import TraceRecord, Agent, Step

        transcript = _run_fixture(fixture_name, tmp_path)
        # Build a minimal TraceRecord wrapping the fixture's session id.
        # We only need trace_id for the orchestrator.
        record = TraceRecord(
            trace_id="trace-test-" + fixture_name,
            session_id="sess-test-" + fixture_name,
            agent=Agent(name="claude-code", version="otbox-fake-1.0"),
            steps=[],
        )
        summary = emit_context_tree_events_from_record(
            project_dir=tmp_path,
            final_record=record,
            transcript_path=transcript,
        )
        assert summary["trace_id"] == record.trace_id
        assert summary["node_count"] >= 1
        assert summary["layer_count"] == 4  # system + messages + tool_registry + runtime_state
        assert summary["active_path_leaf_id"] is not None
