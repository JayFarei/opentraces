"""Tests for ``ot resume`` — agent-specific REPL handoff."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner


# Stable dashed UUID for --at-step tests. Dashes matter: ``claude --resume``
# matches on the session filename, and every Claude session file is UUID-
# with-dashes (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.jsonl). The no-dash
# hex form is invisible to Claude's session discovery.
_FAKE_NEW_SESSION = uuid.UUID("11111111-2222-3333-4444-555555555555")
_FAKE_NEW_SESSION_STR = str(_FAKE_NEW_SESSION)


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
    source_session = project / "source-session.jsonl"
    source_session.write_text(
        "\n".join([
            json.dumps({"type": "user", "uuid": "line-001", "message": {"role": "user", "content": "start"}}),
            json.dumps({"type": "assistant", "uuid": "line-002", "message": {"role": "assistant", "content": [{"type": "text", "text": "inspect"}]}}),
            json.dumps({"type": "assistant", "uuid": "line-003", "message": {"role": "assistant", "content": [{"type": "text", "text": "patch"}]}}),
        ]) + "\n"
    )

    record = {
        "schema_version": "0.3.0",
        "trace_id": "b73af9c8-full-id-1234",
        "session_id": "ff00aa11-sess-id-5678",
        "agent": {"name": "claude-code", "model": "claude-opus"},
        "task": {"description": "do a thing"},
        "timestamp_start": "2026-04-14T00:00:00Z",
        "timestamp_end": "2026-04-14T00:05:00Z",
        "steps": [
            {
                "step_index": 1,
                "role": "user",
                "content": "start",
                "call_type": "main",
                "parent_step": None,
            },
            {
                "step_index": 2,
                "role": "agent",
                "content": "inspect",
                "call_type": "main",
                "parent_step": 1,
            },
            {
                "step_index": 3,
                "role": "agent",
                "content": "patch",
                "call_type": "main",
                "parent_step": 2,
            },
        ],
        "metrics": {},
    }
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
    st.upsert_session(
        session_id="ff00aa11-sess-id-5678",
        source_path=str(source_session),
        observed_size=source_session.stat().st_size,
        observed_mtime=source_session.stat().st_mtime,
    )
    # Anchors live in local state (plan 048), not on the trace record.
    st.set_step_anchors(
        "b73af9c8-full-id-1234",
        {
            1: {"entry_uuid": "line-001", "line_no": 0, "session_file_relpath": None},
            2: {"entry_uuid": "line-002", "line_no": 1, "session_file_relpath": None},
            3: {"entry_uuid": "line-003", "line_no": 2, "session_file_relpath": None},
        },
    )
    st.save()

    yield project


def test_resume_dry_run_prints_claude_cmd(runner, resume_project):
    from opentraces.cli import main
    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        result = runner.invoke(main, ["trail", "resume", "t:b7", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "claude" in result.output
    assert "--resume" in result.output
    assert "ff00aa11-sess-id-5678" in result.output


def test_resume_bare_prefix(runner, resume_project):
    from opentraces.cli import main
    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        result = runner.invoke(main, ["trail", "resume", "b73af9c8", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "--resume ff00aa11-sess-id-5678" in result.output


def test_resume_missing_claude_returns_127(runner, resume_project):
    from opentraces.cli import main
    with patch("shutil.which", return_value=None):
        result = runner.invoke(main, ["trail", "resume", "t:b7"])
    assert result.exit_code == 127, result.output
    assert "not on PATH" in result.output
    assert "claude --resume ff00aa11" in result.output


def test_resume_unknown_prefix_returns_6(runner, resume_project):
    from opentraces.cli import main
    result = runner.invoke(main, ["trail", "resume", "t:9999", "--dry-run"])
    assert result.exit_code == 6, result.output


def test_resume_too_short_prefix_errors(runner, resume_project):
    from opentraces.cli import main
    result = runner.invoke(main, ["trail", "resume", "b", "--dry-run"])
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
    result = runner.invoke(main, ["trail", "resume", "t:b7"])
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
        result = runner.invoke(main, ["trail", "resume", "t:b7"])
    # Since execvp is mocked, agent_resume falls through to `return 1`.
    assert exec_mock.called
    args, _ = exec_mock.call_args
    assert args[0] == "/usr/local/bin/claude"
    assert args[1] == ["/usr/local/bin/claude", "--resume", "ff00aa11-sess-id-5678"]
    assert result.exit_code == 1


def test_resume_at_step_dry_run_prints_new_session_and_truncation(runner, resume_project, monkeypatch):
    from opentraces.cli import main

    fake_home = resume_project / "fake-home"
    monkeypatch.setenv("HOME", str(fake_home))

    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("uuid.uuid4", return_value=_FAKE_NEW_SESSION):
        result = runner.invoke(main, ["trail", "resume", "t:b7", "--at-step", "s2", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert f"claude --resume {_FAKE_NEW_SESSION_STR}" in result.output
    assert "would truncate 2 lines" in result.output
    assert f"new session {_FAKE_NEW_SESSION_STR}" in result.output


def test_resume_at_step_materializes_new_session_and_execs(runner, resume_project, monkeypatch):
    from opentraces.cli import main
    from opentraces.core.repo_identity import encode_claude_path

    fake_home = resume_project / "fake-home"
    monkeypatch.setenv("HOME", str(fake_home))

    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("uuid.uuid4", return_value=_FAKE_NEW_SESSION), \
         patch("os.execvp") as exec_mock, \
         patch("os.chdir"):
        result = runner.invoke(main, ["trail", "resume", "t:b7", "--at-step", "s2"])

    new_session_path = (
        fake_home / ".claude" / "projects" / encode_claude_path(resume_project) / f"{_FAKE_NEW_SESSION_STR}.jsonl"
    )
    assert new_session_path.exists()
    lines = [json.loads(line) for line in new_session_path.read_text().splitlines()]
    assert lines[0]["type"] == "opentraces_resume_parent"
    assert lines[0]["parentSessionId"] == "ff00aa11-sess-id-5678"
    assert lines[0]["parentStepId"] == "s2"
    assert lines[1]["uuid"] == "line-001"
    assert lines[2]["uuid"] == "line-002"
    assert len(lines) == 3

    args, _ = exec_mock.call_args
    assert args[0] == "/usr/local/bin/claude"
    assert args[1] == ["/usr/local/bin/claude", "--resume", _FAKE_NEW_SESSION_STR]
    assert result.exit_code == 1


def test_resume_at_step_falls_back_to_anchor_session_path(
    runner,
    resume_project,
    monkeypatch,
):
    """If the state's session record is missing (fresh state, purged cache),
    resolve_at_step must still find the source JSONL by combining the
    anchor's ``session_file_relpath`` with ``~/.claude/projects/``."""
    from opentraces.cli import main
    from opentraces.core.config import (
        get_project_state_path,
        get_projects_path,
        load_config,
    )
    from opentraces.core.repo_identity import encode_claude_path
    from opentraces.core.state import StateManager

    fake_home = resume_project / "fake-home"
    monkeypatch.setenv("HOME", str(fake_home))

    source_session = (
        get_projects_path(load_config())
        / encode_claude_path(resume_project)
        / "ff00aa11-sess-id-5678.jsonl"
    )
    source_session.parent.mkdir(parents=True, exist_ok=True)
    source_session.write_text(
        "\n".join([
            json.dumps({"type": "user", "uuid": "line-001", "message": {"role": "user", "content": "start"}}),
            json.dumps({"type": "assistant", "uuid": "line-002", "message": {"role": "assistant", "content": [{"type": "text", "text": "inspect"}]}}),
            json.dumps({"type": "assistant", "uuid": "line-003", "message": {"role": "assistant", "content": [{"type": "text", "text": "patch"}]}}),
        ]) + "\n"
    )

    # Re-seed anchors with a session_file_relpath — the fallback path the
    # resolver falls back to when state.get_session(...) is missing.
    state = StateManager(get_project_state_path(resume_project))
    relpath = f"{encode_claude_path(resume_project)}/ff00aa11-sess-id-5678.jsonl"
    state.set_step_anchors(
        "b73af9c8-full-id-1234",
        {
            1: {"entry_uuid": "line-001", "line_no": 0, "session_file_relpath": relpath},
            2: {"entry_uuid": "line-002", "line_no": 1, "session_file_relpath": relpath},
            3: {"entry_uuid": "line-003", "line_no": 2, "session_file_relpath": relpath},
        },
    )
    state._state["sessions"].pop("ff00aa11-sess-id-5678", None)
    state.save()

    fallback_uuid = uuid.UUID("22222222-3333-4444-5555-666666666666")
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("uuid.uuid4", return_value=fallback_uuid):
        result = runner.invoke(main, ["trail", "resume", "t:b7", "--at-step", "s2", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert f"claude --resume {fallback_uuid}" in result.output
    assert "would truncate 2 lines" in result.output


def test_resume_at_step_fork_lineage_survives_ingest_tick(
    runner, resume_project, monkeypatch
):
    """End-to-end: materialize a fork, then simulate the normal ingest
    tick re-observing the forked session grow. The parent_session_id /
    parent_step_id recorded at fork time must NOT be wiped by the ingest
    upsert (which doesn't know about fork lineage and passes no parent
    kwargs).
    """
    from opentraces.cli import main
    from opentraces.core.config import get_project_state_path
    from opentraces.core.state import StateManager

    fake_home = resume_project / "fake-home"
    monkeypatch.setenv("HOME", str(fake_home))

    fork_uuid = uuid.UUID("77777777-8888-9999-aaaa-bbbbbbbbbbbb")
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("uuid.uuid4", return_value=fork_uuid), \
         patch("os.execvp"), \
         patch("os.chdir"):
        result = runner.invoke(main, ["trail", "resume", "t:b7", "--at-step", "s2"])
    assert result.exit_code == 1  # execvp mocked, agent_resume returns 1

    state = StateManager(get_project_state_path(resume_project))
    forked = state.get_session(str(fork_uuid))
    assert forked is not None
    assert forked.parent_session_id == "ff00aa11-sess-id-5678"
    assert forked.parent_step_id == "s2"

    # Simulate the ingest watcher tick observing the forked JSONL grow.
    # It has no idea this session is a fork and passes no parent kwargs.
    state.upsert_session(
        session_id=str(fork_uuid),
        source_path=forked.source_path,
        observed_size=forked.observed_size + 1234,
        observed_mtime=forked.observed_mtime + 10,
    )

    reloaded = StateManager(get_project_state_path(resume_project))
    after = reloaded.get_session(str(fork_uuid))
    assert after is not None
    assert after.observed_size == forked.observed_size + 1234
    assert after.parent_session_id == "ff00aa11-sess-id-5678"
    assert after.parent_step_id == "s2"
