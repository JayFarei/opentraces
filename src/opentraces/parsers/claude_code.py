"""Claude Code session parser.

Reads sessions from ~/.claude/projects/*/. Handles:
- tool_use/tool_result correlation across message boundaries
- Recursive sub-agent loading from subagents/ directories
- System prompt deduplication
- Per-step token usage from API response metadata
- Extended thinking (reasoning_content)
- Warmup detection (low output_tokens + no meaningful content)
- Snippet extraction from Read/Edit/Write/Grep tool results
- Quality filtering (min 1 tool call + min 2 steps)
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Iterator

from opentraces_schema import (
    Agent,
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

from .quality import meets_quality_threshold

logger = logging.getLogger(__name__)

MAX_SUBAGENT_DEPTH = 10
CORRUPTED_LINE_THRESHOLD = 0.05  # Reject session if >5% lines fail to parse


class ClaudeCodeParser:
    """Parser for Claude Code session.jsonl files."""

    agent_name = "claude-code"

    def discover_sessions(self, projects_path: Path) -> Iterator[Path]:
        """Yield paths to all session JSONL files."""
        if not projects_path.exists():
            return

        for project_dir in sorted(projects_path.iterdir()):
            if not project_dir.is_dir():
                continue
            # Session files are either directly in the project dir
            # or inside UUID-named subdirectories
            for jsonl in sorted(project_dir.glob("*.jsonl")):
                # Skip subagent files (they're loaded recursively)
                if "/subagents/" in str(jsonl):
                    continue
                yield jsonl

    def parse_session(
        self,
        session_path: Path,
        byte_offset: int = 0,
    ) -> TraceRecord | None:
        """Parse a Claude Code session file into a TraceRecord."""
        lines = self._read_lines(session_path, byte_offset)
        if lines is None:
            return None

        session_id = session_path.stem
        project_dir = session_path.parent
        project_name = project_dir.name

        # Extract session metadata
        metadata = self._extract_metadata(lines)

        # Build tool_result map (pre-pass)
        tool_result_map = self._build_tool_result_map(lines)

        # Parse steps (with visited set for circular reference detection)
        visited_sessions: set[str] = {session_id}
        steps, system_prompts = self._parse_steps(
            lines, tool_result_map, session_path, depth=0,
            visited_sessions=visited_sessions,
        )

        if not steps:
            return None

        # Populate system_prompts from metadata if parser didn't find them inline
        if not system_prompts and metadata.get("system_prompt_raw"):
            raw = metadata["system_prompt_raw"]
            # system_prompt_raw can be a list of blocks or a string
            if isinstance(raw, list):
                text = "\n".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in raw
                )
            else:
                text = str(raw)
            if text.strip():
                prompt_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
                system_prompts[prompt_hash] = text

        # Renumber all steps sequentially to guarantee uniqueness
        # (subagent inlining can create duplicates)
        # Build old_index -> new_index map and fix parent_step references
        old_to_new: dict[int, int] = {}
        for i, step in enumerate(steps, 1):
            old_to_new[step.step_index] = i
            step.step_index = i

        for step in steps:
            if step.parent_step is not None and step.parent_step in old_to_new:
                step.parent_step = old_to_new[step.parent_step]

        # Build trace record
        record = TraceRecord(
            trace_id=str(uuid.uuid4()),
            session_id=session_id,
            timestamp_start=metadata.get("timestamp_start"),
            timestamp_end=metadata.get("timestamp_end"),
            task=Task(
                description=metadata.get("first_user_message"),
                source="user_prompt",
            ),
            agent=Agent(
                name="claude-code",
                version=metadata.get("version"),
                model=metadata.get("model"),
            ),
            environment=Environment(
                os=self._infer_os(metadata.get("cwd", "")),
                shell=metadata.get("shell"),
                vcs=self._infer_vcs(metadata),
                language_ecosystem=[],
            ),
            system_prompts=system_prompts,
            tool_definitions=metadata.get("tool_definitions", []),
            steps=steps,
            outcome=Outcome(),  # Enriched later with git signals
            metrics=self._compute_metrics(steps),
            security=SecurityMetadata(),
            metadata={"project": project_name},
        )

        if not meets_quality_threshold(record):
            logger.debug(f"Session {session_id} below quality threshold, skipping")
            return None

        return record

    def _read_lines(self, path: Path, byte_offset: int = 0) -> list[dict] | None:
        """Read JSONL lines with line-level error handling."""
        lines = []
        errors = 0
        total = 0

        try:
            with open(path, "r") as f:
                if byte_offset > 0:
                    f.seek(byte_offset)
                    # Discard partial line after seek (may have landed mid-UTF8)
                    f.readline()
                for raw_line in f:
                    total += 1
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        lines.append(json.loads(raw_line))
                    except json.JSONDecodeError:
                        errors += 1
                        logger.warning(f"Corrupted line {total} in {path}")
        except OSError as e:
            logger.error(f"Cannot read {path}: {e}")
            return None

        if total > 0 and errors / total > CORRUPTED_LINE_THRESHOLD:
            logger.error(
                f"Too many corrupted lines in {path}: {errors}/{total} "
                f"({errors/total:.1%} > {CORRUPTED_LINE_THRESHOLD:.0%})"
            )
            return None

        return lines

    def _extract_metadata(self, lines: list[dict]) -> dict[str, Any]:
        """Extract session-level metadata from parsed lines."""
        metadata: dict[str, Any] = {}
        timestamps = []

        for line in lines:
            ts = line.get("timestamp")
            if ts:
                timestamps.append(ts)

            line_type = line.get("type")

            # Extract version from any line that has it
            if "version" in line and not metadata.get("version"):
                metadata["version"] = line["version"]

            # Extract model from assistant message lines (most reliable source)
            if line_type == "assistant" and not metadata.get("model"):
                msg = line.get("message", {})
                if isinstance(msg, dict) and msg.get("model"):
                    metadata["model"] = msg["model"]

            # Extract model and tools from queue-operation content (fallback)
            if line_type == "queue-operation" and "content" in line:
                content = line["content"]
                if isinstance(content, str):
                    try:
                        inner = json.loads(content)
                        if isinstance(inner, dict):
                            if "model" in inner and not metadata.get("model"):
                                metadata["model"] = inner["model"]
                            if "tools" in inner:
                                metadata["tool_definitions"] = [
                                    {"name": t.get("name"), "description": t.get("description", "")}
                                    for t in inner["tools"]
                                ]
                            if "system" in inner:
                                metadata["system_prompt_raw"] = inner["system"]
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.debug("Could not parse metadata from init message: %s", e)

            # Extract first user message
            if (
                line_type == "user"
                and "message" in line
                and not metadata.get("first_user_message")
            ):
                msg = line["message"]
                content = msg.get("content")
                if isinstance(content, str):
                    metadata["first_user_message"] = content[:500]
                elif isinstance(content, list):
                    for block in content:
                        if block.get("type") == "text":
                            metadata["first_user_message"] = block.get("text", "")[:500]
                            break

            # Extract git branch/cwd
            if "gitBranch" in line and not metadata.get("git_branch"):
                metadata["git_branch"] = line["gitBranch"]
            if "cwd" in line and not metadata.get("cwd"):
                metadata["cwd"] = line["cwd"]

        if timestamps:
            metadata["timestamp_start"] = timestamps[0]
            metadata["timestamp_end"] = timestamps[-1]

        return metadata

    def _build_tool_result_map(self, lines: list[dict]) -> dict[str, dict]:
        """Pre-pass: correlate tool_use_id -> tool_result across messages."""
        result_map: dict[str, dict] = {}

        for line in lines:
            if line.get("type") != "user" or "message" not in line:
                continue
            msg = line["message"]
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if block.get("type") == "tool_result":
                    tool_use_id = block.get("tool_use_id")
                    if tool_use_id:
                        result_content = block.get("content", "")
                        if isinstance(result_content, list):
                            # Flatten text blocks
                            result_content = "\n".join(
                                b.get("text", "") for b in result_content if b.get("type") == "text"
                            )
                        result_map[tool_use_id] = {
                            "content": result_content,
                            "tool_use_result": line.get("toolUseResult"),
                            "timestamp": line.get("timestamp"),
                            "is_error": block.get("is_error", False),
                        }

        return result_map

    def _parse_steps(
        self,
        lines: list[dict],
        tool_result_map: dict[str, dict],
        session_path: Path,
        depth: int = 0,
        parent_step_index: int | None = None,
        visited_sessions: set[str] | None = None,
    ) -> tuple[list[Step], dict[str, str]]:
        """Parse JSONL lines into Step objects."""
        steps: list[Step] = []
        system_prompts: dict[str, str] = {}
        step_index = 0
        unknown_blocks = 0
        total_blocks = 0

        for line in lines:
            line_type = line.get("type")

            if line_type not in ("user", "assistant"):
                continue

            msg = line.get("message", {})
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue

            content_blocks = msg.get("content")
            if not isinstance(content_blocks, list):
                # Plain text content (user message)
                if isinstance(content_blocks, str):
                    step_index += 1
                    steps.append(Step(
                        step_index=step_index,
                        role="user",
                        content=content_blocks,
                        timestamp=line.get("timestamp"),
                        parent_step=parent_step_index,
                        call_type="subagent" if depth > 0 else "main",
                    ))
                continue

            # Process content blocks
            step_content = ""
            reasoning = ""
            tool_calls: list[ToolCall] = []
            observations: list[Observation] = []
            snippets: list[Snippet] = []
            token_usage = TokenUsage()

            for block in content_blocks:
                total_blocks += 1
                block_type = block.get("type")

                if block_type == "text":
                    step_content += block.get("text", "")

                elif block_type == "thinking":
                    thinking_text = block.get("thinking", "")
                    if thinking_text:
                        reasoning += thinking_text
                    elif block.get("signature"):
                        # Encrypted/redacted thinking: model reasoned but
                        # content was withheld by provider. Mark as redacted
                        # so consumers know reasoning occurred.
                        if not reasoning:
                            reasoning = "[redacted: model produced reasoning but content was withheld by provider]"

                elif block_type == "tool_use":
                    tool_call_id = block.get("id", str(uuid.uuid4()))
                    tool_name = block.get("name", "unknown")
                    tool_input = block.get("input", {})

                    tc = ToolCall(
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        input=tool_input,
                    )
                    tool_calls.append(tc)

                    # Link observation from result map
                    result = tool_result_map.get(tool_call_id)
                    if result:
                        obs_content = result.get("content", "")
                        is_error = result.get("is_error", False)
                        obs = Observation(
                            source_call_id=tool_call_id,
                            content=str(obs_content)[:10000] if obs_content else None,
                            output_summary=self._summarize_output(obs_content),
                            error="tool_error" if is_error else None,
                        )
                        observations.append(obs)

                        # Extract snippets from tool results
                        extracted = self._extract_snippets(
                            tool_name, tool_input, result, step_index + 1
                        )
                        snippets.extend(extracted)

                        # Compute duration from toolUseResult
                        tur = result.get("tool_use_result")
                        if isinstance(tur, dict):
                            if "durationMs" in tur:
                                val = tur["durationMs"]
                                tc.duration_ms = int(val) if isinstance(val, (int, float)) else None
                            elif "durationSeconds" in tur:
                                val = tur["durationSeconds"]
                                tc.duration_ms = int(val * 1000) if isinstance(val, (int, float)) else None
                            elif "duration" in tur:
                                val = tur["duration"]
                                tc.duration_ms = int(val * 1000) if isinstance(val, (int, float)) else None
                    else:
                        observations.append(Observation(
                            source_call_id=tool_call_id,
                            error="no_result",
                        ))

                elif block_type == "tool_result":
                    # Tool results are in user messages, handled by tool_result_map
                    pass

                elif block_type == "image":
                    # Image blocks (screenshots) are captured but not parsed
                    pass

                else:
                    unknown_blocks += 1
                    logger.warning(f"Unknown content block type: {block_type}")

            # Extract token usage from message.usage
            usage_data = msg.get("usage")
            if usage_data and isinstance(usage_data, dict):
                token_usage = TokenUsage(
                    input_tokens=usage_data.get("input_tokens", 0),
                    output_tokens=usage_data.get("output_tokens", 0),
                    cache_read_tokens=usage_data.get("cache_read_input_tokens", 0),
                    cache_write_tokens=usage_data.get("cache_creation_input_tokens", 0),
                )

            # Determine call_type
            call_type = "subagent" if depth > 0 else "main"
            is_warmup = (
                token_usage.output_tokens <= 10
                and not step_content.strip()
                and not tool_calls
                and role == "assistant"
            )
            if is_warmup:
                call_type = "warmup"

            # Determine agent_role
            agent_role = None
            if role == "assistant":
                agent_role = "main"
                if depth > 0:
                    agent_role = "explore"  # Default for subagents

            # Build step
            mapped_role = "agent" if role == "assistant" else "user"

            # System prompt handling: link agent steps to the system prompt
            system_prompt_hash = None
            if mapped_role == "agent" and system_prompts:
                # Use the first (and typically only) system prompt hash
                system_prompt_hash = next(iter(system_prompts), None)

            # Skip user messages that only contain tool_results (they're observations, not steps)
            if mapped_role == "user" and not step_content and all(
                b.get("type") == "tool_result" for b in content_blocks
            ):
                continue

            step_index += 1
            step = Step(
                step_index=step_index,
                role=mapped_role,
                content=step_content or None,
                reasoning_content=reasoning or None,
                model=line.get("message", {}).get("model"),
                system_prompt_hash=system_prompt_hash,
                agent_role=agent_role,
                parent_step=parent_step_index,
                call_type=call_type,
                tools_available=[tc.tool_name for tc in tool_calls] if tool_calls else [],
                tool_calls=tool_calls,
                observations=observations,
                snippets=snippets,
                token_usage=token_usage,
                timestamp=line.get("timestamp"),
            )

            # Check for Agent/Task tool calls -> recursive sub-agent loading
            for tc in tool_calls:
                if tc.tool_name in ("Agent", "Task") and depth < MAX_SUBAGENT_DEPTH:
                    subagent_ref = self._load_subagent(
                        session_path, tc, step_index, steps, system_prompts, depth,
                        visited_sessions=visited_sessions or set(),
                    )
                    if subagent_ref:
                        step.subagent_trajectory_ref = subagent_ref

            steps.append(step)

        # Warn if too many unknown blocks
        if total_blocks > 0 and unknown_blocks / total_blocks > 0.2:
            logger.error(
                f"High unknown block rate: {unknown_blocks}/{total_blocks} "
                f"({unknown_blocks/total_blocks:.0%}). Claude Code format may have changed."
            )

        return steps, system_prompts

    def _load_subagent(
        self,
        parent_session_path: Path,
        tool_call: ToolCall,
        parent_step_index: int,
        parent_steps: list[Step],
        system_prompts: dict[str, str],
        depth: int,
        visited_sessions: set[str] | None = None,
    ) -> str | None:
        """Recursively load a sub-agent session and inline its steps."""
        session_dir = parent_session_path.parent / parent_session_path.stem
        subagents_dir = session_dir / "subagents"

        if not subagents_dir.exists():
            return None

        # Find subagent file - match by tool_call_id or iterate files
        input_data = tool_call.input
        subagent_type = input_data.get("subagent_type", "general")
        description = input_data.get("description", "")

        # Try to find matching subagent file via meta.json
        for meta_file in subagents_dir.glob("*.meta.json"):
            try:
                meta = json.loads(meta_file.read_text())
                if meta.get("description") == description:
                    jsonl_file = meta_file.with_suffix("").with_suffix(".jsonl")
                    if jsonl_file.exists():
                        return self._inline_subagent(
                            jsonl_file, parent_step_index, parent_steps,
                            system_prompts, depth, subagent_type, visited_sessions,
                        )
            except (json.JSONDecodeError, OSError) as e:
                logger.debug("Skipping subagent state file: %s", e)
                continue

        # Fallback: try first available subagent file
        for jsonl_file in subagents_dir.glob("*.jsonl"):
            return self._inline_subagent(
                jsonl_file, parent_step_index, parent_steps,
                system_prompts, depth, subagent_type, visited_sessions,
            )

        return None

    def _inline_subagent(
        self,
        subagent_path: Path,
        parent_step_index: int,
        parent_steps: list[Step],
        system_prompts: dict[str, str],
        depth: int,
        subagent_type: str,
        visited_sessions: set[str] | None = None,
    ) -> str:
        """Parse and inline a sub-agent session."""
        subagent_id = subagent_path.stem

        # Circular reference detection
        if visited_sessions is not None and subagent_id in visited_sessions:
            logger.warning(f"Circular sub-agent reference detected: {subagent_id}")
            return subagent_id
        if visited_sessions is not None:
            visited_sessions.add(subagent_id)

        lines = self._read_lines(subagent_path)
        if not lines:
            logger.warning(f"Could not read subagent file: {subagent_path}")
            return subagent_id

        tool_result_map = self._build_tool_result_map(lines)
        sub_steps, sub_prompts = self._parse_steps(
            lines, tool_result_map, subagent_path,
            depth=depth + 1, parent_step_index=parent_step_index,
            visited_sessions=visited_sessions,
        )

        # Set agent_role based on subagent_type
        role_map = {
            "Explore": "explore",
            "Plan": "plan",
            "general-purpose": "general",
        }
        agent_role = role_map.get(subagent_type, subagent_type.lower())

        # Renumber sub-agent steps to avoid duplicates with parent
        max_parent_index = max((s.step_index for s in parent_steps), default=0)
        for step in sub_steps:
            max_parent_index += 1
            step.step_index = max_parent_index
            if step.agent_role == "explore":
                step.agent_role = agent_role
            step.call_type = "subagent"

        parent_steps.extend(sub_steps)
        system_prompts.update(sub_prompts)

        return subagent_id

    def _extract_snippets(
        self,
        tool_name: str,
        tool_input: dict,
        result: dict,
        source_step: int,
    ) -> list[Snippet]:
        """Extract code snippets from tool call results."""
        snippets = []
        content = result.get("content", "")
        tur = result.get("tool_use_result", {})

        if tool_name == "Read":
            file_path = tool_input.get("file_path")
            if file_path and content:
                offset = tool_input.get("offset", 1)
                line_count = content.count("\n") + 1 if content else 0
                lang = self._detect_language(file_path)
                snippets.append(Snippet(
                    file_path=file_path,
                    start_line=offset,
                    end_line=offset + line_count - 1,
                    language=lang,
                    text=content[:5000] if len(content) > 5000 else content,
                    source_step=source_step,
                ))

        elif tool_name == "Edit":
            file_path = tool_input.get("file_path")
            new_string = tool_input.get("new_string")
            if file_path and new_string:
                lang = self._detect_language(file_path)
                snippets.append(Snippet(
                    file_path=file_path,
                    language=lang,
                    text=new_string[:5000] if len(new_string) > 5000 else new_string,
                    source_step=source_step,
                ))

        elif tool_name == "Write":
            file_path = tool_input.get("file_path")
            file_content = tool_input.get("content")
            if file_path and file_content:
                lang = self._detect_language(file_path)
                snippets.append(Snippet(
                    file_path=file_path,
                    start_line=1,
                    end_line=file_content.count("\n") + 1,
                    language=lang,
                    text=file_content[:5000] if len(file_content) > 5000 else file_content,
                    source_step=source_step,
                ))

        elif tool_name == "Grep":
            # Parse structured grep output for file paths
            if isinstance(tur, dict) and "matches" in tur:
                matches = tur["matches"]
                if isinstance(matches, list):
                    for match in matches[:10]:
                        if isinstance(match, dict):
                            fp = match.get("file_path") or match.get("path")
                            if fp:
                                snippets.append(Snippet(
                                    file_path=fp,
                                    language=self._detect_language(fp),
                                    source_step=source_step,
                                ))

        return snippets

    @staticmethod
    def _infer_os(cwd: str) -> str | None:
        """Infer operating system from cwd path prefix."""
        if not cwd:
            return None
        if cwd.startswith("/Users/"):
            return "darwin"
        if cwd.startswith("/home/") or cwd.startswith("/root/"):
            return "linux"
        if len(cwd) >= 3 and cwd[1] == ":" and cwd[2] in ("/", "\\"):
            return "windows"
        return None

    @staticmethod
    def _infer_vcs(metadata: dict[str, Any]) -> VCS:
        """Infer VCS info from session metadata."""
        git_branch = metadata.get("git_branch")
        if git_branch and git_branch != "HEAD":
            return VCS(type="git", branch=git_branch)
        if git_branch == "HEAD":
            # HEAD means detached head or git is present but branch unknown
            return VCS(type="git")
        return VCS(type="none")

    def _detect_language(self, file_path: str) -> str | None:
        """Detect programming language from file extension."""
        ext_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".tsx": "typescript", ".jsx": "javascript", ".rb": "ruby",
            ".rs": "rust", ".go": "go", ".java": "java", ".kt": "kotlin",
            ".swift": "swift", ".c": "c", ".cpp": "cpp", ".h": "c",
            ".cs": "csharp", ".php": "php", ".sh": "bash", ".zsh": "bash",
            ".md": "markdown", ".json": "json", ".yaml": "yaml",
            ".yml": "yaml", ".toml": "toml", ".html": "html",
            ".css": "css", ".sql": "sql", ".dockerfile": "dockerfile",
        }
        ext = Path(file_path).suffix.lower()
        return ext_map.get(ext)

    def _summarize_output(self, content: Any, max_len: int = 200) -> str | None:
        """Create a lightweight preview of tool output."""
        if not content:
            return None
        text = str(content)
        if len(text) <= max_len:
            return text
        return text[:max_len] + "..."

    def _compute_metrics(self, steps: list[Step]) -> Metrics:
        """Aggregate token usage across all steps."""
        total_input = 0
        total_output = 0
        total_cache_read = 0

        for step in steps:
            total_input += step.token_usage.input_tokens
            total_output += step.token_usage.output_tokens
            total_cache_read += step.token_usage.cache_read_tokens

        total_input_with_cache = total_input + total_cache_read
        cache_hit_rate = (
            total_cache_read / total_input_with_cache
            if total_input_with_cache > 0
            else None
        )

        # Duration from timestamps
        duration = None
        timestamped_steps = [s for s in steps if s.timestamp]
        if len(timestamped_steps) >= 2:
            try:
                from datetime import datetime
                first = datetime.fromisoformat(timestamped_steps[0].timestamp.replace("Z", "+00:00"))
                last = datetime.fromisoformat(timestamped_steps[-1].timestamp.replace("Z", "+00:00"))
                duration = (last - first).total_seconds()
            except (ValueError, TypeError) as e:
                logger.debug("Could not compute duration from timestamps: %s", e)

        return Metrics(
            total_steps=len(steps),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_duration_s=duration,
            cache_hit_rate=round(cache_hit_rate, 4) if cache_hit_rate is not None else None,
        )
