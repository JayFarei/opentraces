from __future__ import annotations

from textual.widgets import Static


class HelpOverlay(Static):
    """Full-screen help overlay with grouped keybindings."""

    HELP_TEXT = (
        "[bold underline]Keyboard Reference[/bold underline]\n"
        "\n"
        "[bold]Navigation[/bold]\n"
        "  [bold]j[/bold] / [bold]k[/bold]          Move down / up\n"
        "  [bold]Enter[/bold]            Open / expand selected item\n"
        "  [bold]Esc[/bold]              Go back / close overlay\n"
        "  [bold]1[/bold] / [bold]2[/bold] / [bold]3[/bold]      Switch tab (sessions / summary / detail)\n"
        "\n"
        "[bold]Curation[/bold]\n"
        "  [bold]c[/bold]                Commit selected session for push\n"
        "  [bold]r[/bold]                Reject selected session\n"
        "  [bold]d[/bold]                Discard (delete staging file + state)\n"
        "  [bold]p[/bold]                Push committed traces\n"
        "\n"
        "[bold]Search & Filter[/bold]\n"
        "  [bold]/[/bold]                Filter by text\n"
        "  [bold]f[/bold]                Structured filter (agent, date, flags)\n"
        "\n"
        "[bold]Inspection[/bold]\n"
        "  [bold]y[/bold]                Copy selection to clipboard\n"
        "  [bold]Tab[/bold]              Switch analysis panel\n"
        "\n"
        "[bold]General[/bold]\n"
        "  [bold]?[/bold]                Toggle this help overlay\n"
        "  [bold]q[/bold]                Quit\n"
        "\n"
        "[dim]Press [bold]?[/bold] to close[/dim]"
    )

    def __init__(self, **kwargs: object) -> None:
        super().__init__(self.HELP_TEXT, markup=True, **kwargs)
        self.display = False

    def toggle(self) -> None:
        self.display = not self.display
