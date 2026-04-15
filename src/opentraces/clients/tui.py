"""Textual TUI for the OpenTraces repo inbox.

Two-column layout:

    ┌─────────────────────────────┬────────────────────────────┐
    │ [1] Info   project → remote │ [5] Trace header (summary) │
    │ [2] Inbox    list N/M       │ [6] Trace stream           │
    │ [3] Staged   list N/M       │     (conversation / full)  │
    │ [4] Pushed   list N/M       │                            │
    └─────────────────────────────┴────────────────────────────┘

Space moves inbox ↔ staged. `p` opens the push modal (LLM review or
ignore). The trace stream renders a flattened conversation view ported
from the ``traces-audit`` reference TUI.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import ListItem, ListView, RichLog, Static

from ..core.config import (
    get_project_state_path,
    get_project_traces_dir,
    load_project_config,
    project_is_opted_in,
)
from ..core.inbox import get_stage, load_traces
from ..core.review import (
    commit_single,
    discard_trace_state_only,
    reject_trace,
    unstage_trace,
)
from ..core.state import StateManager, TraceStatus
from ..core.workflow import OPENTRACES_ASCII
from .tui_transforms import conversation_view, full_view

logger = logging.getLogger(__name__)

# Accent blue — used for the remote name in the info panel and the key
# letters in the bottom keybar. ANSI ``bright_blue`` is too theme-dependent
# (Textual's ``textual-ansi`` theme was swallowing it to default fg); a
# truecolor hex bypasses the terminal palette and renders as an
# unambiguous blue regardless of terminal theme.
BLUE_ACCENT = "#60a5fa"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def escape(text: str) -> str:
    return text.replace("[", "\\[")


def _relative_time(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        secs = int((datetime.now(timezone.utc) - dt).total_seconds())
        if secs < 0:
            return str(ts)[:16]
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return str(ts)[:16]


def _format_started(ts: str | None) -> str:
    """Render a trace start timestamp as e.g. ``Apr 15 · 2:32 PM``.

    Falls back to the raw ISO prefix when the value doesn't parse.
    """
    if not ts:
        return "unknown"
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return str(ts)[:19]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()
    hour = local.hour % 12 or 12
    return f"{local.strftime('%b')} {local.day} · {hour}:{local.strftime('%M %p')}"


def _truncate(text: str, limit: int) -> str:
    compact = " ".join(str(text).replace("\n", " ").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


def _session_label(trace: dict[str, Any], cap: int = 200) -> str:
    return (trace.get("task", {}).get("description") or "No description")[:cap]


def _short_id(trace_id: str) -> str:
    # Match graph renderer style: first 8 chars after any prefix.
    tid = trace_id.split("_")[-1] if "_" in trace_id else trace_id
    return tid[:8]


def _tool_color(tool_name: str) -> str:
    if tool_name in {"Read", "Edit", "Write", "Grep", "Glob", "Bash"}:
        return "green"
    if tool_name in {"WebSearch", "WebFetch", "ToolSearch"}:
        return "yellow"
    if tool_name == "Agent":
        return "cyan"
    if tool_name == "AskUserQuestion":
        return "bright_blue"
    if tool_name == "Skill":
        return "magenta"
    return "bright_black"


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class TraceRow(ListItem):
    """A single trace row: short id · task · relative time."""

    def __init__(self, trace: dict[str, Any], width_hint: int = 46) -> None:
        super().__init__()
        self.trace = trace
        self.width_hint = width_hint

    def compose(self) -> ComposeResult:
        task = _truncate(_session_label(self.trace), max(12, self.width_hint - 20))
        ts = _relative_time(self.trace.get("timestamp_end") or self.trace.get("timestamp_start"))
        sid = _short_id(self.trace["trace_id"])
        flags = len(self.trace.get("_security_flags", []))
        flag_tag = f" [red]{flags}f[/red]" if flags else ""
        yield Static(
            f"[dim]{sid}[/dim]  {escape(task)}{flag_tag}  [dim]{ts}[/dim]",
            markup=True,
            classes="trace-row",
        )


class FocusableLog(RichLog, can_focus=True):
    pass


class HelpOverlay(Vertical):
    """Full-screen overlay that centers a help card.

    Implementation note: ``align: center middle`` only positions a
    container's *children*, not the container itself within its parent.
    So the overlay fills the screen on the ``overlay`` layer and the
    actual help card lives as its child — that's what gets centered.
    """

    HELP = (
        "[bold]Keybindings[/bold]\n\n"
        "  [bold]1 2 3 4[/bold]   Focus Info / Inbox / Staged / Pushed\n"
        "  [bold]5[/bold]         Focus trace stream\n"
        "  [bold]j k[/bold] or [bold]↑ ↓[/bold]   Navigate\n"
        "  [bold]enter[/bold]     Inspect (focus stream)\n"
        "  [bold]space[/bold]     Add (inbox→staged) / Remove (staged→inbox)\n"
        "  [bold]a[/bold]         Toggle conversation / full view\n"
        "  [bold]g G[/bold]       Jump to top / bottom of trace preview\n"
        "  [bold]\\[ \\][/bold]       Page trace preview up / down (works from any pane)\n"
        "  [bold]p[/bold]         Push staged traces\n"
        "  [bold]r[/bold]         Reject trace (deferred — undo with u)\n"
        "  [bold]d[/bold]         Discard trace (deferred — undo with u)\n"
        "  [bold]u[/bold]         Undo last reject / discard / stage move\n"
        "  [bold]?[/bold]         Toggle this help\n"
        "  [bold]q[/bold]         Quit (flushes pending discards)\n"
    )

    def __init__(self) -> None:
        super().__init__(id="help-overlay")

    def compose(self) -> ComposeResult:
        yield Static(self.HELP, markup=True, id="help-card")

    def on_mount(self) -> None:
        self.styles.display = "none"

    def toggle(self) -> None:
        self.styles.display = "block" if self.styles.display == "none" else "none"


class PushModal(ModalScreen[str | None]):
    """Prompt the user for push mode: LLM review, ignore, or cancel."""

    BINDINGS = (
        Binding("l", "choose('llm')", "LLM review then push"),
        Binding("i", "choose('ignore')", "Ignore and push"),
        Binding("escape", "choose('cancel')", "Cancel"),
    )

    def __init__(self, remote: str | None) -> None:
        super().__init__()
        self.remote = remote

    def compose(self) -> ComposeResult:
        remote_line = self.remote or "no remote set"
        yield Vertical(
            Static("[bold]Push staged traces[/bold]", id="push-title"),
            Static(f"[dim]remote[/dim]  [bright_blue]{remote_line}[/bright_blue]"),
            Static(""),
            Static("[bold]L[/bold]  LLM review then push  [dim]opentraces push --llm-review[/dim]"),
            Static("[bold]I[/bold]  Ignore and push        [dim]opentraces push[/dim]"),
            Static(""),
            Static("[dim]Esc to cancel[/dim]"),
            id="push-modal-body",
        )

    def action_choose(self, choice: str) -> None:
        self.dismiss(None if choice == "cancel" else choice)


class PushRunnerModal(ModalScreen[None]):
    """Runs `opentraces push` in a worker and streams output."""

    BINDINGS = (Binding("escape", "dismiss_if_done", "Close"),)

    def __init__(self, mode: str, project_dir: Path) -> None:
        super().__init__()
        self.mode = mode
        self.project_dir = project_dir
        self._done = False

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(
                f"[bold]Running opentraces push"
                + (" --llm-review" if self.mode == "llm" else "")
                + "[/bold]",
                id="push-runner-title",
            ),
            FocusableLog(id="push-runner-log", markup=False, wrap=True, highlight=False),
            Static("[dim]Esc to close once finished[/dim]", id="push-runner-hint"),
            id="push-runner-body",
        )

    def on_mount(self) -> None:
        self.run_push()

    @work(thread=True, exclusive=True)
    def run_push(self) -> None:
        log = self.query_one("#push-runner-log", RichLog)
        # Resolve the ``opentraces`` console script next to the active
        # interpreter — works in any venv layout (editable install, pipx,
        # deployed wheel) without depending on PATH or shell aliases like
        # ``ot`` or ``otd``. ``python -m opentraces.cli`` doesn't work
        # because the cli module is a package without a ``__main__``.
        script = Path(sys.executable).parent / "opentraces"
        if not script.exists():
            self.app.call_from_thread(
                log.write,
                f"[error] could not find 'opentraces' next to {sys.executable}",
            )
            self._done = True
            return
        cmd = [str(script), "push"]
        if self.mode == "llm":
            cmd.append("--llm-review")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.project_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            self.app.call_from_thread(log.write, f"[error] {exc}")
            self._done = True
            return

        assert proc.stdout is not None
        for line in proc.stdout:
            self.app.call_from_thread(log.write, line.rstrip())
        rc = proc.wait()
        self.app.call_from_thread(log.write, f"\n[exit {rc}]")
        self._done = True

    def action_dismiss_if_done(self) -> None:
        if self._done:
            self.dismiss(None)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


CSS = """
Screen {
    background: ansi_default;
    color: ansi_default;
    layers: base overlay;
}

#app-shell { height: 100%; }

#workspace { height: 1fr; padding: 0 1; }

.panel {
    background: ansi_default;
    border: round ansi_bright_black;
    color: ansi_default;
}
.panel:focus-within { border: round ansi_bright_blue; }

#left-col { width: 52; min-width: 44; margin-right: 1; height: 100%; }
#right-col { width: 1fr; height: 100%; }

#info-panel { height: 4; margin-bottom: 1; padding: 0 1; }
#info-body { padding: 0 0; color: ansi_default; }

.stage-panel { height: 1fr; margin-bottom: 1; }
.stage-panel > ListView {
    height: 1fr;
    background: ansi_default;
    scrollbar-size-vertical: 1;
    scrollbar-background: ansi_default;
    scrollbar-color: ansi_bright_black;
}
.stage-panel > ListView > ListItem {
    padding: 0 1;
    height: 1;
    background: ansi_default;
    color: ansi_default;
}
/* Persistent edge-to-edge highlight for the active row in every stage list,
   on AND off focus. Focus adds a brighter fill so the active pane still
   stands out. */
/* Persistent highlight on the active row. Blurred = subtle grey block so you
   can still tell which trace is active when focus is on another pane.
   Focused = bright cyan block with black text (keeps dim markup readable;
   plain blue background crushed the dim foreground). */
.stage-panel > ListView > ListItem.-highlight,
.stage-panel > ListView > ListItem.-selected {
    background: ansi_bright_black;
    color: ansi_bright_white;
}
.stage-panel > ListView:focus > ListItem.-highlight,
.stage-panel > ListView:focus > ListItem.-selected {
    background: ansi_bright_cyan;
    color: ansi_black;
    text-style: bold;
}
.stage-panel-empty { padding: 1 2; color: ansi_bright_black; }

#trace-panel {
    height: 1fr;
    padding: 0 1;
    /* Preview stays readable above 40 cols — below that, the workspace
       parent will clip rather than letting wrap collapse to 1 char/line. */
    min-width: 40;
}
#trace-stream {
    height: 1fr;
    background: ansi_default;
    /* Soft-wrap prose to the pane width; suppress the horizontal scrollbar
       so the reader never has to pan sideways for a long line. */
    overflow-x: hidden;
    scrollbar-size-vertical: 1;
    scrollbar-size-horizontal: 0;
    scrollbar-background: ansi_default;
    scrollbar-color: ansi_bright_black;
}

#keybar {
    height: 1;
    padding: 0 2;
    color: ansi_bright_black;
}

/* Full-screen translucent layer; the help-card child is what gets centered. */
HelpOverlay {
    layer: overlay;
    width: 100%;
    height: 100%;
    align: center middle;
    background: ansi_default;
}
#help-card {
    width: 78;
    height: auto;
    max-height: 28;
    background: ansi_default;
    border: round ansi_bright_blue;
    padding: 1 2;
}

PushModal {
    align: center middle;
}
#push-modal-body {
    width: 60;
    height: auto;
    background: ansi_default;
    border: round ansi_bright_blue;
    padding: 1 2;
}

PushRunnerModal {
    align: center middle;
}
#push-runner-body {
    width: 90%;
    height: 80%;
    background: ansi_default;
    border: round ansi_bright_blue;
    padding: 1 2;
}
#push-runner-log {
    height: 1fr;
    background: ansi_default;
    scrollbar-color: ansi_bright_black;
}

.trace-row { height: 1; }
"""


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------


@dataclass
class _UndoOp:
    """One reversible action on the undo stack.

    ``kind``        one of ``"stage"`` / ``"unstage"`` / ``"reject"`` /
                    ``"discard"``. Drives the undo branch.
    ``trace_id``    target trace.
    ``label``       short human label for the keybar / notify toast.
    ``prior_status``the ``TraceStatus`` to restore. ``STAGED`` doubles as
                    "no entry / inbox" since ``resolve_visible_stage``
                    maps it back to inbox.
    """

    kind: str
    trace_id: str
    label: str
    prior_status: TraceStatus


STAGE_KEYS = ("inbox", "staged", "pushed")
STAGE_TITLES = {"inbox": "[2] Inbox", "staged": "[3] Staged", "pushed": "[4] Pushed"}
STAGE_IDS = {"inbox": "inbox-list", "staged": "staged-list", "pushed": "pushed-list"}
STAGE_PANEL_IDS = {"inbox": "inbox-panel", "staged": "staged-panel", "pushed": "pushed-panel"}


class OpenTracesApp(App):
    TITLE = "opentraces"
    CSS = CSS
    AUTO_FOCUS = "#inbox-list"

    BINDINGS = (
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "toggle_help", "Help", key_display="?"),
        Binding("1", "focus_stage('info')", "Info", show=False),
        Binding("2", "focus_stage('inbox')", "Inbox", show=False),
        Binding("3", "focus_stage('staged')", "Staged", show=False),
        Binding("4", "focus_stage('pushed')", "Pushed", show=False),
        Binding("5", "focus_stream", "Stream", show=False),
        Binding("tab", "cycle_focus", "Cycle", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("space", "toggle_stage", "Add/Remove", priority=True),
        Binding("p", "push", "Push", priority=True),
        Binding("r", "reject", "Reject", priority=True),
        Binding("d", "discard", "Discard", priority=True),
        Binding("a", "toggle_view_mode", "Toggle view", priority=True),
        Binding("u", "undo", "Undo", priority=True),
        Binding("g", "scroll_home", "Top", show=False),
        Binding("G", "scroll_end", "Bottom", show=False),
        # Page through the trace preview from any pane — saves you having
        # to leave the inbox to skim a long trace.
        Binding("left_square_bracket", "preview_page_up", "Page up", show=False),
        Binding("right_square_bracket", "preview_page_down", "Page down", show=False),
        Binding("enter", "focus_stream", "Inspect", show=False),
    )

    def __init__(self, staging_dir: Path, *, limit: int | None = 500) -> None:
        super().__init__(ansi_color=True)
        self.theme = "textual-ansi"
        self.staging_dir = staging_dir
        self.project_dir = Path.cwd()
        state_path = get_project_state_path(self.project_dir)
        self.state = StateManager(state_path=state_path)
        self.trace_limit = limit
        self.traces: list[dict[str, Any]] = []
        self.by_stage: dict[str, list[dict[str, Any]]] = {k: [] for k in STAGE_KEYS}
        self.project_name = self.project_dir.name
        self.remote_name: str | None = None
        self.remote_visibility: str | None = None
        self._view_mode = "conversation"  # or "full"
        self._current_trace: dict[str, Any] | None = None
        self._active_stage: str | None = None
        # Undo / deferred-destruction model:
        #   - reject and stage-toggle apply state changes immediately and
        #     push an inverse op onto _undo_stack
        #   - discard is fully deferred — the trace is hidden from the
        #     view and its file path queued in _pending_deletes; the
        #     actual file removal happens on quit. Undo just removes
        #     it from the queue.
        # All in-memory: anything still pending when the app exits
        # without a clean quit (e.g. crash) is forgotten.
        self._undo_stack: list[_UndoOp] = []
        self._pending_deletes: dict[str, Path] = {}

    # --- compose -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield HelpOverlay()
        with Vertical(id="app-shell"):
            with Horizontal(id="workspace"):
                with Vertical(id="left-col"):
                    info = Vertical(id="info-panel", classes="panel")
                    info.border_title = "[1] Info"
                    with info:
                        yield Static("", id="info-body", markup=True)
                    for stage in STAGE_KEYS:
                        panel = Vertical(id=STAGE_PANEL_IDS[stage], classes="panel stage-panel")
                        panel.border_title = STAGE_TITLES[stage]
                        with panel:
                            yield ListView(id=STAGE_IDS[stage])
                with Vertical(id="right-col"):
                    trace_panel = Vertical(id="trace-panel", classes="panel")
                    trace_panel.border_title = "[5] Trace Preview"
                    with trace_panel:
                        yield FocusableLog(id="trace-stream", markup=True, wrap=True, highlight=False)
            yield Static(self._keybar_text(), id="keybar", markup=True)

    def on_mount(self) -> None:
        self._load_project_context()
        self._reload_traces()

    # --- data ----------------------------------------------------------

    def _load_project_context(self) -> None:
        try:
            cfg = load_project_config(self.project_dir)
            self.remote_name = cfg.get("remote")
            self.remote_visibility = cfg.get("visibility")
        except Exception:
            self.remote_name = None
            self.remote_visibility = None

    def _reload_traces(self, keep_trace_id: str | None = None) -> None:
        self.traces = [
            t for t in load_traces(self.staging_dir, limit=self.trace_limit)
            if t["trace_id"] not in self._pending_deletes
        ]
        grouped: dict[str, list[dict[str, Any]]] = {k: [] for k in STAGE_KEYS}
        for t in self.traces:
            stage = get_stage(self.state, t["trace_id"])
            if stage in grouped:
                grouped[stage].append(t)
        for k in grouped:
            grouped[k].sort(
                key=lambda tr: tr.get("timestamp_end") or tr.get("timestamp_start") or "",
                reverse=True,
            )
        self.by_stage = grouped

        self._refresh_info_panel()
        for stage in STAGE_KEYS:
            self._refresh_stage_list(stage)

        current = self._find_trace(keep_trace_id) if keep_trace_id else self._first_trace()
        if current:
            self._select_trace(current)
        else:
            self._render_empty_detail()

    def _refresh_info_panel(self) -> None:
        if self.remote_name:
            vis = (self.remote_visibility or "").lower()
            badge = f"  [bright_black]({escape(vis)})[/bright_black]" if vis else ""
            remote_line = (
                f"[bright_black]→[/bright_black] "
                f"[{BLUE_ACCENT}]{escape(self.remote_name)}[/{BLUE_ACCENT}]{badge}"
            )
        else:
            remote_line = "[bright_black]→[/bright_black] [red]no remote[/red]"
        # Two lines — project on top, remote on bottom — so the remote stays
        # visible even when the project folder name is long. Both lines are
        # blue so the info pane reads as one unit.
        self.query_one("#info-body", Static).update(
            f"{escape(self.project_name)}\n"
            f"{remote_line}"
        )
        self.query_one("#info-panel", Vertical).border_subtitle = None

    def _refresh_stage_list(self, stage: str) -> None:
        list_view = self.query_one(f"#{STAGE_IDS[stage]}", ListView)
        list_view.clear()
        items = self.by_stage[stage]
        if stage == "pushed" and not self.remote_name:
            # Keep pushed empty and show instruction in subtitle instead.
            pass
        width_hint = max(36, list_view.size.width - 4 or 44)
        for t in items:
            list_view.append(TraceRow(t, width_hint=width_hint))

        panel = self.query_one(f"#{STAGE_PANEL_IDS[stage]}", Vertical)
        panel.border_subtitle_align = "right"
        if not items:
            if stage == "pushed" and not self.remote_name:
                panel.border_subtitle = "set a remote to push"
            else:
                panel.border_subtitle = "0 / 0"
        else:
            idx = list_view.index if list_view.index is not None else 0
            panel.border_subtitle = f"{idx + 1} / {len(items)}"

    def _update_panel_counter(self, stage: str) -> None:
        list_view = self.query_one(f"#{STAGE_IDS[stage]}", ListView)
        panel = self.query_one(f"#{STAGE_PANEL_IDS[stage]}", Vertical)
        items = self.by_stage[stage]
        if not items:
            if stage == "pushed" and not self.remote_name:
                panel.border_subtitle = "set a remote to push"
            else:
                panel.border_subtitle = "0 / 0"
            return
        # The non-active lists show "— / N" — only the active stage carries a
        # numbered cursor, matching the single-selection model.
        if list_view.index is None:
            panel.border_subtitle = f"— / {len(items)}"
        else:
            panel.border_subtitle = f"{list_view.index + 1} / {len(items)}"

    def _set_active_stage(self, stage: str) -> None:
        """Make ``stage`` the single active list. Clears highlights on the
        other two lists so only one trace in the left column is ever marked
        as the preview source."""
        if stage not in STAGE_KEYS:
            return
        self._active_stage = stage
        for s in STAGE_KEYS:
            if s == stage:
                continue
            lv = self.query_one(f"#{STAGE_IDS[s]}", ListView)
            if lv.index is not None:
                lv.index = None
            self._update_panel_counter(s)

    # --- selection helpers --------------------------------------------

    def _first_trace(self) -> dict[str, Any] | None:
        for stage in STAGE_KEYS:
            if self.by_stage[stage]:
                return self.by_stage[stage][0]
        return None

    def _find_trace(self, trace_id: str | None) -> dict[str, Any] | None:
        if not trace_id:
            return None
        for t in self.traces:
            if t.get("trace_id") == trace_id:
                return t
        return None

    def _select_trace(self, trace: dict[str, Any]) -> None:
        self._current_trace = trace
        stage = get_stage(self.state, trace["trace_id"])
        if stage in STAGE_KEYS and self.by_stage[stage]:
            list_view = self.query_one(f"#{STAGE_IDS[stage]}", ListView)
            for i, item in enumerate(list_view.children):
                if isinstance(item, TraceRow) and item.trace["trace_id"] == trace["trace_id"]:
                    list_view.index = i
                    break
            self._set_active_stage(stage)
            self._update_panel_counter(stage)
        self._render_trace(trace)

    def _focused_list_trace(self) -> dict[str, Any] | None:
        focused = self.focused
        if not focused:
            return None
        for stage in STAGE_KEYS:
            lv = self.query_one(f"#{STAGE_IDS[stage]}", ListView)
            if focused is lv:
                idx = lv.index or 0
                children = list(lv.children)
                if 0 <= idx < len(children) and isinstance(children[idx], TraceRow):
                    return children[idx].trace
        return self._current_trace

    def _focused_stage(self) -> str | None:
        focused = self.focused
        for stage in STAGE_KEYS:
            if focused is self.query_one(f"#{STAGE_IDS[stage]}", ListView):
                return stage
        return None

    # --- detail rendering ---------------------------------------------

    # Colors for the merged trace pane. Headers use the same hue as their
    # body so a quick eye-scan tells user (cyan) apart from agent (magenta).
    USER_COLOR = "bright_cyan"
    USER_BODY = "cyan"
    AGENT_COLOR = "bright_magenta"
    AGENT_BODY = "default"
    TOOL_RESULT_COLOR = "bright_black"
    ERROR_COLOR = "bright_red"

    def _render_empty_detail(self) -> None:
        stream = self.query_one("#trace-stream", RichLog)
        stream.clear()
        stream.write(f"[bold bright_blue]{OPENTRACES_ASCII}[/bold bright_blue]")
        stream.write("")
        stream.write("[dim]No trace selected. This inbox is empty — run opentraces init and finish an agent run.[/dim]")

    def _render_trace(self, trace: dict[str, Any]) -> None:
        stream = self.query_one("#trace-stream", RichLog)
        stream.clear()
        self._write_trace_header(stream, trace)
        stream.write("")
        steps = trace.get("steps", []) or []
        if not steps:
            stream.write("[dim](no steps)[/dim]")
        elif self._view_mode == "conversation":
            self._write_conversation(stream, conversation_view(steps))
        else:
            self._write_full(stream, full_view(steps))
        # Always start a fresh preview at the top of the trace, not the
        # bottom — RichLog.auto_scroll pulls the viewport to the latest
        # write; ``call_after_refresh`` ensures we override that *after*
        # the pending write-driven scroll has applied.
        stream.auto_scroll = False
        self.call_after_refresh(lambda: stream.scroll_home(animate=False))

    def _write_trace_header(self, stream: RichLog, trace: dict[str, Any]) -> None:
        agent = trace.get("agent", {}).get("name", "unknown")
        model = str(trace.get("agent", {}).get("model", "unknown")).split("/")[-1]
        steps = trace.get("steps", [])
        total_steps = trace.get("metrics", {}).get("total_steps", len(steps))
        tool_calls = sum(len(s.get("tool_calls", [])) for s in steps)
        flags = len(trace.get("_security_flags", []))
        tokens_in = trace.get("metrics", {}).get("total_input_tokens", 0) or 0
        tokens_out = trace.get("metrics", {}).get("total_output_tokens", 0) or 0
        cost = trace.get("metrics", {}).get("estimated_cost_usd")
        started = _format_started(trace.get("timestamp_start"))

        cost_str = f"${cost:.2f}" if isinstance(cost, (int, float)) else "—"
        # Flags stay red only when non-zero — one of the few places color
        # carries real signal (a security flag is something to look at).
        flag_str = f"[red]{flags}[/red]" if flags else "0"

        # NBSP ("\u00A0") binds each label to its value so the wrapper never
        # breaks "agent  claude-code" across two lines. Regular double
        # spaces between groups give it room to wrap between fields. Values
        # render in the default foreground, labels in dim — keeping the
        # header neutrally greyscale so cyan/magenta in the body below
        # actually mean "user" and "agent".
        nb = "\u00A0"
        stream.write(
            f"[dim]agent[/dim]{nb}{escape(agent)}  "
            f"[dim]model[/dim]{nb}{escape(model)}  "
            f"[dim]steps[/dim]{nb}{total_steps}  "
            f"[dim]tools[/dim]{nb}{tool_calls}  "
            f"[dim]flags[/dim]{nb}{flag_str}  "
            f"[dim]in[/dim]{nb}{tokens_in:,}  "
            f"[dim]out[/dim]{nb}{tokens_out:,}  "
            f"[dim]cost[/dim]{nb}{cost_str}  "
            f"[dim]started[/dim]{nb}{started}"
        )
        # Separator spans the full pane width so the stats bar reads as an
        # edge-to-edge block instead of a fixed 80-char stripe.
        width = max(40, (stream.size.width or 80) - 1)
        stream.write("[bright_black]" + "─" * width + "[/bright_black]")

    def _write_body(self, stream: RichLog, content: str, color: str) -> None:
        text = content or ""
        for line in text.splitlines() or [""]:
            stream.write(f"[{color}]{escape(line)}[/{color}]")

    def _write_conversation(self, stream: RichLog, items: list[dict[str, Any]]) -> None:
        for it in items:
            if it["type"] == "user":
                stream.write(f"[{self.USER_COLOR} bold]── User ──[/{self.USER_COLOR} bold]")
                self._write_body(stream, it.get("content") or "", self.USER_BODY)
            else:
                hdr = f"[{self.AGENT_COLOR} bold]── Agent ──[/{self.AGENT_COLOR} bold]"
                if it.get("tool_count"):
                    hdr += f"  [dim]{it['tool_count']} tools: {escape(it['tool_summary'])}[/dim]"
                stream.write(hdr)
                self._write_body(stream, it.get("content") or "", self.AGENT_BODY)
            stream.write("")

    def _write_full(self, stream: RichLog, items: list[dict[str, Any]]) -> None:
        for it in items:
            et = it["event_type"]
            body_color = "default"
            if et == "user_message":
                stream.write(f"[{self.USER_COLOR} bold]── User ──[/{self.USER_COLOR} bold]")
                body_color = self.USER_BODY
            elif et == "agent_text":
                stream.write(f"[{self.AGENT_COLOR} bold]── Agent ──[/{self.AGENT_COLOR} bold]")
                body_color = self.AGENT_BODY
            elif et == "tool_call":
                name = it.get("tool_name", "?")
                c = _tool_color(name)
                stream.write(f"[{c} bold]── Tool Call: {escape(name)} ──[/{c} bold]")
                body_color = c
            elif et == "tool_result":
                name = it.get("tool_name") or "result"
                status = it.get("tool_status") or ""
                suffix = f" ({status})" if status else ""
                stream.write(
                    f"[{self.TOOL_RESULT_COLOR} bold]── Tool Result: {escape(name)}{suffix} ──[/{self.TOOL_RESULT_COLOR} bold]"
                )
                body_color = self.TOOL_RESULT_COLOR
            elif et == "error":
                stream.write(f"[{self.ERROR_COLOR} bold]── Error ──[/{self.ERROR_COLOR} bold]")
                body_color = self.ERROR_COLOR
            self._write_body(stream, it.get("content") or "", body_color)
            stream.write("")

    # --- keybar --------------------------------------------------------

    def _keybar_text(self) -> str:
        mode = "conv" if self._view_mode == "conversation" else "full"
        def k(key: str, label: str) -> str:
            # Rich only treats an unclosed ``[`` as the start of a tag;
            # escape that one character so a literal ``[/]`` key label
            # renders as ``[/]`` rather than being eaten as markup.
            safe = key.replace("[", r"\[")
            return f"[{BLUE_ACCENT}]{safe}[/{BLUE_ACCENT}] [dim]{label}[/dim]"
        parts = [
            k("j/k", "move"),
            k("space", "add/remove"),
            k("p", "push"),
            k("r", "reject"),
            k("d", "discard"),
        ]
        if self._undo_stack:
            parts.append(k("u", f"undo ({len(self._undo_stack)})"))
        parts += [
            k("a", f"view:{mode}"),
            k("g/G", "top/bot"),
            k("[/]", "page"),
            k("?", "help"),
            k("q", "quit"),
        ]
        return "  ".join(parts)

    def _refresh_keybar(self) -> None:
        self.query_one("#keybar", Static).update(self._keybar_text())

    # --- actions -------------------------------------------------------

    def action_focus_stage(self, which: str) -> None:
        if which == "info":
            self.set_focus(self.query_one("#info-body", Static))
            return
        if which in STAGE_KEYS:
            lv = self.query_one(f"#{STAGE_IDS[which]}", ListView)
            # Restore a highlight if we cleared it last time focus moved away.
            if lv.index is None and lv.children:
                for i, child in enumerate(lv.children):
                    if isinstance(child, TraceRow):
                        lv.index = i
                        break
            self._set_active_stage(which)
            self.set_focus(lv)

    def action_focus_stream(self) -> None:
        self.set_focus(self.query_one("#trace-stream", RichLog))

    def action_cycle_focus(self) -> None:
        order = ["inbox-list", "staged-list", "pushed-list", "trace-stream"]
        focused_id = self.focused.id if self.focused else None
        try:
            nxt = order[(order.index(focused_id) + 1) % len(order)]
        except ValueError:
            nxt = order[0]
        if nxt == "trace-stream":
            self.set_focus(self.query_one("#trace-stream"))
        else:
            stage = {"inbox-list": "inbox", "staged-list": "staged",
                     "pushed-list": "pushed"}[nxt]
            self.action_focus_stage(stage)

    def action_cursor_down(self) -> None:
        self._move_cursor(1)

    def action_cursor_up(self) -> None:
        self._move_cursor(-1)

    def _move_cursor(self, delta: int) -> None:
        focused = self.focused
        if isinstance(focused, RichLog):
            if delta > 0:
                focused.scroll_down(animate=False)
            else:
                focused.scroll_up(animate=False)
            return
        stage = self._focused_stage()
        if not stage:
            return
        lv = self.query_one(f"#{STAGE_IDS[stage]}", ListView)
        if not lv.children:
            return
        idx = (lv.index or 0) + delta
        idx = max(0, min(len(lv.children) - 1, idx))
        lv.index = idx

    def action_scroll_home(self) -> None:
        self.query_one("#trace-stream", RichLog).scroll_home(animate=False)

    def action_scroll_end(self) -> None:
        self.query_one("#trace-stream", RichLog).scroll_end(animate=False)

    def action_preview_page_up(self) -> None:
        self.query_one("#trace-stream", RichLog).scroll_page_up(animate=False)

    def action_preview_page_down(self) -> None:
        self.query_one("#trace-stream", RichLog).scroll_page_down(animate=False)

    def action_toggle_help(self) -> None:
        self.query_one(HelpOverlay).toggle()

    def action_toggle_view_mode(self) -> None:
        self._view_mode = "full" if self._view_mode == "conversation" else "conversation"
        self._refresh_keybar()
        if self._current_trace:
            self._render_trace(self._current_trace)

    # ---- destructive actions all push an entry onto the undo stack ----

    def _snapshot_status(self, trace_id: str) -> TraceStatus:
        """Return the current ``TraceStatus`` for restore-on-undo.

        ``StateManager.get_trace`` returns a dataclass whose ``status``
        field is typed as ``TraceStatus`` but is actually a bare string
        at runtime (JSON round-trip — dataclasses don't coerce). Coerce
        back to the enum so ``set_trace_status`` — which calls
        ``status.value`` — doesn't blow up on undo.
        """
        entry = self.state.get_trace(trace_id)
        if entry is None:
            return TraceStatus.STAGED
        raw = entry.status
        if isinstance(raw, TraceStatus):
            return raw
        try:
            return TraceStatus(raw)
        except (ValueError, TypeError):
            return TraceStatus.STAGED

    def action_toggle_stage(self) -> None:
        trace = self._focused_list_trace()
        if not trace:
            return
        trace_id = trace["trace_id"]
        stage = get_stage(self.state, trace_id)
        prior = self._snapshot_status(trace_id)
        task_label = (trace.get("task", {}).get("description") or "trace")[:40]
        if stage == "inbox":
            commit_single(self.state, trace_id, task_label)
            self._undo_stack.append(_UndoOp("stage", trace_id,
                                            f"stage '{task_label}'", prior))
            self.notify("Added to staged · u to undo", severity="information")
        elif stage == "staged":
            unstage_trace(self.state, trace_id)
            self._undo_stack.append(_UndoOp("unstage", trace_id,
                                            f"unstage '{task_label}'", prior))
            self.notify("Moved back to inbox · u to undo", severity="information")
        else:
            self.notify("Only inbox/staged traces can be toggled", severity="warning")
            return
        self._reload_traces(keep_trace_id=trace_id)
        self._refresh_keybar()

    def action_reject(self) -> None:
        trace = self._focused_list_trace()
        if not trace:
            return
        trace_id = trace["trace_id"]
        prior = self._snapshot_status(trace_id)
        task_label = (trace.get("task", {}).get("description") or "trace")[:40]
        try:
            reject_trace(self.state, trace_id, with_session_kwarg=True)
        except TypeError:
            reject_trace(self.state, trace_id)
        self._undo_stack.append(_UndoOp("reject", trace_id,
                                        f"reject '{task_label}'", prior))
        self._reload_traces(keep_trace_id=trace_id)
        self._refresh_keybar()
        self.notify("Rejected · u to undo", severity="warning")

    def action_discard(self) -> None:
        """Defer the JSONL deletion until quit. Until then, the trace is
        hidden from the view and ``u`` un-hides it. This is the only
        op that survives only in memory — a crash before quit forgets
        the discard, which is the correct safe-by-default behavior."""
        trace = self._focused_list_trace()
        if not trace:
            return
        trace_id = trace["trace_id"]
        prior = self._snapshot_status(trace_id)
        task_label = (trace.get("task", {}).get("description") or "trace")[:40]
        self._pending_deletes[trace_id] = self.staging_dir / f"{trace_id}.jsonl"
        self._undo_stack.append(_UndoOp("discard", trace_id,
                                        f"discard '{task_label}'", prior))
        self._reload_traces()
        self._refresh_keybar()
        self.notify("Discarded · u to undo (kept until you quit)",
                    severity="warning")

    def _flush_pending_deletes(self) -> int:
        """Apply queued discards. Called from action_quit so the file
        deletions only happen on a clean exit — closing the terminal,
        ``Ctrl-C``, or a crash leaves the staging files in place."""
        flushed = 0
        for trace_id, staging_file in list(self._pending_deletes.items()):
            try:
                discard_trace_state_only(self.state, trace_id,
                                         staging_file=staging_file)
                flushed += 1
            except Exception as exc:  # pragma: no cover — best-effort cleanup
                logger.warning("flush discard failed for %s: %s", trace_id, exc)
        self._pending_deletes.clear()
        return flushed

    def action_quit(self) -> None:
        self._flush_pending_deletes()
        self.exit()

    def action_undo(self) -> None:
        if not self._undo_stack:
            self.notify("Nothing to undo", severity="information")
            return
        op = self._undo_stack.pop()
        if op.kind == "discard":
            self._pending_deletes.pop(op.trace_id, None)
        # Restore the prior on-disk status. STAGED maps back to inbox via
        # resolve_visible_stage so this also handles "no prior entry".
        self.state.set_trace_status(op.trace_id, op.prior_status)
        self._reload_traces(keep_trace_id=op.trace_id)
        self._refresh_keybar()
        self.notify(f"Undid: {op.label}", severity="information")

    def action_push(self) -> None:
        if not self.by_stage["staged"]:
            self.notify("No staged traces to push", severity="warning")
            return

        def after_choice(choice: str | None) -> None:
            if choice is None:
                return
            if not self.remote_name and choice:
                self.notify("No remote set — the push command will prompt you to pick one.",
                            severity="information")

            def after_run(_: None) -> None:
                self._load_project_context()
                self._reload_traces()

            self.push_screen(PushRunnerModal(choice, self.project_dir), after_run)

        self.push_screen(PushModal(self.remote_name), after_choice)

    # --- events --------------------------------------------------------

    _last_stream_width: int = 0

    def on_resize(self, event: events.Resize) -> None:
        """Re-wrap the preview when the pane width changes.

        ``RichLog`` caches rendered strips by width; without an explicit
        re-render the trailing separator bar (which is sized to the widget
        width at write-time) goes stale on resize.
        """
        if not self._current_trace:
            return
        try:
            stream = self.query_one("#trace-stream", RichLog)
        except Exception:
            return
        w = stream.size.width or 0
        if w and w != self._last_stream_width:
            self._last_stream_width = w
            self._render_trace(self._current_trace)

    @on(ListView.Highlighted)
    def on_any_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        source_stage: str | None = None
        for stage, lid in STAGE_IDS.items():
            if event.list_view.id == lid:
                source_stage = stage
                break
        if isinstance(item, TraceRow):
            # Any row highlight on a non-active list promotes that list to
            # active and clears the others — only one row in the left
            # column is highlighted at a time.
            if source_stage and source_stage != self._active_stage:
                self._set_active_stage(source_stage)
            self._current_trace = item.trace
            self._render_trace(item.trace)
        if source_stage:
            self._update_panel_counter(source_stage)

    @on(ListView.Selected)
    def on_any_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, TraceRow):
            self._current_trace = item.trace
            self._render_trace(item.trace)
            self.set_focus(self.query_one("#trace-stream", RichLog))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    cwd = Path.cwd()
    staging_dir = get_project_traces_dir(cwd) if project_is_opted_in(cwd) else cwd

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

    OpenTracesApp(staging_dir=staging_dir).run()


if __name__ == "__main__":
    main()
