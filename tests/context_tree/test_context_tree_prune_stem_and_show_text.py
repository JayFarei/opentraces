"""Issue #42 product-fix coverage.

1. ``ctx prune --to-session`` stems are used VERBATIM, making the
   documented rc=4 no-clobber contract reachable from the CLI (the old
   uuid4-suffix behavior silently minted a new file on every run,
   contradicting the collision error's own hint).
2. The prune packet carries additive ``uuid_set`` / ``uuid_set_sorted``
   keys (active-path order + sorted) for two-way uuid-set assertions.
3. ``ctx show`` text mode renders each layer's ``capture_method`` —
   the honest-provenance label was JSON-only before (the
   ctx-output-parity journey's one red).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.capture.claude_code.context_tree_capture import (
    emit_context_tree_events_from_record,
)
from opentraces.core.context_tree.prune import prune_session_to_node
from opentraces.core.context_tree.query import build_context_tree_projection
from opentraces_schema.models import Agent, TraceRecord


FIXTURES_DIR = Path(__file__).parent.parent / "otbox" / "fixtures" / "sessions"


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _ingest_and_project(fixture_name: str, tmp_path: Path):
    _init_git_repo(tmp_path)
    base = FIXTURES_DIR / fixture_name
    spec = importlib.util.spec_from_file_location(
        f"_stem_{fixture_name.replace('-', '_')}", base / "session.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    transcript = tmp_path / "transcript.jsonl"
    mod.run(tmp_path, transcript)
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


class TestVerbatimStem:

    def test_stem_is_used_verbatim(self, tmp_path):
        projection, transcript = _ingest_and_project("context-tree-linear", tmp_path)
        target = projection.nodes_by_trace["trace-context-tree-linear"][-1]
        packet = prune_session_to_node(
            tmp_path, target, transcript, to_session="exact-stem", write=True
        )
        assert packet["new_session_id"] == "exact-stem"
        assert packet["jsonl_path"].endswith("/exact-stem.jsonl")
        assert Path(packet["jsonl_path"]).exists()

    def test_repeat_write_same_stem_collides_naturally(self, tmp_path):
        """The rc=4 contract is reachable WITHOUT monkey-patching now."""
        projection, transcript = _ingest_and_project("context-tree-linear", tmp_path)
        target = projection.nodes_by_trace["trace-context-tree-linear"][-1]
        prune_session_to_node(
            tmp_path, target, transcript, to_session="no-clobber", write=True
        )
        with pytest.raises(FileExistsError):
            prune_session_to_node(
                tmp_path, target, transcript, to_session="no-clobber", write=True
            )

    def test_stem_is_sanitized(self, tmp_path):
        projection, transcript = _ingest_and_project("context-tree-linear", tmp_path)
        target = projection.nodes_by_trace["trace-context-tree-linear"][-1]
        packet = prune_session_to_node(
            tmp_path, target, transcript, to_session="we ird/$stem", write=False
        )
        assert packet["new_session_id"] == "we-ird--stem"

    def test_anonymous_stems_stay_unique(self, tmp_path):
        projection, transcript = _ingest_and_project("context-tree-linear", tmp_path)
        target = projection.nodes_by_trace["trace-context-tree-linear"][-1]
        first = prune_session_to_node(tmp_path, target, transcript, write=True)
        second = prune_session_to_node(tmp_path, target, transcript, write=True)
        assert first["new_session_id"] != second["new_session_id"]
        assert first["new_session_id"].startswith("sess-")


class TestUuidSetKeys:

    def test_uuid_set_matches_active_path_order(self, tmp_path):
        projection, transcript = _ingest_and_project(
            "context-tree-multi-turn", tmp_path
        )
        target = projection.nodes_by_trace["trace-context-tree-multi-turn"][-1]
        packet = prune_session_to_node(
            tmp_path, target, transcript, to_session="uuid-set", write=True
        )
        assert packet["uuid_set"], "uuid_set must be populated"
        assert len(packet["uuid_set"]) == packet["record_count"]
        assert packet["uuid_set_sorted"] == sorted(packet["uuid_set"])
        # Every reported uuid is genuinely in the written JSONL, in order.
        written = [
            json.loads(line)["uuid"]
            for line in Path(packet["jsonl_path"]).read_text().splitlines()
            if line.strip() and "uuid" in json.loads(line)
        ]
        assert written == packet["uuid_set"]


class TestShowTextCaptureMethod:

    def test_show_text_renders_capture_method(self, tmp_path):
        """ctx show (text) surfaces each layer's capture_method label."""
        from opentraces.cli.ctx import ctx_show_cmd

        projection, _transcript = _ingest_and_project(
            "context-tree-linear", tmp_path
        )
        node_id = projection.nodes_by_trace["trace-context-tree-linear"][0]
        runner = CliRunner()
        result = runner.invoke(
            ctx_show_cmd, [node_id, "--project", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        assert "transcript_reconstruction" in result.output, (
            "capture_method missing from ctx show text mode:\n" + result.output
        )
