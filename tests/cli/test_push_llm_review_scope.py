"""Regression test: ``push --llm-review`` must only gate on the staged
(``COMMITTED``) set, not every JSONL in the staging directory.

User-reported symptom: choosing "LLM review then push" in the TUI
modal aborted with a list of *every* captured trace, even inbox ones
that the user hadn't staged. The gate used to iterate
``load_traces(staging_dir)`` with no status filter.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.cli import main, SENTINEL
from opentraces.core.state import StateManager, TraceStatus


def _extract_json(output: str) -> dict:
    idx = output.find(SENTINEL)
    assert idx >= 0, f"no sentinel in output:\n{output}"
    return json.loads(output[idx + len(SENTINEL):].strip())


def _make_record(trace_id: str, description: str) -> dict:
    return {
        "trace_id": trace_id,
        "schema_version": "0.2.0",
        "timestamp_start": "2026-04-15T10:00:00Z",
        "timestamp_end": "2026-04-15T10:00:00Z",
        "agent": {"name": "claude-code", "model": "anthropic/claude-opus-4-6"},
        "task": {"description": description},
        "metrics": {"total_steps": 0},
        "steps": [],
    }


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    opentraces_dir = home / ".opentraces"
    opentraces_dir.mkdir()
    projects_dir = opentraces_dir / "projects"
    projects_dir.mkdir()

    from opentraces.core import config as _config, paths as _paths
    for mod in (_paths, _config):
        monkeypatch.setattr(mod, "OPENTRACES_DIR", opentraces_dir)
        monkeypatch.setattr(mod, "CONFIG_PATH", opentraces_dir / "config.json")
        monkeypatch.setattr(mod, "CREDENTIALS_PATH", opentraces_dir / "credentials")
        monkeypatch.setattr(mod, "PROJECTS_DIR", projects_dir)
    return opentraces_dir


@pytest.fixture
def project_with_mixed_traces(tmp_path, monkeypatch, isolated_config):
    """Build a project with one INBOX trace and one COMMITTED trace."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".opentraces.json").write_text(json.dumps({
        "marker_version": "2",
        "project_id": "test-project-push-scope",
        "review_policy": "review",
        "push_policy": "manual",
        "remotes": {"origin": {"url": "alice/opentraces", "visibility": "private"}},
        "active_remote": "origin",
        "agents": ["claude-code"],
    }))
    monkeypatch.chdir(project)

    from opentraces.core.config import (
        get_project_state_path, get_project_traces_dir,
    )
    staging = get_project_traces_dir(project)
    staging.mkdir(parents=True, exist_ok=True)

    inbox = _make_record("trace_inbox_999", "inbox one — user has not staged")
    staged = _make_record("trace_staged_888", "committed — goes to push")
    for rec in (inbox, staged):
        (staging / f"{rec['trace_id']}.jsonl").write_text(json.dumps(rec) + "\n")

    sm = StateManager(state_path=get_project_state_path(project))
    sm.set_trace_status("trace_staged_888", TraceStatus.COMMITTED)
    return project, staging


def test_llm_review_gate_scopes_to_committed_only(project_with_mixed_traces):
    """The blocked list must contain only the COMMITTED trace, not the inbox one."""
    _project, _staging = project_with_mixed_traces
    runner = CliRunner()
    result = runner.invoke(main, ["push", "--llm-review"])
    # Gate trips with exit 3 (LLM_REVIEW_BLOCKED).
    assert result.exit_code == 3, result.output
    payload = _extract_json(result.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "LLM_REVIEW_BLOCKED"
    # Human-readable line lists only the staged trace.
    assert "trace_staged_888" in result.output
    assert "trace_inbox_999" not in result.output, (
        "inbox traces must not be included in the --llm-review gate"
    )
