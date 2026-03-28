"""Textual-based TUI for reviewing opentraces sessions.

Provides a two-panel terminal UI (lazytraces) for browsing, approving,
rejecting, redacting, and discarding staged trace sessions.
"""

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
from textual.widgets import Footer, Header, ListItem, ListView, RichLog, Static

from ..config import STAGING_DIR
from ..state import StateManager, TraceStatus


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
        status_map = {
            TraceStatus.STAGED: "staged",
            TraceStatus.APPROVED: "approved",
            TraceStatus.REJECTED: "rejected",
            TraceStatus.UPLOADED: "uploaded",
        }
        return status_map.get(entry.status, "pending")
    return "pending"


def _status_icon(status: str) -> str:
    return {
        "approved": "[green]\u2713[/green]",
        "rejected": "[red]\u2717[/red]",
        "staged": "[yellow]\u25CB[/yellow]",
        "uploaded": "[green]\u2713[/green]",
    }.get(status, "[yellow]\u25CB[/yellow]")


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
        yield Static(_trace_summary(self.trace, self.trace_status), markup=True)

    def refresh_label(self, status: str) -> None:
        self.trace_status = status
        label = _trace_summary(self.trace, status)
        self.query_one(Static).update(label)


class StatusBar(Static):
    """Aggregate counts bar at the top-right area."""

    def __init__(self) -> None:
        super().__init__("")

    def update_counts(self, staged: int, approved: int, rejected: int, pushed: int) -> None:
        text = (
            f"[yellow]staged {staged}[/yellow] | "
            f"[green]approved {approved}[/green] | "
            f"[red]rejected {rejected}[/red] | "
            f"[dim]pushed {pushed}[/dim]"
        )
        self.update(text)


class HelpOverlay(Static):
    """Full-screen help overlay."""

    HELP_TEXT = (
        "[bold]Keybindings[/bold]\n\n"
        "  [bold]j / k[/bold]  or  [bold]up / down[/bold]   Navigate sessions\n"
        "  [bold]a[/bold]                        Approve selected session\n"
        "  [bold]r[/bold]                        Reject selected session\n"
        "  [bold]d[/bold]                        Discard (delete staging file + state)\n"
        "  [bold]p[/bold]                        Push all approved sessions\n"
        "  [bold]Enter[/bold]                    Expand step-by-step detail view\n"
        "  [bold]x[/bold]                        Redact selected step (in step view)\n"
        "  [bold]Esc[/bold]                      Back from step view / close help\n"
        "  [bold]?[/bold]                        Toggle this help overlay\n"
        "  [bold]q[/bold]                        Quit\n"
    )

    def __init__(self) -> None:
        super().__init__(self.HELP_TEXT, markup=True)
        self.styles.display = "none"
        self.styles.width = "100%"
        self.styles.height = "100%"
        self.styles.background = "#1a1a2e"
        self.styles.color = "#e0e0e0"
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
    background: #0f0f1a;
    layers: base overlay;
}

#main-container {
    height: 1fr;
}

#left-panel {
    width: 45%;
    border-right: solid #333;
    height: 100%;
}

#right-panel {
    width: 55%;
    height: 100%;
    padding: 0 1;
}

#session-list {
    height: 1fr;
}

#session-list > ListItem {
    padding: 0 1;
    height: 1;
}

#session-list > ListItem.-selected {
    background: #f97316 30%;
    color: #f97316;
}

#session-list:focus > ListItem.-selected {
    background: #f97316 50%;
    color: #ffffff;
}

#detail-view {
    height: 1fr;
    scrollbar-size: 1 1;
}

#status-bar {
    dock: top;
    height: 1;
    padding: 0 2;
    background: #1a1a2e;
}

#step-list {
    height: 1fr;
}

#step-list > ListItem {
    padding: 0 1;
    height: auto;
    max-height: 4;
}

#step-list > ListItem.-selected {
    background: #f97316 30%;
}

#step-list:focus > ListItem.-selected {
    background: #f97316 50%;
}

HelpOverlay {
    layer: overlay;
}

Footer {
    background: #1a1a2e;
}

Header {
    background: #1a1a2e;
    color: #f97316;
}
"""


class LazyTracesApp(App):
    """Textual TUI for reviewing opentraces sessions."""

    TITLE = "lazytraces"
    SUB_TITLE = "opentraces session reviewer"
    CSS = CSS

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "toggle_help", "Help", key_display="?"),
        Binding("a", "approve", "Approve"),
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
        super().__init__()
        self.staging_dir = staging_dir
        self.traces: list[dict[str, Any]] = []
        self.state = StateManager()
        self._in_step_view = False
        self._step_view_trace: dict[str, Any] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield StatusBar(id="status-bar")
        with Horizontal(id="main-container"):
            with Vertical(id="left-panel"):
                yield ListView(id="session-list")
            with Vertical(id="right-panel"):
                yield RichLog(id="detail-view", markup=True, wrap=True)
        yield HelpOverlay()
        yield Footer()

    def on_mount(self) -> None:
        self._reload_traces()

    # --- Data loading ---

    def _reload_traces(self) -> None:
        self.traces = _load_staged_traces(self.staging_dir)
        session_list = self.query_one("#session-list", ListView)
        session_list.clear()

        for trace in self.traces:
            status = _get_review_status(self.state, trace["trace_id"])
            item = SessionListItem(trace, status)
            session_list.append(item)

        self._update_status_bar()

        if self.traces:
            session_list.index = 0
            self._show_detail(self.traces[0])

    def _update_status_bar(self) -> None:
        staged = approved = rejected = pushed = 0
        for t in self.traces:
            s = _get_review_status(self.state, t["trace_id"])
            if s == "approved":
                approved += 1
            elif s == "rejected":
                rejected += 1
            elif s == "uploaded":
                pushed += 1
            else:
                staged += 1
        self.query_one(StatusBar).update_counts(staged, approved, rejected, pushed)

    # --- Detail panel ---

    def _show_detail(self, trace: dict[str, Any]) -> None:
        detail = self.query_one("#detail-view", RichLog)
        detail.clear()
        self._in_step_view = False
        self._step_view_trace = None

        # Hide step list if present
        for sl in self.query("#step-list"):
            sl.remove()

        trace_id = trace["trace_id"]
        status = _get_review_status(self.state, trace_id)
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

        detail.write(f"[bold]{_status_icon(status)} {status.upper()}[/bold]  {trace_id}")
        detail.write("")
        detail.write(f"[bold]Task:[/bold] {task}")
        detail.write(f"[bold]Agent:[/bold] {agent}  [bold]Model:[/bold] {model}")
        detail.write(f"[bold]Steps:[/bold] {total_steps}  [bold]Tool calls:[/bold] {tool_calls}")
        detail.write(f"[bold]Tokens:[/bold] {tokens_in} in / {tokens_out} out")
        if cost is not None:
            detail.write(f"[bold]Cost:[/bold] ${cost:.4f}")
        detail.write(f"[bold]Time:[/bold] {ts_start} - {ts_end}")

        if flags:
            detail.write("")
            detail.write(f"[red bold]Security flags ({len(flags)}):[/red bold]")
            for f in flags:
                sev = f.get("severity", "")
                detail.write(f"  [{sev}] {f.get('type', '')} - {f.get('reason', '')} (step {f.get('step_index', '?')})")

        detail.write("")
        detail.write("[dim]Press Enter to expand step-by-step view[/dim]")

    def _show_step_view(self, trace: dict[str, Any]) -> None:
        """Replace right panel content with scrollable step detail list."""
        self._in_step_view = True
        self._step_view_trace = trace

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
                truncated = (content or "")[:120].replace("\n", " ")
                detail.write(f"[{role_color} bold][{role.upper()}][/{role_color} bold] step {i}  {truncated}")

            # Tool calls
            for tc in step.get("tool_calls", []):
                tool_name = tc.get("tool_name", "?")
                tool_input = str(tc.get("input", ""))[:60].replace("\n", " ")
                detail.write(f"  [dim]\u2514 {tool_name}({tool_input})[/dim]")

            detail.write("")

        detail.write("[dim]Press x to redact highlighted step, Esc to go back[/dim]")

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
            item.refresh_label("approved")
        self._show_detail(trace)
        self._update_status_bar()
        self.notify("Approved", severity="information")

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
        self.notify("Push not yet wired in TUI", severity="information")

    def action_expand(self) -> None:
        if self._in_step_view:
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
            return

    def action_toggle_help(self) -> None:
        self.query_one(HelpOverlay).toggle()

    def action_cursor_down(self) -> None:
        session_list = self.query_one("#session-list", ListView)
        if session_list.index is not None and session_list.index < len(self.traces) - 1:
            session_list.index += 1

    def action_cursor_up(self) -> None:
        session_list = self.query_one("#session-list", ListView)
        if session_list.index is not None and session_list.index > 0:
            session_list.index -= 1

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
            f"Step redaction: use 'opentraces review --web' for step-level control. "
            f"Session has {len(steps)} steps (indices 0-{len(steps)-1}).",
            severity="information",
        )
        return

        # TODO: replace with ListView-based step selection for safe redaction
        step_index = 0  # placeholder, unreachable

        if step_index < 0 or step_index >= len(steps):
            self.notify("No step to redact", severity="warning")
            return

        if steps[step_index].get("content") == "[REDACTED]":
            self.notify(f"Step {step_index} already redacted", severity="information")
            return

        # Redact in memory
        steps[step_index]["content"] = "[REDACTED]"
        steps[step_index]["reasoning_content"] = None
        steps[step_index]["tool_calls"] = []
        steps[step_index]["observations"] = []
        steps[step_index]["snippets"] = []

        # Persist to staging JSONL
        import re as _re
        if _re.match(r'^[a-f0-9-]+$', trace_id):
            staging_file = self.staging_dir / f"{trace_id}.jsonl"
            if staging_file.exists():
                try:
                    new_line = json.dumps(trace, ensure_ascii=False)
                    fd = tempfile.NamedTemporaryFile(
                        mode="w",
                        dir=str(self.staging_dir),
                        suffix=".jsonl.tmp",
                        delete=False,
                    )
                    try:
                        fd.write(new_line + "\n")
                        fd.flush()
                        os.fsync(fd.fileno())
                        fd.close()
                        os.replace(fd.name, str(staging_file))
                    except BaseException:
                        fd.close()
                        try:
                            os.unlink(fd.name)
                        except OSError:
                            pass
                        raise
                except OSError:
                    pass

        self._show_step_view(trace)
        self.notify(f"Step {step_index} redacted", severity="information")

    # --- Events ---

    @on(ListView.Selected, "#session-list")
    def on_session_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, SessionListItem):
            self._show_detail(item.trace)

    @on(ListView.Highlighted, "#session-list")
    def on_session_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if isinstance(item, SessionListItem):
            self._show_detail(item.trace)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for the lazytraces console script."""
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

    app = LazyTracesApp(staging_dir=staging_dir)
    app.run()


if __name__ == "__main__":
    main()
