"""Runtime polling observer for Plan 54 filesystem mutations."""

from __future__ import annotations

import subprocess
from pathlib import Path

from opentraces.capture.fs_watcher.runtime import poll_project_once
from opentraces.core.trails import (
    close_step_window_with_snapshot,
    open_step_window,
    read_events,
    reconcile_watcher_observations,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "app.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _init_empty_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def test_polling_observer_baselines_then_emits_mutation(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    state = tmp_path / ".git" / "fs-state.json"

    baseline = poll_project_once(tmp_path, state_path=state)
    assert baseline.baseline_initialized is True
    assert baseline.observations == []

    (tmp_path / "app.py").write_text("x = 2\n")
    result = poll_project_once(tmp_path, state_path=state)

    assert result.baseline_initialized is False
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.event_type == "filesystem_mutation_observed"
    assert observation.trace_id is None
    assert observation.step_index is None
    assert observation.payload["path"] == "app.py"
    assert observation.payload["before_blob_id"]["hex"]
    assert observation.payload["after_blob_id"]["hex"]


def test_polling_observer_respects_gitignore(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("ignored.log\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "ignore logs"], cwd=tmp_path, check=True)
    state = tmp_path / ".git" / "fs-state.json"

    poll_project_once(tmp_path, state_path=state)
    (tmp_path / "ignored.log").write_text("secret-ish local noise\n")
    result = poll_project_once(tmp_path, state_path=state)

    assert result.observations == []


def test_polling_observer_skips_internal_opentraces_paths(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / ".agent-trace").mkdir()
    (tmp_path / ".agent-trace" / "traces.jsonl").write_text("{}\n")

    result = poll_project_once(tmp_path, state_path=tmp_path / ".git" / "fs-state.json")

    assert result.paths_seen == 1
    assert result.observations == []


def test_polling_observer_skips_symlinks_without_hashing_targets(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("do-not-store\n")
    (tmp_path / "linked-secret.txt").symlink_to(outside)

    result = poll_project_once(tmp_path, state_path=tmp_path / ".git" / "fs-state.json")

    assert result.skipped_paths == 1
    assert result.paths_seen == 1
    assert "linked-secret.txt" not in (tmp_path / ".git" / "fs-state.json").read_text()


def test_polling_observer_empty_repo_second_scan_emits_creation(tmp_path: Path) -> None:
    _init_empty_repo(tmp_path)
    state = tmp_path / ".git" / "fs-state.json"

    baseline = poll_project_once(tmp_path, state_path=state)
    assert baseline.baseline_initialized is True
    assert baseline.paths_seen == 0

    (tmp_path / "new.py").write_text("x = 1\n")
    second = poll_project_once(tmp_path, state_path=state)

    assert second.baseline_initialized is False
    assert len(second.observations) == 1
    assert second.observations[0].payload["path"] == "new.py"


def test_polling_observer_oversized_transition_is_not_reported_as_deletion(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    target = tmp_path / "local.txt"
    target.write_text("small\n")
    state = tmp_path / ".git" / "fs-state.json"
    poll_project_once(tmp_path, state_path=state, max_file_bytes=100)

    target.write_text("x" * 101)
    oversized = poll_project_once(tmp_path, state_path=state, max_file_bytes=100)

    assert oversized.observations == []
    assert oversized.skipped_paths == 1

    target.unlink()
    deleted = poll_project_once(tmp_path, state_path=state, max_file_bytes=100)
    assert len(deleted.observations) == 1
    assert deleted.observations[0].payload["path"] == "local.txt"
    assert deleted.observations[0].payload["after_blob_id"] is None


def test_polling_observer_feeds_reconciler_for_bash_style_write(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    state = tmp_path / ".git" / "fs-state.json"
    poll_project_once(tmp_path, state_path=state)

    open_step_window(
        tmp_path,
        trace_id="tr-runtime",
        step_index=2,
        agent_step_id="step_2",
        tool_call_id="tc-bash",
        capture_method=["hook_pretooluse"],
        event_time="2026-04-01T10:00:00Z",
        tool_name="Bash",
        declared_command="python generate.py",
    )
    (tmp_path / "generated.py").write_text("answer = 42\n")
    poll_project_once(tmp_path, state_path=state)
    close_step_window_with_snapshot(
        tmp_path,
        trace_id="tr-runtime",
        step_index=2,
        agent_step_id="step_2",
        tool_call_id="tc-bash",
        capture_method=["hook_posttooluse"],
        event_time="2026-12-31T23:59:59Z",
        tool_name="Bash",
        declared_command="python generate.py",
    )

    summary = reconcile_watcher_observations(tmp_path)
    assert summary["attributed"] == 1
    assert summary["patches_created"] == 1

    patches = [e for e in read_events(tmp_path) if e.event_type == "trace_patch_created"]
    assert len(patches) == 1
    assert patches[0].payload["file_path"] == "generated.py"
    assert patches[0].payload["tool_call_id"] == "tc-bash"
    assert patches[0].capture_method == ["watcher_backstop"]


def test_polling_observer_writes_blob_objects_for_reconciler_diff(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    state = tmp_path / ".git" / "fs-state.json"
    poll_project_once(tmp_path, state_path=state)

    (tmp_path / "app.py").write_text("x = 3\n")
    result = poll_project_once(tmp_path, state_path=state)
    before_hex = result.observations[0].payload["before_blob_id"]["hex"]
    after_hex = result.observations[0].payload["after_blob_id"]["hex"]

    assert _git(tmp_path, "cat-file", "-t", before_hex) == "blob"
    assert _git(tmp_path, "cat-file", "-t", after_hex) == "blob"


def test_polling_observer_batches_multiple_mutations(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    state = tmp_path / ".git" / "fs-state.json"
    poll_project_once(tmp_path, state_path=state)

    (tmp_path / "app.py").write_text("x = 4\n")
    (tmp_path / "new.py").write_text("created = True\n")
    result = poll_project_once(tmp_path, state_path=state)

    assert len(result.observations) == 2
    assert {event.payload["path"] for event in result.observations} == {
        "app.py",
        "new.py",
    }
    assert len({event.batch_id for event in result.observations}) == 1


def test_polling_observer_excludes_explicit_paths(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    state = tmp_path / ".git" / "fs-state.json"
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("")
    poll_project_once(tmp_path, state_path=state, exclude_paths=[transcript])

    transcript.write_text('{"event":"PostToolUse"}\n')
    (tmp_path / "generated.py").write_text("answer = 42\n")
    result = poll_project_once(tmp_path, state_path=state, exclude_paths=[transcript])

    assert [event.payload["path"] for event in result.observations] == ["generated.py"]
