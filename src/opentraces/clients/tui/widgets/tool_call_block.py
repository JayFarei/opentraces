"""Expandable tool call widget for the replay screen."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Static

from ..utils import _truncate, escape, _FILE_TOOLS, _WEB_TOOLS, PALETTE
from .diff_view import render_diff_markup

_TOOL_BORDER_COLORS: dict[str, str] = {
    "file": PALETTE["accent"],
    "web": PALETTE["warning"],
    "agent": PALETTE["secondary"],
    "default": PALETTE["text_dim"],
}


def _tool_category(name: str) -> str:
    if name in _FILE_TOOLS:
        return "file"
    if name in _WEB_TOOLS:
        return "web"
    if name == "Agent":
        return "agent"
    return "default"


def format_tool_call_summary(tool_call: dict[str, Any], limit: int = 80) -> str:
    """One-line summary of tool call input for collapsed view."""
    inp = tool_call.get("input") or tool_call.get("arguments") or {}
    if isinstance(inp, str):
        return _truncate(inp, limit)
    # Common patterns: show the most useful field
    for key in ("command", "file_path", "pattern", "query", "content", "url", "path"):
        if key in inp:
            val = inp[key]
            if isinstance(val, str):
                return _truncate(f"{key}={val}", limit)
    # Fallback: compact JSON
    try:
        return _truncate(json.dumps(inp, ensure_ascii=True, separators=(",", ":")), limit)
    except TypeError:
        return _truncate(str(inp), limit)


_EXT_LANG: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "javascript", ".rs": "rust", ".go": "go", ".rb": "ruby",
    ".java": "java", ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
    ".sh": "bash", ".zsh": "bash", ".css": "css", ".html": "html",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".md": "markdown", ".sql": "sql", ".swift": "swift", ".kt": "kotlin",
}


def _detect_language(file_path: str) -> str:
    """Detect language from file extension for syntax hint."""
    suffix = PurePosixPath(file_path).suffix.lower() if file_path else ""
    return _EXT_LANG.get(suffix, "")


def _format_block_content(data: Any, label: str, max_lines: int = 30) -> str:
    """Format input or output data for the expanded view."""
    _m = PALETTE['text_muted']
    if data is None:
        return f"[{_m}]{label}: (none)[/{_m}]"
    if isinstance(data, str):
        text = data
    else:
        try:
            text = json.dumps(data, ensure_ascii=True, indent=2)
        except TypeError:
            text = str(data)
    lines = text.splitlines()
    total = len(lines)
    if total > max_lines:
        lines = lines[:max_lines]
        lines.append(f"... +{total - max_lines} more lines")
        text = "\n".join(lines)
    return f"[bold {_m}]{label}:[/bold {_m}]\n{escape(text)}"


def _format_observation(tool_name: str, tool_input: dict[str, Any] | None, obs_data: Any) -> str:
    """Format observation with tool-type-aware rendering."""
    _m = PALETTE['text_muted']
    if obs_data is None:
        return f"[{_m}]Output: (none)[/{_m}]"

    text = obs_data if isinstance(obs_data, str) else str(obs_data)
    lines = text.splitlines()
    total = len(lines)
    max_lines = 30

    # Add a language/context hint based on tool type
    hint = ""
    inp = tool_input or {}
    if tool_name in ("Read", "read"):
        lang = _detect_language(str(inp.get("file_path", "")))
        if lang:
            hint = f" [{_m}]({lang})[/{_m}]"
    elif tool_name in ("Grep", "grep"):
        pattern = str(inp.get("pattern", ""))
        if pattern:
            hint = f" [{_m}](pattern: {escape(pattern[:40])})[/{_m}]"
    elif tool_name in ("Bash", "bash"):
        hint = f" [{_m}](stdout)[/{_m}]"

    if total > max_lines:
        lines = lines[:max_lines]
        lines.append(f"... +{total - max_lines} more lines")
        text = "\n".join(lines)

    # Color the output based on tool type
    if tool_name in ("Read", "read", "Grep", "grep"):
        # File content: dim green tint for readability
        colored = escape(text)
    elif tool_name in ("Bash", "bash"):
        # Shell output: preserve as-is
        colored = escape(text)
    else:
        colored = escape(text)

    return f"[bold {_m}]Output{hint}:[/bold {_m}]\n{colored}"


class ToolCallBlock(Vertical):
    """Expandable tool call with typed content dispatch.

    When collapsed: Shows tool name + brief input summary (one line).
    When expanded: Shows full input, full observation output.
    """

    expanded: reactive[bool] = reactive(False)

    def __init__(
        self,
        tool_call: dict[str, Any],
        observation: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.tool_call = tool_call
        self.observation = observation
        self._tool_name = tool_call.get("name") or tool_call.get("tool_name") or "unknown"
        self._category = _tool_category(self._tool_name)
        self._border_color = _TOOL_BORDER_COLORS.get(self._category, _TOOL_BORDER_COLORS["default"])
        kwargs.setdefault("classes", "")
        kwargs["classes"] = (kwargs["classes"] + " tool-call-block").strip()
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield Static(
            self._render_text(expanded=False),
            markup=True,
            classes="tool-call-collapsed",
        )
        # Lazy: expanded content rendered on first expand, not at compose time
        expanded = Static(
            "",
            markup=True,
            classes="tool-call-expanded",
        )
        expanded.display = False
        yield expanded
        self._expanded_rendered = False

    def on_mount(self) -> None:
        self._sync_visibility()

    def watch_expanded(self, value: bool) -> None:
        if not self.is_mounted:
            return
        self._sync_visibility()

    def toggle_expanded(self) -> None:
        self.expanded = not self.expanded

    def _render_text(self, *, expanded: bool | None = None) -> str:
        """Build the current block markup without depending on mount-time updates."""
        if expanded is None:
            expanded = self.expanded
        color = self._border_color
        status = self.tool_call.get("status") or ""
        status_icon = {"success": f"[{PALETTE['success']}]OK[/{PALETTE['success']}]", "error": f"[{PALETTE['error']}]ERR[/{PALETTE['error']}]"}.get(
            status, f"[{PALETTE['text_muted']}]{escape(status)}[/{PALETTE['text_muted']}]" if status else ""
        )

        if not expanded:
            _m = PALETTE['text_muted']
            # Bash: show the command directly, not as key=value
            if self._tool_name in ("Bash", "bash"):
                inp = self.tool_call.get("input") or self.tool_call.get("arguments") or {}
                command = ""
                if isinstance(inp, dict):
                    command = str(inp.get("command") or inp.get("cmd") or "")
                summary = _truncate(command, 80) if command else format_tool_call_summary(self.tool_call)
                line = f"[{_m}]$[/{_m}] [{color}]{escape(summary)}[/{color}]"
            else:
                summary = format_tool_call_summary(self.tool_call)
                line = (
                    f"[{_m}]>[/{_m}] [{color}]{escape(self._tool_name)}[/{color}]"
                    f"  {escape(summary)}"
                )
            if status_icon:
                line += f"  {status_icon}"
            return line

        _m = PALETTE['text_muted']
        indicator = f"[{_m}]v[/{_m}]"
        header = f"{indicator} [{color}]{escape(self._tool_name)}[/{color}]"
        if status_icon:
            header += f"  {status_icon}"

        parts = [header, ""]

        # Input
        inp = self.tool_call.get("input") or self.tool_call.get("arguments")

        # Special rendering for Edit tool: inline diff
        if self._tool_name in ("Edit", "edit") and isinstance(inp, dict):
            old_str = inp.get("old_string", "")
            new_str = inp.get("new_string", "")
            file_path = inp.get("file_path", "")
            filename = PurePosixPath(file_path).name if file_path else ""
            if old_str or new_str:
                parts.append(f"[bold {PALETTE['text_muted']}]Diff:[/bold {PALETTE['text_muted']}]")
                diff_lines = render_diff_markup(old_str, new_str, filename)
                parts.extend(diff_lines)
            else:
                parts.append(_format_block_content(inp, "Input"))

        # Special rendering for Write tool: show content with file hint
        elif self._tool_name in ("Write", "write") and isinstance(inp, dict):
            file_path = inp.get("file_path", "")
            content = inp.get("content", "")
            if file_path:
                filename = PurePosixPath(file_path).name if file_path else ""
                suffix = PurePosixPath(file_path).suffix if file_path else ""
                parts.append(f"[bold {PALETTE['text_muted']}]Write: {escape(filename)}[/bold {PALETTE['text_muted']}]")
                if suffix:
                    parts.append(f"[dim]type: {escape(suffix)}[/dim]")
                if content:
                    content_lines = content.splitlines()
                    for cline in content_lines[:50]:
                        parts.append(f"  [{PALETTE['success']}]{escape(cline[:120])}[/{PALETTE['success']}]")
                    if len(content_lines) > 50:
                        parts.append(f"  [dim]... {len(content_lines) - 50} more lines[/dim]")
            else:
                parts.append(_format_block_content(inp, "Input"))
        else:
            parts.append(_format_block_content(inp, "Input"))

        if self.observation is not None:
            obs_content = self.observation.get("content") or self.observation.get("output")
            parts.append("")
            parts.append(_format_observation(self._tool_name, inp if isinstance(inp, dict) else None, obs_content))

        return "\n".join(parts)

    def _sync_visibility(self) -> None:
        collapsed = self.query_one(".tool-call-collapsed", Static)
        expanded = self.query_one(".tool-call-expanded", Static)
        collapsed.display = not self.expanded
        expanded.display = self.expanded
        if self.expanded:
            # Lazy render: populate expanded content on first expand
            if not getattr(self, "_expanded_rendered", False):
                expanded.update(self._render_text(expanded=True))
                self._expanded_rendered = True
            self.add_class("-expanded")
        else:
            self.remove_class("-expanded")

    def get_copyable_text(self) -> str:
        """Return plain text content for clipboard copy."""
        parts = [f"Tool: {self._tool_name}"]
        inp = self.tool_call.get("input") or self.tool_call.get("arguments")
        if inp is not None:
            if isinstance(inp, str):
                parts.append(f"Input: {inp}")
            else:
                try:
                    parts.append(f"Input: {json.dumps(inp, indent=2)}")
                except TypeError:
                    parts.append(f"Input: {inp}")
        if self.observation is not None:
            obs = self.observation.get("content") or self.observation.get("output")
            if obs is not None:
                parts.append(f"Output: {obs}")
        return "\n".join(parts)
