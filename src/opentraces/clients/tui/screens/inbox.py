"""Session curation inbox -- the default screen."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import ListView, ListItem, Static

from ..messages import FlashMessage, RefreshRequested, StageChanged, TraceSelected
from ..utils import (
    ANSI_COLORS,
    _relative_time,
    _single_line,
    _stage_color_ansi,
    _truncate,
    escape,
)
from ..widgets.filter_input import FilterChanged, FilterClosed, FilterInput
from ..widgets.filter_popup import FilterPopup
from ..widgets.help_overlay import HelpOverlay
from ..widgets.key_bar import KeyBar
from ..widgets.session_list import SessionBlock, StageHeader
from ....state import StateManager, TraceStatus
from ....workflow import VISIBLE_STAGE_ORDER, resolve_visible_stage, stage_label

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inbox-local widgets
# ---------------------------------------------------------------------------


class TopBar(Static):
    """Compact 1-line app header with repo context and counts."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__("", *args, markup=True, **kwargs)

    def update_context(
        self, project_name: str, remote: str, counts: dict[str, int]
    ) -> None:
        text = (
            f"[bold white]opentraces[/bold white]  "
            f"[dim]{project_name}[/dim]  "
            f"[#F97316]{remote}[/#F97316]  "
            f"[#EAB308]I:{counts.get('inbox', 0)}[/#EAB308] "
            f"[#F97316]C:{counts.get('committed', 0)}[/#F97316] "
            f"[#22D3EE]P:{counts.get('pushed', 0)}[/#22D3EE] "
            f"[#EF4444]R:{counts.get('rejected', 0)}[/#EF4444]"
        )
        self.update(text)


# ---------------------------------------------------------------------------
# InboxScreen
# ---------------------------------------------------------------------------


class InboxScreen(Screen):
    """Session curation inbox, the default screen."""

    CSS_PATH = "inbox.tcss"

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False, priority=True),
        Binding("k", "cursor_up", "Up", show=False, priority=True),
        Binding("enter", "select_trace", "Open", priority=True),
        Binding("c", "commit", "Commit", priority=True),
        Binding("r", "reject", "Reject", priority=True),
        Binding("d", "discard", "Discard", priority=True),
        Binding("p", "push", "Push", priority=True),
        Binding("slash", "open_filter", "Filter", key_display="/", show=False, priority=True),
        Binding("f", "open_filter_popup", "Filter+", show=False, priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("1", "focus_list", "List", show=False, priority=True),
        Binding("2", "focus_detail", "Detail", show=False, priority=True),
        Binding("question_mark", "toggle_help", "Help", key_display="?"),
        Binding("q", "quit_app", "Quit"),
    ]

    # ── Composition ──────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield HelpOverlay(id="help-overlay")
        with Vertical(id="app-shell"):
            yield TopBar(id="topbar")
            with Horizontal(id="workspace"):
                with Vertical(id="list-panel"):
                    yield FilterInput(id="filter-bar")
                    yield ListView(id="session-list")
                yield Static("", id="trace-detail", markup=True)
            yield KeyBar(id="keybar")

    # ── Filter state ──────────────────────────────────────────────

    _filter_text: str = ""
    _structured_filters: dict[str, str | None] = {}

    # ── Lifecycle ────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.query_one(KeyBar).set_mode("inbox")
        self._reload_traces()
        self.set_focus(self.query_one("#session-list", ListView))

    # ── Properties ───────────────────────────────────────────────

    @property
    def store(self) -> Any:
        return self.app.store  # type: ignore[attr-defined]

    @property
    def state(self) -> StateManager:
        return self.app.state  # type: ignore[attr-defined]

    @property
    def staging_dir(self) -> Path:
        return self.app.staging_dir  # type: ignore[attr-defined]

    @property
    def project_name(self) -> str:
        return self.app.project_name  # type: ignore[attr-defined]

    @property
    def remote_name(self) -> str:
        return self.app.remote_name  # type: ignore[attr-defined]

    # ── Data loading ─────────────────────────────────────────────

    # Max items to render per stage (performance: avoid 7000+ ListView items)
    _MAX_PER_STAGE = 50

    def _reload_traces(self, selected_trace_id: str | None = None) -> None:
        """Reload traces from the store and repopulate the session list."""
        self.store.load()
        session_list = self.query_one("#session-list", ListView)
        session_list.clear()

        # Use the store's pre-built stage index for O(1) stage lookup
        counts = self.store.stage_counts()

        for status in VISIBLE_STAGE_ORDER:
            group = self.store.get_by_stage(status)
            # Apply filters
            if self._filter_text or self._structured_filters:
                group = [t for t in group if self._matches_filters(t, status)]
            if not group:
                continue
            total = len(group)
            capped = group[: self._MAX_PER_STAGE]
            session_list.append(StageHeader(status, total))
            for trace in capped:
                session_list.append(SessionBlock(trace, status))
            if total > self._MAX_PER_STAGE:
                session_list.append(
                    StageHeader(status, 0)  # reuse as "more" indicator
                )

        self._update_topbar(counts)

        if self.store.traces:
            if selected_trace_id is not None and not self._select_trace(selected_trace_id):
                self._move_to_first_session()
            elif selected_trace_id is None:
                self._move_to_first_session()
            selected = self._get_selected_item()
            if selected:
                self._show_detail(selected.trace, selected.trace_status)
        else:
            self._set_empty_state()

    # _stage_counts removed: use store.stage_counts() directly (pre-computed, O(1))

    def _resolve_stage(self, trace: Any) -> str:
        tid = getattr(trace, "trace_id", None) or (
            trace.get("trace_id", "") if isinstance(trace, dict) else ""
        )
        entry = self.state.get_trace(tid)
        return resolve_visible_stage(entry.status) if entry else "inbox"

    def _update_topbar(self, counts: dict[str, int]) -> None:
        self.query_one(TopBar).update_context(
            self.project_name, self.remote_name, counts
        )

    def _set_empty_state(self) -> None:
        detail = self.query_one("#trace-detail", Static)
        detail.update(
            "[bold]No sessions in this inbox[/bold]\n\n"
            "[dim]Run opentraces init in this repo and "
            "finish a connected agent session.[/dim]"
        )

    # ── Selection helpers ────────────────────────────────────────

    def _get_selected_item(self) -> SessionBlock | None:
        session_list = self.query_one("#session-list", ListView)
        children = list(session_list.children)
        idx = session_list.index if session_list.index is not None else 0
        if 0 <= idx < len(children) and isinstance(children[idx], SessionBlock):
            return children[idx]
        # Search forward
        for cursor in range(idx + 1, len(children)):
            if isinstance(children[cursor], SessionBlock):
                session_list.index = cursor
                return children[cursor]
        # Search backward
        for cursor in range(idx - 1, -1, -1):
            if isinstance(children[cursor], SessionBlock):
                session_list.index = cursor
                return children[cursor]
        return None

    def _move_to_first_session(self) -> None:
        session_list = self.query_one("#session-list", ListView)
        for index, child in enumerate(session_list.children):
            if isinstance(child, SessionBlock):
                session_list.index = index
                return

    def _select_trace(self, trace_id: str) -> bool:
        session_list = self.query_one("#session-list", ListView)
        for index, child in enumerate(session_list.children):
            if isinstance(child, SessionBlock) and (getattr(child.trace, "trace_id", "") or (child.trace.get("trace_id", "") if isinstance(child.trace, dict) else "")) == trace_id:
                session_list.index = index
                return True
        return False

    # Selection sync removed for performance. Textual's built-in ListView
    # highlight is sufficient. The old _sync_session_selection iterated ALL
    # children (7000+) on every keypress, causing 2+ seconds per j/k.

    def _move_list_selection(self, delta: int) -> None:
        list_view = self.query_one("#session-list", ListView)
        children = list(list_view.children)
        if not children:
            return
        index = list_view.index if list_view.index is not None else -1
        cursor = index + delta
        while 0 <= cursor < len(children):
            if isinstance(children[cursor], SessionBlock):
                list_view.index = cursor
                return
            cursor += delta

    # ── Detail panel ─────────────────────────────────────────────

    def _show_detail(self, trace: Any, status: str) -> None:
        """Update the right-side detail panel for a trace.

        Accepts both TraceIndexEntry (lightweight) and raw dict.
        """
        detail = self.query_one("#trace-detail", Static)

        # Extract fields from either TraceIndexEntry or dict
        if isinstance(trace, dict):
            task = trace.get("task", {}).get("description") or "No description"
            agent = trace.get("agent", {}).get("name", "unknown")
            model = (trace.get("agent", {}).get("model") or "unknown").split("/")[-1]
            trace_id = trace.get("trace_id", "?")
            total_steps = trace.get("metrics", {}).get("total_steps", 0)
            tokens_in = trace.get("metrics", {}).get("total_input_tokens", 0)
            tokens_out = trace.get("metrics", {}).get("total_output_tokens", 0)
            cost = trace.get("metrics", {}).get("estimated_cost_usd")
            ts = _relative_time(trace.get("timestamp_start"))
            flags_count = len(trace.get("_security_flags", []))
        else:
            task = getattr(trace, "task_description", "No description")
            agent = getattr(trace, "agent_name", "unknown")
            model = (getattr(trace, "agent_model", "unknown") or "unknown").split("/")[-1]
            trace_id = getattr(trace, "trace_id", "?")
            total_steps = getattr(trace, "total_steps", 0)
            tokens_in = getattr(trace, "total_input_tokens", 0)
            tokens_out = getattr(trace, "total_output_tokens", 0)
            cost = getattr(trace, "estimated_cost_usd", None)
            ts = _relative_time(getattr(trace, "timestamp_start", None))
            flags_count = getattr(trace, "security_flags_count", 0)

        stage_color = _stage_color_ansi(status)
        stage_text = stage_label(status).upper()

        lines: list[str] = []
        lines.append(f"[bold]{escape(_single_line(task, 100))}[/bold]")
        lines.append("")
        lines.append(f"[dim]trace[/dim]  {escape(_single_line(trace_id, 80))}")
        lines.append(f"[dim]agent[/dim]  {escape(_single_line(agent, 40))}")
        lines.append(f"[dim]model[/dim]  {escape(_single_line(str(model), 60))}")
        lines.append(
            f"[dim]steps[/dim]  {total_steps}  "
            f"[dim]tokens[/dim] {tokens_in}/{tokens_out}"
        )
        if cost is not None:
            lines.append(f"[dim]cost[/dim]   ${cost:.4f}  [dim]{ts or ''}[/dim]")
        elif ts:
            lines.append(f"[dim]time[/dim]   {ts}")
        lines.append("")
        lines.append(
            f"[{stage_color}]{stage_text}[/{stage_color}]  "
            f"[dim]flags[/dim] {flags_count}"
        )
        lines.append("")
        lines.append("[dim]Enter to inspect[/dim]")
        detail.update("\n".join(lines))

    # ── Actions ──────────────────────────────────────────────────

    def action_cursor_down(self) -> None:
        self._move_list_selection(1)

    def action_cursor_up(self) -> None:
        self._move_list_selection(-1)

    def action_select_trace(self) -> None:
        """Open the inspect/replay screen for the selected trace."""
        item = self._get_selected_item()
        if item is None:
            return
        from .trace import TraceScreen

        # Load full trace (with steps) for replay
        trace_id = getattr(item.trace, "trace_id", "") or (
            item.trace.get("trace_id", "") if isinstance(item.trace, dict) else ""
        )
        full_trace = self.store.get_full_trace(trace_id) if trace_id else None
        if full_trace is None:
            # Fallback: use whatever we have
            full_trace = item.trace if isinstance(item.trace, dict) else {"trace_id": trace_id}
        self.app.push_screen(TraceScreen(full_trace))

    def action_commit(self) -> None:
        item = self._get_selected_item()
        if item is None:
            return
        trace = item.trace
        trace_id = getattr(trace, "trace_id", None) or trace.get("trace_id") if isinstance(trace, dict) else getattr(trace, "trace_id", "")
        entry = self.state.get_trace(trace_id)
        current_stage = resolve_visible_stage(entry.status if entry else None)
        if current_stage != "inbox":
            self.notify("Only inbox sessions can be committed", severity="warning")
            return
        task_desc = getattr(trace, "task_description", None) or (
            trace.get("task", {}).get("description") if isinstance(trace, dict) else "trace"
        ) or "trace"
        task = _truncate(task_desc, 60)
        self.state.create_commit_group([trace_id], task)
        self.store.mark_dirty()
        self.post_message(RefreshRequested(select_trace_id=trace_id))
        self.post_message(FlashMessage("[bold #22C55E]\u2713[/bold #22C55E] Committed"))

    def action_reject(self) -> None:
        item = self._get_selected_item()
        if item is None:
            return
        trace_id = getattr(item.trace, "trace_id", "") or (item.trace.get("trace_id", "") if isinstance(item.trace, dict) else "")
        self.state.set_trace_status(trace_id, TraceStatus.REJECTED, session_id=trace_id)
        self.store.mark_dirty()
        self.post_message(RefreshRequested(select_trace_id=trace_id))
        self.post_message(FlashMessage("[bold #EF4444]\u2717[/bold #EF4444] Rejected"))

    def action_discard(self) -> None:
        item = self._get_selected_item()
        if item is None:
            return
        trace_id = getattr(item.trace, "trace_id", "") or (item.trace.get("trace_id", "") if isinstance(item.trace, dict) else "")

        # Delete staging file
        staging_file = self.staging_dir / f"{trace_id}.jsonl"
        if staging_file.exists():
            try:
                staging_file.unlink()
            except OSError:
                logger.warning(
                    "Failed to delete staging file %s", staging_file, exc_info=True
                )

        # Remove from state
        self.state.delete_trace(trace_id)

        self.store.mark_dirty()
        self.post_message(RefreshRequested())
        self.post_message(FlashMessage("[bold #EAB308]\u2716[/bold #EAB308] Discarded"))

    def action_push(self) -> None:
        self.notify(
            "Run 'opentraces push' to publish committed traces",
            severity="information",
        )

    def action_toggle_help(self) -> None:
        self.query_one(HelpOverlay).toggle()

    def action_back(self) -> None:
        help_overlay = self.query_one(HelpOverlay)
        if help_overlay.display:
            help_overlay.toggle()
            return

    def action_focus_list(self) -> None:
        self.set_focus(self.query_one("#session-list", ListView))

    def action_focus_detail(self) -> None:
        self.set_focus(self.query_one("#trace-detail", Static))

    def action_open_filter(self) -> None:
        """Open the inline text filter bar."""
        self.query_one("#filter-bar", FilterInput).open()

    def action_open_filter_popup(self) -> None:
        """Open the structured filter popup."""
        traces = self.store.traces
        stages = sorted({self._resolve_stage(t) for t in traces})
        agents = sorted({
            getattr(t, "agent_name", None) or (t.get("agent", {}).get("name", "unknown") if isinstance(t, dict) else "unknown")
            for t in traces
        })
        models = sorted({
            (getattr(t, "agent_model", None) or (t.get("agent", {}).get("model") if isinstance(t, dict) else None) or "unknown").split("/")[-1]
            for t in traces
        })
        self.app.push_screen(
            FilterPopup(stages, agents, models, self._structured_filters),
            self._on_filter_popup_result,
        )

    def _on_filter_popup_result(self, result: dict[str, str | None] | None) -> None:
        if result is None:
            return
        self._structured_filters = result
        self._reload_traces()

    def _matches_filters(self, trace: Any, stage: str) -> bool:
        """Check if a trace matches current text and structured filters."""
        # Extract fields from either TraceIndexEntry or dict
        if isinstance(trace, dict):
            task = (trace.get("task", {}).get("description") or "").lower()
            agent = (trace.get("agent", {}).get("name") or "").lower()
            trace_id = (trace.get("trace_id") or "").lower()
            agent_name = trace.get("agent", {}).get("name", "")
            model = (trace.get("agent", {}).get("model") or "unknown").split("/")[-1]
        else:
            task = (getattr(trace, "task_description", "") or "").lower()
            agent = (getattr(trace, "agent_name", "") or "").lower()
            trace_id = (getattr(trace, "trace_id", "") or "").lower()
            agent_name = getattr(trace, "agent_name", "")
            model = (getattr(trace, "agent_model", None) or "unknown").split("/")[-1]

        # Text filter
        if self._filter_text:
            query = self._filter_text.lower()
            if query not in task and query not in agent and query not in trace_id:
                return False
        # Structured filters
        if self._structured_filters:
            sf = self._structured_filters
            if sf.get("stage") and stage != sf["stage"]:
                return False
            if sf.get("agent") and agent_name != sf["agent"]:
                return False
            if sf.get("model") and model != sf["model"]:
                return False
        return True

    def action_quit_app(self) -> None:
        self.app.exit()

    # ── Events ───────────────────────────────────────────────────

    @on(ListView.Highlighted, "#session-list")
    def on_session_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if isinstance(item, SessionBlock):
            self._show_detail(item.trace, item.trace_status)

    @on(ListView.Selected, "#session-list")
    def on_session_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, SessionBlock):
            self.action_select_trace()

    def on_refresh_requested(self, message: RefreshRequested) -> None:
        self._reload_traces(selected_trace_id=message.select_trace_id)

    def on_filter_changed(self, message: FilterChanged) -> None:
        self._filter_text = message.value
        self._reload_traces()

    def on_filter_closed(self, message: FilterClosed) -> None:
        self.set_focus(self.query_one("#session-list", ListView))

    def on_flash_message(self, message: FlashMessage) -> None:
        self.query_one(KeyBar).flash(message.text, message.duration)
