"""Tests for the hidden ``opentraces _scan`` CLI (Phase 1).

Verifies the wiring: the command is registered, accepts expected flags,
calls ``scan_project`` correctly, and surfaces useful output. The actual
ingestion semantics are covered exhaustively in ``test_ingest.py`` — no
need to re-test those through click.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.cli import main


def _write_session(path: Path, session_id: str, turns: int = 3) -> Path:
    """Minimal valid Claude Code session JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for i in range(1, turns + 1):
            ts = f"2026-04-15T07:00:{i:02d}Z"
            user = {
                "type": "user", "sessionId": session_id, "timestamp": ts,
                "message": {"role": "user", "content": f"prompt {i}"},
            }
            asst = {
                "type": "assistant", "sessionId": session_id, "timestamp": ts,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": f"tu_{i}",
                                 "name": "Read", "input": {"file_path": "x.py"}}],
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                },
            }
            result = {
                "type": "user", "sessionId": session_id, "timestamp": ts,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": f"tu_{i}",
                                 "content": "ok"}],
                },
            }
            for line in (user, asst, result):
                f.write(json.dumps(line) + "\n")
    return path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def opted_in_project(tmp_path, monkeypatch):
    """An opted-in project with one pre-existing JSONL session."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".opentraces.json").write_text(json.dumps({
        "marker_version": "2",
        "project_id": "scan-test-0000",
        "review_policy": "review",
        "push_policy": "manual",
        "remotes": {"origin": {"url": "test/test", "visibility": "private"}},
        "active_remote": "origin",
        "agents": ["claude-code"],
    }))

    # Synthesize a JSONL in a path we fully control — the _scan CLI must
    # route through scan_project, which calls discover_claude_jsonl_corpus.
    # We monkeypatch that discovery to return our fixture file so the test
    # doesn't depend on the developer's real ~/.claude/ corpus.
    session_path = tmp_path / "synthetic" / "sess-test.jsonl"
    _write_session(session_path, "sess-test", turns=3)

    monkeypatch.setattr(
        "opentraces.core.ingest.discover_claude_jsonl_corpus",
        lambda _repo: [session_path],
    )
    monkeypatch.chdir(proj)
    return proj


class TestScanCommand:
    def test_scan_command_is_registered(self, runner) -> None:
        """Sanity: the hidden command resolves without error."""
        result = runner.invoke(main, ["_scan", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--reparse" in result.output

    def test_scan_stages_pre_existing_sessions(
        self, runner, opted_in_project
    ) -> None:
        result = runner.invoke(main, ["--json", "_scan"])
        assert result.exit_code == 0, result.output

        payload = _extract_json(result.output)
        assert payload["project"].endswith("proj")
        assert payload["created"] == 1
        assert payload["errored"] == 0

        # Trace landed in inbox (visible stage).
        from opentraces.core.state import StateManager
        from opentraces.core.config import get_project_state_path
        state = StateManager(state_path=get_project_state_path(opted_in_project))
        sess = state.get_session("sess-test")
        assert sess is not None
        assert len(sess.generations) == 1

    def test_scan_dry_run_does_not_write_state(
        self, runner, opted_in_project
    ) -> None:
        result = runner.invoke(main, ["--json", "_scan", "--dry-run"])
        assert result.exit_code == 0, result.output

        from opentraces.core.state import StateManager
        from opentraces.core.config import get_project_state_path
        state = StateManager(state_path=get_project_state_path(opted_in_project))
        assert state.get_session("sess-test") is None, (
            "--dry-run must not touch state"
        )

    def test_scan_reparse_forces_refresh(
        self, runner, opted_in_project
    ) -> None:
        # First pass: creates gen 1.
        result = runner.invoke(main, ["--json", "_scan"])
        assert result.exit_code == 0, result.output

        # Second pass without --reparse is a no-op.
        result = runner.invoke(main, ["--json", "_scan"])
        assert result.exit_code == 0
        payload = _extract_json(result.output)
        assert payload["noops"] == 1
        assert payload["refreshed"] == 0

        # --reparse: same file, still refreshes.
        result = runner.invoke(main, ["--json", "_scan", "--reparse"])
        assert result.exit_code == 0
        payload = _extract_json(result.output)
        assert payload["refreshed"] == 1


def _extract_json(output: str) -> dict:
    """Parse the JSON payload emitted after the ``---OPENTRACES_JSON---`` sentinel."""
    from opentraces.cli import SENTINEL
    idx = output.rfind(SENTINEL)
    assert idx >= 0, f"no sentinel in output: {output!r}"
    blob = output[idx + len(SENTINEL):].strip()
    return json.loads(blob)
