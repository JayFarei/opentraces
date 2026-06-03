"""Unit tests for the Context Tree detector functions.

These cover Agent E's contributions:
- ``detect_compaction_boundaries`` (compaction fixture + empty + no-compaction)
- ``detect_orphan_branches`` (rewound fixture + empty + linear-no-rewind)
- ``detect_subagent_forks`` (subagent fixture + empty + missing-subagents-dir)
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from opentraces.capture.claude_code.context_tree_capture import (
    CompactionBoundary,
    OrphanBranch,
    ParentGraph,
    SubagentFork,
    build_parent_graph,
    detect_compaction_boundaries,
    detect_orphan_branches,
    detect_subagent_forks,
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


def _load_graph(name: str, tmp_path: Path) -> tuple[ParentGraph, Path]:
    transcript = _run_fixture(name, tmp_path)
    records = read_jsonl_records(transcript)
    graph = build_parent_graph(records)
    return graph, transcript


# --------------------------------------------------------------------------- #
# detect_compaction_boundaries
# --------------------------------------------------------------------------- #


class TestDetectCompactionBoundaries:

    def test_empty_graph_returns_no_boundaries(self):
        graph = build_parent_graph([])
        assert detect_compaction_boundaries(graph) == []

    def test_linear_fixture_has_no_boundary(self, tmp_path):
        graph, _ = _load_graph("context-tree-linear", tmp_path)
        assert detect_compaction_boundaries(graph) == []

    def test_rewound_fixture_has_no_boundary(self, tmp_path):
        # Rewind is not a compaction; this guards against false positives
        # from the multi-child branching pattern.
        graph, _ = _load_graph("context-tree-rewound", tmp_path)
        assert detect_compaction_boundaries(graph) == []

    def test_compacted_fixture_yields_single_boundary(self, tmp_path):
        graph, _ = _load_graph("context-tree-compacted", tmp_path)
        boundaries = detect_compaction_boundaries(graph)
        assert len(boundaries) == 1
        b = boundaries[0]
        # All fields populated.
        assert b.pre_record_uuid
        assert b.post_record_uuid
        assert b.compacted_span_first_uuid
        assert b.compacted_span_last_uuid
        assert b.summary_text_hash.startswith("sha256:")
        assert b.lossy_diff_removed_uuids, "v1 approximation: full span removed"

    def test_compacted_boundary_pre_is_active_path_record(self, tmp_path):
        graph, _ = _load_graph("context-tree-compacted", tmp_path)
        boundaries = detect_compaction_boundaries(graph)
        assert len(boundaries) == 1
        b = boundaries[0]
        # pre_record_uuid must be on the active path.
        assert b.pre_record_uuid in set(graph.active_path_uuids)
        # post_record_uuid (if non-empty) must also be on the active path.
        if b.post_record_uuid:
            assert b.post_record_uuid in set(graph.active_path_uuids)

    def test_compacted_boundary_span_is_in_active_path(self, tmp_path):
        graph, _ = _load_graph("context-tree-compacted", tmp_path)
        boundaries = detect_compaction_boundaries(graph)
        assert len(boundaries) == 1
        active_set = set(graph.active_path_uuids)
        for uuid in boundaries[0].lossy_diff_removed_uuids:
            assert uuid in active_set, (
                f"compacted span uuid {uuid} is not on the active path"
            )

    def test_compacted_boundary_post_is_after_summary(self, tmp_path):
        graph, _ = _load_graph("context-tree-compacted", tmp_path)
        boundaries = detect_compaction_boundaries(graph)
        b = boundaries[0]
        # The post record is the first user/assistant after the summary;
        # since the summary itself is a user-role record with
        # isCompactSummary=true, post should not equal any compacted-span
        # uuid (we excluded the summary record).
        pre_set = set(b.lossy_diff_removed_uuids)
        assert b.post_record_uuid not in pre_set


# --------------------------------------------------------------------------- #
# detect_orphan_branches
# --------------------------------------------------------------------------- #


class TestDetectOrphanBranches:

    def test_empty_graph_returns_no_orphans(self):
        graph = build_parent_graph([])
        assert detect_orphan_branches(graph) == []

    def test_linear_fixture_has_no_orphans(self, tmp_path):
        graph, _ = _load_graph("context-tree-linear", tmp_path)
        assert detect_orphan_branches(graph) == []

    def test_compacted_fixture_has_no_orphans(self, tmp_path):
        graph, _ = _load_graph("context-tree-compacted", tmp_path)
        # Compaction doesn't fork the tree (the summary record is on the
        # active path), so we should see no orphan branches.
        assert detect_orphan_branches(graph) == []

    def test_rewound_fixture_yields_single_orphan(self, tmp_path):
        graph, _ = _load_graph("context-tree-rewound", tmp_path)
        orphans = detect_orphan_branches(graph)
        assert len(orphans) == 1
        o = orphans[0]
        assert o.branch_root_uuid
        assert o.leaf_uuid
        assert len(o.record_uuids_in_subtree) >= 1
        # branch root must be in the subtree.
        assert o.branch_root_uuid in o.record_uuids_in_subtree
        # leaf must be in the subtree.
        assert o.leaf_uuid in o.record_uuids_in_subtree

    def test_rewound_orphan_disjoint_from_active_path(self, tmp_path):
        graph, _ = _load_graph("context-tree-rewound", tmp_path)
        orphans = detect_orphan_branches(graph)
        assert len(orphans) == 1
        active_set = set(graph.active_path_uuids)
        for uuid in orphans[0].record_uuids_in_subtree:
            assert uuid not in active_set, (
                f"orphan subtree uuid {uuid} should not be on the active path"
            )

    def test_rewound_orphan_root_parent_is_on_active_path(self, tmp_path):
        graph, _ = _load_graph("context-tree-rewound", tmp_path)
        orphans = detect_orphan_branches(graph)
        assert len(orphans) == 1
        root_rec = graph.by_uuid[orphans[0].branch_root_uuid]
        assert root_rec.parent_uuid in set(graph.active_path_uuids), (
            "branch root's parent (the rewind anchor) must be on the "
            "active path"
        )

    def test_rewound_orphan_size_matches_fixture_shape(self, tmp_path):
        # The rewound fixture's orphan branch is asst_orphan -> tr_orphan
        # (2 records). The detector should pick that exact shape up.
        graph, _ = _load_graph("context-tree-rewound", tmp_path)
        orphans = detect_orphan_branches(graph)
        assert len(orphans) == 1
        assert len(orphans[0].record_uuids_in_subtree) == 2


# --------------------------------------------------------------------------- #
# detect_subagent_forks
# --------------------------------------------------------------------------- #


class TestDetectSubagentForks:

    def test_empty_graph_returns_no_forks(self, tmp_path):
        graph = build_parent_graph([])
        # transcript path doesn't need to exist for an empty graph.
        forks = detect_subagent_forks(graph, tmp_path / "missing.jsonl")
        assert forks == []

    def test_linear_fixture_has_no_forks(self, tmp_path):
        graph, transcript = _load_graph("context-tree-linear", tmp_path)
        assert detect_subagent_forks(graph, transcript) == []

    def test_compacted_fixture_has_no_forks(self, tmp_path):
        graph, transcript = _load_graph("context-tree-compacted", tmp_path)
        assert detect_subagent_forks(graph, transcript) == []

    def test_rewound_fixture_has_no_forks(self, tmp_path):
        graph, transcript = _load_graph("context-tree-rewound", tmp_path)
        assert detect_subagent_forks(graph, transcript) == []

    def test_subagent_fixture_yields_single_fork(self, tmp_path):
        graph, transcript = _load_graph("context-tree-subagent", tmp_path)
        forks = detect_subagent_forks(graph, transcript)
        assert len(forks) == 1
        f = forks[0]
        assert f.parent_record_uuid, "Task tool_use record uuid must be set"
        assert f.subagent_session_id, "subagent session id should resolve via meta.json + JSONL init"
        assert f.subagent_jsonl_path is not None
        assert f.subagent_jsonl_path.exists()
        assert f.subagent_jsonl_path.suffix == ".jsonl"

    def test_subagent_fork_session_id_matches_child_jsonl(self, tmp_path):
        graph, transcript = _load_graph("context-tree-subagent", tmp_path)
        forks = detect_subagent_forks(graph, transcript)
        assert len(forks) == 1
        # The child JSONL's system.init sessionId is the canonical one.
        # Read it directly to confirm the detector resolved it.
        import json

        with forks[0].subagent_jsonl_path.open("r", encoding="utf-8") as fh:
            init = json.loads(fh.readline())
        assert init["type"] == "system"
        assert init["subtype"] == "init"
        assert init["sessionId"] == forks[0].subagent_session_id

    def test_subagent_fork_emitted_without_subagents_dir(self, tmp_path):
        # Run the fixture, then nuke the subagents/ sibling dir before
        # running the detector. We should still emit a SubagentFork
        # (from the parent's Task tool_use) but with no path/session.
        graph, transcript = _load_graph("context-tree-subagent", tmp_path)
        import shutil

        subagents_dir = transcript.with_suffix("") / "subagents"
        if subagents_dir.exists():
            shutil.rmtree(subagents_dir)
        forks = detect_subagent_forks(graph, transcript)
        assert len(forks) == 1
        assert forks[0].subagent_jsonl_path is None
        assert forks[0].subagent_session_id == ""

    def test_subagent_fork_missing_meta_falls_back_to_unreachable(self, tmp_path):
        # Remove the .meta.json but keep the JSONL; we should still emit
        # one SubagentFork (the Task tool_use is present), but without
        # the meta.json match the description -> jsonl lookup misses,
        # so subagent_jsonl_path stays None.
        graph, transcript = _load_graph("context-tree-subagent", tmp_path)
        subagents_dir = transcript.with_suffix("") / "subagents"
        for meta_file in subagents_dir.glob("*.meta.json"):
            meta_file.unlink()
        forks = detect_subagent_forks(graph, transcript)
        assert len(forks) == 1
        assert forks[0].subagent_jsonl_path is None
        assert forks[0].subagent_session_id == ""


# --------------------------------------------------------------------------- #
# Determinism + ordering guarantees
# --------------------------------------------------------------------------- #


class TestDeterminism:

    def test_compaction_detection_is_deterministic(self, tmp_path):
        graph, _ = _load_graph("context-tree-compacted", tmp_path / "a")
        a = detect_compaction_boundaries(graph)
        graph2, _ = _load_graph("context-tree-compacted", tmp_path / "b")
        b = detect_compaction_boundaries(graph2)
        # Same shape (uuids are stable per-fixture by design).
        assert [
            (x.pre_record_uuid, x.post_record_uuid, x.compacted_span_first_uuid,
             x.compacted_span_last_uuid, sorted(x.lossy_diff_removed_uuids))
            for x in a
        ] == [
            (x.pre_record_uuid, x.post_record_uuid, x.compacted_span_first_uuid,
             x.compacted_span_last_uuid, sorted(x.lossy_diff_removed_uuids))
            for x in b
        ]

    def test_orphan_detection_is_deterministic(self, tmp_path):
        graph, _ = _load_graph("context-tree-rewound", tmp_path / "a")
        a = detect_orphan_branches(graph)
        graph2, _ = _load_graph("context-tree-rewound", tmp_path / "b")
        b = detect_orphan_branches(graph2)
        assert [
            (x.branch_root_uuid, x.leaf_uuid, sorted(x.record_uuids_in_subtree))
            for x in a
        ] == [
            (x.branch_root_uuid, x.leaf_uuid, sorted(x.record_uuids_in_subtree))
            for x in b
        ]

    def test_subagent_detection_is_deterministic(self, tmp_path):
        graph_a, transcript_a = _load_graph("context-tree-subagent", tmp_path / "a")
        a = detect_subagent_forks(graph_a, transcript_a)
        graph_b, transcript_b = _load_graph("context-tree-subagent", tmp_path / "b")
        b = detect_subagent_forks(graph_b, transcript_b)
        # subagent_jsonl_path differs by tmp dir, but parent uuid +
        # subagent_session_id are stable.
        assert [(x.parent_record_uuid, x.subagent_session_id) for x in a] == [
            (x.parent_record_uuid, x.subagent_session_id) for x in b
        ]
