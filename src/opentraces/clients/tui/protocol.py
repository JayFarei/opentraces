"""Structural protocols for the TUI widget system."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, runtime_checkable

from textual.widget import Widget


@dataclass
class MenuItem:
    """A single entry in a block's context menu."""

    label: str
    action: str
    key: str | None = None


@runtime_checkable
class BlockProtocol(Protocol):
    """Enables outer cursor navigation between top-level blocks."""

    def block_cursor_up(self) -> Widget | None: ...

    def block_cursor_down(self) -> Widget | None: ...

    def get_cursor_block(self) -> Widget | None: ...

    def block_cursor_clear(self) -> None: ...


@runtime_checkable
class ExpandProtocol(Protocol):
    """Enables expand/collapse on blocks."""

    def can_expand(self) -> bool: ...

    def expand_block(self) -> None: ...

    def collapse_block(self) -> None: ...

    def is_block_expanded(self) -> bool: ...


@runtime_checkable
class MenuProtocol(Protocol):
    """Per-widget context menu."""

    def get_block_menu(self) -> Iterable[MenuItem]: ...

    def get_block_content(self, destination: str) -> str | None: ...
