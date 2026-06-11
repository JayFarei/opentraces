"""Post-commit hook structured logging (plan 047 U3).

The hook is mandatorily silent to the shell, so we assert on the
``.git/opentraces-hook.log`` artifact instead of stderr.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from opentraces.capture.git.post_commit import _hook_log_path, run_for_repo


def _init_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"],
                   cwd=tmp_path, check=True)
    (tmp_path / "x.txt").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _read_log(repo: Path) -> list[dict]:
    log = _hook_log_path(repo)
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def test_unopted_repo_captures_error_not_silent(tmp_path, monkeypatch):
    """Un-opted-in repo: the old hook path swallowed the NotOptedInError
    into ``log.debug`` and was invisible. The new logger must surface it
    in ``error`` so operators can see why nothing happened."""
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _init_repo(tmp_path)
    run_for_repo(repo)

    entries = _read_log(repo)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["notes_written"] is False
    assert entry["sha"]  # HEAD resolved
    # The error must be captured in text form.
    assert entry["error"] is not None
    assert "NotOptedIn" in entry["error"] or "not opted in" in entry["error"].lower()


def test_exception_is_captured_in_log(tmp_path, monkeypatch):
    """An unexpected failure in the hook body must surface in the log,
    not vanish into ``log.debug``."""
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _init_repo(tmp_path)

    # Force the inbox loader to raise so we can verify the error path.
    def _boom(*a, **kw):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(
        "opentraces.core.inbox.load_trace_records", _boom, raising=False,
    )
    # Also make staging appear to exist so we reach load_trace_records.
    monkeypatch.setattr(
        "opentraces.core.config.get_project_traces_dir",
        lambda _repo: (Path(tmp_path) / ".opentraces" / "staging"),
        raising=False,
    )
    (tmp_path / ".opentraces" / "staging").mkdir(parents=True)

    run_for_repo(repo)

    entries = _read_log(repo)
    assert len(entries) == 1
    assert entries[0]["error"]
    assert "synthetic failure" in entries[0]["error"]


def test_trail_anchor_failure_is_logged_without_aborting_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _init_repo(tmp_path)

    import opentraces.core.trails as trails

    def _boom(*a, **kw):
        raise RuntimeError("synthetic anchor failure")

    monkeypatch.setattr(trails, "reconcile_commit_anchors", _boom)
    staging = tmp_path / ".opentraces" / "staging"
    staging.mkdir(parents=True)
    monkeypatch.setattr(
        "opentraces.core.config.get_project_traces_dir",
        lambda _repo: staging,
        raising=False,
    )

    run_for_repo(repo)

    entries = _read_log(repo)
    assert len(entries) == 1
    assert entries[0]["error"] is None
    assert entries[0]["reason"] == "no_traces_in_inbox_window"
    assert entries[0]["trail_anchors_created"] == 0
    assert "synthetic anchor failure" in entries[0]["trail_anchor_error"]


def test_log_rotates_when_past_cap(tmp_path, monkeypatch):
    """When the log exceeds the cap, the writer truncates to the tail."""
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _init_repo(tmp_path)

    import opentraces.capture.git.post_commit as pc
    monkeypatch.setattr(pc, "HOOK_LOG_MAX_BYTES", 1024)

    # Seed an oversized log so the next append triggers truncation.
    log = _hook_log_path(repo)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(b"x" * 4096 + b"\n")

    run_for_repo(repo)

    assert log.stat().st_size < 4096
    # The new structured entry is the last line.
    last = log.read_text().splitlines()[-1]
    rec = json.loads(last)
    assert rec["sha"]
    assert rec["notes_written"] is False


def test_every_run_writes_exactly_one_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _init_repo(tmp_path)
    run_for_repo(repo)
    run_for_repo(repo)
    run_for_repo(repo)
    entries = _read_log(repo)
    assert len(entries) == 3
    for e in entries:
        assert "ts" in e and "sha" in e and "verdicts" in e


# --------------------------------------------------------------------------- #
# pkg-44 #44 — reconcile wall-clock budget surfaced in the hook log.
# --------------------------------------------------------------------------- #

def _opt_in_staging(repo: Path, monkeypatch) -> Path:
    """Point ``get_project_traces_dir`` at an existing (empty) staging dir so
    ``run_for_repo`` reaches ``_reconcile_trail_anchors`` via the
    ``no_traces_in_inbox_window`` path instead of bailing on NotOptedInError."""
    staging = repo / ".opentraces" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "opentraces.core.config.get_project_traces_dir",
        lambda _repo: staging,
        raising=False,
    )
    return staging


def _seed_pending_patch(repo: Path) -> str:
    """Seed one matching trace_patch + a landing commit, returning HEAD.

    Used so the reconcile actually has a patch to search (the budget surface is
    only meaningful when there are patches to visit)."""
    from opentraces.core.trails import GitObjectID, TrailEventDraft, append_event_batch

    authored = "BUDGET = 1\n"
    after = GitObjectID(
        hex=subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo, input=authored, text=True, capture_output=True, check=True,
        ).stdout.strip()
    )
    append_event_batch(
        repo,
        [TrailEventDraft(
            event_type="trace_patch_created",
            trace_id="tr-budget",
            generation_index=0,
            step_index=1,
            capture_method=["hook_posttooluse"],
            payload={
                "trace_patch_id": "tracepatch-sha256:fixture-budget",
                "file_path": "budget.py",
                "affected_range": {"start_line": 1, "end_line": 1},
                "authored_text": authored,
                "raw_authored_hash": "sha256:fixture",
                "git_clean_hash": "sha256:fixture",
                "after_blob_id": after.model_dump(mode="json"),
                "limitations": [],
            },
        )],
        writer="capture-claude-code",
    )
    (repo / "budget.py").write_text(authored)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "land budget"], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def test_budget_fields_present_when_budget_trips(tmp_path, monkeypatch):
    """When the reconcile budget trips, run_for_repo records
    ``trail_anchor_budget_exhausted`` + ``patches_searched`` +
    ``patches_remaining`` in the hook-log entry."""
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _init_repo(tmp_path)
    _seed_pending_patch(repo)
    _opt_in_staging(repo, monkeypatch)

    monkeypatch.setenv("OPENTRACES_HOOK_RECONCILE_BUDGET_SECONDS", "10")
    # post_commit.time and anchors.time are the SAME module object. Drive a
    # monotonic clock that returns a SMALL value for the deadline computation in
    # post_commit (deadline = small + 10) then a LARGE value at the anchors
    # loop-top check, so the budget trips on the first patch.
    import time as _time_mod

    clock = iter([100.0])  # first call: post_commit deadline base = 100 -> 110

    def _stepped_monotonic():
        try:
            return next(clock)
        except StopIteration:
            return 1_000_000.0  # every later (anchors loop-top) call: past 110

    monkeypatch.setattr(_time_mod, "monotonic", _stepped_monotonic)

    run_for_repo(repo)

    entry = _read_log(repo)[-1]
    assert entry.get("trail_anchor_budget_exhausted") is True
    assert "patches_searched" in entry
    assert "patches_remaining" in entry
    assert entry["patches_remaining"] >= 1
    assert entry.get("trail_anchor_error") is None


def test_budget_fields_absent_when_budget_not_tripped(tmp_path, monkeypatch):
    """A normal (non-tripped) reconcile leaves the budget fields OUT of the
    hook-log entry."""
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _init_repo(tmp_path)
    _seed_pending_patch(repo)
    _opt_in_staging(repo, monkeypatch)
    # Generous budget; the tiny reconcile finishes well within it.
    monkeypatch.setenv("OPENTRACES_HOOK_RECONCILE_BUDGET_SECONDS", "600")

    run_for_repo(repo)

    entry = _read_log(repo)[-1]
    assert "trail_anchor_budget_exhausted" not in entry
    assert "patches_searched" not in entry
    assert "patches_remaining" not in entry


def test_budget_value_zero_disables_budget(tmp_path, monkeypatch):
    """``OPENTRACES_HOOK_RECONCILE_BUDGET_SECONDS=0`` disables the budget: the
    reconcile passes ``deadline=None`` so even a far-past monotonic clock never
    trips, and no budget fields are written."""
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _init_repo(tmp_path)
    _seed_pending_patch(repo)
    _opt_in_staging(repo, monkeypatch)

    monkeypatch.setenv("OPENTRACES_HOOK_RECONCILE_BUDGET_SECONDS", "0")
    # Even with a clock that would trip ANY real deadline, disabling means the
    # deadline is None and the loop never short-circuits.
    import opentraces.core.trails.anchors as anchors_mod
    monkeypatch.setattr(anchors_mod.time, "monotonic", lambda: 9_999_999.0)

    run_for_repo(repo)

    entry = _read_log(repo)[-1]
    assert "trail_anchor_budget_exhausted" not in entry
    # The patch was actually anchored (budget disabled, full reconcile ran).
    assert entry["trail_anchors_created"] == 1
