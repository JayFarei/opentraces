"""opentraces-schema: Pydantic models for the opentraces.ai JSONL trace format."""

from .models import (
    Agent,
    Attribution,
    AttributionConversation,
    AttributionFile,
    AttributionRange,
    Environment,
    Metrics,
    Observation,
    Outcome,
    SecurityMetadata,
    Snippet,
    Step,
    Task,
    TokenUsage,
    ToolCall,
    TraceRecord,
    VCS,
)
from .version import SCHEMA_VERSION

__all__ = [
    "Agent",
    "Attribution",
    "AttributionConversation",
    "AttributionFile",
    "AttributionRange",
    "Environment",
    "Metrics",
    "Observation",
    "Outcome",
    "SCHEMA_VERSION",
    "SecurityMetadata",
    "Snippet",
    "Step",
    "Task",
    "TokenUsage",
    "ToolCall",
    "TraceRecord",
    "VCS",
]
