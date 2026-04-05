"""Conversation replay -- the DEFAULT view for a selected trace.

Shows the full conversation as a chat-style block stream:
- User messages: full width, colored background
- Agent responses: full width, default background
- Tool calls: expandable, show name + status collapsed, full input/output expanded
"""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from ..messages import FlashMessage
from ..utils import _single_line, _truncate, escape
from ..widgets.help_overlay import HelpOverlay
from ..widgets.key_bar import KeyBar
from ..widgets.step_block import StepBlock
from ..widgets.tool_call_block import ToolCallBlock

logger = logging.getLogger(__name__)


class _ReplayTopBar(Static):
    """Compact header showing trace context in the replay screen."""

    def __init__(self, trace: dict[str, Any], **kwargs: Any) -> None:
        super().__init__("", markup=True, **kwargs)
        self._trace = trace

    def on_mount(self) -> None:
        trace = self._trace
        task = _truncate(
            trace.get("task", {}).get("description") or "No description", 60
        )
        agent = trace.get("agent", {}).get("name", "unknown")
        model = trace.get("agent", {}).get("model", "unknown")
        steps = trace.get("metrics", {}).get("total_steps", len(trace.get("steps", [])))
        self.update(
            f"[bold #F97316]Replay[/bold #F97316]  "
            f"[bold]{escape(task)}[/bold]  "
            f"[#666666]{escape(str(agent))} / {escape(str(model))} / {steps} steps[/#666666]"
        )


class InspectReplayScreen(Screen):
    """Conversation replay, the DEFAULT view for a selected trace.

    Shows the full conversation as a chat-style block stream with
    expandable tool call blocks.
    """

    CSS_PATH = "replay.tcss"

    BINDINGS = [
        Binding("j", "scroll_down", "Down", show=False, priority=True),
        Binding("k", "scroll_up", "Up", show=False, priority=True),
        Binding("enter", "toggle_expand", "Expand", priority=True),
        Binding("v", "switch_inspector", "Inspector", priority=True),
        Binding("y", "copy_block", "Copy", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("question_mark", "toggle_help", "Help", key_display="?"),
    ]

    def __init__(self, trace: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.trace = trace
        self._focused_index: int = 0
        self._step_blocks: list[StepBlock] = []

    # ── Composition ──────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield HelpOverlay(id="help-overlay")
        with Vertical(id="replay-shell"):
            yield _ReplayTopBar(self.trace, id="replay-topbar")
            with VerticalScroll(id="replay-scroll"):
                trace_start = self.trace.get("timestamp_start")
                steps = self.trace.get("steps") or []
                for i, step in enumerate(steps):
                    yield StepBlock(step, step_index=i, trace_start=trace_start)
            yield KeyBar(id="keybar")

    # ── Lifecycle ────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.query_one(KeyBar).set_mode("replay")
        # Collect step block references for navigation
        self._step_blocks = list(self.query(StepBlock))
        scroll = self.query_one("#replay-scroll", VerticalScroll)
        self.set_focus(scroll)

    # ── Navigation ───────────────────────────────────────────────

    def action_scroll_down(self) -> None:
        scroll = self.query_one("#replay-scroll", VerticalScroll)
        scroll.scroll_down(animate=False)

    def action_scroll_up(self) -> None:
        scroll = self.query_one("#replay-scroll", VerticalScroll)
        scroll.scroll_up(animate=False)

    # ── Expand / collapse ────────────────────────────────────────

    def action_toggle_expand(self) -> None:
        """Toggle expansion of the tool call block nearest to viewport center."""
        tool_blocks = list(self.query(ToolCallBlock))
        if not tool_blocks:
            return
        scroll = self.query_one("#replay-scroll", VerticalScroll)
        viewport_mid = scroll.scroll_y + scroll.size.height // 2

        best: ToolCallBlock | None = None
        best_dist = float("inf")
        for block in tool_blocks:
            block_y = block.virtual_region.y
            dist = abs(block_y - viewport_mid)
            if dist < best_dist:
                best_dist = dist
                best = block

        if best is not None:
            best.toggle_expanded()

    # ── View switching ───────────────────────────────────────────

    def action_switch_inspector(self) -> None:
        """Switch to InspectScreen (analytical view)."""
        from .inspect import InspectScreen

        self.app.switch_screen(InspectScreen(self.trace))

    # ── Copy ─────────────────────────────────────────────────────

    def action_copy_block(self) -> None:
        """Copy the content of the nearest block to clipboard."""
        # Try tool blocks first (more specific)
        tool_blocks = list(self.query(ToolCallBlock))
        step_blocks = self._step_blocks
        all_blocks = tool_blocks + step_blocks  # type: ignore[operator]

        if not all_blocks:
            return

        scroll = self.query_one("#replay-scroll", VerticalScroll)
        viewport_mid = scroll.scroll_y + scroll.size.height // 2

        best: Any = None
        best_dist = float("inf")
        for block in all_blocks:
            block_y = block.virtual_region.y
            dist = abs(block_y - viewport_mid)
            if dist < best_dist:
                best_dist = dist
                best = block

        if best is not None and hasattr(best, "get_copyable_text"):
            text = best.get_copyable_text()
            try:
                import pyperclip
                pyperclip.copy(text)
                self.post_message(FlashMessage("[bold #22C55E]Copied to clipboard[/bold #22C55E]"))
            except ImportError:
                self.notify("Install pyperclip for clipboard support", severity="warning")
            except Exception:
                self.notify("Clipboard copy failed", severity="warning")

    # ── Help ─────────────────────────────────────────────────────

    def action_toggle_help(self) -> None:
        self.query_one(HelpOverlay).toggle()

    # ── Back ─────────────────────────────────────────────────────

    def action_back(self) -> None:
        help_overlay = self.query_one(HelpOverlay)
        if help_overlay.display:
            help_overlay.toggle()
            return
        self.app.pop_screen()

    # ── Flash messages ───────────────────────────────────────────

    def on_flash_message(self, message: FlashMessage) -> None:
        self.query_one(KeyBar).flash(message.text, message.duration)
