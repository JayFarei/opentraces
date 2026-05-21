"""Tests for Codex CLI rollout parser."""

from __future__ import annotations

import json
from pathlib import Path

from opentraces.capture.codex_cli.parse import CodexCliParser
from opentraces.capture.codex_cli.sessions import discover_session_files, sessions_root
from opentraces_schema import TraceRecord


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "codex_cli" / "linear-rollout.jsonl"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def test_discover_sessions_uses_codex_rollout_layout(tmp_path: Path) -> None:
    session_dir = tmp_path / ".codex" / "sessions" / "2026" / "05" / "21"
    good = session_dir / "rollout-2026-05-21T09-00-00-fixture.jsonl"
    bad = session_dir / "notes.jsonl"
    _write_jsonl(good, [{"type": "session_meta", "payload": {}}])
    _write_jsonl(bad, [{"type": "session_meta", "payload": {}}])

    assert sessions_root(home=tmp_path) == tmp_path / ".codex" / "sessions"
    assert list(discover_session_files(home=tmp_path)) == [good]
    assert list(CodexCliParser().discover_sessions(tmp_path / ".codex" / "sessions")) == [good]


def test_project_discovery_uses_recorded_cwd_and_native_session_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    other_project = tmp_path / "other"
    project.mkdir()
    other_project.mkdir()
    session_dir = home / ".codex" / "sessions" / "2026" / "05" / "21"
    good = session_dir / "rollout-2026-05-21T09-00-00-good.jsonl"
    other = session_dir / "rollout-2026-05-21T09-00-01-other.jsonl"
    _write_jsonl(good, [{"type": "session_meta", "payload": {"id": "codex-good", "cwd": str(project)}}])
    _write_jsonl(other, [{"type": "session_meta", "payload": {"id": "codex-other", "cwd": str(other_project)}}])
    monkeypatch.setenv("HOME", str(home))

    parser = CodexCliParser()

    assert list(parser.discover_project_sessions(project)) == [good]
    assert parser.session_id_from_path(good) == "codex-good"


def test_fixture_parse_emits_valid_trace_record() -> None:
    record = CodexCliParser().parse_session(FIXTURE)

    assert record is not None
    assert isinstance(record, TraceRecord)
    assert TraceRecord.model_validate(record.model_dump()) == record
    assert record.agent.name == "codex-cli"
    assert record.agent.version == "0.132.0"
    assert record.agent.model == "openai/gpt-5.5"
    assert record.session_id == "019e0000-0000-7000-8000-000000000001"
    assert record.task.description == "Add a small parser test for Codex CLI."
    assert record.metrics.total_input_tokens == 1000
    assert record.metrics.total_output_tokens == 120
    assert record.environment.vcs.type == "git"
    assert record.environment.vcs.branch == "main"


def test_fixture_steps_pair_function_calls_and_outputs() -> None:
    record = CodexCliParser().parse_session(FIXTURE)

    assert record is not None
    assert [step.role for step in record.steps] == ["user", "agent", "agent"]
    assert record.steps[0].content == "Add a small parser test for Codex CLI."

    tool_step = record.steps[1]
    assert tool_step.content == "I will inspect the fixture and run the focused parser test."
    assert tool_step.reasoning_content == "[redacted: Codex CLI recorded encrypted reasoning content]"
    assert len(tool_step.tool_calls) == 1
    assert tool_step.tool_calls[0].tool_name == "exec_command"
    assert tool_step.tool_calls[0].tool_call_id == "call_fixture_001"
    assert tool_step.tool_calls[0].input == {
        "cmd": "pytest tests/capture/test_parser_codex_cli.py -q",
        "workdir": "/workspace/demo",
    }
    assert len(tool_step.observations) == 1
    assert tool_step.observations[0].source_call_id == "call_fixture_001"
    assert "PASSED" in (tool_step.observations[0].content or "")

    assert record.steps[2].content == "Parser test is in place and passes."


def test_fixture_metadata_is_compact_and_records_limitations() -> None:
    record = CodexCliParser().parse_session(FIXTURE)

    assert record is not None
    metadata = record.metadata
    assert metadata["raw_event_counts"] == {
        "session_meta": 1,
        "response_item": 8,
        "turn_context": 1,
        "event_msg": 2,
    }
    assert metadata["response_item_counts"] == {
        "message": 5,
        "reasoning": 1,
        "function_call": 1,
        "function_call_output": 1,
    }
    assert metadata["function_names"] == {"exec_command": 1}
    assert metadata["codex_cli"]["originator"] == "codex-tui"
    assert metadata["codex_cli"]["source"] == "tui"
    assert metadata["codex_cli"]["agent_nickname"] == "Codex"
    assert metadata["codex_cli"]["agent_role"] == "worker"
    assert metadata["codex_cli"]["base_instructions"]["chars"] == len("Fixture system instructions.")
    assert "Fixture system instructions." not in json.dumps(metadata)
    assert metadata["normalized_tool_calls"] == [
        {
            "step_index": 2,
            "tool_call_id": "call_fixture_001",
            "raw_tool_name": "exec_command",
            "tool_kind": "shell",
            "shell": {
                "command": "pytest tests/capture/test_parser_codex_cli.py -q",
                "cwd": "/workspace/demo",
            },
        }
    ]

    limitation_codes = {item["code"] for item in metadata["capture_limitations"]}
    assert "codex_cli_parser_minimal_sp2" in limitation_codes
    assert "codex_cli_context_tree_session_level_projection" in limitation_codes
    assert "codex_cli_hook_sidecar_missing" in limitation_codes
