from __future__ import annotations

import json
from pathlib import Path

from opentraces.capture.pi.parse import PiSessionParser
from opentraces.capture.pi.sessions import cwd_slug, discover_session_files, sessions_root
from opentraces_schema import TraceRecord

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "pi" / "linear-session.jsonl"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def test_discover_sessions_uses_pi_layout(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    root = tmp_path / ".pi" / "agent" / "sessions"
    good = root / cwd_slug(project) / "20260602_session.jsonl"
    bad = root / "notes.jsonl"
    _write_jsonl(good, [{"type": "session", "id": "pi-good", "cwd": str(project)}])
    _write_jsonl(bad, [{"type": "session", "id": "pi-bad", "cwd": str(project)}])

    assert sessions_root(home=tmp_path) == root
    assert list(discover_session_files(home=tmp_path)) == [good]
    assert list(PiSessionParser().discover_sessions(root)) == [good]


def test_project_discovery_uses_recorded_cwd_and_session_id(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    project = tmp_path / "repo"
    other = tmp_path / "other"
    project.mkdir()
    other.mkdir()
    root = home / ".pi" / "agent" / "sessions"
    good = root / cwd_slug(project) / "20260602_good.jsonl"
    other_file = root / cwd_slug(other) / "20260602_other.jsonl"
    _write_jsonl(good, [{"type": "session", "id": "pi-good", "cwd": str(project)}])
    _write_jsonl(other_file, [{"type": "session", "id": "pi-other", "cwd": str(other)}])
    monkeypatch.setenv("HOME", str(home))

    parser = PiSessionParser()

    assert list(parser.discover_project_sessions(project)) == [good]
    assert parser.session_id_from_path(good) == "pi-good"


def test_fixture_parse_emits_valid_trace_record() -> None:
    record = PiSessionParser().parse_session(FIXTURE)

    assert record is not None
    assert isinstance(record, TraceRecord)
    assert TraceRecord.model_validate(record.model_dump()) == record
    assert record.agent.name == "pi"
    assert record.agent.version == "1.0.0"
    assert record.agent.model == "anthropic/claude-sonnet-4"
    assert record.session_id == "pi-linear-session"
    assert record.task.description == "Add a farewell helper."
    assert record.metrics.total_input_tokens == 220
    assert record.metrics.total_output_tokens == 50
    assert record.metrics.total_cache_read_tokens == 5
    assert record.metrics.total_cache_creation_tokens == 0
    assert record.metrics.cache_hit_rate == 0.0222


def test_fixture_steps_pair_tool_calls_and_outputs() -> None:
    record = PiSessionParser().parse_session(FIXTURE)

    assert record is not None
    assert [step.role for step in record.steps] == ["user", "agent", "agent"]
    tool_step = record.steps[1]
    assert tool_step.content == "I'll edit src/app.py."
    assert len(tool_step.tool_calls) == 1
    assert tool_step.tool_calls[0].tool_name == "Edit"
    assert tool_step.tool_calls[0].tool_call_id == "call-edit-1"
    assert tool_step.observations[0].source_call_id == "call-edit-1"
    assert "updated" in (tool_step.observations[0].content or "")


def test_parser_merges_pi_sidecars_for_hook_metadata_and_provider_context(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    session = tmp_path / "session.jsonl"
    rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    rows[0]["cwd"] = str(project)
    _write_jsonl(session, rows)
    sidecar = project / ".opentraces" / "pi" / "events" / "pi-linear-session.jsonl"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("\n".join(json.dumps(row) for row in [
        {
            "schema_version": "opentraces.pi.sidecar.v1",
            "type": "opentraces_pi_event",
            "event": "active_leaf",
            "event_id": "leaf",
            "sequence": 1,
            "session_id": "pi-linear-session",
            "session_file": str(session),
            "cwd": str(project),
            "leaf_id": "a2",
            "timestamp": "2026-06-02T10:00:01Z",
            "final": False,
            "data": {},
        },
        {
            "schema_version": "opentraces.pi.sidecar.v1",
            "type": "opentraces_pi_event",
            "event": "provider_request",
            "event_id": "provider",
            "sequence": 2,
            "session_id": "pi-linear-session",
            "session_file": str(session),
            "cwd": str(project),
            "timestamp": "2026-06-02T10:00:02Z",
            "final": False,
            "data": {"system": "system prompt", "messages": [{"role": "user", "content": "Add"}], "tools": [{"name": "Edit", "input_schema": {"type": "object"}}]},
        },
        {
            "schema_version": "opentraces.pi.sidecar.v1",
            "type": "opentraces_pi_event",
            "event": "tree",
            "event_id": "tree",
            "sequence": 3,
            "session_id": "pi-linear-session",
            "session_file": str(session),
            "cwd": str(project),
            "timestamp": "2026-06-02T10:00:02.500Z",
            "final": False,
            "data": {"old_leaf_id": "a1", "new_leaf_id": "a2"},
        },
        {
            "schema_version": "opentraces.pi.sidecar.v1",
            "type": "opentraces_pi_event",
            "event": "tool_pre",
            "event_id": "pre",
            "sequence": 4,
            "session_id": "pi-linear-session",
            "session_file": str(session),
            "cwd": str(project),
            "tool_call_id": "call-edit-1",
            "timestamp": "2026-06-02T10:00:03Z",
            "final": False,
            "data": {"tool_name": "Edit", "input": {"file_path": "src/app.py"}},
        },
    ]) + "\n")

    record = PiSessionParser().parse_session(session)

    assert record is not None
    assert record.metadata["hook_pre_tool_use"]["call-edit-1"]["tool"] == "Edit"
    assert record.metadata["pi"]["active_leaf_id"] == "a2"
    assert record.metadata["pi"]["active_leaf_source"] == "sidecar:tree"
    assert record.metadata["pi"]["tree_events"][0]["old_leaf_id"] == "a1"
    assert record.metadata["pi"]["provider_contexts"][0]["capture_method"] == "live_capture"
    assert record.metadata["pi"]["tool_registry"][0]["name"] == "Edit"


def test_parser_normalizes_pi_tool_kinds_and_user_bash(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    session = tmp_path / "tool-kinds.jsonl"
    tool_blocks = [
        {"type": "toolCall", "id": "call-bash", "name": "bash", "arguments": {"command": "pytest -q"}},
        {"type": "toolCall", "id": "call-read", "name": "read", "arguments": {"path": "src/app.py"}},
        {"type": "toolCall", "id": "call-grep", "name": "grep", "arguments": {"pattern": "farewell"}},
        {"type": "toolCall", "id": "call-edit", "name": "edit", "arguments": {"path": "src/app.py"}},
        {"type": "toolCall", "id": "call-mcp", "name": "mcp_tool", "arguments": {"server": "github"}},
        {"type": "toolCall", "id": "call-task", "name": "task", "arguments": {"description": "delegate"}},
        {"type": "toolCall", "id": "call-ot", "name": "ot_search", "arguments": {"query": "recent"}},
        {"type": "toolCall", "id": "call-custom", "name": "custom_widget", "arguments": {"value": 1}},
    ]
    rows = [
        {"type": "session", "version": 3, "id": "pi-tool-session", "timestamp": "2026-06-02T10:00:00Z", "cwd": str(project)},
        {"type": "message", "id": "u1", "parentId": None, "timestamp": "2026-06-02T10:00:01Z", "message": {"role": "user", "content": "Exercise all Pi tool kinds."}},
        {"type": "message", "id": "a1", "parentId": "u1", "timestamp": "2026-06-02T10:00:02Z", "message": {"role": "assistant", "provider": "openai-codex", "model": "gpt-5.5", "content": tool_blocks}},
        {"type": "message", "id": "tb", "parentId": "a1", "timestamp": "2026-06-02T10:00:03Z", "message": {"role": "toolResult", "toolCallId": "call-bash", "content": [{"type": "text", "text": "tests passed"}], "isError": False}},
        {"type": "bashExecution", "id": "ub1", "parentId": "tb", "timestamp": "2026-06-02T10:00:04Z", "command": "git status --short", "output": "", "exitCode": 0},
        {"type": "message", "id": "a2", "parentId": "ub1", "timestamp": "2026-06-02T10:00:05Z", "message": {"role": "assistant", "provider": "openai-codex", "model": "gpt-5.5", "content": [{"type": "text", "text": "Done."}]}},
    ]
    _write_jsonl(session, rows)

    record = PiSessionParser().parse_session(session)

    assert record is not None
    by_id = {row["tool_call_id"]: row for row in record.metadata["normalized_tool_calls"]}
    assert by_id["call-bash"]["tool_kind"] == "shell"
    assert by_id["call-bash"]["shell"]["command"] == "pytest -q"
    assert by_id["call-read"]["tool_kind"] == "read"
    assert by_id["call-grep"]["tool_kind"] == "search"
    assert by_id["call-edit"]["tool_kind"] == "write"
    assert by_id["call-mcp"]["tool_kind"] == "mcp"
    assert by_id["call-task"]["tool_kind"] == "subagent"
    assert by_id["call-ot"]["tool_kind"] == "opentraces_retrieval"
    assert by_id["call-ot"]["opentraces_internal_readonly"] is True
    assert by_id["call-custom"]["tool_kind"] == "tool"
    assert any(step.content == "! git status --short" for step in record.steps)
    bash_step = next(step for step in record.steps if step.content == "! git status --short")
    assert bash_step.observations[0].source_call_id.startswith("pi-user-bash:")


def test_parser_maps_pi_cache_write_and_actual_cost(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    session = tmp_path / "cost-session.jsonl"
    rows = [
        {"type": "session", "version": 3, "id": "pi-cost-session", "timestamp": "2026-06-02T10:00:00Z", "cwd": str(project)},
        {"type": "message", "id": "u1", "parentId": None, "timestamp": "2026-06-02T10:00:01Z", "message": {"role": "user", "content": "Report cost."}},
        {
            "type": "message",
            "id": "a1",
            "parentId": "u1",
            "timestamp": "2026-06-02T10:00:02Z",
            "message": {
                "role": "assistant",
                "provider": "anthropic",
                "model": "claude-sonnet-4",
                "usage": {
                    "input": 100,
                    "output": 20,
                    "cacheRead": 25,
                    "cacheWrite": 10,
                    "cost": {"input": 0.01, "output": 0.02, "cacheRead": 0.003, "cacheWrite": 0.004, "total": 0.037},
                },
                "content": [
                    {"type": "text", "text": "I'll inspect the file."},
                    {"type": "toolCall", "id": "call-read", "name": "Read", "arguments": {"file_path": "src/app.py"}},
                ],
            },
        },
        {"type": "message", "id": "t1", "parentId": "a1", "timestamp": "2026-06-02T10:00:03Z", "message": {"role": "toolResult", "toolCallId": "call-read", "toolName": "Read", "content": [{"type": "text", "text": "print('ok')"}], "isError": False}},
        {"type": "message", "id": "a2", "parentId": "t1", "timestamp": "2026-06-02T10:00:04Z", "message": {"role": "assistant", "provider": "anthropic", "model": "claude-sonnet-4", "content": [{"type": "text", "text": "Done."}]}},
    ]
    _write_jsonl(session, rows)

    record = PiSessionParser().parse_session(session)

    assert record is not None
    assert record.metrics.total_input_tokens == 100
    assert record.metrics.total_output_tokens == 20
    assert record.metrics.total_cache_read_tokens == 25
    assert record.metrics.total_cache_creation_tokens == 10
    assert record.metrics.cache_hit_rate == 0.2
    assert record.metrics.estimated_cost_usd == 0.037
