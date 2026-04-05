"""Session list item widgets for the inbox screen."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.widgets import ListItem, Static

from ..utils import (
    _relative_time,
    _stage_color_ansi,
    _truncate,
    escape,
)
from ....workflow import stage_label


class SessionBlock(ListItem):
    """A single session row in the inbox list.

    Accepts either a TraceIndexEntry dataclass or a raw dict. Uses duck typing
    to access fields: tries attribute access first, falls back to dict .get().
    """

    def __init__(self, trace: Any, status: str) -> None:
        super().__init__()
        self.trace = trace
        self.trace_status = status
        self.is_selected = False

    def _field(self, name: str, default: Any = "") -> Any:
        """Get a field from the trace, supporting both dataclass and dict."""
        return getattr(self.trace, name, None) or (
            self.trace.get(name, default) if isinstance(self.trace, dict) else default
        )

    def compose(self) -> ComposeResult:
        yield Static(self._render_row(), markup=True, classes="session-row")

    def _render_row(self) -> str:
        task = _truncate(self._field("task_description", "No description"), 44)
        # Fallback for raw dict traces
        if task == "No description" and isinstance(self.trace, dict):
            task = _truncate(
                self.trace.get("task", {}).get("description") or "No description", 44
            )
        agent = self._field("agent_name", "unknown")
        if agent == "unknown" and isinstance(self.trace, dict):
            agent = self.trace.get("agent", {}).get("name", "unknown")
        model = self._field("agent_model", "unknown")
        if model == "unknown" and isinstance(self.trace, dict):
            model = self.trace.get("agent", {}).get("model") or "unknown"
        model = model.split("/")[-1]
        steps = self._field("total_steps", 0)
        if not steps and isinstance(self.trace, dict):
            steps = self.trace.get("metrics", {}).get(
                "total_steps", len(self.trace.get("steps", []))
            )
        flags = self._field("security_flags_count", 0)
        if not flags and isinstance(self.trace, dict):
            flags = len(self.trace.get("_security_flags", []))
        ts = _relative_time(self._field("timestamp_start"))
        stage = stage_label(self.trace_status).upper()
        stage_color = _stage_color_ansi(self.trace_status)

        marker = "[white]\u25cf[/white]" if self.is_selected else "[dim]\u00b7[/dim]"
        flag_text = f"  [red]{flags}f[/red]" if flags else ""
        return (
            f"{marker} [{stage_color}]{stage[:1]}[/{stage_color}] "
            f"{escape(task)}  "
            f"[dim]{agent[:8]}/{model[:8]} {steps}s {ts}[/dim]"
            f"{flag_text}"
        )

    def refresh_label(
        self, status: str | None = None, selected: bool | None = None
    ) -> None:
        if status is not None:
            self.trace_status = status
        if selected is not None:
            self.is_selected = selected
        try:
            self.query_one(Static).update(self._render_row())
        except Exception:
            pass


class StageHeader(ListItem):
    """Non-interactive stage group divider."""

    def __init__(self, status: str, count: int) -> None:
        super().__init__(disabled=True)
        self.status = status
        self.count = count

    def compose(self) -> ComposeResult:
        color = _stage_color_ansi(self.status)
        label = stage_label(self.status).upper()
        yield Static(
            f"[{color}]\u25cf[/{color}] [bold]{label}[/bold] [dim]{self.count}[/dim]",
            markup=True,
            classes="stage-header",
        )
