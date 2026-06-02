"""Parser for Codex CLI rollout JSONL sessions."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from opentraces_schema import (
    Agent,
    Environment,
    Metrics,
    Observation,
    Outcome,
    SecurityMetadata,
    Step,
    Task,
    ToolCall,
    TraceRecord,
    VCS,
)

from ...core.trails.contract import snapshot_resume_trace_metadata
from ...quality.parse_gate import meets_quality_threshold
from .context_tree_capture import (
    emit_context_tree_events_from_record as _emit_context_tree_events_from_record,
)
from .sessions import discover_session_files, session_relpath

logger = logging.getLogger(__name__)

CORRUPTED_LINE_THRESHOLD = 0.05
MAX_CONTENT_CHARS = 10000
MAX_SUMMARY_CHARS = 500
_SKILL_MD = "SKILL.md"
_SKILL_PATH_BOUNDARY_CHARS = frozenset(" \t\r\n\"'`$<>|")
_INVALID_SKILL_NAME_CHARS = frozenset("*?[]{}")


class CodexCliParser:
    """Minimal parser for Codex CLI ``rollout-*.jsonl`` files.

    Handles ordinary user/assistant/tool loops from current Codex CLI
    envelopes plus opentraces hook sidecars. Advanced parity work such as
    cross-file sub-agent linking and Context Tree projection is deliberately
    surfaced as limitations rather than inferred here.
    """

    agent_name = "codex-cli"
    emit_context_tree_events_from_record = staticmethod(_emit_context_tree_events_from_record)

    def __init__(self) -> None:
        self.step_anchors: dict[int, dict[str, Any]] = {}
        self._step_anchor_rows: list[dict[str, Any]] = []

    def discover_sessions(self, root: str | Path | None = None) -> Iterator[Path]:
        """Yield Codex CLI rollout files under ``root`` or the real HOME."""
        yield from discover_session_files(root=root)

    def discover_project_sessions(self, project_dir: Path) -> Iterator[Path]:
        """Yield Codex rollouts whose recorded cwd is inside ``project_dir``."""
        project = Path(project_dir).expanduser().resolve()
        for session_path in self.discover_sessions():
            session_meta, turn_context = _read_session_header(session_path)
            raw_cwd = session_meta.get("cwd") or turn_context.get("cwd")
            if not isinstance(raw_cwd, str) or not raw_cwd:
                continue
            try:
                cwd = Path(raw_cwd).expanduser().resolve()
            except OSError:
                continue
            if cwd == project or project in cwd.parents:
                yield session_path

    def session_id_from_path(self, session_path: Path) -> str:
        """Return the native Codex thread id when the rollout header has one."""
        session_meta, _turn_context = _read_session_header(Path(session_path))
        session_id = session_meta.get("id")
        if isinstance(session_id, str) and session_id:
            return session_id
        return _session_id_from_path(Path(session_path))

    def parse_session(self, session_path: Path, byte_offset: int = 0) -> TraceRecord | None:
        """Parse one Codex CLI rollout JSONL file into a ``TraceRecord``."""
        self.step_anchors = {}
        self._step_anchor_rows = []

        lines = self._read_lines(session_path, byte_offset=byte_offset)
        if lines is None:
            return None

        metadata = self._extract_metadata(lines, session_path)
        _merge_sidecar_hook_metadata(metadata, session_path)
        output_map = self._build_output_map(lines)
        steps = self._parse_steps(lines, output_map, session_path)
        if not steps:
            return None

        for i, step in enumerate(steps, 1):
            step.step_index = i

        self.step_anchors = {
            step.step_index: anchor
            for step, anchor in zip(steps, self._step_anchor_rows)
        }

        task_description = next((s.content for s in steps if s.role == "user" and s.content), None)
        metrics = self._build_metrics(metadata, steps)
        provider = metadata.get("model_provider")
        model = _normalize_model_id(metadata.get("model"), provider=provider)

        trace_metadata: dict[str, Any] = {
            "source": "codex_cli_rollout",
            "trace_trails": snapshot_resume_trace_metadata(),
            "session_file": session_relpath(session_path),
            "raw_event_counts": metadata.get("raw_event_counts", {}),
            "response_item_counts": metadata.get("response_item_counts", {}),
            "function_names": metadata.get("function_names", {}),
            "codex_cli": metadata.get("codex_cli", {}),
            "normalized_tool_calls": _normalized_tool_calls(steps),
            "capture_limitations": _capture_limitations(metadata),
        }
        skill_invocations = _skill_invocations(steps)
        if skill_invocations:
            trace_metadata["skill_invocations"] = skill_invocations
        subagent_ref = _subagent_session_ref(metadata)
        if subagent_ref:
            trace_metadata["subagent_session"] = subagent_ref
        for key in (
            "hook_git_final",
            "hook_pre_tool_use",
            "hook_post_tool_use",
            "hook_stop",
            "codex_cli_hook_sidecar",
            "permission_requests",
            "compaction_events",
            "codex_cli_lifecycle_hooks",
            "is_compacted",
            "permission_mode",
        ):
            if key in metadata:
                trace_metadata[key] = metadata[key]

        record = TraceRecord(
            trace_id=str(uuid.uuid4()),
            session_id=metadata.get("session_id") or session_path.stem,
            timestamp_start=metadata.get("timestamp_start"),
            timestamp_end=metadata.get("timestamp_end"),
            execution_context="devtime",
            task=Task(description=task_description, source="user_prompt" if task_description else None),
            agent=Agent(
                name=self.agent_name,
                version=metadata.get("cli_version"),
                model=model,
            ),
            environment=Environment(
                vcs=_vcs_from_git(metadata.get("git")),
                language_ecosystem=[],
            ),
            steps=steps,
            outcome=Outcome(success=None, signal_source="source_metadata", signal_confidence="inferred"),
            metrics=metrics,
            security=SecurityMetadata(),
            metadata=trace_metadata,
        )

        if not meets_quality_threshold(record):
            logger.debug("Codex session %s below quality threshold", session_path)
            return None

        return record

    def _read_lines(self, path: Path, byte_offset: int = 0) -> list[dict[str, Any]] | None:
        lines: list[dict[str, Any]] = []
        errors = 0
        total = 0

        try:
            with open(path, "rb") as f:
                if byte_offset > 0:
                    f.seek(0, 2)
                    file_size = f.tell()
                    byte_offset = min(byte_offset, file_size)
                    if byte_offset > 0:
                        f.seek(byte_offset - 1)
                        previous = f.read(1)
                        f.seek(byte_offset)
                        if previous not in (b"\n", b"\r"):
                            f.readline()
                for raw_line in f:
                    total += 1
                    try:
                        text = raw_line.decode("utf-8").strip()
                    except UnicodeDecodeError:
                        errors += 1
                        continue
                    if not text:
                        continue
                    try:
                        lines.append(json.loads(text))
                    except json.JSONDecodeError:
                        errors += 1
        except OSError as exc:
            logger.error("Cannot read Codex session %s: %s", path, exc)
            return None

        if total > 0 and errors / total > CORRUPTED_LINE_THRESHOLD:
            logger.error(
                "Too many corrupted lines in %s: %d/%d",
                path,
                errors,
                total,
            )
            return None

        for i, line in enumerate(lines):
            line["_original_line_no"] = i
        return lines

    def _extract_metadata(self, lines: list[dict[str, Any]], session_path: Path) -> dict[str, Any]:
        raw_event_counts: Counter[str] = Counter()
        response_item_counts: Counter[str] = Counter()
        function_names: Counter[str] = Counter()
        session_meta: dict[str, Any] = {}
        turn_context: dict[str, Any] = {}
        last_token_usage: dict[str, Any] = {}
        first_timestamp: str | None = None
        last_timestamp: str | None = None

        for line in lines:
            event_type, payload = _unwrap(line)
            raw_event_counts[event_type] += 1

            ts = _coerce_timestamp(line.get("timestamp") or payload.get("timestamp"))
            if ts and first_timestamp is None:
                first_timestamp = ts
            if ts:
                last_timestamp = ts

            if event_type == "session_meta" and isinstance(payload, dict) and not session_meta:
                session_meta = payload
            elif event_type == "turn_context" and isinstance(payload, dict):
                turn_context = payload
            elif event_type == "event_msg" and isinstance(payload, dict):
                if payload.get("type") == "token_count":
                    info = payload.get("info") or {}
                    if isinstance(info, dict):
                        last_token_usage = info.get("total_token_usage") or {}
            elif event_type == "response_item" and isinstance(payload, dict):
                item_type = str(payload.get("type") or "unknown")
                response_item_counts[item_type] += 1
                if item_type in {"function_call", "custom_tool_call"} and payload.get("name"):
                    function_names[str(payload["name"])] += 1

        git = session_meta.get("git") if isinstance(session_meta.get("git"), dict) else {}
        codex_meta: dict[str, Any] = {
            "originator": session_meta.get("originator"),
            "thread_source": session_meta.get("thread_source"),
            "agent_nickname": session_meta.get("agent_nickname"),
            "agent_role": session_meta.get("agent_role"),
            "source": _compact_source(session_meta.get("source")),
            "model_provider": session_meta.get("model_provider"),
            "turn_id": turn_context.get("turn_id"),
            "cwd": session_meta.get("cwd") or turn_context.get("cwd"),
            "approval_policy": turn_context.get("approval_policy"),
            "effort": turn_context.get("effort"),
            "sandbox_policy": _policy_type(turn_context.get("sandbox_policy")),
            "permission_profile": _policy_type(turn_context.get("permission_profile")),
        }

        base_instructions = session_meta.get("base_instructions")
        if isinstance(base_instructions, dict):
            codex_meta["base_instructions"] = _text_ref(base_instructions.get("text"))
        elif isinstance(base_instructions, str):
            codex_meta["base_instructions"] = _text_ref(base_instructions)

        if isinstance(turn_context.get("user_instructions"), str):
            codex_meta["turn_user_instructions"] = _text_ref(turn_context.get("user_instructions"))

        return {
            "session_id": session_meta.get("id") or _session_id_from_path(session_path),
            "timestamp_start": _coerce_timestamp(session_meta.get("timestamp")) or first_timestamp,
            "timestamp_end": last_timestamp,
            "cli_version": session_meta.get("cli_version"),
            "model": session_meta.get("model") or turn_context.get("model"),
            "model_provider": session_meta.get("model_provider"),
            "git": git,
            "raw_event_counts": dict(raw_event_counts),
            "response_item_counts": dict(response_item_counts),
            "function_names": dict(function_names),
            "last_token_usage": last_token_usage,
            "codex_cli": {k: v for k, v in codex_meta.items() if v is not None},
        }

    def _build_output_map(self, lines: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        outputs: dict[str, dict[str, Any]] = {}
        for line in lines:
            event_type, payload = _unwrap(line)
            if event_type != "response_item" or not isinstance(payload, dict):
                continue
            if payload.get("type") not in {"function_call_output", "custom_tool_call_output"}:
                continue
            call_id = payload.get("call_id")
            if not call_id:
                continue
            outputs[str(call_id)] = {
                "output": payload.get("output"),
                "line_no": line.get("_original_line_no"),
            }
        return outputs

    def _parse_steps(
        self,
        lines: list[dict[str, Any]],
        output_map: dict[str, dict[str, Any]],
        session_path: Path,
    ) -> list[Step]:
        steps: list[Step] = []
        current_agent: dict[str, Any] | None = None
        pending_outputs: set[str] = set()
        observed_outputs: set[str] = set()
        has_turn_context = any(_unwrap(line)[0] == "turn_context" for line in lines)
        seen_turn_context = not has_turn_context
        session_file_relpath = session_relpath(session_path)

        def flush_agent() -> None:
            nonlocal current_agent, pending_outputs, observed_outputs
            if not current_agent:
                return
            if not (
                current_agent["content"]
                or current_agent["reasoning"]
                or current_agent["tool_calls"]
            ):
                current_agent = None
                pending_outputs = set()
                observed_outputs = set()
                return
            step = Step(
                step_index=len(steps) + 1,
                role="agent",
                content=_join_text(current_agent["content"]) or None,
                reasoning_content=_join_text(current_agent["reasoning"]) or None,
                model=current_agent.get("model"),
                agent_role="main",
                call_type="main",
                tools_available=[tc.tool_name for tc in current_agent["tool_calls"]],
                tool_calls=current_agent["tool_calls"],
                observations=current_agent["observations"],
                timestamp=current_agent.get("timestamp"),
            )
            steps.append(step)
            self._step_anchor_rows.append(current_agent["anchor"])
            current_agent = None
            pending_outputs = set()
            observed_outputs = set()

        def ensure_agent(line: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal current_agent
            if current_agent is None:
                current_agent = {
                    "content": [],
                    "reasoning": [],
                    "tool_calls": [],
                    "observations": [],
                    "timestamp": _coerce_timestamp(line.get("timestamp") or payload.get("timestamp")),
                    "model": _normalize_model_id(payload.get("model")),
                    "anchor": {
                        "entry_uuid": payload.get("id") or payload.get("call_id"),
                        "line_no": line.get("_original_line_no"),
                        "session_file_relpath": session_file_relpath,
                    },
                }
            return current_agent

        for line in lines:
            event_type, payload = _unwrap(line)
            if event_type == "turn_context":
                seen_turn_context = True
                continue
            if event_type != "response_item" or not isinstance(payload, dict):
                continue
            if not seen_turn_context:
                continue

            item_type = payload.get("type")
            if item_type == "message":
                role = payload.get("role")
                text = _content_text(payload.get("content"))
                if role == "user":
                    flush_agent()
                    if text:
                        steps.append(Step(
                            step_index=len(steps) + 1,
                            role="user",
                            content=_truncate(text),
                            timestamp=_coerce_timestamp(line.get("timestamp") or payload.get("timestamp")),
                            call_type="main",
                        ))
                        self._step_anchor_rows.append({
                            "entry_uuid": payload.get("id"),
                            "line_no": line.get("_original_line_no"),
                            "session_file_relpath": session_file_relpath,
                        })
                elif role == "assistant":
                    agent = ensure_agent(line, payload)
                    if text:
                        agent["content"].append(text)
                else:
                    continue

            elif item_type == "reasoning":
                agent = ensure_agent(line, payload)
                reasoning = _reasoning_text(payload)
                if reasoning:
                    agent["reasoning"].append(reasoning)

            elif item_type in {"function_call", "custom_tool_call"}:
                agent = ensure_agent(line, payload)
                call_id = str(payload.get("call_id") or uuid.uuid4())
                tool_name = str(payload.get("name") or "unknown")
                tool_input = _parse_arguments(
                    payload.get("arguments") if item_type == "function_call" else payload.get("input")
                )
                tool_call = ToolCall(
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    input=tool_input,
                )
                agent["tool_calls"].append(tool_call)
                pending_outputs.add(call_id)

                output = output_map.get(call_id)
                if output is not None:
                    content = output.get("output")
                    agent["observations"].append(Observation(
                        source_call_id=call_id,
                        content=_truncate(content) if content is not None else None,
                        output_summary=_summarize_output(content),
                    ))
                else:
                    agent["observations"].append(Observation(
                        source_call_id=call_id,
                        error="no_result",
                    ))

            elif item_type in {"function_call_output", "custom_tool_call_output"}:
                call_id = payload.get("call_id")
                if call_id:
                    observed_outputs.add(str(call_id))
                if pending_outputs and pending_outputs.issubset(observed_outputs):
                    flush_agent()

        flush_agent()
        return steps

    @staticmethod
    def _build_metrics(metadata: dict[str, Any], steps: list[Step]) -> Metrics:
        usage = metadata.get("last_token_usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        cache_read_tokens = int(usage.get("cached_input_tokens") or 0)
        return Metrics(
            total_steps=len(steps),
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
            total_cache_read_tokens=cache_read_tokens,
        )


def _unwrap(line: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return ``(event_type, payload)`` for current and early fixture envelopes."""
    if isinstance(line.get("type"), str) and isinstance(line.get("payload"), dict):
        return str(line["type"]), line["payload"]
    for key in ("session_meta", "turn_context", "event_msg", "response_item"):
        if isinstance(line.get(key), dict):
            return key, line[key]
    return str(line.get("type") or "unknown"), line


def _read_session_header(path: Path, *, max_lines: int = 64) -> tuple[dict[str, Any], dict[str, Any]]:
    session_meta: dict[str, Any] = {}
    turn_context: dict[str, Any] = {}

    try:
        with open(path, "rb") as f:
            for line_no, raw_line in enumerate(f):
                if line_no >= max_lines:
                    break
                try:
                    row = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(row, dict):
                    continue
                event_type, payload = _unwrap(row)
                if event_type == "session_meta" and isinstance(payload, dict) and not session_meta:
                    session_meta = payload
                elif event_type == "turn_context" and isinstance(payload, dict):
                    turn_context = payload
                if session_meta and turn_context:
                    break
    except OSError:
        return {}, {}

    return session_meta, turn_context


def _merge_sidecar_hook_metadata(metadata: dict[str, Any], session_path: Path) -> None:
    session_id = metadata.get("session_id")
    cwd = (metadata.get("codex_cli") or {}).get("cwd")
    if not isinstance(session_id, str) or not session_id:
        return
    if not isinstance(cwd, str) or not cwd:
        return
    sidecar = Path(cwd) / ".opentraces" / "codex-cli" / "hooks" / f"{_safe_sidecar_name(session_id)}.jsonl"
    if not sidecar.exists():
        return
    metadata["codex_cli_hook_sidecar"] = str(sidecar)
    try:
        rows = sidecar.read_text().splitlines()
    except OSError:
        return
    for raw_line in rows:
        if not raw_line.strip():
            continue
        try:
            line = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(line, dict):
            continue
        _merge_hook_line(metadata, line)


def _safe_sidecar_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", ".", "-"} else "_" for ch in value).strip("._") or "unknown"


def _merge_hook_line(metadata: dict[str, Any], line: dict[str, Any]) -> None:
    if line.get("type") != "opentraces_hook":
        return
    event = line.get("event")
    data = line.get("data") or {}
    if not isinstance(data, dict):
        return

    if event == "Stop":
        git = data.get("git") or {}
        if git:
            metadata["hook_git_final"] = git
        metadata.setdefault("hook_stop", []).append({
            "timestamp": line.get("timestamp"),
            "session_id": data.get("session_id"),
            "turn_id": data.get("turn_id"),
            "agent_type": "main",
            "permission_mode": data.get("permission_mode"),
            "stop_hook_active": data.get("stop_hook_active"),
            "git": git,
            "trail": data.get("trail") or {},
        })
        if not metadata.get("permission_mode") and data.get("permission_mode"):
            metadata["permission_mode"] = data["permission_mode"]
        return

    if event == "PreToolUse":
        tool_use_id = data.get("tool_use_id")
        if tool_use_id:
            metadata.setdefault("hook_pre_tool_use", {})[str(tool_use_id)] = {
                "timestamp": line.get("timestamp"),
                "tool": data.get("tool"),
                "tool_input": data.get("tool_input") or {},
                "trail": data.get("trail") or {},
            }
        return

    if event == "PostToolUse":
        tool_use_id = data.get("tool_use_id")
        if tool_use_id:
            metadata.setdefault("hook_post_tool_use", {})[str(tool_use_id)] = {
                "timestamp": line.get("timestamp"),
                "tool": data.get("tool"),
                "file_path": data.get("file_path"),
                "start_line": data.get("start_line"),
                "end_line": data.get("end_line"),
                "content_hash": data.get("content_hash"),
                "confidence": data.get("confidence"),
                "capture_status": data.get("capture_status"),
                "limitations": data.get("limitations") or [],
                "tool_input": data.get("tool_input") or {},
                "tool_response": data.get("tool_response") or {},
                "trail": data.get("trail") or {},
            }
        return

    if event in {"PreCompact", "PostCompact"}:
        metadata["is_compacted"] = True
        metadata.setdefault("compaction_events", []).append({
            "trigger": data.get("trigger") or "hook",
            "source": "codex_cli_hook",
            "event": event,
            "timestamp": line.get("timestamp"),
        })
        return

    if event == "PermissionRequest":
        metadata.setdefault("permission_requests", []).append({
            "timestamp": line.get("timestamp"),
            "turn_id": data.get("turn_id"),
            "tool": data.get("tool"),
            "tool_input": data.get("tool_input") or {},
            "permission_mode": data.get("permission_mode"),
            "source": "codex_cli_hook",
        })
        return

    if event in {"SessionStart", "UserPromptSubmit"}:
        metadata.setdefault("codex_cli_lifecycle_hooks", []).append({
            "event": event,
            "timestamp": line.get("timestamp"),
            "turn_id": data.get("turn_id"),
            "source": data.get("source"),
        })


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str) and block.get("type") in {
            "input_text",
            "output_text",
            "text",
        }:
            parts.append(text)
    return "\n".join(part for part in parts if part).strip()


def _reasoning_text(payload: dict[str, Any]) -> str | None:
    summary = payload.get("summary")
    if isinstance(summary, list):
        text = _content_text(summary)
        if text:
            return text
    elif isinstance(summary, str) and summary.strip():
        return summary.strip()
    if payload.get("encrypted_content"):
        return "[redacted: Codex CLI recorded encrypted reasoning content]"
    return None


def _parse_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {"raw": _truncate(arguments, limit=MAX_SUMMARY_CHARS)}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return {}


def _join_text(parts: list[str]) -> str:
    return _truncate("\n".join(part for part in parts if part).strip())


def _truncate(value: Any, *, limit: int = MAX_CONTENT_CHARS) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit]


def _summarize_output(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\n", " ")
    if not text:
        return None
    return text[:MAX_SUMMARY_CHARS]


def _normalize_model_id(model: Any, *, provider: Any = None) -> str | None:
    if not isinstance(model, str) or not model:
        return None
    if "/" in model:
        return model
    if isinstance(provider, str) and provider:
        return f"{provider}/{model}"
    return model


def _vcs_from_git(git: Any) -> VCS:
    if not isinstance(git, dict):
        return VCS(type="none")
    if git.get("commit_hash") or git.get("branch") or git.get("repository_url"):
        return VCS(
            type="git",
            base_commit=git.get("commit_hash"),
            branch=git.get("branch"),
        )
    return VCS(type="none")


def _policy_type(value: Any) -> str | None:
    if isinstance(value, dict):
        raw = value.get("type")
        return str(raw) if raw is not None else None
    if isinstance(value, str):
        return value
    return None


def _compact_source(source: Any) -> Any:
    if isinstance(source, str):
        return source
    if not isinstance(source, dict):
        return None
    subagent = source.get("subagent")
    if not isinstance(subagent, dict):
        return {"kind": "object"}
    thread_spawn = subagent.get("thread_spawn")
    if not isinstance(thread_spawn, dict):
        return {"kind": "subagent"}
    return {
        "kind": "subagent",
        "parent_thread_id": thread_spawn.get("parent_thread_id"),
        "depth": thread_spawn.get("depth"),
        "agent_nickname": thread_spawn.get("agent_nickname"),
        "agent_role": thread_spawn.get("agent_role"),
    }


def _text_ref(text: Any) -> dict[str, Any] | None:
    if not isinstance(text, str):
        return None
    return {
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "chars": len(text),
    }


def _normalized_tool_calls(steps: list[Step]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for step in steps:
        for call in step.tool_calls or []:
            entry: dict[str, Any] = {
                "step_index": step.step_index,
                "tool_call_id": call.tool_call_id,
                "raw_tool_name": call.tool_name,
                "tool_kind": _tool_kind(call.tool_name, call.input),
            }
            shell = _shell_metadata(call.input) if entry["tool_kind"] == "shell" else None
            if shell:
                entry["shell"] = shell
            normalized.append(entry)
    return normalized


def _tool_kind(tool_name: str | None, tool_input: dict[str, Any] | None = None) -> str:
    name = (tool_name or "").lower()
    data = tool_input or {}
    if any(part in name for part in ("bash", "shell", "exec", "command", "terminal")):
        return "shell"
    if name in {"apply_patch", "edit", "write"} or any(
        part in name for part in ("apply_patch", "edit_file", "write_file", "patch")
    ):
        return "write"
    if any(part in name for part in ("read", "open_file", "view_file", "cat")):
        return "read"
    if any(part in name for part in ("search", "grep", "rg", "find")):
        return "search"
    if "mcp" in name or data.get("server") or data.get("mcp_server"):
        return "mcp"
    if "permission" in name:
        return "permission"
    if "skill" in name or data.get("skill_name"):
        return "skill"
    if any(part in name for part in ("task", "subagent", "spawn_agent")):
        return "subagent"
    return "tool"


def _shell_metadata(tool_input: dict[str, Any] | None) -> dict[str, Any]:
    data = tool_input or {}
    command = data.get("cmd") or data.get("command") or data.get("script")
    cwd = data.get("cwd") or data.get("workdir") or data.get("working_directory")
    result: dict[str, Any] = {}
    if isinstance(command, str):
        result["command"] = _truncate(command, limit=MAX_SUMMARY_CHARS)
    if isinstance(cwd, str):
        result["cwd"] = cwd
    return result


def _skill_invocations(steps: list[Step]) -> list[dict[str, Any]]:
    invocations: list[dict[str, Any]] = []
    seen: set[tuple[int | None, str, str]] = set()
    for step in steps:
        for call in step.tool_calls or []:
            payload: dict[str, Any] | None = None
            if _tool_kind(call.tool_name, call.input) == "skill":
                skill_name = call.input.get("skill_name") or call.input.get("name")
                if isinstance(skill_name, str) and skill_name.strip():
                    payload = {
                        "step_index": step.step_index,
                        "tool_call_id": call.tool_call_id,
                        "skill_name": skill_name.strip(),
                        "source": "codex_cli_tool_call",
                    }
            if payload is None:
                payload = _skill_invocation_from_skill_body_read(step, call)
            if payload is None:
                continue
            key = (
                payload.get("step_index"),
                str(payload.get("tool_call_id") or ""),
                str(payload.get("skill_name") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            invocations.append(payload)
    return invocations


def _skill_invocation_from_skill_body_read(step: Step, call: ToolCall) -> dict[str, Any] | None:
    """Detect Codex's current skill-use shape: reading ``.../skills/<name>/SKILL.md``."""

    for value in _walk_string_values(call.input):
        skill_path = _skill_md_path(value)
        if skill_path is None:
            continue
        skill_name = _skill_name_from_skill_md_path(skill_path)
        if skill_name is None:
            continue
        return {
            "step_index": step.step_index,
            "tool_call_id": call.tool_call_id,
            "skill_name": skill_name,
            "source": "codex_cli_skill_body_read",
            "skill_path": skill_path,
        }
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
    return _normalize_skill_name(path.parent.name)


def _normalize_skill_name(value: Any) -> str | None:
    name = str(value).strip().lstrip("/")
    if not name or name == "skills":
        return None
    if "/" in name or "\\" in name:
        return None
    if any(char in name for char in _INVALID_SKILL_NAME_CHARS):
        return None
    return name


def _subagent_session_ref(metadata: dict[str, Any]) -> dict[str, Any] | None:
    source = (metadata.get("codex_cli") or {}).get("source")
    if not isinstance(source, dict) or source.get("kind") != "subagent":
        return None
    return {
        "session_id": metadata.get("session_id"),
        "parent_thread_id": source.get("parent_thread_id"),
        "depth": source.get("depth"),
        "agent_nickname": source.get("agent_nickname"),
        "agent_role": source.get("agent_role"),
        "capture_limitation": "codex_cli_subagent_linkage_metadata_only",
    }


def _coerce_timestamp(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return None


def _session_id_from_path(path: Path) -> str:
    stem = path.stem
    return stem.removeprefix("rollout-")


def _capture_limitations(metadata: dict[str, Any]) -> list[dict[str, str]]:
    limitations = [
        {
            "code": "codex_cli_parser_minimal_sp2",
            "message": "Parses response_item messages, function calls, outputs, and opentraces hook sidecars.",
        },
        {
            "code": "codex_cli_context_tree_session_level_projection",
            "message": "Context Tree projection is emitted by ingest as session-level reconstructed layers, not exact per-step prompt windows.",
        },
    ]
    if not metadata.get("codex_cli_hook_sidecar"):
        limitations.append({
            "code": "codex_cli_hook_sidecar_missing",
            "message": "No opentraces Codex hook sidecar was found for this session.",
        })
    source = (metadata.get("codex_cli") or {}).get("source")
    if isinstance(source, dict) and source.get("kind") == "subagent":
        limitations.append({
            "code": "codex_cli_subagent_linkage_metadata_only",
            "message": "Sub-agent source metadata is preserved, but parent/child TraceRecord linkage is metadata-only in this slice.",
        })
    return limitations
