"""Tests for the hidden ``opentraces _scan`` CLI (Phase 1).

Verifies the wiring: the command is registered, accepts expected flags,
calls ``scan_project`` correctly, and surfaces useful output. The actual
ingestion semantics are covered exhaustively in ``test_ingest.py`` — no
need to re-test those through click.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

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
        assert "--trace-record-only" in result.output

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

    def test_scan_auto_enrolls_unmarked_project_in_global_mode(
        self, runner, tmp_path, monkeypatch
    ) -> None:
        project = tmp_path / "project"
        project.mkdir()
        session_path = tmp_path / "synthetic" / "sess-auto.jsonl"
        _write_session(session_path, "sess-auto", turns=3)
        monkeypatch.setattr(
            "opentraces.core.ingest.discover_claude_jsonl_corpus",
            lambda _repo: [session_path],
        )
        monkeypatch.setattr(
            "opentraces.capture.codex_cli.parse.CodexCliParser.discover_project_sessions",
            lambda _self, _repo: iter(()),
        )

        result = runner.invoke(
            main,
            ["--json", "_scan", "--project", str(project)],
        )
        assert result.exit_code == 0, result.output
        assert (project / ".opentraces.json").is_file()

        payload = _extract_json(result.output)
        assert payload["created"] == 1

        from opentraces.core.config import (
            get_project_state_path,
            load_config,
            opted_in_projects,
        )
        from opentraces.core.state import StateManager

        assert str(project.resolve()) in opted_in_projects(load_config())
        state = StateManager(state_path=get_project_state_path(project))
        assert state.get_session("sess-auto") is not None

    def test_scan_dry_run_respects_project_agents(
        self, runner, opted_in_project, monkeypatch
    ) -> None:
        def _unexpected_codex(_self, _repo):
            raise AssertionError("dry-run should honor project agent selection")

        monkeypatch.setattr(
            "opentraces.capture.codex_cli.parse.CodexCliParser.discover_project_sessions",
            _unexpected_codex,
        )

        result = runner.invoke(main, ["--json", "_scan", "--dry-run"])
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        assert payload["sessions_seen"] == 1
        assert payload["would"][0]["agent"] == "claude-code"

    def test_scan_dry_run_uses_repo_root_from_nested_cwd(
        self, runner, tmp_path, monkeypatch
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        (repo / ".opentraces.json").write_text(json.dumps({
            "marker_version": "2",
            "project_id": "scan-nested-cwd",
            "review_policy": "review",
            "push_policy": "manual",
            "agents": ["claude-code"],
        }))
        nested = repo / "pkg" / "nested"
        nested.mkdir(parents=True)
        session_path = tmp_path / "synthetic" / "sess-nested-cwd.jsonl"
        _write_session(session_path, "sess-nested-cwd", turns=3)

        monkeypatch.setattr(
            "opentraces.core.ingest.discover_claude_jsonl_corpus",
            lambda _repo: [session_path],
        )
        monkeypatch.chdir(nested)

        result = runner.invoke(main, ["--json", "_scan", "--dry-run"])

        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        assert payload["project"] == str(repo.resolve())
        assert payload["sessions_seen"] == 1

    def test_scan_project_flag_normalizes_nested_repo_path_before_auto_enroll(
        self, runner, tmp_path, monkeypatch
    ) -> None:
        repo = tmp_path / "project"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        nested = repo / "pkg" / "nested"
        nested.mkdir(parents=True)
        session_path = tmp_path / "synthetic" / "sess-auto-root.jsonl"
        _write_session(session_path, "sess-auto-root", turns=3)

        monkeypatch.setattr(
            "opentraces.core.ingest.discover_claude_jsonl_corpus",
            lambda _repo: [session_path],
        )
        monkeypatch.setattr(
            "opentraces.capture.codex_cli.parse.CodexCliParser.discover_project_sessions",
            lambda _self, _repo: iter(()),
        )

        result = runner.invoke(
            main,
            ["--json", "_scan", "--project", str(nested)],
        )

        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        assert payload["project"] == str(repo.resolve())
        assert (repo / ".opentraces.json").is_file()
        assert not (nested / ".opentraces.json").exists()

    def test_scan_missing_session_explains_raw_logs_are_local(
        self, runner, opted_in_project
    ) -> None:
        result = runner.invoke(main, ["_scan", "--session", "missing-session"])

        assert result.exit_code == 3
        assert "raw agent corpus" in result.output
        assert "machine-local" in result.output
        assert "trace get" in result.output

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
