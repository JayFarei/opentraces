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
            and event.payload["trace_patch_id"] == _tp("orphan")
    ]
    # Phase 5 expanded the algorithm list to include the structural
    # fallback; the search event records every tier attempted.
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
            and event.payload["trace_patch_id"] == _tp("research")
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

    search_events = [
        event
        for event in read_events(repo)
        if event.event_type == "git_anchor_search_completed"
        and event.payload["trace_patch_id"] == _tp("repeat")
    ]
    assert [event.payload["result"] for event in search_events] == [
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
