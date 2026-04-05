"""Step block widget for the conversation replay and trace screens."""

from __future__ import annotations

from typing import Any

from rich.markdown import Markdown as RichMarkdown
from rich.console import RenderableType
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Static

from ..utils import _format_offset, escape
from .tool_call_block import ToolCallBlock


class StepBlock(Vertical):
    """A single step rendered as a chat block."""

    expanded: reactive[bool] = reactive(False)
    selected: reactive[bool] = reactive(False)

    def __init__(
        self,
        step: dict[str, Any],
        step_index: int,
        trace_start: str | None = None,
        agent_name: str = "Assistant",
        timestamp_label: str | None = None,
        collapse_content: bool = False,
        collapsed_lines: int = 5,
        render_tool_calls: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.step = step
        self.step_index = step_index
        self.trace_start = trace_start
        self._role = step.get("role", "assistant")
        self._agent_name = agent_name
        self._timestamp_label = timestamp_label
        self._collapse_content = collapse_content
        self._collapsed_lines = collapsed_lines
        self._render_tool_calls = render_tool_calls

    def compose(self) -> ComposeResult:
        initial_header = Static(self._header_text(), markup=True, classes="step-header")
        initial_content = self.step.get("content") or ""
        content_renderable: RenderableType | str = self.step.get("_content_renderable") or ""
        if not content_renderable and initial_content:
            if self.step.get("_content_markdown", True):
                content_renderable = RichMarkdown(initial_content)
            else:
                content_renderable = initial_content
        yield initial_header
        yield Static(content_renderable, classes="step-content")
        yield Static("", markup=True, classes="step-preview")
        yield Static("", markup=True, classes="step-hint")

        if self._render_tool_calls:
            tool_calls = self.step.get("tool_calls") or []
            observations = self.step.get("observations") or []
            for i, tc in enumerate(tool_calls):
                obs = observations[i] if i < len(observations) else None
                yield ToolCallBlock(tc, observation=obs)

    def on_mount(self) -> None:
        role = self._role
        if role == "user":
            self.add_class("user-message")
        elif role == "assistant":
            self.add_class("agent-message")
        elif role == "system":
            self.add_class("system-message")
        else:
            self.add_class("agent-message")
        self._sync_selected()
        self._sync_visibility()
        self.call_after_refresh(lambda: self.refresh(layout=True))

    def watch_expanded(self, _value: bool) -> None:
        if self.is_mounted:
            self._sync_visibility()

    def watch_selected(self, _value: bool) -> None:
        self._sync_selected()

    def _sync_selected(self) -> None:
        if self.selected:
            self.add_class("-selected")
        else:
            self.remove_class("-selected")

    def _time_text(self) -> str:
        if self._timestamp_label is not None:
            return self._timestamp_label
        timestamp = self.step.get("timestamp")
        return _format_offset(self.trace_start, timestamp)

    def _header_text(self) -> str:
        time_text = self._time_text()
        time_markup = f"  [#666666]{escape(time_text)}[/#666666]" if time_text else ""

        if self._role == "user":
            return f"[bold #5C9CF5]User[/bold #5C9CF5]{time_markup}"
        if self._role == "assistant":
            return f"[bold #E0E0E0]{escape(self._agent_name)}[/bold #E0E0E0]{time_markup}"
        if self._role == "system":
            return f"[#666666]System[/#666666]{time_markup}"
        return f"[#666666]{escape(self._role)}[/#666666]{time_markup}"

    def _preview_text(self, content: str) -> tuple[str, int]:
        lines = content.splitlines()
        preview_lines = [
            line.lstrip() if line.strip() else ""
            for line in lines[:3]
        ]
        remaining = max(0, len(lines) - len(preview_lines))
        return "\n".join(preview_lines), remaining

    def _sync_render(self) -> None:
        if not self.is_mounted:
            return
        self.query_one(".step-header", Static).update(self._header_text())
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        if not self.is_mounted:
            return

        content_widget = self.query_one(".step-content", Static)
        preview = self.query_one(".step-preview", Static)
        hint = self.query_one(".step-hint", Static)
        content = self.step.get("_content_plain") or self.step.get("content") or ""
        should_collapse = self._collapse_content and len(content.splitlines()) > self._collapsed_lines

        if not content:
            content_widget.display = False
            preview.display = False
            hint.display = False
        elif self.expanded or not should_collapse:
            content_widget.display = True
            preview.display = False
            hint.display = False
        else:
            preview_text, remaining = self._preview_text(content)
            preview.update(escape(preview_text))
            hint.update(f"[#666666]... +{remaining} lines (enter to expand)[/#666666]")
            content_widget.display = False
            preview.display = True
            hint.display = True

        if self._render_tool_calls:
            for block in self.query(ToolCallBlock):
                if block.expanded != self.expanded:
                    block.expanded = self.expanded

    def get_copyable_text(self) -> str:
        """Return plain text content for clipboard copy."""
        content = self.step.get("_content_plain") or self.step.get("content") or ""
        parts = [f"[{self._role}] {content}"]
        for tc in self.step.get("tool_calls") or []:
            name = tc.get("name") or tc.get("tool_name") or "unknown"
            parts.append(f"  Tool: {name}")
        return "\n".join(parts)
