"""Tests for the LLM Field-Intent Auditor (plan 040)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from opentraces_schema import TraceRecord, SCHEMA_VERSION
from opentraces_schema.models import (
    Agent, Task, Environment, VCS, Step, ToolCall, Observation,
    TokenUsage, Outcome, Metrics, SecurityMetadata,
)

from opentraces.quality.schema_audit import FIELD_SPECS
from opentraces.quality.field_intent import generator, auditor
from opentraces.quality.field_intent.generator import (
    FieldIntent,
    load_spec,
    save_spec,
    missing_paths,
    draft_entry,
    fill_interactive,
    _run_claude,
)
from opentraces.quality.field_intent.auditor import (
    FieldFinding,
    AuditReport,
    cross_field_checks,
    check_completeness,
    format_report,
)


ALL_PATHS = [fs.path for fs in FIELD_SPECS]


def _min_trace(**overrides) -> TraceRecord:
    base = dict(
        trace_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        session_id="s1",
        agent=Agent(name="claude-code"),
        steps=[
            Step(step_index=0, role="user", content="hi"),
            Step(step_index=1, role="agent", content="ok"),
        ],
        metrics=Metrics(total_steps=2, total_input_tokens=0, total_output_tokens=0),
    )
    base.update(overrides)
    return TraceRecord(**base)


# ---------------------------------------------------------------------------
# generator: load/save/missing
# ---------------------------------------------------------------------------

def test_load_spec_missing_file_returns_empty(tmp_path):
    assert load_spec(tmp_path / "nope.yaml") == {}


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "fi.yaml"
    entries = {
        "task.description": FieldIntent(
            path="task.description",
            description="First user message",
            good_example="Fix the login bug",
            failure_modes=["Empty", "Truncated mid-word"],
        ),
    }
    save_spec(entries, p)
    loaded = load_spec(p)
    assert loaded == entries


def test_missing_paths_empty_spec_lists_all(tmp_path):
    assert set(missing_paths({}, FIELD_SPECS)) == set(ALL_PATHS)


def test_missing_paths_full_spec_empty(tmp_path):
    full = {
        fs.path: FieldIntent(path=fs.path, description="d", good_example="e", failure_modes=[])
        for fs in FIELD_SPECS
    }
    assert missing_paths(full, FIELD_SPECS) == []


def test_branch_schema_fields_are_in_inventory():
    branch_paths = {
        "execution_context",
        "task.repository_url",
        "outcome.terminal_state",
        "outcome.reward",
        "outcome.reward_source",
        "attribution.revision",
        "attribution.unaccounted_files",
        "attribution.files[].conversations[].ids",
        "attribution.files[].conversations[].related",
        "attribution.files[].conversations[].ranges[].change_type",
        "attribution.files[].conversations[].ranges[].original",
        "attribution.files[].conversations[].ranges[].contributor",
        "lifecycle",
        "generation_index",
        "git_links",
        "git_links[].vcs_type",
        "git_links[].revision",
        "git_links[].repo_url",
        "git_links[].branch",
        "git_links[].tier",
        "git_links[].commit_reachable",
        "git_links[].content_alive",
    }
    assert branch_paths <= set(ALL_PATHS)


# ---------------------------------------------------------------------------
# generator: append-only behavior
# ---------------------------------------------------------------------------

def test_fill_interactive_is_append_only(tmp_path, monkeypatch):
    p = tmp_path / "fi.yaml"
    existing = {
        "task.description": FieldIntent(
            path="task.description",
            description="KEEP ME",
            good_example="original",
            failure_modes=["orig"],
        ),
    }
    save_spec(existing, p)
    before_bytes = p.read_bytes()

    # Force every draft to yield a fixed entry; only missing fields should prompt.
    prompts: list[str] = []

    def fake_draft(path, spec, model="haiku"):
        prompts.append(path)
        return {"description": f"d-{path}", "good_example": f"e-{path}", "failure_modes": ["f"]}

    monkeypatch.setattr(generator, "draft_entry", fake_draft)
    # Accept draft verbatim (no editing).
    monkeypatch.setattr(generator, "_confirm_entry", lambda path, draft: FieldIntent(
        path=path, description=draft["description"], good_example=draft["good_example"],
        failure_modes=draft["failure_modes"],
    ))

    rc = fill_interactive(p, model="haiku", non_interactive=False)
    assert rc == 0

    # Existing entry untouched (byte-identical substring preserved)
    reloaded = load_spec(p)
    assert reloaded["task.description"] == existing["task.description"]
    # All other paths got prompted exactly once
    assert "task.description" not in prompts
    assert set(prompts) == set(ALL_PATHS) - {"task.description"}


def test_fill_interactive_noop_when_complete(tmp_path, monkeypatch):
    p = tmp_path / "fi.yaml"
    full = {
        fs.path: FieldIntent(path=fs.path, description="d", good_example="e", failure_modes=[])
        for fs in FIELD_SPECS
    }
    save_spec(full, p)
    snapshot = p.read_bytes()

    monkeypatch.setattr(generator, "draft_entry", lambda *a, **k: pytest.fail("should not draft"))

    rc = fill_interactive(p, model="haiku", non_interactive=True)
    assert rc == 0
    assert p.read_bytes() == snapshot


def test_fill_non_interactive_missing_fields_exits_nonzero_and_writes_nothing(tmp_path):
    p = tmp_path / "fi.yaml"
    # no file → all fields missing
    rc = fill_interactive(p, model="haiku", non_interactive=True)
    assert rc != 0
    assert not p.exists()


# ---------------------------------------------------------------------------
# generator: _run_claude binary-missing path
# ---------------------------------------------------------------------------

def test_run_claude_missing_binary_returns_none(monkeypatch):
    monkeypatch.setattr(generator.shutil, "which", lambda _: None)
    assert _run_claude("hello", model="haiku") is None


def test_run_claude_parses_headless_json(monkeypatch):
    monkeypatch.setattr(generator.shutil, "which", lambda _: "/fake/claude")
    payload = {"result": json.dumps({"description": "d", "good_example": "e", "failure_modes": []})}

    class R:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    monkeypatch.setattr(generator.subprocess, "run", lambda *a, **k: R())
    out = _run_claude("prompt", model="haiku")
    assert out == {"description": "d", "good_example": "e", "failure_modes": []}


def test_draft_entry_returns_none_when_claude_absent(monkeypatch):
    monkeypatch.setattr(generator, "_run_claude", lambda *a, **k: None)
    spec = FIELD_SPECS[0]
    assert draft_entry(spec.path, spec) is None


# ---------------------------------------------------------------------------
# auditor: cross-field checks
# ---------------------------------------------------------------------------

def test_cross_field_total_steps_mismatch_detected():
    t = _min_trace(metrics=Metrics(total_steps=999, total_input_tokens=0, total_output_tokens=0))
    findings = cross_field_checks(t)
    paths = {(f.field_path, f.suspected_cause) for f in findings}
    assert ("metrics.total_steps", "enrichment_gap") in paths


def test_cross_field_orphan_tool_call_detected():
    t = _min_trace(steps=[
        Step(
            step_index=1, role="agent",
            tool_calls=[ToolCall(tool_call_id="tc_x", tool_name="Read", input={})],
            observations=[],  # orphan
        ),
    ], metrics=Metrics(total_steps=1, total_input_tokens=0, total_output_tokens=0))
    findings = cross_field_checks(t)
    causes = {f.suspected_cause for f in findings if f.field_path.startswith("steps[].tool_calls")}
    assert "parser_bug" in causes


def test_cross_field_committed_without_sha_detected():
    t = _min_trace(outcome=Outcome(
        success=True, signal_source="heuristic", signal_confidence="derived",
        committed=True, commit_sha=None,
    ))
    findings = cross_field_checks(t)
    assert any(f.field_path == "outcome.committed" for f in findings)


def test_cross_field_clean_trace_no_findings():
    t = _min_trace()
    findings = cross_field_checks(t)
    assert findings == []


# ---------------------------------------------------------------------------
# auditor: completeness gate
# ---------------------------------------------------------------------------

def test_check_completeness_refuses_incomplete(tmp_path):
    spec_path = tmp_path / "fi.yaml"
    save_spec({}, spec_path)
    ok, msg = check_completeness(spec_path)
    assert ok is False
    assert "_audit-spec" in msg


def test_check_completeness_passes_on_full(tmp_path):
    spec_path = tmp_path / "fi.yaml"
    full = {
        fs.path: FieldIntent(path=fs.path, description="d", good_example="e", failure_modes=[])
        for fs in FIELD_SPECS
    }
    save_spec(full, spec_path)
    ok, msg = check_completeness(spec_path)
    assert ok is True


# ---------------------------------------------------------------------------
# auditor: report formatting + binary-missing skip path
# ---------------------------------------------------------------------------

def test_format_report_includes_findings():
    rep = AuditReport(
        traces_sampled=3,
        llm_skipped=True,
        llm_skip_reason="claude binary missing",
        findings=[
            FieldFinding(trace_id="t1", field_path="metrics.total_steps",
                         verdict="wrong", evidence="2 != 3",
                         suspected_cause="enrichment_gap"),
        ],
    )
    out = format_report(rep)
    assert "metrics.total_steps" in out
    assert "skipped" in out.lower()
    assert "enrichment_gap" in out


def test_audit_run_emits_cross_field_when_binary_missing(tmp_path, monkeypatch):
    spec_path = tmp_path / "fi.yaml"
    full = {
        fs.path: FieldIntent(path=fs.path, description="d", good_example="e", failure_modes=[])
        for fs in FIELD_SPECS
    }
    save_spec(full, spec_path)

    # inject a broken trace with a known cross-field violation
    staging = tmp_path / "staging"
    staging.mkdir()
    t = _min_trace(metrics=Metrics(total_steps=99, total_input_tokens=0, total_output_tokens=0))
    (staging / "t.jsonl").write_text(t.model_dump_json())

    monkeypatch.setattr(auditor.shutil, "which", lambda _: None)
    rep = auditor.audit_run(
        sample=5, staging_dir=staging, spec_path=spec_path, model="haiku",
    )
    assert rep.llm_skipped
    assert any(f.field_path == "metrics.total_steps" for f in rep.findings)
