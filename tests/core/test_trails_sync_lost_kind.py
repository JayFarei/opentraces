"""Cluster F — D3: ``lost_kind`` discriminator on ``lost`` survival rows.

The Phase 5 ``lost`` state today flattens two distinct outcomes:

* ``file_deleted`` — the file no longer exists at HEAD;
* ``hunk_removed`` — the file is alive at HEAD but the specific hunk's
  authored lines are gone.

D3 surfaces the discriminator so reviewers can sort lost patches by
recoverability: a ``file_deleted`` lost is less recoverable than a
``hunk_removed`` lost (the file is available to grep / restore lines from).
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


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


def _append_patch_and_anchor(
    repo: Path,
    *,
    patch_id: str,
    authored: str,
    file_path: str,
    affected_range: dict,
    anchor_commit: str,
) -> None:
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
                    "raw_authored_hash": sha256_text(authored),
                    "git_clean_hash": sha256_text(authored.strip()),
                    "limitations": [],
                },
            ),
            TrailEventDraft(
                event_type="git_anchor_created",
                trace_id=f"tr-{patch_id}",
                step_index=1,
                capture_method=["manual_attach"],
                payload={
                    "git_anchor_id": f"gitanchor-sha256:{patch_id}",
                    "trace_patch_id": f"tracepatch-sha256:{patch_id}",
                    "commit_id": {"algo": "sha1", "hex": anchor_commit},
                    "path": file_path,
                    "range": affected_range,
                    "relation": "anchored_in_git",
                    "evidence_tier": "exact_range_hash",
                    "evidence_firmness": "firm",
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )


def test_lost_kind_file_deleted(tmp_path: Path) -> None:
    """Lost via file deletion ⇒ ``lost_kind == 'file_deleted'``."""
    _init_repo(tmp_path)
    (tmp_path / "ghost.py").write_text("def ghost():\n    return 'authored'\n")
    anchor = _commit(tmp_path, "anchor")
    (tmp_path / "ghost.py").unlink()
    _commit(tmp_path, "drop ghost.py")

    _append_patch_and_anchor(
        tmp_path,
        patch_id="ghost",
        authored="    return 'authored'\n",
        file_path="ghost.py",
        affected_range={"start_line": 2, "end_line": 2},
        anchor_commit=anchor,
    )

    payload = sync_patch(tmp_path, "tracepatch-sha256:ghost")
    current = payload["current_survival"]
    assert current["survival_state"] == "lost"
    assert current.get("lost_kind") == "file_deleted"


def test_lost_kind_hunk_removed(tmp_path: Path) -> None:
    """Lost while file is alive at HEAD ⇒ ``lost_kind == 'hunk_removed'``.

    Drives a small repo where the patch's authored lines are placed in an
    intra-file location that gets erased while the file lives on, and the
    range point becomes shorter than the original anchor. With both:
    range_exists=False and preserved=0 the survival path falls into the
    final ``lost`` return — D3 must tag it ``hunk_removed``.
    """
    _init_repo(tmp_path)
    # Anchor on a long file so the original line range is valid.
    body = ["line0", "line1", "line2", "line3", "line4", "line5", "line6"]
    (tmp_path / "live.py").write_text("\n".join(body) + "\n")
    anchor = _commit(tmp_path, "anchor with deep content")
    # Now shrink the file: range start_line=5 still falls inside but lines
    # are gone. range_exists=False because file is shorter.
    (tmp_path / "live.py").write_text("only-one-line\n")
    _commit(tmp_path, "shrink live.py")

    _append_patch_and_anchor(
        tmp_path,
        patch_id="huntrim",
        authored="line5\n",
        file_path="live.py",
        affected_range={"start_line": 5, "end_line": 5},
        anchor_commit=anchor,
    )

    payload = sync_patch(tmp_path, "tracepatch-sha256:huntrim")
    current = payload["current_survival"]
    assert current["survival_state"] == "lost"
    # File alive ⇒ kind is hunk_removed (the authored line is no longer
    # present anywhere in the surviving file content)
    assert current.get("lost_kind") == "hunk_removed"
    # No killer commit attribution for the cheap path.
    assert current.get("lost_at_commit_sha") is None


def test_alive_states_have_no_lost_kind(tmp_path: Path) -> None:
    """Survival states other than ``lost`` MUST NOT carry ``lost_kind``.

    A reviewer querying ``lost_kind`` should be able to assume the state
    is ``lost`` without an extra check.
    """
    _init_repo(tmp_path)
    (tmp_path / "live.py").write_text("def f():\n    return 'authored-line'\n")
    anchor = _commit(tmp_path, "anchor")

    _append_patch_and_anchor(
        tmp_path,
        patch_id="alive",
        authored="    return 'authored-line'\n",
        file_path="live.py",
        affected_range={"start_line": 2, "end_line": 2},
        anchor_commit=anchor,
    )

    payload = sync_patch(tmp_path, "tracepatch-sha256:alive")
    current = payload["current_survival"]
    assert current["survival_state"] == "alive_on_path"
    assert "lost_kind" not in current
    assert "lost_at_commit_sha" not in current
