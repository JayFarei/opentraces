"""Phase 5 survival states for trail synchronization.

Phase 4 shipped five survival states (alive_on_path, alive_transformed,
reverted, lost, unknown). Phase 5 adds the four states reserved in the
schema:

* ``alive_moved`` — anchor's range survives at a renamed path;
* ``partially_preserved`` — a subset of the anchored range survives;
* ``repaired`` — a human committer edited the agent's range after the
  anchor commit;
* ``orphaned`` — reserved for reference-transaction observation
  (deferred beyond Phase 5).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from opentraces.core.trails import (
    GitObjectID,
    TrailEventDraft,
    append_event_batch,
    sync_patch,
)


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
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


def _emit_anchored_patch(
    repo: Path,
    *,
    trace_patch_id: str,
    file_path: str,
    authored_text: str,
    affected_range: dict[str, int],
    commit_sha: str,
    git_anchor_id: str,
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
                    "snapshot_before_id": "snapshot-pre",
                    "snapshot_after_id": "snapshot-post",
                    "file_path": file_path,
                    "affected_range": affected_range,
                    "authored_text": authored_text,
                    "raw_authored_hash": "sha256:fixture",
                    "git_clean_hash": "sha256:fixture",
                    "before_blob_id": before_blob.model_dump(mode="json"),
                    "after_blob_id": after_blob.model_dump(mode="json"),
                    "limitations": [],
                },
            ),
            TrailEventDraft(
                event_type="git_anchor_created",
                trace_id="tr1",
                generation_index=0,
                step_index=1,
                capture_method=["post_commit_correlator"],
                payload={
                    "git_anchor_id": git_anchor_id,
                    "trace_patch_id": trace_patch_id,
                    "commit_id": {"algo": "sha1", "hex": commit_sha},
                    "path": file_path,
                    "range": affected_range,
                    "blob_id": after_blob.model_dump(mode="json"),
                    "patch_id": None,
                    "observed_ref": "HEAD",
                    "relation": "anchored_in_git",
                    "evidence_tier": "exact_range_hash",
                    "evidence_firmness": "firm",
                    "source": "post-commit-correlator",
                    "limitations": [],
                },
            ),
        ],
        writer="capture-claude-code",
    )


def _commit(repo: Path, message: str, *, env_extra: dict[str, str] | None = None) -> str:
    env = None
    if env_extra:
        import os

        env = os.environ.copy()
        env.update(env_extra)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True, env=env)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def test_rename_plus_edit_reports_alive_moved(tmp_path: Path) -> None:
    """Plan §Phase 5 edge fixture #8.

    Agent anchored a patch in ``auth.py``. A later commit renames the
    file to ``authentication.py`` AND edits a line. ``sync_patch``
    must report survival_state=``alive_moved``, the current path, and
    the rename hop count so consumers can distinguish "moved" from
    "moved-and-touched".
    """
    _init_repo(tmp_path)
    authored = "def authorize():\n    return True\n"
    (tmp_path / "auth.py").write_text(authored)
    anchor_commit = _commit(tmp_path, "anchor authorize")

    _emit_anchored_patch(
        tmp_path,
        trace_patch_id="tracepatch-sha256:rename-fixture",
        file_path="auth.py",
        authored_text="    return True\n",
        affected_range={"start_line": 2, "end_line": 2},
        commit_sha=anchor_commit,
        git_anchor_id="gitanchor-sha256:rename-fixture",
    )

    # Rename + edit in one commit.
    subprocess.run(["git", "mv", "auth.py", "authentication.py"], cwd=tmp_path, check=True)
    (tmp_path / "authentication.py").write_text("def authorize():\n    return True\n# touched\n")
    _commit(tmp_path, "rename + edit")

    result = sync_patch(tmp_path, "tracepatch-sha256:rename-fixture")
    survival_states = {obs["survival_state"] for obs in result["observations"]}
    assert "alive_moved" in survival_states
    moved_obs = next(
        obs for obs in result["observations"] if obs["survival_state"] == "alive_moved"
    )
    assert moved_obs["current_path"] == "authentication.py"
    assert moved_obs.get("rename_hops", 0) >= 1


def test_partial_preservation_reports_partially_preserved(tmp_path: Path) -> None:
    """Plan §Phase 5 edge fixture #10.

    Agent anchored a multi-line range. A later commit removes part of
    that range while keeping another part. ``sync_patch`` must report
    ``partially_preserved`` so consumers can flag the partial drift.
    """
    _init_repo(tmp_path)
    authored = "def authorize():\n    line_one()\n    line_two()\n    line_three()\n"
    (tmp_path / "auth.py").write_text(authored)
    anchor_commit = _commit(tmp_path, "anchor multiline")

    _emit_anchored_patch(
        tmp_path,
        trace_patch_id="tracepatch-sha256:partial-fixture",
        file_path="auth.py",
        authored_text="    line_one()\n    line_two()\n    line_three()\n",
        affected_range={"start_line": 2, "end_line": 4},
        commit_sha=anchor_commit,
        git_anchor_id="gitanchor-sha256:partial-fixture",
    )

    # Later commit removes line_two and line_three; line_one survives.
    (tmp_path / "auth.py").write_text("def authorize():\n    line_one()\n")
    _commit(tmp_path, "trim authorize")

    result = sync_patch(tmp_path, "tracepatch-sha256:partial-fixture")
    survival_states = {obs["survival_state"] for obs in result["observations"]}
    assert "partially_preserved" in survival_states
    partial_obs = next(
        obs for obs in result["observations"] if obs["survival_state"] == "partially_preserved"
    )
    assert partial_obs["preserved_line_count"] >= 1
    assert partial_obs["preserved_line_count"] < 3


def test_partial_preservation_when_original_range_no_longer_exists(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    authored = "def authorize():\n    line_one()\n    line_two()\n    line_three()\n"
    (tmp_path / "auth.py").write_text(authored)
    anchor_commit = _commit(tmp_path, "anchor multiline")

    _emit_anchored_patch(
        tmp_path,
        trace_patch_id="tracepatch-sha256:partial-short-file-fixture",
        file_path="auth.py",
        authored_text="    line_two()\n    line_three()\n",
        affected_range={"start_line": 3, "end_line": 4},
        commit_sha=anchor_commit,
        git_anchor_id="gitanchor-sha256:partial-short-file-fixture",
    )

    (tmp_path / "auth.py").write_text("def authorize():\n    line_three()\n")
    _commit(tmp_path, "shorten authorize")

    result = sync_patch(tmp_path, "tracepatch-sha256:partial-short-file-fixture")
    current = result["current_survival"]
    assert current["survival_state"] == "partially_preserved"
    assert current["preserved_line_count"] == 1
    assert current["authored_line_count"] == 2


def test_human_repair_reports_repaired(tmp_path: Path) -> None:
    """Plan §Phase 5 edge fixture #9.

    Agent anchored a patch (committed by ``agent@opentraces``). A
    different human committer edits the same range later. Survival is
    reported as ``repaired`` so consumers can credit the human edit
    rather than treating it as agent-side transformation.
    """
    _init_repo(tmp_path)
    authored = "def authorize():\n    return True\n"
    (tmp_path / "auth.py").write_text(authored)
    anchor_commit = _commit(tmp_path, "agent authored")

    _emit_anchored_patch(
        tmp_path,
        trace_patch_id="tracepatch-sha256:repair-fixture",
        file_path="auth.py",
        authored_text="    return True\n",
        affected_range={"start_line": 2, "end_line": 2},
        commit_sha=anchor_commit,
        git_anchor_id="gitanchor-sha256:repair-fixture",
    )

    # Human committer edits the agent's range.
    (tmp_path / "auth.py").write_text("def authorize():\n    return user.is_admin\n")
    _commit(
        tmp_path,
        "human override",
        env_extra={
            "GIT_AUTHOR_NAME": "Alice Human",
            "GIT_AUTHOR_EMAIL": "alice@humans.example",
            "GIT_COMMITTER_NAME": "Alice Human",
            "GIT_COMMITTER_EMAIL": "alice@humans.example",
        },
    )

    result = sync_patch(tmp_path, "tracepatch-sha256:repair-fixture")
    survival_states = {obs["survival_state"] for obs in result["observations"]}
    assert "repaired" in survival_states
    repaired_obs = next(
        obs for obs in result["observations"] if obs["survival_state"] == "repaired"
    )
    assert repaired_obs["repair_committer_email"] == "alice@humans.example"
