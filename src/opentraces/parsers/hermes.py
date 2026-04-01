"""Parser for Hermes Agent traces (ShareGPT + XML tool calls).

Row mapper: converts individual dataset rows to TraceRecords.
Does NOT handle download/streaming (that's CLI responsibility).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from opentraces_schema.models import (
    Agent,
    Metrics,
    Observation,
    Outcome,
    Step,
    Task,
    ToolCall,
    TraceRecord,
)

logger = logging.getLogger(__name__)

# Max content length before regex parsing (1MB safety bound)
_MAX_CONTENT_LEN = 1_048_576

# Map Hermes tool names to canonical names used by enrichment functions.
# Only code tools need mapping (enrichment functions check for Edit/Write/Read/Bash/Grep).
# Browser and web tools pass through as-is since no enrichment depends on them.
TOOL_NAME_MAP: dict[str, str] = {
    "terminal": "Bash",
    "write_file": "Write",
    "read_file": "Read",
    "patch": "Edit",
    "search_files": "Grep",
    "execute_code": "Bash",
}

# Tools that are known Hermes tools but don't need normalization.
# These won't trigger "unmapped tool" warnings.
KNOWN_PASSTHROUGH_TOOLS: set[str] = {
    "browser_navigate", "browser_snapshot", "browser_click", "browser_type",
    "browser_scroll", "browser_back", "browser_press", "browser_close",
    "browser_get_images", "browser_vision", "browser_console",
    "web_search", "web_extract",
    "vision_analyze", "image_generate",
    "todo", "memory", "clarify", "delegate", "send_message",
    "session_search", "skill_manage", "skill_view", "skills_list",
    "mixture_of_agents", "text_to_speech", "code_execution",
    "honcho_context", "honcho_conclude",
    "ha_get_state", "ha_list_entities", "ha_list_services", "ha_call_service",
    "rl_select_environment", "rl_start_training", "rl_stop_training",
    "rl_list_environments", "rl_list_runs", "rl_get_results",
    "rl_get_current_config", "rl_edit_config", "rl_check_status",
}

# Map Hermes argument keys to canonical keys (keyed by ORIGINAL tool name).
ARG_NAME_MAP: dict[str, dict[str, str]] = {
    "patch": {"path": "file_path", "original": "old_string", "replacement": "new_string"},
    "write_file": {"path": "file_path", "content": "content"},
    "read_file": {"path": "file_path"},
    "terminal": {"command": "command"},
}

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
_TOOL_RESPONSE_RE = re.compile(r"<tool_response>(.*?)</tool_response>", re.DOTALL)


class HermesParser:
    """Parser for Hermes Agent traces (ShareGPT + XML tool calls).

    Implements FormatImporter protocol. Row mapper: converts individual
    dataset rows to TraceRecords. Does NOT handle download/streaming.
    """

    format_name = "hermes"
    file_extensions = [".jsonl", ".json"]

    # ------------------------------------------------------------------
    # Protocol methods
    # ------------------------------------------------------------------

    def import_traces(self, input_path: Path, max_records: int = 0) -> list[TraceRecord]:
        """Import from a local JSONL file of Hermes conversations."""
        records: list[TraceRecord] = []
        with open(input_path) as f:
            for i, line in enumerate(f):
                if max_records and len(records) >= max_records:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed JSON at line %d", i)
                    continue
                record = self.map_record(row, i)
                if record is not None:
                    records.append(record)
        return records

    def map_record(
        self, row: dict, index: int, source_info: dict | None = None,
    ) -> TraceRecord | None:
        """Convert one dataset row to a TraceRecord.

        Returns None if the row is invalid or unparseable.
        """
        conversations = row.get("conversations")
        if not conversations:
            return None

        steps, system_prompts, linkage_failures = self._conversations_to_steps(conversations)
        if not steps:
            return None

        # Build provenance metadata (no raw source data)
        source_info = source_info or {}
        dataset_id = source_info.get("dataset_id", "unknown")
        subset = source_info.get("subset", "default")
        revision = source_info.get("revision", "unknown")
        revision_short = revision[:12] if revision != "unknown" else "unknown"

        metadata: dict[str, Any] = {
            "source_dataset": dataset_id,
            "source_dataset_revision": revision,
            "source_subset": subset,
            "source_split": source_info.get("split", "train"),
            "source_row_index": index,
            "step_fidelity": "conversation_turn",
            "step_fidelity_note": (
                "Source provides conversation turns, not individual API calls. "
                "Step count may undercount actual LLM calls."
            ),
            "source_completion_status": {
                "completed": row.get("completed"),
                "partial": row.get("partial"),
            },
        }

        if linkage_failures:
            metadata["linkage_failures"] = linkage_failures

        # P4: Extract tool_stats for richer outcome signals
        tool_stats = row.get("tool_stats", {}) or {}
        total_tool_failures = 0
        total_tool_calls_counted = 0
        for tool_name, stats in tool_stats.items():
            if isinstance(stats, dict):
                total_tool_failures += stats.get("failure", 0) or 0
                total_tool_calls_counted += stats.get("count", 0) or 0
        if total_tool_calls_counted > 0:
            metadata["tool_success_rate"] = round(
                1.0 - (total_tool_failures / total_tool_calls_counted), 3,
            )
            metadata["tool_failure_count"] = total_tool_failures

        # Extract model name
        row_metadata = row.get("metadata", {}) or {}
        model = row_metadata.get("model") or None  # None if unknown; don't fabricate placeholder

        # Extract task description
        source_row = row.get("source_row", {}) or {}
        task_desc = source_row.get("original_prompt") or None
        task_source = source_row.get("task_source") or None

        # Extract metrics from source usage dict (AD7: don't fabricate)
        usage = row.get("usage", {}) or {}
        metrics = Metrics(
            total_steps=len(steps),
            total_input_tokens=usage.get("prompt_tokens", 0) or 0,
            total_output_tokens=usage.get("completion_tokens", 0) or 0,
            estimated_cost_usd=usage.get("estimated_cost_usd") or None,
        )

        # Extract timestamp
        timestamp = row_metadata.get("timestamp") or None

        # Build session_id with revision for dedup safety (FIX-7)
        session_id = f"{dataset_id}:{subset}:{revision_short}:{index}"

        # P1: Infer outcome from completed/partial fields + rl_get_results observations.
        # completed=True means the agent finished normally; weaker than git-commit ground truth.
        completed = row.get("completed")
        partial = row.get("partial")

        # Scan observations for RL environment reward signal (strongest runtime signal).
        # Match by tool_call_id: collect IDs for rl_get_results tool calls first,
        # then look for observations linked to those calls.
        rl_tool_call_ids: set[str] = {
            tc.tool_call_id
            for step in steps
            for tc in step.tool_calls
            if tc.tool_name == "rl_get_results"
        }
        rl_reward: float | None = None
        if rl_tool_call_ids:
            for step in steps:
                for obs in step.observations:
                    if obs.content and obs.source_call_id in rl_tool_call_ids:
                        try:
                            data = json.loads(obs.content)
                            if isinstance(data, dict) and "reward" in data:
                                rl_reward = float(data["reward"])
                        except (json.JSONDecodeError, ValueError, TypeError):
                            pass

        if rl_reward is not None:
            # Ground truth from RL environment: use "derived" confidence.
            # reward > 0: success; reward == 0: task not completed (abandoned);
            # reward < 0: explicit failure signal from environment.
            if rl_reward > 0:
                terminal_state = "goal_reached"
                success = True
            elif rl_reward < 0:
                terminal_state = "error"
                success = False
            else:
                terminal_state = "abandoned"
                success = False
            outcome = Outcome(
                success=success,
                signal_source="rl_environment",
                signal_confidence="derived",
                terminal_state=terminal_state,
                reward=rl_reward,
                reward_source="rl_environment",
                description=f"RL environment reward: {rl_reward}",
            )
        elif completed is True and not partial:
            outcome = Outcome(
                success=True,
                signal_source="source_metadata",
                signal_confidence="inferred",
                terminal_state="goal_reached",
                description="Agent completed task (source completed=True, partial=False)",
            )
        elif completed is False:
            outcome = Outcome(
                success=False,
                signal_source="source_metadata",
                signal_confidence="inferred",
                terminal_state="abandoned",
                description="Agent did not complete task (source completed=False)",
            )
        else:
            # partial=True or unknown: ambiguous, don't overclaim terminal state.
            outcome = Outcome(
                success=None,
                terminal_state="interrupted" if partial else None,
            )

        # P2: Propagate session model to every agent step
        for step in steps:
            if step.role == "agent" and not step.model:
                step.model = model

        record = TraceRecord(
            trace_id=str(uuid.uuid4()),
            session_id=session_id,
            timestamp_start=timestamp,
            execution_context="runtime",
            task=Task(description=task_desc, source=task_source),
            agent=Agent(name="hermes-agent", model=model),
            system_prompts=system_prompts,
            tool_definitions=[],
            steps=steps,
            outcome=outcome,
            metrics=metrics,
            metadata=metadata,
        )
        return record

    # ------------------------------------------------------------------
    # XML parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def parse_thinking(text: str) -> tuple[str, str | None]:
        """Extract <think>...</think>. Returns (cleaned_text, thinking).

        P5: Empty think blocks (Hermes injects <think>\\n</think>\\n when no
        reasoning exists) are treated as None, not empty string.
        """
        if len(text) > _MAX_CONTENT_LEN:
            text = text[:_MAX_CONTENT_LEN]
        matches = _THINK_RE.findall(text)
        if not matches:
            return text, None
        # Join all think blocks; empty blocks (Hermes injects them) are dropped
        thinking = " ".join(m.strip() for m in matches if m.strip()) or None
        cleaned = _THINK_RE.sub("", text).strip()
        return cleaned, thinking

    @staticmethod
    def parse_tool_calls(text: str, step_index: int) -> tuple[str, list[ToolCall]]:
        """Extract <tool_call>JSON</tool_call> blocks.

        Returns (cleaned_text, tool_calls).
        Skips malformed JSON with a warning.
        """
        if len(text) > _MAX_CONTENT_LEN:
            text = text[:_MAX_CONTENT_LEN]
        tool_calls: list[ToolCall] = []
        for i, match in enumerate(_TOOL_CALL_RE.finditer(text)):
            raw = match.group(1).strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    "Skipping malformed tool_call JSON at step %d, block %d", step_index, i,
                )
                continue
            name = data.get("name", "unknown")
            args = data.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw": args}
            call_id = data.get("tool_call_id") or f"tc_{step_index}_{i}"
            tool_calls.append(ToolCall(
                tool_call_id=call_id,
                tool_name=name,
                input=args if isinstance(args, dict) else {},
            ))
        cleaned = _TOOL_CALL_RE.sub("", text).strip()
        return cleaned, tool_calls

    @staticmethod
    def parse_tool_responses(text: str) -> list[Observation]:
        """Extract <tool_response>JSON</tool_response> blocks.

        Returns Observations. Unlinked responses get error='unlinked_response'.
        """
        if len(text) > _MAX_CONTENT_LEN:
            text = text[:_MAX_CONTENT_LEN]
        observations: list[Observation] = []
        for match in _TOOL_RESPONSE_RE.finditer(text):
            raw = match.group(1).strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # Try treating the whole content as plain text
                observations.append(Observation(
                    source_call_id="unknown",
                    content=raw[:2000] if raw else None,
                    error="unlinked_response",
                ))
                continue
            call_id = data.get("tool_call_id")
            content = data.get("content")
            if isinstance(content, (dict, list)):
                content = json.dumps(content)
            elif content is not None:
                content = str(content)
            # P3: Generate output_summary (truncated preview)
            summary = (content[:200] if content else None)
            observations.append(Observation(
                source_call_id=call_id or "unknown",
                content=content,
                output_summary=summary,
                error=None if call_id else "unlinked_response",
            ))
        return observations

    # ------------------------------------------------------------------
    # Conversation -> Steps conversion
    # ------------------------------------------------------------------

    def _conversations_to_steps(
        self, conversations: list[dict],
    ) -> tuple[list[Step], dict[str, str], int]:
        """Convert Hermes conversations to Steps.

        FIX-4: Tool responses are folded onto the preceding assistant step
        as observations, not separate steps. This matches enrichment expectations
        (attribution.py and git_signals.py look at same-step observations).

        Returns (steps, system_prompts, linkage_failures).
        """
        steps: list[Step] = []
        system_prompts: dict[str, str] = {}
        linkage_failures = 0
        step_index = 0
        current_system_hash: str | None = None

        # Collect pending tool responses to fold onto the last assistant step
        pending_observations: list[Observation] = []

        for msg in conversations:
            # Support both standard (role/content) and ShareGPT (from/value) keys
            role = (msg.get("role") or msg.get("from", "")).lower()
            content = (msg.get("content") or msg.get("value", "")) or ""

            # Normalize ShareGPT role names
            if role in ("human", "user"):
                role = "user"
            elif role in ("gpt", "assistant"):
                role = "assistant"

            if role == "system":
                prompt_hash = hashlib.md5(
                    content.encode(), usedforsecurity=False,
                ).hexdigest()[:12]
                system_prompts[prompt_hash] = content
                current_system_hash = prompt_hash
                continue

            if role == "user":
                # Flush any pending observations onto the last assistant step
                if pending_observations and steps:
                    last = steps[-1]
                    last.observations.extend(pending_observations)
                    pending_observations = []

                steps.append(Step(
                    step_index=step_index,
                    role="user",
                    content=content,
                ))
                step_index += 1
                continue

            if role == "assistant":
                # Flush pending observations onto previous assistant step
                if pending_observations and steps:
                    last = steps[-1]
                    last.observations.extend(pending_observations)
                    pending_observations = []

                # Parse thinking, tool calls
                text, thinking = self.parse_thinking(content)
                text, tool_calls = self.parse_tool_calls(text, step_index)

                # Normalize tool calls
                normalized_calls = []
                for tc in tool_calls:
                    canonical, mapped_args, _ = self._normalize_tool_call(
                        tc.tool_name, tc.input,
                    )
                    normalized_calls.append(ToolCall(
                        tool_call_id=tc.tool_call_id,
                        tool_name=canonical,
                        input=mapped_args,
                    ))

                steps.append(Step(
                    step_index=step_index,
                    role="agent",
                    content=text if text else None,
                    reasoning_content=thinking,
                    system_prompt_hash=current_system_hash,
                    tool_calls=normalized_calls,
                    observations=[],  # will be populated from tool messages
                ))
                step_index += 1
                continue

            if role == "tool":
                # Parse tool responses and queue them for the last assistant step
                observations = self.parse_tool_responses(content)
                if not observations:
                    # Plain text tool response (no XML wrapper)
                    observations = [Observation(
                        source_call_id="unknown",
                        content=content[:2000] if content else None,
                        error="unlinked_response",
                    )]

                # Link observations to tool calls by propagating IDs.
                # Hermes format: <tool_call> often lacks tool_call_id, but
                # <tool_response> has it. Match by position and update the
                # tool call's ID to match the response's ID.
                if steps:
                    last_agent = None
                    for s in reversed(steps):
                        if s.role == "agent":
                            last_agent = s
                            break
                    if last_agent:
                        # Count observations already folded OR still pending from
                        # previous tool messages for this same agent step.
                        existing_obs = len(last_agent.observations) + len(pending_observations)
                        for obs_idx, obs in enumerate(observations):
                            tc_idx = existing_obs + obs_idx
                            if (
                                obs.source_call_id != "unknown"
                                and tc_idx < len(last_agent.tool_calls)
                            ):
                                # Propagate the response's real ID to the tool call
                                last_agent.tool_calls[tc_idx] = ToolCall(
                                    tool_call_id=obs.source_call_id,
                                    tool_name=last_agent.tool_calls[tc_idx].tool_name,
                                    input=last_agent.tool_calls[tc_idx].input,
                                    duration_ms=last_agent.tool_calls[tc_idx].duration_ms,
                                )
                            elif obs.source_call_id == "unknown" and tc_idx < len(last_agent.tool_calls):
                                # No ID in response, use the synthetic one
                                obs.source_call_id = last_agent.tool_calls[tc_idx].tool_call_id
                            else:
                                linkage_failures += 1

                pending_observations.extend(observations)
                continue

        # Flush any remaining observations
        if pending_observations and steps:
            last = steps[-1]
            last.observations.extend(pending_observations)

        return steps, system_prompts, linkage_failures

    def _normalize_tool_call(
        self, name: str, args: dict,
    ) -> tuple[str, dict, str]:
        """Normalize tool name and argument keys.

        Arg mapping is looked up by ORIGINAL name before name mapping.
        Returns (canonical_name, mapped_args, original_name).
        """
        original_name = name

        # Map argument keys (by original name, before renaming)
        arg_map = ARG_NAME_MAP.get(name, {})
        mapped_args = {}
        for k, v in args.items():
            new_key = arg_map.get(k, k)
            mapped_args[new_key] = v

        # Map tool name
        canonical = TOOL_NAME_MAP.get(name, name)
        if (
            canonical == name
            and name not in TOOL_NAME_MAP.values()
            and name not in KNOWN_PASSTHROUGH_TOOLS
        ):
            logger.info("Unmapped tool name: %s", name)

        return canonical, mapped_args, original_name
