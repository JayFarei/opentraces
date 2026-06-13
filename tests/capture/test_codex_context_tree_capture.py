from __future__ import annotations

import importlib

from opentraces_schema import (
    Agent,
    Environment,
    Observation,
    Outcome,
    Step,
    ToolCall,
    TraceRecord,
    VCS,
)

from opentraces.capture.codex_cli.context_tree_capture import (
    LIMITATION_SESSION_LEVEL_LAYERS,
    LIMITATION_TOOL_SCHEMAS_UNAVAILABLE,
    build_context_tree_projection_from_record,
)
from opentraces.capture.codex_cli.parse import CodexCliParser
from opentraces.core.context_tree import (
    CONTEXT_COMPACTION_OBSERVED,
    CONTEXT_LAYER_CAPTURED,
    CONTEXT_NODE_OBSERVED,
    CONTEXT_TREE_RECONCILED,
)


def _record(*, compactions: list[dict] | None = None) -> TraceRecord:
    metadata = {
        "source": "codex_cli_rollout",
        "function_names": {"shell": 1},
        "codex_cli": {
            "cwd": "/tmp/project",
            "approval_policy": "never",
            "sandbox_policy": "danger-full-access",
            "base_instructions": {"sha256": "abc", "chars": 120},
        },
    }
    if compactions is not None:
        metadata["compaction_events"] = compactions
    return TraceRecord(
        trace_id="trace-codex-context",
        session_id="thread-123",
        agent=Agent(name="codex-cli", version="1.2.3", model="openai/gpt-5"),
        environment=Environment(
            vcs=VCS(type="git", branch="main", base_commit="abc123")
        ),
        outcome=Outcome(commit_sha="def456"),
        steps=[
            Step(step_index=1, role="user", content="Fix the bug", call_type="main"),
            Step(
                step_index=2,
                role="agent",
                content="I updated the test.",
                reasoning_content="[redacted: encrypted]",
                tools_available=["shell"],
                tool_calls=[
                    ToolCall(
                        tool_call_id="call-1",
                        tool_name="shell",
                        input={"cmd": "pytest tests/capture -q"},
                    )
                ],
                observations=[
                    Observation(
                        source_call_id="call-1",
                        output_summary="1 passed",
                    )
                ],
                call_type="main",
            ),
        ],
        metadata=metadata,
    )


def test_builds_session_level_context_tree_events_and_step_ids():
    record = _record()

    projection = build_context_tree_projection_from_record(record)

    assert [draft.event_type for draft in projection.drafts].count(CONTEXT_LAYER_CAPTURED) == 4
    assert [draft.event_type for draft in projection.drafts].count(CONTEXT_NODE_OBSERVED) == 2
    assert projection.drafts[-1].event_type == CONTEXT_TREE_RECONCILED
    assert projection.summary["node_count"] == 2
    assert projection.summary["layer_count"] == 4
    assert projection.summary["capture_method"] == "transcript_reconstruction"
    assert LIMITATION_SESSION_LEVEL_LAYERS in projection.summary["limitations"]
    assert LIMITATION_TOOL_SCHEMAS_UNAVAILABLE in projection.summary["limitations"]

    node_events = [
        draft.payload for draft in projection.drafts
        if draft.event_type == CONTEXT_NODE_OBSERVED
    ]
    assert node_events[0]["branch_type"] == "root"
    assert node_events[1]["branch_type"] == "linear"
    assert node_events[1]["parent_node_id"] == node_events[0]["node_id"]
    assert node_events[0]["transcript_uuid"] == "codex:thread-123:step:1"
    assert node_events[0]["trail_anchor_hint"]["commit_id"] == "def456"
    assert node_events[1]["trail_anchor_hint"]["commit_id"] == "def456"
    assert record.steps[0].context_node_id == node_events[0]["node_id"]
    assert record.steps[1].context_node_id == node_events[1]["node_id"]


def test_projection_is_stable_for_same_record_content():
    first = build_context_tree_projection_from_record(_record())
    second = build_context_tree_projection_from_record(_record())

    assert [node.node_id for node in first.nodes] == [node.node_id for node in second.nodes]
    assert [layer.layer_id for layer in first.layers] == [layer.layer_id for layer in second.layers]


def test_compaction_metadata_emits_context_compaction_event():
    record = _record(compactions=[{
        "source": "codex_cli_hook",
        "event": "PostCompact",
        "trigger": "manual",
        "timestamp": "2026-05-21T10:00:00Z",
        "step_index": 2,
    }])

    projection = build_context_tree_projection_from_record(record)

    compaction_events = [
        draft for draft in projection.drafts
        if draft.event_type == CONTEXT_COMPACTION_OBSERVED
    ]
    assert len(compaction_events) == 1
    event = compaction_events[0]
    assert event.trace_id == record.trace_id
    assert event.step_index == 2
    assert event.payload["source"] == "codex_cli_hook"
    assert event.payload["event"] == "PostCompact"
    assert event.payload["pre_node_id"] == record.steps[1].context_node_id
    assert event.payload["post_node_id"] == record.steps[1].context_node_id
    assert projection.summary["compaction_count"] == 1


def test_parser_exposes_context_tree_ingest_hook():
    parser = CodexCliParser()

    assert callable(parser.emit_context_tree_events_from_record)


def test_parser_context_tree_hook_invokes_without_bound_self(
    tmp_path,
    monkeypatch,
):
    calls = []

    def fake_append_event_batch(project_dir, drafts, writer):
        calls.append((project_dir, drafts, writer))
        return []

    event_log = importlib.import_module("opentraces.core.trails.event_log")
    monkeypatch.setattr(event_log, "append_event_batch", fake_append_event_batch)
    record = _record()
    parser = CodexCliParser()

    summary = parser.emit_context_tree_events_from_record(
        project_dir=tmp_path,
        final_record=record,
        transcript_path=tmp_path / "rollout.jsonl",
    )

    assert summary["node_count"] == 2
    assert summary["capture_limitations"] == summary["limitations"]
    assert record.steps[0].context_node_id is not None
    assert calls
    assert calls[0][0] == tmp_path
    assert calls[0][2] == "codex-context-tree-capture"
