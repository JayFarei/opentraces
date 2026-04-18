"""Parser handling of away_summary recaps.

Claude Code emits ``{type: system, subtype: away_summary}`` records when the
user returns from a long churn ("✻ Churned for 1m 20s / ※ recap: …"). The
``content`` field is a mid-session self-summary of current intent and next
action. High-signal for blame/story surfaces that need to explain *why*
a commit happened, not just *what* changed.

These recaps must be captured verbatim onto ``metadata.away_summaries`` in
chronological order. They must not leak into user steps (they are not user
turns) and must coexist with the existing ``post_turn_summary`` capture.
"""

from __future__ import annotations

import json
from pathlib import Path

from opentraces.capture.claude_code.parse import ClaudeCodeParser


def _write(tmp_path: Path, lines: list[dict], name: str = "recap.jsonl") -> Path:
    p = tmp_path / name
    with p.open("w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return p


def _user_turn(ts: str, text: str) -> dict:
    return {
        "type": "user",
        "sessionId": "recap-sess",
        "timestamp": ts,
        "message": {"role": "user", "content": text},
    }


def _assistant_tool_turn(ts: str, tool_id: str) -> dict:
    return {
        "type": "assistant",
        "sessionId": "recap-sess",
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tool_id, "name": "Read",
                 "input": {"file_path": "x.py"}},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 10},
        },
    }


def _tool_result(ts: str, tool_id: str) -> dict:
    return {
        "type": "user",
        "sessionId": "recap-sess",
        "timestamp": ts,
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": "ok"},
            ],
        },
    }


def _away_summary(ts: str, content: str) -> dict:
    """The line Claude Code writes after a long churn."""
    return {
        "type": "system",
        "subtype": "away_summary",
        "sessionId": "recap-sess",
        "timestamp": ts,
        "content": content,
        "uuid": f"away-{ts}",
    }


def test_parser_captures_away_summaries_in_order(tmp_path: Path) -> None:
    """Two recaps in the session land on metadata.away_summaries in order."""
    lines = [
        _user_turn("2026-04-17T17:00:00Z", "fix the init flow"),
        _assistant_tool_turn("2026-04-17T17:00:01Z", "tu_1"),
        _tool_result("2026-04-17T17:00:02Z", "tu_1"),
        _away_summary(
            "2026-04-17T17:04:10Z",
            "Fixing the v0.3 init flow so users can attach to an existing "
            "HuggingFace dataset, not just create-or-skip.",
        ),
        _assistant_tool_turn("2026-04-17T17:05:00Z", "tu_2"),
        _tool_result("2026-04-17T17:05:01Z", "tu_2"),
        _away_summary(
            "2026-04-17T17:25:25Z",
            "We're fixing 0.3.1 release bugs; the HF dataset selector fix is "
            "committed locally as d02ccdf9 but not pushed.",
        ),
        _assistant_tool_turn("2026-04-17T17:30:00Z", "tu_3"),
        _tool_result("2026-04-17T17:30:01Z", "tu_3"),
    ]
    session_path = _write(tmp_path, lines)

    record = ClaudeCodeParser().parse_session(session_path)
    assert record is not None

    recaps = record.metadata.get("away_summaries")
    assert recaps is not None, "away_summaries must be present when recaps exist"
    assert len(recaps) == 2, f"expected 2 recaps, got {len(recaps)}"

    assert "init flow" in recaps[0]["content"]
    assert recaps[0]["timestamp"] == "2026-04-17T17:04:10Z"
    assert "d02ccdf9" in recaps[1]["content"]
    assert recaps[1]["timestamp"] == "2026-04-17T17:25:25Z"


def test_away_summary_does_not_become_a_user_step(tmp_path: Path) -> None:
    """The recap is a system record, not user input. It must not pollute steps."""
    lines = [
        _user_turn("2026-04-17T17:00:00Z", "real user prompt"),
        _assistant_tool_turn("2026-04-17T17:00:01Z", "tu_1"),
        _tool_result("2026-04-17T17:00:02Z", "tu_1"),
        _away_summary("2026-04-17T17:04:10Z", "recap content that must stay out of steps"),
        _user_turn("2026-04-17T17:05:00Z", "follow-up prompt"),
        _assistant_tool_turn("2026-04-17T17:05:01Z", "tu_2"),
        _tool_result("2026-04-17T17:05:02Z", "tu_2"),
    ]
    session_path = _write(tmp_path, lines)

    record = ClaudeCodeParser().parse_session(session_path)
    assert record is not None

    user_texts = [step.content or "" for step in record.steps if step.role == "user"]
    assert any("real user prompt" in t for t in user_texts)
    assert any("follow-up prompt" in t for t in user_texts)
    assert not any("recap content" in t for t in user_texts), (
        "away_summary is a system record, not a user turn"
    )


def test_no_away_summary_key_when_absent(tmp_path: Path) -> None:
    """Sessions with no recaps must not materialize an empty key in metadata."""
    lines = [
        _user_turn("2026-04-17T17:00:00Z", "quick task"),
        _assistant_tool_turn("2026-04-17T17:00:01Z", "tu_1"),
        _tool_result("2026-04-17T17:00:02Z", "tu_1"),
    ]
    session_path = _write(tmp_path, lines)

    record = ClaudeCodeParser().parse_session(session_path)
    assert record is not None
    assert "away_summaries" not in record.metadata, (
        "empty list should not be carried; key should be absent when no recaps"
    )


def test_away_summary_without_content_is_skipped(tmp_path: Path) -> None:
    """Malformed recap records (no content) must not crash or pollute metadata."""
    lines = [
        _user_turn("2026-04-17T17:00:00Z", "task"),
        _assistant_tool_turn("2026-04-17T17:00:01Z", "tu_1"),
        _tool_result("2026-04-17T17:00:02Z", "tu_1"),
        {  # missing "content" field
            "type": "system",
            "subtype": "away_summary",
            "sessionId": "recap-sess",
            "timestamp": "2026-04-17T17:04:10Z",
        },
        _away_summary("2026-04-17T17:10:00Z", "valid recap"),
    ]
    session_path = _write(tmp_path, lines)

    record = ClaudeCodeParser().parse_session(session_path)
    assert record is not None

    recaps = record.metadata.get("away_summaries") or []
    assert len(recaps) == 1, "only the recap with content should be kept"
    assert recaps[0]["content"] == "valid recap"
