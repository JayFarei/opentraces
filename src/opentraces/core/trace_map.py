"""Deterministic Trace Map builders for Plan 56 query workflows."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from opentraces_schema import TraceMap, TraceMapEdge, TraceMapNode, TraceRecord

from .text_redaction import redact_index_text


_TEST_COMMAND_RE = re.compile(
    r"\b(pytest|npm\s+test|pnpm\s+test|yarn\s+test|cargo\s+test|go\s+test|"
    r"vitest|jest|rspec|mvn\s+test|gradle\s+test)\b",
    re.IGNORECASE,
)


def build_trace_map(record: TraceRecord) -> TraceMap:
    """Build a workflow-neutral typed outline from a trace record."""

    builder = _TraceMapBuilder(record.trace_id)

    steps = sorted(record.steps, key=lambda s: s.step_index)
    last_agent_text_step = next(
        (step for step in reversed(steps) if step.role == "agent" and step.content),
        None,
    )

    for step in steps:
        if step.role == "user" and step.content:
            builder.add_node(
                unit_id=f"tu:{record.trace_id}:step:{step.step_index}",
                action_type="user_instruction",
                step_index=step.step_index,
                text_preview=_preview(step.content),
            )
        elif step.role == "agent" and step.content:
            builder.add_node(
                unit_id=f"tu:{record.trace_id}:step:{step.step_index}",
                action_type=_agent_text_action_type(step.content, is_last=step is last_agent_text_step),
                step_index=step.step_index,
                text_preview=_preview(step.content),
            )

        for call in step.tool_calls:
            action_type = _tool_action_type(call.tool_name, call.input)
            node = builder.add_node(
                unit_id=f"tu:{record.trace_id}:tool:{call.tool_call_id}",
                action_type=action_type,
                step_index=step.step_index,
                tool_name=call.tool_name,
                files_read=_files_for_tool(call.tool_name, call.input, read=True),
                files_modified=_files_for_tool(call.tool_name, call.input, read=False),
                text_preview=_tool_preview(call.tool_name, call.input),
                metadata={"tool_call_id": call.tool_call_id},
            )
            if action_type in {"file_edit", "test_run", "tool_call", "file_read"}:
                node.signal_refs.extend(_signal_refs_for_tool(record.trace_id, call.tool_call_id, call.input))

        for observation in step.observations:
            builder.add_node(
                unit_id=f"tu:{record.trace_id}:observation:{observation.source_call_id}",
                action_type="error_signal" if observation.error else "tool_result",
                step_index=step.step_index,
                text_preview=_preview(observation.output_summary or observation.content or observation.error or ""),
                metadata={"source_call_id": observation.source_call_id},
            )

    trace_map = TraceMap(
        trace_id=record.trace_id,
        root_node_ids=[builder.nodes[0].node_id] if builder.nodes else [],
        nodes=builder.nodes,
        edges=builder.edges,
    )
    return trace_map


def slice_trace_map_for_candidate(
    trace_map: TraceMap,
    candidate_node_id: str,
    *,
    max_steps: int = 40,
) -> TraceMap:
    """Return a bounded verification slice around a candidate node."""

    interesting = {
        "user_instruction",
        "agent_plan",
        "file_edit",
        "test_run",
        "error_signal",
        "patch_created",
        "git_anchor",
        "final_response",
    }
    by_id = {node.node_id: node for node in trace_map.nodes}
    if candidate_node_id not in by_id:
        return TraceMap(
            trace_id=trace_map.trace_id,
            limitations=[f"candidate node not found: {candidate_node_id}"],
        )

    candidate_index = next(
        index for index, node in enumerate(trace_map.nodes) if node.node_id == candidate_node_id
    )
    start = 0
    end = len(trace_map.nodes)
    selected: list[TraceMapNode] = []

    for node in trace_map.nodes[start:end]:
        if node.action_type in interesting:
            selected.append(node)
        if len(selected) >= max_steps:
            break

    if not any(node.node_id == candidate_node_id for node in selected):
        selected = [trace_map.nodes[candidate_index], *selected[: max_steps - 1]]

    selected_ids = {node.node_id for node in selected}
    edges = [
        edge
        for edge in trace_map.edges
        if edge.source_node_id in selected_ids and edge.target_node_id in selected_ids
    ]
    return TraceMap(
        trace_id=trace_map.trace_id,
        root_node_ids=[selected[0].node_id] if selected else [],
        nodes=selected,
        edges=edges,
        limitations=[f"bounded_to_{max_steps}_nodes"] if len(selected) == max_steps else [],
    )


def trace_map_around(trace_map: TraceMap, node_id: str, *, depth: int = 2) -> TraceMap:
    """Return a bounded neighborhood around a node in map order."""

    index = _node_index(trace_map, node_id)
    if index is None:
        return TraceMap(trace_id=trace_map.trace_id, limitations=[f"node not found: {node_id}"])
    start = max(0, index - max(0, depth))
    end = min(len(trace_map.nodes), index + max(0, depth) + 1)
    return _submap(trace_map, trace_map.nodes[start:end])


def walk_trace_map(
    trace_map: TraceMap,
    node_id: str,
    *,
    direction: str,
    until_action_types: set[str] | None = None,
    max_steps: int = 40,
) -> TraceMap:
    """Walk backward or forward from a node across meaningful action nodes."""

    index = _node_index(trace_map, node_id)
    if index is None:
        return TraceMap(trace_id=trace_map.trace_id, limitations=[f"node not found: {node_id}"])
    interesting = {
        "user_instruction",
        "agent_message",
        "agent_plan",
        "skill_invocation",
        "file_read",
        "file_edit",
        "test_run",
        "error_signal",
        "patch_created",
        "git_anchor",
        "subagent_call",
        "snapshot",
        "security_finding",
        "final_response",
    }
    until = until_action_types or set()
    if direction == "back":
        indexes = range(index, -1, -1)
    elif direction == "forward":
        indexes = range(index, len(trace_map.nodes))
    else:
        raise ValueError("direction must be 'back' or 'forward'")

    selected: list[TraceMapNode] = []
    for cursor in indexes:
        node = trace_map.nodes[cursor]
        if node.node_id != node_id and node.action_type not in interesting:
            continue
        selected.append(node)
        if node.node_id != node_id and node.action_type in until:
            break
        if len(selected) >= max_steps:
            break
    out = _submap(trace_map, selected)
    if len(selected) == max_steps:
        out.limitations.append(f"bounded_to_{max_steps}_nodes")
    return out


def _node_index(trace_map: TraceMap, node_id: str) -> int | None:
    for index, node in enumerate(trace_map.nodes):
        if node.node_id == node_id or node.unit_id == node_id:
            return index
    return None


def _submap(trace_map: TraceMap, selected: list[TraceMapNode]) -> TraceMap:
    selected_ids = {node.node_id for node in selected}
    return TraceMap(
        trace_id=trace_map.trace_id,
        root_node_ids=[selected[0].node_id] if selected else [],
        nodes=selected,
        edges=[
            edge
            for edge in trace_map.edges
            if edge.source_node_id in selected_ids and edge.target_node_id in selected_ids
        ],
        limitations=[],
    )


class _TraceMapBuilder:
    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self.nodes: list[TraceMapNode] = []
        self.edges: list[TraceMapEdge] = []

    def add_node(
        self,
        *,
        unit_id: str,
        action_type: str,
        step_index: int | None,
        text_preview: str | None = None,
        tool_name: str | None = None,
        files_read: list[str] | None = None,
        files_modified: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceMapNode:
        ordinal = len(self.nodes) + 1
        previous = self.nodes[-1] if self.nodes else None
        node = TraceMapNode(
            node_id=f"tmn:{self.trace_id}:{ordinal}",
            trace_id=self.trace_id,
            unit_id=unit_id,
            action_type=action_type,  # type: ignore[arg-type]
            step_index=step_index,
            start_step_index=step_index,
            end_step_index=step_index,
            previous_node_id=previous.node_id if previous else None,
            tool_name=tool_name,
            files_read=files_read or [],
            files_modified=files_modified or [],
            text_preview=text_preview,
            metadata=metadata or {},
        )
        if previous:
            previous.next_node_id = node.node_id
            self.edges.append(
                TraceMapEdge(
                    edge_id=f"tme:{self.trace_id}:{len(self.edges) + 1}",
                    trace_id=self.trace_id,
                    source_node_id=previous.node_id,
                    target_node_id=node.node_id,
                    edge_type="previous_next",
                )
            )
        self.nodes.append(node)
        return node


def _agent_text_action_type(content: str, *, is_last: bool) -> str:
    lowered = content.lower()
    if is_last:
        return "final_response"
    if "plan:" in lowered or lowered.startswith("plan\n") or "- " in content:
        return "agent_plan"
    return "agent_message"


def _tool_action_type(tool_name: str, tool_input: dict[str, Any]) -> str:
    normalized = tool_name.lower().replace("_", "").replace("-", "")
    command = str(tool_input.get("command") or "")
    if normalized in {"edit", "write", "multiedit"}:
        return "file_edit"
    if normalized in {"read", "view", "glob", "grep"}:
        return "file_read"
    if normalized in {"bash", "shell"} and _TEST_COMMAND_RE.search(command):
        return "test_run"
    if "skill" in normalized:
        return "skill_invocation"
    return "tool_call"


def _files_for_tool(tool_name: str, tool_input: dict[str, Any], *, read: bool) -> list[str]:
    normalized = tool_name.lower().replace("_", "").replace("-", "")
    keys = ("file", "path") if read else ("file_path", "path")
    if read and normalized not in {"read", "view", "glob", "grep"}:
        return []
    if not read and normalized not in {"edit", "write", "multiedit"}:
        return []
    files: list[str] = []
    for key in keys:
        value = tool_input.get(key)
        files.extend(_as_strings(value))
    return sorted(dict.fromkeys(files))


def _signal_refs_for_tool(trace_id: str, tool_call_id: str, tool_input: dict[str, Any]) -> list[str]:
    command = str(tool_input.get("command") or "")
    if not command or not _TEST_COMMAND_RE.search(command):
        return []
    return [f"signal:{trace_id}:tool:{tool_call_id}:test_command_seen"]


def _tool_preview(tool_name: str, tool_input: dict[str, Any]) -> str:
    if "command" in tool_input:
        return _preview(str(tool_input["command"]))
    if "file_path" in tool_input:
        return _preview(f"{tool_name} {tool_input['file_path']}")
    if "file" in tool_input:
        return _preview(f"{tool_name} {tool_input['file']}")
    return tool_name


def _preview(text: str, *, limit: int = 160) -> str:
    compact = " ".join(redact_index_text(text).split())
    return compact[: limit - 3] + "..." if len(compact) > limit else compact


def _as_strings(value: Any) -> Iterable[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]
