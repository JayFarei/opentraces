"""Trace Trails Phase 4 Patch Trail follow coverage."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    reconcile_commit_anchors,
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


def _append_patch(repo: Path, *, patch_id: str, authored: str) -> None:
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
                    "affected_range": {"start_line": 2, "end_line": 2},
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


def _follow(repo: Path, *args: str) -> dict:
    result = CliRunner().invoke(
        main,
        ["trail", "follow", *args, "--json", "--project", str(repo)],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_trail_follow_patch_alive_on_path(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    authored = "    return 'alive-on-path-phase-four'\n"
    _anchor_patch(tmp_path, patch_id="alive", authored=authored)

    payload = _follow(tmp_path, "--patch", "tracepatch-sha256:alive")

    assert payload["relation"] == "patch_trail_observed"
    assert payload["current_survival"]["survival_state"] == "alive_on_path"
    assert payload["observations"][0]["path"] == "app.py"


def test_trail_follow_patch_alive_transformed(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    authored = "    return 'alive-transformed-phase-four'\n"
    _anchor_patch(tmp_path, patch_id="transformed", authored=authored)
    (tmp_path / "app.py").write_text(
        "def value():\n    return 'human-transformed-phase-four'\n"
    )
    _commit(tmp_path, "transform anchored line")

    payload = _follow(tmp_path, "--patch", "tracepatch-sha256:transformed")

    assert payload["current_survival"]["survival_state"] == "alive_transformed"


def test_trail_follow_anchor_reverted(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    authored = "    return 'reverted-phase-four'\n"
    anchor = _anchor_patch(tmp_path, patch_id="reverted", authored=authored)
    anchor_commit = anchor["commit_id"]["hex"]
    subprocess.run(["git", "revert", "--no-edit", anchor_commit], cwd=tmp_path, check=True)

    payload = _follow(tmp_path, "--anchor", anchor["git_anchor_id"])

    current = payload["current_survival"]
    assert current["survival_state"] == "reverted"
    assert current["revert_commit_id"]["hex"] == _git(tmp_path, "rev-parse", "HEAD")


def test_trail_follow_patch_lost_when_path_deleted(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    authored = "    return 'lost-phase-four'\n"
    _anchor_patch(tmp_path, patch_id="lost", authored=authored)
    subprocess.run(["git", "rm", "-q", "app.py"], cwd=tmp_path, check=True)
    _commit(tmp_path, "delete anchored file")

    payload = _follow(tmp_path, "--patch", "tracepatch-sha256:lost")

    assert payload["current_survival"]["survival_state"] == "lost"


def test_trail_follow_unknown_for_unreachable_anchor_commit(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    authored = "    return 'unknown-phase-four'\n"
    _append_patch(tmp_path, patch_id="unknown", authored=authored)
    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="git_anchor_created",
                trace_id="tr-unknown",
                step_index=1,
                capture_method=["manual_attach"],
                payload={
                    "git_anchor_id": "gitanchor-sha256:unknown",
                    "trace_patch_id": "tracepatch-sha256:unknown",
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

    payload = _follow(tmp_path, "--patch", "tracepatch-sha256:unknown")

    current = payload["current_survival"]
    assert current["survival_state"] == "unknown"
    assert "anchor_commit_not_reachable_from_head" in current["limitations"]
