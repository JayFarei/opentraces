"""Normalize skill invocation evidence from captured traces."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from opentraces_schema import TraceRecord

_SKILL_MD = "SKILL.md"
_SKILL_PATH_BOUNDARY_CHARS = frozenset(" \t\r\n\"'`$<>|")
_INVALID_SKILL_NAME_CHARS = frozenset("*?[]{}")


@dataclass(frozen=True)
class SkillInvocation:
    skill_name: str
    source: str
    step_index: int | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    metadata_index: int | None = None
    command_name: str | None = None
    args: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


def detect_skill_invocations(record: TraceRecord) -> list[SkillInvocation]:
    """Return skill invocations across tool-call and metadata capture shapes.

    Claude Code currently captures slash-command skills in trace metadata with
    ``name``. Codex CLI captures skill tool calls with ``skill_name`` and may
    also expose the same evidence as normalized tool metadata. This helper is
    the core ingestion point so consumers do not need per-agent skill ledgers.
    """

    invocations: list[SkillInvocation] = []
    seen_tool_call_ids: set[str] = set()
    metadata_items = _metadata_skill_invocations(record)
    metadata_by_tool_call_id = {
        tool_call_id: item
        for item in metadata_items
        if (tool_call_id := _optional_str(item.get("tool_call_id"))) is not None
    }

    for step in record.steps:
        for call in step.tool_calls or []:
            tool_input = _mapping(getattr(call, "input", None))
            skill_name = _skill_name(tool_input)
            source = tool_input.get("source") or "tool_call"
            evidence: dict[str, Any] = {"input": tool_input, "metadata": {}}
            if skill_name is None or not _looks_like_skill_call(call.tool_name, tool_input):
                body_read = _skill_body_read(tool_input)
                if body_read is None:
                    continue
                skill_name, skill_path = body_read
                source = _skill_body_read_source(getattr(record.agent, "name", None))
                evidence = {"input": tool_input, "skill_path": skill_path}
            tool_call_id = str(call.tool_call_id)
            metadata = metadata_by_tool_call_id.get(tool_call_id, {})
            if metadata:
                source = metadata.get("source") or source
                evidence["metadata"] = metadata
            seen_tool_call_ids.add(tool_call_id)
            invocations.append(
                SkillInvocation(
                    skill_name=skill_name,
                    source=str(source),
                    step_index=step.step_index,
                    tool_call_id=tool_call_id,
                    tool_name=call.tool_name,
                    args=_string_or_empty(tool_input.get("args") or metadata.get("args")),
                    evidence=evidence,
                )
            )

    seen_metadata: set[tuple[str, str, str | None, int | None, str]] = set()
    for idx, item in enumerate(metadata_items):
        skill_name = _skill_name(item)
        if skill_name is None:
            continue
        tool_call_id = _optional_str(item.get("tool_call_id"))
        if tool_call_id in seen_tool_call_ids:
            continue
        source = str(item.get("source") or "metadata")
        args = _string_or_empty(item.get("args"))
        step_index = _optional_int(item.get("step_index"))
        key = (skill_name, source, tool_call_id, step_index, args)
        if key in seen_metadata:
            continue
        seen_metadata.add(key)
        invocations.append(
            SkillInvocation(
                skill_name=skill_name,
                source=source,
                step_index=step_index,
                tool_call_id=tool_call_id,
                metadata_index=idx,
                command_name=_optional_str(item.get("command_name")),
                args=args,
                evidence=item,
            )
        )

    return invocations


def trace_skill_names(record: TraceRecord) -> list[str]:
    return sorted({invocation.skill_name for invocation in detect_skill_invocations(record)})


def _metadata_skill_invocations(record: TraceRecord) -> list[dict[str, Any]]:
    raw = record.metadata.get("skill_invocations")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _looks_like_skill_call(tool_name: str | None, tool_input: dict[str, Any]) -> bool:
    normalized = (tool_name or "").lower()
    return "skill" in normalized or "skill_name" in tool_input


def _skill_name(data: dict[str, Any]) -> str | None:
    value = data.get("name") or data.get("skill") or data.get("skill_name")
    if value is None:
        return None
    return _normalize_skill_name(value)


def _skill_body_read(tool_input: dict[str, Any]) -> tuple[str, str] | None:
    for value in _walk_string_values(tool_input):
        skill_path = _skill_md_path(value)
        if skill_path is None:
            continue
        skill_name = _skill_name_from_skill_md_path(skill_path)
        if skill_name is None:
            continue
        return skill_name, skill_path
    return None


def _walk_string_values(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_string_values(item)


def _skill_md_path(value: str) -> str | None:
    start_at = 0
    while True:
        marker_start = value.find(_SKILL_MD, start_at)
        if marker_start == -1:
            return None

        path_start = marker_start
        while (
            path_start > 0
            and value[path_start - 1] not in _SKILL_PATH_BOUNDARY_CHARS
        ):
            path_start -= 1
        path_end = marker_start + len(_SKILL_MD)
        # The match must terminate at a path boundary (or end-of-string); otherwise
        # "SKILL.mdx", "SKILL.md.bak", "SKILL.mdEXTRA" would be truncated to a phantom
        # "SKILL.md" and yield a false-positive skill invocation.
        if path_end < len(value) and value[path_end] not in _SKILL_PATH_BOUNDARY_CHARS:
            start_at = path_end
            continue
        path = value[path_start:path_end]
        if path.startswith("/") and path.endswith(f"/{_SKILL_MD}") and "..." not in path:
            return path
        start_at = path_end


def _skill_body_read_source(agent_name: str | None) -> str:
    normalized = (agent_name or "").lower()
    if normalized == "codex-cli":
        return "codex_cli_skill_body_read"
    if normalized == "pi":
        return "pi_skill_body_read"
    return "skill_body_read"


def _skill_name_from_skill_md_path(value: str) -> str | None:
    try:
        path = Path(value)
    except (OSError, ValueError):
        return None
    parts = path.parts
    if not parts or path.name != "SKILL.md":
        return None
    try:
        skills_index = parts.index("skills")
    except ValueError:
        return None
    if not any(part in {".agents", ".claude", ".codex"} for part in parts[:skills_index]):
        return None
    return _normalize_skill_name(path.parent.name, reject_reserved=True)


def _normalize_skill_name(value: Any, *, reject_reserved: bool = False) -> str | None:
    name = str(value).strip().lstrip("/")
    if not name:
        return None
    if reject_reserved and name == "skills":
        return None
    if name in {".", ".."}:  # path-traversal segments are not skill names
        return None
    if "/" in name or "\\" in name:
        return None
    if any(char in name for char in _INVALID_SKILL_NAME_CHARS):
        return None
    # Reject control/format chars (Unicode category C*), which covers C0/C1 control
    # codes and bidi/zero-width overrides (e.g. U+202E) that would corrupt the name
    # as it flows into TraceUnit facets and the index text.
    if any(unicodedata.category(char).startswith("C") for char in name):
        return None
    return name


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _string_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
