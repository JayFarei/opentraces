"""Public models for Trace Index, Trace Map, and CandidatePacket contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Confidence = Literal["high", "medium", "low"]
NodeConfidence = Literal["deterministic", "heuristic"]


class TraceFacet(BaseModel):
    """A queryable fact attached to a trace unit."""

    name: str
    value: str | int | float | bool
    source: Literal[
        "exact_schema",
        "tool_input",
        "bash_command",
        "test_output",
        "file_path",
        "web_url",
        "dependency",
        "trail_projection",
        "prose_mention",
        "heuristic",
    ]
    confidence: Confidence = "high"
    evidence_ref: str | None = None


class TraceSignal(BaseModel):
    """A deterministic or heuristic signal attached to a trace unit."""

    name: str
    value: bool | str | int | float | list[str]
    confidence: Confidence = "medium"
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceUnit(BaseModel):
    """An addressable, bounded search document derived from trace evidence."""

    unit_id: str
    unit_type: Literal[
        "trace",
        "trace_map_node",
        "trace_slice",
        "trace_intent_candidate",
        "patch",
        "skill_invocation",
        "tool_sequence",
        "test_or_error_signal",
        "git_anchor",
    ]
    trace_id: str
    project_slug: str
    files: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    title_text: str = ""
    intent_text: str = ""
    action_text: str = ""
    evidence_text: str = ""
    artifact_text: str = ""
    facets: list[TraceFacet] = Field(default_factory=list)
    signals: list[TraceSignal] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    trail_refs: list[str] = Field(default_factory=list)


class TraceMapNode(BaseModel):
    """A typed Trace Map node with enough metadata for bounded traversal."""

    node_id: str
    trace_id: str
    unit_id: str
    action_type: Literal[
        "user_instruction",
        "agent_message",
        "agent_plan",
        "tool_call",
        "tool_result",
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
        # Cluster B / Plan 54: virtual aggregate node introduced by the
        # `--bursts` projection. Carries the burst summary in ``metadata``
        # (step_range, unique_files, patches, unique_git_anchors).
        "change_burst",
    ]
    step_index: int | None = None
    parent_node_id: str | None = None
    previous_node_id: str | None = None
    next_node_id: str | None = None
    start_step_index: int | None = None
    end_step_index: int | None = None
    tool_name: str | None = None
    files_read: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    anchor_refs: list[str] = Field(default_factory=list)
    signal_refs: list[str] = Field(default_factory=list)
    text_preview: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Cluster B / Plan 54: forward-pass propagation. The ``step_index`` of
    # the most recent ``user_instruction`` whose step <= this node's step,
    # or ``None`` if none precedes. Lets consumers attach intent without
    # walking back through every preceding node.
    active_user_step: int | None = None
    confidence: NodeConfidence = "deterministic"


class TraceMapEdge(BaseModel):
    """A typed relationship between Trace Map nodes."""

    edge_id: str
    trace_id: str
    source_node_id: str
    target_node_id: str
    edge_type: Literal[
        "parent_child",
        "previous_next",
        "causes",
        "observes",
        "edits",
        "tests",
        "anchors",
        "subagent",
    ]
    anchor_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    confidence: NodeConfidence = "deterministic"


class TraceMap(BaseModel):
    """A deterministic, workflow-neutral outline of a trace."""

    trace_id: str
    root_node_ids: list[str] = Field(default_factory=list)
    nodes: list[TraceMapNode] = Field(default_factory=list)
    edges: list[TraceMapEdge] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class CandidatePacket(BaseModel):
    """Bounded query result for candidate evidence discovery."""

    unit_id: str
    unit_type: str
    trace_id: str
    project_slug: str
    title: str
    intent_preview: str | None = None
    candidate_kind: str | None = None
    match_explanation: str
    score: float
    score_parts: dict[str, float] = Field(default_factory=dict)
    matched_fields: dict[str, list[str]] = Field(default_factory=dict)
    facets: list[TraceFacet] = Field(default_factory=list)
    signals: list[TraceSignal] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    trail_refs: list[str] = Field(default_factory=list)
    map_ref: str | None = None
    map_node_refs: list[str] = Field(default_factory=list)
    refs: dict[str, str] = Field(default_factory=dict)
    slice_preview: TraceMap | None = None
    limitations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
