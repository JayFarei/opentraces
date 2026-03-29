"""Textual-based TUI for the OpenTraces repo inbox."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import ListItem, ListView, RichLog, Static

from ..config import STAGING_DIR, load_project_config
from ..state import StateManager, TraceStatus
from ..workflow import OPENTRACES_ASCII, VISIBLE_STAGE_ORDER, resolve_visible_stage, stage_label


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_staged_traces(staging_dir: Path) -> list[dict[str, Any]]:
    """Load traces from the staging directory, falling back to sample data."""
    traces: list[dict[str, Any]] = []
    if staging_dir.exists():
        for jsonl_file in sorted(staging_dir.glob("*.jsonl")):
            try:
                text = jsonl_file.read_text().strip()
                if text:
                    for line in text.splitlines():
                        line = line.strip()
                        if line:
                            traces.append(json.loads(line))
            except (json.JSONDecodeError, OSError):
                continue
    return traces


def _get_review_status(state: StateManager, trace_id: str) -> str:
    entry = state.get_trace(trace_id)
    if entry:
        return resolve_visible_stage(entry.status)
    return "inbox"


def _status_icon(status: str) -> str:
    return {
        "ready": "[green]\u2713[/green]",
        "committed": "[cyan]\u25A0[/cyan]",
        "rejected": "[red]\u2717[/red]",
        "inbox": "[yellow]\u25CB[/yellow]",
        "pushed": "[green]\u2713[/green]",
    }.get(status, "[yellow]\u25CB[/yellow]")


def _stage_color(status: str) -> str:
    return {
        "inbox": "ansi_yellow",
        "ready": "ansi_green",
        "committed": "ansi_bright_blue",
        "pushed": "ansi_cyan",
        "rejected": "ansi_red",
    }.get(status, "ansi_yellow")


def _relative_time(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        secs = int(delta.total_seconds())
        if secs < 0:
            return ts[:16]
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return str(ts)[:16]


def _trace_summary(trace: dict[str, Any], status: str) -> str:
    task = (trace.get("task", {}).get("description") or "No description")[:40]
    steps = trace.get("metrics", {}).get("total_steps", len(trace.get("steps", [])))
    tool_calls = sum(len(s.get("tool_calls", [])) for s in trace.get("steps", []))
    flags = len(trace.get("_security_flags", []))
    ts = _relative_time(trace.get("timestamp_start"))
    icon = _status_icon(status)

    flag_str = f" [red]{flags} flags[/red]" if flags else ""
    return f"{icon} {task}  [dim]{steps}s {tool_calls}tc{flag_str} {ts}[/dim]"


def _sort_key(trace: dict[str, Any], state: StateManager) -> tuple[int, str]:
    status = _get_review_status(state, trace["trace_id"])
    try:
        stage_index = VISIBLE_STAGE_ORDER.index(status)
    except ValueError:
        stage_index = 0
    timestamp = trace.get("timestamp_start") or ""
    return (stage_index, timestamp)


def _project_dir_from_staging(staging_dir: Path) -> Path:
    if staging_dir.name == "staging" and staging_dir.parent.name == ".opentraces":
        return staging_dir.parent.parent
    return Path.cwd()


def _truncate(text: str, limit: int) -> str:
    compact = " ".join(text.replace("\n", " ").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)] + "..."


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class SessionListItem(ListItem):
    """A single session row in the left panel."""

    def __init__(self, trace: dict[str, Any], status: str) -> None:
        super().__init__()
        self.trace = trace
        self.trace_status = status

    def compose(self) -> ComposeResult:
        yield Static(self._render_row(), markup=True, classes="session-row")

    def _render_row(self) -> str:
        task = _truncate(self.trace.get("task", {}).get("description") or "No description", 30)
        agent = self.trace.get("agent", {}).get("name", "unknown")
        model = self.trace.get("agent", {}).get("model") or "unknown"
        model = model.split("/")[-1]
        steps = self.trace.get("metrics", {}).get("total_steps", len(self.trace.get("steps", [])))
        tool_calls = sum(len(s.get("tool_calls", [])) for s in self.trace.get("steps", []))
        flags = len(self.trace.get("_security_flags", []))
        ts = _relative_time(self.trace.get("timestamp_start"))
        stage = stage_label(self.trace_status).upper()
        stage_color = _stage_color(self.trace_status)
        flag_text = f"  [ansi_red]{flags}f[/ansi_red]" if flags else ""
        return (
            f"[bold]{task}[/bold]\n"
            f"[{stage_color}]{stage}[/{stage_color}] [dim]{agent[:8]}/{model[:8]}  {steps}s {tool_calls}t  {ts}{flag_text}[/dim]"
        )

    def refresh_label(self, status: str) -> None:
        self.trace_status = status
        self.query_one(Static).update(self._render_row())


class TopBar(Static):
    """Compact app header with repo context and counts."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__("", *args, **kwargs)

    def update_context(self, project_name: str, remote: str, counts: dict[str, int]) -> None:
        text = (
            f"[bold ansi_bright_white]opentraces[/bold ansi_bright_white]  "
            f"[dim]{project_name}[/dim]  "
            f"[ansi_bright_blue]{remote}[/ansi_bright_blue]\n"
            f"[ansi_yellow]INBOX[/ansi_yellow]: {counts['inbox']}   "
            f"[ansi_green]READY[/ansi_green]: {counts['ready']}   "
            f"[ansi_bright_blue]COMMITTED[/ansi_bright_blue]: {counts['committed']}   "
            f"[ansi_cyan]PUSHED[/ansi_cyan]: {counts['pushed']}   "
            f"[ansi_red]REJECTED[/ansi_red]: {counts['rejected']}"
        )
        self.update(text)


class KeyBar(Static):
    """Persistent keybinding footer."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__("", *args, **kwargs)

    def update_mode(self, in_step_view: bool) -> None:
        if in_step_view:
            text = (
                "[bold]1[/bold] sessions   [bold]2[/bold] summary   [bold]3[/bold] detail   "
                "[bold]esc[/bold] back   [bold]x[/bold] redact hint   "
                "[bold]?[/bold] help   [bold]q[/bold] quit"
            )
        else:
            text = (
                "[bold]1[/bold] sessions   [bold]2[/bold] summary   [bold]3[/bold] detail   "
                "[bold]j/k[/bold] move   [bold]enter[/bold] inspect   "
                "[bold]a[/bold] ready   [bold]c[/bold] commit   "
                "[bold]r[/bold] reject   [bold]d[/bold] discard   "
                "[bold]p[/bold] push   [bold]?[/bold] help   [bold]q[/bold] quit"
            )
        self.update(text)


class PaneBody(Static, can_focus=True):
    """Focusable pane body for keyboard navigation."""


class HelpOverlay(Static):
    """Full-screen help overlay."""

    HELP_TEXT = (
        "[bold]Keybindings[/bold]\n\n"
        "  [bold]j / k[/bold]  or  [bold]up / down[/bold]   Navigate sessions\n"
        "  [bold]a[/bold]                        Move selected session to Ready\n"
        "  [bold]c[/bold]                        Commit selected ready session\n"
        "  [bold]r[/bold]                        Reject selected session\n"
        "  [bold]d[/bold]                        Discard (delete staging file + state)\n"
        "  [bold]p[/bold]                        Push committed traces from the CLI\n"
        "  [bold]Enter[/bold]                    Expand step-by-step detail view\n"
        "  [bold]x[/bold]                        Redact selected step (in step view)\n"
        "  [bold]Esc[/bold]                      Back from step view / close help\n"
        "  [bold]?[/bold]                        Toggle this help overlay\n"
        "  [bold]q[/bold]                        Quit\n"
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(self.HELP_TEXT, markup=True, *args, **kwargs)
        self.styles.display = "none"
        self.styles.width = "100%"
        self.styles.height = "100%"
        self.styles.background = "ansi_default"
        self.styles.color = "ansi_default"
        self.styles.padding = (2, 4)
        self.styles.layer = "overlay"

    def toggle(self) -> None:
        if self.styles.display == "none":
            self.styles.display = "block"
        else:
            self.styles.display = "none"


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

CSS = """
Screen {
    background: ansi_default;
    color: ansi_default;
    layers: base overlay;
}

#app-shell {
    height: 100%;
}

#topbar {
    height: 3;
    padding: 0 2;
    background: ansi_default;
    color: ansi_default;
    border-bottom: solid ansi_bright_black;
}

#workspace {
    height: 1fr;
    padding: 1;
}

.panel {
    background: ansi_default;
    border: round ansi_bright_black;
    color: ansi_default;
}

.panel:focus-within {
    border: round ansi_white;
}

.panel-title {
    height: 1;
    padding: 0 1;
    background: ansi_default;
    color: ansi_bright_black;
    text-style: bold;
}

.panel:focus-within > .panel-title {
    color: ansi_white;
}

#sidebar-panel {
    width: 36;
    min-width: 32;
    margin-right: 1;
}

#sidebar-meta {
    height: 4;
    padding: 1 1 0 1;
    color: ansi_bright_black;
    border-bottom: solid ansi_bright_black;
}

#session-list {
    height: 1fr;
    background: ansi_default;
}

#session-list > ListItem {
    padding: 0 1;
    height: 3;
    border-left: wide transparent;
}

#session-list > ListItem.-selected {
    background: ansi_default;
    border-left: wide ansi_bright_black;
    color: ansi_default;
    text-style: bold;
}

#session-list:focus > ListItem.-selected {
    background: ansi_default;
    border-left: wide ansi_white;
    color: ansi_default;
    text-style: bold;
}

#main-column {
    width: 1fr;
    height: 100%;
}

#summary-panel {
    height: 12;
    margin-bottom: 1;
}

#summary-body {
    padding: 1;
    color: ansi_default;
}

#detail-panel {
    height: 1fr;
}

#detail-view {
    height: 1fr;
    scrollbar-size-vertical: 1;
    scrollbar-size-horizontal: 1;
    background: ansi_default;
    padding: 0 1;
    scrollbar-background: ansi_default;
    scrollbar-background-hover: ansi_default;
    scrollbar-background-active: ansi_default;
    scrollbar-color: ansi_bright_black;
    scrollbar-color-hover: ansi_bright_black;
    scrollbar-color-active: ansi_bright_black;
    scrollbar-corner-color: ansi_default;
}

HelpOverlay {
    layer: overlay;
}

#keybar {
    height: 2;
    padding: 0 2;
    background: ansi_default;
    color: ansi_bright_black;
    border-top: solid ansi_bright_black;
}

.session-row {
    height: 2;
}

#empty-state {
    padding: 2 3;
    color: ansi_bright_black;
}

HelpOverlay {
    width: 72;
    height: auto;
    max-height: 22;
    align: center middle;
    background: ansi_default;
    border: round ansi_bright_blue;
    color: ansi_default;
    padding: 1 2;
}
"""


class OpenTracesApp(App):
    """Textual TUI for the repo-local OpenTraces inbox."""

    TITLE = "opentraces"
    SUB_TITLE = "repo inbox"
    AUTO_FOCUS = "#session-list"
    CSS = CSS

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "toggle_help", "Help", key_display="?"),
        Binding("1", "focus_sessions", "Sessions", show=False),
        Binding("2", "focus_summary", "Summary", show=False),
        Binding("3", "focus_detail", "Detail", show=False),
        Binding("a", "approve", "Approve"),
        Binding("c", "commit", "Commit"),
        Binding("r", "reject", "Reject"),
        Binding("d", "discard", "Discard"),
        Binding("p", "push", "Push"),
        Binding("enter", "expand", "Expand"),
        Binding("escape", "back", "Back"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("x", "redact_step", "Redact step", show=False),
    ]

    def __init__(self, staging_dir: Path) -> None:
        super().__init__(ansi_color=True)
        self.theme = "textual-ansi"
        self.staging_dir = staging_dir
        self.project_dir = _project_dir_from_staging(staging_dir)
        self.traces: list[dict[str, Any]] = []
        self.state = StateManager()
        self._in_step_view = False
        self._step_view_trace: dict[str, Any] | None = None
        self.project_name = self.project_dir.name
        self.remote_name = "remote not set"

    def compose(self) -> ComposeResult:
        yield HelpOverlay()
        with Vertical(id="app-shell"):
            yield TopBar(id="topbar")
            with Horizontal(id="workspace"):
                with Vertical(id="sidebar-panel", classes="panel"):
                    yield Static("[1] Sessions", id="sessions-title", classes="panel-title")
                    yield Static("", id="sidebar-meta", markup=True)
                    yield ListView(id="session-list")
                with Vertical(id="main-column"):
                    with Vertical(id="summary-panel", classes="panel"):
                        yield Static("[2] Summary", id="summary-title", classes="panel-title")
                        yield PaneBody("", id="summary-body", markup=True)
                    with Vertical(id="detail-panel", classes="panel"):
                        yield Static("[3] Detail", id="detail-title", classes="panel-title")
                        yield RichLog(id="detail-view", markup=True, wrap=True)
            yield KeyBar(id="keybar", markup=True)

    def on_mount(self) -> None:
        self._load_project_context()
        self.query_one(KeyBar).update_mode(False)
        self._reload_traces()
        self.set_focus(self.query_one("#session-list", ListView))

    # --- Data loading ---

    def _load_project_context(self) -> None:
        try:
            proj_config = load_project_config(self.project_dir)
            self.remote_name = proj_config.get("remote") or "remote not set"
        except Exception:
            self.remote_name = "remote not set"

    def _set_empty_state(self) -> None:
        summary = self.query_one("#summary-body", Static)
        detail = self.query_one("#detail-view", RichLog)
        summary.update(
            "[bold]No sessions in this inbox[/bold]\n"
            "[dim]Run opentraces init in this repo and finish a connected agent session.[/dim]"
        )
        detail.clear()
        detail.write(f"[bold ansi_bright_blue]{OPENTRACES_ASCII}[/bold ansi_bright_blue]")
        detail.write("")
        detail.write("[dim]This repo inbox is empty.[/dim]")
        detail.write("[dim]OpenTraces will capture sessions here after setup.[/dim]")

    def _reload_traces(self) -> None:
        self.traces = sorted(_load_staged_traces(self.staging_dir), key=lambda trace: _sort_key(trace, self.state))
        session_list = self.query_one("#session-list", ListView)
        session_list.clear()

        for trace in self.traces:
            status = _get_review_status(self.state, trace["trace_id"])
            item = SessionListItem(trace, status)
            session_list.append(item)

        self._update_status_bar()
        self._update_sidebar_meta()

        if self.traces:
            session_list.index = 0
            self._show_detail(self.traces[0])
        else:
            self._set_empty_state()

    def _stage_counts(self) -> dict[str, int]:
        counts = {stage: 0 for stage in VISIBLE_STAGE_ORDER}
        for t in self.traces:
            s = _get_review_status(self.state, t["trace_id"])
            counts[s if s in counts else "inbox"] += 1
        return counts

    def _update_status_bar(self) -> None:
        self.query_one(TopBar).update_context(self.project_name, self.remote_name, self._stage_counts())

    def _update_sidebar_meta(self) -> None:
        total = len(self.traces)
        counts = self._stage_counts()
        self.query_one("#sidebar-meta", Static).update(
            f"[dim]project[/dim]\n"
            f"{self.project_name}\n"
            f"[dim]remote[/dim] [ansi_bright_blue]{_truncate(self.remote_name, 22)}[/ansi_bright_blue]   "
            f"[dim]sessions[/dim] {total}   "
            f"[ansi_yellow]{counts['inbox']} inbox[/ansi_yellow]"
        )

    # --- Detail panel ---

    def _update_summary(self, trace: dict[str, Any], status: str) -> None:
        summary = self.query_one("#summary-body", Static)
        task = trace.get("task", {}).get("description") or "No description"
        agent = trace.get("agent", {}).get("name", "unknown")
        model = trace.get("agent", {}).get("model", "unknown")
        steps = trace.get("steps", [])
        total_steps = trace.get("metrics", {}).get("total_steps", len(steps))
        tool_calls = sum(len(s.get("tool_calls", [])) for s in steps)
        flags = trace.get("_security_flags", [])
        ts_start = trace.get("timestamp_start", "")
        cost = trace.get("metrics", {}).get("estimated_cost_usd")
        tokens_in = trace.get("metrics", {}).get("total_input_tokens", 0)
        tokens_out = trace.get("metrics", {}).get("total_output_tokens", 0)
        summary.update(
            f"[{_stage_color(status)}]{stage_label(status).upper()}[/{_stage_color(status)}]  "
            f"[dim]{trace['trace_id']}[/dim]\n"
            f"[bold]{task}[/bold]\n"
            f"[dim]agent[/dim] {agent}   [dim]model[/dim] {model}\n"
            f"[dim]steps[/dim] {total_steps}   [dim]tools[/dim] {tool_calls}   "
            f"[dim]flags[/dim] {len(flags)}\n"
            f"[dim]tokens[/dim] {tokens_in} in / {tokens_out} out   "
            f"[dim]started[/dim] {ts_start[:19] if ts_start else 'unknown'}"
            + (f"\n[dim]cost[/dim] ${cost:.4f}" if cost is not None else "")
        )

    def _set_detail_title(self, text: str) -> None:
        self.query_one("#detail-title", Static).update(text)

    def _show_detail(self, trace: dict[str, Any]) -> None:
        detail = self.query_one("#detail-view", RichLog)
        detail.clear()
        self._in_step_view = False
        self._step_view_trace = None
        self.query_one(KeyBar).update_mode(False)
        self._set_detail_title("[3] Detail")

        trace_id = trace["trace_id"]
        status = _get_review_status(self.state, trace_id)
        self._update_summary(trace, status)
        task = trace.get("task", {}).get("description") or "No description"
        agent = trace.get("agent", {}).get("name", "unknown")
        model = trace.get("agent", {}).get("model", "unknown")
        steps = trace.get("steps", [])
        total_steps = trace.get("metrics", {}).get("total_steps", len(steps))
        tool_calls = sum(len(s.get("tool_calls", [])) for s in steps)
        flags = trace.get("_security_flags", [])
        ts_start = trace.get("timestamp_start", "")
        ts_end = trace.get("timestamp_end", "")
        cost = trace.get("metrics", {}).get("estimated_cost_usd")
        tokens_in = trace.get("metrics", {}).get("total_input_tokens", 0)
        tokens_out = trace.get("metrics", {}).get("total_output_tokens", 0)

        detail.write(f"[bold]{task}[/bold]")
        detail.write("")
        detail.write(f"[dim]trace[/dim] {trace_id}")
        detail.write(f"[dim]status[/dim] {stage_label(status)}   [dim]agent[/dim] {agent}   [dim]model[/dim] {model}")
        detail.write(f"[dim]steps[/dim] {total_steps}   [dim]tool calls[/dim] {tool_calls}")
        detail.write(f"[dim]tokens[/dim] {tokens_in} in / {tokens_out} out")
        if cost is not None:
            detail.write(f"[dim]cost[/dim] ${cost:.4f}")
        detail.write(f"[dim]time[/dim] {ts_start} -> {ts_end}")

        if flags:
            detail.write("")
            detail.write(f"[bold ansi_red]Security flags ({len(flags)})[/bold ansi_red]")
            for f in flags:
                sev = f.get("severity", "")
                detail.write(f"  [{sev}] {f.get('type', '')} -> {f.get('reason', '')} (step {f.get('step_index', '?')})")

        if steps:
            detail.write("")
            detail.write("[bold]Recent steps[/bold]")
            for i, step in enumerate(steps[:6]):
                role = step.get("role", "?")
                role_color = {"user": "cyan", "agent": "green", "system": "yellow"}.get(role, "white")
                content = (step.get("content", "") or "").replace("\n", " ")
                if len(content) > 110:
                    content = content[:107] + "..."
                detail.write(f"  [{role_color}]{role.upper()}[/{role_color}]  {content or '[no content]'}")

        detail.write("")
        detail.write("[dim]Press Enter to inspect every step in this session.[/dim]")

    def _show_step_view(self, trace: dict[str, Any]) -> None:
        """Replace right panel content with scrollable step detail list."""
        self._in_step_view = True
        self._step_view_trace = trace
        self.query_one(KeyBar).update_mode(True)
        self._set_detail_title("[3] Detail [Inspecting]")

        detail = self.query_one("#detail-view", RichLog)
        detail.clear()

        steps = trace.get("steps", [])
        if not steps:
            detail.write("[dim]No steps in this trace.[/dim]")
            return

        for i, step in enumerate(steps):
            role = step.get("role", "?")
            content = step.get("content", "")

            # Role badge colors
            role_color = {"user": "cyan", "agent": "green", "system": "yellow"}.get(role, "white")

            if content == "[REDACTED]":
                detail.write(f"[{role_color} bold][{role.upper()}][/{role_color} bold] step {i}  [red][REDACTED][/red]")
            else:
                detail.write(f"[{role_color} bold][{role.upper()}][/{role_color} bold] step {i}")
                truncated = (content or "").replace("\n", " ")
                detail.write(f"  {truncated[:160]}")

            # Tool calls
            for tc in step.get("tool_calls", []):
                tool_name = tc.get("tool_name", "?")
                tool_input = str(tc.get("input", ""))[:60].replace("\n", " ")
                detail.write(f"  [dim]\u2514 {tool_name}({tool_input})[/dim]")

            detail.write("")

        detail.write("[dim]Press Esc to return. Use the web inbox for precise step redaction.[/dim]")
        self.set_focus(detail)

    # --- Actions ---

    def _get_selected_trace(self) -> dict[str, Any] | None:
        session_list = self.query_one("#session-list", ListView)
        idx = session_list.index
        if idx is not None and 0 <= idx < len(self.traces):
            return self.traces[idx]
        return None

    def _get_selected_item(self) -> SessionListItem | None:
        session_list = self.query_one("#session-list", ListView)
        idx = session_list.index
        if idx is not None:
            children = list(session_list.children)
            if 0 <= idx < len(children):
                item = children[idx]
                if isinstance(item, SessionListItem):
                    return item
        return None

    def action_approve(self) -> None:
        trace = self._get_selected_trace()
        if not trace:
            return
        trace_id = trace["trace_id"]
        self.state.set_trace_status(trace_id, TraceStatus.APPROVED, session_id=trace_id)
        item = self._get_selected_item()
        if item:
            item.refresh_label("ready")
        self._show_detail(trace)
        self._update_status_bar()
        self.notify("Marked ready", severity="information")

    def action_commit(self) -> None:
        trace = self._get_selected_trace()
        if not trace:
            return

        trace_id = trace["trace_id"]
        entry = self.state.get_trace(trace_id)
        current_stage = resolve_visible_stage(entry.status if entry else None)
        if current_stage != "ready":
            self.notify("Move the session to Ready before committing", severity="warning")
            return

        task = (trace.get("task", {}).get("description") or "trace")[:60]
        self.state.create_commit_group([trace_id], f"Commit trace: {task}")
        item = self._get_selected_item()
        if item:
            item.refresh_label("committed")
        self._show_detail(trace)
        self._update_status_bar()
        self.notify("Committed trace", severity="information")

    def action_reject(self) -> None:
        trace = self._get_selected_trace()
        if not trace:
            return
        trace_id = trace["trace_id"]
        self.state.set_trace_status(trace_id, TraceStatus.REJECTED, session_id=trace_id)
        item = self._get_selected_item()
        if item:
            item.refresh_label("rejected")
        self._show_detail(trace)
        self._update_status_bar()
        self.notify("Rejected", severity="warning")

    def action_discard(self) -> None:
        trace = self._get_selected_trace()
        if not trace:
            return
        trace_id = trace["trace_id"]

        # Delete staging file
        staging_file = self.staging_dir / f"{trace_id}.jsonl"
        if staging_file.exists():
            try:
                staging_file.unlink()
            except OSError:
                pass

        # Remove from state
        if trace_id in self.state._state.get("traces", {}):
            del self.state._state["traces"][trace_id]
            self.state.save()

        self._reload_traces()
        self.notify("Discarded", severity="warning")

    def action_push(self) -> None:
        self.notify("Run 'opentraces push' to publish committed traces", severity="information")

    def action_expand(self) -> None:
        if self._in_step_view:
            self.set_focus(self.query_one("#detail-view", RichLog))
            return
        trace = self._get_selected_trace()
        if trace:
            self._show_step_view(trace)

    def action_back(self) -> None:
        # Close help if open
        help_overlay = self.query_one(HelpOverlay)
        if help_overlay.styles.display != "none":
            help_overlay.toggle()
            return

        if self._in_step_view:
            trace = self._get_selected_trace()
            if trace:
                self._show_detail(trace)
            self.set_focus(self.query_one("#session-list", ListView))
            return

    def action_toggle_help(self) -> None:
        self.query_one(HelpOverlay).toggle()

    def action_cursor_down(self) -> None:
        if self._in_step_view:
            self.query_one("#detail-view", RichLog).scroll_down(animate=False, immediate=True)
            return
        session_list = self.query_one("#session-list", ListView)
        if session_list.index is not None and session_list.index < len(self.traces) - 1:
            session_list.index += 1

    def action_cursor_up(self) -> None:
        if self._in_step_view:
            self.query_one("#detail-view", RichLog).scroll_up(animate=False, immediate=True)
            return
        session_list = self.query_one("#session-list", ListView)
        if session_list.index is not None and session_list.index > 0:
            session_list.index -= 1

    def action_focus_sessions(self) -> None:
        self.set_focus(self.query_one("#session-list", ListView))

    def action_focus_summary(self) -> None:
        self.set_focus(self.query_one("#summary-body", PaneBody))

    def action_focus_detail(self) -> None:
        self.set_focus(self.query_one("#detail-view", RichLog))

    def action_redact_step(self) -> None:
        """Redact a step in step view. Prompts for step index to avoid wrong-step redaction."""
        if not self._in_step_view or not self._step_view_trace:
            self.notify("Enter step view first (press Enter)", severity="warning")
            return

        trace = self._step_view_trace
        trace_id = trace["trace_id"]
        steps = trace.get("steps", [])

        if not steps:
            self.notify("No steps to redact", severity="warning")
            return

        # Show available step indices and ask user to type the index
        # For now, notify with instructions, use the web UI for step-level redaction
        self.notify(
            f"Step redaction: use 'opentraces web' for step-level control. "
            f"Session has {len(steps)} steps (indices 0-{len(steps)-1}).",
            severity="information",
        )

    # --- Events ---

    @on(ListView.Selected, "#session-list")
    def on_session_selected(self, event: ListView.Selected) -> None:
        if self._in_step_view:
            return
        item = event.item
        if isinstance(item, SessionListItem):
            self._show_step_view(item.trace)

    @on(ListView.Highlighted, "#session-list")
    def on_session_highlighted(self, event: ListView.Highlighted) -> None:
        if self._in_step_view:
            return
        item = event.item
        if isinstance(item, SessionListItem):
            self._show_detail(item.trace)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for the terminal inbox console script."""
    staging_dir = STAGING_DIR

    # Parse --staging-dir from sys.argv
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--staging-dir" and i + 1 < len(args):
            staging_dir = Path(args[i + 1])
            i += 2
        elif args[i].startswith("--staging-dir="):
            staging_dir = Path(args[i].split("=", 1)[1])
            i += 1
        else:
            i += 1

    app = OpenTracesApp(staging_dir=staging_dir)
    app.run()


if __name__ == "__main__":
    main()
