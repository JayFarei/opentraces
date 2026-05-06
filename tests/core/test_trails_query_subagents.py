from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from opentraces_schema import Agent, Step, ToolCall, TraceRecord

from opentraces.core import config as config_module
from opentraces.core.config import _write_marker
from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    build_trail_query_projection,
    emit_step_window_events_from_record,
    read_events,
    write_worktree_tree,
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


def test_parent_agent_patch_without_child_trail_rows_is_typed_unresolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.setattr(config_module, "PROJECTS_DIR", tmp_path / "opentraces" / "projects")

    trace_id = "trace-subagent-unresolved"
    traces_dir = config_module.get_project_traces_dir(repo)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{trace_id}.jsonl").write_text(
        json.dumps(
            {
                "trace_id": trace_id,
                "session_id": "session-subagent-unresolved",
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
                    }
                ],
            },
            sort_keys=True,
        )
        + "\n"
    )

    parent_patch_id = _sha("parent agent unresolved patch")
    append_event_batch(
        repo,
        [_patch_event(patch_id=parent_patch_id, trace_id=trace_id, step_index=17)],
        writer="test-fixture",
    )

    projection = build_trail_query_projection(repo)

    parent = projection.patches_by_id[parent_patch_id]
    assert parent["attribution_role"] == "delegation_envelope_unresolved"
    assert parent["step_metadata"]["tool_name"] == "Agent"
    assert parent["step_metadata"]["subagent_trajectory_ref"] == "agent-a272453201cd7609a"


def test_parent_agent_aggregate_patch_with_child_refs_is_typed_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    commit_sha = _init_repo(repo)
    monkeypatch.setattr(config_module, "PROJECTS_DIR", tmp_path / "opentraces" / "projects")

    trace_id = "trace-subagent-summary"
    traces_dir = config_module.get_project_traces_dir(repo)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{trace_id}.jsonl").write_text(
        json.dumps(
            {
                "trace_id": trace_id,
                "session_id": "session-subagent-summary",
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

    child_patch_id = _sha("subagent child summary patch")
    parent_patch_id = _sha("parent agent aggregate summary patch")
    append_event_batch(
        repo,
        [
            _patch_event(patch_id=child_patch_id, trace_id=trace_id, step_index=14),
            _anchor_event(
                anchor_id=_sha("subagent child summary anchor"),
                patch_id=child_patch_id,
                trace_id=trace_id,
                step_index=14,
                commit_sha=commit_sha,
            ),
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=trace_id,
                generation_index=0,
                step_index=17,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": parent_patch_id,
                    "file_path": "delegated/subagent_note.txt",
                    "affected_range": {"start_line": 1, "end_line": 2},
                    "authored_text": "note from subagent\nextra aggregate line\n",
                    "raw_authored_hash": sha256_text(
                        "note from subagent\nextra aggregate line\n"
                    ),
                    "git_clean_hash": sha256_text(
                        "note from subagent extra aggregate line"
                    ),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    projection = build_trail_query_projection(repo)

    parent = projection.patches_by_id[parent_patch_id]
    assert parent["attribution_role"] == "delegation_envelope_summary"
    assert "delegation_envelope_summary" in parent["limitations"]
    assert parent["delegated_patch_refs"][0]["trace_patch_id"] == child_patch_id


def test_linked_subagent_worktree_hooks_emit_child_patch_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.setattr(config_module, "PROJECTS_DIR", tmp_path / "opentraces" / "projects")

    child = repo / ".claude" / "worktrees" / "agent-linked"
    child.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "agent-linked", str(child), "HEAD"],
        cwd=repo,
        check=True,
    )

    target = child / "delegated" / "subagent_note.txt"
    before_tree = write_worktree_tree(child)
    target.write_text("note from linked subagent\n")
    after_tree = write_worktree_tree(child)
    child_head = _git(child, "rev-parse", "HEAD")
    child_head_id = {"algo": "sha1", "hex": child_head}

    trace_id = "trace-linked-subagent-worktree"
    record = TraceRecord(
        trace_id=trace_id,
        session_id="session-linked-subagent-worktree",
        generation_index=0,
        agent=Agent(name="claude-code"),
        steps=[
            Step(
                step_index=17,
                role="agent",
                call_type="main",
                tool_calls=[
                    ToolCall(
                        tool_call_id="toolu-parent-agent",
                        tool_name="Agent",
                        input={"description": "delegate write"},
                    )
                ],
                subagent_trajectory_ref="agent-linked",
            ),
            Step(
                step_index=14,
                role="agent",
                call_type="subagent",
                parent_step=17,
                tool_calls=[
                    ToolCall(
                        tool_call_id="toolu-child-write",
                        tool_name="Write",
                        input={
                            "file_path": str(target),
                            "content": "note from linked subagent\n",
                        },
                    )
                ],
            ),
        ],
        metadata={
            "hook_pre_tool_use": {
                "toolu-child-write": {
                    "tool": "Write",
                    "tool_use_id": "toolu-child-write",
                    "tool_input": {
                        "file_path": str(target),
                        "content": "note from linked subagent\n",
                    },
                    "timestamp": "2026-05-06T10:00:00Z",
                    "trail": {
                        "worktree_root": str(child),
                        "tree_id": before_tree,
                        "git_head": child_head_id,
                    },
                }
            },
            "hook_post_tool_use": {
                "toolu-child-write": {
                    "tool": "Write",
                    "tool_use_id": "toolu-child-write",
                    "tool_input": {
                        "file_path": str(target),
                        "content": "note from linked subagent\n",
                    },
                    "timestamp": "2026-05-06T10:00:02Z",
                    "trail": {
                        "worktree_root": str(child),
                        "tree_id": after_tree,
                        "git_head": child_head_id,
                    },
                }
            },
        },
    )
    traces_dir = config_module.get_project_traces_dir(repo)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{trace_id}.jsonl").write_text(record.model_dump_json() + "\n")

    result = emit_step_window_events_from_record(repo, record)

    child_events = [
        event
        for event in read_events(repo)
        if event.trace_id == trace_id and event.step_index == 14
    ]
    assert result.skipped_tool_calls == 1  # parent Agent envelope has no hook pair here.
    assert [event.event_type for event in child_events] == [
        "trace_step_window_opened",
        "trace_snapshot_created",
        "trace_snapshot_created",
        "trace_step_window_closed",
        "trace_patch_created",
    ]
    patch = child_events[-1]
    assert patch.payload["file_path"] == "delegated/subagent_note.txt"
    assert patch.payload["tool_call_id"] == "toolu-child-write"

    projection = build_trail_query_projection(repo)
    row = projection.patches_by_id[patch.payload["trace_patch_id"]]
    assert row["attribution_role"] == "leaf_writer"
    assert row["delegation_parent"]["step_index"] == 17


def test_linked_subagent_bash_absolute_path_emits_child_patch_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentraces.capture.claude_code.hooks._trails import trail_state

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.setattr(config_module, "PROJECTS_DIR", tmp_path / "opentraces" / "projects")

    child = repo / ".claude" / "worktrees" / "agent-linked-bash"
    child.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "agent-linked-bash", str(child), "HEAD"],
        cwd=repo,
        check=True,
    )

    target = repo / "delegated" / "bash_absolute.txt"
    command = (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        f"Path({str(target)!r}).write_text('bash absolute\\n')\n"
        "PY"
    )
    tool_input = {"command": command}
    before = trail_state(str(child), "Bash", tool_input)
    target.write_text("bash absolute\n")
    after = trail_state(str(child), "Bash", tool_input)

    assert before["worktree_root"] == str(repo)
    assert after["worktree_root"] == str(repo)
    assert before["tree_id"] != after["tree_id"]

    trace_id = "trace-linked-subagent-bash-absolute"
    record = TraceRecord(
        trace_id=trace_id,
        session_id="session-linked-subagent-bash-absolute",
        generation_index=0,
        agent=Agent(name="claude-code"),
        steps=[
            Step(
                step_index=17,
                role="agent",
                call_type="main",
                tool_calls=[
                    ToolCall(
                        tool_call_id="toolu-parent-agent",
                        tool_name="Agent",
                        input={"description": "delegate bash write"},
                    )
                ],
                subagent_trajectory_ref="agent-linked-bash",
            ),
            Step(
                step_index=14,
                role="agent",
                call_type="subagent",
                parent_step=17,
                tool_calls=[
                    ToolCall(
                        tool_call_id="toolu-child-bash",
                        tool_name="Bash",
                        input=tool_input,
                    )
                ],
            ),
        ],
        metadata={
            "hook_pre_tool_use": {
                "toolu-child-bash": {
                    "tool": "Bash",
                    "tool_use_id": "toolu-child-bash",
                    "tool_input": tool_input,
                    "timestamp": "2026-05-06T10:00:00Z",
                    "trail": before,
                }
            },
            "hook_post_tool_use": {
                "toolu-child-bash": {
                    "tool": "Bash",
                    "tool_use_id": "toolu-child-bash",
                    "tool_input": tool_input,
                    "timestamp": "2026-05-06T10:00:02Z",
                    "trail": after,
                    "capture_status": "hook_only",
                    "limitations": ["hook_only"],
                }
            },
        },
    )
    traces_dir = config_module.get_project_traces_dir(repo)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{trace_id}.jsonl").write_text(record.model_dump_json() + "\n")

    result = emit_step_window_events_from_record(repo, record)

    child_events = [
        event
        for event in read_events(repo)
        if event.trace_id == trace_id and event.step_index == 14
    ]
    assert result.skipped_tool_calls == 1
    assert child_events[-1].event_type == "trace_patch_created"
    patch = child_events[-1]
    assert patch.payload["file_path"] == "delegated/bash_absolute.txt"
    assert patch.payload["tool_call_id"] == "toolu-child-bash"

    projection = build_trail_query_projection(repo)
    row = projection.patches_by_id[patch.payload["trace_patch_id"]]
    assert row["attribution_role"] == "leaf_writer"
    assert row["step_metadata"]["tool_name"] == "Bash"
    assert row["delegation_parent"]["step_index"] == 17
