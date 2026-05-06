from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from opentraces.core import config as config_module
from opentraces.core.config import _write_marker
from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    build_trail_query_projection,
)
from opentraces.core.trails.models import sha256_text


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    _write_marker(repo, "project-subagent-query", {})
    (repo / "delegated").mkdir()
    (repo / "delegated" / "subagent_note.txt").write_text("note from subagent\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


def _sha(label: str) -> str:
    return sha256_text(label).split(":", 1)[1]


def _patch_event(*, patch_id: str, trace_id: str, step_index: int) -> TrailEventDraft:
    authored = "note from subagent\n"
    return TrailEventDraft(
        event_type="trace_patch_created",
        trace_id=trace_id,
        generation_index=0,
        step_index=step_index,
        capture_method=["hook_posttooluse"],
        payload={
            "trace_patch_id": patch_id,
            "file_path": "delegated/subagent_note.txt",
            "affected_range": {"start_line": 1, "end_line": 1},
            "authored_text": authored,
            "raw_authored_hash": sha256_text(authored),
            "git_clean_hash": sha256_text(" ".join(authored.split())),
            "limitations": [],
        },
    )


def _anchor_event(
    *,
    anchor_id: str,
    patch_id: str,
    trace_id: str,
    step_index: int,
    commit_sha: str,
) -> TrailEventDraft:
    return TrailEventDraft(
        event_type="git_anchor_created",
        trace_id=trace_id,
        generation_index=0,
        step_index=step_index,
        capture_method=["hook_posttooluse"],
        payload={
            "git_anchor_id": anchor_id,
            "trace_patch_id": patch_id,
            "commit_id": {"algo": "sha1", "hex": commit_sha},
            "path": "delegated/subagent_note.txt",
            "range": {"start_line": 1, "end_line": 1},
            "relation": "anchored_in_git",
            "evidence_tier": "exact_range_hash",
            "evidence_firmness": "firm",
            "limitations": [],
        },
    )


@pytest.mark.usefixtures("monkeypatch")
def test_subagent_leaf_patch_owns_parent_agent_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    commit_sha = _init_repo(repo)
    monkeypatch.setattr(config_module, "PROJECTS_DIR", tmp_path / "opentraces" / "projects")

    trace_id = "trace-subagent-query"
    traces_dir = config_module.get_project_traces_dir(repo)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{trace_id}.jsonl").write_text(
        json.dumps(
            {
                "trace_id": trace_id,
                "session_id": "session-subagent-query",
                "generation_index": 0,
                "steps": [
                    {
                        "step_index": 17,
                        "role": "agent",
                        "call_type": "main",
                        "tool_calls": [
                            {
                                "tool_call_id": "toolu-parent-agent",
                                "tool_name": "Agent",
                                "input": {"description": "delegate write"},
                            }
                        ],
                        "subagent_trajectory_ref": "agent-a272453201cd7609a",
                    },
                    {
                        "step_index": 14,
                        "role": "agent",
                        "call_type": "subagent",
                        "parent_step": 17,
                        "tool_calls": [
                            {
                                "tool_call_id": "toolu-child-write",
                                "tool_name": "Write",
                                "input": {"file_path": "delegated/subagent_note.txt"},
                            }
                        ],
                    },
                ],
            },
            sort_keys=True,
        )
        + "\n"
    )

    child_patch_id = _sha("subagent child patch")
    parent_patch_id = _sha("parent agent duplicate patch")
    child_anchor_id = _sha("subagent child anchor")
    parent_anchor_id = _sha("parent agent duplicate anchor")
    append_event_batch(
        repo,
        [
            _patch_event(patch_id=child_patch_id, trace_id=trace_id, step_index=14),
            _anchor_event(
                anchor_id=child_anchor_id,
                patch_id=child_patch_id,
                trace_id=trace_id,
                step_index=14,
                commit_sha=commit_sha,
            ),
            _patch_event(patch_id=parent_patch_id, trace_id=trace_id, step_index=17),
            _anchor_event(
                anchor_id=parent_anchor_id,
                patch_id=parent_patch_id,
                trace_id=trace_id,
                step_index=17,
                commit_sha=commit_sha,
            ),
        ],
        writer="test-fixture",
    )

    projection = build_trail_query_projection(repo)

    child = projection.patches_by_id[child_patch_id]
    parent = projection.patches_by_id[parent_patch_id]
    assert child["attribution_role"] == "leaf_writer"
    assert child["delegation_parent"]["step_index"] == 17
    assert child["step_metadata"]["call_type"] == "subagent"
    assert child["step_metadata"]["tool_name"] == "Write"

    assert parent["attribution_role"] == "delegation_envelope_duplicate"
    assert parent["owned_by_trace_patch_id"] == child_patch_id
    assert "delegation_envelope_duplicate" in parent["limitations"]
    assert parent["delegated_patch_refs"] == [
        {
            "trace_id": trace_id,
            "generation_index": 0,
            "step_index": 14,
            "step_id": "step_14",
            "trace_patch_id": child_patch_id,
            "git_anchor_id": child_anchor_id,
            "commit_sha": commit_sha,
            "file_path": "delegated/subagent_note.txt",
            "affected_range": {"start_line": 1, "end_line": 1},
            "range": {"start_line": 1, "end_line": 1},
            "tool_name": "Write",
            "tool_call_id": "toolu-child-write",
            "attribution_role": "leaf_writer",
        }
    ]
    assert projection.delegated_patches_for_parent_step(trace_id, 17)[0][
        "trace_patch_id"
    ] == child_patch_id

    anchored_rows = {row["trace_patch_id"]: row for row in projection.anchors_for_commit(commit_sha)}
    assert anchored_rows[child_patch_id]["attribution_role"] == "leaf_writer"
    assert anchored_rows[parent_patch_id]["owned_by_trace_patch_id"] == child_patch_id
