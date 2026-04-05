"""Collapsible sidebar with panels, following Toad's pattern."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class SideBarPanel(Vertical):
    """A single collapsible panel within the sidebar."""

    collapsed: reactive[bool] = reactive(False)

    def __init__(
        self,
        title: str,
        *children: Widget,
        collapsed: bool = False,
        **kwargs: object,
    ) -> None:
        super().__init__(*children, **kwargs)
        self._title = title
        self.collapsed = collapsed

    def compose(self) -> ComposeResult:
        indicator = "\u25b6" if self.collapsed else "\u25bc"
        yield Static(
            f"[bold]{indicator} {self._title}[/bold]",
            markup=True,
            classes="sidebar-panel-title",
        )

    def watch_collapsed(self, collapsed: bool) -> None:
        """Toggle visibility of panel content."""
        indicator = "\u25b6" if collapsed else "\u25bc"
        try:
            title = self.query_one(".sidebar-panel-title", Static)
            title.update(f"[bold]{indicator} {self._title}[/bold]")
        except Exception:
            pass
        # Hide all children except the title
        for child in self.children:
            if not child.has_class("sidebar-panel-title"):
                child.display = not collapsed

    def toggle(self) -> None:
        self.collapsed = not self.collapsed


class SideBar(Vertical):
    """Collapsible sidebar with multiple panels."""

    BINDINGS = [
        Binding("left", "collapse_sidebar", "Collapse", show=False),
    ]

    expanded: reactive[bool] = reactive(True)
    DEFAULT_CSS = """
    SideBar {
        width: 40;
        min-width: 28;
        dock: left;
        background: $ot-panel;
        border-right: solid $border;
    }
    SideBar.-collapsed {
        width: 0;
        min-width: 0;
        display: none;
    }
    .sidebar-panel-title {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """

    def watch_expanded(self, expanded: bool) -> None:
        if expanded:
            self.remove_class("-collapsed")
        else:
            self.add_class("-collapsed")

    def action_collapse_sidebar(self) -> None:
        self.expanded = not self.expanded

    def toggle(self) -> None:
        self.expanded = not self.expanded
