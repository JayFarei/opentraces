"""Parser for native Pi session JSONL plus OpenTraces Pi sidecars."""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter, defaultdict
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
from .bridge import SIDECAR_SCHEMA_VERSION, iter_sidecar_events
from .context_tree_capture import (
    emit_context_tree_events_from_record as _emit_context_tree_events_from_record,
)
from .sessions import discover_session_files, read_session_header, session_id_from_file, session_relpath

logger = logging.getLogger(__name__)

CORRUPTED_LINE_THRESHOLD = 0.05
MAX_CONTENT_CHARS = 10000
MAX_SUMMARY_CHARS = 500


class PiSessionParser:
    """Parse Pi's tree-shaped native session JSONL into a TraceRecord.

    v1 emits the active branch as the trace spine.  Other branches and tree
    transitions are preserved under ``metadata.pi`` / Context Tree events rather
    than becoming separate TraceRecords.
    """

    agent_name = "pi"
    emit_context_tree_events_from_record = staticmethod(_emit_context_tree_events_from_record)

    def __init__(self) -> None:
        self.step_anchors: dict[int, dict[str, Any]] = {}
        self._step_anchor_rows: list[dict[str, Any]] = []

    def discover_sessions(self, root: str | Path | None = None) -> Iterator[Path]:
        yield from discover_session_files(root=root)

    def discover_project_sessions(self, project_dir: Path) -> Iterator[Path]:
        project = Path(project_dir).expanduser().resolve()
        for session_path in self.discover_sessions():
            header = read_session_header(session_path)
            raw_cwd = header.get("cwd")
            if not isinstance(raw_cwd, str) or not raw_cwd:
                continue
            try:
                cwd = Path(raw_cwd).expanduser().resolve()
            except OSError:
                continue
            if cwd == project or project in cwd.parents:
                yield session_path

    def session_id_from_path(self, session_path: Path) -> str:
        return session_id_from_file(session_path)

    def parse_session(self, session_path: Path, byte_offset: int = 0) -> TraceRecord | None:
        self.step_anchors = {}
        self._step_anchor_rows = []
        rows = self._read_lines(session_path, byte_offset=byte_offset)
        if rows is None:
            return None
        header = next((row for row in rows if row.get("type") == "session"), {})
        session_id = str(header.get("id") or session_id_from_file(session_path))
        cwd = str(header.get("cwd") or "")
        sidecars = list(iter_sidecar_events(cwd, session_id)) if cwd else []
        metadata = self._extract_metadata(rows, sidecars, session_path, session_id=session_id)
        active_rows = _active_branch_rows(rows, metadata)
        steps = self._parse_steps(active_rows, session_path)
        if not steps:
            return None
        for i, step in enumerate(steps, 1):
            step.step_index = i
        self.step_anchors = {
            step.step_index: anchor
            for step, anchor in zip(steps, self._step_anchor_rows)
        }

        task_description = next(
            (
                s.content
                for s in steps
                if s.role == "user" and s.content and not s.content.startswith("! ")
            ),
            None,
        )
        model = _normalize_model_id(metadata.get("model"), provider=metadata.get("provider"))
        trace_metadata: dict[str, Any] = {
            "source": "pi_session_jsonl",
            "trace_trails": snapshot_resume_trace_metadata(),
            "session_file": session_relpath(session_path),
            "raw_event_counts": metadata.get("raw_event_counts", {}),
            "entry_type_counts": metadata.get("entry_type_counts", {}),
            "pi": metadata.get("pi", {}),
            "normalized_tool_calls": _normalized_tool_calls(steps),
            "capture_limitations": _capture_limitations(metadata),
        }
        for key in (
            "hook_git_final",
            "hook_pre_tool_use",
            "hook_post_tool_use",
            "hook_stop",
            "pi_sidecar_files",
            "compaction_events",
            "is_compacted",
            "permission_mode",
        ):
            if key in metadata:
                trace_metadata[key] = metadata[key]

        record = TraceRecord(
            trace_id=str(uuid.uuid4()),
            session_id=session_id,
            timestamp_start=metadata.get("timestamp_start"),
            timestamp_end=metadata.get("timestamp_end"),
            execution_context="devtime",
            task=Task(description=task_description, source="user_prompt" if task_description else None),
            agent=Agent(
                name=self.agent_name,
                version=metadata.get("agent_version"),
                model=model,
            ),
            environment=Environment(
                vcs=_vcs_from_git(metadata.get("git")),
                language_ecosystem=[],
            ),
            steps=steps,
            outcome=Outcome(success=None, signal_source="source_metadata", signal_confidence="inferred"),
            metrics=self._build_metrics(metadata, steps),
            security=SecurityMetadata(),
            metadata=trace_metadata,
        )
        if not meets_quality_threshold(record):
            logger.debug("Pi session %s below quality threshold", session_path)
            return None
        return record

    def _read_lines(self, path: Path, byte_offset: int = 0) -> list[dict[str, Any]] | None:
        rows: list[dict[str, Any]] = []
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
                        row = json.loads(text)
                    except json.JSONDecodeError:
                        errors += 1
                        continue
                    if isinstance(row, dict):
                        row["_original_line_no"] = len(rows)
                        rows.append(row)
        except OSError as exc:
            logger.error("Cannot read Pi session %s: %s", path, exc)
            return None
        if total > 0 and errors / total > CORRUPTED_LINE_THRESHOLD:
            logger.error("Too many corrupted lines in %s: %d/%d", path, errors, total)
            return None
        return rows

    def _extract_metadata(
        self,
        rows: list[dict[str, Any]],
        sidecars: list[dict[str, Any]],
        session_path: Path,
        *,
        session_id: str,
    ) -> dict[str, Any]:
        raw_event_counts: Counter[str] = Counter()
        entry_type_counts: Counter[str] = Counter()
        first_ts: str | None = None
        last_ts: str | None = None
        header: dict[str, Any] = {}
        provider: str | None = None
        model: str | None = None
        agent_version: str | None = None
        usage_totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        children: dict[str | None, list[str]] = defaultdict(list)
        ids: set[str] = set()
        labels: dict[str, str] = {}
        branch_summaries: list[dict[str, Any]] = []
        tree_events: list[dict[str, Any]] = []
        compactions: list[dict[str, Any]] = []
        active_leaf = None
        active_leaf_source = "fallback_deepest_newest"
        sidecar_files: set[str] = set()
        provider_contexts: list[dict[str, Any]] = []
        tool_registry: list[dict[str, Any]] = []
        runtime_state: dict[str, Any] = {}
        git: dict[str, Any] = {}

        for row in rows:
            kind = str(row.get("type") or "unknown")
            entry_type_counts[kind] += 1
            raw_event_counts[kind] += 1
            ts = _coerce_timestamp(row.get("timestamp"))
            if ts and first_ts is None:
                first_ts = ts
            if ts:
                last_ts = ts
            if kind == "session" and not header:
                header = row
                agent_version = _string(row.get("pi_version") or row.get("version_label") or row.get("agent_version"))
                runtime_state.update({"cwd": row.get("cwd"), "session_version": row.get("version")})
                continue
            entry_id = _string(row.get("id"))
            if entry_id:
                ids.add(entry_id)
                parent_id = row.get("parentId")
                children[parent_id if isinstance(parent_id, str) else None].append(entry_id)
            if kind == "message" and isinstance(row.get("message"), dict):
                message = row["message"]
                usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
                usage_totals["input"] += int(usage.get("input") or usage.get("input_tokens") or 0)
                usage_totals["output"] += int(usage.get("output") or usage.get("output_tokens") or 0)
                usage_totals["cache_read"] += int(usage.get("cacheRead") or usage.get("cache_read") or 0)
                usage_totals["cache_write"] += int(usage.get("cacheWrite") or usage.get("cache_write") or 0)
                if message.get("role") == "assistant":
                    provider = _string(message.get("provider")) or provider
                    model = _string(message.get("model")) or model
            elif kind == "model_change":
                provider = _string(row.get("provider")) or provider
                model = _string(row.get("modelId") or row.get("model")) or model
                runtime_state["model"] = _normalize_model_id(model, provider=provider)
            elif kind == "thinking_level_change":
                runtime_state["thinking_level"] = row.get("thinkingLevel") or row.get("level")
            elif kind == "compaction":
                compactions.append({
                    "source": "pi_session_jsonl",
                    "event": "Compaction",
                    "timestamp": ts,
                    "entry_id": row.get("id"),
                    "first_kept_entry_id": row.get("firstKeptEntryId"),
                    "tokens_before": row.get("tokensBefore"),
                })
            elif kind == "branch_summary":
                branch_summaries.append({
                    "entry_id": row.get("id"),
                    "from_id": row.get("fromId"),
                    "timestamp": ts,
                    "summary": _truncate(row.get("summary"), limit=MAX_SUMMARY_CHARS),
                })
            elif kind == "label" and row.get("targetId"):
                labels[str(row["targetId"])] = str(row.get("label") or "")
            elif kind == "session_info":
                runtime_state["session_name"] = row.get("name")

        for sidecar in sidecars:
            raw_event_counts[f"sidecar:{sidecar.get('event')}"] += 1
            if sidecar.get("session_file"):
                sidecar_files.add(str(sidecar.get("session_file")))
            if sidecar.get("event") in {"active_leaf", "tree"}:
                data = sidecar.get("data") if isinstance(sidecar.get("data"), dict) else {}
                leaf = sidecar.get("leaf_id") or data.get("new_leaf_id")
                if isinstance(leaf, str) and leaf:
                    active_leaf = leaf
                    active_leaf_source = f"sidecar:{sidecar.get('event')}"
                if sidecar.get("event") == "tree":
                    tree_events.append({
                        "timestamp": sidecar.get("timestamp"),
                        "old_leaf_id": data.get("old_leaf_id"),
                        "new_leaf_id": data.get("new_leaf_id") or sidecar.get("leaf_id"),
                        "entry_id": sidecar.get("entry_id"),
                        "from_extension": data.get("from_extension"),
                    })
            _merge_sidecar_line(
                {
                    "provider_contexts": provider_contexts,
                    "tool_registry": tool_registry,
                    "runtime_state": runtime_state,
                    "compactions": compactions,
                },
                sidecar,
            )

        metadata: dict[str, Any] = {
            "session_id": session_id,
            "timestamp_start": _coerce_timestamp(header.get("timestamp")) or first_ts,
            "timestamp_end": last_ts,
            "agent_version": agent_version,
            "provider": provider,
            "model": model,
            "usage_totals": usage_totals,
            "raw_event_counts": dict(raw_event_counts),
            "entry_type_counts": dict(entry_type_counts),
            "git": git,
            "pi": {
                "session_id": session_id,
                "session_file": str(session_path),
                "session_relpath": session_relpath(session_path),
                "cwd": header.get("cwd"),
                "version": header.get("version"),
                "branch_count": _branch_count(children),
                "active_leaf_id": active_leaf,
                "active_leaf_source": active_leaf_source,
                "labels": labels,
                "branch_summaries": branch_summaries,
                "tree_events": tree_events,
                "provider_contexts": provider_contexts,
                "tool_registry": tool_registry,
                "runtime_state": runtime_state,
                "sidecar_event_count": len(sidecars),
            },
        }
        if sidecar_files:
            metadata["pi_sidecar_files"] = sorted(sidecar_files)
        if compactions:
            metadata["compaction_events"] = compactions
            metadata["is_compacted"] = True
        if active_leaf:
            metadata["active_leaf_id"] = active_leaf
        # second pass: expose hook keys from sidecars.
        for sidecar in sidecars:
            _merge_hook_metadata(metadata, sidecar)
        return metadata

    @staticmethod
    def _build_metrics(metadata: dict[str, Any], steps: list[Step]) -> Metrics:
        usage = metadata.get("usage_totals") or {}
        return Metrics(
            total_steps=len(steps),
            total_input_tokens=int(usage.get("input") or 0),
            total_output_tokens=int(usage.get("output") or 0),
            total_cache_read_tokens=int(usage.get("cache_read") or 0),
            total_cache_write_tokens=int(usage.get("cache_write") or 0),
        )

    def _parse_steps(self, rows: list[dict[str, Any]], session_path: Path) -> list[Step]:
        steps: list[Step] = []
        call_to_step: dict[str, Step] = {}
        relpath = session_relpath(session_path)

        def add_anchor(row: dict[str, Any]) -> None:
            self._step_anchor_rows.append({
                "entry_uuid": row.get("id"),
                "parent_uuid": row.get("parentId"),
                "line_no": row.get("_original_line_no"),
                "session_file_relpath": relpath,
            })

        for row in rows:
            kind = row.get("type")
            if kind == "message" and isinstance(row.get("message"), dict):
                message = row["message"]
                role = message.get("role")
                if role == "user":
                    text = _content_text(message.get("content"))
                    if text:
                        steps.append(Step(
                            step_index=len(steps) + 1,
                            role="user",
                            content=_truncate(text),
                            timestamp=_coerce_timestamp(row.get("timestamp") or message.get("timestamp")),
                            call_type="main",
                        ))
                        add_anchor(row)
                elif role == "assistant":
                    content, reasoning, tool_calls = _assistant_parts(message.get("content"))
                    step = Step(
                        step_index=len(steps) + 1,
                        role="agent",
                        content=_truncate(content) if content else None,
                        reasoning_content=_truncate(reasoning) if reasoning else None,
                        model=_normalize_model_id(message.get("model"), provider=message.get("provider")),
                        agent_role="main",
                        call_type="main",
                        tools_available=[tc.tool_name for tc in tool_calls],
                        tool_calls=tool_calls,
                        observations=[],
                        timestamp=_coerce_timestamp(row.get("timestamp") or message.get("timestamp")),
                    )
                    steps.append(step)
                    add_anchor(row)
                    for call in tool_calls:
                        call_to_step[call.tool_call_id] = step
                elif role == "toolResult":
                    call_id = _string(message.get("toolCallId") or message.get("tool_call_id"))
                    if not call_id:
                        continue
                    obs = Observation(
                        source_call_id=call_id,
                        content=_truncate(_content_text(message.get("content"))) or None,
                        output_summary=_summarize_output(_content_text(message.get("content"))),
                        error="tool_error" if message.get("isError") else None,
                    )
                    target = call_to_step.get(call_id)
                    if target is not None:
                        target.observations.append(obs)
                elif role == "bashExecution":
                    command = _string(message.get("command")) or ""
                    output = _string(message.get("output")) or ""
                    if command:
                        steps.append(Step(
                            step_index=len(steps) + 1,
                            role="user",
                            content=f"! {command}",
                            observations=[Observation(
                                source_call_id=f"pi-user-bash:{row.get('id') or len(steps)+1}",
                                content=_truncate(output) if output else None,
                                output_summary=_summarize_output(output),
                                error=(f"exit {message.get('exitCode')}" if message.get("exitCode") not in {0, None} else None),
                            )],
                            timestamp=_coerce_timestamp(row.get("timestamp") or message.get("timestamp")),
                            call_type="main",
                        ))
                        add_anchor(row)
            elif kind == "custom_message":
                if row.get("display") is False:
                    continue
                text = _content_text(row.get("content"))
                if text:
                    steps.append(Step(
                        step_index=len(steps) + 1,
                        role="user",
                        content=_truncate(text),
                        timestamp=_coerce_timestamp(row.get("timestamp")),
                        call_type="main",
                    ))
                    add_anchor(row)
            elif kind == "message" and not isinstance(row.get("message"), dict):
                continue
            elif kind == "bashExecution":
                command = _string(row.get("command")) or ""
                output = _string(row.get("output")) or ""
                if command:
                    steps.append(Step(
                        step_index=len(steps) + 1,
                        role="user",
                        content=f"! {command}",
                        observations=[Observation(
                            source_call_id=f"pi-user-bash:{row.get('id') or len(steps)+1}",
                            content=_truncate(output) if output else None,
                            output_summary=_summarize_output(output),
                            error=(f"exit {row.get('exitCode')}" if row.get("exitCode") not in {0, None} else None),
                        )],
                        timestamp=_coerce_timestamp(row.get("timestamp")),
                        call_type="main",
                    ))
                    add_anchor(row)
        return steps


def _active_branch_rows(rows: list[dict[str, Any]], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    entries = [row for row in rows if row.get("type") != "session"]
    by_id = {str(row.get("id")): row for row in entries if row.get("id")}
    if not by_id:
        return entries
    parent_of = {
        str(row.get("id")): row.get("parentId")
        for row in entries
        if row.get("id")
    }
    children = {str(row.get("parentId")) for row in entries if row.get("parentId")}
    leaves = [entry_id for entry_id in by_id if entry_id not in children]
    leaf = metadata.get("active_leaf_id")
    if not isinstance(leaf, str) or leaf not in by_id:
        leaf = _deepest_newest_leaf(leaves, by_id, parent_of)
    if not leaf:
        return entries
    path_ids: list[str] = []
    current: str | None = leaf
    seen: set[str] = set()
    while current and current not in seen and current in by_id:
        seen.add(current)
        path_ids.append(current)
        parent = parent_of.get(current)
        current = parent if isinstance(parent, str) else None
    keep = set(reversed(path_ids))
    ordered = [row for row in entries if row.get("id") in keep]
    return ordered or entries


def _deepest_newest_leaf(
    leaves: list[str],
    by_id: dict[str, dict[str, Any]],
    parent_of: dict[str, Any],
) -> str | None:
    if not leaves:
        return next(reversed(by_id)) if by_id else None

    def depth(entry_id: str) -> int:
        count = 0
        current: str | None = entry_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            parent = parent_of.get(current)
            if not isinstance(parent, str) or parent not in by_id:
                break
            count += 1
            current = parent
        return count

    return max(leaves, key=lambda entry_id: (depth(entry_id), _coerce_timestamp(by_id[entry_id].get("timestamp")) or ""))


def _branch_count(children: dict[str | None, list[str]]) -> int:
    return sum(max(0, len(values) - 1) for values in children.values())


def _merge_sidecar_line(targets: dict[str, Any], sidecar: dict[str, Any]) -> None:
    event = sidecar.get("event")
    data = sidecar.get("data") if isinstance(sidecar.get("data"), dict) else {}
    if event in {"provider_request", "context"}:
        ctx = {
            "event": event,
            "timestamp": sidecar.get("timestamp"),
            "entry_id": sidecar.get("entry_id"),
            "leaf_id": sidecar.get("leaf_id"),
            "turn_index": sidecar.get("turn_index"),
            "capture_method": "live_capture",
            "completeness": data.get("completeness") or "full",
            "system": data.get("system") or data.get("system_prompt"),
            "messages": data.get("messages"),
            "tool_registry": data.get("tool_registry") or data.get("tools"),
            "runtime_state": data.get("runtime_state"),
            "raw_provider_body_refs": data.get("raw_provider_body_refs"),
        }
        targets["provider_contexts"].append({k: v for k, v in ctx.items() if v is not None})
        tools = data.get("tool_registry") or data.get("tools")
        if isinstance(tools, list):
            targets["tool_registry"].extend([t for t in tools if isinstance(t, dict)])
        runtime = data.get("runtime_state")
        if isinstance(runtime, dict):
            targets["runtime_state"].update(runtime)
    elif event == "model_change":
        model = data.get("model") or data.get("modelId")
        if model:
            targets["runtime_state"]["model"] = model
    elif event == "thinking_change":
        targets["runtime_state"]["thinking_level"] = data.get("level") or data.get("thinkingLevel")
    elif event == "compaction":
        targets["compactions"].append({
            "source": "pi_sidecar",
            "event": "Compaction",
            "timestamp": sidecar.get("timestamp"),
            "entry_id": sidecar.get("entry_id"),
            "trigger": data.get("trigger") or "pi",
            "summary": _truncate(data.get("summary"), limit=MAX_SUMMARY_CHARS),
        })


def _merge_hook_metadata(metadata: dict[str, Any], sidecar: dict[str, Any]) -> None:
    if sidecar.get("schema_version") != SIDECAR_SCHEMA_VERSION:
        return
    event = sidecar.get("event")
    data = sidecar.get("data") if isinstance(sidecar.get("data"), dict) else {}
    if event == "session_shutdown":
        git = data.get("git") or metadata.get("git") or {}
        if git:
            metadata["git"] = git
            metadata["hook_git_final"] = git
        metadata.setdefault("hook_stop", []).append({
            "timestamp": sidecar.get("timestamp"),
            "session_id": sidecar.get("session_id"),
            "agent_type": "main",
            "permission_mode": data.get("permission_mode"),
            "stop_hook_active": False,
            "git": git,
            "trail": data.get("trail") or {},
        })
        return
    if event == "tool_pre":
        tool_use_id = sidecar.get("tool_call_id") or data.get("tool_call_id") or data.get("tool_use_id")
        if tool_use_id:
            metadata.setdefault("hook_pre_tool_use", {})[str(tool_use_id)] = {
                "timestamp": sidecar.get("timestamp"),
                "tool": data.get("tool_name") or data.get("tool"),
                "tool_input": data.get("input") or data.get("tool_input") or {},
                "trail": data.get("trail") or {},
            }
        return
    if event == "tool_post":
        tool_use_id = sidecar.get("tool_call_id") or data.get("tool_call_id") or data.get("tool_use_id")
        if tool_use_id:
            metadata.setdefault("hook_post_tool_use", {})[str(tool_use_id)] = {
                "timestamp": sidecar.get("timestamp"),
                "tool": data.get("tool_name") or data.get("tool"),
                "file_path": data.get("file_path"),
                "start_line": data.get("start_line"),
                "end_line": data.get("end_line"),
                "content_hash": data.get("content_hash"),
                "confidence": data.get("confidence"),
                "capture_status": data.get("capture_status") or "captured",
                "limitations": data.get("limitations") or [],
                "tool_input": data.get("input") or data.get("tool_input") or {},
                "tool_response": data.get("result") or data.get("tool_response") or {},
                "trail": data.get("trail") or {},
            }
        return
    if event == "user_bash":
        metadata.setdefault("pi_user_bash_events", []).append({
            "timestamp": sidecar.get("timestamp"),
            "command": data.get("command"),
            "exit_code": data.get("exit_code"),
            "exclude_from_context": data.get("exclude_from_context"),
        })


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif block.get("type") in {"input_text", "output_text"} and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(part for part in parts if part).strip()


def _assistant_parts(content: Any) -> tuple[str, str, list[ToolCall]]:
    if isinstance(content, str):
        return content, "", []
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    if not isinstance(content, list):
        return "", "", []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        elif kind == "thinking":
            thinking = block.get("thinking") or block.get("text")
            if isinstance(thinking, str):
                reasoning_parts.append(thinking)
        elif kind == "toolCall":
            call_id = str(block.get("id") or uuid.uuid4())
            tool_calls.append(ToolCall(
                tool_call_id=call_id,
                tool_name=str(block.get("name") or "unknown"),
                input=_dict_or_empty(block.get("arguments") or block.get("input")),
            ))
    return (
        _truncate("\n".join(text_parts).strip()),
        _truncate("\n".join(reasoning_parts).strip()),
        tool_calls,
    )


def _dict_or_empty(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": _truncate(value, limit=MAX_SUMMARY_CHARS)}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {}


def _truncate(value: Any, *, limit: int = MAX_CONTENT_CHARS) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit]


def _summarize_output(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\n", " ")
    if not text:
        return None
    return text[:MAX_SUMMARY_CHARS]


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _normalize_model_id(model: Any, *, provider: Any = None) -> str | None:
    if not isinstance(model, str) or not model:
        return None
    if "/" in model:
        return model
    if isinstance(provider, str) and provider:
        return f"{provider}/{model}"
    return model


def _coerce_timestamp(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / (1000 if value > 10_000_000_000 else 1), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return None


def _vcs_from_git(git: Any) -> VCS:
    if not isinstance(git, dict):
        return VCS(type="none")
    sha = git.get("commit_hash") or git.get("sha") or git.get("head")
    branch = git.get("branch")
    if sha or branch or git.get("repository_url"):
        return VCS(type="git", base_commit=sha, branch=branch)
    return VCS(type="none")


def _normalized_tool_calls(steps: list[Step]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in steps:
        for call in step.tool_calls or []:
            kind = _tool_kind(call.tool_name, call.input)
            row: dict[str, Any] = {
                "step_index": step.step_index,
                "tool_call_id": call.tool_call_id,
                "raw_tool_name": call.tool_name,
                "tool_kind": kind,
            }
            if kind == "shell":
                row["shell"] = _shell_metadata(call.input)
            if str(call.tool_name).startswith("ot_"):
                row["opentraces_internal_readonly"] = True
            out.append(row)
    return out


def _tool_kind(tool_name: str | None, tool_input: dict[str, Any] | None = None) -> str:
    name = (tool_name or "").lower()
    if name.startswith("ot_"):
        return "opentraces_retrieval"
    if any(part in name for part in ("bash", "shell", "exec", "command", "terminal")):
        return "shell"
    if name in {"apply_patch", "edit", "write", "multiedit"} or any(part in name for part in ("edit", "write", "patch")):
        return "write"
    if any(part in name for part in ("read", "open", "view", "cat")):
        return "read"
    if any(part in name for part in ("search", "grep", "rg", "find")):
        return "search"
    if "mcp" in name or (tool_input or {}).get("server"):
        return "mcp"
    if any(part in name for part in ("task", "subagent", "spawn_agent")):
        return "subagent"
    return "tool"


def _shell_metadata(tool_input: dict[str, Any] | None) -> dict[str, Any]:
    data = tool_input or {}
    out: dict[str, Any] = {}
    command = data.get("cmd") or data.get("command") or data.get("script")
    cwd = data.get("cwd") or data.get("workdir") or data.get("working_directory")
    if isinstance(command, str):
        out["command"] = _truncate(command, limit=MAX_SUMMARY_CHARS)
    if isinstance(cwd, str):
        out["cwd"] = cwd
    return out


def _capture_limitations(metadata: dict[str, Any]) -> list[dict[str, str]]:
    limitations = [
        {
            "code": "pi_parser_active_branch_v1",
            "message": "Pi v1 capture emits the active session branch as the TraceRecord spine and preserves other branches as metadata/context events.",
        }
    ]
    pi = metadata.get("pi") or {}
    if not metadata.get("pi_sidecar_files"):
        limitations.append({
            "code": "pi_sidecar_missing",
            "message": "No OpenTraces Pi sidecar was found; TraceRecord parsed from transcript-only Pi JSONL.",
        })
    if int(pi.get("branch_count") or 0) > 0:
        limitations.append({
            "code": "pi_dropped_inactive_branches_v1",
            "message": "Inactive Pi session branches are preserved as metadata but not exported as separate TraceRecords in v1.",
        })
    if not (pi.get("provider_contexts") or []):
        limitations.append({
            "code": "pi_context_tree_transcript_fallback",
            "message": "No provider/context sidecar was available; Context Tree uses transcript reconstruction.",
        })
    counts = metadata.get("raw_event_counts") if isinstance(metadata.get("raw_event_counts"), dict) else {}
    if int(counts.get("sidecar:pi_capture_boundary_timeout") or 0) > 0:
        limitations.append({
            "code": "pi_capture_boundary_timeout",
            "message": "One or more Pi capture bridge calls exceeded the configured short timeout and were recorded as limitations.",
        })
    return limitations
