"""Cluster C — split the overloaded ``unknown`` survival state.

Today ``_compute_survival`` and the no-anchor branch of ``_sync`` lump
multiple distinct conditions under ``survival_state="unknown"``. This file
covers the four post-split states:

* ``orphan_branch`` — anchor commit not reachable from observed_ref;
* ``missing_authored_text`` — patch has no original line content;
* ``never_committed`` — patch was authored, no Git Anchor ever matured,
  and the file currently exists in HEAD;
* ``unknown`` — kept as a fallback for truly indeterminate cases (anchor
  commit / path / observed_commit_id missing).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    sync_patch,
)
from opentraces.core.trails.models import sha256_text


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


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


def _append_patch(
    repo: Path,
    *,
    patch_id: str,
    authored: str,
    file_path: str = "app.py",
    affected_range: dict | None = None,
) -> None:
    affected_range = affected_range or {"start_line": 2, "end_line": 2}
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=f"tr-{patch_id}",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": f"tracepatch-sha256:{patch_id}",
                    "file_path": file_path,
                    "affected_range": affected_range,
                    "authored_text": authored,
                    "raw_authored_hash": sha256_text(authored or "<empty>"),
                    "git_clean_hash": sha256_text((authored or "<empty>").strip()),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )


def test_split_unknown_orphan_branch(tmp_path: Path) -> None:
    """Anchor commit not in HEAD's ancestry is now ``orphan_branch``,
    not ``unknown``."""
    _init_repo(tmp_path)
    authored = "    return 'orphan-fixture'\n"
    _append_patch(tmp_path, patch_id="orphan", authored=authored)

    # Synthesize a Git Anchor whose commit_id is unreachable from HEAD.
    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="git_anchor_created",
                trace_id="tr-orphan",
                step_index=1,
                capture_method=["manual_attach"],
                payload={
                    "git_anchor_id": "gitanchor-sha256:orphan",
                    "trace_patch_id": "tracepatch-sha256:orphan",
                    "commit_id": {"algo": "sha1", "hex": "f" * 40},
                    "path": "app.py",
                    "range": {"start_line": 2, "end_line": 2},
                    "relation": "anchored_in_git",
                    "evidence_tier": "exact_range_hash",
                    "evidence_firmness": "firm",
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    payload = sync_patch(tmp_path, "tracepatch-sha256:orphan")
    current = payload["current_survival"]
    assert current["survival_state"] == "orphan_branch"
    assert "anchor_commit_not_reachable_from_head" in current["limitations"]
    # Retention is undefined for orphan_branch.
    assert current.get("retention_fraction") is None


def test_split_unknown_missing_authored_text(tmp_path: Path) -> None:
    """A reachable anchor whose patch lacks ``authored_text`` is now
    ``missing_authored_text``, not ``unknown``."""
    _init_repo(tmp_path)
    # Anchor on a real reachable commit, but the patch event has empty
    # authored_text so the survival path bails out at the authored-lines check.
    (tmp_path / "app.py").write_text("def value():\n    return 'no-authored-text'\n")
    anchor_commit = _commit(tmp_path, "anchor for missing-authored-text")

    _append_patch(
        tmp_path,
        patch_id="noauthored",
        authored="",
    )
    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="git_anchor_created",
                trace_id="tr-noauthored",
                step_index=1,
                capture_method=["manual_attach"],
                payload={
                    "git_anchor_id": "gitanchor-sha256:noauthored",
                    "trace_patch_id": "tracepatch-sha256:noauthored",
                    "commit_id": {"algo": "sha1", "hex": anchor_commit},
                    "path": "app.py",
                    "range": {"start_line": 2, "end_line": 2},
                    "relation": "anchored_in_git",
                    "evidence_tier": "exact_range_hash",
                    "evidence_firmness": "firm",
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    payload = sync_patch(tmp_path, "tracepatch-sha256:noauthored")
    current = payload["current_survival"]
    assert current["survival_state"] == "missing_authored_text"
    assert "missing_authored_text" in current["limitations"]
    assert current.get("retention_fraction") is None


def test_split_unknown_never_committed_when_anchor_never_matured(
    tmp_path: Path,
) -> None:
    """Patch authored, no Git Anchor ever fired, but the file still exists
    in HEAD => ``never_committed`` (the patch was superseded intra-trace
    before any commit captured it)."""
    _init_repo(tmp_path)
    # Patch claims edits to app.py, which DOES exist in HEAD (from the seed).
    _append_patch(
        tmp_path,
        patch_id="never-mat",
        authored="    return 'authored-but-never-anchored'\n",
        file_path="app.py",
    )

    payload = sync_patch(tmp_path, "tracepatch-sha256:never-mat")
    current = payload["current_survival"]
    assert current["survival_state"] == "never_committed"
    # Trail-level limitation should still note no anchor event.
    assert "no_git_anchor_event" in payload["trail_limitations"]
    assert current.get("retention_fraction") is None


def test_unknown_kept_as_fallback_when_file_path_missing(tmp_path: Path) -> None:
    """``unknown`` is kept for the truly indeterminate case: patch has no
    file_path AND no anchor — we genuinely cannot decide."""
    _init_repo(tmp_path)
    # Patch with no file_path and no anchor => survival is unknowable.
    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-truly-unknown",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": "tracepatch-sha256:truly-unknown",
                    "file_path": None,
                    "affected_range": {"start_line": 1, "end_line": 1},
                    "authored_text": "x\n",
                    "raw_authored_hash": sha256_text("x\n"),
                    "git_clean_hash": sha256_text("x"),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    payload = sync_patch(tmp_path, "tracepatch-sha256:truly-unknown")
    current = payload["current_survival"]
    # When neither file_path nor anchor are set, we fall back to unknown.
    assert current["survival_state"] == "unknown"


def test_never_committed_only_when_file_currently_exists(tmp_path: Path) -> None:
    """If the file the patch claims to edit is gone from HEAD AND no anchor
    matured, never_committed is *not* the right call — the file is just
    gone. Use ``unknown`` for that lossy case."""
    _init_repo(tmp_path)
    # Remove the seeded file so the patch's file_path is not present in HEAD.
    subprocess.run(["git", "rm", "-q", "app.py"], cwd=tmp_path, check=True)
    _commit(tmp_path, "delete app.py before patch ever materialized")

    _append_patch(
        tmp_path,
        patch_id="never-but-gone",
        authored="    return 'authored-but-file-missing'\n",
        file_path="app.py",
    )

    payload = sync_patch(tmp_path, "tracepatch-sha256:never-but-gone")
    current = payload["current_survival"]
    # File is missing AND no anchor — fall back to unknown rather than
    # claim never_committed (which would imply the file still exists).
    assert current["survival_state"] == "unknown"
