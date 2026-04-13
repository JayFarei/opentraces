"""Semantic color themes for CLI output.

Commands paint with role names (``primary``, ``accent``, ``tier.emitted`` …)
rather than literal colors. Swapping palettes only requires changing the
mapping below — call sites stay identical.

Theme selection, in order of precedence:
  1. explicit ``--theme`` flag on a command
  2. ``$OT_THEME`` env var (``light`` / ``dark`` / ``auto``)
  3. ``auto``: inspect ``$COLORFGBG`` (many terminals set it as ``fg;bg``;
     bg >= 7 is a light palette), else ``dark``.
"""

from __future__ import annotations

import os
from typing import Literal

ThemeName = Literal["dark", "light"]


# -- Palettes --------------------------------------------------------------

DARK_STYLES: dict[str, str] = {
    # semantic roles
    "primary":        "cyan",
    "accent":         "green",
    "warning":        "yellow",
    "error":          "red",
    "muted":          "bright_black",
    "strong":         "bold",
    # stack-graph glyphs (GitButler-inspired)
    "stack.head":     "cyan bold",
    "stack.body":     "bright_black",
    "stack.rule":     "bright_black",
    "stack.base":     "green bold",
    "stack.label":    "cyan dim",
    # commit / trace identity
    "commit.sha":     "green bold",
    "commit.subject": "white",
    "trace.id":       "cyan bold",
    # plan-041 tiers
    "tier.emitted":   "green",
    "tier.diverged":  "yellow",
    "tier.overlap":   "bright_black",
    "tier.orphan":    "bright_black",
    # review stages (status column)
    "stage.inbox":    "yellow",
    "stage.committed":"cyan",
    "stage.pushed":   "green",
    "stage.rejected": "red",
}

LIGHT_STYLES: dict[str, str] = {
    "primary":        "blue",
    "accent":         "dark_green",
    "warning":        "dark_orange3",
    "error":          "red",
    "muted":          "grey50",
    "strong":         "bold",
    "stack.head":     "blue bold",
    "stack.body":     "grey50",
    "stack.rule":     "grey50",
    "stack.base":     "dark_green bold",
    "stack.label":    "blue dim",
    "commit.sha":     "dark_green bold",
    "commit.subject": "black",
    "trace.id":       "blue bold",
    "tier.emitted":   "dark_green",
    "tier.diverged":  "dark_orange3",
    "tier.overlap":   "grey50",
    "tier.orphan":    "grey50",
    "stage.inbox":    "dark_orange3",
    "stage.committed":"blue",
    "stage.pushed":   "dark_green",
    "stage.rejected": "red",
}


# -- Resolution ------------------------------------------------------------

def _auto_detect() -> ThemeName:
    """Best-effort terminal background detection via COLORFGBG."""
    fgbg = os.environ.get("COLORFGBG", "")
    if ";" in fgbg:
        try:
            bg = int(fgbg.split(";")[-1])
            return "light" if bg >= 7 else "dark"
        except ValueError:
            pass
    return "dark"


def resolve_theme(name: str | None = None) -> ThemeName:
    """Pick a theme honoring explicit value, ``$OT_THEME``, then auto."""
    for candidate in (name, os.environ.get("OT_THEME")):
        if not candidate:
            continue
        candidate = candidate.strip().lower()
        if candidate in ("dark", "light"):
            return candidate  # type: ignore[return-value]
        if candidate == "auto":
            return _auto_detect()
    return _auto_detect()


def get_palette(name: str | None = None) -> dict[str, str]:
    return LIGHT_STYLES if resolve_theme(name) == "light" else DARK_STYLES


# -- Rich integration ------------------------------------------------------

def get_rich_theme(name: str | None = None):
    """Return a ``rich.theme.Theme`` for the resolved palette."""
    from rich.theme import Theme
    return Theme(get_palette(name))


def get_console(
    *,
    name: str | None = None,
    file=None,
    force_terminal: bool | None = None,
    width: int | None = None,
):
    """Factory for a themed ``rich.console.Console``."""
    from rich.console import Console
    kwargs: dict = {"theme": get_rich_theme(name)}
    if file is not None:
        kwargs["file"] = file
    if force_terminal is not None:
        kwargs["force_terminal"] = force_terminal
        kwargs["color_system"] = "truecolor" if force_terminal else None
    if width is not None:
        kwargs["width"] = width
    return Console(**kwargs)
