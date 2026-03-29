"""ATIF v1.6 (Agent Trajectory Interchange Format) exporter.

Lossy projection from opentraces TraceRecord to ATIF v1.6 JSONL.
Preserves: steps, tool_calls, observations, reasoning_content, token_usage, agent.
Drops: attribution, security, environment, system_prompts, outcome, dependencies,
       metrics aggregate, content_hash, snippets, hierarchy.

ATIF spec: https://harborframework.com/docs/agents/trajectory-format
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from opentraces_schema import TraceRecord

logger = logging.getLogger(__name__)

from .base import FieldStatus


class ATIFExporter:
    """Export TraceRecords to ATIF v1.6 JSONL format."""

    format_name = "atif"
    file_extension = ".jsonl"
    description = "Agent Trajectory Interchange Format v1.6"

    def export_traces(self, records: list[TraceRecord]) -> Iterator[str]:
        """Yield one ATIF JSONL line per TraceRecord.

        Skips records that fail conversion, logs errors, continues.
        """
        errors = 0
        for record in records:
            try:
                atif_dict = self._to_atif(record)
                yield json.dumps(atif_dict, default=str)
            except Exception as e:
                errors += 1
                logger.warning(
                    f"Skipping trace {record.trace_id} during ATIF export: {e}"
                )
        if errors:
            logger.info(f"ATIF export: {errors} records skipped due to errors")

    def field_coverage(self) -> dict[str, FieldStatus]:
        return {
            "steps": "full",
            "tool_calls": "full",
            "observations": "full",
            "reasoning_content": "full",
            "token_usage": "partial",  # drops prefix_reuse_tokens, cache_write_tokens
            "agent": "full",
            "session_id": "full",
            "tool_definitions": "full",
            "timestamps": "full",
            "attribution": "dropped",
            "security": "dropped",
            "environment": "dropped",
            "outcome": "dropped",
            "dependencies": "dropped",
            "system_prompts": "dropped",
            "metrics_aggregate": "dropped",
            "content_hash": "dropped",
            "snippets": "dropped",
            "hierarchy": "dropped",
        }

    def _to_atif(self, record: TraceRecord) -> dict[str, Any]:
        """Convert a single TraceRecord to ATIF v1.6 dict."""
        atif: dict[str, Any] = {
            "schema_version": "ATIF-v1.6",
            "session_id": record.session_id,
            "agent": self._map_agent(record),
            "steps": self._map_steps(record),
        }
        return atif

    def _map_agent(self, record: TraceRecord) -> dict[str, Any]:
        agent: dict[str, Any] = {
            "name": record.agent.name,
        }
        if record.agent.version:
            agent["version"] = record.agent.version
        if record.agent.model:
            agent["model_name"] = record.agent.model
        if record.tool_definitions:
            agent["tool_definitions"] = record.tool_definitions
        return agent

    def _map_steps(self, record: TraceRecord) -> list[dict[str, Any]]:
        """Map opentraces Steps to ATIF steps.

        Renumbers step_id sequentially starting at 1 (ATIF convention)
        regardless of source step_index values.
        """
        atif_steps = []
        for emit_index, step in enumerate(record.steps, 1):
            atif_step: dict[str, Any] = {
                "step_id": emit_index,
                "source": step.role,  # system | user | agent (same in both)
            }

            # Message content (may be None for pure tool-call steps)
            if step.content is not None:
                atif_step["message"] = step.content

            if step.reasoning_content:
                atif_step["reasoning_content"] = step.reasoning_content

            if step.model:
                atif_step["model_name"] = step.model

            if step.timestamp:
                atif_step["timestamp"] = step.timestamp

            # Tool calls
            if step.tool_calls:
                atif_step["tool_calls"] = [
                    {
                        "tool_call_id": tc.tool_call_id,
                        "function_name": tc.tool_name,
                        "arguments": tc.input,  # ATIF accepts dict
                    }
                    for tc in step.tool_calls
                ]

            # Observations -> ATIF observation (singular) with results array
            if step.observations:
                atif_step["observation"] = {
                    "results": [
                        self._map_observation(obs)
                        for obs in step.observations
                    ]
                }

            # Token usage -> ATIF metrics
            usage = step.token_usage
            if usage.input_tokens or usage.output_tokens:
                atif_step["metrics"] = {
                    "prompt_tokens": usage.input_tokens,
                    "completion_tokens": usage.output_tokens,
                    "cached_tokens": usage.cache_read_tokens,
                }

            atif_steps.append(atif_step)

        return atif_steps

    def _map_observation(self, obs: Any) -> dict[str, Any]:
        """Map a single opentraces Observation to ATIF ObservationResult."""
        result: dict[str, Any] = {
            "source_call_id": obs.source_call_id,
        }
        if obs.content is not None:
            result["content"] = obs.content
        elif obs.error:
            # Dangling tool calls: preserve the error signal
            result["content"] = f"[error: {obs.error}]"
        return result
