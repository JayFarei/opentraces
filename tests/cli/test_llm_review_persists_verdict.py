"""Regression: ``llm-review`` must write verdicts back into the trace
JSONL so the subsequent ``push --llm-review`` gate can see them.

Symptom (user-reported from the TUI push modal): step 1 ran
``opentraces llm-review --scope staged`` and the log confirmed a
clean ``shareable: yes`` verdict; step 2 then ran ``opentraces push
--llm-review`` and aborted with "not reviewed" on the exact same
trace. Root cause: ``run_llm_review`` returned verdicts but nothing
wrote them back — persistence was planned (the gate reads
``metadata.llm_review``) but never wired.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.state import StateManager, TraceStatus


def _make_record(trace_id: str, content_hash: str = "ch_x") -> dict:
    return {
        "trace_id": trace_id,
        "content_hash": content_hash,
        "schema_version": "0.2.0",
        "timestamp_start": "2026-04-15T10:00:00Z",
        "timestamp_end": "2026-04-15T10:00:00Z",
        "agent": {"name": "claude-code", "model": "anthropic/claude-opus-4-6"},
        "task": {"description": "needs review"},
        "metrics": {"total_steps": 1},
        "steps": [{"role": "user", "content": "hello",
                   "timestamp": "2026-04-15T10:00:00Z"}],
    }


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    opentraces_dir = home / ".opentraces"; opentraces_dir.mkdir()
    projects_dir = opentraces_dir / "projects"; projects_dir.mkdir()

    from opentraces.core import config as _config, paths as _paths
    for mod in (_paths, _config):
        monkeypatch.setattr(mod, "OPENTRACES_DIR", opentraces_dir)
        monkeypatch.setattr(mod, "CONFIG_PATH", opentraces_dir / "config.json")
        monkeypatch.setattr(mod, "CREDENTIALS_PATH", opentraces_dir / "credentials")
        monkeypatch.setattr(mod, "PROJECTS_DIR", projects_dir)

    proj = tmp_path / "proj"; proj.mkdir()
    (proj / ".opentraces.json").write_text(json.dumps({
        "marker_version": "2",
        "project_id": "test-llm-persist",
        "review_policy": "review",
        "push_policy": "manual",
        "remotes": {"origin": {"url": "alice/opentraces", "visibility": "private"}},
        "active_remote": "origin",
        "agents": ["claude-code"],
    }))
    monkeypatch.chdir(proj)

    from opentraces.core.config import (
        get_project_state_path, get_project_traces_dir,
    )
    staging = get_project_traces_dir(proj)
    staging.mkdir(parents=True, exist_ok=True)
    rec = _make_record("claude-code_7edbd13c-65fb-49d6-a28e-dd0e42637f45")
    jsonl = staging / f"{rec['trace_id']}.jsonl"
    jsonl.write_text(json.dumps(rec) + "\n")
    sm = StateManager(state_path=get_project_state_path(proj))
    sm.set_trace_status(rec["trace_id"], TraceStatus.COMMITTED)
    return proj, staging, jsonl, rec["trace_id"]


def test_clean_verdict_is_persisted_to_trace_metadata(project):
    _proj, _staging, jsonl, tid = project
    # Stub the LLM call so the test doesn't require a local model. This
    # produces a clean shareable verdict shaped like the real outcome.
    from opentraces.security.llm_review import LLMReviewVerdict

    def _fake_review_trace(steps, provider, context):
        return LLMReviewVerdict(
            shareable="yes",
            missed_sensitive_data="no",
            summary="clean",
            flagged_parts=[],
        )

    runner = CliRunner()
    # run_llm_review imports ``review_trace`` and ``build_provider``
    # locally from security.llm_review / security.llm_provider, so patch
    # at source so the function picks up the fakes on its next call.
    with patch("opentraces.security.llm_review.review_trace",
               _fake_review_trace), \
            patch("opentraces.security.llm_provider.build_provider",
                  return_value=object()):
        result = runner.invoke(main, [
            "llm-review", "--scope", "staged",
            "--api-format", "fake", "--model", "test",
        ])
    assert result.exit_code == 0, result.output

    # The verdict is now in the trace JSONL's metadata.
    rec = json.loads(jsonl.read_text().strip().splitlines()[0])
    meta = rec.get("metadata", {}).get("llm_review") or {}
    assert meta.get("status") == "complete", meta
    assert meta.get("shareable") == "yes", meta


def test_blocking_verdict_moves_trace_to_blocked_state(project):
    _proj, _staging, jsonl, tid = project
    from opentraces.security.llm_review import LLMReviewVerdict

    def _fake_review_trace(steps, provider, context):
        return LLMReviewVerdict(
            shareable="no",
            missed_sensitive_data="yes",
            summary="contains a password",
            flagged_parts=[{"text": "password=...", "reason": "credential"}],
        )

    runner = CliRunner()
    # run_llm_review imports ``review_trace`` and ``build_provider``
    # locally from security.llm_review / security.llm_provider, so patch
    # at source so the function picks up the fakes on its next call.
    with patch("opentraces.security.llm_review.review_trace",
               _fake_review_trace), \
            patch("opentraces.security.llm_provider.build_provider",
                  return_value=object()):
        result = runner.invoke(main, [
            "llm-review", "--scope", "staged",
            "--api-format", "fake", "--model", "test",
        ])
    assert result.exit_code == 0, result.output

    # Trace is now BLOCKED in state, so it leaves the staged bucket and
    # the user sees it flagged rather than silently failing the push gate.
    from opentraces.core.config import get_project_state_path
    sm = StateManager(state_path=get_project_state_path(_proj))
    entry = sm.get_trace(tid)
    assert entry is not None
    raw = entry.status
    assert raw == TraceStatus.BLOCKED.value or raw == TraceStatus.BLOCKED, raw
