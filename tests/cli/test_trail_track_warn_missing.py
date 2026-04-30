"""Cluster C-4 — ``trail track --warn-missing-patches``.

When a trace's TrailEvents include ``file_edit`` events but no
``trace_patch_created`` events, that's the indexer-bug signal we want to
surface a day before. Adding ``--warn-missing-patches`` to
``trail track <TRACE_ID>`` makes the command emit ``limitations:
["trail_capture_incomplete"]`` plus the counts so callers can flag those
traces.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
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


def _emit_file_edit(repo: Path, *, trace_id: str, step_index: int, file_path: str) -> None:
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="file_edit",
                trace_id=trace_id,
                step_index=step_index,
                capture_method=["hook_posttooluse"],
                payload={
                    "file_path": file_path,
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


def _run(repo: Path, args: list[str]):
    return CliRunner().invoke(main, args + ["--project", str(repo)])


def test_warn_missing_patches_flags_traces_with_no_capture(tmp_path: Path) -> None:
    """Trace with file_edits but no patches => warn surface fires."""
    _init_repo(tmp_path)
    # Two file_edit events, zero patches.
    _emit_file_edit(tmp_path, trace_id="tr-broken", step_index=1, file_path="app.py")
    _emit_file_edit(tmp_path, trace_id="tr-broken", step_index=2, file_path="app.py")

    result = _run(
        tmp_path,
        ["trail", "track", "tr-broken", "--warn-missing-patches", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    limitations = payload.get("limitations") or []
    assert "trail_capture_incomplete" in limitations
    capture = payload.get("trail_capture_audit") or {}
    assert capture.get("file_edits_count") == 2
    assert capture.get("patch_created_count") == 0


def test_warn_missing_patches_silent_when_capture_complete(tmp_path: Path) -> None:
    """Trace with both file_edits and patches => no warning."""
    _init_repo(tmp_path)
    _emit_file_edit(tmp_path, trace_id="tr-ok", step_index=1, file_path="app.py")
    _emit_patch(tmp_path, trace_id="tr-ok", step_index=1, patch_id="tracepatch-sha256:ok-1")

    result = _run(
        tmp_path,
        ["trail", "track", "tr-ok", "--warn-missing-patches", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    limitations = payload.get("limitations") or []
    assert "trail_capture_incomplete" not in limitations


def test_warn_missing_patches_silent_when_no_file_edits(tmp_path: Path) -> None:
    """Trace with no file_edits and no patches => not flagged (nothing
    was attempted, so capture isn't incomplete — it's empty)."""
    _init_repo(tmp_path)
    # Append a non-edit, non-patch event so the trace exists at all.
    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="trace_step_capture_incomplete",
                trace_id="tr-empty",
                step_index=1,
                capture_method=["hook_pretooluse"],
                payload={"reason": "no-edit"},
            ),
        ],
        writer="test-fixture",
    )

    result = _run(
        tmp_path,
        ["trail", "track", "tr-empty", "--warn-missing-patches", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    limitations = payload.get("limitations") or []
    assert "trail_capture_incomplete" not in limitations


def test_warn_missing_patches_only_valid_with_trace_id(tmp_path: Path) -> None:
    """``--warn-missing-patches`` is per-trace; reject with --patch/--anchor
    or batch flags."""
    _init_repo(tmp_path)

    result = _run(
        tmp_path,
        ["trail", "track", "--patch", "tracepatch-sha256:foo", "--warn-missing-patches"],
    )
    assert result.exit_code == 2

    result = _run(
        tmp_path,
        ["trail", "track", "--all", "--warn-missing-patches", "--json"],
    )
    assert result.exit_code == 2
