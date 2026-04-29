"""Trace Trails Phase 4 Patch Trail sync coverage."""

from __future__ import annotations

import json
import importlib
import subprocess
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
sync_module = importlib.import_module("opentraces.core.trails.sync")
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


def _sync(repo: Path, *args: str) -> dict:
    result = CliRunner().invoke(
        main,
        ["trail", "sync", *args, "--json", "--project", str(repo)],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_trail_sync_patch_alive_on_path(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    authored = "    return 'alive-on-path-phase-four'\n"
    _anchor_patch(tmp_path, patch_id="alive", authored=authored)

    payload = _sync(tmp_path, "--patch", "tracepatch-sha256:alive")

    assert payload["relation"] == "patch_trail_observed"
    assert payload["observation_scope"] == "anchor_to_head"
    assert payload["current_survival"]["survival_state"] == "alive_on_path"
    assert payload["observations"][0]["path"] == "app.py"


def test_trail_sync_patch_alive_transformed(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    authored = "    return 'alive-transformed-phase-four'\n"
    _anchor_patch(tmp_path, patch_id="transformed", authored=authored)
    (tmp_path / "app.py").write_text("def value():\n    return 'human-transformed-phase-four'\n")
    _commit(tmp_path, "transform anchored line")

    payload = _sync(tmp_path, "--patch", "tracepatch-sha256:transformed")

    assert payload["current_survival"]["survival_state"] == "alive_transformed"
    assert [obs["survival_state"] for obs in payload["observations"]] == [
        "alive_on_path",
        "alive_transformed",
    ]


def test_trail_sync_patch_emits_chronological_commit_observations(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    authored = "    return 'chronological-phase-four'\n"
    anchor = _anchor_patch(tmp_path, patch_id="chronological", authored=authored)
    anchor_commit = anchor["commit_id"]["hex"]
    (tmp_path / "notes.txt").write_text("noise\n")
    noise_commit = _commit(tmp_path, "unrelated later commit")
    (tmp_path / "app.py").write_text("def value():\n    return 'chronological-edited'\n")
    transformed_commit = _commit(tmp_path, "transform anchored line")
    subprocess.run(["git", "rm", "-q", "app.py"], cwd=tmp_path, check=True)
    deleted_commit = _commit(tmp_path, "delete anchored line")

    payload = _sync(tmp_path, "--patch", "tracepatch-sha256:chronological")

    assert [obs["observed_commit_id"]["hex"] for obs in payload["observations"]] == [
        anchor_commit,
        noise_commit,
        transformed_commit,
        deleted_commit,
    ]
    assert [obs["survival_state"] for obs in payload["observations"]] == [
        "alive_on_path",
        "alive_on_path",
        "alive_transformed",
        "lost",
    ]
    assert payload["current_survival"]["survival_state"] == "lost"


def test_survival_recomputed_from_current_git_state_not_stale_capture_boolean(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    authored = "    return 'live-recomputed-phase-four'\n"
    _anchor_patch(tmp_path, patch_id="live", authored=authored)

    before = _sync(tmp_path, "--patch", "tracepatch-sha256:live")
    assert before["current_survival"]["survival_state"] == "alive_on_path"

    (tmp_path / "app.py").write_text("def value():\n    return 'changed-after-sync'\n")
    _commit(tmp_path, "change after first sync")

    after = _sync(tmp_path, "--patch", "tracepatch-sha256:live")
    assert after["current_survival"]["survival_state"] == "alive_transformed"


def test_trail_sync_anchor_reverted(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    authored = "    return 'reverted-phase-four'\n"
    anchor = _anchor_patch(tmp_path, patch_id="reverted", authored=authored)
    anchor_commit = anchor["commit_id"]["hex"]
    subprocess.run(["git", "revert", "--no-edit", anchor_commit], cwd=tmp_path, check=True)

    payload = _sync(tmp_path, "--anchor", anchor["git_anchor_id"])

    current = payload["current_survival"]
    assert current["survival_state"] == "reverted"
    assert current["revert_commit_id"]["hex"] == _git(tmp_path, "rev-parse", "HEAD")
    assert [obs["survival_state"] for obs in payload["observations"]] == [
        "alive_on_path",
        "reverted",
    ]


def test_trail_sync_reintroduced_patch_is_alive_not_permanently_reverted(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    authored = "    return 'reintroduced-after-revert'\n"
    anchor = _anchor_patch(tmp_path, patch_id="reintroduced", authored=authored)
    anchor_commit = anchor["commit_id"]["hex"]
    subprocess.run(["git", "revert", "--no-edit", anchor_commit], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("def value():\n" + authored)
    _commit(tmp_path, "reintroduce reverted line")

    payload = _sync(tmp_path, "--anchor", anchor["git_anchor_id"])

    assert payload["current_survival"]["survival_state"] == "alive_on_path"
    assert [obs["survival_state"] for obs in payload["observations"]] == [
        "alive_on_path",
        "reverted",
        "alive_on_path",
    ]


def test_trail_sync_patch_lost_when_path_deleted(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    authored = "    return 'lost-phase-four'\n"
    _anchor_patch(tmp_path, patch_id="lost", authored=authored)
    subprocess.run(["git", "rm", "-q", "app.py"], cwd=tmp_path, check=True)
    _commit(tmp_path, "delete anchored file")

    payload = _sync(tmp_path, "--patch", "tracepatch-sha256:lost")

    assert payload["current_survival"]["survival_state"] == "lost"
    assert [obs["survival_state"] for obs in payload["observations"]] == [
        "alive_on_path",
        "lost",
    ]


def test_trail_sync_patch_aggregates_multiple_anchor_observations(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    authored = "    return 'multi-anchor-aggregation-phase-four'\n"
    _append_patch(tmp_path, patch_id="multi", authored=authored)

    (tmp_path / "app.py").write_text("def value():\n" + authored)
    first_commit = _commit(tmp_path, "apply first copy")
    assert (
        len(reconcile_commit_anchors(tmp_path, first_commit, writer="post-commit-correlator")) == 1
    )

    (tmp_path / "app.py").write_text("def value():\n" + authored + authored)
    second_commit = _commit(tmp_path, "apply second copy")
    assert (
        len(reconcile_commit_anchors(tmp_path, second_commit, writer="post-commit-correlator")) == 1
    )

    (tmp_path / "app.py").write_text("def value():\n" + authored)
    _commit(tmp_path, "remove second copy")

    payload = _sync(tmp_path, "--patch", "tracepatch-sha256:multi")

    assert [obs["survival_state"] for obs in payload["current_observations"]] == [
        "alive_on_path",
        "lost",
    ]
    assert payload["current_survival"]["survival_state"] == "alive_on_path"
    assert payload["current_survival"]["aggregation"] == "any_alive_anchor_wins"


def test_trail_sync_unknown_for_unreachable_anchor_commit(tmp_path: Path) -> None:
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

    payload = _sync(tmp_path, "--patch", "tracepatch-sha256:unknown")

    current = payload["current_survival"]
    assert current["survival_state"] == "unknown"
    assert "anchor_commit_not_reachable_from_head" in current["limitations"]


def test_trail_sync_bounds_revert_search(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    authored = "    return 'bounded-revert-search-phase-four'\n"
    _anchor_patch(tmp_path, patch_id="bounded", authored=authored)
    monkeypatch.setattr(sync_module, "REVERT_SEARCH_LIMIT", 1)

    (tmp_path / "noise_a.txt").write_text("a\n")
    _commit(tmp_path, "noise a")
    (tmp_path / "noise_b.txt").write_text("b\n")
    _commit(tmp_path, "noise b")

    payload = _sync(tmp_path, "--patch", "tracepatch-sha256:bounded")

    current = payload["current_survival"]
    assert current["survival_state"] == "alive_on_path"
    assert "revert_search_truncated" in current["limitations"]


def test_trail_sync_history_limit_keeps_current_head(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    authored = "    return 'bounded-history-phase-four'\n"
    anchor = _anchor_patch(tmp_path, patch_id="history-limit", authored=authored)
    anchor_commit = anchor["commit_id"]["hex"]

    (tmp_path / "noise_a.txt").write_text("a\n")
    first_later_commit = _commit(tmp_path, "noise a")
    (tmp_path / "noise_b.txt").write_text("b\n")
    _commit(tmp_path, "noise b")
    (tmp_path / "app.py").write_text("def value():\n    return 'bounded-history-edited'\n")
    head_commit = _commit(tmp_path, "transform after skipped commit")

    payload = _sync(
        tmp_path,
        "--patch",
        "tracepatch-sha256:history-limit",
        "--history-limit",
        "3",
    )

    assert [obs["observed_commit_id"]["hex"] for obs in payload["observations"]] == [
        anchor_commit,
        first_later_commit,
        head_commit,
    ]
    assert payload["current_survival"]["survival_state"] == "alive_transformed"
    assert "patch_trail_history_truncated" in payload["trail_limitations"]
    assert payload["history_limit"] == 3
    assert all(obs["anchor_descendant_count"] == 3 for obs in payload["observations"])


def test_trail_sync_observation_metadata_fields(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    authored = "    return 'metadata-phase-four'\n"
    _anchor_patch(tmp_path, patch_id="metadata", authored=authored)
    (tmp_path / "noise.txt").write_text("x\n")
    _commit(tmp_path, "noise")

    payload = _sync(tmp_path, "--patch", "tracepatch-sha256:metadata")

    assert payload["history_limit"] == sync_module.PATCH_TRAIL_COMMIT_LIMIT
    observations = payload["observations"]
    assert [obs["observation_sequence"] for obs in observations] == list(range(len(observations)))
    assert [obs["anchor_trail_index"] for obs in observations] == list(range(len(observations)))
    for obs in observations:
        assert isinstance(obs["observed_commit_time"], int)
        assert obs["observed_commit_time"] > 0
        assert obs["anchor_descendant_count"] == 1


def test_trail_sync_multi_anchor_indices_and_sequence(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    authored = "    return 'multi-anchor-indices-phase-four'\n"
    _append_patch(tmp_path, patch_id="indices", authored=authored)

    (tmp_path / "app.py").write_text("def value():\n" + authored)
    first_commit = _commit(tmp_path, "apply first copy")
    assert (
        len(reconcile_commit_anchors(tmp_path, first_commit, writer="post-commit-correlator")) == 1
    )

    (tmp_path / "app.py").write_text("def value():\n" + authored + authored)
    second_commit = _commit(tmp_path, "apply second copy")
    assert (
        len(reconcile_commit_anchors(tmp_path, second_commit, writer="post-commit-correlator")) == 1
    )

    payload = _sync(tmp_path, "--patch", "tracepatch-sha256:indices")

    sequences = [obs["observation_sequence"] for obs in payload["observations"]]
    assert sequences == list(range(len(sequences)))

    trail_indices = [obs["anchor_trail_index"] for obs in payload["observations"]]
    assert trail_indices.count(0) == 2

    anchor_event_sequences = {obs["anchor_event_sequence"] for obs in payload["observations"]}
    assert len(anchor_event_sequences) == 2
