"""Expandable tool call widget for the replay screen."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Static

from ..utils import _truncate, escape, _FILE_TOOLS, _WEB_TOOLS
from .diff_view import render_diff_markup

# Tool-type border colors (Rich hex, not CSS tokens)
_TOOL_BORDER_COLORS: dict[str, str] = {
    "file": "#22C55E",
    "web": "#EAB308",
    "agent": "#22D3EE",
    "default": "#444444",
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


def _format_block_content(data: Any, label: str) -> str:
    """Format input or output data for the expanded view."""
    if data is None:
        return f"[#666666]{label}: (none)[/#666666]"
    if isinstance(data, str):
        text = data
    else:
        try:
            text = json.dumps(data, ensure_ascii=True, indent=2)
        except TypeError:
            text = str(data)
    # Limit very long outputs to avoid overwhelming the TUI
    lines = text.splitlines()
    if len(lines) > 200:
        lines = lines[:200]
        lines.append(f"... ({len(text.splitlines()) - 200} more lines)")
        text = "\n".join(lines)
    return f"[bold #666666]{label}:[/bold #666666]\n{escape(text)}"


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
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield Static(
            self._render_text(expanded=False),
            markup=True,
            classes="tool-call-block tool-call-block-collapsed",
        )
        expanded = Static(
            self._render_text(expanded=True),
            markup=True,
            classes="tool-call-block tool-call-block-expanded",
        )
        expanded.display = False
        yield expanded

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
        status_icon = {"success": "[#22C55E]OK[/#22C55E]", "error": "[#EF4444]ERR[/#EF4444]"}.get(
            status, f"[#666666]{escape(status)}[/#666666]" if status else ""
        )

        if not expanded:
            summary = format_tool_call_summary(self.tool_call)
            line = (
                f"[#666666]>[/#666666] [{color}]{escape(self._tool_name)}[/{color}]"
                f"  {escape(summary)}"
            )
            if status_icon:
                line += f"  {status_icon}"
            return line

        indicator = "[#666666]v[/#666666]"
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
                parts.append("[bold #666666]Diff:[/bold #666666]")
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
                parts.append(f"[bold #666666]Write: {escape(filename)}[/bold #666666]")
                if suffix:
                    parts.append(f"[dim]type: {escape(suffix)}[/dim]")
                if content:
                    content_lines = content.splitlines()
                    for cline in content_lines[:50]:
                        parts.append(f"  [#22C55E]{escape(cline[:120])}[/#22C55E]")
                    if len(content_lines) > 50:
                        parts.append(f"  [dim]... {len(content_lines) - 50} more lines[/dim]")
            else:
                parts.append(_format_block_content(inp, "Input"))
        else:
            parts.append(_format_block_content(inp, "Input"))

        if self.observation is not None:
            obs_content = self.observation.get("content") or self.observation.get("output")
            parts.append("")
            parts.append(_format_block_content(obs_content, "Output"))

        return "\n".join(parts)

    def _sync_visibility(self) -> None:
        collapsed = self.query_one(".tool-call-block-collapsed", Static)
        expanded = self.query_one(".tool-call-block-expanded", Static)
        collapsed.display = not self.expanded
        expanded.display = self.expanded
        if self.expanded:
            expanded.add_class("-expanded")
            collapsed.remove_class("-expanded")
        else:
            collapsed.remove_class("-expanded")
            expanded.remove_class("-expanded")

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
