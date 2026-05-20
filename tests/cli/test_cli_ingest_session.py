"""Hidden ``opentraces _ingest-session <transcript>`` CLI (Phase 2).

Called fire-and-forget by the Stop hook to refresh a single session's
inbox state without waiting on the 5-minute watcher tick. Must be
fast, silent, and never-raise: a broken CLI invocation from a
Claude Code hook would be invisible to the user and should never
propagate to the agent session.
"""

from __future__ import annotations

import json
import signal
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.cli import main


def _write_session(dir: Path, session_id: str, turns: int = 3) -> Path:
    dir.mkdir(parents=True, exist_ok=True)
    path = dir / f"{session_id}.jsonl"
    with path.open("w") as f:
        for i in range(1, turns + 1):
            ts = f"2026-04-15T07:00:{i:02d}Z"
            for line in (
                {"type": "user", "sessionId": session_id, "timestamp": ts,
                 "message": {"role": "user", "content": f"prompt {i}"}},
                {"type": "assistant", "sessionId": session_id, "timestamp": ts,
                 "message": {
                     "role": "assistant",
                     "content": [{"type": "tool_use", "id": f"tu_{i}",
                                  "name": "Read", "input": {"file_path": "x.py"}}],
                     "usage": {"input_tokens": 10, "output_tokens": 10},
                 }},
                {"type": "user", "sessionId": session_id, "timestamp": ts,
                 "message": {
                     "role": "user",
                     "content": [{"type": "tool_result", "tool_use_id": f"tu_{i}",
                                  "content": "ok"}],
                 }},
            ):
                f.write(json.dumps(line) + "\n")
    return path


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def opted_in_project(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".opentraces.json").write_text(json.dumps({
        "marker_version": "2",
        "project_id": "ingest-test-0000",
        "review_policy": "review",
        "push_policy": "manual",
        "remotes": {"origin": {"url": "t/t", "visibility": "private"}},
        "active_remote": "origin",
        "agents": ["claude-code"],
    }))
    monkeypatch.chdir(proj)
    return proj


class TestIngestSessionCommand:
    def test_command_is_registered(self, runner):
        result = runner.invoke(main, ["_ingest-session", "--help"])
        assert result.exit_code == 0

    def test_ingests_single_session_via_path(
        self, runner, opted_in_project, tmp_path, monkeypatch
    ):
        session = _write_session(tmp_path / "corpus", "sess-hook", turns=3)

        result = runner.invoke(
            main, ["_ingest-session", str(session), "--project", str(opted_in_project)]
        )
        assert result.exit_code == 0, result.output

        from opentraces.core.state import StateManager
        from opentraces.core.config import get_project_state_path
        state = StateManager(state_path=get_project_state_path(opted_in_project))
        assert state.get_session("sess-hook") is not None

    def test_missing_path_exits_cleanly(self, runner, opted_in_project):
        """A vanished transcript_path must exit 0 silently, never raise.

        The hook is fire-and-forget — noise here would cascade into
        Claude Code's own session. Silent success is the contract.
        """
        result = runner.invoke(
            main, ["_ingest-session", "/nonexistent/path.jsonl",
                   "--project", str(opted_in_project)]
        )
        assert result.exit_code == 0, result.output
        assert signal.alarm(0) == 0

    def test_no_opted_in_project_exits_cleanly(
        self, runner, tmp_path, monkeypatch
    ):
        """Session under a non-enlisted project: quiet no-op, exit 0."""
        non_opted = tmp_path / "unrelated"
        non_opted.mkdir()
        session = _write_session(tmp_path / "corpus", "orphan", turns=3)
        monkeypatch.chdir(non_opted)
        result = runner.invoke(
            main, ["_ingest-session", str(session)]
        )
        assert result.exit_code == 0, (
            "hook path must not fail on non-enlisted projects"
        )
