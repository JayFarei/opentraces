"""Trace Trails Phase 3 delayed Git Anchor coverage."""

from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    read_events,
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


def _oid(repo: Path, rev_path: str) -> dict[str, str]:
    return {"algo": "sha1", "hex": _git(repo, "rev-parse", rev_path)}


def _tp(name: str) -> str:
    return hashlib.sha256(f"trace-patch:{name}".encode("utf-8")).hexdigest()


def _ga(name: str) -> str:
    return hashlib.sha256(f"git-anchor:{name}".encode("utf-8")).hexdigest()


def test_post_commit_reconciler_adds_delayed_git_anchor(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    seed_sha = _git(repo, "rev-parse", "HEAD")
    authored = "    return 'delayed-anchor-distinctive-line-54-phase-three'\n"
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-delayed",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": _tp("delayed"),
                    "snapshot_before_id": "snapshot-before-delayed",
                    "snapshot_after_id": "snapshot-after-delayed",
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": authored,
                    "raw_authored_hash": sha256_text(authored),
                    "git_clean_hash": sha256_text(" ".join(authored.split())),
                    "before_blob_id": _oid(repo, f"{seed_sha}:app.py"),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    (repo / "app.py").write_text("def value():\n" + authored)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "delayed agent patch"], cwd=repo, check=True)
    commit_sha = _git(repo, "rev-parse", "HEAD")

    created = reconcile_commit_anchors(repo, commit_sha, writer="post-commit-correlator")
    assert [anchor["trace_patch_id"] for anchor in created] == [_tp("delayed")]

    trace_result = CliRunner().invoke(
        main,
        [
            "trail",
            "explain",
            "--trace",
            "tr-delayed",
            "--step",
            "1",
            "--json",
            "--project",
            str(repo),
        ],
    )
    assert trace_result.exit_code == 0, trace_result.output
    trace_payload = json.loads(trace_result.output)
    assert trace_payload["relation"] == "anchored_in_git"
    assert trace_payload["git_anchor"]["commit_sha"] == commit_sha

    commit_result = CliRunner().invoke(
        main,
        [
            "trail",
            "explain",
            "--commit",
            commit_sha,
            "--json",
            "--project",
            str(repo),
        ],
    )
    assert commit_result.exit_code == 0, commit_result.output
    commit_payload = json.loads(commit_result.output)
    assert commit_payload["commit_sha"] == commit_sha
    assert commit_payload["trace_patches"][0]["trace_id"] == "tr-delayed"
    assert commit_payload["trace_patches"][0]["trace_patch_id"] == _tp("delayed")
    assert commit_payload["trace_patches"][0]["evidence_tier"] == "exact_range_hash"
    assert "git_anchor_search_completed" in [
        event["event_type"] for event in commit_payload["source_events"]
    ]

    line_result = CliRunner().invoke(
        main,
        [
            "trail",
            "explain",
            "app.py:2",
            "--json",
            "--project",
            str(repo),
        ],
    )
    assert line_result.exit_code == 0, line_result.output
    line_payload = json.loads(line_result.output)
    assert line_payload["target"] == "app.py:2"
    assert line_payload["trace_patch"]["trace_id"] == "tr-delayed"
    assert line_payload["trace_patch"]["trace_patch_id"] == _tp("delayed")


def test_trail_mature_anchors_patch_created_after_commit(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    seed_sha = _git(repo, "rev-parse", "HEAD")
    authored = "    return 'patch-created-after-commit-plan-54'\n"

    (repo / "app.py").write_text("def value():\n" + authored)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit before ingest"], cwd=repo, check=True)
    commit_sha = _git(repo, "rev-parse", "HEAD")

    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-after-commit",
                step_index=1,
                capture_method=["watcher_backstop"],
                payload={
                    "trace_patch_id": _tp("after-commit"),
                    "snapshot_before_id": "snapshot-before-after-commit",
                    "snapshot_after_id": "snapshot-after-after-commit",
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": authored,
                    "raw_authored_hash": sha256_text(authored),
                    "git_clean_hash": sha256_text(" ".join(authored.split())),
                    "before_blob_id": _oid(repo, f"{seed_sha}:app.py"),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    result = CliRunner().invoke(
        main,
        ["trail", "mature", "--commit", commit_sha, "--json", "--project", str(repo)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["commits_considered"] == 1
    assert payload["searches_completed"] == 1
    assert payload["anchors_created"] == 1

    events = read_events(repo)
    assert any(e.event_type == "git_anchor_created" for e in events)
    search = [e for e in events if e.event_type == "git_anchor_search_completed"][0]
    assert search.payload["results"][0]["result"] == "anchored"


def test_trail_mature_deduplicates_replayed_trace_patch_events(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    seed_sha = _git(repo, "rev-parse", "HEAD")
    authored = "    return 'watcher-corroborated-replay-dedup'\n"

    (repo / "app.py").write_text("def value():\n" + authored)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit before replay"], cwd=repo, check=True)
    commit_sha = _git(repo, "rev-parse", "HEAD")

    payload = {
        "trace_patch_id": _tp("replayed"),
        "snapshot_before_id": "snapshot-before-replayed",
        "snapshot_after_id": "snapshot-after-replayed",
        "file_path": "app.py",
        "affected_range": {"start_line": 2, "end_line": 2},
        "authored_text": authored,
        "raw_authored_hash": sha256_text(authored),
        "git_clean_hash": sha256_text(" ".join(authored.split())),
        "before_blob_id": _oid(repo, f"{seed_sha}:app.py"),
        "limitations": [],
    }
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-replayed",
                step_index=1,
                capture_method=["hook_pretooluse", "hook_posttooluse"],
                payload=payload,
            ),
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-replayed",
                step_index=1,
                capture_method=[
                    "hook_pretooluse",
                    "hook_posttooluse",
                    "watcher_backstop",
                ],
                payload=payload,
            ),
        ],
        writer="test-fixture",
    )

    result = CliRunner().invoke(
        main,
        ["trail", "mature", "--commit", commit_sha, "--json", "--project", str(repo)],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["searches_completed"] == 1
    assert summary["anchors_created"] == 1

    events = read_events(repo)
    anchors = [e for e in events if e.event_type == "git_anchor_created"]
    searches = [e for e in events if e.event_type == "git_anchor_search_completed"]
    assert len(anchors) == 1
    assert len(searches) == 1
    assert anchors[0].payload["trace_patch_id"] == _tp("replayed")


def test_trail_mature_records_unknowns_and_is_idempotent(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    seed_sha = _git(repo, "rev-parse", "HEAD")
    authored = "    return 'not in this commit'\n"
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-unknown",
                step_index=1,
                capture_method=["watcher_backstop"],
                payload={
                    "trace_patch_id": _tp("unknown"),
                    "snapshot_before_id": "snapshot-before-unknown",
                    "snapshot_after_id": "snapshot-after-unknown",
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": authored,
                    "raw_authored_hash": sha256_text(authored),
                    "git_clean_hash": sha256_text(" ".join(authored.split())),
                    "before_blob_id": _oid(repo, f"{seed_sha}:app.py"),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    first = CliRunner().invoke(
        main,
        ["trail", "mature", "--commit", "HEAD", "--json", "--project", str(repo)],
    )
    assert first.exit_code == 0, first.output
    first_payload = json.loads(first.output)
    assert first_payload["searches_completed"] == 1
    assert first_payload["anchors_created"] == 0

    second = CliRunner().invoke(
        main,
        ["trail", "mature", "--commit", "HEAD", "--json", "--project", str(repo)],
    )
    assert second.exit_code == 0, second.output
    second_payload = json.loads(second.output)
    assert second_payload["searches_completed"] == 0
    assert second_payload["anchors_created"] == 0

    searches = [
        e for e in read_events(repo)
        if e.event_type == "git_anchor_search_completed"
    ]
    assert len(searches) == 1
    assert searches[0].payload["results"][0]["result"] == "unknown"


def test_trail_mature_fails_for_invalid_explicit_commit(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)

    result = CliRunner().invoke(
        main,
        [
            "trail",
            "mature",
            "--commit",
            "definitely-not-a-ref",
            "--json",
            "--project",
            str(repo),
        ],
    )

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["commits_considered"] == 0
    assert payload["errors"] == ["unresolved commit ref: definitely-not-a-ref"]


def test_trail_mature_fails_for_non_git_project(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["trail", "mature", "--json", "--project", str(tmp_path)],
    )

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["commits_considered"] == 0
    assert payload["errors"] == ["not a Git repository or HEAD is unavailable"]


def test_ot_trace_patch_trail_resource_resolves(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    seed_sha = _git(repo, "rev-parse", "HEAD")
    authored = "    return 'resource-trace-patch-trail-line-54-phase-six'\n"
    trace_patch_id = _tp("resource-trail")
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-resource",
                step_index=3,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": trace_patch_id,
                    "snapshot_before_id": "snapshot-before-resource",
                    "snapshot_after_id": "snapshot-after-resource",
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": authored,
                    "raw_authored_hash": sha256_text(authored),
                    "git_clean_hash": sha256_text(" ".join(authored.split())),
                    "before_blob_id": _oid(repo, f"{seed_sha}:app.py"),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    (repo / "app.py").write_text("def value():\n" + authored)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "resource patch"], cwd=repo, check=True)
    commit_sha = _git(repo, "rev-parse", "HEAD")
    reconcile_commit_anchors(repo, commit_sha, writer="post-commit-correlator")

    resource = f"ot://trace-patch/sha256/{trace_patch_id}/trail"
    result = CliRunner().invoke(
        main,
        ["trail", "resolve", resource, "--json", "--project", str(repo)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["resource"] == resource
    assert payload["resource_type"] == "trace_patch_trail"
    assert payload["relation"] == "trace_patch_trail_resolved"
    assert payload["trace_id"] == "tr-resource"
    assert payload["trace_patch_id"] == trace_patch_id
    assert len(payload["containing_segment_id"]) == 64
    assert payload["trace_slice"]["containing_segment_ref"]["kind"] == "trace_slice"
    assert payload["trace_slice"]["start_step_index"] == 0
    assert payload["trace_slice"]["end_step_index"] == 6
    assert payload["trail"]["relation"] == "patch_trail_observed"


def test_ot_git_anchor_resource_resolves(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    seed_sha = _git(repo, "rev-parse", "HEAD")
    authored = "    return 'resource-git-anchor-line-54-phase-six'\n"
    trace_patch_id = _tp("resource-anchor")
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-anchor-resource",
                step_index=5,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": trace_patch_id,
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": authored,
                    "raw_authored_hash": sha256_text(authored),
                    "git_clean_hash": sha256_text(" ".join(authored.split())),
                    "before_blob_id": _oid(repo, f"{seed_sha}:app.py"),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    (repo / "app.py").write_text("def value():\n" + authored)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "resource anchor"], cwd=repo, check=True)
    commit_sha = _git(repo, "rev-parse", "HEAD")
    anchors = reconcile_commit_anchors(repo, commit_sha, writer="post-commit-correlator")
    git_anchor_id = anchors[0]["git_anchor_id"]

    resource = f"ot://git-anchor/sha256/{git_anchor_id}"
    result = CliRunner().invoke(
        main,
        ["trail", "resolve", resource, "--json", "--project", str(repo)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["resource"] == resource
    assert payload["resource_type"] == "git_anchor"
    assert payload["relation"] == "git_anchor_resolved"
    assert payload["git_anchor"]["git_anchor_id"] == git_anchor_id
    assert payload["trace_patch"]["trace_patch_id"] == trace_patch_id
    assert len(payload["containing_segment_id"]) == 64
    assert payload["trace_slice"]["containing_segment_ref"]["kind"] == "trace_slice"
    assert payload["trace_slice"]["start_step_index"] == 2
    assert payload["trace_slice"]["end_step_index"] == 8
    assert payload["trail"]["relation"] == "patch_trail_observed"


def test_ot_file_line_origin_resource_resolves(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    seed_sha = _git(repo, "rev-parse", "HEAD")
    authored = "    return 'resource-file-line-origin-54-phase-six'\n"
    trace_patch_id = _tp("resource-file-line")
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-file-resource",
                step_index=2,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": trace_patch_id,
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": authored,
                    "raw_authored_hash": sha256_text(authored),
                    "git_clean_hash": sha256_text(" ".join(authored.split())),
                    "before_blob_id": _oid(repo, f"{seed_sha}:app.py"),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    (repo / "app.py").write_text("def value():\n" + authored)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "resource file line"], cwd=repo, check=True)
    commit_sha = _git(repo, "rev-parse", "HEAD")
    anchors = reconcile_commit_anchors(repo, commit_sha, writer="post-commit-correlator")

    resource = "ot://file/app.py/line/2/origin"
    result = CliRunner().invoke(
        main,
        ["trail", "resolve", resource, "--json", "--project", str(repo)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["resource"] == resource
    assert payload["resource_type"] == "file_line_origin"
    assert payload["relation"] == "anchored_in_git"
    assert payload["trace_patch"]["trace_patch_id"] == trace_patch_id
    assert payload["git_anchor"]["git_anchor_id"] == anchors[0]["git_anchor_id"]
    assert len(payload["containing_segment_id"]) == 64
    assert payload["trace_slice"]["containing_segment_ref"]["kind"] == "trace_slice"


def test_ot_resources_normalize_legacy_prefixed_anchor_patch_ids(
    tmp_path: Path,
) -> None:
    repo = tmp_path
    _init_repo(repo)
    seed_sha = _git(repo, "rev-parse", "HEAD")
    authored = "    return 'legacy resource anchor bridge'\n"
    trace_patch_id = _tp("legacy-resource-anchor")
    git_anchor_id = _ga("legacy-resource-anchor")

    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-legacy-resource",
                step_index=4,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": f"tracepatch-sha256:{trace_patch_id}",
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": authored,
                    "raw_authored_hash": sha256_text(authored),
                    "git_clean_hash": sha256_text(" ".join(authored.split())),
                    "before_blob_id": _oid(repo, f"{seed_sha}:app.py"),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    (repo / "app.py").write_text("def value():\n" + authored)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "legacy resource anchor"],
        cwd=repo,
        check=True,
    )
    commit_sha = _git(repo, "rev-parse", "HEAD")
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="git_anchor_created",
                trace_id="tr-legacy-resource",
                step_index=4,
                capture_method=["post_commit_correlator"],
                payload={
                    "git_anchor_id": f"gitanchor-sha256:{git_anchor_id}",
                    "trace_patch_id": f"tracepatch-sha256:{trace_patch_id}",
                    "commit_id": {"algo": "sha1", "hex": commit_sha},
                    "path": "app.py",
                    "range": {"start_line": 2, "end_line": 2},
                    "blob_id": _oid(repo, f"{commit_sha}:app.py"),
                    "observed_ref": commit_sha,
                    "relation": "anchored_in_git",
                    "evidence_tier": "exact_range_hash",
                    "evidence_firmness": "firm",
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    anchor_resource = f"ot://git-anchor/sha256/{git_anchor_id}"
    anchor_result = CliRunner().invoke(
        main,
        ["trail", "resolve", anchor_resource, "--json", "--project", str(repo)],
    )
    assert anchor_result.exit_code == 0, anchor_result.output
    anchor_payload = json.loads(anchor_result.output)
    assert anchor_payload["git_anchor_id"] == git_anchor_id
    assert anchor_payload["trace_patch_id"] == trace_patch_id
    assert anchor_payload["trace_patch"]["trace_patch_id"] == trace_patch_id
    assert anchor_payload["lineage_key"]["trace_patch"]["id"] == trace_patch_id
    assert anchor_payload["lineage_key"]["git_anchor"]["id"] == git_anchor_id

    line_result = CliRunner().invoke(
        main,
        [
            "trail",
            "resolve",
            "ot://file/app.py/line/2/origin",
            "--json",
            "--project",
            str(repo),
        ],
    )
    assert line_result.exit_code == 0, line_result.output
    line_payload = json.loads(line_result.output)
    assert line_payload["trace_patch"]["trace_patch_id"] == trace_patch_id
    assert line_payload["git_anchor"]["git_anchor_id"] == git_anchor_id


def test_no_match_appends_search_completed_unknown(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    authored = "    return 'missing-anchor-distinctive-line-54-phase-three'\n"
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-orphan",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": _tp("orphan"),
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

    (repo / "other.py").write_text("print('unrelated')\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "unrelated"], cwd=repo, check=True)
    commit_sha = _git(repo, "rev-parse", "HEAD")

    assert reconcile_commit_anchors(repo, commit_sha, writer="post-commit-correlator") == []

    commit_result = CliRunner().invoke(
        main,
        [
            "trail",
            "explain",
            "--commit",
            commit_sha,
            "--json",
            "--project",
            str(repo),
        ],
    )
    assert commit_result.exit_code == 0, commit_result.output
    payload = json.loads(commit_result.output)
    assert payload["trace_patches"] == []
    search_events = [
        event
        for event in payload["source_events"]
        if event["event_type"] == "git_anchor_search_completed"
    ]
    assert search_events
    assert search_events[0]["result"] == "unknown"
    stored_search_events = [
        event
        for event in read_events(repo)
        if event.event_type == "git_anchor_search_completed"
        and any(
            r["trace_patch_id"] == _tp("orphan")
            for r in event.payload.get("results", [])
        )
    ]
    # Phase 5 expanded the algorithm list to include the structural
    # fallback; the summary records every tier attempted (top-level).
    assert stored_search_events[0].payload["algorithms_attempted"] == [
        "exact_range_hash",
        "structural_match",
    ]


def test_unanchored_patch_can_be_researched_under_new_attribution_version(
    tmp_path: Path,
) -> None:
    repo = tmp_path
    _init_repo(repo)
    authored = "    return 'future-algorithm-might-find-this'\n"
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-research",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": _tp("research"),
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

    (repo / "other.py").write_text("print('still unrelated')\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "unrelated"], cwd=repo, check=True)
    commit_sha = _git(repo, "rev-parse", "HEAD")

    assert reconcile_commit_anchors(repo, commit_sha) == []
    assert reconcile_commit_anchors(repo, commit_sha) == []
    assert (
        reconcile_commit_anchors(
            repo,
            commit_sha,
            attribution_version="0.2.0",
        )
        == []
    )

    search_versions = [
        event.ATTRIBUTION_VERSION
        for event in read_events(repo)
        if event.event_type == "git_anchor_search_completed"
        and any(
            r["trace_patch_id"] == _tp("research")
            for r in event.payload.get("results", [])
        )
    ]
    assert search_versions == ["0.1.0", "0.2.0"]


def test_many_trace_patches_in_one_commit(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    (repo / "other.py").write_text("def other():\n    return 'old'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add other"], cwd=repo, check=True)
    alpha = "    return 'alpha-delayed-anchor-line-54-phase-three'\n"
    beta = "    return 'beta-delayed-anchor-line-54-phase-three'\n"
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-alpha",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": _tp("alpha"),
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": alpha,
                    "raw_authored_hash": sha256_text(alpha),
                    "git_clean_hash": sha256_text(" ".join(alpha.split())),
                    "limitations": [],
                },
            ),
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-beta",
                step_index=2,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": _tp("beta"),
                    "file_path": "other.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": beta,
                    "raw_authored_hash": sha256_text(beta),
                    "git_clean_hash": sha256_text(" ".join(beta.split())),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )
    (repo / "app.py").write_text("def value():\n" + alpha)
    (repo / "other.py").write_text("def other():\n" + beta)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "two delayed patches"], cwd=repo, check=True)
    commit_sha = _git(repo, "rev-parse", "HEAD")

    created = reconcile_commit_anchors(repo, commit_sha, writer="post-commit-correlator")
    assert {anchor["trace_patch_id"] for anchor in created} == {
        _tp("alpha"),
        _tp("beta"),
    }

    # plan 090: the two patch-searches collapse into ONE summary event whose
    # results carry both per-patch outcomes. This is the N>1 meaning-preservation
    # the single-patch fixtures cannot exercise: one summary, two results.
    summaries = [
        e for e in read_events(repo)
        if e.event_type == "git_anchor_search_completed"
    ]
    assert len(summaries) == 1
    summary = summaries[0].payload
    assert summary["summary"] is True
    assert summary["searched"] == 2
    assert summary["anchored"] == 2
    assert summary["unknown"] == 0
    assert len(summary["results"]) == 2
    assert {r["trace_patch_id"] for r in summary["results"]} == {
        _tp("alpha"),
        _tp("beta"),
    }
    assert {r["result"] for r in summary["results"]} == {"anchored"}
    # The summary spans both traces, so its top-level trace_id is None; the
    # per-patch trace_ids live inside results[].
    assert summaries[0].trace_id is None
    assert {r["trace_id"] for r in summary["results"]} == {"tr-alpha", "tr-beta"}

    result = CliRunner().invoke(
        main,
        ["trail", "explain", "--commit", commit_sha, "--json", "--project", str(repo)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert {patch["trace_id"] for patch in payload["trace_patches"]} == {
        "tr-alpha",
        "tr-beta",
    }


def test_one_trace_patch_can_anchor_in_multiple_commits(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    authored = "    return 'repeat-delayed-anchor-line-54-phase-three'\n"
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-repeat",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": _tp("repeat"),
                    "snapshot_before_id": "snapshot-before-repeat",
                    "snapshot_after_id": "snapshot-after-repeat",
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

    (repo / "app.py").write_text("def value():\n" + authored)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "apply repeated patch"], cwd=repo, check=True)
    first_commit = _git(repo, "rev-parse", "HEAD")
    assert [
        anchor["trace_patch_id"]
        for anchor in reconcile_commit_anchors(repo, first_commit, writer="post-commit-correlator")
    ] == [_tp("repeat")]

    (repo / "app.py").write_text("def value():\n    return 'intermediate-without-repeat-anchor'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "remove repeated patch"], cwd=repo, check=True)
    middle_commit = _git(repo, "rev-parse", "HEAD")
    assert reconcile_commit_anchors(repo, middle_commit, writer="post-commit-correlator") == []

    (repo / "app.py").write_text("def value():\n" + authored)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "reapply repeated patch"], cwd=repo, check=True)
    second_commit = _git(repo, "rev-parse", "HEAD")
    assert [
        anchor["trace_patch_id"]
        for anchor in reconcile_commit_anchors(repo, second_commit, writer="post-commit-correlator")
    ] == [_tp("repeat")]

    anchor_events = [
        event
        for event in read_events(repo)
        if event.event_type == "git_anchor_created"
        and event.payload["trace_patch_id"] == _tp("repeat")
    ]
    assert {event.payload["commit_id"]["hex"] for event in anchor_events} == {
        first_commit,
        second_commit,
    }

    # #358 v3: results[] is ANCHORED-ONLY, so the middle (unknown) commit's
    # summary carries zero result dicts for this patch -- read the outcome
    # off the scalars (shape-independent) instead of digging into results[],
    # and cross-check results[] content for the two anchored commits.
    def _outcome_for(commit_sha: str) -> str:
        matches = [
            event
            for event in read_events(repo)
            if event.event_type == "git_anchor_search_completed"
            and (event.payload.get("search_head") or {}).get("hex") == commit_sha
        ]
        assert len(matches) == 1, f"expected exactly one search summary for {commit_sha}"
        payload = matches[0].payload
        if payload["anchored"]:
            assert any(
                r["trace_patch_id"] == _tp("repeat") and r["result"] == "anchored"
                for r in payload["results"]
            )
            return "anchored"
        assert payload["unknown"] == 1
        assert payload["results"] == []
        return "unknown"

    assert [
        _outcome_for(commit)
        for commit in [first_commit, middle_commit, second_commit]
    ] == [
        "anchored",
        "unknown",
        "anchored",
    ]

    result = CliRunner().invoke(
        main,
        ["trail", "explain", "--commit", second_commit, "--json", "--project", str(repo)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["commit_sha"] == second_commit
    assert payload["trace_patches"][0]["trace_patch_id"] == _tp("repeat")

    trace_result = CliRunner().invoke(
        main,
        [
            "trail",
            "explain",
            "--trace",
            "tr-repeat",
            "--step",
            "1",
            "--json",
            "--project",
            str(repo),
        ],
    )
    assert trace_result.exit_code == 0, trace_result.output
    trace_payload = json.loads(trace_result.output)
    assert [anchor["commit_sha"] for anchor in trace_payload["git_anchors"]] == [
        first_commit,
        second_commit,
    ]
    assert trace_payload["git_anchor"]["commit_sha"] == second_commit
    assert trace_payload["git_anchor_id"] == trace_payload["git_anchors"][-1]["git_anchor_id"]
    assert "multiple_candidate_commits" in trace_payload["limitations"]
