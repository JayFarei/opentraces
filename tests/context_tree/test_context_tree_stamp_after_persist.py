"""Regression tests for issue #210 (seal-family W2): stamp-after-persist.

Both the Claude and Codex Context Tree emitters used to populate
``Step.context_node_id`` / ``summary["step_node_id_map"]`` BEFORE the
``append_event_batch`` call that actually persists the backing
``context_node_observed`` events to the canonical Trail event log. If that
append raised (disk full, corrupt ref, concurrent writer, ...), the
exception was logged and swallowed, but the steps had already been stamped
with node ids that resolve to nothing in the event log or the bucket
``context.jsonl.gz`` companion -- a record that lies about what it captured.

These tests fault-inject an ``append_event_batch`` failure and assert the
steps come out UNstamped. They must be RED against pre-fix code (steps get
stamped anyway) and GREEN after the stamp-after-persist fix.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from opentraces_schema.models import Agent, Step, TraceRecord


FIXTURES_DIR = Path(__file__).parent.parent / "otbox" / "fixtures" / "sessions"


def _init_git_repo(path: Path) -> None:
    """Initialize a git repo for project_dir. append_event_batch needs this."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _run_fixture(name: str, tmp_path: Path) -> Path:
    base = FIXTURES_DIR / name
    spec = importlib.util.spec_from_file_location(f"_stamp_{name}", base / "session.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    transcript = tmp_path / "transcript.jsonl"
    mod.run(tmp_path, transcript)
    return transcript


class TestClaudeStampAfterPersist:
    def test_step_node_id_map_empty_when_append_fails(self, tmp_path, monkeypatch):
        from opentraces.capture.claude_code import context_tree_capture as m

        def _boom(**kwargs):
            raise RuntimeError("simulated append_event_batch failure")

        monkeypatch.setattr(m, "_append_context_tree_events", _boom)

        transcript = _run_fixture("context-tree-linear", tmp_path)
        record = TraceRecord(
            trace_id="trace-stamp-claude-1",
            session_id="sess-stamp-claude-1",
            agent=Agent(name="claude-code", version="otbox-fake-1.0"),
            steps=[Step(step_index=i, role="agent") for i in range(1, 6)],
        )

        summary = m.emit_context_tree_events_from_record(
            project_dir=tmp_path,
            final_record=record,
            transcript_path=transcript,
        )

        assert summary["step_node_id_map"] == {}
        assert "context_tree_not_captured" in summary["capture_limitations"]

        # Mirror ingest's own wiring step: it must find nothing to stamp.
        for step in record.steps:
            node_id = summary["step_node_id_map"].get(step.step_index)
            if node_id is not None:
                step.context_node_id = node_id
        assert all(s.context_node_id is None for s in record.steps)

    def test_step_node_id_map_populated_when_append_succeeds(self, tmp_path):
        from opentraces.capture.claude_code import context_tree_capture as m

        _init_git_repo(tmp_path)
        transcript = _run_fixture("context-tree-linear", tmp_path)
        record = TraceRecord(
            trace_id="trace-stamp-claude-2",
            session_id="sess-stamp-claude-2",
            agent=Agent(name="claude-code", version="otbox-fake-1.0"),
            steps=[Step(step_index=i, role="agent") for i in range(1, 6)],
        )

        summary = m.emit_context_tree_events_from_record(
            project_dir=tmp_path,
            final_record=record,
            transcript_path=transcript,
        )

        assert summary["step_node_id_map"], "expected a populated step map on success"


def _codex_record(trace_id: str) -> TraceRecord:
    from opentraces_schema import Environment, Outcome, VCS

    return TraceRecord(
        trace_id=trace_id,
        session_id="thread-stamp-1",
        agent=Agent(name="codex-cli", version="1.2.3", model="openai/gpt-5"),
        environment=Environment(vcs=VCS(type="git", branch="main", base_commit="abc123")),
        outcome=Outcome(commit_sha="def456"),
        steps=[
            Step(step_index=1, role="user", content="Fix the bug", call_type="main"),
            Step(step_index=2, role="agent", content="Done.", call_type="main"),
        ],
        metadata={"source": "codex_cli_rollout"},
    )


class TestCodexStampAfterPersist:
    def test_steps_unstamped_when_append_fails(self, tmp_path, monkeypatch):
        import importlib

        from opentraces.capture.codex_cli import context_tree_capture as m

        event_log = importlib.import_module("opentraces.core.trails.event_log")

        def _boom(project_dir, drafts, writer):
            raise RuntimeError("simulated append_event_batch failure")

        monkeypatch.setattr(event_log, "append_event_batch", _boom)

        record = _codex_record("trace-stamp-codex-1")

        with pytest.raises(RuntimeError):
            m.emit_context_tree_events_from_record(
                project_dir=tmp_path,
                final_record=record,
                transcript_path=None,
            )

        assert all(s.context_node_id is None for s in record.steps), (
            "steps must stay unstamped when the append that persists their "
            "backing context_node_observed events fails"
        )

    def test_steps_stamped_when_append_succeeds(self, tmp_path, monkeypatch):
        import importlib

        from opentraces.capture.codex_cli import context_tree_capture as m

        event_log = importlib.import_module("opentraces.core.trails.event_log")
        calls = []

        def _fake_append(project_dir, drafts, writer):
            calls.append((project_dir, drafts, writer))
            return []

        monkeypatch.setattr(event_log, "append_event_batch", _fake_append)

        record = _codex_record("trace-stamp-codex-2")

        summary = m.emit_context_tree_events_from_record(
            project_dir=tmp_path,
            final_record=record,
            transcript_path=None,
        )

        assert calls
        assert summary["node_count"] == 2
        assert all(s.context_node_id is not None for s in record.steps)
