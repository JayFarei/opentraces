"""Cluster C-4 — ``opentraces doctor`` flags trail_capture_incomplete traces.

The doctor command reports a ``trail_capture_audit`` panel that scans
recent traces for the indexer-bug fingerprint (``file_edit`` events
without matching ``trace_patch_created`` events).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.doctor import audit_trail_capture
from opentraces.core.config import _write_marker
from opentraces.core.trails import TrailEventDraft, append_event_batch
from opentraces.core.trails.models import sha256_text


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "app.py").write_text("def value():\n    return 'old'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _emit_file_edit(repo: Path, *, trace_id: str, step_index: int) -> None:
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="file_edit",
                trace_id=trace_id,
                step_index=step_index,
                capture_method=["hook_posttooluse"],
                payload={
                    "file_path": "app.py",
                    "before_text_hash": sha256_text("before"),
                    "after_text_hash": sha256_text("after"),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )


def _emit_patch(repo: Path, *, trace_id: str, step_index: int, patch_id: str) -> None:
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=trace_id,
                step_index=step_index,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": patch_id,
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": "    return 'patch'\n",
                    "raw_authored_hash": sha256_text("authored"),
                    "git_clean_hash": sha256_text("authored"),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )


def test_audit_trail_capture_flags_incomplete_traces(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    # tr-broken: file_edits but no patches => flagged
    _emit_file_edit(tmp_path, trace_id="tr-broken", step_index=1)
    _emit_file_edit(tmp_path, trace_id="tr-broken", step_index=2)
    # tr-ok: both present => not flagged
    _emit_file_edit(tmp_path, trace_id="tr-ok", step_index=1)
    _emit_patch(tmp_path, trace_id="tr-ok", step_index=1, patch_id="tracepatch-sha256:ok-1")

    audit = audit_trail_capture(tmp_path, days=7)
    assert audit["state"] == "warn"
    assert audit["traces_scanned"] == 2
    assert len(audit["incomplete"]) == 1
    entry = audit["incomplete"][0]
    assert entry["trace_id"] == "tr-broken"
    assert entry["file_edits_count"] == 2
    assert entry["patch_created_count"] == 0


def test_audit_trail_capture_ok_when_no_incomplete_traces(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _emit_file_edit(tmp_path, trace_id="tr-ok", step_index=1)
    _emit_patch(tmp_path, trace_id="tr-ok", step_index=1, patch_id="tracepatch-sha256:ok-1")

    audit = audit_trail_capture(tmp_path, days=7)
    assert audit["state"] == "ok"
    assert audit["incomplete"] == []


def test_audit_trail_capture_state_missing_when_repo_has_no_event_log(
    tmp_path: Path,
) -> None:
    """No event log ref yet — audit returns state=ok with zero scanned."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)
    (tmp_path / "x.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True)

    audit = audit_trail_capture(tmp_path, days=7)
    # No events at all — read_events returns [] and audit reports ok.
    assert audit["state"] == "ok"
    assert audit["traces_scanned"] == 0
    assert audit["incomplete"] == []


def test_doctor_reports_trail_capture_incomplete(tmp_path: Path, monkeypatch) -> None:
    """``opentraces doctor --json`` must include ``trail_capture_audit`` and
    flag traces with file_edits but no patches."""
    _init_repo(tmp_path)
    _write_marker(tmp_path, "doctor-c4-test", {})
    _emit_file_edit(tmp_path, trace_id="tr-broken", step_index=1)
    _emit_file_edit(tmp_path, trace_id="tr-broken", step_index=2)

    # Run doctor from inside the project directory so cwd-based panels see it.
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["--json", "doctor"])
    assert result.exit_code in (0, 3), result.output
    # The doctor JSON is wrapped with a ``---OPENTRACES_JSON---`` sentinel
    # in the human/JSON dual output. Strip leading non-JSON lines.
    output = result.output
    if "---OPENTRACES_JSON---" in output:
        output = output.split("---OPENTRACES_JSON---", 1)[1]
    payload = json.loads(output)
    assert "doctor" in payload
    audit = payload["doctor"].get("trail_capture_audit") or {}
    assert audit.get("state") == "warn", audit
    incomplete = audit.get("incomplete") or []
    trace_ids = {row["trace_id"] for row in incomplete}
    assert "tr-broken" in trace_ids
