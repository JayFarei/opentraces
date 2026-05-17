"""Phase 2 integration tests: detector results flow into ContextNodes.

These exercise the full path from JSONL fixture, through walker +
layer builders + detectors, into the materialized node graph. The
unit tests for each piece live in their own files; this suite checks
they compose correctly.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from opentraces.capture.claude_code.context_tree_capture import (
    build_parent_graph,
    detect_compaction_boundaries,
    detect_orphan_branches,
    detect_subagent_forks,
    emit_context_tree_events_from_record,
    read_jsonl_records,
)
from opentraces_schema.models import Agent, TraceRecord


FIXTURES_DIR = Path(__file__).parent / "otbox" / "fixtures" / "sessions"


def _run_fixture(name: str, tmp_path: Path) -> Path:
    base = FIXTURES_DIR / name
    spec = importlib.util.spec_from_file_location(f"_orch_{name}", base / "session.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    transcript = tmp_path / "transcript.jsonl"
    mod.run(tmp_path, transcript)
    return transcript


def _materialize(fixture_name: str, tmp_path: Path):
    """Run the full materialization pipeline for one fixture."""
    from opentraces.capture.claude_code.context_tree_capture import (
        _materialize_nodes_for_active_path,
        build_runtime_state_layer,
        build_messages_layer,
        build_system_layer,
        build_tool_registry_layer,
    )

    transcript = _run_fixture(fixture_name, tmp_path)
    records = read_jsonl_records(transcript)
    graph = build_parent_graph(records)
    active = graph.active_path()
    init = next(
        (r for r in records if r.record_type == "system" and r.subtype == "init"),
        None,
    )
    layers = [
        build_system_layer(init_record=init, read_from_disk=False),
        build_messages_layer(active_path=active),
        build_tool_registry_layer(init_record=init, on_disk_mcp_servers={}, on_disk_skills=[]),
        build_runtime_state_layer(init_record=init, on_disk_mcp_servers={}),
    ]
    compactions = detect_compaction_boundaries(graph)
    orphans = detect_orphan_branches(graph)
    subagent_forks = detect_subagent_forks(graph, transcript)
    nodes = _materialize_nodes_for_active_path(
        trace_id=f"trace-{fixture_name}",
        active_path=active,
        layers=layers,
        compactions=compactions,
        orphans=orphans,
        subagent_forks=subagent_forks,
        parent_graph=graph,
    )
    return {
        "graph": graph,
        "compactions": compactions,
        "orphans": orphans,
        "subagent_forks": subagent_forks,
        "nodes": nodes,
        "transcript": transcript,
    }


class TestLinearFlow:

    def test_linear_fixture_all_linear_branch_types(self, tmp_path):
        out = _materialize("context-tree-linear", tmp_path)
        nodes = out["nodes"]
        assert len(nodes) >= 2
        assert nodes[0].branch_type == "root"
        for n in nodes[1:]:
            assert n.branch_type == "linear", f"non-linear node in linear fixture: {n}"

    def test_linear_fixture_no_detector_hits(self, tmp_path):
        out = _materialize("context-tree-linear", tmp_path)
        assert out["compactions"] == []
        assert out["orphans"] == []
        assert out["subagent_forks"] == []


class TestCompactionFlow:

    def test_compacted_fixture_emits_compaction_fork(self, tmp_path):
        out = _materialize("context-tree-compacted", tmp_path)
        assert len(out["compactions"]) >= 1
        post_uuids = {b.post_record_uuid for b in out["compactions"]}
        fork_nodes = [
            n for n in out["nodes"]
            if n.branch_type == "compaction_fork"
        ]
        assert len(fork_nodes) >= 1, "expected at least one compaction_fork node"
        # Every fork node's transcript_uuid must match a detected post_record_uuid
        for n in fork_nodes:
            assert n.transcript_uuid in post_uuids


class TestRewindFlow:

    def test_rewound_fixture_emits_rewind_branch_nodes(self, tmp_path):
        out = _materialize("context-tree-rewound", tmp_path)
        assert len(out["orphans"]) >= 1
        rewind_nodes = [
            n for n in out["nodes"]
            if n.branch_type == "rewind_branch"
        ]
        assert len(rewind_nodes) >= 1, "expected at least one rewind_branch node"
        # Every rewind node's parent should resolve to an existing node
        all_node_ids = {n.node_id for n in out["nodes"]}
        for n in rewind_nodes:
            assert n.parent_node_id in all_node_ids, \
                f"rewind_branch node parents an unknown node: {n.parent_node_id}"

    def test_rewound_fixture_orphan_not_on_active_path(self, tmp_path):
        out = _materialize("context-tree-rewound", tmp_path)
        active_path_uuids = set(out["graph"].active_path_uuids)
        for orphan in out["orphans"]:
            assert orphan.branch_root_uuid not in active_path_uuids
            for sub_uuid in orphan.record_uuids_in_subtree:
                assert sub_uuid not in active_path_uuids


class TestSubagentFlow:

    def test_subagent_fixture_emits_subagent_fork_node(self, tmp_path):
        out = _materialize("context-tree-subagent", tmp_path)
        assert len(out["subagent_forks"]) >= 1
        fork_nodes = [
            n for n in out["nodes"]
            if n.branch_type == "subagent_fork"
        ]
        assert len(fork_nodes) >= 1, "expected at least one subagent_fork node"
        # Each fork node must carry the subagent_session_id
        for n in fork_nodes:
            assert n.subagent_session_id, \
                "subagent_fork node missing subagent_session_id"


class TestOrchestratorSummary:

    @pytest.mark.parametrize("fixture_name,expected_min_nodes", [
        ("context-tree-linear", 2),
        ("context-tree-multi-turn", 2),
        ("context-tree-rewound", 3),       # active path + at least 1 orphan
        ("context-tree-subagent", 2),      # active path + 1 subagent_fork
        ("context-tree-compacted", 2),
    ])
    def test_emit_orchestrator_summary(self, tmp_path, fixture_name, expected_min_nodes):
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
        assert summary["node_count"] >= expected_min_nodes
        assert summary["layer_count"] == 4
        assert summary["active_path_leaf_id"] is not None
