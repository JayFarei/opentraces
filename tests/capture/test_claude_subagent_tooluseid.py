"""Regression tests for duplicate Claude subagent descriptions."""

from __future__ import annotations

import json
from pathlib import Path

from opentraces.capture.claude_code.context_tree_capture import (
    JsonlRecord,
    build_parent_graph,
    detect_subagent_forks,
)
from opentraces.capture.claude_code.parse import ClaudeCodeParser


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _child_session(session_id: str, text: str) -> list[dict]:
    return [
        {
            "type": "system",
            "subtype": "init",
            "sessionId": session_id,
            "timestamp": "2026-07-06T10:00:00Z",
        },
        {
            "type": "user",
            "sessionId": session_id,
            "timestamp": "2026-07-06T10:00:01Z",
            "message": {"role": "user", "content": "Run the delegated task."},
        },
        {
            "type": "assistant",
            "sessionId": session_id,
            "timestamp": "2026-07-06T10:00:02Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        },
    ]


def test_parser_matches_duplicate_subagent_descriptions_by_tool_use_id(
    tmp_path: Path,
) -> None:
    description = "Inspect the project"
    main_rows = [
        {
            "type": "user",
            "sessionId": "main-session",
            "timestamp": "2026-07-06T10:00:00Z",
            "version": "1.0.83",
            "message": {"role": "user", "content": "Run two inspections."},
        },
        {
            "type": "assistant",
            "sessionId": "main-session",
            "timestamp": "2026-07-06T10:00:05Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_child_a",
                        "name": "Task",
                        "input": {"description": description},
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        },
        {
            "type": "assistant",
            "sessionId": "main-session",
            "timestamp": "2026-07-06T10:01:05Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_child_b",
                        "name": "Task",
                        "input": {"description": description},
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        },
    ]
    transcript = tmp_path / "main-session.jsonl"
    _write_jsonl(transcript, main_rows)

    subagents = tmp_path / "main-session" / "subagents"
    subagents.mkdir(parents=True)
    for stem, tool_use_id, session_id, text in [
        ("child-a", "toolu_child_a", "child-session-a", "child A completed"),
        ("child-b", "toolu_child_b", "child-session-b", "child B completed"),
    ]:
        (subagents / f"{stem}.meta.json").write_text(
            json.dumps({"description": description, "toolUseId": tool_use_id}),
            encoding="utf-8",
        )
        _write_jsonl(subagents / f"{stem}.jsonl", _child_session(session_id, text))

    record = ClaudeCodeParser().parse_session(transcript)

    assert record is not None
    contents = [step.content for step in record.steps if step.content]
    assert "child A completed" in contents
    assert "child B completed" in contents


def test_context_tree_matches_duplicate_subagent_descriptions_by_tool_use_id(
    tmp_path: Path,
) -> None:
    description = "Inspect the project"
    transcript = tmp_path / "main-session.jsonl"
    subagents = tmp_path / "main-session" / "subagents"
    subagents.mkdir(parents=True)
    for stem, tool_use_id, session_id in [
        ("child-a", "toolu_child_a", "child-session-a"),
        ("child-b", "toolu_child_b", "child-session-b"),
    ]:
        (subagents / f"{stem}.meta.json").write_text(
            json.dumps(
                {
                    "description": description,
                    "session_id": session_id,
                    "toolUseId": tool_use_id,
                }
            ),
            encoding="utf-8",
        )
        _write_jsonl(subagents / f"{stem}.jsonl", _child_session(session_id, "done"))

    records = [
        JsonlRecord(
            offset=0,
            uuid="assistant-a",
            parent_uuid=None,
            record_type="assistant",
            subtype=None,
            raw={
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_child_a",
                            "name": "Task",
                            "input": {"description": description},
                        }
                    ]
                }
            },
        ),
        JsonlRecord(
            offset=1,
            uuid="assistant-b",
            parent_uuid="assistant-a",
            record_type="assistant",
            subtype=None,
            raw={
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_child_b",
                            "name": "Task",
                            "input": {"description": description},
                        }
                    ]
                }
            },
        ),
    ]
    graph = build_parent_graph(records)

    forks = detect_subagent_forks(graph, transcript)

    assert [fork.subagent_session_id for fork in forks] == [
        "child-session-a",
        "child-session-b",
    ]
