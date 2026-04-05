"""Proportional timing bar for a single step, inspired by otel-tui's Gantt timeline."""

from __future__ import annotations

from typing import Any

from textual.widgets import Static

from ..utils import _parse_iso, _tool_color_ansi

# Unicode block characters for sub-cell rendering
_FULL_BLOCK = "\u2588"
_PARTIAL_BLOCKS = [
    " ",        # 0/8
    "\u258f",   # 1/8
    "\u258e",   # 2/8
    "\u258d",   # 3/8
    "\u258c",   # 4/8
    "\u258b",   # 5/8
    "\u258a",   # 6/8
    "\u2589",   # 7/8
]


def _tool_color_for_step(step: dict[str, Any]) -> str:
    """Pick a color based on the step's primary tool or role."""
    tool_calls = step.get("tool_calls", [])
    if tool_calls:
        return _tool_color_ansi(tool_calls[0].get("tool_name", ""))
    role = step.get("role", "")
    if role == "user":
        return "#F97316"
    if role == "system":
        return "#666666"
    return "#22D3EE"


class TimingBar(Static):
    """Proportional timing bar for a single step.

    Implementation follows otel-tui's timeline rendering:
    left = (step_start - trace_start) / trace_duration * width
    bar_width = step_duration / trace_duration * width
    """

    def __init__(
        self,
        step: dict[str, Any],
        trace_start_iso: str | None,
        trace_end_iso: str | None,
        bar_width: int = 40,
        **kwargs: object,
    ) -> None:
        super().__init__("", markup=True, **kwargs)
        self._step = step
        self._trace_start_iso = trace_start_iso
        self._trace_end_iso = trace_end_iso
        self._bar_width = bar_width

    def on_mount(self) -> None:
        self._render_bar()

    def _render_bar(self) -> None:
        trace_start = _parse_iso(self._trace_start_iso)
        trace_end = _parse_iso(self._trace_end_iso)
        step_ts = _parse_iso(self._step.get("timestamp"))

        if not trace_start or not trace_end or not step_ts:
            self.update("[dim]" + "." * self._bar_width + "[/dim]")
            return

        trace_dur = (trace_end - trace_start).total_seconds()
        if trace_dur <= 0:
            self.update("[dim]" + "." * self._bar_width + "[/dim]")
            return

        # Estimate step duration from tool calls or default 1s
        tool_calls = self._step.get("tool_calls", [])
        step_dur_ms = sum(tc.get("duration_ms", 0) or 0 for tc in tool_calls)
        step_dur = step_dur_ms / 1000.0 if step_dur_ms > 0 else 1.0

        offset_s = max(0, (step_ts - trace_start).total_seconds())
        left_frac = offset_s / trace_dur
        bar_frac = min(step_dur / trace_dur, 1.0 - left_frac)

        left_cells = left_frac * self._bar_width
        bar_cells = max(0.125, bar_frac * self._bar_width)

        color = _tool_color_for_step(self._step)

        # Build character-cell line
        chars: list[str] = []
        for i in range(self._bar_width):
            cell_start = float(i)
            cell_end = float(i + 1)
            bar_start = left_cells
            bar_end = left_cells + bar_cells

            if cell_end <= bar_start or cell_start >= bar_end:
                chars.append(" ")
            elif cell_start >= bar_start and cell_end <= bar_end:
                chars.append(_FULL_BLOCK)
            else:
                # Partial overlap
                overlap = min(cell_end, bar_end) - max(cell_start, bar_start)
                eighth = int(overlap * 8)
                eighth = max(0, min(7, eighth))
                chars.append(_PARTIAL_BLOCKS[eighth])

        bar_text = "".join(chars)
        self.update(f"[{color}]{bar_text}[/{color}]")
