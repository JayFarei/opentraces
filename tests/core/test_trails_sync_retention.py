"""Cluster C — ``retention_fraction`` for surviving patches.

Every observation row whose state is ``alive_on_path``,
``alive_transformed``, ``alive_moved``, or ``partially_preserved`` should
carry a ``retention_fraction`` ∈ [0, 1] describing how many authored
lines survive at the observed_ref. For ``lost``, ``unknown``,
``orphan_branch``, ``missing_authored_text`` it is ``None``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    reconcile_commit_anchors,
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


def _append_patch(repo: Path, *, patch_id: str, authored: str, affected_range=None) -> None:
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
                    "file_path": "app.py",
                    "affected_range": affected_range,
                    "authored_text": authored,
                    "raw_authored_hash": sha256_text(authored),
                    "git_clean_hash": sha256_text(" ".join(authored.split())),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )


def _anchor_patch(repo: Path, *, patch_id: str, authored: str) -> dict:
    _append_patch(repo, patch_id=patch_id, authored=authored)
    (repo / "app.py").write_text("def value():\n" + authored)
    commit_sha = _commit(repo, f"apply {patch_id}")
    anchors = reconcile_commit_anchors(repo, commit_sha, writer="post-commit-correlator")
    assert len(anchors) == 1
    return anchors[0]


def test_retention_fraction_alive_on_path_is_1(tmp_path: Path) -> None:
    """``alive_on_path`` is full retention => retention_fraction == 1.0."""
    _init_repo(tmp_path)
    authored = "    return 'alive-retain'\n"
    _anchor_patch(tmp_path, patch_id="alive-retain", authored=authored)

    payload = sync_patch(tmp_path, "tracepatch-sha256:alive-retain")
    current = payload["current_survival"]
    assert current["survival_state"] == "alive_on_path"
    assert current["retention_fraction"] == 1.0


def test_retention_fraction_partially_preserved_computed(tmp_path: Path) -> None:
    """Partial preservation reports a fraction in (0, 1) rounded to 3dp."""
    _init_repo(tmp_path)
    authored = (
        "def authorize():\n"
        "    line_one()\n"
        "    line_two()\n"
        "    line_three()\n"
    )
    (tmp_path / "auth.py").write_text(authored)
    anchor_commit = _commit(tmp_path, "anchor multiline")

    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-partial",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": "tracepatch-sha256:partial-ret",
                    "file_path": "auth.py",
                    "affected_range": {"start_line": 2, "end_line": 4},
                    "authored_text": (
                        "    line_one()\n    line_two()\n    line_three()\n"
                    ),
                    "raw_authored_hash": sha256_text("partial"),
                    "git_clean_hash": sha256_text("partial"),
                    "limitations": [],
                },
            ),
            TrailEventDraft(
                event_type="git_anchor_created",
                trace_id="tr-partial",
                step_index=1,
                capture_method=["post_commit_correlator"],
                payload={
                    "git_anchor_id": "gitanchor-sha256:partial-ret",
                    "trace_patch_id": "tracepatch-sha256:partial-ret",
                    "commit_id": {"algo": "sha1", "hex": anchor_commit},
                    "path": "auth.py",
                    "range": {"start_line": 2, "end_line": 4},
                    "relation": "anchored_in_git",
                    "evidence_tier": "exact_range_hash",
                    "evidence_firmness": "firm",
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    # Trim to keep only line_one (1 of 3 lines retained).
    (tmp_path / "auth.py").write_text("def authorize():\n    line_one()\n")
    _commit(tmp_path, "trim authorize")

    payload = sync_patch(tmp_path, "tracepatch-sha256:partial-ret")
    current = payload["current_survival"]
    assert current["survival_state"] == "partially_preserved"
    fraction = current["retention_fraction"]
    assert isinstance(fraction, float)
    assert 0.0 < fraction < 1.0
    # 1 of 3 authored lines survives => 0.333.
    assert fraction == round(1 / 3, 3)


def test_retention_fraction_none_for_lost(tmp_path: Path) -> None:
    """``lost`` rows never carry a retention fraction (it would be 0)."""
    _init_repo(tmp_path)
    authored = "    return 'will-be-lost'\n"
    _anchor_patch(tmp_path, patch_id="lost-ret", authored=authored)
    subprocess.run(["git", "rm", "-q", "app.py"], cwd=tmp_path, check=True)
    _commit(tmp_path, "delete app.py")

    payload = sync_patch(tmp_path, "tracepatch-sha256:lost-ret")
    current = payload["current_survival"]
    assert current["survival_state"] == "lost"
    assert current.get("retention_fraction") is None


def test_retention_fraction_none_for_unknown(tmp_path: Path) -> None:
    """Truly indeterminate ``unknown`` rows never carry a retention fraction."""
    _init_repo(tmp_path)
    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-no-files",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": "tracepatch-sha256:no-files",
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

    payload = sync_patch(tmp_path, "tracepatch-sha256:no-files")
    current = payload["current_survival"]
    assert current["survival_state"] == "unknown"
    assert current.get("retention_fraction") is None


def test_retention_fraction_alive_transformed(tmp_path: Path) -> None:
    """``alive_transformed`` reports a retention fraction (likely 0 when
    no authored line survives literally, but the field is always present)."""
    _init_repo(tmp_path)
    authored = "    return 'transform-retention'\n"
    _anchor_patch(tmp_path, patch_id="transform-retention", authored=authored)
    (tmp_path / "app.py").write_text(
        "def value():\n    return 'human-rewrote-this-line-completely'\n"
    )
    _commit(tmp_path, "transform line")

    payload = sync_patch(tmp_path, "tracepatch-sha256:transform-retention")
    current = payload["current_survival"]
    assert current["survival_state"] == "alive_transformed"
    fraction = current["retention_fraction"]
    assert isinstance(fraction, float)
    assert 0.0 <= fraction <= 1.0
