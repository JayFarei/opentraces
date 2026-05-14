"""Textual TUI for the OpenTraces repo inbox.

Two-column layout:

    ┌─────────────────────────────┬────────────────────────────┐
    │ [1] Info   project → remote │ [5] Trace header (summary) │
    │ [2] Inbox    list N/M       │ [6] Trace stream           │
    │ [3] Staged   list N/M       │     (conversation / full)  │
    │ [4] Pushed   list N/M       │                            │
    └─────────────────────────────┴────────────────────────────┘

Space moves inbox ↔ staged. `p` opens the dataset publication guidance modal.
The trace stream renders a flattened conversation view ported
from the ``traces-audit`` reference TUI.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.theme import Theme

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import ListItem, ListView, RichLog, Static

from ...core.config import (
    get_project_state_path,
    get_project_traces_dir,
    load_project_config,
    project_is_opted_in,
)
from ...core.inbox import get_stage, load_traces
from ...core.review import (
    commit_single,
    discard_trace_state_only,
    reject_trace,
    unstage_trace,
)
from ...core.state import StateManager, TraceStatus
from ...core.workflow import OPENTRACES_ASCII
from .transforms import conversation_view, full_view

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


def _fmt_tokens(n: int) -> str:
    """Compact human-readable token count: 73, 8.7K, 12K, 1.0M, 73M."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        v = n / 1000
        return f"{v:.1f}K" if v < 10 else f"{int(v)}K"
    v = n / 1_000_000
    return f"{v:.1f}M" if v < 10 else f"{int(v)}M"


def _is_recently_touched(ts_end: str | None, window_seconds: int = 7200) -> bool:
    """True if ``ts_end`` is within the last ``window_seconds`` (default 2h).

    Used as a "recently touched" hint in the inbox — it does NOT claim
    the underlying Claude Code session is still live, only that the last
    observed turn landed recently. See plan B.6 for the intended UX.
    """
    if not ts_end:
        return False
    try:
        dt = datetime.fromisoformat(str(ts_end).replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    return 0 <= secs <= window_seconds


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
    """A single trace row: status dot · short id · task · relative time.

    The leading marker is intentionally tiny, so the help overlay carries
    the legend: red for blocked, yellow for residual findings, cyan half
    dot for "recently touched", dim dot for normal rows. ``↑N`` is the
    generation counter for the session: a newer trace from the same
    session replacing an older one. ``i`` on the row brings up the full
    pipeline breakdown.
    """

    def __init__(
        self, trace: dict[str, Any], *, is_blocked: bool = False,
        width_hint: int = 46,
    ) -> None:
        super().__init__()
        self.trace = trace
        self.is_blocked = is_blocked
        self.width_hint = width_hint

    def compose(self) -> ComposeResult:
        task = _truncate(_session_label(self.trace), max(12, self.width_hint - 22))
        ts = _relative_time(self.trace.get("timestamp_end") or self.trace.get("timestamp_start"))
        sid = _short_id(self.trace["trace_id"])
        flags = len(self.trace.get("_security_flags", []))
        if self.is_blocked:
            dot = "[red]●[/red]"
        elif flags:
            dot = "[yellow]●[/yellow]"
        elif _is_recently_touched(self.trace.get("timestamp_end")):
            dot = "[cyan dim]◐[/cyan dim]"
        else:
            dot = "[dim]·[/dim]"
        gen = self.trace.get("generation_index") or 0
        gen_tag = f"  [cyan dim]↑{gen}[/cyan dim]" if gen else ""
        yield Static(
            f"{dot} [dim]{sid}[/dim]  {escape(task)}  [dim]{ts}[/dim]{gen_tag}",
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
        "[bold]Keybindings[/bold]\n"
        "  [bold]1 2 3 4[/bold]   Focus Info / Inbox / Staged / Pushed\n"
        "  [bold]5[/bold]         Focus trace stream\n"
        "  [bold]j k[/bold] or [bold]↑ ↓[/bold]   Navigate\n"
        "  [bold]enter[/bold]     Inspect (focus stream)\n"
        "  [bold]space[/bold]     Add (inbox→staged) / Remove (staged→inbox)\n"
        "  [bold]a[/bold]         Toggle conversation / full view\n"
        "  [bold]g G[/bold]       Jump to top / bottom of trace preview\n"
        "  [bold]\\[ \\][/bold]       Page trace preview up / down (works from any pane)\n"
        "  [bold]p[/bold]         Push staged traces\n"
        "  [bold]r[/bold]         Refresh (re-capture and reload)\n"
        "  [bold]d[/bold]         Discard trace (deferred — undo with u)\n"
        "  [bold]u[/bold]         Undo last reject / discard / stage move\n"
        "  [bold]i[/bold]         Security pipeline info for the selected trace\n"
        "  [bold]?[/bold]         Toggle this help\n"
        "  [bold]q[/bold]         Quit (flushes pending discards)\n"
        "[bold]Trace Row Legend[/bold]\n"
        "  [dim]·[/dim] normal   [cyan dim]◐[/cyan dim] recently touched (~2h)   "
        "[yellow]●[/yellow] findings need review\n"
        "  [red]●[/red] blocked  [cyan dim]↑N[/cyan dim] same session resumed; "
        "[bold]r[/bold] pulls newer trace; push latest\n"
    )

    def __init__(self) -> None:
        super().__init__(id="help-overlay")

    def compose(self) -> ComposeResult:
        yield Static(self.HELP, markup=True, id="help-card")

    def on_mount(self) -> None:
        self.styles.display = "none"

    def toggle(self) -> None:
        self.styles.display = "block" if self.styles.display == "none" else "none"


class SecurityInfoModal(ModalScreen[None]):
    """Pipeline breakdown for a single trace.

    Answers "what has run on this trace, and what does the TUI still
    expect me to do?" without forcing the user to pop out to the CLI.
    Tier labels mirror the ``opentraces status`` column vocabulary.
    """

    BINDINGS = (Binding("escape", "close", "Close"), Binding("i", "close", "Close"))

    def __init__(
        self, trace: dict[str, Any], visible_stage: str,
        block_reason: str | None = None,
    ) -> None:
        super().__init__()
        self.trace = trace
        self.visible_stage = visible_stage
        self.block_reason = block_reason

    def compose(self) -> ComposeResult:
        yield Vertical(self._build_body(), id="security-modal-body")

    def _build_body(self) -> Static:
        t = self.trace
        stage = self.visible_stage
        meta_all = t.get("metadata") or {}
        sec_meta = meta_all.get("security") or {}
        tier1 = t.get("_security_flags") or []
        # Top-level ``security`` carries the pipeline report written at
        # capture time: scanned flag, flags_reviewed, redactions_applied,
        # classifier_version. Surfacing it is how the user knows the
        # pipeline *did* do something even when no residual flags remain.
        sec_top = t.get("security") or {}
        regex_scanned = bool(sec_top.get("scanned"))
        redactions = int(sec_top.get("redactions_applied") or 0)
        flags_reviewed = int(sec_top.get("flags_reviewed") or 0)
        th_status = (sec_meta.get("tools") or {}).get("trufflehog") or {}
        th_findings = th_status.get("findings")
        lr = meta_all.get("llm_review") or {}
        lr_status = lr.get("status")
        lr_shareable = lr.get("shareable")
        lr_missed = lr.get("missed_sensitive_data")

        def ok_dot() -> str: return "[green]✓[/green]"
        def warn_dot() -> str: return "[yellow]●[/yellow]"
        def bad_dot() -> str: return "[red]●[/red]"
        def pending_dot() -> str: return "[dim]·[/dim]"

        # Tier 1 (regex / entropy). If the scanner auto-redacted any
        # hits, the user cares about the redaction count more than the
        # residual-findings count (which is typically zero once redactions
        # have been applied).
        if tier1:
            regex_line = (f"{warn_dot()} [bold]Regex / entropy[/bold]  "
                          f"{len(tier1)} residual finding(s)")
        elif redactions > 0:
            regex_line = (
                f"{ok_dot()} [bold]Regex / entropy[/bold]  "
                f"[dim]{flags_reviewed} reviewed, {redactions} auto-redacted[/dim]"
            )
        elif regex_scanned:
            regex_line = f"{ok_dot()} [bold]Regex / entropy[/bold]  scanned, no findings"
        else:
            regex_line = f"{pending_dot()} [bold]Regex / entropy[/bold]  not scanned yet"

        # Tier 1.5 (TruffleHog). Prefer the explicit status marker written
        # by the pipeline (records both clean runs and findings). Fall back
        # to the state block_reason when the tool never ran.
        br = (self.block_reason or "").strip()
        th_from_state = br.lower().startswith("trufflehog")
        th_marker_status = th_status.get("status")
        th_version = th_status.get("version") or ""
        if th_findings:
            th_line = (f"{bad_dot()} [bold]TruffleHog[/bold]  "
                       f"{len(th_findings)} finding(s)")
        elif th_marker_status == "clean":
            version_tail = f" [dim]({escape(th_version)})[/dim]" if th_version else ""
            th_line = (f"{ok_dot()} [bold]TruffleHog[/bold]  "
                       f"scanned, no findings{version_tail}")
        elif th_from_state:
            tail = br.split(":", 1)[1].strip() if ":" in br else br
            th_line = (f"{bad_dot()} [bold]TruffleHog[/bold]  "
                       f"[dim]{escape(tail)}[/dim]")
        else:
            th_line = (f"{pending_dot()} [bold]TruffleHog[/bold]  not run  "
                       "[dim](opt-in: opentraces setup trufflehog)[/dim]")

        # Manual review — inferred from visible stage.
        if stage == "inbox":
            manual_line = (f"{pending_dot()} [bold]Manual review[/bold]  pending  "
                           "[dim](press space to add to staged)[/dim]")
        elif stage == "staged":
            manual_line = f"{ok_dot()} [bold]Manual review[/bold]  staged"
        elif stage == "pushed":
            manual_line = f"{ok_dot()} [bold]Manual review[/bold]  pushed"
        else:
            manual_line = f"{pending_dot()} [bold]Manual review[/bold]  —"

        # Tier 2 (LLM review)
        if lr_status == "complete" and lr_shareable == "yes" and lr_missed != "yes":
            model = lr.get("model") or "?"
            lr_line = (f"{ok_dot()} [bold]LLM review[/bold]  "
                       f"shareable  [dim]({escape(model)})[/dim]")
        elif lr_status == "complete":
            lr_line = (f"{bad_dot()} [bold]LLM review[/bold]  blocked  "
                       f"[dim]shareable={lr_shareable}, "
                       f"missed={lr_missed}[/dim]")
        else:
            lr_line = (f"{pending_dot()} [bold]LLM review[/bold]  not run  "
                       "[dim](opt-in: opentraces setup llm-review)[/dim]")

        body = (
            f"[bold]Security pipeline[/bold]\n"
            f"[dim]{escape(_short_id(t['trace_id']))}  "
            f"{escape(_truncate(_session_label(t), 60))}[/dim]\n\n"
            f"  {regex_line}\n"
            f"  {th_line}\n"
            f"  {manual_line}\n"
            f"  {lr_line}\n\n"
            f"[dim]Esc or i to close[/dim]"
        )
        return Static(body, markup=True, id="security-modal-text")

    def action_close(self) -> None:
        self.dismiss(None)


class PushModal(ModalScreen[None]):
    """Explain that trace push moved to dataset publication."""

    BINDINGS = (
        Binding("escape", "dismiss", "Close"),
        Binding("enter", "dismiss", "Close"),
    )

    def __init__(self, remote: str | None, staged_count: int = 0) -> None:
        super().__init__()
        self.remote = remote
        self.staged_count = staged_count

    def compose(self) -> ComposeResult:
        remote_line = self.remote or "no remote set"
        n = self.staged_count
        suffix = "" if n == 1 else "s"
        yield Vertical(
            Static(
                "[bold]Dataset publication replaces trace push[/bold]",
                id="push-title",
            ),
            Static(f"[dim]staged traces[/dim]  [{BLUE_ACCENT}]{n}[/{BLUE_ACCENT}] trace{suffix}"),
            Static(f"[dim]remote[/dim]  [bright_blue]{remote_line}[/bright_blue]"),
            Static(""),
            Static("[bold]Next step[/bold]  create or update a dataset from the bucket."),
            Static("[dim]opentraces dataset run <name>[/dim]"),
            Static("[dim]opentraces dataset review <name>[/dim]"),
            Static("[dim]opentraces dataset publish <name>[/dim]"),
            Static(""),
            Static("[dim]Esc or Enter to close[/dim]"),
            id="push-modal-body",
        )

    def action_dismiss(self) -> None:
        self.dismiss(None)


class RefreshRunnerModal(ModalScreen[None]):
    """Runs a generation-aware inbox refresh and surfaces live counts."""

    BINDINGS = (Binding("escape", "dismiss_if_done", "Close"),)

    def __init__(self, project_dir: Path, pre_snapshot: dict[str, str]) -> None:
        super().__init__()
        self.project_dir = project_dir
        self.pre_snapshot = pre_snapshot
        self._done = False
        self._processed = 0
        self._total = 0
        self._counts: dict[str, int] = {
            "new": 0,
            "refreshed": 0,
            "new_generation": 0,
            "noop": 0,
            "skipped": 0,
            "error": 0,
        }
        self._current_session: str | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[bold]Refreshing inbox from session corpus[/bold]",
                   id="refresh-runner-title"),
            Static("", id="refresh-runner-stats", markup=True),
            FocusableLog(id="refresh-runner-log", markup=False,
                         wrap=True, highlight=False),
            Static(
                "[dim]Running... counts update as each session finishes.[/dim]",
                id="refresh-runner-hint",
            ),
            id="refresh-runner-body",
        )

    def on_mount(self) -> None:
        self._refresh_stats()
        self.run_refresh()

    def _refresh_stats(self) -> None:
        total = self._total
        current = self._current_session or "—"
        body = (
            f"[dim]Sessions[/dim]  {self._processed}/{total}\n"
            f"[dim]New[/dim]  {self._counts['new']}    "
            f"[dim]Refreshed[/dim]  {self._counts['refreshed']}    "
            f"[dim]New gen[/dim]  {self._counts['new_generation']}\n"
            f"[dim]No-op[/dim]  {self._counts['noop']}    "
            f"[dim]Skipped[/dim]  {self._counts['skipped']}    "
            f"[dim]Errors[/dim]  {self._counts['error']}\n"
            f"[dim]Current[/dim]  {escape(current)}"
        )
        self.query_one("#refresh-runner-stats", Static).update(body)

    def _set_total(self, total: int) -> None:
        self._total = total
        self._refresh_stats()

    def _record_result(self, result: Any, done: int, total: int) -> None:
        action = str(getattr(result, "action", "") or "")
        if action in self._counts:
            self._counts[action] += 1
        self._processed = done
        self._total = total
        self._current_session = str(getattr(result, "session_id", "") or "—")
        self._refresh_stats()

        prefix = {
            "new": "+",
            "refreshed": "~",
            "new_generation": "↑",
            "noop": "·",
            "skipped": "-",
            "error": "!",
        }.get(action, "?")
        action_label = action.replace("_", " ") or "unknown"
        tid = getattr(result, "trace_id", None)
        sup = getattr(result, "supersedes", None)
        err = getattr(result, "error", None)
        bits = [
            f"{prefix} {getattr(result, 'session_id', '?')}",
            action_label,
        ]
        if tid:
            bits.append(f"→ {str(tid)[:8]}")
        if sup:
            bits.append(f"supersedes {str(sup)[:8]}")
        if err:
            bits.append(f"({err})")
        self.query_one("#refresh-runner-log", RichLog).write("  ".join(bits))

    def _finish_run(self, report: Any) -> None:
        self._done = True
        self._current_session = None
        self._refresh_stats()
        finish = getattr(self.app, "_finish_refresh", None)
        if callable(finish):
            finish(self.pre_snapshot, report)
        self.query_one("#refresh-runner-hint", Static).update(
            "[dim]Done — Esc to close[/dim]"
        )

    def _finish_with_error(self, message: str) -> None:
        self._done = True
        self._current_session = None
        self._counts["error"] += 1
        self._refresh_stats()
        self.query_one("#refresh-runner-log", RichLog).write(f"! refresh failed  ({message})")
        self.query_one("#refresh-runner-hint", Static).update(
            "[dim]Refresh failed — Esc to close[/dim]"
        )
        self.app.notify("Refresh failed", severity="error")

    @work(thread=True, exclusive=True)
    def run_refresh(self) -> None:
        from ...core import ingest as ingest_core
        from ...core.repo_identity import discover_claude_jsonl_corpus

        try:
            total = len(discover_claude_jsonl_corpus(self.project_dir))
            self.app.call_from_thread(self._set_total, total)

            def on_result(result: Any, done: int, total_count: int) -> None:
                self.app.call_from_thread(
                    self._record_result, result, done, total_count
                )

            report = ingest_core.scan_project(
                self.project_dir,
                on_result=on_result,
            )
        except Exception as exc:
            logger.exception("Refresh scan failed")
            self.app.call_from_thread(self._finish_with_error, str(exc))
            return
        self.app.call_from_thread(self._finish_run, report)

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
    padding: 0 2;
}

PushModal {
    align: center middle;
}

SecurityInfoModal {
    align: center middle;
}
#security-modal-body {
    width: 78;
    height: auto;
    max-height: 24;
    background: ansi_default;
    border: round ansi_bright_blue;
    padding: 1 2;
}
#push-modal-body {
    width: 78;
    height: auto;
    background: ansi_default;
    border: round ansi_bright_blue;
    padding: 2 3;
}

RefreshRunnerModal {
    align: center middle;
}
#refresh-runner-body {
    width: 90%;
    height: 80%;
    background: ansi_default;
    border: round ansi_bright_blue;
    padding: 1 2;
}
#refresh-runner-stats {
    height: 4;
    padding-bottom: 1;
}
#refresh-runner-log {
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
        Binding("r", "refresh", "Refresh", priority=True),
        Binding("d", "discard", "Discard", priority=True),
        Binding("a", "toggle_view_mode", "Toggle view", priority=True),
        Binding("u", "undo", "Undo", priority=True),
        Binding("i", "security_info", "Security info", priority=True),
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
        self._blocked_ids: set[str] = set()

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
        self._blocked_ids: set[str] = set()
        for t in self.traces:
            stage = get_stage(self.state, t["trace_id"])
            # Blocked traces surface in the Inbox with a red dot instead
            # of disappearing — the user still needs to see them, read
            # the reason in the preview, and recover or reject manually.
            if stage == "blocked":
                self._blocked_ids.add(t["trace_id"])
                stage = "inbox"
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
            list_view.append(TraceRow(
                t,
                is_blocked=t["trace_id"] in self._blocked_ids,
                width_hint=width_hint,
            ))

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
        self._write_flag_callout(stream, trace)
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
        metrics = trace.get("metrics", {}) or {}
        total_steps = metrics.get("total_steps", len(steps))
        tool_calls = sum(len(s.get("tool_calls", [])) for s in steps)

        # "flags" previously showed residual tier-1 findings — which drops
        # to 0 when the scanner auto-redacts everything, hiding the fact
        # that the pipeline did work. Now we show redactions_applied
        # (what the pipeline removed) and only flip to red when there are
        # residual findings that still need human review.
        # "flags" = total items the scanner flagged (auto-redacted +
        # still-residual). Red when any are unredacted and still need
        # human review; default color otherwise. We drop the "redacted"
        # / "to review" words — color carries the signal and the bare
        # number keeps the header compact.
        sec_top = trace.get("security") or {}
        residual = len(trace.get("_security_flags") or [])
        redactions = int(sec_top.get("redactions_applied") or 0)
        total_flags = residual + redactions
        if residual:
            flag_str = f"[red]{total_flags}[/red]"
        elif total_flags:
            flag_str = str(total_flags)
        else:
            flag_str = "0"

        # Claude Code pushes almost all context through the prompt cache,
        # so step.input_tokens stays tiny (just new material) while
        # step.cache_read_tokens holds the re-used prompt. The honest
        # "what the model saw" figure is input + cache_read. Sum them.
        new_in = int(metrics.get("total_input_tokens", 0) or 0)
        cache_read = int(metrics.get("total_cache_read_tokens", 0) or 0)
        total_in = new_in + cache_read
        tokens_out = int(metrics.get("total_output_tokens", 0) or 0)
        cost = metrics.get("estimated_cost_usd")
        started = _format_started(trace.get("timestamp_start"))

        cost_str = f"${cost:.2f}" if isinstance(cost, (int, float)) else "—"

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
            f"[dim]in[/dim]{nb}{_fmt_tokens(total_in)}  "
            f"[dim]out[/dim]{nb}{_fmt_tokens(tokens_out)}  "
            f"[dim]cost[/dim]{nb}{cost_str}  "
            f"[dim]started[/dim]{nb}{started}"
        )
        # Separator spans the full pane width so the stats bar reads as an
        # edge-to-edge block instead of a fixed 80-char stripe.
        width = max(40, (stream.size.width or 80) - 1)
        stream.write("[bright_black]" + "─" * width + "[/bright_black]")

    def _block_label(self, reason: str | None) -> tuple[str, str]:
        """Map a raw ``block_reason`` string onto a (tier, detail) pair.

        The security tiers stamp the reason with a leading prefix —
        ``TruffleHog:``, ``llm-review:``, ``regex:`` etc. — so we can
        pick a user-friendly tier name without enumerating every
        possible reason. Unknown prefixes fall back to a generic
        "Blocked" label with the raw reason as the detail.
        """
        raw = (reason or "").strip()
        low = raw.lower()
        if low.startswith("trufflehog"):
            return ("TruffleHog", raw.split(":", 1)[1].strip() if ":" in raw else raw)
        if low.startswith("llm-review") or low.startswith("llm review"):
            return ("LLM review", raw.split(":", 1)[1].strip() if ":" in raw else raw)
        if low.startswith("regex"):
            return ("regex / entropy", raw.split(":", 1)[1].strip() if ":" in raw else raw)
        return ("", raw)

    def _write_flag_callout(self, stream: RichLog, trace: dict[str, Any]) -> None:
        """Leading callout that explains *why* a trace is blocked / flagged.

        Resolves the block reason from the StateManager entry (its
        ``block_reason`` is the ground truth — any tier can write
        there). Falls back to LLM-review metadata for the "not-yet
        blocked but flagged" case where the verdict exists on the
        trace itself.
        """
        trace_id = trace["trace_id"]
        is_blocked = trace_id in self._blocked_ids
        entry = self.state.get_trace(trace_id) if is_blocked else None
        block_reason = getattr(entry, "block_reason", None) if entry else None
        meta = (trace.get("metadata") or {}).get("llm_review") or {}
        tier1 = trace.get("_security_flags") or []
        wrote = False
        if is_blocked:
            tier, detail = self._block_label(block_reason)
            tier_txt = f" by {tier}" if tier else ""
            detail_txt = (
                f"  [dim]{escape(_truncate(detail, 120))}[/dim]" if detail else ""
            )
            stream.write(f"[red]● Blocked{tier_txt}[/red]{detail_txt}")
            wrote = True
        elif meta.get("shareable") == "no" \
                or meta.get("missed_sensitive_data") == "yes":
            summary = meta.get("summary") or "flagged by LLM review"
            flagged = meta.get("flagged_parts") or []
            extra = ""
            if flagged:
                first = flagged[0]
                snippet = first.get("text") or first.get("reason") or ""
                if snippet:
                    extra = f" — [dim]{escape(_truncate(snippet, 80))}[/dim]"
            stream.write(
                f"[red]● Flagged by LLM review[/red]  "
                f"{escape(_truncate(summary, 120))}{extra}"
            )
            wrote = True
        if tier1 and not is_blocked:
            kinds = sorted({f.get("type", "") for f in tier1 if f.get("type")})
            kinds_txt = ", ".join(kinds[:4]) if kinds else "findings"
            stream.write(
                f"[yellow]● Tier 1 flagged[/yellow]  "
                f"{len(tier1)} finding(s) · [dim]{escape(kinds_txt)}[/dim]"
            )
            wrote = True
        if wrote:
            stream.write("[dim]i — show security pipeline[/dim]")
            stream.write("")

    # Named-style overrides for role-tinted markdown. Rich's ``Markdown``
    # renderer looks up every element (``markdown.text``, ``markdown.h1``,
    # ``markdown.code`` …) against the active console theme. We keep the
    # structural styles (code, code_block, table) at their Rich defaults
    # so fenced blocks stay syntax-highlighted and inline code keeps its
    # cyan pill, but override the prose-bearing entries so the turn body
    # still reads as "this block is the user/agent talking" at a glance.
    _MD_THEME_USER = Theme({
        "markdown.text":       USER_BODY,
        "markdown.paragraph":  USER_BODY,
        "markdown.list_item":  USER_BODY,
        "markdown.item":       USER_BODY,
        "markdown.strong":     f"bold {USER_BODY}",
        "markdown.emph":       f"italic {USER_BODY}",
    }, inherit=True)
    _MD_THEME_AGENT = Theme({
        "markdown.text":       AGENT_BODY,
        "markdown.paragraph":  AGENT_BODY,
        "markdown.list_item":  AGENT_BODY,
        "markdown.item":       AGENT_BODY,
        "markdown.strong":     f"bold {AGENT_BODY}",
        "markdown.emph":       f"italic {AGENT_BODY}",
    }, inherit=True)

    def _write_markdown_body(
        self, stream: RichLog, content: str, role: str,
    ) -> None:
        """Render ``content`` as markdown (bold, headers, tables, fenced
        code, inline code, lists) with role-tinted prose.

        Reserved for user/agent turn bodies only — tool_call/tool_result/
        error content goes through ``_write_body`` which leaves raw JSON
        and ANSI-sanitized output untouched. The theme override tints
        just the text-bearing nodes (paragraph / list_item / strong /
        emph); code, tables, and code_blocks stay in Rich's default
        palette so the reader still sees a cyan pill for ``foo`` and a
        syntax-highlighted block for triple-backtick fences.

        Implementation: RichLog doesn't expose its internal console, and
        Rich's Markdown resolves named styles (``markdown.text`` etc.)
        against the console theme at render time. So we build a
        throw-away Console with the role theme, send its direct output
        to an in-memory sink, then replay the recorded ANSI into a Rich
        ``Text`` object for ``stream.write``.
        """
        if not content:
            return
        theme = self._MD_THEME_USER if role == "user" else self._MD_THEME_AGENT
        width = max(40, (stream.size.width or 80) - 2)
        con = Console(
            width=width,
            theme=theme,
            force_terminal=True,
            color_system="truecolor",
            record=True,
            file=StringIO(),
            legacy_windows=False,
        )
        con.print(Markdown(content, code_theme="monokai"))
        from rich.text import Text
        text = Text.from_ansi(con.export_text(styles=True))
        stream.write(text)

    def _write_body(self, stream: RichLog, content: str, color: str) -> None:
        text = content or ""
        for line in text.splitlines() or [""]:
            stream.write(f"[{color}]{escape(line)}[/{color}]")

    def _write_conversation(self, stream: RichLog, items: list[dict[str, Any]]) -> None:
        for it in items:
            if it["type"] == "user":
                stream.write(f"[{self.USER_COLOR} bold]── User ──[/{self.USER_COLOR} bold]")
                self._write_markdown_body(stream, it.get("content") or "", "user")
            else:
                hdr = f"[{self.AGENT_COLOR} bold]── Agent ──[/{self.AGENT_COLOR} bold]"
                if it.get("tool_count"):
                    hdr += f"  [dim]{it['tool_count']} tools: {escape(it['tool_summary'])}[/dim]"
                stream.write(hdr)
                self._write_markdown_body(stream, it.get("content") or "", "agent")
            stream.write("")

    def _write_full(self, stream: RichLog, items: list[dict[str, Any]]) -> None:
        for it in items:
            et = it["event_type"]
            body_color = "default"
            md_role: str | None = None  # set for user/agent text only
            if et == "user_message":
                stream.write(f"[{self.USER_COLOR} bold]── User ──[/{self.USER_COLOR} bold]")
                md_role = "user"
            elif et == "agent_text":
                stream.write(f"[{self.AGENT_COLOR} bold]── Agent ──[/{self.AGENT_COLOR} bold]")
                md_role = "agent"
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
            if md_role is not None:
                self._write_markdown_body(stream, it.get("content") or "", md_role)
            else:
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
            k("r", "refresh"),
            k("d", "discard"),
        ]
        if self._undo_stack:
            parts.append(k("u", f"undo ({len(self._undo_stack)})"))
        parts += [
            k("a", f"view:{mode}"),
            k("g/G", "top/bot"),
            k("[/]", "page"),
            k("i", "sec info"),
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

    async def action_toggle_stage(self) -> None:
        trace = self._focused_list_trace()
        if not trace:
            return
        trace_id = trace["trace_id"]
        source_stage = self._focused_stage() or self._active_stage
        if source_stage not in STAGE_KEYS:
            return
        source_lv = self.query_one(f"#{STAGE_IDS[source_stage]}", ListView)
        source_index = source_lv.index or 0

        stage = get_stage(self.state, trace_id)
        prior = self._snapshot_status(trace_id)
        task_label = (trace.get("task", {}).get("description") or "trace")[:40]
        if stage == "inbox":
            dest_stage = "staged"
            commit_single(self.state, trace_id, task_label)
            self._undo_stack.append(_UndoOp("stage", trace_id,
                                            f"stage '{task_label}'", prior))
            self.notify("Added to staged · u to undo", severity="information")
        elif stage == "staged":
            dest_stage = "inbox"
            unstage_trace(self.state, trace_id)
            self._undo_stack.append(_UndoOp("unstage", trace_id,
                                            f"unstage '{task_label}'", prior))
            self.notify("Moved back to inbox · u to undo", severity="information")
        else:
            self.notify("Only inbox/staged traces can be toggled", severity="warning")
            return
        await self._move_trace_row(trace, source_stage, dest_stage, source_index)
        self._refresh_keybar()

    async def _move_trace_row(
        self,
        trace: dict[str, Any],
        source_stage: str,
        dest_stage: str,
        source_index: int,
    ) -> None:
        """Incrementally shuffle one row from ``source_stage`` to ``dest_stage``.

        Avoids the full-reload flicker: no disk read, no clear+rebuild across
        all three lists. Only the source and destination lists mutate, and the
        source cursor advances to whatever row slid up into ``source_index``.

        Awaits the DOM mutations so by the time we pin the cursor, the source
        list's children list is accurate — otherwise Textual applies the
        ``-highlight`` class to the row being removed, and the new top row
        reads as unhighlighted until the user nudges j/k.
        """
        trace_id = trace.get("trace_id")

        # Update the in-memory groupings first — these are the source of truth
        # the handlers read from.
        src_items = self.by_stage[source_stage]
        for i, t in enumerate(src_items):
            if t.get("trace_id") == trace_id:
                src_items.pop(i)
                break

        dest_items = self.by_stage[dest_stage]
        key = trace.get("timestamp_end") or trace.get("timestamp_start") or ""
        insert_at = len(dest_items)
        for i, t in enumerate(dest_items):
            tkey = t.get("timestamp_end") or t.get("timestamp_start") or ""
            if key >= tkey:
                insert_at = i
                break
        dest_items.insert(insert_at, trace)

        source_lv = self.query_one(f"#{STAGE_IDS[source_stage]}", ListView)
        dest_lv = self.query_one(f"#{STAGE_IDS[dest_stage]}", ListView)

        # Build the destination row before we touch the DOM so both ops can
        # be queued and awaited together — Textual batches them into one
        # render pass, so the user sees a single transition rather than
        # remove-then-insert flicker.
        width_hint = max(36, dest_lv.size.width - 4 or 44)
        new_row = TraceRow(
            trace,
            is_blocked=trace_id in self._blocked_ids,
            width_hint=width_hint,
        )
        dest_children = list(dest_lv.children)
        if insert_at < len(dest_children):
            mount_await = dest_lv.mount(new_row, before=dest_children[insert_at])
        else:
            mount_await = dest_lv.append(new_row)

        remove_await = None
        for child in list(source_lv.children):
            if isinstance(child, TraceRow) and child.trace.get("trace_id") == trace_id:
                remove_await = child.remove()
                break

        if remove_await is not None:
            await remove_await
        if mount_await is not None:
            await mount_await

        remaining = len(src_items)
        if remaining == 0:
            source_lv.index = None
            self._update_panel_counter(source_stage)
            self._update_panel_counter(dest_stage)
            fallback = self._first_trace()
            if fallback:
                self._select_trace(fallback)
            else:
                self._render_empty_detail()
            return

        next_idx = max(0, min(source_index, remaining - 1))
        # Bounce through None so a numerically unchanged index still emits
        # Highlighted — and so Textual reassigns the ``-highlight`` class to
        # the row that now occupies ``next_idx``.
        source_lv.index = None
        source_lv.index = next_idx
        self._set_active_stage(source_stage)
        self._update_panel_counter(source_stage)
        self._update_panel_counter(dest_stage)
        self.set_focus(source_lv)

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

    def action_refresh(self) -> None:
        """Run a live inbox refresh with per-action counters."""
        pre_snapshot: dict[str, str] = {
            t.get("trace_id", ""): str(t.get("timestamp_end") or "")
            for t in self.traces
            if t.get("trace_id")
        }
        self.push_screen(RefreshRunnerModal(self.project_dir, pre_snapshot))

    def _finish_refresh(self, pre_snapshot: dict[str, str], report: Any | None = None) -> None:
        self._rehydrate_after_external_write()

        remote_index = self._load_remote_index_if_fresh()
        recently = 0
        supersedes_local = 0
        supersedes_remote = 0
        for trace in self.traces:
            gen = trace.get("generation_index") or 0
            sid = trace.get("session_id") or ""
            if _is_recently_touched(trace.get("timestamp_end")):
                recently += 1
            if gen > 1 and sid:
                supersedes_local += 1
                if remote_index and remote_index.supersedes_remote(sid, gen):
                    supersedes_remote += 1

        if report is None:
            new_count = 0
            updated_count = 0
            for trace in self.traces:
                tid = trace.get("trace_id", "")
                te = str(trace.get("timestamp_end") or "")
                if not tid:
                    continue
                if tid not in pre_snapshot:
                    new_count += 1
                elif pre_snapshot[tid] and te > pre_snapshot[tid]:
                    updated_count += 1
            toast = (
                f"Pulled +{new_count} new / ~{updated_count} updated · "
                f"{recently} recently touched"
            )
            severity = "information"
        else:
            created = int(getattr(report, "created", 0) or 0)
            refreshed = int(getattr(report, "refreshed", 0) or 0)
            new_generations = int(getattr(report, "new_generations", 0) or 0)
            noops = int(getattr(report, "noops", 0) or 0)
            skipped = int(getattr(report, "skipped", 0) or 0)
            errored = int(getattr(report, "errored", 0) or 0)
            parts = [
                "Refresh complete",
                f"{created} new",
                f"{refreshed} refreshed",
            ]
            if new_generations:
                parts.append(f"{new_generations} new gen")
            if noops:
                parts.append(f"{noops} unchanged")
            if skipped:
                parts.append(f"{skipped} skipped")
            if errored:
                parts.append(f"{errored} error(s)")
            parts.append(f"{recently} recently touched")
            toast = " · ".join(parts)
            severity = "warning" if errored else "information"

        if supersedes_local:
            toast += (
                f" · {supersedes_local} supersede earlier gen "
                f"(remote {supersedes_remote} / local {supersedes_local - supersedes_remote})"
            )
        self.notify(toast, severity=severity)

    def _load_remote_index_if_fresh(self, ttl_seconds: int = 900):
        """Return the cached RemoteIndex if present and younger than ``ttl_seconds``.

        Non-fatal: a missing or stale cache just suppresses the "remote
        supersedes" portion of the refresh toast. Users can run
        ``opentraces pull`` to refresh it manually.
        """
        try:
            from ..core.config import get_project_dir
            from ..publish.huggingface.remote_index import (
                RemoteIndex,
                cache_path_for,
            )
            path = cache_path_for(get_project_dir(self.project_dir))
            idx = RemoteIndex.load(path)
            if idx is None or idx.is_stale(ttl_seconds):
                return None
            return idx
        except Exception:
            logger.exception("remote_index load failed")
            return None

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

    def _rehydrate_after_external_write(self) -> None:
        """Refresh from disk after another surface mutated state.json.

        ``self.state`` is initialised once at app startup and kept
        entirely in-memory. Rebuild ``StateManager`` from disk before
        reloading the view after a non-TUI action changes trace state.
        """
        self.state = StateManager(
            state_path=get_project_state_path(self.project_dir)
        )
        self._load_project_context()
        self._reload_traces()

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

    def action_security_info(self) -> None:
        # Toggle: if the modal is already on top, close it. The app-level
        # binding for ``i`` fires with priority, which otherwise swallows
        # the modal's own escape-binding for the same key and opens a
        # second copy of itself on top.
        if isinstance(self.screen, SecurityInfoModal):
            self.pop_screen()
            return
        trace = self._current_trace or self._focused_list_trace()
        if not trace:
            self.notify("Select a trace first", severity="warning")
            return
        entry = self.state.get_trace(trace["trace_id"])
        stage = get_stage(self.state, trace["trace_id"])
        if stage == "blocked":
            stage = "inbox"  # TUI surfaces blocked under inbox
        block_reason = getattr(entry, "block_reason", None) if entry else None
        self.push_screen(SecurityInfoModal(trace, stage, block_reason))

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
            self.notify("No staged traces selected for dataset publication", severity="warning")
            return

        self.push_screen(
            PushModal(self.remote_name, staged_count=len(self.by_stage["staged"])),
        )

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
        source_stage: str | None = None
        for stage, lid in STAGE_IDS.items():
            if event.list_view.id == lid:
                source_stage = stage
                break
        if source_stage is None:
            return
        # During an incremental stage-toggle, ``.remove()`` on the old row is
        # async — the event may still carry the row that's about to leave the
        # DOM. Trust ``self.by_stage`` (updated synchronously by
        # ``_move_trace_row``) as the source of truth, and fall back to the
        # event's item only when the in-memory view has nothing at that index.
        lv = event.list_view
        idx = lv.index
        stage_items = self.by_stage.get(source_stage, [])
        trace: dict[str, Any] | None = None
        if idx is not None and 0 <= idx < len(stage_items):
            trace = stage_items[idx]
        elif isinstance(event.item, TraceRow):
            trace = event.item.trace
        if trace is not None:
            # Highlighting a non-active list promotes it to active — only one
            # row in the left column is ever the preview source.
            if source_stage != self._active_stage:
                self._set_active_stage(source_stage)
            self._current_trace = trace
            self._render_trace(trace)
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
