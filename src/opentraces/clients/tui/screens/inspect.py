"""Analytical trace inspector with sidebar panels.

Accessed via 'v' key from InspectReplayScreen.
Shows structured panels: step index, quality scores, security flags,
timing bars, and token waterfall.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import ListView, ListItem, Static

from ..messages import FlashMessage
from ..utils import (
    _format_offset,
    _role_color_ansi,
    _single_line,
    _tool_color_ansi,
    escape,
)
from ..widgets.git_context import GitStatePanel, ForkPreviewModal
from ..widgets.help_overlay import HelpOverlay
from ..widgets.interestingness import compute_interestingness, interestingness_marker
from ..widgets.key_bar import KeyBar
from ..widgets.quality_panel import QualityPanel
from ..widgets.security_panel import SecurityPanel
from ..widgets.timing_bar import TimingBar
from ..widgets.token_waterfall import TokenWaterfall

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step index items for the sidebar list
# ---------------------------------------------------------------------------


class StepIndexItem(ListItem):
    """A single step entry in the sidebar step index."""

    def __init__(
        self,
        step: dict[str, Any],
        trace_start: str | None,
        interest_score: float,
        **kwargs: object,
    ) -> None:
        self.step = step
        self.interest_score = interest_score
        idx = step.get("step_index", 0)
        role = step.get("role", "?")
        offset = _format_offset(trace_start, step.get("timestamp"))

        marker = interestingness_marker(interest_score)
        role_color = _role_color_ansi(role)

        # Primary tool name if any
        tool_calls = step.get("tool_calls", [])
        tool_label = ""
        if tool_calls:
            tool_name = tool_calls[0].get("tool_name", "")
            tool_color = _tool_color_ansi(tool_name)
            tool_label = f" [{tool_color}]{escape(tool_name[:12])}[/{tool_color}]"

        content_preview = _single_line(step.get("content", "") or "", 16)

        label = (
            f"{marker}[dim]{idx:>3}[/dim] "
            f"[{role_color}]{role.upper()[:4]:4}[/{role_color}]"
            f"{tool_label}"
            f" [dim]{escape(content_preview)}[/dim]"
        )
        if offset:
            label += f" [dim]{offset}[/dim]"

        super().__init__(Static(label, markup=True), **kwargs)


# ---------------------------------------------------------------------------
# Block stream: expanded step detail
# ---------------------------------------------------------------------------


class StepBlock(Static):
    """Expandable step block in the center stream."""

    def __init__(
        self,
        step: dict[str, Any],
        trace_start: str | None,
        trace_end: str | None,
        interest_score: float,
        show_timing: bool = False,
        **kwargs: object,
    ) -> None:
        super().__init__("", markup=True, **kwargs)
        self._step = step
        self._trace_start = trace_start
        self._trace_end = trace_end
        self._interest_score = interest_score
        self._show_timing = show_timing
        self._expanded = False

    def on_mount(self) -> None:
        self._render_block()

    def toggle_expand(self) -> None:
        self._expanded = not self._expanded
        self._render_block()

    def _render_block(self) -> None:
        step = self._step
        idx = step.get("step_index", 0)
        role = step.get("role", "?")
        role_color = _role_color_ansi(role)
        marker = interestingness_marker(self._interest_score)
        offset = _format_offset(self._trace_start, step.get("timestamp"))

        # Header line
        tool_calls = step.get("tool_calls", [])
        tool_names = ", ".join(tc.get("tool_name", "?") for tc in tool_calls[:3])
        tool_part = f" [dim]{escape(tool_names)}[/dim]" if tool_names else ""

        header = (
            f"{marker}[bold][{role_color}]{role.upper()}[/{role_color}][/bold] "
            f"[dim]step {idx}[/dim]{tool_part}"
        )
        if offset:
            header += f"  [dim]{offset}[/dim]"

        lines: list[str] = [header]

        if not self._expanded:
            # Collapsed: single-line preview
            content = _single_line(step.get("content", "") or "", 80)
            if content:
                lines.append(f"  [dim]{escape(content)}[/dim]")
        else:
            # Expanded: full content and tool details
            content = step.get("content") or ""
            if content:
                lines.append("")
                for line in content.splitlines()[:30]:
                    lines.append(f"  {escape(line[:120])}")
                if len(content.splitlines()) > 30:
                    lines.append(f"  [dim]... {len(content.splitlines())} total lines[/dim]")

            # Reasoning content
            reasoning = step.get("reasoning_content")
            if reasoning:
                lines.append("")
                lines.append("  [bold dim]Reasoning:[/bold dim]")
                for line in str(reasoning).splitlines()[:10]:
                    lines.append(f"  [dim]{escape(line[:120])}[/dim]")

            # Tool calls
            for tc in tool_calls:
                tool_name = tc.get("tool_name", "?")
                tool_color = _tool_color_ansi(tool_name)
                dur = tc.get("duration_ms")
                dur_str = f" {dur}ms" if dur else ""
                lines.append(f"  [{tool_color}]{escape(tool_name)}[/{tool_color}]{dur_str}")

                # Tool input preview
                tool_input = tc.get("input", {})
                if tool_input:
                    preview = _single_line(tool_input, 100)
                    lines.append(f"    [dim]{escape(preview)}[/dim]")

            # Observations
            observations = step.get("observations", [])
            for obs in observations:
                err = obs.get("error")
                if err:
                    lines.append(f"  [#EF4444]ERROR: {escape(_single_line(err, 80))}[/#EF4444]")
                summary = obs.get("output_summary")
                if summary:
                    lines.append(f"  [dim]{escape(_single_line(summary, 100))}[/dim]")

            # Token usage
            usage = step.get("token_usage", {})
            inp = usage.get("input_tokens", 0)
            out = usage.get("output_tokens", 0)
            cache_r = usage.get("cache_read_tokens", 0)
            if inp or out:
                tok_line = f"  [dim]tokens: {inp}i / {out}o"
                if cache_r:
                    tok_line += f" / {cache_r} cache"
                tok_line += "[/dim]"
                lines.append(tok_line)

        # Timing bar overlay
        if self._show_timing:
            lines.append("")
            # Render inline timing bar
            bar = self._build_timing_bar()
            lines.append(f"  {bar}")

        lines.append("")  # Spacing between blocks
        self.update("\n".join(lines))

    def _build_timing_bar(self) -> str:
        """Build a compact inline timing bar string."""
        from ..widgets.timing_bar import TimingBar as _TB, _tool_color_for_step, _FULL_BLOCK, _PARTIAL_BLOCKS
        from ..utils import _parse_iso

        trace_start = _parse_iso(self._trace_start)
        trace_end = _parse_iso(self._trace_end)
        step_ts = _parse_iso(self._step.get("timestamp"))

        if not trace_start or not trace_end or not step_ts:
            return "[dim]" + "." * 30 + "[/dim]"

        trace_dur = (trace_end - trace_start).total_seconds()
        if trace_dur <= 0:
            return "[dim]" + "." * 30 + "[/dim]"

        tool_calls = self._step.get("tool_calls", [])
        step_dur_ms = sum(tc.get("duration_ms", 0) or 0 for tc in tool_calls)
        step_dur = step_dur_ms / 1000.0 if step_dur_ms > 0 else 1.0

        bar_width = 30
        offset_s = max(0, (step_ts - trace_start).total_seconds())
        left_frac = offset_s / trace_dur
        bar_frac = min(step_dur / trace_dur, 1.0 - left_frac)
        left_cells = left_frac * bar_width
        bar_cells = max(0.125, bar_frac * bar_width)
        color = _tool_color_for_step(self._step)

        chars: list[str] = []
        for i in range(bar_width):
            cell_start = float(i)
            cell_end = float(i + 1)
            bar_start = left_cells
            bar_end = left_cells + bar_cells
            if cell_end <= bar_start or cell_start >= bar_end:
                chars.append(" ")
            elif cell_start >= bar_start and cell_end <= bar_end:
                chars.append(_FULL_BLOCK)
            else:
                overlap = min(cell_end, bar_end) - max(cell_start, bar_start)
                eighth = max(0, min(7, int(overlap * 8)))
                chars.append(_PARTIAL_BLOCKS[eighth])

        return f"[{color}]{''.join(chars)}[/{color}]"


# ---------------------------------------------------------------------------
# InspectScreen
# ---------------------------------------------------------------------------


class InspectScreen(Screen):
    """Analytical trace inspector with sidebar panels.

    Accessed via 'v' key from InspectReplayScreen.
    Shows structured panels: step index, quality scores, security flags,
    timing bars, and token waterfall.
    """

    CSS_PATH = "inspect.tcss"

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False, priority=True),
        Binding("k", "cursor_up", "Up", show=False, priority=True),
        Binding("enter", "toggle_expand", "Expand", priority=True),
        Binding("space", "toggle_expand", "Expand", show=False, priority=True),
        Binding("v", "switch_to_replay", "Replay", priority=True),
        Binding("i", "sort_interestingness", "Interestingness", priority=True),
        Binding("n", "tag_interesting", "Tag", priority=True),
        Binding("y", "copy_block", "Copy", priority=True),
        Binding("t", "toggle_timing", "Timing", priority=True),
        Binding("s", "toggle_security", "Security", priority=True),
        Binding("g", "toggle_git", "Git", priority=True),
        Binding("T", "fork_from_commit", "Fork", priority=True),
        Binding("1", "focus_sidebar", "Sidebar", show=False, priority=True),
        Binding("2", "focus_blockstream", "Blocks", show=False, priority=True),
        Binding("3", "focus_bottom", "Bottom", show=False, priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("question_mark", "toggle_help", "Help", key_display="?"),
    ]

    def __init__(self, trace: dict[str, Any], **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._trace = trace
        self._steps: list[dict[str, Any]] = trace.get("steps", [])
        self._interest_scores: list[float] = []
        self._sorted_by_interest = False
        self._show_timing = False
        self._show_security_overlay = False
        self._selected_block_index = 0
        self._tagged_steps: set[int] = set()
        self._show_git_panel = False
        # Resolve repo path from trace metadata or cwd
        repo_str = trace.get("environment", {}).get("cwd") or trace.get("repo_path")
        self._repo_path: Path | None = Path(repo_str) if repo_str else None

    # ── Composition ──────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield HelpOverlay(id="help-overlay")
        with Vertical(id="inspect-shell"):
            yield Static("", id="inspect-topbar", markup=True)
            with Horizontal(id="inspect-workspace"):
                with Vertical(id="inspect-sidebar"):
                    yield Static("[bold]Steps[/bold]", markup=True)
                    yield ListView(id="step-index-list")
                    yield QualityPanel(self._trace, id="quality-panel")
                    yield SecurityPanel(self._trace, id="security-panel")
                with Vertical(id="inspect-center"):
                    yield Static("", id="block-stream", markup=True)
            with Vertical(id="inspect-bottom"):
                yield Static("[bold]Token Waterfall[/bold]", markup=True)
                yield TokenWaterfall(self._steps, id="token-waterfall")
            yield GitStatePanel(id="git-state-panel")
            yield KeyBar(id="keybar")

    # ── Lifecycle ────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.query_one(KeyBar).set_mode("inspect")
        self._interest_scores = compute_interestingness(self._steps)
        self._populate_step_index()
        self._render_block_stream()
        self._update_topbar()
        # Git panel starts hidden
        self.query_one("#git-state-panel", GitStatePanel).display = False
        self.set_focus(self.query_one("#step-index-list", ListView))

    # ── Data population ──────────────────────────────────────────

    def _update_topbar(self) -> None:
        trace = self._trace
        task = _single_line(trace.get("task", {}).get("description") or "No description", 60)
        agent = trace.get("agent", {}).get("name", "unknown")
        model = trace.get("agent", {}).get("model", "")
        total_steps = len(self._steps)
        flags = len(trace.get("_security_flags", []))

        top_text = (
            f"[bold white]inspect[/bold white]  "
            f"[dim]{escape(task)}[/dim]\n"
            f"[dim]{escape(agent)}[/dim]  "
            f"[dim]{escape(str(model)[:40])}[/dim]  "
            f"[dim]{total_steps} steps[/dim]"
        )
        if flags:
            top_text += f"  [#EF4444]{flags} flags[/#EF4444]"

        self.query_one("#inspect-topbar", Static).update(top_text)

    def _populate_step_index(self) -> None:
        """Fill the sidebar step index list."""
        step_list = self.query_one("#step-index-list", ListView)
        step_list.clear()

        trace_start = self._trace.get("timestamp_start")

        for i, step in enumerate(self._steps):
            score = self._interest_scores[i] if i < len(self._interest_scores) else 0.0
            item = StepIndexItem(step, trace_start, score)
            step_list.append(item)

    def _render_block_stream(self) -> None:
        """Render the center block stream as Rich markup text."""
        if not self._steps:
            self.query_one("#block-stream", Static).update(
                "[dim]No steps in this trace[/dim]"
            )
            return

        trace_start = self._trace.get("timestamp_start")
        trace_end = self._trace.get("timestamp_end")

        lines: list[str] = []
        for i, step in enumerate(self._steps):
            score = self._interest_scores[i] if i < len(self._interest_scores) else 0.0
            idx = step.get("step_index", i)
            role = step.get("role", "?")
            role_color = _role_color_ansi(role)
            marker = interestingness_marker(score)
            offset = _format_offset(trace_start, step.get("timestamp"))

            # Tool calls summary
            tool_calls = step.get("tool_calls", [])
            tool_names = ", ".join(tc.get("tool_name", "?") for tc in tool_calls[:3])
            tool_part = f" [dim]{escape(tool_names)}[/dim]" if tool_names else ""

            # Tagged indicator
            tag_mark = " [bold #22C55E]*[/bold #22C55E]" if idx in self._tagged_steps else ""

            header = (
                f"{marker}[bold][{role_color}]{role.upper()}[/{role_color}][/bold] "
                f"[dim]step {idx}[/dim]{tool_part}{tag_mark}"
            )
            if offset:
                header += f"  [dim]{offset}[/dim]"
            lines.append(header)

            # Content preview
            is_selected = (i == self._selected_block_index)
            content = step.get("content", "") or ""
            if is_selected:
                # Expanded view
                if content:
                    for line in content.splitlines()[:30]:
                        lines.append(f"  {escape(line[:120])}")
                    if len(content.splitlines()) > 30:
                        lines.append(f"  [dim]... {len(content.splitlines())} total lines[/dim]")

                # Reasoning
                reasoning = step.get("reasoning_content")
                if reasoning:
                    lines.append("  [bold dim]Reasoning:[/bold dim]")
                    for line in str(reasoning).splitlines()[:10]:
                        lines.append(f"  [dim]{escape(line[:120])}[/dim]")

                # Tool details
                for tc in tool_calls:
                    tool_name = tc.get("tool_name", "?")
                    tool_color = _tool_color_ansi(tool_name)
                    dur = tc.get("duration_ms")
                    dur_str = f" {dur}ms" if dur else ""
                    lines.append(f"  [{tool_color}]{escape(tool_name)}[/{tool_color}]{dur_str}")
                    tool_input = tc.get("input", {})
                    if tool_input:
                        preview = _single_line(tool_input, 100)
                        lines.append(f"    [dim]{escape(preview)}[/dim]")

                # Observations
                for obs in step.get("observations", []):
                    err = obs.get("error")
                    if err:
                        lines.append(f"  [#EF4444]ERROR: {escape(_single_line(err, 80))}[/#EF4444]")
                    summary = obs.get("output_summary")
                    if summary:
                        lines.append(f"  [dim]{escape(_single_line(summary, 100))}[/dim]")

                # Token usage
                usage = step.get("token_usage", {})
                inp = usage.get("input_tokens", 0)
                out = usage.get("output_tokens", 0)
                cache_r = usage.get("cache_read_tokens", 0)
                if inp or out:
                    tok_line = f"  [dim]tokens: {inp}i / {out}o"
                    if cache_r:
                        tok_line += f" / {cache_r} cache"
                    tok_line += "[/dim]"
                    lines.append(tok_line)

                # Security overlay
                if self._show_security_overlay:
                    step_flags = [
                        f for f in self._trace.get("_security_flags", [])
                        if f.get("step_index") == idx
                    ]
                    if step_flags:
                        lines.append("  [bold #EF4444]Security flags:[/bold #EF4444]")
                        for sf in step_flags:
                            sev = sf.get("severity", "info")
                            reason = sf.get("reason", "")
                            lines.append(f"    [#EF4444]{sev}: {escape(_single_line(reason, 60))}[/#EF4444]")
            else:
                # Collapsed: single-line preview
                preview = _single_line(content, 80)
                if preview:
                    lines.append(f"  [dim]{escape(preview)}[/dim]")

            # Timing bar overlay
            if self._show_timing:
                lines.append(self._inline_timing(step, trace_start, trace_end))

            lines.append("")  # Spacing

        # Highlight selected block with a marker
        self.query_one("#block-stream", Static).update("\n".join(lines))

    def _inline_timing(
        self,
        step: dict[str, Any],
        trace_start: str | None,
        trace_end: str | None,
    ) -> str:
        """Build a compact inline timing bar string."""
        from ..utils import _parse_iso
        from ..widgets.timing_bar import (
            _FULL_BLOCK,
            _PARTIAL_BLOCKS,
            _tool_color_for_step,
        )

        ts_start = _parse_iso(trace_start)
        ts_end = _parse_iso(trace_end)
        step_ts = _parse_iso(step.get("timestamp"))

        if not ts_start or not ts_end or not step_ts:
            return "  [dim]" + "." * 30 + "[/dim]"

        trace_dur = (ts_end - ts_start).total_seconds()
        if trace_dur <= 0:
            return "  [dim]" + "." * 30 + "[/dim]"

        tool_calls = step.get("tool_calls", [])
        step_dur_ms = sum(tc.get("duration_ms", 0) or 0 for tc in tool_calls)
        step_dur = step_dur_ms / 1000.0 if step_dur_ms > 0 else 1.0

        bar_width = 30
        offset_s = max(0, (step_ts - ts_start).total_seconds())
        left_frac = offset_s / trace_dur
        bar_frac = min(step_dur / trace_dur, 1.0 - left_frac)
        left_cells = left_frac * bar_width
        bar_cells = max(0.125, bar_frac * bar_width)
        color = _tool_color_for_step(step)

        chars: list[str] = []
        for ci in range(bar_width):
            cell_start = float(ci)
            cell_end = float(ci + 1)
            bar_start = left_cells
            bar_end = left_cells + bar_cells
            if cell_end <= bar_start or cell_start >= bar_end:
                chars.append(" ")
            elif cell_start >= bar_start and cell_end <= bar_end:
                chars.append(_FULL_BLOCK)
            else:
                overlap = min(cell_end, bar_end) - max(cell_start, bar_start)
                eighth = max(0, min(7, int(overlap * 8)))
                chars.append(_PARTIAL_BLOCKS[eighth])

        return f"  [{color}]{''.join(chars)}[/{color}]"

    # ── Actions ──────────────────────────────────────────────────

    def action_cursor_down(self) -> None:
        if self._selected_block_index < len(self._steps) - 1:
            self._selected_block_index += 1
            self._sync_selection()

    def action_cursor_up(self) -> None:
        if self._selected_block_index > 0:
            self._selected_block_index -= 1
            self._sync_selection()

    def _sync_selection(self) -> None:
        """Sync sidebar list, block stream, and waterfall to selected index."""
        step_list = self.query_one("#step-index-list", ListView)
        children = list(step_list.children)
        if 0 <= self._selected_block_index < len(children):
            step_list.index = self._selected_block_index
        self._render_block_stream()

        waterfall = self.query_one("#token-waterfall", TokenWaterfall)
        waterfall.scroll_to_step(self._selected_block_index)

    def action_toggle_expand(self) -> None:
        """Toggle expand/collapse of the currently selected block."""
        # Re-render will show expanded view for selected block
        self._render_block_stream()

    def action_switch_to_replay(self) -> None:
        """Switch back to InspectReplayScreen."""
        from .inspect_replay import InspectReplayScreen

        self.app.switch_screen(InspectReplayScreen(self._trace))

    def action_sort_interestingness(self) -> None:
        """Sort steps by interestingness score."""
        if not self._steps:
            return

        if self._sorted_by_interest:
            # Restore original order
            self._steps = self._trace.get("steps", [])[:]
            self._sorted_by_interest = False
            self.post_message(FlashMessage("[dim]Restored original order[/dim]"))
        else:
            # Sort by interestingness descending
            paired = list(zip(self._interest_scores, self._steps))
            paired.sort(key=lambda p: p[0], reverse=True)
            self._interest_scores = [p[0] for p in paired]
            self._steps = [p[1] for p in paired]
            self._sorted_by_interest = True
            self.post_message(FlashMessage("[bold #EAB308]Sorted by interestingness[/bold #EAB308]"))

        self._selected_block_index = 0
        self._populate_step_index()
        self._render_block_stream()

    def action_tag_interesting(self) -> None:
        """Tag the current step as an interesting pattern."""
        if not self._steps:
            return
        step = self._steps[self._selected_block_index]
        idx = step.get("step_index", self._selected_block_index)

        if idx in self._tagged_steps:
            self._tagged_steps.discard(idx)
            self.post_message(FlashMessage(f"[dim]Untagged step {idx}[/dim]"))
        else:
            self._tagged_steps.add(idx)
            self.post_message(FlashMessage(f"[bold #22C55E]Tagged step {idx}[/bold #22C55E]"))

        self._render_block_stream()

    def action_copy_block(self) -> None:
        """Copy the selected block content to clipboard."""
        if not self._steps:
            return
        step = self._steps[self._selected_block_index]
        content = step.get("content", "") or ""
        idx = step.get("step_index", self._selected_block_index)

        try:
            import pyperclip
            pyperclip.copy(content)
            self.post_message(FlashMessage(f"[bold #22C55E]Copied step {idx}[/bold #22C55E]"))
        except ImportError:
            self.post_message(FlashMessage("[dim]Install pyperclip for clipboard support[/dim]"))
        except Exception:
            self.post_message(FlashMessage("[#EF4444]Clipboard not available[/#EF4444]"))

    def action_toggle_timing(self) -> None:
        """Toggle timing bar overlay on each step."""
        self._show_timing = not self._show_timing
        state = "on" if self._show_timing else "off"
        self.post_message(FlashMessage(f"[dim]Timing overlay {state}[/dim]"))
        self._render_block_stream()

    def action_toggle_security(self) -> None:
        """Toggle security overlay on each step."""
        self._show_security_overlay = not self._show_security_overlay
        state = "on" if self._show_security_overlay else "off"
        self.post_message(FlashMessage(f"[dim]Security overlay {state}[/dim]"))
        self._render_block_stream()

    # ── Git actions ───────────────────────────────────────────────

    def _find_commit_sha_for_step(self, step: dict[str, Any]) -> str | None:
        """Extract a commit SHA from a step's observations.

        Scans Bash tool calls that contain "git commit" and looks for
        a SHA-like pattern in the observation output.
        """
        sha_re = re.compile(r"\b([0-9a-f]{7,40})\b")
        for tc in step.get("tool_calls", []):
            tool_name = tc.get("tool_name") or tc.get("name") or ""
            tool_input = tc.get("input") or tc.get("arguments") or {}
            command = ""
            if isinstance(tool_input, dict):
                command = tool_input.get("command", "")
            elif isinstance(tool_input, str):
                command = tool_input
            if tool_name.lower() != "bash" or "git commit" not in command:
                continue
            # Check observations for a SHA
            for obs in step.get("observations", []):
                output = obs.get("output") or obs.get("content") or ""
                if isinstance(output, str):
                    match = sha_re.search(output)
                    if match:
                        return match.group(1)
        return None

    def _find_nearest_commit_sha(self) -> str | None:
        """Find a commit SHA from the selected step or nearby steps."""
        if not self._steps:
            return None
        # Check selected step first, then scan outward
        idx = self._selected_block_index
        for offset in range(len(self._steps)):
            for candidate in (idx + offset, idx - offset):
                if 0 <= candidate < len(self._steps):
                    sha = self._find_commit_sha_for_step(self._steps[candidate])
                    if sha:
                        return sha
        return None

    def action_toggle_git(self) -> None:
        """Toggle git state panel showing commit info."""
        git_panel = self.query_one("#git-state-panel", GitStatePanel)
        if self._show_git_panel:
            git_panel.hide()
            self._show_git_panel = False
            self.post_message(FlashMessage("[dim]Git panel hidden[/dim]"))
            return

        sha = self._find_nearest_commit_sha()
        if not sha:
            self.post_message(FlashMessage("[#EF4444]No git commit found in trace[/#EF4444]"))
            return

        if not self._repo_path or not self._repo_path.is_dir():
            self.post_message(FlashMessage("[#EF4444]No git repo path available[/#EF4444]"))
            return

        git_panel.show_commit(sha, self._repo_path)
        self._show_git_panel = True

    def action_fork_from_commit(self) -> None:
        """Open the fork preview modal for the nearest commit."""
        sha = self._find_nearest_commit_sha()
        if not sha:
            self.post_message(FlashMessage("[#EF4444]No git commit found in trace[/#EF4444]"))
            return

        if not self._repo_path or not self._repo_path.is_dir():
            self.post_message(FlashMessage("[#EF4444]No git repo path available[/#EF4444]"))
            return

        trace_id = self._trace.get("trace_id", "unknown")

        def _on_dismiss(forked: bool) -> None:
            if forked:
                self.post_message(
                    FlashMessage(f"[bold #22C55E]Forked to opentraces/replay/{trace_id[:8]}-{self._selected_block_index}[/bold #22C55E]")
                )

        self.app.push_screen(
            ForkPreviewModal(
                commit_sha=sha,
                trace_id=trace_id,
                step_idx=self._selected_block_index,
                repo_path=self._repo_path,
            ),
            callback=_on_dismiss,
        )

    def action_focus_sidebar(self) -> None:
        self.set_focus(self.query_one("#step-index-list", ListView))

    def action_focus_blockstream(self) -> None:
        self.set_focus(self.query_one("#block-stream", Static))

    def action_focus_bottom(self) -> None:
        self.set_focus(self.query_one("#token-waterfall", TokenWaterfall))

    def action_back(self) -> None:
        help_overlay = self.query_one(HelpOverlay)
        if help_overlay.display:
            help_overlay.toggle()
            return
        self.app.pop_screen()

    def action_toggle_help(self) -> None:
        self.query_one(HelpOverlay).toggle()

    # ── Events ───────────────────────────────────────────────────

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Sync block stream when sidebar selection changes."""
        if event.item is None:
            return
        step_list = self.query_one("#step-index-list", ListView)
        idx = step_list.index
        if idx is not None and 0 <= idx < len(self._steps):
            self._selected_block_index = idx
            self._render_block_stream()
            waterfall = self.query_one("#token-waterfall", TokenWaterfall)
            waterfall.scroll_to_step(idx)

    def on_flash_message(self, message: FlashMessage) -> None:
        self.query_one(KeyBar).flash(message.text, message.duration)
