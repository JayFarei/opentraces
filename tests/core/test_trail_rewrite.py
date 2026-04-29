"""Phase 5 Git rewrite scenarios — amend, rebase, squash, cherry-pick."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from opentraces.core.trails import (
    GitObjectID,
    TrailEventDraft,
    append_event_batch,
    sync_patch,
    read_events,
    reconcile_commit_anchors,
    supersede_anchors_for_rewrite,
)


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "agent@opentraces"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "agent"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _hash_object(repo: Path, content: str) -> str:
    proc = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=content,
        text=True,
        capture_output=True,
        check=True,
    )
    return proc.stdout.strip()


def _emit_hook_patch(
    repo: Path,
    *,
    trace_patch_id: str,
    file_path: str,
    authored_text: str,
    affected_range: dict[str, int],
) -> None:
    before_blob = GitObjectID(hex=_hash_object(repo, "before\n"))
    after_blob = GitObjectID(hex=_hash_object(repo, authored_text))
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr1",
                generation_index=0,
                step_index=1,
                capture_method=["hook_pretooluse", "hook_posttooluse"],
                payload={
                    "trace_patch_id": trace_patch_id,
                    "snapshot_before_id": "snap-pre",
                    "snapshot_after_id": "snap-post",
                    "file_path": file_path,
                    "affected_range": affected_range,
                    "authored_text": authored_text,
                    "raw_authored_hash": "sha256:fixture",
                    "git_clean_hash": "sha256:fixture",
                    "before_blob_id": before_blob.model_dump(mode="json"),
                    "after_blob_id": after_blob.model_dump(mode="json"),
                    "limitations": [],
                },
            )
        ],
        writer="capture-claude-code",
    )


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def test_amend_supersedes_old_anchor_and_creates_new(tmp_path: Path) -> None:
    """git commit --amend produces a new commit replacing the old one.

    The substrate must:

    1. Mark the old anchor superseded so trail sync can route around
       the now-unreachable old commit.
    2. Create a new anchor at the amended commit so survival can be
       computed against current HEAD.
    """
    _init_repo(tmp_path)
    (tmp_path / "auth.py").write_text("def authorize():\n    return False\n")
    _commit(tmp_path, "seed auth")

    authored = "    return True\n"
    (tmp_path / "auth.py").write_text("def authorize():\n    return True\n")
    old_commit = _commit(tmp_path, "make True")

    _emit_hook_patch(
        tmp_path,
        trace_patch_id="tracepatch-sha256:amend",
        file_path="auth.py",
        authored_text=authored,
        affected_range={"start_line": 2, "end_line": 2},
    )
    reconcile_commit_anchors(tmp_path, old_commit)

    # Amend the commit message; same patch content, new commit SHA.
    subprocess.run(
        ["git", "commit", "--amend", "-q", "-m", "make True (reworded)"],
        cwd=tmp_path,
        check=True,
    )
    new_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    assert new_commit != old_commit

    summary = supersede_anchors_for_rewrite(
        tmp_path, [(old_commit, new_commit)]
    )
    assert summary["anchors_superseded"] == 1
    assert summary["new_anchors_created"] == 1

    events = read_events(tmp_path)
    superseded_events = [
        e for e in events if e.event_type == "git_anchor_superseded"
    ]
    assert len(superseded_events) == 1
    supersede_payload = superseded_events[0].payload
    assert supersede_payload["old_commit_id"]["hex"] == old_commit
    assert supersede_payload["new_commit_id"]["hex"] == new_commit
    assert superseded_events[0].capture_method == ["post_rewrite_hook"]

    anchor_events = [e for e in events if e.event_type == "git_anchor_created"]
    assert len(anchor_events) == 2
    anchor_commits = {(e.payload.get("commit_id") or {}).get("hex") for e in anchor_events}
    assert anchor_commits == {old_commit, new_commit}

    # sync_patch routes through the new anchor (latest event_sequence).
    result = sync_patch(tmp_path, "tracepatch-sha256:amend")
    survival_states = {
        obs["survival_state"] for obs in result["current_observations"]
    }
    assert "alive_on_path" in survival_states


def test_supersede_is_idempotent(tmp_path: Path) -> None:
    """Re-running supersede_anchors_for_rewrite with the same pairs
    appends no new supersede events and reports anchors_already_
    superseded for each pair already on file.
    """
    _init_repo(tmp_path)
    (tmp_path / "auth.py").write_text("def authorize():\n    return True\n")
    old_commit = _commit(tmp_path, "first")
    _emit_hook_patch(
        tmp_path,
        trace_patch_id="tracepatch-sha256:idem",
        file_path="auth.py",
        authored_text="    return True\n",
        affected_range={"start_line": 2, "end_line": 2},
    )
    reconcile_commit_anchors(tmp_path, old_commit)
    subprocess.run(
        ["git", "commit", "--amend", "-q", "-m", "first (reworded)"],
        cwd=tmp_path,
        check=True,
    )
    new_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()

    first = supersede_anchors_for_rewrite(tmp_path, [(old_commit, new_commit)])
    second = supersede_anchors_for_rewrite(tmp_path, [(old_commit, new_commit)])
    assert first["anchors_superseded"] == 1
    assert second["anchors_superseded"] == 0
    assert second["anchors_already_superseded"] == 1
    # No duplicate supersede events.
    events = read_events(tmp_path)
    superseded_events = [
        e for e in events if e.event_type == "git_anchor_superseded"
    ]
    assert len(superseded_events) == 1


def test_rebase_supersedes_each_rewritten_commit(tmp_path: Path) -> None:
    """Rebase applies a sequence of (old, new) rewrite pairs.

    Each old anchor is marked superseded; new anchors are created at
    each rewritten commit. The patch follows the rewrite history.
    """
    _init_repo(tmp_path)
    (tmp_path / "auth.py").write_text("def authorize():\n    return False\n")
    _commit(tmp_path, "base")
    (tmp_path / "auth.py").write_text("def authorize():\n    return True\n")
    old_commit = _commit(tmp_path, "agent change")
    _emit_hook_patch(
        tmp_path,
        trace_patch_id="tracepatch-sha256:rebase",
        file_path="auth.py",
        authored_text="    return True\n",
        affected_range={"start_line": 2, "end_line": 2},
    )
    reconcile_commit_anchors(tmp_path, old_commit)

    # Simulate rebase by amending — same end result for the anchor flow.
    subprocess.run(
        ["git", "commit", "--amend", "-q", "-m", "agent change (reworded)"],
        cwd=tmp_path,
        check=True,
    )
    new_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()

    summary = supersede_anchors_for_rewrite(
        tmp_path, [(old_commit, new_commit)]
    )
    assert summary["anchors_superseded"] == 1
    assert summary["new_anchors_created"] == 1


def test_squash_collapses_multiple_anchors_to_one_new_anchor(
    tmp_path: Path,
) -> None:
    """Squash maps N old commits to one new commit. supersede must
    emit one supersede event per old anchor and produce one new anchor
    at the squashed commit."""
    _init_repo(tmp_path)
    (tmp_path / "auth.py").write_text("def authorize():\n    return False\n")
    _commit(tmp_path, "seed")
    (tmp_path / "auth.py").write_text(
        "def authorize():\n    return False\n    log_call()\n"
    )
    commit_a = _commit(tmp_path, "add log_call")
    (tmp_path / "auth.py").write_text(
        "def authorize():\n    return True\n    log_call()\n"
    )
    commit_b = _commit(tmp_path, "flip authorize")

    _emit_hook_patch(
        tmp_path,
        trace_patch_id="tracepatch-sha256:squash-a",
        file_path="auth.py",
        authored_text="    log_call()\n",
        affected_range={"start_line": 3, "end_line": 3},
    )
    _emit_hook_patch(
        tmp_path,
        trace_patch_id="tracepatch-sha256:squash-b",
        file_path="auth.py",
        authored_text="    return True\n",
        affected_range={"start_line": 2, "end_line": 2},
    )
    reconcile_commit_anchors(tmp_path, commit_a)
    reconcile_commit_anchors(tmp_path, commit_b)

    # Squash A and B into one commit by resetting and recommitting.
    subprocess.run(
        ["git", "reset", "--soft", "HEAD~2"], cwd=tmp_path, check=True
    )
    squashed_commit = _commit(tmp_path, "squashed: log_call + authorize true")

    summary = supersede_anchors_for_rewrite(
        tmp_path,
        [(commit_a, squashed_commit), (commit_b, squashed_commit)],
    )
    assert summary["anchors_superseded"] == 2
    # Both patches should re-anchor at the squashed commit.
    assert summary["new_anchors_created"] >= 1

    events = read_events(tmp_path)
    new_anchor_commits = [
        (e.payload.get("commit_id") or {}).get("hex")
        for e in events
        if e.event_type == "git_anchor_created"
        and e.payload.get("source") in {"post-commit-correlator", "post-rewrite-hook"}
    ]
    assert squashed_commit in new_anchor_commits


def test_cherry_pick_creates_a_second_anchor(tmp_path: Path) -> None:
    """Cherry-pick replays a commit on a different branch. Both the
    original and the cherry-picked commits contain the same patch, so
    reconcile_commit_anchors creates a second anchor without
    superseding the first.
    """
    _init_repo(tmp_path)
    (tmp_path / "auth.py").write_text("def authorize():\n    return False\n")
    _commit(tmp_path, "seed")
    subprocess.run(
        ["git", "checkout", "-q", "-b", "feature"], cwd=tmp_path, check=True
    )
    (tmp_path / "auth.py").write_text("def authorize():\n    return True\n")
    feature_commit = _commit(tmp_path, "feature: authorize true")

    _emit_hook_patch(
        tmp_path,
        trace_patch_id="tracepatch-sha256:cherry",
        file_path="auth.py",
        authored_text="    return True\n",
        affected_range={"start_line": 2, "end_line": 2},
    )
    reconcile_commit_anchors(tmp_path, feature_commit)

    subprocess.run(["git", "checkout", "-q", "main"], cwd=tmp_path, check=True)
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = "2026-04-26T13:00:00Z"
    env["GIT_COMMITTER_DATE"] = "2026-04-26T13:00:00Z"
    subprocess.run(
        ["git", "cherry-pick", "-x", feature_commit],
        cwd=tmp_path,
        check=True,
        env=env,
    )
    cherry_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    assert cherry_commit != feature_commit

    new_anchors = reconcile_commit_anchors(tmp_path, cherry_commit)
    assert len(new_anchors) == 1
    assert new_anchors[0]["commit_id"]["hex"] == cherry_commit

    events = read_events(tmp_path)
    anchor_commits = [
        (e.payload.get("commit_id") or {}).get("hex")
        for e in events
        if e.event_type == "git_anchor_created"
    ]
    assert feature_commit in anchor_commits
    assert cherry_commit in anchor_commits
    # Cherry-pick is NOT a rewrite; no supersede events.
    assert not any(
        e.event_type == "git_anchor_superseded" for e in events
    )
