"""Structured filter popup overlay, activated by f.

From agensic brief 26: centered overlay (58% wide, 42% tall) with filterable
fields. Left/Right cycles through available enum values per field. Up/Down
moves cursor between fields. Enter/Esc closes.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Static


class FilterApplied(Message):
    """Posted when the user confirms filter selections."""

    def __init__(self, filters: dict[str, str | None]) -> None:
        super().__init__()
        self.filters = filters


class FilterPopup(ModalScreen[dict[str, str | None]]):
    """Structured filter popup with enum cycling per field."""

    BINDINGS = [
        Binding("escape", "dismiss_popup", "Close", priority=True),
        Binding("enter", "apply_filter", "Apply", priority=True),
        Binding("j", "cursor_down", "Down", show=False, priority=True),
        Binding("k", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("left", "cycle_left", "Prev", show=False, priority=True),
        Binding("right", "cycle_right", "Next", show=False, priority=True),
        Binding("r", "reset_all", "Reset", show=False, priority=True),
    ]

    def __init__(
        self,
        stages: list[str],
        agents: list[str],
        models: list[str],
        current_filters: dict[str, str | None] | None = None,
    ) -> None:
        super().__init__()
        self._fields: list[tuple[str, list[str]]] = [
            ("stage", ["(all)"] + stages),
            ("agent", ["(all)"] + agents),
            ("model", ["(all)"] + models),
        ]
        self._cursor = 0
        self._selections: dict[str, int] = {}
        # Restore previous selections
        if current_filters:
            for field_name, options in self._fields:
                val = current_filters.get(field_name)
                if val and val in options:
                    self._selections[field_name] = options.index(val)

    def compose(self) -> ComposeResult:
        with Vertical(id="filter-popup-container"):
            yield Static("[bold]Filter Sessions[/bold]", id="filter-title", markup=True)
            yield Static("", id="filter-body", markup=True)
            yield Static(
                "[dim]j/k[/dim] move   [dim]←/→[/dim] cycle   "
                "[dim]r[/dim] reset   [dim]enter[/dim] apply   [dim]esc[/dim] close",
                id="filter-hints",
                markup=True,
            )

    def on_mount(self) -> None:
        self._render_fields()

    def _render_fields(self) -> None:
        lines: list[str] = []
        for i, (field_name, options) in enumerate(self._fields):
            idx = self._selections.get(field_name, 0)
            value = options[idx]
            prefix = "[bold #F97316]>[/bold #F97316] " if i == self._cursor else "  "
            if i == self._cursor:
                lines.append(
                    f"{prefix}[bold]{field_name}[/bold]  "
                    f"[#22D3EE bold]< {value} >[/#22D3EE bold]"
                )
            else:
                lines.append(
                    f"{prefix}[dim]{field_name}[/dim]  {value}"
                )
        self.query_one("#filter-body", Static).update("\n".join(lines))

    def _current_field(self) -> tuple[str, list[str]]:
        return self._fields[self._cursor]

    def action_cursor_down(self) -> None:
        self._cursor = min(self._cursor + 1, len(self._fields) - 1)
        self._render_fields()

    def action_cursor_up(self) -> None:
        self._cursor = max(self._cursor - 1, 0)
        self._render_fields()

    def action_cycle_right(self) -> None:
        field_name, options = self._current_field()
        idx = self._selections.get(field_name, 0)
        self._selections[field_name] = (idx + 1) % len(options)
        self._render_fields()

    def action_cycle_left(self) -> None:
        field_name, options = self._current_field()
        idx = self._selections.get(field_name, 0)
        self._selections[field_name] = (idx - 1) % len(options)
        self._render_fields()

    def action_reset_all(self) -> None:
        self._selections.clear()
        self._render_fields()

    def action_apply_filter(self) -> None:
        result: dict[str, str | None] = {}
        for field_name, options in self._fields:
            idx = self._selections.get(field_name, 0)
            value = options[idx]
            result[field_name] = None if value == "(all)" else value
        self.dismiss(result)

    def action_dismiss_popup(self) -> None:
        self.dismiss(None)
