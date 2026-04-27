"""Trace Trails Phase 1 CLI coverage."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.trails import TrailEventDraft, append_event_batch, append_exact_patch_trail


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


def _oid(repo: Path, rev_path: str) -> dict[str, str]:
    return {"algo": "sha1", "hex": _git(repo, "rev-parse", rev_path)}


def test_trail_explain_exact_patch_anchor_from_events(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    (repo / "app.py").write_text("def value():\n    return 'old'\n")
    _commit(repo, "seed")

    authored = "    return 'agent-produced-distinctive-line-54-phase-one'\n"
    (repo / "app.py").write_text("def value():\n" + authored)
    commit_sha = _commit(repo, "agent change")

    append_exact_patch_trail(
        repo,
        trace_id="tr1",
        step_index=1,
        file_path="app.py",
        authored_text=authored,
        commit_ref=commit_sha,
        writer="test-fixture",
        capture_method=["hook_posttooluse"],
    )

    result = CliRunner().invoke(
        main,
        [
            "trail",
            "explain",
            "--trace",
            "tr1",
            "--step",
            "1",
            "--json",
            "--project",
            str(repo),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["trace_id"] == "tr1"
    assert payload["step_id"] == "step_1"
    assert payload["relation"] == "anchored_in_git"
    assert payload["evidence_tier"] == "exact_range_hash"
    assert payload["evidence_firmness"] == "firm"
    assert payload["file_path"] == "app.py"
    assert payload["affected_range"] == {"start_line": 2, "end_line": 2}
    assert payload["git_anchor"]["commit_sha"] == commit_sha
    assert payload["git_anchor"]["path"] == "app.py"
    assert len(payload["trace_patch_id"]) == 64
    assert payload["trace_patch_ref"]["kind"] == "trace_patch"
    assert payload["trace_patch_ref"]["ref"].startswith("ot://trace-patch/sha256/")
    assert len(payload["git_anchor_id"]) == 64
    assert payload["git_anchor"]["git_anchor_ref"]["kind"] == "git_anchor"
    assert payload["git_anchor"]["git_anchor_ref"]["ref"].startswith("ot://git-anchor/sha256/")
    assert payload["raw_authored_hash"].startswith("sha256:")
    assert payload["git_clean_hash"].startswith("sha256:")
    assert payload["before_blob_id"]["algo"] == "sha1"
    assert payload["after_blob_id"]["algo"] == "sha1"
    assert payload["trace_snapshot_before"]["tree_id"]["algo"] == "sha1"
    assert payload["trace_snapshot_after"]["tree_id"]["algo"] == "sha1"
    assert [e["event_type"] for e in payload["source_events"]] == [
        "trace_snapshot_created",
        "trace_snapshot_created",
        "trace_patch_created",
        "git_anchor_created",
    ]

    human_commit = CliRunner().invoke(
        main,
        [
            "trail",
            "explain",
            "--commit",
            commit_sha,
            "--project",
            str(repo),
        ],
    )
    assert human_commit.exit_code == 0, human_commit.output
    assert f"tp:{payload['trace_patch_id'][:8]}" in human_commit.output
    assert payload["trace_patch_id"] not in human_commit.output
    assert "exact range match" in human_commit.output

    follow = CliRunner().invoke(
        main,
        [
            "trail",
            "follow",
            "--patch",
            payload["trace_patch_id"],
            "--project",
            str(repo),
        ],
    )
    assert follow.exit_code == 0, follow.output
    assert f"Patch Trail tp:{payload['trace_patch_id'][:8]}" in follow.output
    assert payload["trace_patch_id"] not in follow.output


def test_trail_explain_includes_containing_segment_id(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    (repo / "app.py").write_text("def value():\n    return 'old'\n")
    _commit(repo, "seed")

    authored = "    return 'agent-produced-slice-context-line-54-phase-six'\n"
    (repo / "app.py").write_text("def value():\n" + authored)
    commit_sha = _commit(repo, "agent change")

    append_exact_patch_trail(
        repo,
        trace_id="tr-slice",
        step_index=4,
        file_path="app.py",
        authored_text=authored,
        commit_ref=commit_sha,
        writer="test-fixture",
        capture_method=["hook_posttooluse"],
    )

    result = CliRunner().invoke(
        main,
        [
            "trail",
            "explain",
            "--trace",
            "tr-slice",
            "--step",
            "4",
            "--json",
            "--project",
            str(repo),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["patch_status"] == "patched"
    assert len(payload["containing_segment_id"]) == 64
    trace_slice = payload["trace_slice"]
    assert trace_slice["containing_segment_id"] == payload["containing_segment_id"]
    assert trace_slice["containing_segment_ref"]["kind"] == "trace_slice"
    assert trace_slice["content_status"] == "phase8_deferred"
    assert trace_slice["end_step_index"] == 7
    assert trace_slice["generation_index"] == 0
    assert trace_slice["git_anchor_id"] == payload["git_anchor_id"]
    assert trace_slice["git_anchor_ref"]["id"] == payload["git_anchor_id"]
    assert trace_slice["relation"] == "contains_trace_patch"
    assert trace_slice["source"] == "default_step_neighborhood"
    assert trace_slice["start_step_index"] == 1
    assert trace_slice["trace_id"] == "tr-slice"
    assert trace_slice["trace_patch_id"] == payload["trace_patch_id"]
    assert trace_slice["trace_patch_ref"]["id"] == payload["trace_patch_id"]
    assert payload["git_anchor"]["containing_segment_id"] == payload["containing_segment_id"]
    assert payload["resource_refs"]["trace_patch_trail"] == (
        f"ot://trace-patch/sha256/{payload['trace_patch_id']}/trail"
    )
    assert payload["resource_refs"]["git_anchor"] == (
        f"ot://git-anchor/sha256/{payload['git_anchor_id']}"
    )
    assert payload["resource_refs"]["file_line_origin"] == "ot://file/app.py/line/2/origin"


def test_missing_anchor_returns_unknown_not_failure(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    (repo / "app.py").write_text("def value():\n    return 'old'\n")
    seed_sha = _commit(repo, "seed")
    (repo / "app.py").write_text(
        "def value():\n    return 'agent-produced-unanchored-line-54'\n",
    )
    commit_sha = _commit(repo, "unsearched change")

    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_snapshot_created",
                trace_id="tr-missing",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "snapshot_id": "snapshot-before-missing",
                    "snapshot_role": "before",
                    "tree_id": _oid(repo, f"{seed_sha}^{{tree}}"),
                    "git_head_id": _oid(repo, seed_sha),
                    "capture_status": "phase1_logical",
                    "limitations": ["full_snapshot_storage_deferred_phase2"],
                },
            ),
            TrailEventDraft(
                event_type="trace_snapshot_created",
                trace_id="tr-missing",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "snapshot_id": "snapshot-after-missing",
                    "snapshot_role": "after",
                    "tree_id": _oid(repo, f"{commit_sha}^{{tree}}"),
                    "git_head_id": _oid(repo, commit_sha),
                    "capture_status": "phase1_logical",
                    "limitations": ["full_snapshot_storage_deferred_phase2"],
                },
            ),
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-missing",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": "tracepatch-sha256:missing-anchor",
                    "snapshot_before_id": "snapshot-before-missing",
                    "snapshot_after_id": "snapshot-after-missing",
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": "    return 'agent-produced-unanchored-line-54'\n",
                    "raw_authored_hash": "sha256:placeholder-raw",
                    "git_clean_hash": "sha256:placeholder-clean",
                    "before_blob_id": _oid(repo, f"{seed_sha}:app.py"),
                    "after_blob_id": _oid(repo, f"{commit_sha}:app.py"),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    result = CliRunner().invoke(
        main,
        [
            "trail",
            "explain",
            "--trace",
            "tr-missing",
            "--step",
            "1",
            "--json",
            "--project",
            str(repo),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["relation"] == "unknown"
    assert payload["evidence_tier"] == "unknown"
    assert payload["evidence_firmness"] == "unknown"
    assert payload["git_anchor_id"] is None
    assert "git_anchor_unknown" in payload["limitations"]


def test_research_only_trace_has_no_patch_status_without_failure(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    (repo / "notes.md").write_text("research notes\n")
    seed_sha = _commit(repo, "seed")

    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_snapshot_created",
                trace_id="tr-research-only",
                step_index=2,
                capture_method=["hook_posttooluse"],
                payload={
                    "snapshot_id": "snapshot-research-only-step2",
                    "snapshot_role": "after",
                    "tree_id": _oid(repo, f"{seed_sha}^{{tree}}"),
                    "git_head_id": _oid(repo, seed_sha),
                    "capture_status": "read_only_step",
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    result = CliRunner().invoke(
        main,
        [
            "trail",
            "explain",
            "--trace",
            "tr-research-only",
            "--step",
            "2",
            "--json",
            "--project",
            str(repo),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["relation"] == "no_patch"
    assert payload["patch_status"] == "no_patch"
    assert payload["trace_patch_id"] is None
    assert payload["git_anchor_id"] is None
    assert len(payload["containing_segment_id"]) == 64
    assert payload["trace_slice"]["containing_segment_ref"]["kind"] == "trace_slice"
    assert payload["trace_slice"]["relation"] == "contains_no_patch_step"
    assert payload["source_events"][0]["event_type"] == "trace_snapshot_created"

    human_result = CliRunner().invoke(
        main,
        [
            "trail",
            "explain",
            "--trace",
            "tr-research-only",
            "--step",
            "2",
            "--project",
            str(repo),
        ],
    )
    assert human_result.exit_code == 0, human_result.output
    assert "patch status: no_patch" in human_result.output
    assert "relation: no_patch" in human_result.output


def test_rebuild_round_trip_after_projection_delete(tmp_path: Path) -> None:
    repo = tmp_path
    _init_repo(repo)
    (repo / "app.py").write_text("def value():\n    return 'old'\n")
    _commit(repo, "seed")
    authored = "    return 'agent-produced-roundtrip-line-54-phase-one'\n"
    (repo / "app.py").write_text("def value():\n" + authored)
    commit_sha = _commit(repo, "agent change")
    append_exact_patch_trail(
        repo,
        trace_id="tr-roundtrip",
        step_index=1,
        file_path="app.py",
        authored_text=authored,
        commit_ref=commit_sha,
        writer="test-fixture",
        capture_method=["hook_posttooluse"],
    )

    args = [
        "trail",
        "explain",
        "--trace",
        "tr-roundtrip",
        "--step",
        "1",
        "--json",
        "--project",
        str(repo),
    ]
    first = CliRunner().invoke(main, args)
    assert first.exit_code == 0, first.output
    subprocess.run(
        ["git", "update-ref", "-d", "refs/notes/opentraces"],
        cwd=repo,
        check=False,
    )
    refs = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname)", "refs/opentraces/local/traces"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    for ref in refs.stdout.splitlines():
        subprocess.run(["git", "update-ref", "-d", ref], cwd=repo, check=True)
    second = CliRunner().invoke(main, args)
    assert second.exit_code == 0, second.output
    assert json.loads(second.output) == json.loads(first.output)
