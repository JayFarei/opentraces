from __future__ import annotations

from opentraces_schema import Agent, Observation, Step, ToolCall, TraceRecord

from opentraces.capture.pi.context_tree_capture import (
    LIMITATION_TRANSCRIPT_FALLBACK,
    build_context_tree_projection_from_record,
)
from opentraces.capture.pi.parse import PiSessionParser
from opentraces.core.context_tree import (
    CONTEXT_COMPACTION_OBSERVED,
    CONTEXT_LAYER_CAPTURED,
    CONTEXT_NODE_OBSERVED,
    CONTEXT_TREE_RECONCILED,
)


def _record(*, provider_context: bool = True, compactions: list[dict] | None = None) -> TraceRecord:
    metadata = {
        "source": "pi_session_jsonl",
        "pi": {
            "cwd": "/tmp/project",
            "session_file": "/tmp/pi.jsonl",
            "active_leaf_id": "leaf-1",
            "active_leaf_source": "sidecar:active_leaf",
            "branch_count": 1,
            "tree_events": [{"old_leaf_id": "leaf-old", "new_leaf_id": "leaf-1"}],
            "provider_contexts": [],
            "tool_registry": [{"name": "Edit", "input_schema": {"type": "object"}}],
            "runtime_state": {"thinking_level": "high"},
        },
    }
    if provider_context:
        metadata["pi"]["provider_contexts"] = [{
            "capture_method": "live_capture",
            "completeness": "full",
            "system": "system prompt",
            "messages": [{"role": "user", "content": "Fix bug"}],
            "tool_registry": [{"name": "Edit", "input_schema": {"type": "object"}}],
            "runtime_state": {"permission_mode": "default"},
        }]
    if compactions is not None:
        metadata["compaction_events"] = compactions
    return TraceRecord(
        trace_id="trace-pi-context",
        session_id="pi-thread-123",
        agent=Agent(name="pi", version="1.0.0", model="anthropic/claude-sonnet-4"),
        steps=[
            Step(step_index=1, role="user", content="Fix bug", call_type="main"),
            Step(
                step_index=2,
                role="agent",
                content="Edited file",
                call_type="main",
                tool_calls=[ToolCall(tool_call_id="call-1", tool_name="Edit", input={"file_path": "app.py"})],
                observations=[Observation(source_call_id="call-1", output_summary="ok")],
            ),
        ],
        metadata=metadata,
    )


def test_provider_context_builds_full_live_capture_layers_and_step_ids() -> None:
    record = _record(provider_context=True)

    projection = build_context_tree_projection_from_record(record)

    assert [draft.event_type for draft in projection.drafts].count(CONTEXT_LAYER_CAPTURED) == 4
    assert [draft.event_type for draft in projection.drafts].count(CONTEXT_NODE_OBSERVED) == 2
    assert projection.drafts[-1].event_type == CONTEXT_TREE_RECONCILED
    assert projection.summary["capture_method"] == "live_capture"
    assert projection.summary["capture_methods"] == ["live_capture"]
    assert projection.layers[0].completeness == "full"
    assert projection.layers[1].content["tree_events"][0]["old_leaf_id"] == "leaf-old"
    assert projection.drafts[-1].payload["orphan_branch_roots"] == ["leaf-old"]
    assert projection.drafts[-1].payload["pi_tree_events"][0]["new_leaf_id"] == "leaf-1"
    assert record.steps[0].context_node_id is not None
    assert record.steps[1].context_node_id is not None


def test_transcript_only_fallback_records_honest_limitation() -> None:
    record = _record(provider_context=False)

    projection = build_context_tree_projection_from_record(record)

    assert projection.summary["capture_method"] == "transcript_reconstruction"
    assert LIMITATION_TRANSCRIPT_FALLBACK in projection.summary["limitations"]
    assert all(layer.completeness == "approximated" for layer in projection.layers)


def test_compaction_metadata_emits_context_compaction_event() -> None:
    record = _record(provider_context=True, compactions=[{
        "source": "pi_sidecar",
        "event": "Compaction",
        "trigger": "manual",
        "timestamp": "2026-06-02T10:00:00Z",
        "step_index": 2,
    }])

    projection = build_context_tree_projection_from_record(record)

    compaction_events = [draft for draft in projection.drafts if draft.event_type == CONTEXT_COMPACTION_OBSERVED]
    assert len(compaction_events) == 1
    assert compaction_events[0].trace_id == record.trace_id
    assert compaction_events[0].payload["source"] == "pi_sidecar"
    assert projection.summary["compaction_count"] == 1


def test_parser_exposes_context_tree_ingest_hook() -> None:
    assert callable(PiSessionParser().emit_context_tree_events_from_record)


def test_parser_context_tree_hook_invokes_without_bound_self(tmp_path, monkeypatch) -> None:
    calls = []

    def fake_append_event_batch(project_dir, drafts, writer):
        calls.append((project_dir, drafts, writer))
        return []

    import importlib

    event_log = importlib.import_module("opentraces.core.trails.event_log")
    monkeypatch.setattr(event_log, "append_event_batch", fake_append_event_batch)
    record = _record(provider_context=True)

    summary = PiSessionParser().emit_context_tree_events_from_record(
        project_dir=tmp_path,
        final_record=record,
        transcript_path=tmp_path / "session.jsonl",
    )

    assert summary["node_count"] == 2
    assert summary["capture_limitations"] == summary["limitations"]
    assert calls[0][2] == "pi-context-tree-capture"
