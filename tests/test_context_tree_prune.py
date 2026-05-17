"""Unit tests for ``prune_session_to_node``.

Covers dry-run vs write semantics, record count matches the active
path length, the new JSONL parses and carries the correct uuids,
collision detection raises FileExistsError, and schema_version
stamping.

See ``kb/plans/077-context-tree-substrate.md`` §"JSONL pruner" and
the ``ctx-prune-no-clobber`` journey shape.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from opentraces.capture.claude_code.context_tree_capture import (
    emit_context_tree_events_from_record,
)
from opentraces.core.context_tree.contract import CONTEXT_RESUME_SCHEMA_VERSION
from opentraces.core.context_tree.prune import prune_session_to_node
from opentraces.core.context_tree.query import build_context_tree_projection
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
    spec = importlib.util.spec_from_file_location(f"_prune_{name}", base / "session.py")
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


class TestDryRun:

    def test_dry_run_does_not_write_file(self, tmp_path):
        projection, transcript = _ingest_and_project("context-tree-linear", tmp_path)
        node_ids = projection.nodes_by_trace.get("trace-context-tree-linear", [])
        target = node_ids[-1]
        packet = prune_session_to_node(
            tmp_path,
            target,
            transcript,
            to_session="dry-run-test",
        )
        dest = Path(packet["jsonl_path"])
        assert not dest.exists(), "dry-run should not write a file"
        assert packet["wrote"] is False

    def test_dry_run_reports_record_count(self, tmp_path):
        projection, transcript = _ingest_and_project("context-tree-linear", tmp_path)
        node_ids = projection.nodes_by_trace.get("trace-context-tree-linear", [])
        target = node_ids[-1]
        packet = prune_session_to_node(
            tmp_path,
            target,
            transcript,
            to_session="count-test",
        )
        # The dry-run still walks the source JSONL and counts the
        # records that would survive pruning.
        assert packet["record_count"] >= 1


class TestWrite:

    def test_write_creates_jsonl(self, tmp_path):
        projection, transcript = _ingest_and_project("context-tree-linear", tmp_path)
        node_ids = projection.nodes_by_trace.get("trace-context-tree-linear", [])
        target = node_ids[-1]
        packet = prune_session_to_node(
            tmp_path,
            target,
            transcript,
            to_session="write-test",
            write=True,
        )
        dest = Path(packet["jsonl_path"])
        assert dest.exists(), "write=True must produce a JSONL file"
        assert packet["wrote"] is True

    def test_written_jsonl_parses(self, tmp_path):
        projection, transcript = _ingest_and_project("context-tree-multi-turn", tmp_path)
        node_ids = projection.nodes_by_trace.get("trace-context-tree-multi-turn", [])
        target = node_ids[-1]
        packet = prune_session_to_node(
            tmp_path,
            target,
            transcript,
            to_session="parse-test",
            write=True,
        )
        dest = Path(packet["jsonl_path"])
        records = []
        for line in dest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            records.append(json.loads(line))
        assert records, "pruned JSONL must contain at least one record"
        # Every parsed record's uuid must appear on the active path's
        # transcript_uuid set.
        active_path = projection.active_path(
            "trace-context-tree-multi-turn", leaf_id=target
        )
        path_uuids = {n.transcript_uuid for n in active_path}
        for rec in records:
            uuid = rec.get("uuid")
            if uuid is not None:
                assert uuid in path_uuids, (
                    f"record {uuid} not on active path"
                )

    def test_record_count_matches_active_path(self, tmp_path):
        projection, transcript = _ingest_and_project("context-tree-linear", tmp_path)
        node_ids = projection.nodes_by_trace.get("trace-context-tree-linear", [])
        target = node_ids[-1]
        active_path = projection.active_path(
            "trace-context-tree-linear", leaf_id=target
        )
        packet = prune_session_to_node(
            tmp_path,
            target,
            transcript,
            to_session="count-match",
            write=True,
        )
        # Record count is at most the active path length (records
        # may be missing from the source JSONL but never extra).
        assert packet["record_count"] <= len(active_path)
        assert packet["active_path_length"] == len(active_path)


class TestCollision:

    def test_collision_raises_file_exists(self, tmp_path):
        projection, transcript = _ingest_and_project("context-tree-linear", tmp_path)
        node_ids = projection.nodes_by_trace.get("trace-context-tree-linear", [])
        target = node_ids[-1]
        packet_1 = prune_session_to_node(
            tmp_path,
            target,
            transcript,
            to_session="collide",
            write=True,
        )
        dest = Path(packet_1["jsonl_path"])
        assert dest.exists()
        # Pre-create a different file at the same destination to force
        # the collision (the new prune call gets a fresh suffix, so we
        # have to manually exercise the contract by re-using the path).
        # Simpler: monkey-patch to a deterministic stem and re-run.
        from opentraces.core.context_tree import prune as prune_mod

        # Force the same destination by stubbing the uuid suffix
        orig = prune_mod._new_session_id

        def _fixed(stem):
            return f"{stem}-aaaaaaaaaaaa"

        prune_mod._new_session_id = _fixed
        try:
            first = prune_session_to_node(
                tmp_path,
                target,
                transcript,
                to_session="collide-fixed",
                write=True,
            )
            assert Path(first["jsonl_path"]).exists()
            with pytest.raises(FileExistsError):
                prune_session_to_node(
                    tmp_path,
                    target,
                    transcript,
                    to_session="collide-fixed",
                    write=True,
                )
        finally:
            prune_mod._new_session_id = orig

    def test_dry_run_collision_does_not_raise(self, tmp_path):
        projection, transcript = _ingest_and_project("context-tree-linear", tmp_path)
        node_ids = projection.nodes_by_trace.get("trace-context-tree-linear", [])
        target = node_ids[-1]
        # Pre-create a colliding file
        from opentraces.core.context_tree import prune as prune_mod

        orig = prune_mod._new_session_id
        prune_mod._new_session_id = lambda stem: f"{stem}-bbbbbbbbbbbb"
        try:
            packet_w = prune_session_to_node(
                tmp_path,
                target,
                transcript,
                to_session="dry-collide",
                write=True,
            )
            assert Path(packet_w["jsonl_path"]).exists()
            # Dry-run on the same target must not raise
            packet_d = prune_session_to_node(
                tmp_path,
                target,
                transcript,
                to_session="dry-collide",
                write=False,
            )
            assert packet_d["wrote"] is False
        finally:
            prune_mod._new_session_id = orig


class TestSchemaStamping:

    def test_dry_run_packet_stamped(self, tmp_path):
        projection, transcript = _ingest_and_project("context-tree-linear", tmp_path)
        node_ids = projection.nodes_by_trace.get("trace-context-tree-linear", [])
        packet = prune_session_to_node(
            tmp_path,
            node_ids[-1],
            transcript,
            to_session="stamp-dry",
        )
        assert packet["schema_version"] == CONTEXT_RESUME_SCHEMA_VERSION

    def test_write_packet_stamped(self, tmp_path):
        projection, transcript = _ingest_and_project("context-tree-linear", tmp_path)
        node_ids = projection.nodes_by_trace.get("trace-context-tree-linear", [])
        packet = prune_session_to_node(
            tmp_path,
            node_ids[-1],
            transcript,
            to_session="stamp-write",
            write=True,
        )
        assert packet["schema_version"] == CONTEXT_RESUME_SCHEMA_VERSION

    def test_unknown_node_returns_envelope(self, tmp_path):
        _ingest_and_project("context-tree-linear", tmp_path)
        bogus = "sha256:" + ("0" * 64)
        packet = prune_session_to_node(
            tmp_path,
            bogus,
            tmp_path / "transcript.jsonl",
            to_session="bogus",
        )
        assert packet["schema_version"] == CONTEXT_RESUME_SCHEMA_VERSION
        assert packet["error"] == "node not found"
        assert "context_layer_unavailable" in packet["limitations"]
