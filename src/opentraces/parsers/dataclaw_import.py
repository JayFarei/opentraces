"""DataClaw JSONL import adapter.

Converts DataClaw conversation exports into opentraces TraceRecord format.
DataClaw stores flat JSONL with session-level aggregates and message lists.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from opentraces_schema.models import (
    Agent,
    Metrics,
    Observation,
    Step,
    TokenUsage,
    ToolCall,
    TraceRecord,
)
from opentraces_schema.version import SCHEMA_VERSION


def _map_messages_to_steps(messages: list[dict]) -> list[Step]:
    """Convert DataClaw message list to opentraces Steps."""
    steps: list[Step] = []
    for i, msg in enumerate(messages):
        role = msg.get("role", "user")
        # Map DataClaw roles to opentraces roles
        if role == "assistant":
            mapped_role = "agent"
        elif role in ("system", "user"):
            mapped_role = role
        else:
            mapped_role = "user"

        # Map tool_uses to ToolCall objects
        tool_calls = []
        observations = []
        for tu in msg.get("tool_uses", []) or []:
            call_id = tu.get("id", f"call_{i}_{len(tool_calls)}")
            tool_calls.append(
                ToolCall(
                    tool_call_id=call_id,
                    tool_name=tu.get("name", "unknown"),
                    input=tu.get("input", {}),
                )
            )
            # If tool_use has a result, create an observation
            if "result" in tu:
                observations.append(
                    Observation(
                        source_call_id=call_id,
                        content=str(tu["result"]) if tu["result"] is not None else None,
                    )
                )

        step = Step(
            step_index=i,
            role=mapped_role,
            content=msg.get("content"),
            reasoning_content=msg.get("thinking"),
            tool_calls=tool_calls,
            observations=observations,
            token_usage=TokenUsage(),
        )
        steps.append(step)

    return steps


def _convert_record(raw: dict) -> TraceRecord:
    """Convert a single DataClaw record to a TraceRecord."""
    trace_id = str(uuid.uuid4())
    session_id = raw.get("session_id", trace_id)

    messages = raw.get("messages", [])
    steps = _map_messages_to_steps(messages)

    # Extract token counts if available
    token_counts = raw.get("token_counts", {})
    total_input = token_counts.get("input_tokens", 0)
    total_output = token_counts.get("output_tokens", 0)

    agent = Agent(
        name=raw.get("agent_name", "unknown"),
        model=raw.get("model"),
    )

    metrics = Metrics(
        total_steps=len(steps),
        total_input_tokens=total_input,
        total_output_tokens=total_output,
    )

    trace = TraceRecord(
        schema_version=SCHEMA_VERSION,
        trace_id=trace_id,
        session_id=session_id,
        agent=agent,
        steps=steps,
        metrics=metrics,
        timestamp_start=raw.get("timestamp"),
        metadata={"import_source": "dataclaw"},
    )

    trace.content_hash = trace.compute_content_hash()
    return trace


def import_dataclaw(input_path: Path) -> list[TraceRecord]:
    """Read DataClaw conversations.jsonl and convert to opentraces schema.

    Args:
        input_path: Path to the DataClaw JSONL file.

    Returns:
        List of TraceRecord objects.
    """
    traces: list[TraceRecord] = []
    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            traces.append(_convert_record(raw))
    return traces
