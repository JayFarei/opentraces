"""Tests for ``ot resume`` — agent-specific REPL handoff."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def resume_project(tmp_path, monkeypatch):
    """Tmp project with an opted-in marker and one claude-code trace.

    HOME + OPENTRACES_DIR isolation is already provided by the autouse
    ``_isolate_opentraces_global_state`` fixture in tests/conftest.py via
    ``monkeypatch.setattr`` on module globals. Previously this fixture
    did ``importlib.reload`` on core modules, which broke class identity
    for any later test using ``pytest.raises(UnknownRemoteError)`` —
    that check compared the reloaded class against the old one imported
    at module-collection time.
    """
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".opentraces.json").write_text(
        '{"project_id": "abc123", "policy": {}}'
    )
    monkeypatch.chdir(project)

    from opentraces.core import config as _config
    traces_dir = _config.get_project_traces_dir(project)
    traces_dir.mkdir(parents=True, exist_ok=True)

    # Minimal TraceRecord with a claude-code agent.
    record = {
        "schema_version": "0.2.1",
        "trace_id": "b73af9c8-full-id-1234",
        "session_id": "ff00aa11-sess-id-5678",
        "agent": {"name": "claude-code", "model": "claude-opus"},
        "task": {"description": "do a thing"},
        "timestamp_start": "2026-04-14T00:00:00Z",
        "timestamp_end": "2026-04-14T00:05:00Z",
        "steps": [],
        "metrics": {},
    }
    import json
    (traces_dir / "b73af9c8-full-id-1234.jsonl").write_text(
        json.dumps(record) + "\n"
    )

    # State entry (so _load_project_state is happy).
    from opentraces.core import state as _state
    st = _state.StateManager(_config.get_project_state_path(project))
    st._state.setdefault("traces", {})["b73af9c8-full-id-1234"] = {
        "trace_id": "b73af9c8-full-id-1234",
        "session_id": "ff00aa11-sess-id-5678",
        "status": "parsed",
        "created_at": 0.0,
    }
    st.save()

    yield project


def test_resume_dry_run_prints_claude_cmd(runner, resume_project):
    from opentraces.cli import main
    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        result = runner.invoke(main, ["resume", "t:b7", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "claude" in result.output
    assert "--resume" in result.output
    assert "ff00aa11-sess-id-5678" in result.output


def test_resume_bare_prefix(runner, resume_project):
    from opentraces.cli import main
    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        result = runner.invoke(main, ["resume", "b73af9c8", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "--resume ff00aa11-sess-id-5678" in result.output


def test_resume_missing_claude_returns_127(runner, resume_project):
    from opentraces.cli import main
    with patch("shutil.which", return_value=None):
        result = runner.invoke(main, ["resume", "t:b7"])
    assert result.exit_code == 127, result.output
    assert "not on PATH" in result.output
    assert "claude --resume ff00aa11" in result.output


def test_resume_unknown_prefix_returns_6(runner, resume_project):
    from opentraces.cli import main
    result = runner.invoke(main, ["resume", "t:9999", "--dry-run"])
    assert result.exit_code == 6, result.output


def test_resume_too_short_prefix_errors(runner, resume_project):
    from opentraces.cli import main
    result = runner.invoke(main, ["resume", "b", "--dry-run"])
    assert result.exit_code == 2, result.output


def test_resume_non_claude_agent_prints_hint(runner, resume_project):
    from opentraces.core import config as _config
    import json
    # Overwrite the trace with a hermes agent.
    tfile = _config.get_project_traces_dir(resume_project) / "b73af9c8-full-id-1234.jsonl"
    rec = json.loads(tfile.read_text().strip())
    rec["agent"]["name"] = "hermes"
    tfile.write_text(json.dumps(rec) + "\n")

    from opentraces.cli import main
    result = runner.invoke(main, ["resume", "t:b7"])
    assert result.exit_code == 0, result.output
    assert "hermes" in result.output or "No native resume" in result.output


def test_resume_execvp_invoked_without_dry_run(runner, resume_project):
    from opentraces.cli import main
    # SystemExit path: resume_claude_code calls execvp which we mock; the
    # command then calls sys.exit(rc). execvp doesn't return normally,
    # so mock it to raise a sentinel we can catch.
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("os.execvp") as exec_mock, \
         patch("os.chdir"):
        result = runner.invoke(main, ["resume", "t:b7"])
    # Since execvp is mocked, agent_resume falls through to `return 1`.
    assert exec_mock.called
    args, _ = exec_mock.call_args
    assert args[0] == "/usr/local/bin/claude"
    assert args[1] == ["/usr/local/bin/claude", "--resume", "ff00aa11-sess-id-5678"]
    assert result.exit_code == 1
