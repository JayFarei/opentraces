"""App-level Textual messages for the OpenTraces TUI."""

from __future__ import annotations

from typing import Any

from textual.message import Message


class StageChanged(Message):
    """Fired when a trace's stage changes."""

    def __init__(self, trace_id: str, old_stage: str, new_stage: str) -> None:
        super().__init__()
        self.trace_id = trace_id
        self.old_stage = old_stage
        self.new_stage = new_stage


class TraceSelected(Message):
    """Fired when user selects a trace in the inbox."""

    def __init__(self, trace_id: str) -> None:
        super().__init__()
        self.trace_id = trace_id


class TraceDeselected(Message):
    """Fired when the current trace selection is cleared."""

    pass


class RefreshRequested(Message):
    """Request a data refresh (e.g., after mutation)."""

    def __init__(self, select_trace_id: str | None = None) -> None:
        super().__init__()
        self.select_trace_id = select_trace_id


class TraceLoaded(Message):
    """Fired when a full trace has been loaded from disk."""

    def __init__(self, trace_id: str, trace: dict[str, Any]) -> None:
        super().__init__()
        self.trace_id = trace_id
        self.trace = trace


class FlashMessage(Message):
    """Show a timed flash in the KeyBar."""

    def __init__(self, text: str, duration: float = 3.0) -> None:
        super().__init__()
        self.text = text
        self.duration = duration
