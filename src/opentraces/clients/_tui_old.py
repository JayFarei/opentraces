"""Textual-based TUI for the OpenTraces repo inbox."""

from __future__ import annotations

import json
import logging
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any

def escape(text: str) -> str:
    """Escape ALL square brackets for Rich markup, not just tag-like ones."""
    return text.replace("[", "\\[")
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.widgets import ListItem, ListView, RichLog, Static

from ..config import STAGING_DIR, get_project_state_path, load_project_config
from ..inbox import get_stage, load_traces
from ..state import StateManager, TraceStatus
from ..workflow import OPENTRACES_ASCII, VISIBLE_STAGE_ORDER, resolve_visible_stage, stage_label

logger = logging.getLogger(__name__)


def _status_icon(status: str) -> str:
    return {
        "committed": "[cyan]\u25A0[/cyan]",
        "rejected": "[red]\u2717[/red]",
        "inbox": "[yellow]\u25CB[/yellow]",
        "pushed": "[green]\u2713[/green]",
    }.get(status, "[yellow]\u25CB[/yellow]")


def _stage_color(status: str) -> str:
    return {
        "inbox": "ansi_yellow",
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
    status = get_stage(state, trace["trace_id"])
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


def _single_line(value: Any, limit: int = 120) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=True, separators=(",", ": "))
        except TypeError:
            text = str(value)
    compact = " ".join(text.replace("\n", " ").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)] + "..."


def _line_limit(widget: Static | RichLog, padding: int = 6, floor: int = 48) -> int:
    width = getattr(widget.size, "width", 0) or 0
    return max(floor, width - padding)


def _fold_lines(value: Any, width: int) -> list[str]:
    if value is None:
        return [""]
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=True, indent=2)
        except TypeError:
            value = str(value)
    wrapped: list[str] = []
    for raw_line in str(value).splitlines() or [""]:
        chunks = textwrap.wrap(
            raw_line,
            width=width,
            break_long_words=True,
            break_on_hyphens=False,
            replace_whitespace=False,
            drop_whitespace=False,
        )
        wrapped.extend(chunks or [""])
    return wrapped


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _format_offset(trace_start: str | None, step_ts: str | None) -> str:
    start = _parse_iso(trace_start)
    step = _parse_iso(step_ts)
    if not start or not step:
        return ""
    seconds = max(0, int((step - start).total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _tool_color(tool_name: str) -> str:
    if tool_name in {"Read", "Edit", "Write", "Grep", "Glob", "Bash"}:
        return "ansi_green"
    if tool_name in {"WebSearch", "WebFetch", "ToolSearch"}:
        return "ansi_yellow"
    if tool_name == "Agent":
        return "ansi_cyan"
    if tool_name == "AskUserQuestion":
        return "ansi_bright_blue"
    if tool_name == "Skill":
        return "ansi_magenta"
    return "ansi_bright_black"


def _role_color(role: str, call_type: str | None = None) -> str:
    if role == "user":
        return "ansi_bright_blue"
    if role == "subagent" or call_type == "subagent":
        return "ansi_cyan"
    if role == "agent":
        return "ansi_magenta"
    if role == "system":
        return "ansi_bright_black"
    return "ansi_default"


def _source_label(step: dict[str, Any], tool_name: str | None = None) -> str:
    role = step.get("role")
    if role == "user":
        return "user"
    if tool_name in {"Read", "Edit", "Write", "Grep", "Glob", "Bash"}:
        return "proj"
    if tool_name in {"WebSearch", "WebFetch", "ToolSearch"}:
        return "ext"
    if role == "system":
        return "proj"
    return "agent"


def _source_color(source: str) -> str:
    return {
        "user": "ansi_bright_blue",
        "agent": "ansi_magenta",
        "proj": "ansi_green",
        "ext": "ansi_yellow",
    }.get(source, "ansi_default")


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class SessionListItem(ListItem):
    """A single session row in the left panel."""

    def __init__(self, trace: dict[str, Any], status: str) -> None:
        super().__init__()
        self.trace = trace
        self.trace_status = status
        self.is_selected = False

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
        marker = "[ansi_white]●[/ansi_white]" if self.is_selected else "[dim]·[/dim]"
        flag_text = f"  [ansi_red]{flags}f[/ansi_red]" if flags else ""
        return (
            f"{marker} {escape(task)}\n"
            f"[{stage_color}]{stage}[/{stage_color}] [dim]{agent[:8]}/{model[:8]}  {steps}s {tool_calls}t  {ts}{flag_text}[/dim]"
        )

    def refresh_label(self, status: str | None = None, selected: bool | None = None) -> None:
        if status is not None:
            self.trace_status = status
        if selected is not None:
            self.is_selected = selected
        self.query_one(Static).update(self._render_row())


class StepListItem(ListItem):
    """A single trace step row in inspect mode."""

    def __init__(self, step: dict[str, Any], step_index: int, trace_start: str | None) -> None:
        super().__init__()
        self.step = step
        self.step_index = step_index
        self.trace_start = trace_start
        self.is_selected = False

    def compose(self) -> ComposeResult:
        yield Static(self._render_row(), markup=True, classes="step-row")

    def _render_row(self) -> str:
        role = self.step.get("role", "?")
        call_type = self.step.get("call_type")
        role_color = _role_color(role, call_type)
        role_tag = role.upper()
        offset = _format_offset(self.trace_start, self.step.get("timestamp")) or "--:--"
        tool_calls = self.step.get("tool_calls", [])
        tool_label = ""
        if tool_calls:
            first_tool = tool_calls[0].get("tool_name", "?")
            tool_label = f"  [{_tool_color(first_tool)}]{first_tool}[/{_tool_color(first_tool)}]"
            if len(tool_calls) > 1:
                tool_label += f" [dim]+{len(tool_calls) - 1}[/dim]"
        marker = "[ansi_white]●[/ansi_white]" if self.is_selected else "[dim]·[/dim]"
        content = _single_line(self.step.get("content"), 52)
        if content == "[REDACTED]":
            content_markup = "[ansi_red][REDACTED][/ansi_red]"
        elif content:
            content_markup = escape(content)
        elif tool_calls:
            content_markup = "[dim]Tool activity[/dim]"
        else:
            content_markup = "[dim]No inline content[/dim]"
        return (
            f"{marker} [dim]{offset:>7}[/dim] [{role_color}]{role_tag}[/{role_color}] [dim]#{self.step_index}[/dim]{tool_label}\n"
            f"{content_markup}"
        )

    def set_selected(self, selected: bool) -> None:
        self.is_selected = selected
        self.query_one(Static).update(self._render_row())


class StageHeaderItem(ListItem):
    """Non-session divider row in the session sidebar."""

    def __init__(self, status: str, count: int) -> None:
        super().__init__()
        self.status = status
        self.count = count

    def compose(self) -> ComposeResult:
        color = _stage_color(self.status)
        label = stage_label(self.status).upper()
        yield Static(
            f"[{color}]●[/{color}] [bold]{label}[/bold] [dim]{self.count}[/dim]",
            markup=True,
            classes="stage-header",
        )


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
            f"[ansi_bright_blue]COMMITTED[/ansi_bright_blue]: {counts['committed']}   "
            f"[ansi_cyan]PUSHED[/ansi_cyan]: {counts['pushed']}   "
            f"[ansi_red]REJECTED[/ansi_red]: {counts['rejected']}"
        )
        self.update(text)


class KeyBar(Static):
    """Persistent keybinding footer."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__("", *args, **kwargs)

    def update_mode(self, in_step_view: bool, fullscreen: bool = False) -> None:
        fullscreen_hint = "[bold]f[/bold] windowed   " if fullscreen else "[bold]f[/bold] fullscreen   "
        if in_step_view:
            text = (
                "[bold]1[/bold] sessions   [bold]2[/bold] summary   [bold]3[/bold] detail   "
                f"{fullscreen_hint}"
                "[bold]esc[/bold] back   [bold]x[/bold] redact hint   "
                "[bold]?[/bold] help   [bold]q[/bold] quit"
            )
        else:
            text = (
                "[bold]1[/bold] sessions   [bold]2[/bold] summary   [bold]3[/bold] detail   "
                f"{fullscreen_hint}"
                "[bold]j/k[/bold] move   [bold]enter[/bold] inspect   "
                "[bold]c[/bold] commit   [bold]r[/bold] reject   "
                "[bold]d[/bold] discard   [bold]p[/bold] push   "
                "[bold]?[/bold] help   [bold]q[/bold] quit"
            )
        self.update(text)


class PaneBody(Static, can_focus=True):
    """Focusable pane body for keyboard navigation."""


class FocusableLog(RichLog, can_focus=True):
    """RichLog variant that can participate in keyboard focus flow."""


class HelpOverlay(Static):
    """Full-screen help overlay."""

    HELP_TEXT = (
        "[bold]Keybindings[/bold]\n\n"
        "  [bold]j / k[/bold]  or  [bold]up / down[/bold]   Navigate sessions\n"
        "  [bold]c[/bold]                        Commit selected session for push\n"
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
        super().__init__(self.HELP_TEXT, *args, markup=True, **kwargs)
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
    padding: 0 1 1 1;
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
    width: 40;
    min-width: 36;
    margin-right: 1;
}

#sidebar-meta {
    height: 4;
    padding: 0 1;
    color: ansi_bright_black;
    border-bottom: solid ansi_bright_black;
}

#session-list {
    height: 1fr;
    background: ansi_default;
    scrollbar-size-vertical: 1;
    scrollbar-size-horizontal: 1;
    scrollbar-background: ansi_default;
    scrollbar-background-hover: ansi_default;
    scrollbar-background-active: ansi_default;
    scrollbar-color: ansi_bright_black;
    scrollbar-color-hover: ansi_bright_black;
    scrollbar-color-active: ansi_bright_black;
    scrollbar-corner-color: ansi_default;
}

#session-list > ListItem {
    padding: 0 1;
    height: 2;
    background: ansi_default;
    color: ansi_default;
}

#session-list > ListItem.-selected {
    background: ansi_default;
    color: ansi_default;
    text-style: none;
}

#session-list:focus > ListItem.-selected {
    background: ansi_default;
    color: ansi_default;
    text-style: none;
}

.stage-header {
    height: 1;
    color: ansi_bright_black;
}

#session-list > StageHeaderItem {
    height: 1;
    padding: 0 1;
    background: ansi_default;
    color: ansi_bright_black;
}

#main-column {
    width: 1fr;
    height: 100%;
}

#summary-panel {
    height: 11;
    margin-bottom: 1;
}

#summary-body {
    padding: 0 1 1 1;
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

#inspect-workspace {
    display: none;
    height: 1fr;
}

#step-list-panel {
    width: 42;
    min-width: 36;
    margin-right: 1;
    border-right: solid ansi_bright_black;
}

#step-list-title,
#step-content-title {
    height: 1;
    padding: 0 1;
    color: ansi_bright_black;
    text-style: bold;
}

#step-list {
    height: 1fr;
    background: ansi_default;
    scrollbar-size-vertical: 1;
    scrollbar-size-horizontal: 1;
    scrollbar-background: ansi_default;
    scrollbar-background-hover: ansi_default;
    scrollbar-background-active: ansi_default;
    scrollbar-color: ansi_bright_black;
    scrollbar-color-hover: ansi_bright_black;
    scrollbar-color-active: ansi_bright_black;
    scrollbar-corner-color: ansi_default;
}

#step-list > ListItem {
    padding: 0 1;
    height: 2;
    background: ansi_default;
    color: ansi_default;
}

#step-list > ListItem.-selected {
    background: ansi_default;
    color: ansi_default;
    text-style: none;
}

#step-list:focus > ListItem.-selected {
    background: ansi_default;
    color: ansi_default;
    text-style: none;
}

.step-row {
    height: 2;
}

#step-content-panel {
    width: 1fr;
}

#step-content {
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

Screen.detail-fullscreen #sidebar-panel,
Screen.detail-fullscreen #summary-panel {
    display: none;
}

Screen.detail-fullscreen #main-column {
    width: 100%;
}

Screen.detail-fullscreen #detail-panel {
    height: 100%;
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

    BINDINGS = (
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "toggle_help", "Help", key_display="?"),
        Binding("1", "focus_sessions", "Sessions", show=False, priority=True),
        Binding("2", "focus_summary", "Summary", show=False, priority=True),
        Binding("3", "focus_detail", "Detail", show=False, priority=True),
        Binding("f", "toggle_fullscreen", "Fullscreen", show=False, priority=True),
        Binding("c", "commit", "Commit", priority=True),
        Binding("r", "reject", "Reject", priority=True),
        Binding("d", "discard", "Discard", priority=True),
        Binding("p", "push", "Push", priority=True),
        Binding("enter", "expand", "Expand", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("j", "cursor_down", "Down", show=False, priority=True),
        Binding("k", "cursor_up", "Up", show=False, priority=True),
        Binding("x", "redact_step", "Redact step", show=False, priority=True),
    )

    def __init__(self, staging_dir: Path, fullscreen: bool = False) -> None:
        super().__init__(ansi_color=True)
        self.theme = "textual-ansi"
        self.staging_dir = staging_dir
        self.project_dir = _project_dir_from_staging(staging_dir)
        self.traces: list[dict[str, Any]] = []
        state_path = get_project_state_path(self.project_dir)
        self.state = StateManager(state_path=state_path if state_path.parent.exists() else None)
        self._in_step_view = False
        self._detail_fullscreen = False
        self._launch_fullscreen = fullscreen
        self._step_view_trace: dict[str, Any] | None = None
        self._step_content_active = False
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
                        yield FocusableLog(id="detail-view", markup=True, wrap=True)
                        with Horizontal(id="inspect-workspace"):
                            with Vertical(id="step-list-panel"):
                                yield Static("Steps", id="step-list-title")
                                yield ListView(id="step-list")
                            with Vertical(id="step-content-panel"):
                                yield Static("Step Content", id="step-content-title")
                                yield FocusableLog(id="step-content", markup=True, wrap=True)
            yield KeyBar(id="keybar", markup=True)

    def on_mount(self) -> None:
        self._load_project_context()
        self.query_one(KeyBar).update_mode(False, fullscreen=self._detail_fullscreen)
        self._reload_traces()
        if self._launch_fullscreen and self.traces:
            self._show_step_view(self.traces[0], fullscreen=True)
            return
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
        self.query_one("#inspect-workspace", Horizontal).styles.display = "none"
        self.query_one("#detail-view", RichLog).styles.display = "block"
        summary.update(
            "[bold]No sessions in this inbox[/bold]\n"
            "[dim]Run opentraces init in this repo and finish a connected agent session.[/dim]"
        )
        detail.clear()
        detail.write(f"[bold ansi_bright_blue]{OPENTRACES_ASCII}[/bold ansi_bright_blue]")
        detail.write("")
        detail.write("[dim]This repo inbox is empty.[/dim]")
        detail.write("[dim]OpenTraces will capture sessions here after setup.[/dim]")

    def _reload_traces(self, selected_trace_id: str | None = None) -> None:
        self.traces = sorted(load_traces(self.staging_dir), key=lambda trace: _sort_key(trace, self.state))
        session_list = self.query_one("#session-list", ListView)
        session_list.clear()

        counts = self._stage_counts()
        for status in VISIBLE_STAGE_ORDER:
            group = [trace for trace in self.traces if get_stage(self.state, trace["trace_id"]) == status]
            if not group:
                continue
            session_list.append(StageHeaderItem(status, counts[status]))
            for trace in group:
                item = SessionListItem(trace, status)
                session_list.append(item)

        self._update_status_bar()
        self._update_sidebar_meta()

        if self.traces:
            if selected_trace_id is not None and not self._select_trace(selected_trace_id):
                self._move_to_first_session()
            elif selected_trace_id is None:
                self._move_to_first_session()
            selected = self._get_selected_item()
            if selected:
                self._sync_session_selection(selected)
                self._show_detail(selected.trace)
        else:
            self._set_empty_state()

    def _stage_counts(self) -> dict[str, int]:
        counts = {stage: 0 for stage in VISIBLE_STAGE_ORDER}
        for t in self.traces:
            s = get_stage(self.state, t["trace_id"])
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
            f"[ansi_yellow]{counts['inbox']} inbox[/ansi_yellow]   "
            f"[ansi_bright_blue]{counts['committed']} committed[/ansi_bright_blue]"
        )

    def _move_to_first_session(self) -> None:
        session_list = self.query_one("#session-list", ListView)
        for index, child in enumerate(session_list.children):
            if isinstance(child, SessionListItem):
                session_list.index = index
                return

    def _select_trace(self, trace_id: str) -> bool:
        session_list = self.query_one("#session-list", ListView)
        for index, child in enumerate(session_list.children):
            if isinstance(child, SessionListItem) and child.trace.get("trace_id") == trace_id:
                session_list.index = index
                return True
        return False

    def _sync_session_selection(self, selected_item: SessionListItem | None) -> None:
        session_list = self.query_one("#session-list", ListView)
        for child in session_list.children:
            if isinstance(child, SessionListItem):
                child.refresh_label(selected=(child is selected_item))

    def _sync_step_selection(self, selected_item: StepListItem | None) -> None:
        step_list = self.query_one("#step-list", ListView)
        for child in step_list.children:
            if isinstance(child, StepListItem):
                child.set_selected(child is selected_item)

    def _move_list_selection(self, list_id: str, delta: int, selectable_type: type[ListItem]) -> None:
        list_view = self.query_one(list_id, ListView)
        children = list(list_view.children)
        if not children:
            return
        index = list_view.index if list_view.index is not None else -1
        cursor = index + delta
        while 0 <= cursor < len(children):
            if isinstance(children[cursor], selectable_type):
                list_view.index = cursor
                return
            cursor += delta

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

    def _set_fullscreen(self, enabled: bool) -> None:
        self._detail_fullscreen = enabled
        if enabled:
            self.screen.add_class("detail-fullscreen")
        else:
            self.screen.remove_class("detail-fullscreen")
        self.query_one(KeyBar).update_mode(self._in_step_view, fullscreen=enabled)

    def _update_inspect_titles(self, step_count: int | None = None) -> None:
        count_text = f" ({step_count})" if step_count is not None else ""
        step_title = f"Steps{count_text}"
        content_title = "Step Content"
        if self._step_content_active:
            content_title += "  scroll"
        else:
            step_title += "  browse"
        self.query_one("#step-list-title", Static).update(step_title)
        self.query_one("#step-content-title", Static).update(content_title)

    def _show_standard_detail_view(self) -> None:
        self.query_one("#inspect-workspace", Horizontal).styles.display = "none"
        self.query_one("#detail-view", RichLog).styles.display = "block"

    def _show_inspect_view(self) -> None:
        self.query_one("#detail-view", RichLog).styles.display = "none"
        self.query_one("#inspect-workspace", Horizontal).styles.display = "block"

    def _show_detail(self, trace: dict[str, Any]) -> None:
        detail = self.query_one("#detail-view", RichLog)
        detail.clear()
        self._show_standard_detail_view()
        self._in_step_view = False
        self._step_view_trace = None
        self._step_content_active = False
        self._set_fullscreen(False)
        self.query_one(KeyBar).update_mode(False, fullscreen=self._detail_fullscreen)
        self._set_detail_title("[3] Detail")
        self._sync_session_selection(self._get_selected_item())

        trace_id = trace["trace_id"]
        status = get_stage(self.state, trace_id)
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
        line_limit = _line_limit(detail)
        step_limit = max(32, line_limit - 12)

        detail.write(f"[bold]{escape(_single_line(task, line_limit))}[/bold]")
        detail.write("")
        detail.write(f"[dim]trace[/dim] {escape(_single_line(trace_id, line_limit - 8))}")
        detail.write(f"[dim]status[/dim] {stage_label(status)}   [dim]agent[/dim] {escape(_single_line(agent, step_limit))}")
        detail.write(f"[dim]model[/dim] {escape(_single_line(str(model), line_limit - 8))}")
        detail.write(f"[dim]steps[/dim] {total_steps}   [dim]tool calls[/dim] {tool_calls}")
        detail.write(f"[dim]tokens[/dim] {tokens_in} in / {tokens_out} out")
        if cost is not None:
            detail.write(f"[dim]cost[/dim] ${cost:.4f}")
        detail.write(f"[dim]time[/dim] {escape(_single_line(f'{ts_start} -> {ts_end}', line_limit - 7))}")

        if flags:
            detail.write("")
            detail.write(f"[bold ansi_red]Security flags ({len(flags)})[/bold ansi_red]")
            for f in flags:
                sev = f.get("severity", "")
                flag_line = f"{f.get('type', '')} -> {f.get('reason', '')} (step {f.get('step_index', '?')})"
                detail.write(f"  [{sev}] {escape(_single_line(flag_line, line_limit - 6))}")

        if steps:
            detail.write("")
            detail.write("[bold]Recent steps[/bold]")
            for i, step in enumerate(steps[:6]):
                role = step.get("role", "?")
                role_color = {"user": "cyan", "agent": "green", "system": "yellow"}.get(role, "white")
                content = _single_line(step.get("content", "") or "", step_limit)
                detail.write(f"  [{role_color}]{role.upper()}[/{role_color}]  {escape(content or '[no content]')}")

        detail.write("")
        detail.write("[dim]Press Enter to inspect every step in this session.[/dim]")

    def _render_step_content(self, trace: dict[str, Any], step_index: int) -> None:
        step = trace.get("steps", [])[step_index]
        step_content = self.query_one("#step-content", RichLog)
        step_content.clear()
        line_limit = _line_limit(step_content, padding=8, floor=44)

        role = step.get("role", "?")
        call_type = step.get("call_type")
        role_color = _role_color(role, call_type)
        role_tag = role.upper()
        offset = _format_offset(trace.get("timestamp_start"), step.get("timestamp")) or "--:--"
        content = step.get("content")

        step_content.write(
            f"[dim]{offset:>7}[/dim] [{role_color} bold]{role_tag}[/{role_color} bold] "
            f"[dim]step {step_index}[/dim]"
        )
        if call_type:
            step_content.write(f"[dim]call type[/dim] {call_type}")
        if content == "[REDACTED]":
            step_content.write("")
            step_content.write("[ansi_red][REDACTED][/ansi_red]")
        elif content:
            step_content.write("")
            for line in _fold_lines(content, line_limit):
                step_content.write(escape(line))

        tool_calls = step.get("tool_calls", [])
        if tool_calls:
            step_content.write("")
            step_content.write("[bold]Tool calls[/bold]")
            for tc in tool_calls:
                tool_name = tc.get("tool_name", "?")
                tool_color = _tool_color(tool_name)
                for index, line in enumerate(_fold_lines(tc.get("input", ""), max(28, line_limit - 4))):
                    prefix = f"[{tool_color}]↳ {escape(tool_name)}[/{tool_color}] " if index == 0 else "  "
                    step_content.write(f"{prefix}[dim]{escape(line)}[/dim]")

        observations = step.get("observations", [])
        if observations:
            step_content.write("")
            step_content.write("[bold]Observations[/bold]")
            for obs in observations:
                obs_text = obs.get("content") or obs.get("output_summary") or ""
                folded = _fold_lines(obs_text, line_limit)
                for line in folded[:8]:
                    step_content.write(f"[dim]{escape(line)}[/dim]")
                if len(folded) > 8:
                    step_content.write("[dim]...[/dim]")

        snippets = step.get("snippets", [])
        if snippets:
            step_content.write("")
            step_content.write("[bold]Snippets[/bold]")
            for snippet in snippets[:3]:
                for line in _fold_lines(snippet, line_limit):
                    step_content.write(f"[dim]{escape(line)}[/dim]")

        step_content.write("")
        step_content.write("[dim]Enter focuses this pane. Esc returns to the step list, then back to the session.[/dim]")

    def _show_step_view(self, trace: dict[str, Any], fullscreen: bool = True) -> None:
        """Switch into inspect mode with a step list and step content pane."""
        self._in_step_view = True
        self._step_view_trace = trace
        self._step_content_active = False
        self._show_inspect_view()
        self._set_fullscreen(fullscreen)
        self.query_one(KeyBar).update_mode(True, fullscreen=self._detail_fullscreen)
        detail_label = "[3] Detail [Inspecting Fullscreen]" if self._detail_fullscreen else "[3] Detail [Inspecting]"
        self._set_detail_title(detail_label)

        steps = trace.get("steps", [])
        step_list = self.query_one("#step-list", ListView)
        step_list.clear()
        if not steps:
            step_content = self.query_one("#step-content", RichLog)
            step_content.clear()
            step_content.write("[dim]No steps in this trace.[/dim]")
            return

        trace_start = trace.get("timestamp_start")
        for i, step in enumerate(steps):
            step_list.append(StepListItem(step, i, trace_start))

        step_list.index = 0
        first_step = next((child for child in step_list.children if isinstance(child, StepListItem)), None)
        self._sync_step_selection(first_step)
        self._update_inspect_titles(len(steps))
        self._render_step_content(trace, 0)
        self.set_focus(step_list)

    # --- Actions ---

    def _get_selected_trace(self) -> dict[str, Any] | None:
        item = self._get_selected_item()
        return item.trace if item else None

    def _get_selected_item(self) -> SessionListItem | None:
        session_list = self.query_one("#session-list", ListView)
        children = list(session_list.children)
        idx = session_list.index if session_list.index is not None else 0
        if 0 <= idx < len(children) and isinstance(children[idx], SessionListItem):
            return children[idx]
        for cursor in range(idx + 1, len(children)):
            item = children[cursor]
            if isinstance(item, SessionListItem):
                session_list.index = cursor
                return item
        for cursor in range(idx - 1, -1, -1):
            item = children[cursor]
            if isinstance(item, SessionListItem):
                session_list.index = cursor
                return item
        return None

    def _focus_within(self, selector: str) -> bool:
        target = self.query_one(selector)
        widget = self.focused
        while widget is not None:
            if widget is target:
                return True
            widget = widget.parent
        return False

    def action_commit(self) -> None:
        trace = self._get_selected_trace()
        if not trace:
            return

        trace_id = trace["trace_id"]
        entry = self.state.get_trace(trace_id)
        current_stage = resolve_visible_stage(entry.status if entry else None)
        if current_stage != "inbox":
            self.notify("Only inbox sessions can be committed", severity="warning")
            return

        task = (trace.get("task", {}).get("description") or "trace")[:60]
        self.state.create_commit_group([trace_id], task)
        self._reload_traces(selected_trace_id=trace_id)
        self.notify("Committed", severity="information")

    def action_reject(self) -> None:
        trace = self._get_selected_trace()
        if not trace:
            return
        trace_id = trace["trace_id"]
        self.state.set_trace_status(trace_id, TraceStatus.REJECTED, session_id=trace_id)
        self._reload_traces(selected_trace_id=trace_id)
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
                logger.warning("Failed to delete staging file %s", staging_file, exc_info=True)

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
            self._step_content_active = True
            self._update_inspect_titles(len(self._step_view_trace.get("steps", [])) if self._step_view_trace else None)
            self.set_focus(self.query_one("#step-content", RichLog))
            return
        trace = self._get_selected_trace()
        if trace:
            self._show_step_view(trace, fullscreen=True)

    def action_back(self) -> None:
        # Close help if open
        help_overlay = self.query_one(HelpOverlay)
        if help_overlay.styles.display != "none":
            help_overlay.toggle()
            return

        if self._in_step_view:
            if self._step_content_active:
                self._step_content_active = False
                self._update_inspect_titles(len(self._step_view_trace.get("steps", [])) if self._step_view_trace else None)
                self.set_focus(self.query_one("#step-list", ListView))
                return
            trace = self._get_selected_trace()
            if trace:
                self._show_detail(trace)
            self.set_focus(self.query_one("#session-list", ListView))
            return

    def action_toggle_help(self) -> None:
        self.query_one(HelpOverlay).toggle()

    def action_toggle_fullscreen(self) -> None:
        if not self._in_step_view:
            return
        self._set_fullscreen(not self._detail_fullscreen)
        detail_label = "[3] Detail [Inspecting Fullscreen]" if self._detail_fullscreen else "[3] Detail [Inspecting]"
        self._set_detail_title(detail_label)
        self.set_focus(self.query_one("#detail-view", RichLog))

    def action_cursor_down(self) -> None:
        if self._in_step_view:
            if self._step_content_active:
                self.query_one("#step-content", RichLog).scroll_down(animate=False, immediate=True)
                return
            self._move_list_selection("#step-list", 1, StepListItem)
            return
        self._move_list_selection("#session-list", 1, SessionListItem)

    def action_cursor_up(self) -> None:
        if self._in_step_view:
            if self._step_content_active:
                self.query_one("#step-content", RichLog).scroll_up(animate=False, immediate=True)
                return
            self._move_list_selection("#step-list", -1, StepListItem)
            return
        self._move_list_selection("#session-list", -1, SessionListItem)

    def action_focus_sessions(self) -> None:
        if self._detail_fullscreen:
            self._set_fullscreen(False)
        self.set_focus(self.query_one("#session-list", ListView))

    def action_focus_summary(self) -> None:
        if self._detail_fullscreen:
            self._set_fullscreen(False)
        self.set_focus(self.query_one("#summary-body", PaneBody))

    def action_focus_detail(self) -> None:
        if self._in_step_view:
            self._step_content_active = True
            self._update_inspect_titles(len(self._step_view_trace.get("steps", [])) if self._step_view_trace else None)
            self.set_focus(self.query_one("#step-content", RichLog))
            return
        self.set_focus(self.query_one("#detail-view", RichLog))

    def action_redact_step(self) -> None:
        """Redact a step in step view. Prompts for step index to avoid wrong-step redaction."""
        if not self._in_step_view or not self._step_view_trace:
            self.notify("Enter step view first (press Enter)", severity="warning")
            return

        trace = self._step_view_trace
        _trace_id = trace["trace_id"]
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

    async def on_key(self, event: Key) -> None:
        keymap = {
            "1": self.action_focus_sessions,
            "2": self.action_focus_summary,
            "3": self.action_focus_detail,
            "f": self.action_toggle_fullscreen,
            "c": self.action_commit,
            "r": self.action_reject,
            "d": self.action_discard,
            "p": self.action_push,
            "enter": self.action_expand,
            "escape": self.action_back,
            "j": self.action_cursor_down,
            "k": self.action_cursor_up,
            "x": self.action_redact_step,
            "question_mark": self.action_toggle_help,
        }
        action = keymap.get(event.key)
        if action is None:
            return
        event.stop()
        action()

    # --- Events ---

    @on(ListView.Selected, "#session-list")
    def on_session_selected(self, event: ListView.Selected) -> None:
        if self._in_step_view:
            return
        item = event.item
        if isinstance(item, SessionListItem):
            self._sync_session_selection(item)
            self._show_step_view(item.trace)

    @on(ListView.Highlighted, "#session-list")
    def on_session_highlighted(self, event: ListView.Highlighted) -> None:
        if self._in_step_view:
            return
        item = event.item
        if isinstance(item, SessionListItem):
            self._sync_session_selection(item)
            self._show_detail(item.trace)

    @on(ListView.Highlighted, "#step-list")
    def on_step_highlighted(self, event: ListView.Highlighted) -> None:
        if not self._in_step_view or not self._step_view_trace:
            return
        item = event.item
        if isinstance(item, StepListItem):
            self._sync_step_selection(item)
            self._render_step_content(self._step_view_trace, item.step_index)

    @on(ListView.Selected, "#step-list")
    def on_step_selected(self, event: ListView.Selected) -> None:
        if not self._in_step_view:
            return
        item = event.item
        if isinstance(item, StepListItem) and self._step_view_trace:
            self._sync_step_selection(item)
            self._render_step_content(self._step_view_trace, item.step_index)
            self._step_content_active = True
            self._update_inspect_titles(len(self._step_view_trace.get("steps", [])))
            self.set_focus(self.query_one("#step-content", RichLog))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for the terminal inbox console script."""
    staging_dir = STAGING_DIR
    fullscreen = False

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
        elif args[i] == "--fullscreen":
            fullscreen = True
            i += 1
        else:
            i += 1

    app = OpenTracesApp(staging_dir=staging_dir, fullscreen=fullscreen)
    app.run()


if __name__ == "__main__":
    main()
