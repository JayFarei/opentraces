"""Parser comparison view -- side-by-side diff of two parser outputs.

Shows the same raw session parsed by two parsers, highlighting:
- Field-level differences between outputs
- Schema coverage (which fields each parser populates vs leaves null)
- Errors (missing attribution, broken parent_step links, etc.)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static

from ..messages import FlashMessage
from ..utils import _single_line, escape
from ..widgets.error_highlight import ErrorHighlight
from ..widgets.help_overlay import HelpOverlay
from ..widgets.key_bar import KeyBar
from ..widgets.schema_coverage import SchemaCoverage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _field_match_color(val_a: Any, val_b: Any) -> str:
    """Return hex color based on field comparison.

    Green (#22C55E): both present and equal.
    Yellow (#EAB308): both present but differ.
    Red (#EF4444): one is null/missing.
    """
    has_a = val_a is not None and val_a != "" and val_a != 0
    has_b = val_b is not None and val_b != "" and val_b != 0
    if has_a and has_b:
        return "#22C55E" if val_a == val_b else "#EAB308"
    if has_a != has_b:
        return "#EF4444"
    return "#6B7280"  # both missing, dim gray


def _get(d: dict[str, Any], path: str) -> Any:
    """Resolve dot-separated path."""
    parts = path.split(".")
    current: Any = d
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def _render_field_line(
    label: str, val_a: Any, val_b: Any, show_side: str = "both"
) -> str:
    """Render one comparison line with color-coded values."""
    color = _field_match_color(val_a, val_b)
    str_a = _short(val_a)
    str_b = _short(val_b)

    if show_side == "a":
        return f"[{color}]{label}:[/{color}] {escape(str_a)}"
    if show_side == "b":
        return f"[{color}]{label}:[/{color}] {escape(str_b)}"
    return f"[{color}]{label}:[/{color}] {escape(str_a)} [dim]|[/dim] {escape(str_b)}"


def _short(value: Any) -> str:
    """Shorten a value for display."""
    if value is None:
        return "(null)"
    s = str(value)
    if len(s) > 60:
        return s[:57] + "..."
    return s


def _render_trace_panel(trace: dict[str, Any], parser_name: str) -> str:
    """Render a single trace as a scrollable Rich-markup block."""
    lines: list[str] = []
    lines.append(f"[bold underline]{escape(parser_name)}[/bold underline]")
    lines.append("")

    # Task
    task = trace.get("task", {})
    desc = task.get("description") or "(null)"
    lines.append(f"[bold]Task:[/bold] {escape(_single_line(desc, 80))}")
    if task.get("repository"):
        lines.append(f"  repo: {escape(str(task.get('repository')))}")
    lines.append("")

    # Agent
    agent = trace.get("agent", {})
    lines.append(f"[bold]Agent:[/bold] {escape(str(agent.get('name', '?')))}")
    if agent.get("version"):
        lines.append(f"  version: {escape(str(agent.get('version')))}")
    if agent.get("model"):
        lines.append(f"  model: {escape(str(agent.get('model')))}")
    lines.append("")

    # Outcome
    outcome = trace.get("outcome", {})
    success = outcome.get("success")
    committed = outcome.get("committed", False)
    success_color = "#22C55E" if success else "#EF4444" if success is False else "#6B7280"
    lines.append(
        f"[bold]Outcome:[/bold] "
        f"[{success_color}]success={success}[/{success_color}], "
        f"committed={committed}"
    )
    if outcome.get("terminal_state"):
        lines.append(f"  terminal_state: {outcome.get('terminal_state')}")
    if outcome.get("reward") is not None:
        lines.append(f"  reward: {outcome.get('reward')}")
    lines.append("")

    # Metrics
    metrics = trace.get("metrics", {})
    lines.append("[bold]Metrics:[/bold]")
    lines.append(f"  steps: {metrics.get('total_steps', 0)}")
    lines.append(
        f"  tokens: {metrics.get('total_input_tokens', 0)}i "
        f"/ {metrics.get('total_output_tokens', 0)}o"
    )
    if metrics.get("total_duration_s") is not None:
        lines.append(f"  duration: {metrics.get('total_duration_s'):.1f}s")
    if metrics.get("cache_hit_rate") is not None:
        lines.append(f"  cache_hit: {metrics.get('cache_hit_rate'):.2%}")
    if metrics.get("estimated_cost_usd") is not None:
        lines.append(f"  cost: ${metrics.get('estimated_cost_usd'):.4f}")
    lines.append("")

    # Attribution
    attr = trace.get("attribution")
    if attr:
        files = attr.get("files", [])
        lines.append(f"[bold]Attribution:[/bold] {len(files)} file(s)")
        for af in files[:10]:
            path = af.get("path", "?")
            convs = len(af.get("conversations", []))
            lines.append(f"  {escape(path)} ({convs} conv)")
        if len(files) > 10:
            lines.append(f"  [dim]... {len(files) - 10} more[/dim]")
    else:
        lines.append("[bold]Attribution:[/bold] [dim](none)[/dim]")
    lines.append("")

    # Steps summary
    steps = trace.get("steps", [])
    lines.append(f"[bold]Steps:[/bold] {len(steps)}")
    for step in steps:
        idx = step.get("step_index", "?")
        role = step.get("role", "?")
        tool_calls = step.get("tool_calls", [])
        tool_names = ", ".join(tc.get("tool_name", "?") for tc in tool_calls[:3])
        content_preview = _single_line(step.get("content", "") or "", 40)

        step_line = f"  [dim]{idx:>3}[/dim] {role.upper()[:4]:4}"
        if tool_names:
            step_line += f" {escape(tool_names[:30])}"
        if content_preview:
            step_line += f" [dim]{escape(content_preview)}[/dim]"
        lines.append(step_line)

    return "\n".join(lines)


def _render_unified_diff(
    trace_a: dict[str, Any],
    trace_b: dict[str, Any],
    name_a: str,
    name_b: str,
) -> str:
    """Render a unified-style diff of the two traces."""
    lines: list[str] = []
    lines.append(f"[bold]Unified diff: {escape(name_a)} vs {escape(name_b)}[/bold]")
    lines.append("")

    compare_fields = [
        ("task.description", "task.description"),
        ("agent.name", "agent.name"),
        ("agent.version", "agent.version"),
        ("agent.model", "agent.model"),
        ("outcome.success", "outcome.success"),
        ("outcome.committed", "outcome.committed"),
        ("outcome.terminal_state", "outcome.terminal_state"),
        ("metrics.total_steps", "metrics.total_steps"),
        ("metrics.total_input_tokens", "metrics.total_input_tokens"),
        ("metrics.total_output_tokens", "metrics.total_output_tokens"),
        ("metrics.total_duration_s", "metrics.total_duration_s"),
        ("metrics.cache_hit_rate", "metrics.cache_hit_rate"),
        ("metrics.estimated_cost_usd", "metrics.estimated_cost_usd"),
        ("timestamp_start", "timestamp_start"),
        ("timestamp_end", "timestamp_end"),
        ("execution_context", "execution_context"),
    ]

    for label, path in compare_fields:
        val_a = _get(trace_a, path)
        val_b = _get(trace_b, path)
        if val_a == val_b:
            lines.append(f"  [#22C55E]{label}: {escape(_short(val_a))}[/#22C55E]")
        else:
            lines.append(f"  [#EF4444]- {label}: {escape(_short(val_a))}[/#EF4444]  [dim]({escape(name_a)})[/dim]")
            lines.append(f"  [#22C55E]+ {label}: {escape(_short(val_b))}[/#22C55E]  [dim]({escape(name_b)})[/dim]")

    # Steps diff
    steps_a = trace_a.get("steps", [])
    steps_b = trace_b.get("steps", [])
    lines.append("")
    lines.append(f"[bold]Steps: {len(steps_a)} vs {len(steps_b)}[/bold]")

    max_steps = max(len(steps_a), len(steps_b))
    for i in range(min(max_steps, 50)):
        sa = steps_a[i] if i < len(steps_a) else None
        sb = steps_b[i] if i < len(steps_b) else None

        if sa is None:
            role_b = (sb or {}).get("role", "?")
            lines.append(f"  [#22C55E]+ step {i}: {role_b}[/#22C55E]  [dim](only in {escape(name_b)})[/dim]")
        elif sb is None:
            role_a = sa.get("role", "?")
            lines.append(f"  [#EF4444]- step {i}: {role_a}[/#EF4444]  [dim](only in {escape(name_a)})[/dim]")
        else:
            role_a = sa.get("role", "?")
            role_b = sb.get("role", "?")
            tc_a = len(sa.get("tool_calls", []))
            tc_b = len(sb.get("tool_calls", []))
            if role_a == role_b and tc_a == tc_b:
                lines.append(f"  [#22C55E]  step {i}: {role_a} ({tc_a} tools)[/#22C55E]")
            else:
                lines.append(
                    f"  [#EAB308]~ step {i}: "
                    f"{role_a}({tc_a}t) vs {role_b}({tc_b}t)[/#EAB308]"
                )

    if max_steps > 50:
        lines.append(f"  [dim]... {max_steps - 50} more steps[/dim]")

    # Attribution diff
    lines.append("")
    attr_a = trace_a.get("attribution") or {}
    attr_b = trace_b.get("attribution") or {}
    files_a = {f.get("path", ""): f for f in attr_a.get("files", [])}
    files_b = {f.get("path", ""): f for f in attr_b.get("files", [])}
    all_paths = sorted(set(files_a.keys()) | set(files_b.keys()))

    if all_paths:
        lines.append("[bold]Attribution files:[/bold]")
        for p in all_paths:
            in_a = p in files_a
            in_b = p in files_b
            if in_a and in_b:
                lines.append(f"  [#22C55E]  {escape(p)}[/#22C55E]")
            elif in_a:
                lines.append(f"  [#EF4444]- {escape(p)}[/#EF4444]  [dim](only in {escape(name_a)})[/dim]")
            else:
                lines.append(f"  [#22C55E]+ {escape(p)}[/#22C55E]  [dim](only in {escape(name_b)})[/dim]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CompareScreen
# ---------------------------------------------------------------------------


class CompareScreen(Screen):
    """Parser comparison view -- side-by-side diff of two parser outputs.

    Shows the same raw session parsed by two parsers, highlighting:
    - Field-level differences between outputs
    - Schema coverage (which fields each parser populates vs leaves null)
    - Errors (missing attribution, broken parent_step links, etc.)
    """

    CSS_PATH = "compare.tcss"

    BINDINGS = [
        Binding("j", "scroll_down", "Down", show=False, priority=True),
        Binding("k", "scroll_up", "Up", show=False, priority=True),
        Binding("tab", "switch_focus", "Switch panel", priority=True),
        Binding("d", "toggle_diff_mode", "Diff mode", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("question_mark", "toggle_help", "Help", key_display="?"),
    ]

    def __init__(
        self,
        trace_a: dict[str, Any],
        trace_b: dict[str, Any],
        parser_a_name: str,
        parser_b_name: str,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._trace_a = trace_a
        self._trace_b = trace_b
        self._name_a = parser_a_name
        self._name_b = parser_b_name
        self._unified_mode = False
        self._focus_panel: str = "left"  # "left" or "right"
        self._scroll_offset = 0

    # -- Composition -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield HelpOverlay(id="help-overlay")
        with Vertical(id="compare-shell"):
            yield Static("", id="compare-topbar", markup=True)
            with Horizontal(id="compare-panels"):
                yield Static("", id="panel-left", markup=True)
                yield Static("[dim]|[/dim]", id="panel-divider", markup=True)
                yield Static("", id="panel-right", markup=True)
            yield Static("", id="unified-view", markup=True)
            with Vertical(id="compare-bottom"):
                yield SchemaCoverage(id="schema-coverage")
                with Horizontal(id="error-row"):
                    yield ErrorHighlight(id="errors-a")
                    yield ErrorHighlight(id="errors-b")
            yield KeyBar(id="keybar")

    # -- Lifecycle ---------------------------------------------------------

    def on_mount(self) -> None:
        self.query_one(KeyBar).set_mode("compare")
        self._render_topbar()
        self._render_panels()
        self._render_coverage()
        self._render_errors()
        self._apply_mode()
        self.set_focus(self.query_one("#panel-left", Static))

    # -- Rendering ---------------------------------------------------------

    def _render_topbar(self) -> None:
        steps_a = len(self._trace_a.get("steps", []))
        steps_b = len(self._trace_b.get("steps", []))
        mode_label = "unified" if self._unified_mode else "side-by-side"

        top = (
            f"[bold white]compare[/bold white]  "
            f"[dim]{escape(self._name_a)}[/dim] vs "
            f"[dim]{escape(self._name_b)}[/dim]  "
            f"[dim]{steps_a}/{steps_b} steps[/dim]  "
            f"[dim]mode: {mode_label}[/dim]"
        )
        self._topbar_base = top
        self.query_one("#compare-topbar", Static).update(top)

    def _render_panels(self) -> None:
        left_text = _render_trace_panel(self._trace_a, self._name_a)
        right_text = _render_trace_panel(self._trace_b, self._name_b)
        unified_text = _render_unified_diff(
            self._trace_a, self._trace_b, self._name_a, self._name_b
        )

        self.query_one("#panel-left", Static).update(left_text)
        self.query_one("#panel-right", Static).update(right_text)
        self.query_one("#unified-view", Static).update(unified_text)

    def _render_coverage(self) -> None:
        coverage = self.query_one("#schema-coverage", SchemaCoverage)
        coverage.compare(self._trace_a, self._trace_b, self._name_a, self._name_b)

    def _render_errors(self) -> None:
        err_a = self.query_one("#errors-a", ErrorHighlight)
        err_b = self.query_one("#errors-b", ErrorHighlight)
        errors_a = err_a.analyze(self._trace_a, self._name_a)
        errors_b = err_b.analyze(self._trace_b, self._name_b)

        # Update topbar with error counts
        total_errs = len(errors_a) + len(errors_b)
        if total_errs > 0:
            topbar = self.query_one("#compare-topbar", Static)
            # Append error count to existing topbar text
            self._topbar_base = getattr(self, "_topbar_base", "")
            topbar.update(
                self._topbar_base + f"  [#EF4444]{total_errs} errors[/#EF4444]"
            )

    def _apply_mode(self) -> None:
        """Show/hide panels based on diff mode."""
        panel_left = self.query_one("#panel-left", Static)
        panel_right = self.query_one("#panel-right", Static)
        divider = self.query_one("#panel-divider", Static)
        unified = self.query_one("#unified-view", Static)

        if self._unified_mode:
            panel_left.display = False
            panel_right.display = False
            divider.display = False
            unified.display = True
        else:
            panel_left.display = True
            panel_right.display = True
            divider.display = True
            unified.display = False

    # -- Actions -----------------------------------------------------------

    def action_scroll_down(self) -> None:
        """Scroll both panels down in sync."""
        if self._unified_mode:
            unified = self.query_one("#unified-view", Static)
            unified.scroll_down(animate=False)
        else:
            left = self.query_one("#panel-left", Static)
            right = self.query_one("#panel-right", Static)
            left.scroll_down(animate=False)
            right.scroll_down(animate=False)

    def action_scroll_up(self) -> None:
        """Scroll both panels up in sync."""
        if self._unified_mode:
            unified = self.query_one("#unified-view", Static)
            unified.scroll_up(animate=False)
        else:
            left = self.query_one("#panel-left", Static)
            right = self.query_one("#panel-right", Static)
            left.scroll_up(animate=False)
            right.scroll_up(animate=False)

    def action_switch_focus(self) -> None:
        """Switch focus between left and right panels."""
        if self._unified_mode:
            return
        if self._focus_panel == "left":
            self._focus_panel = "right"
            self.set_focus(self.query_one("#panel-right", Static))
            self.post_message(FlashMessage("[dim]Focus: right panel[/dim]", 1.5))
        else:
            self._focus_panel = "left"
            self.set_focus(self.query_one("#panel-left", Static))
            self.post_message(FlashMessage("[dim]Focus: left panel[/dim]", 1.5))

    def action_toggle_diff_mode(self) -> None:
        """Toggle between side-by-side and unified diff mode."""
        self._unified_mode = not self._unified_mode
        mode_label = "unified" if self._unified_mode else "side-by-side"
        self.post_message(FlashMessage(f"[dim]Mode: {mode_label}[/dim]", 2.0))
        self._render_topbar()
        self._apply_mode()

    def action_back(self) -> None:
        help_overlay = self.query_one(HelpOverlay)
        if help_overlay.display:
            help_overlay.toggle()
            return
        self.app.pop_screen()

    def action_toggle_help(self) -> None:
        self.query_one(HelpOverlay).toggle()

    # -- Events ------------------------------------------------------------

    def on_flash_message(self, message: FlashMessage) -> None:
        self.query_one(KeyBar).flash(message.text, message.duration)
