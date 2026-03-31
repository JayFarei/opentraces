"""Pydantic v2 models for the opentraces.ai JSONL trace schema.

This module defines the complete schema for enriched agent session traces.
Each TraceRecord represents one complete agent session or task unit.

The schema is informed by ATIF v1.6, ADP, Agent Trace spec, and field patterns
found in existing HF datasets (nlile, Nebius, CoderForge).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from .version import SCHEMA_VERSION


class Task(BaseModel):
    """Structured task metadata for filtering and grouping."""

    description: str | None = None
    source: str | None = Field(None, description="user_prompt, cli_arg, skill, etc.")
    repository: str | None = Field(None, description="owner/repo format")
    base_commit: str | None = None


class Agent(BaseModel):
    """Agent identity following provider/model convention."""

    name: str = Field(description="Agent identifier: claude-code, cursor, codex, etc.")
    version: str | None = None
    model: str | None = Field(None, description="provider/model-name, e.g. anthropic/claude-sonnet-4-20250514")


class VCS(BaseModel):
    """Version control metadata. type='none' when not in a git repo."""

    type: Literal["git", "none"] = "none"
    base_commit: str | None = None
    branch: str | None = None
    diff: str | None = Field(None, description="Unified diff string or null")


class Environment(BaseModel):
    """Runtime environment metadata for filtering and reproducibility."""

    os: str | None = None
    shell: str | None = None
    vcs: VCS = Field(default_factory=VCS)
    language_ecosystem: list[str] = Field(default_factory=list)


class ToolCall(BaseModel):
    """A single tool invocation within a step."""

    tool_call_id: str
    tool_name: str
    input: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = None


class Observation(BaseModel):
    """Result of a tool call, linked back via source_call_id."""

    source_call_id: str
    content: str | None = None
    output_summary: str | None = Field(None, description="Lightweight preview of tool result")
    error: str | None = Field(None, description="Error info, e.g. 'no_result' for dangling tool calls")


class Snippet(BaseModel):
    """Code block extracted from tool results or agent responses."""

    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    language: str | None = None
    text: str | None = None
    source_step: int | None = Field(None, description="Step index that produced this snippet")


class TokenUsage(BaseModel):
    """Per-step token usage breakdown for cost and efficiency analysis."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    prefix_reuse_tokens: int = 0


class Step(BaseModel):
    """A single LLM API call (request + response) in the TAO loop.

    Each step represents one thought-action-observation cycle, not a
    conversational turn. This aligns with ATIF's step-based model.
    """

    step_index: int
    role: Literal["system", "user", "agent"]
    content: str | None = None
    reasoning_content: str | None = Field(None, description="Chain-of-thought / extended thinking")
    model: str | None = None
    system_prompt_hash: str | None = Field(None, description="Key into top-level system_prompts map")
    agent_role: str | None = Field(None, description="main, explore, plan, etc.")
    parent_step: int | None = Field(None, description="Step index of parent for sub-agent hierarchy")
    call_type: Literal["main", "subagent", "warmup"] | None = None
    subagent_trajectory_ref: str | None = Field(None, description="Session ID of sub-agent trajectory")
    tools_available: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    snippets: list[Snippet] = Field(default_factory=list)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    timestamp: str | None = None


class Outcome(BaseModel):
    """Session outcome signals for RL/reward modeling.

    signal_confidence indicates how trustworthy the signal is:
    - derived: deterministic extraction (e.g. committed from git)
    - inferred: heuristic-based (e.g. success from test output patterns)
    - annotated: human or CI annotation
    """

    success: bool | None = None
    signal_source: str = "deterministic"
    signal_confidence: Literal["derived", "inferred", "annotated"] = "derived"
    description: str | None = None
    patch: str | None = Field(None, description="Unified diff produced by the session")
    committed: bool = False
    commit_sha: str | None = None


class AttributionRange(BaseModel):
    """A range of lines attributed to an agent conversation."""

    start_line: int
    end_line: int
    content_hash: str | None = Field(None, description="murmur3 hash for cross-refactor tracking")
    confidence: Literal["high", "medium", "low"] | None = None


class AttributionConversation(BaseModel):
    """Links attributed code ranges to the conversation that produced them."""

    contributor: dict[str, str] = Field(
        default_factory=dict,
        description="e.g. {type: 'ai', model_id: 'anthropic/claude-sonnet-4-20250514'}",
    )
    url: str | None = Field(None, description="opentraces://trace_id/step_N")
    ranges: list[AttributionRange] = Field(default_factory=list)


class AttributionFile(BaseModel):
    """Attribution data for a single file."""

    path: str
    conversations: list[AttributionConversation] = Field(default_factory=list)


class Attribution(BaseModel):
    """Embedded Agent Trace-compatible attribution block.

    Bridges trajectory (process) and attribution (output). Records which
    files and line ranges were produced by the agent session.

    Marked experimental in v0.1 - confidence varies by session complexity.
    """

    experimental: bool = True
    files: list[AttributionFile] = Field(default_factory=list)


class Metrics(BaseModel):
    """Aggregated session-level metrics for analytics and cost modeling."""

    total_steps: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_duration_s: float | None = None
    cache_hit_rate: float | None = Field(None, ge=0.0, le=1.0)
    estimated_cost_usd: float | None = None


class SecurityMetadata(BaseModel):
    """Records what security processing was applied and what was flagged/redacted."""

    scanned: bool = False
    flags_reviewed: int = 0
    redactions_applied: int = 0
    classifier_version: str | None = None


class TraceRecord(BaseModel):
    """Top-level model for one complete agent session trace.

    Each line in the JSONL file is one TraceRecord. The schema bridges
    trajectory data (ATIF/ADP) with code attribution (Agent Trace spec),
    creating the complete record of process + output.
    """

    schema_version: str = SCHEMA_VERSION
    trace_id: str
    session_id: str
    content_hash: str | None = None
    timestamp_start: str | None = None
    timestamp_end: str | None = None
    task: Task = Field(default_factory=Task)
    agent: Agent
    environment: Environment = Field(default_factory=Environment)
    system_prompts: dict[str, str] = Field(
        default_factory=dict,
        description="Deduplicated system prompts keyed by hash",
    )
    tool_definitions: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    outcome: Outcome = Field(default_factory=Outcome)
    dependencies: list[str] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    security: SecurityMetadata = Field(default_factory=SecurityMetadata)
    attribution: Attribution | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def compute_content_hash(self) -> str:
        """Compute SHA-256 hash of the trace content for deduplication.

        Excludes content_hash and trace_id so re-parsing identical content
        produces the same hash regardless of the random UUID assigned.
        """
        data = self.model_dump(exclude={"content_hash", "trace_id"})
        serialized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()

    def to_jsonl_line(self) -> str:
        """Serialize to a single JSONL line with computed content_hash."""
        self.content_hash = self.compute_content_hash()
        return self.model_dump_json()
