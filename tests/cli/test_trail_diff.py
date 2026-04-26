"""Trace Trails Phase 2 snapshot diff CLI coverage."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.trails import append_step_snapshot, read_events, write_worktree_tree


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "app.py").write_text("def value():\n    return 'old'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def test_trail_diff_between_two_step_snapshots(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)

    step1 = append_step_snapshot(
        repo,
        trace_id="tr-diff",
        step_index=1,
        agent_step_id="s1",
        tool_call_id="tool-read",
        capture_method=["hook_posttooluse"],
        capture_status="hook_only",
        limitations=["hook_only"],
    )
    (repo / "app.py").write_text(
        "def value():\n    return 'phase-two-snapshot-diff-line'\n",
    )
    step2 = append_step_snapshot(
        repo,
        trace_id="tr-diff",
        step_index=2,
        agent_step_id="s2",
        tool_call_id="tool-edit",
        capture_method=["hook_posttooluse"],
        capture_status="hook_only",
        limitations=["hook_only"],
    )

    result = CliRunner().invoke(
        main,
        [
            "trail",
            "diff",
            "--trace",
            "tr-diff",
            "--from-step",
            "1",
            "--to-step",
            "2",
            "--json",
            "--project",
            str(repo),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["trace_id"] == "tr-diff"
    assert payload["from_step"] == 1
    assert payload["to_step"] == 2
    assert payload["from_snapshot_id"] == step1.snapshot_id
    assert payload["to_snapshot_id"] == step2.snapshot_id
    assert payload["from_tree_id"]["algo"] == "sha1"
    assert payload["to_tree_id"]["hex"] == step2.tree_id["hex"]
    assert payload["to_tree_id"] == write_worktree_tree(repo)
    assert payload["trace_patch"]["files"][0]["path"] == "app.py"
    assert payload["trace_patch"]["files"][0]["added_range"] == {
        "start_line": 2,
        "end_line": 2,
    }
    assert "phase-two-snapshot-diff-line" in payload["trace_patch"]["patch"]

    events = read_events(repo)
    event_types = [event.event_type for event in events]
    assert event_types == [
        "trace_step_window_opened",
        "trace_snapshot_created",
        "trace_step_window_closed",
        "trace_step_window_opened",
        "trace_snapshot_created",
        "trace_step_window_closed",
    ]
    assert all(event.capture_method == ["hook_posttooluse"] for event in events)


def test_snapshot_subtree_survives_after_advisory_refs_deleted_and_gc_runs(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    append_step_snapshot(
        repo,
        trace_id="tr-gc-snapshot",
        step_index=1,
        agent_step_id="s1",
        tool_call_id="tool-read",
        capture_method=["hook_posttooluse"],
    )
    (repo / "generated.txt").write_text("uncommitted generated snapshot content\n")
    snap = append_step_snapshot(
        repo,
        trace_id="tr-gc-snapshot",
        step_index=2,
        agent_step_id="s2",
        tool_call_id="tool-write",
        capture_method=["hook_posttooluse"],
    )

    refs = subprocess.check_output(
        ["git", "for-each-ref", "--format=%(refname)", "refs/opentraces/local/traces"],
        cwd=repo,
        text=True,
    ).splitlines()
    for ref in refs:
        subprocess.run(["git", "update-ref", "-d", ref], cwd=repo, check=True)
    subprocess.run(["git", "reflog", "expire", "--expire=now", "--all"], cwd=repo, check=True)
    subprocess.run(["git", "gc", "--prune=now", "--aggressive"], cwd=repo, check=True)

    subprocess.run(["git", "cat-file", "-e", snap.tree_id["hex"]], cwd=repo, check=True)
    diff = CliRunner().invoke(
        main,
        [
            "trail",
            "diff",
            "--trace",
            "tr-gc-snapshot",
            "--from-step",
            "1",
            "--to-step",
            "2",
            "--json",
            "--project",
            str(repo),
        ],
    )
    assert diff.exit_code == 0, diff.output
    assert "generated.txt" in json.loads(diff.output)["trace_patch"]["patch"]


def test_snapshot_refs_are_create_only(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    first = append_step_snapshot(
        repo,
        trace_id="tr-create-only",
        step_index=1,
        agent_step_id="s1",
        tool_call_id="tool-read",
        capture_method=["hook_posttooluse"],
    )
    first_ref_value = _git(repo, "rev-parse", first.ref)

    (repo / "app.py").write_text("def value():\n    return 'new after ref exists'\n")
    second = append_step_snapshot(
        repo,
        trace_id="tr-create-only",
        step_index=1,
        agent_step_id="s1-rerun",
        tool_call_id="tool-rerun",
        capture_method=["hook_posttooluse"],
    )

    assert second.tree_id != first.tree_id
    assert _git(repo, "rev-parse", first.ref) == first_ref_value


def test_hook_payload_state_mismatch_records_limitation(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    claimed = write_worktree_tree(repo)
    (repo / "app.py").write_text("def value():\n    return 'verified tree differs'\n")
    append_step_snapshot(
        repo,
        trace_id="tr-mismatch",
        step_index=1,
        agent_step_id="s1",
        tool_call_id="tool-edit",
        capture_method=["hook_posttooluse"],
        claimed_tree_id=claimed,
    )

    snapshot = next(
        event
        for event in read_events(repo)
        if event.event_type == "trace_snapshot_created"
    )
    assert "hook_payload_state_mismatch" in snapshot.payload["limitations"]
