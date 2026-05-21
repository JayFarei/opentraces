"""Advanced Codex CLI parser semantics that do not require live Codex."""

from __future__ import annotations

import json
from pathlib import Path

from opentraces.capture.codex_cli.parse import CodexCliParser


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def _base_rows(
    *,
    session_id: str,
    cwd: Path,
    user_text: str = "Use the opentraces skill to inspect capture status.",
) -> list[dict]:
    return [
        {
            "timestamp": "2026-05-21T09:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "timestamp": "2026-05-21T09:00:00Z",
                "cwd": str(cwd),
                "originator": "codex-tui",
                "cli_version": "0.132.0",
                "source": "tui",
                "agent_nickname": "Codex",
                "agent_role": "worker",
                "model_provider": "openai",
                "model": "gpt-5.5",
            },
        },
        {
            "timestamp": "2026-05-21T09:00:01Z",
            "type": "turn_context",
            "payload": {
                "turn_id": f"{session_id}-turn",
                "cwd": str(cwd),
                "approval_policy": "never",
                "permission_profile": {"type": "trusted"},
            },
        },
        {
            "timestamp": "2026-05-21T09:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": user_text}],
            },
        },
    ]


def test_skill_invocation_metadata_does_not_pollute_task_intent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    session = tmp_path / "rollout-skill.jsonl"
    rows = _base_rows(session_id="codex-skill", cwd=project)
    rows.extend([
        {
            "timestamp": "2026-05-21T09:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "skill_invoke",
                "call_id": "call_skill",
                "arguments": json.dumps({
                    "skill_name": "opentraces",
                    "body": "Base directory for this skill: /private/hidden",
                }),
            },
        },
        {
            "timestamp": "2026-05-21T09:00:04Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_skill",
                "output": "skill loaded",
            },
        },
        {
            "timestamp": "2026-05-21T09:00:05Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "I used the skill."}],
            },
        },
    ])
    _write_jsonl(session, rows)

    record = CodexCliParser().parse_session(session)

    assert record is not None
    assert record.task.description == "Use the opentraces skill to inspect capture status."
    assert "Base directory for this skill" not in record.task.description
    assert record.metadata["skill_invocations"] == [
        {
            "step_index": 2,
            "tool_call_id": "call_skill",
            "skill_name": "opentraces",
            "source": "codex_cli_tool_call",
        }
    ]
    assert record.metadata["normalized_tool_calls"][0]["tool_kind"] == "skill"


def test_sidecar_permission_and_compaction_metadata(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    session_id = "codex-sidecar"
    session = tmp_path / "rollout-sidecar.jsonl"
    rows = _base_rows(session_id=session_id, cwd=project, user_text="Edit the file.")
    rows.extend([
        {
            "timestamp": "2026-05-21T09:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "apply_patch",
                "call_id": "call_patch",
                "arguments": json.dumps({"patch": "*** Begin Patch\n*** End Patch"}),
            },
        },
        {
            "timestamp": "2026-05-21T09:00:04Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_patch",
                "output": "Done!",
            },
        },
    ])
    _write_jsonl(session, rows)
    sidecar = project / ".opentraces" / "codex-cli" / "hooks" / f"{session_id}.jsonl"
    _write_jsonl(sidecar, [
        {
            "type": "opentraces_hook",
            "event": "PermissionRequest",
            "timestamp": "2026-05-21T09:00:02Z",
            "data": {
                "turn_id": "turn-1",
                "tool": "apply_patch",
                "tool_input": {"path": "src/app.py"},
                "permission_mode": "on-request",
            },
        },
        {
            "type": "opentraces_hook",
            "event": "PreCompact",
            "timestamp": "2026-05-21T09:00:06Z",
            "data": {"trigger": "manual"},
        },
        {
            "type": "opentraces_hook",
            "event": "PostCompact",
            "timestamp": "2026-05-21T09:00:07Z",
            "data": {"trigger": "manual"},
        },
    ])

    record = CodexCliParser().parse_session(session)

    assert record is not None
    assert record.metadata["permission_requests"] == [
        {
            "timestamp": "2026-05-21T09:00:02Z",
            "turn_id": "turn-1",
            "tool": "apply_patch",
            "tool_input": {"path": "src/app.py"},
            "permission_mode": "on-request",
            "source": "codex_cli_hook",
        }
    ]
    assert record.metadata["is_compacted"] is True
    assert [event["event"] for event in record.metadata["compaction_events"]] == [
        "PreCompact",
        "PostCompact",
    ]
    limitation_codes = {item["code"] for item in record.metadata["capture_limitations"]}
    assert "codex_cli_hook_sidecar_missing" not in limitation_codes
    assert record.metadata["normalized_tool_calls"][0]["tool_kind"] == "write"


def test_subagent_source_is_preserved_as_metadata_only(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    session = tmp_path / "rollout-child.jsonl"
    rows = _base_rows(session_id="codex-child", cwd=project, user_text="Handle child task.")
    rows[0]["payload"]["source"] = {
        "subagent": {
            "thread_spawn": {
                "parent_thread_id": "codex-parent",
                "depth": 1,
                "agent_nickname": "ParserWorker",
                "agent_role": "worker",
            }
        }
    }
    rows.append({
        "timestamp": "2026-05-21T09:00:05Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Child done."}],
        },
    })
    rows.append({
        "timestamp": "2026-05-21T09:00:06Z",
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "call_child",
            "arguments": json.dumps({"cmd": "pytest -q", "workdir": str(project)}),
        },
    })
    rows.append({
        "timestamp": "2026-05-21T09:00:07Z",
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": "call_child",
            "output": "passed",
        },
    })
    _write_jsonl(session, rows)

    record = CodexCliParser().parse_session(session)

    assert record is not None
    assert record.metadata["codex_cli"]["source"] == {
        "kind": "subagent",
        "parent_thread_id": "codex-parent",
        "depth": 1,
        "agent_nickname": "ParserWorker",
        "agent_role": "worker",
    }
    assert record.metadata["subagent_session"]["parent_thread_id"] == "codex-parent"
    assert record.metadata["subagent_session"]["capture_limitation"] == (
        "codex_cli_subagent_linkage_metadata_only"
    )
    limitation_codes = {item["code"] for item in record.metadata["capture_limitations"]}
    assert "codex_cli_subagent_linkage_metadata_only" in limitation_codes
