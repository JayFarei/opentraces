"""Shared helper functions for the OpenTraces TUI.

Extracted from the monolithic tui.py.  Every function returns CSS token
names by default (for Textual styling).  The ``ANSI_COLORS`` dict provides
the original Rich/ansi_ fallbacks for markup that still needs them.
"""

from __future__ import annotations

import json
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Colour maps
# ---------------------------------------------------------------------------

#: CSS design-system tokens (Textual default)
STAGE_TOKENS: dict[str, str] = {
    "inbox": "$warning",
    "committed": "$primary",
    "pushed": "$secondary",
    "rejected": "$error",
}

#: Rich/ansi fallback colours
ANSI_COLORS: dict[str, str] = {
    "inbox": "ansi_yellow",
    "committed": "ansi_bright_blue",
    "pushed": "ansi_cyan",
    "rejected": "ansi_red",
    # tool / role helpers
    "file_tool": "ansi_green",
    "web_tool": "ansi_yellow",
    "agent_tool": "ansi_cyan",
    "ask_tool": "ansi_bright_blue",
    "skill_tool": "ansi_magenta",
    "dim": "ansi_bright_black",
    "user_role": "ansi_bright_blue",
    "subagent_role": "ansi_cyan",
    "agent_role": "ansi_magenta",
    "system_role": "ansi_bright_black",
    "default_role": "ansi_default",
    "source_user": "ansi_bright_blue",
    "source_agent": "ansi_magenta",
    "source_proj": "ansi_green",
    "source_ext": "ansi_yellow",
    "source_default": "ansi_default",
}

# Rich markup color names (hex values for Rich, not CSS tokens)
# These match the DESIGN.md color palette
RICH_COLORS: dict[str, str] = {
    "primary": "#F97316",
    "secondary": "#22D3EE",
    "accent": "#22C55E",
    "panel": "#191919",
    "background": "#111111",
    "foreground": "#E0E0E0",
    "text-muted": "#666666",
    "error": "#EF4444",
    "warning": "#EAB308",
    "success": "#22C55E",
}

# Tool category sets (reused by _tool_color and _source_label)
_FILE_TOOLS = {"Read", "Edit", "Write", "Grep", "Glob", "Bash"}
_WEB_TOOLS = {"WebSearch", "WebFetch", "ToolSearch"}

# ---------------------------------------------------------------------------
# Markup / escaping
# ---------------------------------------------------------------------------


def escape(text: str) -> str:
    """Escape ALL square brackets for Rich markup, not just tag-like ones."""
    return text.replace("[", "\\[")


# ---------------------------------------------------------------------------
# Stage presentation
# ---------------------------------------------------------------------------


def _status_icon(status: str) -> str:
    return {
        "committed": "[cyan]\u25A0[/cyan]",
        "rejected": "[red]\u2717[/red]",
        "inbox": "[yellow]\u25CB[/yellow]",
        "pushed": "[green]\u2713[/green]",
    }.get(status, "[yellow]\u25CB[/yellow]")


def _stage_color(status: str) -> str:
    """Return the CSS token name for a visible stage."""
    return STAGE_TOKENS.get(status, "$warning")


def _stage_color_ansi(status: str) -> str:
    """Return the Rich/ansi colour string for a visible stage."""
    return ANSI_COLORS.get(status, "ansi_yellow")


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _relative_time(ts: str | None) -> str:
    if not ts:
        return ""
    try:
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


# ---------------------------------------------------------------------------
# Trace summaries & sorting
# ---------------------------------------------------------------------------


def _trace_summary(trace: dict[str, Any], status: str) -> str:
    task = (trace.get("task", {}).get("description") or "No description")[:40]
    steps = trace.get("metrics", {}).get("total_steps", len(trace.get("steps", [])))
    tool_calls = sum(len(s.get("tool_calls", [])) for s in trace.get("steps", []))
    flags = len(trace.get("_security_flags", []))
    ts = _relative_time(trace.get("timestamp_start"))
    icon = _status_icon(status)

    flag_str = f" [red]{flags} flags[/red]" if flags else ""
    return f"{icon} {task}  [dim]{steps}s {tool_calls}tc{flag_str} {ts}[/dim]"


def _sort_key(trace: dict[str, Any], stage_fn: Any) -> tuple[int, str]:
    """Sort key using a callable that returns the visible stage for a trace.

    ``stage_fn`` should accept a trace-id string and return the stage name.
    This avoids importing StateManager at module level.
    """
    from ...workflow import VISIBLE_STAGE_ORDER

    status = stage_fn(trace["trace_id"])
    try:
        stage_index = VISIBLE_STAGE_ORDER.index(status)
    except ValueError:
        stage_index = 0
    timestamp = trace.get("timestamp_start") or ""
    return (stage_index, timestamp)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _project_dir_from_staging(staging_dir: Path) -> Path:
    if staging_dir.name == "staging" and staging_dir.parent.name == ".opentraces":
        return staging_dir.parent.parent
    return Path.cwd()


# ---------------------------------------------------------------------------
# Text truncation / folding
# ---------------------------------------------------------------------------


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


def _line_limit(widget: Any, padding: int = 6, floor: int = 48) -> int:
    """Dynamic width from a Textual widget (Static or RichLog)."""
    width = getattr(getattr(widget, "size", None), "width", 0) or 0
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


# ---------------------------------------------------------------------------
# Colour helpers for tools / roles / sources
# ---------------------------------------------------------------------------


def _tool_color(tool_name: str) -> str:
    """Return CSS token for a tool name."""
    if tool_name in _FILE_TOOLS:
        return "$accent"
    if tool_name in _WEB_TOOLS:
        return "$warning"
    if tool_name == "Agent":
        return "$secondary"
    if tool_name == "AskUserQuestion":
        return "$primary"
    if tool_name == "Skill":
        return "$accent"
    return "$text-muted"


def _tool_color_ansi(tool_name: str) -> str:
    """Return Rich/ansi colour for a tool name."""
    if tool_name in _FILE_TOOLS:
        return ANSI_COLORS["file_tool"]
    if tool_name in _WEB_TOOLS:
        return ANSI_COLORS["web_tool"]
    if tool_name == "Agent":
        return ANSI_COLORS["agent_tool"]
    if tool_name == "AskUserQuestion":
        return ANSI_COLORS["ask_tool"]
    if tool_name == "Skill":
        return ANSI_COLORS["skill_tool"]
    return ANSI_COLORS["dim"]


def _role_color(role: str, call_type: str | None = None) -> str:
    """Return CSS token for a role."""
    if role == "user":
        return "$primary"
    if role == "subagent" or call_type == "subagent":
        return "$secondary"
    if role == "agent":
        return "$accent"
    if role == "system":
        return "$text-muted"
    return "$text"


def _role_color_ansi(role: str, call_type: str | None = None) -> str:
    """Return Rich/ansi colour for a role."""
    if role == "user":
        return ANSI_COLORS["user_role"]
    if role == "subagent" or call_type == "subagent":
        return ANSI_COLORS["subagent_role"]
    if role == "agent":
        return ANSI_COLORS["agent_role"]
    if role == "system":
        return ANSI_COLORS["system_role"]
    return ANSI_COLORS["default_role"]


def _source_label(step: dict[str, Any], tool_name: str | None = None) -> str:
    role = step.get("role")
    if role == "user":
        return "user"
    if tool_name in _FILE_TOOLS:
        return "proj"
    if tool_name in _WEB_TOOLS:
        return "ext"
    if role == "system":
        return "proj"
    return "agent"


def _source_color(source: str) -> str:
    """Return CSS token for a source label."""
    return {
        "user": "$primary",
        "agent": "$accent",
        "proj": "$accent",
        "ext": "$warning",
    }.get(source, "$text")


def _source_color_ansi(source: str) -> str:
    """Return Rich/ansi colour for a source label."""
    return {
        "user": ANSI_COLORS["source_user"],
        "agent": ANSI_COLORS["source_agent"],
        "proj": ANSI_COLORS["source_proj"],
        "ext": ANSI_COLORS["source_ext"],
    }.get(source, ANSI_COLORS["source_default"])
