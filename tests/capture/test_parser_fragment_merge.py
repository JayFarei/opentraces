"""Coalescing of multi-fragment assistant responses.

Claude Code writes some assistant turns as multiple JSONL rows (one per
content-block type) that share a single ``requestId`` and carry the same
``message.usage`` block. The parser must merge those rows into one Step
so downstream metrics aren't double-counted. See the evidence doc at
/tmp/opentraces-parser-bug-evidence.md and the Codex review verdict.
"""

from __future__ import annotations

import json
from pathlib import Path

from opentraces.capture.claude_code.parse import ClaudeCodeParser


def _write(tmp: Path, lines: list[dict]) -> Path:
    p = tmp / "session.jsonl"
    with open(p, "w") as f:
        for ln in lines:
            f.write(json.dumps(ln) + "\n")
    return p


# A shared usage block — all fragments of a single API call carry
# identical usage (byte-verified against ~/.claude/projects transcripts).
_USAGE = {
    "input_tokens": 6,
    "output_tokens": 180,
    "cache_read_input_tokens": 16_979,
    "cache_creation_input_tokens": 32_622,
}

_USER_OPENER = {
    "type": "user",
    "sessionId": "sess-1",
    "timestamp": "2026-04-16T15:41:48.000Z",
    "version": "1.0.83",
    "message": {"role": "user", "content": "Do the thing."},
}


def _assistant(uuid: str, rid: str, blocks: list[dict], ts: str) -> dict:
    return {
        "type": "assistant",
        "sessionId": "sess-1",
        "uuid": uuid,
        "requestId": rid,
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "content": blocks,
            "usage": _USAGE,
            "stop_reason": "tool_use",
        },
    }


def _tool_result(uuid: str, tc_id: str, ts: str, out: str) -> dict:
    return {
        "type": "user",
        "sessionId": "sess-1",
        "uuid": uuid,
        "timestamp": ts,
        "toolUseResult": {"stdout": out},
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tc_id, "content": out}],
        },
    }


class TestCoalesceAssistantFragments:
    """Direct tests of the _coalesce_assistant_fragments helper."""

    def test_thinking_and_tool_use_fragments_merged(self):
        parser = ClaudeCodeParser()
        lines = [
            _assistant(
                "a1", "req_1",
                [{"type": "thinking", "thinking": "Let me check first."}],
                "2026-04-16T15:41:49.312Z",
            ),
            _assistant(
                "a2", "req_1",
                [{"type": "tool_use", "id": "tu_1", "name": "Bash",
                  "input": {"command": "ls"}}],
                "2026-04-16T15:41:50.275Z",
            ),
        ]
        out = parser._coalesce_assistant_fragments(lines)
        assert len(out) == 1
        blocks = out[0]["message"]["content"]
        assert [b["type"] for b in blocks] == ["thinking", "tool_use"]
        assert out[0]["uuid"] == "a1"  # first fragment's anchor survives
        assert out[0]["timestamp"] == "2026-04-16T15:41:49.312Z"

    def test_text_then_tool_use_also_merged(self):
        parser = ClaudeCodeParser()
        lines = [
            _assistant("a1", "req_2",
                       [{"type": "text", "text": "Reading file now."}],
                       "2026-04-16T15:41:49.312Z"),
            _assistant("a2", "req_2",
                       [{"type": "tool_use", "id": "tu_1", "name": "Read",
                         "input": {"file_path": "foo.py"}}],
                       "2026-04-16T15:41:50.275Z"),
        ]
        out = parser._coalesce_assistant_fragments(lines)
        assert len(out) == 1
        blocks = out[0]["message"]["content"]
        assert [b["type"] for b in blocks] == ["text", "tool_use"]

    def test_three_fragment_thinking_text_tool_use(self):
        parser = ClaudeCodeParser()
        lines = [
            _assistant("a1", "req_3",
                       [{"type": "thinking", "thinking": "plan"}],
                       "2026-04-16T15:41:49.000Z"),
            _assistant("a2", "req_3",
                       [{"type": "text", "text": "Now I'll run ls."}],
                       "2026-04-16T15:41:49.500Z"),
            _assistant("a3", "req_3",
                       [{"type": "tool_use", "id": "tu_1", "name": "Bash",
                         "input": {"command": "ls"}}],
                       "2026-04-16T15:41:50.000Z"),
        ]
        out = parser._coalesce_assistant_fragments(lines)
        assert len(out) == 1
        blocks = out[0]["message"]["content"]
        assert [b["type"] for b in blocks] == ["thinking", "text", "tool_use"]

    def test_thinking_then_text_only_merged(self):
        parser = ClaudeCodeParser()
        lines = [
            _assistant("a1", "req_4",
                       [{"type": "thinking", "thinking": "Deciding."}],
                       "2026-04-16T15:41:49.000Z"),
            _assistant("a2", "req_4",
                       [{"type": "text", "text": "Here's my answer."}],
                       "2026-04-16T15:41:49.500Z"),
        ]
        out = parser._coalesce_assistant_fragments(lines)
        assert len(out) == 1
        blocks = out[0]["message"]["content"]
        assert [b["type"] for b in blocks] == ["thinking", "text"]

    def test_interleaved_tool_result_does_not_block_merge(self):
        """Fragments can be separated by user tool_result rows — merge by
        requestId, not adjacency (Codex review finding)."""
        parser = ClaudeCodeParser()
        lines = [
            _assistant("a1", "req_5",
                       [{"type": "thinking", "thinking": "Consider earlier tool result."}],
                       "2026-04-16T15:41:49.000Z"),
            _tool_result("u1", "tu_prior", "2026-04-16T15:41:49.500Z", "prior output"),
            _assistant("a2", "req_5",
                       [{"type": "tool_use", "id": "tu_next", "name": "Bash",
                         "input": {"command": "echo next"}}],
                       "2026-04-16T15:41:50.000Z"),
        ]
        out = parser._coalesce_assistant_fragments(lines)
        # 1 merged assistant + 1 user tool_result = 2 rows
        assert len(out) == 2
        assistant_row = next(r for r in out if r["type"] == "assistant")
        blocks = assistant_row["message"]["content"]
        assert [b["type"] for b in blocks] == ["thinking", "tool_use"]
        # Tool_result row preserved
        assert any(r["type"] == "user" for r in out)

    def test_missing_requestId_leaves_lines_untouched(self):
        """Legacy CC captures lacking requestId — fall back to current
        behavior (one step per assistant line)."""
        parser = ClaudeCodeParser()
        lines = [
            {
                "type": "assistant",
                "sessionId": "sess-1",
                "uuid": "a1",
                # no requestId
                "timestamp": "2026-04-16T15:41:49.000Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "thinking", "thinking": "x"}],
                    "usage": _USAGE,
                },
            },
            {
                "type": "assistant",
                "sessionId": "sess-1",
                "uuid": "a2",
                # no requestId
                "timestamp": "2026-04-16T15:41:49.500Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {"command": "ls"}}],
                    "usage": _USAGE,
                },
            },
        ]
        out = parser._coalesce_assistant_fragments(lines)
        # No merging; both rows preserved as-is.
        assert len(out) == 2

    def test_single_fragment_request_unchanged(self):
        parser = ClaudeCodeParser()
        lines = [
            _assistant("a1", "req_solo",
                       [{"type": "tool_use", "id": "tu_1", "name": "Bash",
                         "input": {"command": "echo hi"}}],
                       "2026-04-16T15:41:49.000Z"),
        ]
        out = parser._coalesce_assistant_fragments(lines)
        assert len(out) == 1
        assert out[0] is lines[0]  # untouched (no group collision)

    def test_input_dict_not_mutated(self):
        """Merging must be non-destructive on caller-provided dicts."""
        parser = ClaudeCodeParser()
        original_first = {
            "type": "assistant",
            "sessionId": "sess-1",
            "uuid": "a1",
            "requestId": "req_np",
            "timestamp": "t0",
            "message": {"role": "assistant",
                        "content": [{"type": "thinking", "thinking": "x"}],
                        "usage": _USAGE},
        }
        original_second = _assistant(
            "a2", "req_np",
            [{"type": "tool_use", "id": "tu_1", "name": "Bash",
              "input": {"command": "ls"}}],
            "t1",
        )
        lines = [original_first, original_second]
        # deep copy snapshot for comparison
        snapshot_first = json.loads(json.dumps(original_first))
        snapshot_second = json.loads(json.dumps(original_second))

        out = parser._coalesce_assistant_fragments(lines)
        # output is merged, but the original dicts should be unchanged
        assert original_first == snapshot_first
        assert original_second == snapshot_second
        # out[0] is a fresh dict
        assert out[0] is not original_first
        assert out[0]["message"]["content"] == [
            {"type": "thinking", "thinking": "x"},
            {"type": "tool_use", "id": "tu_1", "name": "Bash",
             "input": {"command": "ls"}},
        ]


class TestParseSessionEndToEnd:
    """Full parse pass, verifying the fix takes effect via the public API."""

    def test_thinking_plus_tool_use_produces_one_step(self, tmp_path: Path):
        lines = [
            _USER_OPENER,
            _assistant("a1", "req_A",
                       [{"type": "thinking", "thinking": "I'll list files."}],
                       "2026-04-16T15:41:49.312Z"),
            _assistant("a2", "req_A",
                       [{"type": "tool_use", "id": "tu_1", "name": "Bash",
                         "input": {"command": "ls"}}],
                       "2026-04-16T15:41:50.275Z"),
            _tool_result("u_tr", "tu_1", "2026-04-16T15:41:51.000Z", "foo.py\nbar.py"),
        ]
        session = _write(tmp_path, lines)
        parser = ClaudeCodeParser()
        record = parser.parse_session(session)

        assert record is not None
        # user + one merged assistant step (not two)
        agent_steps = [s for s in record.steps if s.role == "agent"]
        assert len(agent_steps) == 1, (
            f"expected one merged agent step, got {len(agent_steps)}"
        )

        step = agent_steps[0]
        # Both reasoning_content and tool_calls survived the merge
        assert step.reasoning_content is not None
        assert "list files" in step.reasoning_content
        assert len(step.tool_calls) == 1
        assert step.tool_calls[0].tool_name == "Bash"
        # And the observation was linked via the tool_result
        assert len(step.observations) == 1
        # Usage attached ONCE — not double-counted
        assert step.token_usage.input_tokens == 6
        assert step.token_usage.output_tokens == 180
        assert step.token_usage.cache_read_tokens == 16_979
        assert step.token_usage.cache_write_tokens == 32_622

    def test_metrics_not_double_counted(self, tmp_path: Path):
        """Before the fix, a 2-fragment turn was emitted as 2 Steps carrying
        identical usage. Post-fix totals should match a single turn's
        usage exactly."""
        lines = [
            _USER_OPENER,
            _assistant("a1", "req_X",
                       [{"type": "thinking", "thinking": "hmm"}],
                       "2026-04-16T15:41:49.312Z"),
            _assistant("a2", "req_X",
                       [{"type": "tool_use", "id": "tu_1", "name": "Read",
                         "input": {"file_path": "foo.py"}}],
                       "2026-04-16T15:41:50.275Z"),
            _tool_result("u_tr", "tu_1", "2026-04-16T15:41:51.000Z", "print('hi')"),
        ]
        session = _write(tmp_path, lines)
        parser = ClaudeCodeParser()
        record = parser.parse_session(session)
        assert record is not None

        m = record.metrics
        assert m.total_input_tokens == _USAGE["input_tokens"]
        assert m.total_output_tokens == _USAGE["output_tokens"]
        assert m.total_cache_read_tokens == _USAGE["cache_read_input_tokens"]
        assert m.total_cache_creation_tokens == _USAGE["cache_creation_input_tokens"]

    def test_anchor_line_no_references_first_fragment(self, tmp_path: Path):
        """After coalescing, the anchor for the merged step should point at
        the first fragment's original 0-indexed line position in the raw
        JSONL — not at the coalesced list position."""
        lines = [
            _USER_OPENER,  # file line 0
            _assistant("a1", "req_L",
                       [{"type": "thinking", "thinking": "x"}],
                       "2026-04-16T15:41:49.312Z"),  # file line 1 — first fragment
            _assistant("a2", "req_L",
                       [{"type": "tool_use", "id": "tu_1", "name": "Bash",
                         "input": {"command": "ls"}}],
                       "2026-04-16T15:41:50.275Z"),  # file line 2 — dropped
            _tool_result("u_tr", "tu_1", "2026-04-16T15:41:51.000Z", "ok"),  # file line 3
        ]
        session = _write(tmp_path, lines)
        parser = ClaudeCodeParser()
        record = parser.parse_session(session)
        assert record is not None

        # step_anchors is {step_index (1-based): {entry_uuid, line_no, session_file_relpath}}
        # The merged agent step should anchor at the first fragment's line (1).
        anchors = parser.step_anchors
        # Find the agent step's index — it's the one whose entry_uuid is 'a1'
        agent_anchor = next(
            (a for a in anchors.values() if a["entry_uuid"] == "a1"), None
        )
        assert agent_anchor is not None, "agent step anchor missing"
        assert agent_anchor["line_no"] == 1

    def test_multiple_requests_in_session_preserved_individually(self, tmp_path: Path):
        """Two separate API calls — each 2-fragment — should collapse to
        2 agent steps, not 4."""
        lines = [
            _USER_OPENER,
            # request A — 2 fragments
            _assistant("a1", "req_A",
                       [{"type": "thinking", "thinking": "A1"}],
                       "2026-04-16T15:41:49.000Z"),
            _assistant("a2", "req_A",
                       [{"type": "tool_use", "id": "tu_A", "name": "Bash",
                         "input": {"command": "ls"}}],
                       "2026-04-16T15:41:49.500Z"),
            _tool_result("u_A", "tu_A", "2026-04-16T15:41:50.000Z", "ok"),
            # request B — 2 fragments
            _assistant("b1", "req_B",
                       [{"type": "thinking", "thinking": "B1"}],
                       "2026-04-16T15:41:51.000Z"),
            _assistant("b2", "req_B",
                       [{"type": "tool_use", "id": "tu_B", "name": "Bash",
                         "input": {"command": "cat foo"}}],
                       "2026-04-16T15:41:51.500Z"),
            _tool_result("u_B", "tu_B", "2026-04-16T15:41:52.000Z", "hi"),
        ]
        session = _write(tmp_path, lines)
        parser = ClaudeCodeParser()
        record = parser.parse_session(session)
        assert record is not None

        agent_steps = [s for s in record.steps if s.role == "agent"]
        assert len(agent_steps) == 2, (
            f"expected two merged agent steps, got {len(agent_steps)}"
        )
        assert "A1" in (agent_steps[0].reasoning_content or "")
        assert "B1" in (agent_steps[1].reasoning_content or "")
