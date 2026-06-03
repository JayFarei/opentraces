"""Pure presentation helpers: styling, trace labels, git chips, status cells.

Extracted from cli/__init__.py (behavior-preserving split).
All symbols are re-exported from opentraces.cli for backward-compat.

NOTE: emit_json / human_echo / human_hint / print_banner / error_response are
NOT here because they depend on the _json_mode global that lives in __init__.py
and is mutated by the main() click callback. Moving them would require a
circular import or a shared mutable reference, so they stay in __init__.py.
"""

from __future__ import annotations

import click

# Plan 041 tier priority for picking the "best" git_link to display.
_STAGE_COLORS = {
    "inbox": "yellow",
    "staged": "cyan",
    "pushed": "green",
    "rejected": "red",
    "blocked": "red",
}


def _bold(text: str) -> str:
    return click.style(text, bold=True)


def _dim(text: str) -> str:
    return click.style(text, dim=True)


def _ok(text: str) -> str:
    return click.style(text, fg="green", bold=True)


def _warn(text: str) -> str:
    return click.style(text, fg="yellow")


def _err(text: str) -> str:
    return click.style(text, fg="red", bold=True)


def _stage_c(label: str, stage_key: str) -> str:
    color = _STAGE_COLORS.get(stage_key.lower())
    return click.style(label, fg=color) if color else label


def _describe_trace(record) -> tuple[str, str]:
    """Pick the best short label for a trace.

    Returns (label, source) where source is one of
    "task" | "step" | "tool" | "none".
    """
    task = getattr(record, "task", None)
    desc = getattr(task, "description", None) if task else None
    if desc:
        return desc.strip(), "task"
    for step in getattr(record, "steps", []) or []:
        content = getattr(step, "content", None)
        if content:
            flat = " ".join(content.split())
            if flat:
                return flat, "step"
    # Tool-only trace — synthesize a label from the first meaningful tool call.
    for step in getattr(record, "steps", []) or []:
        for tc in getattr(step, "tool_calls", []) or []:
            tool = getattr(tc, "tool_name", None)
            if not tool:
                continue
            raw_input = getattr(tc, "input", None)
            # input may be a dict OR a JSON string (parser-dependent).
            inp = None
            if isinstance(raw_input, dict):
                inp = raw_input
            elif isinstance(raw_input, str):
                try:
                    import json as _json
                    inp = _json.loads(raw_input)
                except Exception:
                    inp = None
            if isinstance(inp, dict):
                if tool == "Bash" and inp.get("command"):
                    return f"$ {inp['command']}", "tool"
                for key in ("description", "prompt", "file_path", "path", "query", "pattern"):
                    v = inp.get(key)
                    if v:
                        return f"{tool}: {v}", "tool"
            return f"{tool} call", "tool"
    return "untitled", "none"


# Plan 041 tier priority for picking the "best" git_link to display.
_TIER_PRIORITY = {
    "tool_emitted": 0,
    "tool_emitted_with_divergence": 1,
    "overlapping": 2,
    "orphan": 3,
}

_TIER_GLYPH = {
    "tool_emitted": ("✓", "green"),
    "tool_emitted_with_divergence": ("~", "yellow"),
    "overlapping": ("?", "bright_black"),
    "orphan": ("·", "bright_black"),
}


def _git_chip(record) -> tuple[str, str, str] | None:
    """Return (glyph, short_sha, color) for the best git_link, or None."""
    links = getattr(record, "git_links", None) or []
    if not links:
        return None
    best = min(links, key=lambda link: _TIER_PRIORITY.get(getattr(link, "tier", "orphan"), 99))
    sha = (getattr(best, "revision", "") or "")[:7]
    glyph, color = _TIER_GLYPH.get(best.tier, ("·", "bright_black"))
    return (glyph, sha, color)


def _status_cell(entry, record) -> tuple[str, str]:
    """Git-log-style status combining workflow stage + outcome.

    Returns (rich_markup, plain_text) so callers can render or emit JSON
    without re-deriving or stripping markup.
    """
    from ..core.workflow import resolve_visible_stage

    visible = resolve_visible_stage(entry.status if entry else None)
    if visible == "pushed":
        return "[green bold]✓ pushed[/]", "pushed"
    if visible == "rejected":
        return "[red]✗ rejected[/]", "rejected"
    if visible == "staged":
        return "[green]✓ staged[/]", "staged"

    # inbox — differentiate by outcome signals
    outcome = getattr(record, "outcome", None)
    if outcome:
        terminal = getattr(outcome, "terminal_state", None)
        success = getattr(outcome, "success", None)
        if success is False or terminal == "error":
            return "[red]✗ failed[/]", "failed"
        if terminal == "compacted":
            return "[yellow]~ compacted[/]", "compacted"
        if getattr(outcome, "committed", False):
            return "[green]✓ done[/]", "done"
    return "[dim]○ open[/]", "open"
