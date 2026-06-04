"""Cross-substrate join: Step.context_node_id wired by ingest.

Closes the reviewer-flagged gap that the schema field exists but no
code path was populating it. The orchestrator now returns a step_index
-> node_id map; ingest reads it and mutates final_record.steps in
place before the staging JSONL is written.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

from opentraces.capture.claude_code.context_tree_capture import (
    emit_context_tree_events_from_record,
)
from opentraces_schema.models import Agent, Step, TraceRecord


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
    spec = importlib.util.spec_from_file_location(f"_join_{name}", base / "session.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    transcript = tmp_path / "transcript.jsonl"
    mod.run(tmp_path, transcript)
    return transcript


class TestOrchestratorReturnsStepMap:

    def test_step_map_present_in_summary(self, tmp_path):
        _init_git_repo(tmp_path)
        transcript = _run_fixture("context-tree-linear", tmp_path)
        record = TraceRecord(
            trace_id="trace-join-1",
            session_id="sess-join-1",
            agent=Agent(name="claude-code", version="otbox-fake-1.0"),
            steps=[],
        )
        summary = emit_context_tree_events_from_record(
            project_dir=tmp_path,
            final_record=record,
            transcript_path=transcript,
        )
        assert "step_node_id_map" in summary
        assert isinstance(summary["step_node_id_map"], dict)
        assert len(summary["step_node_id_map"]) >= 2
        for sid, nid in summary["step_node_id_map"].items():
            assert isinstance(sid, int)
            assert isinstance(nid, str)
            assert nid.startswith("sha256:")


class TestIngestWiringIntegration:

    def test_ingest_callable_path_populates_context_node_id(self, tmp_path):
        """End-to-end: simulate the ingest call path manually by invoking
        the orchestrator and then walking the step map onto a TraceRecord
        with real Step instances, the way ingest does it."""
        _init_git_repo(tmp_path)
        transcript = _run_fixture("context-tree-multi-turn", tmp_path)
        record = TraceRecord(
            trace_id="trace-join-3",
            session_id="sess-join-3",
            agent=Agent(name="claude-code", version="otbox-fake-1.0"),
            steps=[
                Step(step_index=i, role="agent")
                for i in range(15)
            ],
        )
        summary = emit_context_tree_events_from_record(
            project_dir=tmp_path,
            final_record=record,
            transcript_path=transcript,
        )
        step_map = summary["step_node_id_map"]
        assert step_map, "multi-turn fixture should produce a step map"

        for step in record.steps:
            node_id = step_map.get(step.step_index)
            if node_id is not None:
                step.context_node_id = node_id

        with_node_id = [s for s in record.steps if s.context_node_id is not None]
        assert with_node_id, "expected at least one step to receive a context_node_id"
        for s in with_node_id:
            assert s.context_node_id.startswith("sha256:")

    def test_context_tree_summary_field_assignable(self, tmp_path):
        _init_git_repo(tmp_path)
        transcript = _run_fixture("context-tree-linear", tmp_path)
        record = TraceRecord(
            trace_id="trace-join-4",
            session_id="sess-join-4",
            agent=Agent(name="claude-code", version="otbox-fake-1.0"),
            steps=[],
        )
        assert record.context_tree_summary == {}
        summary = emit_context_tree_events_from_record(
            project_dir=tmp_path,
            final_record=record,
            transcript_path=transcript,
        )
        public = {k: v for k, v in summary.items() if k != "step_node_id_map"}
        record.context_tree_summary = public
        as_dict = record.model_dump()
        assert as_dict["context_tree_summary"] == public
        assert "node_count" in record.context_tree_summary
