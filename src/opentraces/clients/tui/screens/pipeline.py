"""Pipeline diagnostics dashboard -- opentraces serve.

Shows five diagnostic panels:
1. Stage Board -- kanban of StateManager stages
2. Security Scan Stream -- live flag feed
3. Quality Score Panel -- per-trace gate pass/fail
4. Stats Dashboard -- token/cost/flag/push counts
5. Behavior Discovery -- cross-session tool-call n-gram frequency
"""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static

from ..messages import FlashMessage, RefreshRequested
from ..utils import (
    RICH_COLORS,
    _relative_time,
    _single_line,
    _stage_color_ansi,
    _truncate,
    escape,
)
from ..widgets.behavior_discovery import BehaviorDiscovery
from ..widgets.help_overlay import HelpOverlay
from ..widgets.key_bar import KeyBar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline-local widgets
# ---------------------------------------------------------------------------


class PipelineTopBar(Static):
    """Compact header showing pipeline context."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__("", *args, markup=True, **kwargs)

    def update_context(
        self, project_name: str, remote: str, total: int
    ) -> None:
        self.update(
            f"[bold white]opentraces serve[/bold white]  "
            f"[dim]{project_name}[/dim]  "
            f"[#F97316]{remote}[/#F97316]  "
            f"[dim]{total} traces[/dim]"
        )


class StageBoard(Static):
    """Kanban columns for each pipeline stage."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", markup=True, **kwargs)

    def update_data(
        self,
        counts: dict[str, int],
        by_stage: dict[str, list[dict[str, Any]]],
    ) -> None:
        stage_colors = {
            "inbox": "#EAB308",
            "committed": "#F97316",
            "pushed": "#22D3EE",
            "rejected": "#EF4444",
        }
        lines: list[str] = []

        for stage in ("inbox", "committed", "pushed", "rejected"):
            color = stage_colors.get(stage, "#666666")
            count = counts.get(stage, 0)
            lines.append(
                f"[bold {color}]{stage.upper()}[/bold {color}] "
                f"[dim]({count})[/dim]"
            )
            traces = by_stage.get(stage, [])
            for trace in traces[:5]:
                task = _truncate(
                    trace.get("task", {}).get("description") or "No description",
                    50,
                )
                agent = trace.get("agent", {}).get("name", "?")
                age = _relative_time(trace.get("timestamp_start"))
                lines.append(
                    f"  [#E0E0E0]{escape(task)}[/#E0E0E0]  "
                    f"[dim]{escape(agent)}  {age}[/dim]"
                )
            if count > 5:
                lines.append(f"  [dim]... +{count - 5} more[/dim]")
            lines.append("")

        self.update("\n".join(lines))


class SecurityStream(Static):
    """Live security flag feed across all traces."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", markup=True, **kwargs)

    def update_traces(self, traces: list[dict[str, Any]]) -> None:
        sev_colors = {
            "high": "#EF4444",
            "medium": "#EAB308",
            "low": "#666666",
        }
        lines: list[str] = []
        flags: list[dict[str, Any]] = []

        for trace in traces:
            trace_id = trace.get("trace_id", "?")
            for flag in trace.get("_security_flags", []):
                flags.append({**flag, "_trace_id": trace_id})

        if not flags:
            lines.append("[dim]No security flags detected.[/dim]")
            self.update("\n".join(lines))
            return

        lines.append(f"[bold #EF4444]{len(flags)} flags[/bold #EF4444]")
        lines.append("")

        # Sort: high first
        sev_order = {"high": 0, "medium": 1, "low": 2}
        flags.sort(key=lambda f: sev_order.get(f.get("severity", "low"), 3))

        for flag in flags[:15]:
            sev = flag.get("severity", "low")
            color = sev_colors.get(sev, "#666666")
            ftype = flag.get("type", "")
            reason = _truncate(flag.get("reason", ""), 40)
            step_idx = flag.get("step_index", "?")
            tid = flag.get("_trace_id", "?")[:8]
            lines.append(
                f"  [{color}]{sev.upper()}[/{color}] "
                f"[#E0E0E0]{escape(ftype)}[/#E0E0E0] "
                f"[dim]{escape(reason)}[/dim]  "
                f"[dim]step {step_idx}  {tid}[/dim]"
            )

        if len(flags) > 15:
            lines.append(f"  [dim]... +{len(flags) - 15} more[/dim]")

        self.update("\n".join(lines))


class QualityPanel(Static):
    """Quality score distribution across traces."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", markup=True, **kwargs)

    def update_traces(self, traces: list[dict[str, Any]]) -> None:
        scores: list[float] = []
        for trace in traces:
            q = trace.get("_quality")
            if q is not None and isinstance(q, dict):
                score = q.get("overall_score") or q.get("score")
                if score is not None:
                    scores.append(float(score))

        if not scores:
            self.update(
                "[dim]No quality scores available.\n"
                "Run `opentraces assess` to see quality scores.[/dim]"
            )
            return

        # Basic histogram: 0-2, 2-4, 4-6, 6-8, 8-10
        buckets = [0, 0, 0, 0, 0]
        for s in scores:
            idx = min(int(s / 2), 4)
            buckets[idx] += 1

        avg = sum(scores) / len(scores)
        passed = sum(1 for s in scores if s >= 6.0)
        failed = len(scores) - passed

        lines: list[str] = []
        lines.append(
            f"[bold #22D3EE]Quality[/bold #22D3EE]  "
            f"avg [bold]{avg:.1f}[/bold]  "
            f"[#22C55E]{passed} pass[/#22C55E]  "
            f"[#EF4444]{failed} fail[/#EF4444]"
        )
        lines.append("")

        labels = ["0-2", "2-4", "4-6", "6-8", "8-10"]
        max_count = max(buckets) if buckets else 1
        for label, count in zip(labels, buckets):
            bar_len = int((count / max(max_count, 1)) * 15)
            bar = "\u2588" * bar_len
            color = "#EF4444" if label in ("0-2", "2-4") else "#22C55E"
            lines.append(
                f"  [dim]{label:>4}[/dim] [{color}]{bar}[/{color}] "
                f"[dim]{count}[/dim]"
            )

        self.update("\n".join(lines))


class StatsPanel(Static):
    """Aggregate metrics across all traces."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", markup=True, **kwargs)

    def update_traces(self, traces: list[dict[str, Any]]) -> None:
        total_sessions = len(traces)
        total_steps = 0
        total_tool_calls = 0
        tokens_in = 0
        tokens_out = 0
        cost_total = 0.0
        flag_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}

        for trace in traces:
            steps = trace.get("steps", [])
            metrics = trace.get("metrics", {})
            total_steps += metrics.get("total_steps", len(steps))
            total_tool_calls += sum(
                len(s.get("tool_calls", [])) for s in steps
            )
            tokens_in += metrics.get("total_input_tokens", 0)
            tokens_out += metrics.get("total_output_tokens", 0)
            c = metrics.get("estimated_cost_usd")
            if c is not None:
                cost_total += float(c)

            for flag in trace.get("_security_flags", []):
                sev = flag.get("severity", "low")
                if sev in flag_counts:
                    flag_counts[sev] += 1

        lines: list[str] = []
        lines.append("[bold #22D3EE]Stats[/bold #22D3EE]")
        lines.append("")
        lines.append(f"  [dim]Sessions:[/dim]    [bold]{total_sessions}[/bold]")
        lines.append(f"  [dim]Steps:[/dim]       [bold]{total_steps}[/bold]")
        lines.append(f"  [dim]Tool calls:[/dim]  [bold]{total_tool_calls}[/bold]")
        lines.append("")
        lines.append(f"  [dim]Tokens in:[/dim]   {tokens_in:,}")
        lines.append(f"  [dim]Tokens out:[/dim]  {tokens_out:,}")
        if cost_total > 0:
            lines.append(f"  [dim]Est. cost:[/dim]   [bold]${cost_total:.4f}[/bold]")
        lines.append("")
        lines.append("[dim]Flags:[/dim]")
        lines.append(
            f"  [#EF4444]high {flag_counts['high']}[/#EF4444]  "
            f"[#EAB308]medium {flag_counts['medium']}[/#EAB308]  "
            f"[#666666]low {flag_counts['low']}[/#666666]"
        )

        self.update("\n".join(lines))


# ---------------------------------------------------------------------------
# Pipeline help overlay
# ---------------------------------------------------------------------------


class PipelineHelpOverlay(HelpOverlay):
    """Help overlay with pipeline-specific keybindings."""

    HELP_TEXT = (
        "[bold underline]Pipeline Dashboard[/bold underline]\n"
        "\n"
        "[bold]Navigation[/bold]\n"
        "  [bold]j[/bold] / [bold]k[/bold]          Cycle panel focus\n"
        "  [bold]1-5[/bold]              Jump to specific panel\n"
        "  [bold]Enter[/bold]            Expand details for selected item\n"
        "  [bold]Esc[/bold]              Back to inbox\n"
        "\n"
        "[bold]Actions[/bold]\n"
        "  [bold]r[/bold]                Refresh all data\n"
        "\n"
        "[bold]Panels[/bold]\n"
        "  [bold]1[/bold]  Stage Board      Kanban of pipeline stages\n"
        "  [bold]2[/bold]  Stats            Token/cost/flag aggregates\n"
        "  [bold]3[/bold]  Security         Live flag feed\n"
        "  [bold]4[/bold]  Quality          Score distribution & gates\n"
        "  [bold]5[/bold]  Behavior         Tool-call n-gram patterns\n"
        "\n"
        "[bold]General[/bold]\n"
        "  [bold]?[/bold]                Toggle this help overlay\n"
        "  [bold]q[/bold]                Quit\n"
        "\n"
        "[dim]Press [bold]?[/bold] to close[/dim]"
    )

    def __init__(self, **kwargs: object) -> None:
        # Skip HelpOverlay.__init__ to use our own HELP_TEXT
        Static.__init__(self, self.HELP_TEXT, markup=True, **kwargs)
        self.display = False


# ---------------------------------------------------------------------------
# PipelineScreen
# ---------------------------------------------------------------------------


class PipelineScreen(Screen):
    """Pipeline diagnostics dashboard -- opentraces serve.

    Shows five diagnostic panels:
    1. Stage Board -- kanban of StateManager stages
    2. Security Scan Stream -- live flag feed
    3. Quality Score Panel -- per-trace gate pass/fail
    4. Stats Dashboard -- token/cost/flag/push counts
    5. Behavior Discovery -- cross-session tool-call n-gram frequency
    """

    CSS_PATH = "pipeline.tcss"

    BINDINGS = [
        Binding("j", "next_panel", "Next", show=False, priority=True),
        Binding("k", "prev_panel", "Prev", show=False, priority=True),
        Binding("enter", "expand_detail", "Details", priority=True),
        Binding("r", "refresh_data", "Refresh", priority=True),
        Binding("1", "focus_panel_1", "Stage Board", show=False, priority=True),
        Binding("2", "focus_panel_2", "Stats", show=False, priority=True),
        Binding("3", "focus_panel_3", "Security", show=False, priority=True),
        Binding("4", "focus_panel_4", "Quality", show=False, priority=True),
        Binding("5", "focus_panel_5", "Behavior", show=False, priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("question_mark", "toggle_help", "Help", key_display="?"),
        Binding("q", "quit_app", "Quit"),
    ]

    _panel_ids: list[str] = [
        "stage-board",
        "stats-panel",
        "security-stream",
        "quality-panel",
        "behavior-panel",
    ]
    _active_panel_index: int = 0

    # ── Composition ──────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield PipelineHelpOverlay(id="help-overlay")
        with Vertical(id="pipeline-shell"):
            yield PipelineTopBar(id="pipeline-topbar")
            with Horizontal(id="pipeline-top-row"):
                with Vertical(id="stage-board-container", classes="pipeline-panel"):
                    yield Static(
                        "[bold #22D3EE]Stage Board[/bold #22D3EE]",
                        id="stage-board-title",
                        classes="panel-title",
                    )
                    yield StageBoard(id="stage-board")
                with Vertical(id="stats-container", classes="pipeline-panel"):
                    yield StatsPanel(id="stats-panel")
            with Horizontal(id="pipeline-bottom-row"):
                with Vertical(id="security-container", classes="pipeline-panel"):
                    yield Static(
                        "[bold #EF4444]Security Stream[/bold #EF4444]",
                        id="security-title",
                        classes="panel-title",
                    )
                    yield SecurityStream(id="security-stream")
                with Vertical(id="quality-container", classes="pipeline-panel"):
                    yield QualityPanel(id="quality-panel")
                with Vertical(id="behavior-container", classes="pipeline-panel"):
                    yield Static(
                        "[bold #F97316]Behavior Discovery[/bold #F97316]",
                        id="behavior-title",
                        classes="panel-title",
                    )
                    yield BehaviorDiscovery(id="behavior-panel")
            yield KeyBar(id="keybar")

    # ── Properties ───────────────────────────────────────────────

    @property
    def store(self) -> Any:
        return self.app.store  # type: ignore[attr-defined]

    @property
    def project_name(self) -> str:
        return self.app.project_name  # type: ignore[attr-defined]

    @property
    def remote_name(self) -> str:
        return self.app.remote_name  # type: ignore[attr-defined]

    # ── Lifecycle ────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.query_one(KeyBar).set_mode("pipeline")
        self._refresh_all()
        self._highlight_active_panel()

    # ── Data loading ─────────────────────────────────────────────

    def _refresh_all(self) -> None:
        """Reload data from the store and update all panels."""
        self.store.load()
        traces = self.store.traces
        counts = self.store.stage_counts()

        # Build by-stage map
        by_stage: dict[str, list[dict[str, Any]]] = {}
        for stage in ("inbox", "committed", "pushed", "rejected"):
            by_stage[stage] = self.store.get_by_stage(stage)

        # Top bar
        self.query_one(PipelineTopBar).update_context(
            self.project_name, self.remote_name, len(traces)
        )

        # Panels
        self.query_one("#stage-board", StageBoard).update_data(counts, by_stage)
        self.query_one("#stats-panel", StatsPanel).update_traces(traces)
        self.query_one("#security-stream", SecurityStream).update_traces(traces)
        self.query_one("#quality-panel", QualityPanel).update_traces(traces)
        self.query_one("#behavior-panel", BehaviorDiscovery).update_traces(traces)

    # ── Panel focus ──────────────────────────────────────────────

    def _highlight_active_panel(self) -> None:
        """Update visual highlight on the active panel."""
        for i, pid in enumerate(self._panel_ids):
            container_id = pid.replace("-panel", "-container").replace(
                "-board", "-board-container"
            ).replace("-stream", "-container")
            # Use the parent container for highlighting
            try:
                widget = self.query_one(f"#{pid}")
                parent = widget.parent
                if parent is not None:
                    if i == self._active_panel_index:
                        parent.add_class("active-panel")
                    else:
                        parent.remove_class("active-panel")
            except Exception:
                pass

    def _focus_panel(self, index: int) -> None:
        self._active_panel_index = index % len(self._panel_ids)
        self._highlight_active_panel()
        try:
            widget = self.query_one(f"#{self._panel_ids[self._active_panel_index]}")
            self.set_focus(widget)
        except Exception:
            pass

    # ── Actions ──────────────────────────────────────────────────

    def action_next_panel(self) -> None:
        self._focus_panel(self._active_panel_index + 1)

    def action_prev_panel(self) -> None:
        self._focus_panel(self._active_panel_index - 1)

    def action_expand_detail(self) -> None:
        """Placeholder: expand details for the selected item in active panel."""
        self.notify("Detail expansion coming soon", severity="information")

    def action_refresh_data(self) -> None:
        self._refresh_all()
        self.post_message(FlashMessage("[bold #22C55E]\u21bb[/bold #22C55E] Refreshed"))

    def action_focus_panel_1(self) -> None:
        self._focus_panel(0)

    def action_focus_panel_2(self) -> None:
        self._focus_panel(1)

    def action_focus_panel_3(self) -> None:
        self._focus_panel(2)

    def action_focus_panel_4(self) -> None:
        self._focus_panel(3)

    def action_focus_panel_5(self) -> None:
        self._focus_panel(4)

    def action_back(self) -> None:
        help_overlay = self.query_one(PipelineHelpOverlay)
        if help_overlay.display:
            help_overlay.toggle()
            return
        self.app.pop_screen()

    def action_toggle_help(self) -> None:
        self.query_one(PipelineHelpOverlay).toggle()

    def action_quit_app(self) -> None:
        self.app.exit()

    # ── Events ───────────────────────────────────────────────────

    def on_refresh_requested(self, message: RefreshRequested) -> None:
        self._refresh_all()

    def on_flash_message(self, message: FlashMessage) -> None:
        self.query_one(KeyBar).flash(message.text, message.duration)
