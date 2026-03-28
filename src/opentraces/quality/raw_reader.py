"""Independent raw session JSONL reader for quality assessment.

Reads Claude Code session files directly with json.loads, producing a
lightweight RawSessionSummary. Deliberately avoids importing ClaudeCodeParser
to prevent circular validation (using the parser to validate the parser).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class RawSessionSummary:
    """Lightweight summary of a raw Claude Code session JSONL file.
    Built independently of ClaudeCodeParser to avoid circular validation."""

    total_lines: int = 0
    corrupted_lines: int = 0
    user_messages: int = 0
    assistant_messages: int = 0
    tool_use_blocks: int = 0
    tool_result_blocks: int = 0
    thinking_blocks_total: int = 0
    thinking_blocks_with_content: int = 0
    usage_entries: int = 0
    total_content_chars: int = 0
    timestamps: int = 0  # count of lines with timestamps
    subagent_tool_calls: int = 0  # Agent/Task tool_use blocks
    models_seen: list[str] = field(default_factory=list)
    system_prompt_count: int = 0


def read_raw_session(path: Path | str) -> RawSessionSummary:
    """Read a raw Claude Code session JSONL file and produce summary counts.

    Each line is expected to be a JSON object with at minimum a ``type`` field.
    Lines that fail to parse are counted as corrupted and skipped.
    """
    path = Path(path)
    summary = RawSessionSummary()
    models_seen_set: set[str] = set()

    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                summary.total_lines += 1
                raw_line = raw_line.strip()
                if not raw_line:
                    continue

                try:
                    line = json.loads(raw_line)
                except json.JSONDecodeError:
                    summary.corrupted_lines += 1
                    continue

                if not isinstance(line, dict):
                    summary.corrupted_lines += 1
                    continue

                _process_line(line, summary, models_seen_set)

    except OSError as e:
        logger.error(f"Cannot read {path}: {e}")

    summary.models_seen = sorted(models_seen_set)
    return summary


def _process_line(
    line: dict,
    summary: RawSessionSummary,
    models_seen_set: set[str],
) -> None:
    """Process a single parsed JSONL line and update summary counts."""
    line_type = line.get("type")

    # Timestamp counting
    if line.get("timestamp"):
        summary.timestamps += 1

    # Message type counting
    if line_type == "user":
        summary.user_messages += 1
    elif line_type == "assistant":
        summary.assistant_messages += 1

    # queue-operation lines carry model info and system prompts
    if line_type == "queue-operation":
        _process_queue_operation(line, summary, models_seen_set)
        return

    # Only user and assistant lines have message.content to inspect
    if line_type not in ("user", "assistant"):
        return

    msg = line.get("message")
    if not isinstance(msg, dict):
        return

    # Extract model from message
    model = msg.get("model")
    if model and isinstance(model, str):
        models_seen_set.add(model)

    # Count usage entries (non-zero tokens)
    usage = msg.get("usage")
    if isinstance(usage, dict):
        input_tokens = usage.get("input_tokens", 0) or 0
        output_tokens = usage.get("output_tokens", 0) or 0
        if input_tokens > 0 or output_tokens > 0:
            summary.usage_entries += 1

    # Process content blocks
    content = msg.get("content")
    if isinstance(content, str):
        summary.total_content_chars += len(content)
        return

    if not isinstance(content, list):
        return

    for block in content:
        if not isinstance(block, dict):
            continue

        block_type = block.get("type")

        if block_type == "text":
            text = block.get("text", "")
            if isinstance(text, str):
                summary.total_content_chars += len(text)

        elif block_type == "tool_use":
            summary.tool_use_blocks += 1
            tool_name = block.get("name", "")
            if tool_name in ("Agent", "Task"):
                summary.subagent_tool_calls += 1

        elif block_type == "tool_result":
            summary.tool_result_blocks += 1
            # Count content chars from tool result content
            result_content = block.get("content", "")
            if isinstance(result_content, str):
                summary.total_content_chars += len(result_content)
            elif isinstance(result_content, list):
                for sub_block in result_content:
                    if isinstance(sub_block, dict) and sub_block.get("type") == "text":
                        text = sub_block.get("text", "")
                        if isinstance(text, str):
                            summary.total_content_chars += len(text)

        elif block_type == "thinking":
            summary.thinking_blocks_total += 1
            thinking_text = block.get("thinking", "")
            if isinstance(thinking_text, str) and thinking_text.strip():
                summary.thinking_blocks_with_content += 1


def _process_queue_operation(
    line: dict,
    summary: RawSessionSummary,
    models_seen_set: set[str],
) -> None:
    """Extract model and system prompt info from queue-operation lines."""
    content = line.get("content")
    if not isinstance(content, str):
        return

    try:
        inner = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return

    if not isinstance(inner, dict):
        return

    model = inner.get("model")
    if model and isinstance(model, str):
        models_seen_set.add(model)

    if "system" in inner:
        summary.system_prompt_count += 1
