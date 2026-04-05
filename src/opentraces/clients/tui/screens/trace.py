"""Unified trace viewer with a block-based transcript and step sidebar."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from rich.console import Group, RenderableType
from rich.markdown import Markdown as RichMarkdown
from rich.syntax import Syntax
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, ListItem, ListView, Static

from ..messages import FlashMessage
from ..utils import _truncate, escape
from ..widgets.help_overlay import HelpOverlay
from ..widgets.key_bar import KeyBar
from ..widgets.step_block import StepBlock
from ..widgets.tool_call_block import format_tool_call_summary

logger = logging.getLogger(__name__)

C_PRIMARY = "#fab283"
C_SECONDARY = "#5c9cf5"
C_TOOL = "#6a6a6a"
C_SUCCESS = "#7fd88f"
C_ERROR = "#e06c75"
C_WARN = "#e5c07b"
C_ACCENT = "#9d7cd8"

TOOL_COLORS: dict[str, str] = {
    "Read": C_SUCCESS,
    "Edit": C_PRIMARY,
    "Write": C_PRIMARY,
    "Bash": "#56b6c2",
    "Grep": C_SUCCESS,
    "Glob": C_SUCCESS,
    "Agent": C_ACCENT,
    "WebSearch": C_WARN,
    "WebFetch": C_WARN,
    "Skill": C_ACCENT,
    "AskUserQuestion": C_SECONDARY,
    "TaskCreate": C_ACCENT,
    "TaskUpdate": C_ACCENT,
    "EnterPlanMode": C_ACCENT,
    "ExitPlanMode": C_ACCENT,
    "SendMessage": C_SECONDARY,
    "ToolSearch": C_TOOL,
    "todo": C_ACCENT,
    "TodoWrite": C_ACCENT,
}

TOOL_CATEGORIES: dict[str, str] = {
    "Read": "File",
    "Edit": "File",
    "Write": "File",
    "Glob": "File",
    "Grep": "File",
    "Bash": "Shell",
    "Agent": "Agent",
    "SendMessage": "Agent",
    "TaskCreate": "Task",
    "TaskUpdate": "Task",
    "EnterPlanMode": "Plan",
    "ExitPlanMode": "Plan",
    "WebSearch": "Web",
    "WebFetch": "Web",
    "AskUserQuestion": "Ask",
    "Skill": "Skill",
    "ToolSearch": "Tool",
    "todo": "Task",
    "TodoWrite": "Task",
}

_XML_STRIP_TAGS = {
    "system-reminder",
    "task-notification",
    "task-id",
    "tool-use-id",
    "output-file",
    "status",
    "summary",
    "result",
    "usage",
    "total_tokens",
    "tool_uses",
    "duration_ms",
    "command-name",
    "command-message",
    "command-args",
    "local-command-caveat",
    "local-command-stdout",
    "antml_thinking",
}

_XML_STRIP_RE = re.compile(
    r"</?(?:" + "|".join(re.escape(tag) for tag in _XML_STRIP_TAGS) + r")[^>]*>",
    re.IGNORECASE,
)

SPARK = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def _tc(name: str) -> str:
    return TOOL_COLORS.get(name, C_TOOL)


def _cat(name: str) -> str:
    if name.startswith("mcp__"):
        return "MCP"
    return TOOL_CATEGORIES.get(name, "Tool")


def _strip_xml(text: str) -> str:
    cleaned = _XML_STRIP_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _fmt_dur(seconds: float | int | None) -> str:
    if not seconds:
        return "-"
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _fmt_tok(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 10000:
        return f"{n / 1000:.1f}k"
    if n < 1000000:
        return f"{n // 1000}k"
    return f"{n / 1000000:.1f}M"


def _fmt_time_24h(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    except Exception:
        return ""


def _sparkline(steps: list[dict[str, Any]], width: int = 20) -> str:
    tokens = [
        (step.get("token_usage") or {}).get("input_tokens", 0)
        + (step.get("token_usage") or {}).get("output_tokens", 0)
        for step in steps
    ]
    if not tokens or max(tokens) == 0:
        return ""
    if len(tokens) > width:
        ratio = len(tokens) / width
        tokens = [
            max(tokens[int(i * ratio): int((i + 1) * ratio)] or [0])
            for i in range(width)
        ]
    maximum = max(tokens)
    return "".join(SPARK[min(int(token / maximum * 7), 7)] for token in tokens)


def _step_label(step: dict[str, Any], idx: int) -> str:
    role = step.get("role", "?")
    tool_calls = step.get("tool_calls") or []

    if role == "user":
        tag = f"[bold {C_SECONDARY}]USER[/bold {C_SECONDARY}]"
    elif role in ("assistant", "agent"):
        tag = f"[{C_PRIMARY}]AGNT[/{C_PRIMARY}]"
    elif role == "system":
        tag = "[dim]SYS [/dim]"
    else:
        tag = f"[dim]{role[:4].upper():4s}[/dim]"

    if tool_calls:
        first = tool_calls[0].get("tool_name", "?")
        category = _cat(first)
        color = _tc(first)
        label = f"[{color}]{category}[/{color}]"
        if len(tool_calls) > 1:
            label += f"[dim] x{len(tool_calls)}[/dim]"
    else:
        content = _truncate(step.get("content") or "", 18)
        label = f"[dim]{escape(content)}[/dim]" if content else ""

    return f"[dim]{idx:3d}[/dim]  {tag}  {label}"


def _tool_name(tool_call: dict[str, Any]) -> str:
    return tool_call.get("name") or tool_call.get("tool_name") or "unknown"


def _tool_status_text(tool_call: dict[str, Any]) -> Text:
    status = tool_call.get("status") or ""
    if not status:
        return Text("")
    if status == "success":
        return Text("  OK", style=f"bold {C_SUCCESS}")
    if status == "error":
        return Text("  ERR", style=f"bold {C_ERROR}")
    return Text(f"  {status}", style="#888888")


def _tool_text_line(name: str, summary: str) -> Text:
    line = Text()
    line.append(name, style=f"bold {_tc(name)}")
    if summary:
        line.append("  ")
        line.append(summary, style="#D6D6D6")
    return line


def _task_line(title: str, status: str | None = None) -> Text:
    colors = {
        "new": C_ACCENT,
        "in_progress": C_WARN,
        "completed": C_SUCCESS,
        "pending": "#888888",
    }
    labels = {
        "new": "NEW",
        "in_progress": "DOING",
        "completed": "DONE",
        "pending": "TASK",
    }
    key = status or "pending"
    line = Text()
    line.append(labels.get(key, "TASK"), style=f"bold {colors.get(key, '#888888')}")
    line.append("  ")
    line.append(title, style="bold #E8E8E8")
    return line


def _todo_label(status: str | None) -> tuple[str, str]:
    labels = {
        "pending": ("TODO", "#888888"),
        "in_progress": ("DOING", C_WARN),
        "completed": ("DONE", C_SUCCESS),
        "cancelled": ("DROP", C_ERROR),
    }
    return labels.get(status or "pending", ("TODO", "#888888"))


def _indented_text(value: str, *, style: str = "#B8B8B8") -> Text:
    line = Text("  ")
    line.append(value, style=style)
    return line


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _todo_items(tool_call: dict[str, Any]) -> list[dict[str, Any]]:
    inp = tool_call.get("input") or tool_call.get("arguments") or {}
    todos = inp.get("todos")
    if not isinstance(todos, list):
        return []
    return [todo for todo in todos if isinstance(todo, dict)]


def _todo_summary(items: list[dict[str, Any]]) -> Text:
    counts = {"pending": 0, "in_progress": 0, "completed": 0, "cancelled": 0}
    for item in items:
        status = str(item.get("status") or "pending")
        counts[status] = counts.get(status, 0) + 1

    line = Text()
    line.append("TODOS", style=f"bold {C_ACCENT}")
    line.append(f"  {len(items)}", style="bold #E8E8E8")
    for key, label in (
        ("in_progress", "doing"),
        ("completed", "done"),
        ("pending", "pending"),
    ):
        count = counts.get(key, 0)
        if count:
            _, color = _todo_label(key)
            line.append("  ")
            line.append(f"{count} {label}", style=color)
    if counts.get("cancelled", 0):
        line.append("  ")
        line.append(f"{counts['cancelled']} dropped", style=C_ERROR)
    return line


def _todo_changed_items(
    items: list[dict[str, Any]],
    previous_items: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not previous_items:
        return items

    previous_by_id = {str(item.get("id") or ""): item for item in previous_items}
    changed: list[dict[str, Any]] = []
    for item in items:
        item_id = str(item.get("id") or "")
        previous = previous_by_id.get(item_id)
        if previous != item:
            changed.append(item)
    return changed


def _todo_item_line(item: dict[str, Any]) -> Text:
    status = str(item.get("status") or "pending")
    label, color = _todo_label(status)
    line = Text()
    line.append(label, style=f"bold {color}")
    item_id = str(item.get("id") or "").strip()
    if item_id:
        line.append("  ")
        line.append(f"#{item_id}", style="#7A7A7A")
    content = _compact_text(item.get("content") or item.get("title") or "Untitled todo")
    if content:
        line.append("  ")
        line.append(content, style="#E8E8E8")
    priority = _compact_text(item.get("priority"))
    if priority:
        line.append("  ")
        line.append(f"[{priority}]", style="#7FAFD4")
    return line


def _todo_renderable(
    tool_call: dict[str, Any],
    previous_items: list[dict[str, Any]] | None = None,
) -> RenderableType:
    items = _todo_items(tool_call)
    if not items:
        return _tool_text_line("todo", format_tool_call_summary(tool_call, limit=120))

    changed = _todo_changed_items(items, previous_items)
    parts: list[RenderableType] = [_todo_summary(items)]
    if previous_items and changed and len(changed) < len(items):
        parts.append(_indented_text("updated", style="#7A7A7A"))
    for item in changed or items:
        parts.append(_todo_item_line(item))
    return Group(*parts)


def _task_create_renderable(tool_call: dict[str, Any]) -> RenderableType:
    inp = tool_call.get("input") or tool_call.get("arguments") or {}
    subject = str(inp.get("subject") or "Task")
    description = str(inp.get("description") or "").strip()
    parts: list[RenderableType] = [_task_line(subject, "new")]
    if description:
        parts.append(_indented_text(description))
    return Group(*parts)


def _task_update_renderable(tool_call: dict[str, Any], task_lookup: dict[str, str] | None) -> RenderableType:
    inp = tool_call.get("input") or tool_call.get("arguments") or {}
    task_id = str(inp.get("taskId") or inp.get("task_id") or "?")
    title = ""
    if task_lookup:
        title = task_lookup.get(task_id, "")
    active_form = str(inp.get("activeForm") or inp.get("active_form") or "").strip()
    status = str(inp.get("status") or "pending")
    heading = title or active_form or f"Task {task_id}"
    parts: list[RenderableType] = [_task_line(f"#{task_id}  {heading}", status)]
    if active_form and active_form != heading:
        parts.append(_indented_text(active_form, style="#D2D2D2"))
    return Group(*parts)


def _delegate_task_renderable(tool_call: dict[str, Any]) -> RenderableType:
    inp = tool_call.get("input") or tool_call.get("arguments") or {}
    tasks = inp.get("tasks")
    if not isinstance(tasks, list):
        tasks = [inp]

    parts: list[RenderableType] = []
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            continue
        goal = str(task.get("goal") or f"Delegated task {index}")
        toolsets = task.get("toolsets") or []
        parts.append(_task_line(goal, "pending"))
        if isinstance(toolsets, list) and toolsets:
            parts.append(
                _indented_text(
                    "[" + ", ".join(str(item) for item in toolsets) + "]",
                    style="#7FAFD4",
                )
            )
        context = _compact_text(task.get("context"))
        if context:
            parts.append(_indented_text(context))
    if parts:
        return Group(*parts)
    return _tool_text_line("delegate_task", format_tool_call_summary(tool_call, limit=120))


def _bash_command(tool_call: dict[str, Any]) -> str:
    inp = tool_call.get("input") or tool_call.get("arguments") or {}
    if isinstance(inp, dict):
        for key in ("command", "cmd"):
            value = inp.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _render_tool_call(tool_call: dict[str, Any], task_lookup: dict[str, str] | None = None) -> RenderableType:
    name = _tool_name(tool_call)
    if name in {"todo", "TodoWrite"}:
        return _todo_renderable(tool_call)
    if name == "TaskCreate":
        return _task_create_renderable(tool_call)
    if name == "TaskUpdate":
        return _task_update_renderable(tool_call, task_lookup)
    if name == "delegate_task":
        return _delegate_task_renderable(tool_call)

    summary = format_tool_call_summary(tool_call, limit=120)
    if name == "Bash":
        command = _bash_command(tool_call)
        header = _tool_text_line(name, "")
        header.append_text(_tool_status_text(tool_call))
        if command:
            return Group(
                header,
                Syntax(
                    command,
                    "bash",
                    theme="monokai",
                    line_numbers=False,
                    word_wrap=True,
                    background_color="#101318",
                    padding=(0, 1),
                ),
            )
        fallback = _tool_text_line(name, summary)
        fallback.append_text(_tool_status_text(tool_call))
        return fallback

    line = _tool_text_line(name, summary)
    line.append_text(_tool_status_text(tool_call))
    return line


def _render_tool_sequence(tool_calls: list[dict[str, Any]], task_lookup: dict[str, str] | None = None) -> RenderableType:
    renderables: list[RenderableType] = []
    previous_todos: list[dict[str, Any]] | None = None
    for tool_call in tool_calls:
        name = _tool_name(tool_call)
        if name in {"todo", "TodoWrite"}:
            renderables.append(_todo_renderable(tool_call, previous_todos))
            previous_todos = _todo_items(tool_call)
            continue
        renderables.append(_render_tool_call(tool_call, task_lookup))
    return Group(*renderables)


def _plain_tool_sequence(tool_calls: list[dict[str, Any]], task_lookup: dict[str, str] | None = None) -> str:
    lines: list[str] = []
    previous_todos: list[dict[str, Any]] | None = None
    for tool_call in tool_calls:
        name = _tool_name(tool_call)
        if name in {"todo", "TodoWrite"}:
            items = _todo_items(tool_call)
            changed = _todo_changed_items(items, previous_todos)
            counts = {
                "in_progress": sum(1 for item in items if item.get("status") == "in_progress"),
                "completed": sum(1 for item in items if item.get("status") == "completed"),
                "pending": sum(1 for item in items if item.get("status") == "pending"),
            }
            summary = [f"TODOS  {len(items)}"]
            if counts["in_progress"]:
                summary.append(f"{counts['in_progress']} doing")
            if counts["completed"]:
                summary.append(f"{counts['completed']} done")
            if counts["pending"]:
                summary.append(f"{counts['pending']} pending")
            lines.append("  ".join(summary))
            if previous_todos and changed and len(changed) < len(items):
                lines.append("  updated")
            for item in changed or items:
                label, _ = _todo_label(str(item.get("status") or "pending"))
                item_id = str(item.get("id") or "").strip()
                content = _compact_text(item.get("content") or item.get("title") or "Untitled todo")
                prefix = f"{label}  "
                if item_id:
                    prefix += f"#{item_id}  "
                line = prefix + content
                priority = _compact_text(item.get("priority"))
                if priority:
                    line += f"  [{priority}]"
                lines.append(line)
            previous_todos = items
            continue
        if name == "TaskCreate":
            inp = tool_call.get("input") or tool_call.get("arguments") or {}
            subject = str(inp.get("subject") or "Task")
            description = str(inp.get("description") or "").strip()
            lines.append(f"NEW  {subject}")
            if description:
                lines.append(f"  {description}")
            continue
        if name == "TaskUpdate":
            inp = tool_call.get("input") or tool_call.get("arguments") or {}
            task_id = str(inp.get("taskId") or inp.get("task_id") or "?")
            status = str(inp.get("status") or "pending")
            active_form = str(inp.get("activeForm") or inp.get("active_form") or "").strip()
            title = task_lookup.get(task_id, "") if task_lookup else ""
            heading = title or active_form or f"Task {task_id}"
            lines.append(f"{status.upper()}  #{task_id}  {heading}")
            continue
        if name == "delegate_task":
            inp = tool_call.get("input") or tool_call.get("arguments") or {}
            tasks = inp.get("tasks")
            if not isinstance(tasks, list):
                tasks = [inp]
            for task in tasks:
                if isinstance(task, dict):
                    goal = str(task.get("goal") or "Delegated task")
                    lines.append(f"TASK  {goal}")
                    toolsets = task.get("toolsets") or []
                    if isinstance(toolsets, list) and toolsets:
                        lines.append(f"  [{', '.join(str(item) for item in toolsets)}]")
                    context = _compact_text(task.get("context"))
                    if context:
                        lines.append(f"  {context}")
            continue
        if name == "Bash":
            command = _bash_command(tool_call)
            if command:
                lines.append(f"{name}\n{command}")
                continue
        lines.append(f"{name}  {format_tool_call_summary(tool_call)}")
    return "\n".join(lines)


class StepIndexItem(ListItem):
    def __init__(self, step: dict[str, Any], index: int) -> None:
        super().__init__(Static(_step_label(step, index), markup=True))
        self.step_index = index


class JumpInput(Static):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", markup=True, **kwargs)
        self.display = False

    def compose(self) -> ComposeResult:
        yield Input(placeholder="step #...", id="jump-input", type="integer")

    def open(self) -> None:
        self.display = True
        input_widget = self.query_one("#jump-input", Input)
        input_widget.value = ""
        input_widget.focus()

    def close(self) -> None:
        self.display = False


class TraceScreen(Screen):
    """Trace viewer with a scrollable transcript and step index."""

    CSS_PATH = "trace.tcss"

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False, priority=True),
        Binding("k", "cursor_up", "Up", show=False, priority=True),
        Binding("enter", "toggle_expand", "Expand", priority=True),
        Binding("slash", "jump_to_step", "Jump", key_display="/", show=False, priority=True),
        Binding("1", "focus_transcript", "Transcript", show=False, priority=True),
        Binding("2", "focus_sidebar", "Sidebar", show=False, priority=True),
        Binding("y", "copy_block", "Copy", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("question_mark", "toggle_help", "Help", key_display="?"),
    ]

    def __init__(self, trace: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._trace = trace
        self._raw_steps = trace.get("steps") or []
        self._task_lookup = self._build_task_lookup(self._raw_steps)
        self._display_steps = self._build_display_steps(self._raw_steps)
        self._expanded_step: int | None = None
        self._cursor_step = 0
        self._agent_name = (trace.get("agent") or {}).get("name") or "agent"
        self._step_blocks: list[StepBlock] = []

    def compose(self) -> ComposeResult:
        yield HelpOverlay(id="help-overlay")
        with Vertical(id="trace-shell"):
            yield Static("", id="trace-topbar", markup=True)
            with Horizontal(id="trace-workspace"):
                with VerticalScroll(id="transcript"):
                    yield Static("", id="transcript-empty", markup=True)
                with Vertical(id="sidebar"):
                    yield Static("", id="sidebar-summary", markup=True)
                    yield ListView(id="step-index")
            yield JumpInput(id="jump-bar")
            yield KeyBar(id="keybar")

    def on_mount(self) -> None:
        self.query_one(KeyBar).set_mode("trace_transcript")
        self._update_topbar()
        self._populate_sidebar()
        self._populate_transcript()
        self._sync_cursor(scroll=False)
        self.set_focus(self.query_one("#transcript", VerticalScroll))

    def _steps(self) -> list[dict[str, Any]]:
        return self._display_steps

    def _update_topbar(self) -> None:
        trace = self._trace
        task = _truncate((trace.get("task") or {}).get("description") or "No description", 50)
        metrics = trace.get("metrics") or {}
        parts = [
            f"[bold {C_PRIMARY}]\u2190 esc[/bold {C_PRIMARY}]",
            f"[bold]{escape(task)}[/bold]",
            (
                f"[dim]{escape(self._agent_name)}  "
                f"{len(self._steps())} blocks  {_fmt_dur(metrics.get('total_duration_s'))}[/dim]"
            ),
        ]
        if len(self._raw_steps) != len(self._steps()):
            parts.append(f"[dim]{len(self._raw_steps)} raw steps[/dim]")
        cost = metrics.get("estimated_cost_usd")
        if cost:
            parts.append(f"[dim]${cost:.2f}[/dim]")
        self.query_one("#trace-topbar", Static).update("  ".join(parts))

    def _populate_sidebar(self) -> None:
        trace = self._trace
        outcome = trace.get("outcome") or {}
        metrics = trace.get("metrics") or {}
        flags = trace.get("_security_flags") or []
        steps = self._steps()

        lines: list[str] = []
        success = outcome.get("success")
        if success is True:
            lines.append(f" [{C_SUCCESS}]\u2713 success[/{C_SUCCESS}]")
        elif success is False:
            lines.append(f" [{C_ERROR}]\u2716 failed[/{C_ERROR}]")
        else:
            lines.append(" [dim]unknown[/dim]")
        lines.append("")
        lines.append(
            f" [dim]tokens[/dim]  {_fmt_tok(metrics.get('total_input_tokens', 0))} in / "
            f"{_fmt_tok(metrics.get('total_output_tokens', 0))} out"
        )
        cost = metrics.get("estimated_cost_usd")
        if cost is not None:
            lines.append(f" [dim]cost[/dim]    ${cost:.4f}")
        lines.append(f" [dim]time[/dim]    {_fmt_dur(metrics.get('total_duration_s'))}")
        if flags:
            lines.append(f" [{C_ERROR}]flags   {len(flags)}[/{C_ERROR}]")
        else:
            lines.append(f" [{C_SUCCESS}]flags   0[/{C_SUCCESS}]")

        sidebar_width = self.query_one("#sidebar").size.width
        spark = _sparkline(steps, max(10, (sidebar_width or 38) - 12))
        if spark:
            lines.append(f"\n [dim]tokens[/dim]  {spark}")

        self.query_one("#sidebar-summary", Static).update("\n".join(lines))

        index = self.query_one("#step-index", ListView)
        index.clear()
        for i, step in enumerate(steps):
            index.append(StepIndexItem(step, i))
        if steps:
            index.index = self._cursor_step

    def _sanitized_step(self, step: dict[str, Any]) -> dict[str, Any]:
        cloned = dict(step)
        content = step.get("content")
        sanitized = _strip_xml(content) if isinstance(content, str) else ""
        if sanitized:
            cloned["content"] = sanitized
            cloned["_content_plain"] = sanitized
            cloned["_content_markdown"] = True
        else:
            cloned["content"] = sanitized
            cloned["_content_plain"] = sanitized
            cloned["_content_markdown"] = True
        return cloned

    def _is_tool_only_step(self, step: dict[str, Any]) -> bool:
        role = step.get("role")
        content = step.get("content")
        sanitized = _strip_xml(content) if isinstance(content, str) else ""
        return role in {"assistant", "agent"} and not sanitized and bool(step.get("tool_calls"))

    def _is_visually_empty_step(self, step: dict[str, Any]) -> bool:
        content = step.get("content")
        sanitized = _strip_xml(content) if isinstance(content, str) else ""
        return not sanitized and not (step.get("tool_calls") or [])

    def _group_tool_steps(self, grouped_steps: list[dict[str, Any]]) -> dict[str, Any]:
        first = self._sanitized_step(grouped_steps[0])
        tool_calls: list[dict[str, Any]] = []
        for step in grouped_steps:
            tool_calls.extend(step.get("tool_calls") or [])

        grouped = dict(first)
        grouped["tool_calls"] = tool_calls
        grouped["content"] = _plain_tool_sequence(tool_calls, self._task_lookup)
        grouped["_content_plain"] = grouped["content"]
        grouped["_content_renderable"] = _render_tool_sequence(tool_calls, self._task_lookup)
        grouped["_content_markdown"] = False
        grouped["_force_full_content"] = True
        return grouped

    def _step_with_renderable(self, step: dict[str, Any]) -> dict[str, Any]:
        sanitized = self._sanitized_step(step)
        tool_calls = step.get("tool_calls") or []
        if not tool_calls:
            return sanitized

        renderables: list[RenderableType] = []
        plain_parts: list[str] = []
        content = sanitized.get("content") or ""
        if content:
            if sanitized.get("_content_markdown", True):
                renderables.append(RichMarkdown(content))
            else:
                renderables.append(content)
            plain_parts.append(content)

        renderables.append(_render_tool_sequence(tool_calls, self._task_lookup))
        plain_parts.append(_plain_tool_sequence(tool_calls, self._task_lookup))
        sanitized["_content_renderable"] = Group(*renderables)
        sanitized["_content_plain"] = "\n\n".join(part for part in plain_parts if part)
        sanitized["_content_markdown"] = False
        sanitized["_force_full_content"] = True
        return sanitized

    def _build_task_lookup(self, steps: list[dict[str, Any]]) -> dict[str, str]:
        task_lookup: dict[str, str] = {}
        task_counter = 0
        for step in steps:
            for tool_call in step.get("tool_calls") or []:
                name = _tool_name(tool_call)
                inp = tool_call.get("input") or tool_call.get("arguments") or {}
                if name == "TaskCreate":
                    task_counter += 1
                    subject = str(inp.get("subject") or f"Task {task_counter}")
                    task_lookup[str(task_counter)] = subject
        return task_lookup

    def _build_display_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        display_steps: list[dict[str, Any]] = []
        index = 0
        while index < len(steps):
            step = steps[index]
            if self._is_visually_empty_step(step):
                index += 1
                continue
            if self._is_tool_only_step(step):
                grouped_steps = [step]
                index += 1
                while index < len(steps) and self._is_tool_only_step(steps[index]):
                    grouped_steps.append(steps[index])
                    index += 1
                display_steps.append(self._group_tool_steps(grouped_steps))
                continue

            display_steps.append(self._step_with_renderable(step))
            index += 1
        return display_steps

    def _populate_transcript(self) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.remove_children()
        self._step_blocks = []

        steps = self._steps()
        if not steps:
            transcript.mount(Static("[dim]No steps in this trace[/dim]", markup=True, id="transcript-empty"))
            return

        widgets: list[StepBlock] = []
        for index, step in enumerate(steps):
            sanitized = self._sanitized_step(step)
            widgets.append(
                StepBlock(
                    sanitized,
                    step_index=index,
                    agent_name=self._agent_name,
                    timestamp_label=_fmt_time_24h(sanitized.get("timestamp")),
                    collapse_content=not sanitized.get("_force_full_content", False),
                    render_tool_calls=False,
                    classes="trace-step",
                )
            )
        transcript.mount_all(widgets)
        self._step_blocks = widgets

    def _sync_sidebar_cursor(self) -> None:
        sidebar = self.query_one("#step-index", ListView)
        steps = self._steps()
        if not steps:
            return
        if sidebar.index != self._cursor_step:
            sidebar.index = self._cursor_step

    def _sync_cursor(self, *, scroll: bool = True) -> None:
        for index, block in enumerate(self._step_blocks):
            block.selected = index == self._cursor_step
            block.expanded = index == self._expanded_step

        self._sync_sidebar_cursor()

        if scroll and self._step_blocks:
            target = self._step_blocks[self._cursor_step]
            self.query_one("#transcript", VerticalScroll).scroll_to_widget(
                target, animate=False, top=False
            )

    def action_cursor_down(self) -> None:
        if self._cursor_step < len(self._steps()) - 1:
            self._cursor_step += 1
            self._sync_cursor()

    def action_cursor_up(self) -> None:
        if self._cursor_step > 0:
            self._cursor_step -= 1
            self._sync_cursor()

    def action_toggle_expand(self) -> None:
        jump = self.query_one("#jump-bar", JumpInput)
        if jump.display:
            self._submit_jump(self.query_one("#jump-input", Input).value)
            return
        if not self._step_blocks:
            return
        if self._expanded_step == self._cursor_step:
            self._expanded_step = None
        else:
            self._expanded_step = self._cursor_step
        self._sync_cursor()

    def action_focus_transcript(self) -> None:
        self.set_focus(self.query_one("#transcript", VerticalScroll))

    def action_focus_sidebar(self) -> None:
        self.set_focus(self.query_one("#step-index", ListView))

    @on(ListView.Highlighted, "#step-index")
    def on_step_index_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, StepIndexItem):
            self._cursor_step = event.item.step_index
            self._sync_cursor()

    @on(ListView.Selected, "#step-index")
    def on_step_index_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, StepIndexItem):
            self._cursor_step = event.item.step_index
            self._expanded_step = event.item.step_index
            self._sync_cursor()
            self.action_focus_transcript()

    def action_jump_to_step(self) -> None:
        self.query_one("#jump-bar", JumpInput).open()

    def _submit_jump(self, value: str) -> None:
        self.query_one("#jump-bar", JumpInput).close()
        steps = self._steps()
        try:
            target = int(value)
        except ValueError:
            target = -1

        if 0 <= target < len(steps):
            self._cursor_step = target
            self._expanded_step = None
            self._sync_cursor()
            self.post_message(FlashMessage(f"[dim]jumped to step {target}[/dim]"))
        elif steps:
            self.post_message(
                FlashMessage(
                    f"[{C_ERROR}]step {target} out of range (0-{len(steps) - 1})[/{C_ERROR}]"
                )
            )
        self.set_focus(self.query_one("#transcript", VerticalScroll))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit_jump(event.value)

    def action_copy_block(self) -> None:
        steps = self._steps()
        if not steps or self._cursor_step >= len(steps):
            return
        content = steps[self._cursor_step].get("content") or ""
        try:
            import pyperclip

            pyperclip.copy(content)
            self.post_message(
                FlashMessage(f"[{C_SUCCESS}]\u2713 copied step {self._cursor_step}[/{C_SUCCESS}]")
            )
        except ImportError:
            self.notify("Install pyperclip for clipboard", severity="warning")
        except Exception:
            self.notify("Copy failed", severity="warning")

    def action_toggle_help(self) -> None:
        self.query_one(HelpOverlay).toggle()

    def action_back(self) -> None:
        help_overlay = self.query_one(HelpOverlay)
        if help_overlay.display:
            help_overlay.toggle()
            return

        jump = self.query_one("#jump-bar", JumpInput)
        if jump.display:
            jump.close()
            self.set_focus(self.query_one("#transcript", VerticalScroll))
            return

        self.app.pop_screen()

    def on_flash_message(self, message: FlashMessage) -> None:
        self.query_one(KeyBar).flash(message.text, message.duration)
