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
