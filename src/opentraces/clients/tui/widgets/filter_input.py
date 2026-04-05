"""Inline text filter for the session list, activated by /."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Input, Static


class FilterChanged(Message):
    """Posted when the filter text changes."""

    def __init__(self, value: str) -> None:
        super().__init__()
        self.value = value


class FilterClosed(Message):
    """Posted when the filter input is dismissed."""


class FilterInput(Horizontal):
    """Inline text filter bar, shown/hidden on demand."""

    BINDINGS = [
        Binding("escape", "close_filter", "Close", priority=True),
    ]

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.display = False

    def compose(self) -> ComposeResult:
        yield Static("[bold]/[/bold] ", markup=True, classes="filter-prefix")
        yield Input(placeholder="filter sessions...", id="filter-input")

    def open(self) -> None:
        """Show the filter bar and focus the input."""
        self.display = True
        inp = self.query_one("#filter-input", Input)
        inp.value = ""
        inp.focus()

    def close(self) -> None:
        """Hide the filter bar and clear the filter."""
        self.display = False
        self.post_message(FilterChanged(""))
        self.post_message(FilterClosed())

    def action_close_filter(self) -> None:
        self.close()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.post_message(FilterChanged(event.value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter confirms filter and returns focus to list
        self.post_message(FilterClosed())
        self.display = False
