"""Adapter test: Claude Code hook payload → parsed trace → enriched Intent.

Plan 038 phase 2 acceptance: "given a Claude Code hook payload fixture,
the adapter resolves the session file, parses it, and calls the
enrichment module with the expected trace object."
"""

from __future__ import annotations

import json
from pathlib import Path

from opentraces.capture.claude_code.hooks.intent_adapter import (
    enrich_from_hook_payload,
    parse_session_file,
)


def _write_session(tmp_path: Path, lines: list[dict]) -> Path:
    p = tmp_path / "session.jsonl"
    with p.open("w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return p


def _minimal_session_lines() -> list[dict]:
    """Session with a tool_use round-trip so the quality gate lets it through."""
    return [
        {
            "type": "user",
            "sessionId": "sess-adapt",
            "timestamp": "2026-04-12T10:00:00Z",
            "version": "1.0.83",
            "message": {"role": "user", "content": "Add the Intent schema block."},
        },
        {
            "type": "assistant",
            "sessionId": "sess-adapt",
            "timestamp": "2026-04-12T10:00:01Z",
            "message": {
                "role": "assistant",
                "model": "anthropic/claude-sonnet-4-5",
                "content": [
                    {"type": "tool_use", "id": "tc1", "name": "Read", "input": {"file_path": "models.py"}},
                ],
                "usage": {
                    "input_tokens": 50,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        },
        {
            "type": "user",
            "sessionId": "sess-adapt",
            "timestamp": "2026-04-12T10:00:02Z",
            "toolUseResult": {"stdout": "class Task: pass"},
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tc1", "content": "class Task: pass"}],
            },
        },
        {
            "type": "assistant",
            "sessionId": "sess-adapt",
            "timestamp": "2026-04-12T10:00:03Z",
            "message": {
                "role": "assistant",
                "model": "anthropic/claude-sonnet-4-5",
                "content": [{"type": "text", "text": "Added the Intent model beside Task."}],
                "usage": {
                    "input_tokens": 80,
                    "output_tokens": 30,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        },
    ]


class TestAdapter:
    def test_parse_session_file_happy_path(self, tmp_path):
        session = _write_session(tmp_path, _minimal_session_lines())
        trace = parse_session_file(session)
        assert trace is not None
        assert trace.agent.name == "claude-code"

    def test_parse_session_file_missing_returns_none(self, tmp_path):
        assert parse_session_file(tmp_path / "nope.jsonl") is None

    def test_enrich_from_payload_parses_and_calls_model(self, tmp_path):
        session = _write_session(tmp_path, _minimal_session_lines())
        captured: dict[str, str] = {}

        def client(model_id: str, prompt: str) -> str:
            captured["model"] = model_id
            captured["prompt"] = prompt
            return json.dumps({
                "title": "Add Intent schema",
                "summary": "Introduced the session-level Intent block.",
            })

        payload = {"transcript_path": str(session), "session_id": "sess-adapt"}
        trace = enrich_from_hook_payload(payload, client)
        assert trace is not None
        assert trace.intent is not None
        assert trace.intent.title == "Add Intent schema"
        assert trace.intent.source == "llm_hook"
        assert captured["model"] == "anthropic/claude-sonnet-4-5"
        assert "Add the Intent schema block" in captured["prompt"]

    def test_enrich_from_payload_no_transcript_path_returns_none(self):
        def client(m, p):  # pragma: no cover - should not be called
            raise AssertionError("model client should not be invoked")
        assert enrich_from_hook_payload({}, client) is None

    def test_enrich_from_payload_missing_file_returns_none(self, tmp_path):
        def client(m, p):  # pragma: no cover
            raise AssertionError("should not be invoked")
        payload = {"transcript_path": str(tmp_path / "ghost.jsonl")}
        assert enrich_from_hook_payload(payload, client) is None
