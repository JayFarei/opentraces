"""Context-sensitive keybinding footer with timed flash feedback."""

from __future__ import annotations

import time
from typing import Any

from rich.text import Text
from textual.reactive import reactive
from textual.visual import RichVisual
from textual.widget import Widget


# Default hint text shown before set_mode is called
_DEFAULT_HINTS = (
    "[bold]j/k[/bold] move   [bold]enter[/bold] open   "
    "[bold]c[/bold] commit   [bold]r[/bold] reject   "
    "[bold]?[/bold] help   [bold]q[/bold] quit"
)


class KeyBar(Widget):
    """Context-sensitive keybinding footer with timed flash feedback.

    Uses render() instead of Static.update() to avoid visual=None race
    during screen push lifecycle in Textual 8.x.
    """

    DEFAULT_CSS = """
    KeyBar {
        height: 2;
        padding: 0 2;
        dock: bottom;
    }
    """

    _display_text: reactive[str] = reactive(_DEFAULT_HINTS, layout=True)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._flash_text = ""
        self._flash_expires = 0.0
        self._mode_hints = _DEFAULT_HINTS

    def render(self) -> Text:
        return Text.from_markup(self._display_text)

    def set_mode(self, mode: str) -> None:
        """Update keybinding hints for current mode."""
        hints = {
            "inbox": (
                "[bold]j/k[/bold] move   [bold]enter[/bold] open   "
                "[bold]c[/bold] commit   [bold]r[/bold] reject   "
                "[bold]d[/bold] discard   [bold]p[/bold] push   "
                "[bold]/[/bold] filter   [bold]f[/bold] filter+   "
                "[bold]?[/bold] help   [bold]q[/bold] quit"
            ),
            "trace": (
                "[bold]j/k[/bold] navigate   [bold]enter[/bold] expand/collapse   "
                "[bold]y[/bold] copy   "
                "[bold]?[/bold] help   [bold]esc[/bold] back"
            ),
            "trace_transcript": (
                "[bold]j/k[/bold] navigate   [bold]enter[/bold] expand/collapse   "
                "[bold]y[/bold] copy   "
                "[bold]?[/bold] help   [bold]esc[/bold] back"
            ),
            "trace_analysis": (
                "[bold]j/k[/bold] scroll   [bold]enter[/bold] expand   "
                "[bold]tab[/bold] transcript   "
                "[bold]y[/bold] copy   "
                "[bold]?[/bold] help   [bold]esc[/bold] back"
            ),
        }
        self._mode_hints = hints.get(mode, hints["inbox"])
        self._refresh_bar()

    def flash(self, text: str, duration: float = 3.0) -> None:
        """Show a timed flash message that auto-expires."""
        self._flash_text = text
        self._flash_expires = time.monotonic() + duration
        self._refresh_bar()
        self.set_timer(duration, self._clear_flash)

    def _clear_flash(self) -> None:
        if time.monotonic() >= self._flash_expires:
            self._flash_text = ""
            self._refresh_bar()

    def _refresh_bar(self) -> None:
        if self._flash_text:
            self._display_text = f"{self._flash_text}  [dim]|[/dim]  {self._mode_hints}"
        else:
            self._display_text = self._mode_hints or " "
