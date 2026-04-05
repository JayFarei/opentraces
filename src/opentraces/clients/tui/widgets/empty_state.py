from __future__ import annotations

from textual.widgets import Static

OPENTRACES_ASCII = r"""
  ___  _____
 / _ \|_   _|
| | | | | |
| |_| | | |
 \___/  |_|
 OPENTRACES
""".strip("\n")


class EmptyState(Static):
    """Empty state placeholder with contextual hint text."""

    def __init__(self, screen_name: str = "inbox", **kwargs: object) -> None:
        hints = {
            "inbox": (
                f"[bold cyan]{OPENTRACES_ASCII}[/bold cyan]\n\n"
                "[bold]No sessions in this inbox[/bold]\n\n"
                "[dim]Run [bold]opentraces parse[/bold] to get started.[/dim]\n"
                "[dim]Sessions will appear here after parsing agent traces.[/dim]"
            ),
            "inspect": (
                "[bold]No trace selected[/bold]\n\n"
                "[dim]Select a session from the inbox to inspect.[/dim]"
            ),
            "pipeline": (
                "[bold]No pipeline data[/bold]\n\n"
                "[dim]Run [bold]opentraces parse[/bold] and [bold]opentraces push[/bold] "
                "to see pipeline activity.[/dim]"
            ),
        }
        super().__init__(hints.get(screen_name, hints["inbox"]), markup=True, **kwargs)
