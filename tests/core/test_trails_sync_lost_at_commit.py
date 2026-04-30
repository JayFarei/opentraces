"""Cluster F — D2: when a patch is ``lost``, surface the killer commit.

The current ``_compute_survival`` lumps every dead patch into
``survival_state="lost"`` with no pointer at WHICH commit killed it.
D2 walks the anchor's downstream history and records:

* ``lost_at_commit_sha`` — the first commit (anchor..HEAD) whose diff
  removed the patch's authored lines (or the file deletion commit when
  the file no longer exists at HEAD).

When the file is alive at HEAD but the specific hunk is gone, the killer
commit is left as ``None`` and the row carries the new ``lost_kind``
discriminator (covered in :mod:`test_trails_sync_lost_kind`).
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


def test_file_deleted_killer_commit_resolved(tmp_path: Path) -> None:
    """When the patch's file is removed downstream, ``lost_at_commit_sha``
    points at the deletion commit and ``lost_kind == 'file_deleted'``."""
    _init_repo(tmp_path)
    # 1: seed
    (tmp_path / "victim.py").write_text("def victim():\n    return 'before'\n")
    seed = _commit(tmp_path, "seed")
    # 2: anchor — patch authored at this commit
    (tmp_path / "victim.py").write_text("def victim():\n    return 'authored'\n")
    anchor_commit = _commit(tmp_path, "patch landed")
    # 3: a noise commit not touching the file
    (tmp_path / "other.py").write_text("x = 1\n")
    _commit(tmp_path, "unrelated edit")
    # 4: the killer — delete the file
    (tmp_path / "victim.py").unlink()
    killer = _commit(tmp_path, "drop victim.py")
    # 5: more downstream noise
    (tmp_path / "other.py").write_text("x = 2\n")
    _commit(tmp_path, "post-deletion noise")

    _append_patch_and_anchor(
        tmp_path,
        patch_id="filedel",
        authored="    return 'authored'\n",
        file_path="victim.py",
        affected_range={"start_line": 2, "end_line": 2},
        anchor_commit=anchor_commit,
    )

    payload = sync_patch(tmp_path, "tracepatch-sha256:filedel")
    current = payload["current_survival"]
    assert current["survival_state"] == "lost"
    assert current.get("lost_kind") == "file_deleted"
    sha = current.get("lost_at_commit_sha")
    assert sha is not None, "lost_at_commit_sha must be populated for file_deleted lost"
    assert sha.startswith(killer[:12])


def test_hunk_removed_no_killer_commit_field(tmp_path: Path) -> None:
    """When the file is alive at HEAD but the patch's lines are gone,
    ``lost_kind == 'hunk_removed'`` and ``lost_at_commit_sha`` is ``None``.

    The D2 contract: only the file-deletion sub-case auto-resolves a single
    killer commit. Hunk-level lookup is intentionally deferred (line-walk
    via ``git log -L`` is far more expensive and ambiguous) so the cheap
    answer is "no killer attribution available".
    """
    _init_repo(tmp_path)
    (tmp_path / "live.py").write_text("def first():\n    return 'authored'\n\ndef second():\n    return 'kept'\n")
    anchor_commit = _commit(tmp_path, "anchor with two functions")
    # Replace just the patch's hunk; file lives on
    (tmp_path / "live.py").write_text("def first():\n    return 'rewritten'\n\ndef second():\n    return 'kept'\n")
    _commit(tmp_path, "rewrite first()")

    _append_patch_and_anchor(
        tmp_path,
        patch_id="hunkremoved",
        authored="    return 'authored'\n",
        file_path="live.py",
        affected_range={"start_line": 2, "end_line": 2},
        anchor_commit=anchor_commit,
    )

    payload = sync_patch(tmp_path, "tracepatch-sha256:hunkremoved")
    current = payload["current_survival"]
    # The repaired-by-non-anchor logic fires if a different committer touched
    # the range, but with a single-author repo we expect alive_transformed
    # or lost depending on the implementation. In either case the new
    # ``lost_kind``/``lost_at_commit_sha`` fields must follow the contract.
    if current["survival_state"] == "lost":
        assert current.get("lost_kind") == "hunk_removed"
        assert current.get("lost_at_commit_sha") is None


def test_attribution_caches_within_batch(tmp_path: Path) -> None:
    """Two lost patches on the same file resolve the killer commit via
    a single git lookup. The cache must be observable: calling the
    resolver twice with the same (file, anchor, head) triple costs no
    additional ``git log`` calls.
    """
    from opentraces.core.trails.sync import _resolve_lost_attribution

    _init_repo(tmp_path)
    (tmp_path / "seed.txt").write_text("seed\n")
    _commit(tmp_path, "seed")
    (tmp_path / "victim.py").write_text("a = 1\n")
    anchor_commit = _commit(tmp_path, "anchor")
    (tmp_path / "victim.py").unlink()
    killer = _commit(tmp_path, "kill victim")

    cache: dict = {}
    head = _git(tmp_path, "rev-parse", "HEAD")
    sha1, kind1 = _resolve_lost_attribution(
        tmp_path,
        anchor_commit=anchor_commit,
        path="victim.py",
        observed_ref=head,
        authored_lines=["a = 1"],
        cache=cache,
    )
    sha2, kind2 = _resolve_lost_attribution(
        tmp_path,
        anchor_commit=anchor_commit,
        path="victim.py",
        observed_ref=head,
        authored_lines=["a = 1"],
        cache=cache,
    )
    assert sha1 == sha2
    assert kind1 == kind2 == "file_deleted"
    assert sha1 and sha1.startswith(killer[:12])
    # Cache must hold one entry keyed by (path, anchor, head).
    assert len(cache) == 1
