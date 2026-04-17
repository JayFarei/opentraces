"""Canonical --help layout for opentraces CLI commands.

Every command and group inherits ``OpentracesCommand`` / ``OpentracesGroup``
so every help screen looks the same: ASCII banner, tagline, a filled
command-title bar, usage, description, dim rule, then the body (options,
examples, see-also, subcommands). Section headers are bold; command and
option names are cyan. Individual commands pass structured metadata via
decorator kwargs:

    @main.command(
        "push",
        examples=[
            "opentraces push",
            "opentraces push --private",
        ],
        see_also=[
            ("opentraces assess", "score traces before upload"),
        ],
        option_groups=[
            ("Visibility", ["private", "public"]),
        ],
    )

Kwargs not recognized by vanilla ``click.Command.__init__`` flow through to
``OpentracesCommand.__init__`` courtesy of ``click.decorators.command``.
Click ``HelpFormatter`` strips ANSI when stdout is not a TTY and honors
``NO_COLOR``, so the same output is safe in pipes, CI logs, and test runners.
"""
from __future__ import annotations

import click


def _bold(text: str) -> str:
    return click.style(text, bold=True)


def _cyan(text: str) -> str:
    return click.style(text, fg="cyan", bold=True)


def _command_path(ctx: click.Context) -> str:
    """Full invocation path for display, e.g. 'opentraces setup claude-code'.

    Walks the Click context chain and normalizes the root to ``opentraces``
    regardless of how the program was launched (``ot``, ``python -m
    opentraces``, etc.), so help screens always read the same.
    """
    parts: list[str] = []
    c: click.Context | None = ctx
    while c is not None:
        parts.append(c.info_name or "")
        c = c.parent
    parts.reverse()
    if parts:
        parts[0] = "opentraces"
    return " ".join(p for p in parts if p)


def _render_header(ctx: click.Context, formatter: click.HelpFormatter) -> None:
    """Write the title bar (and, at the root, the banner) for help screens.

    The ASCII banner + tagline only render for ``opentraces --help`` since
    they're branding. Every subcommand/group gets just the highlighted
    title bar, which is the space-efficient "you are here" anchor.
    """
    from ..core.workflow import OPENTRACES_ASCII, OPENTRACES_TAGLINE
    from .. import __version__

    is_root = ctx.parent is None
    if is_root:
        formatter.write(click.style(OPENTRACES_ASCII, dim=True) + "\n")
        formatter.write(
            "\n  " + click.style(OPENTRACES_TAGLINE, bold=True)
            + click.style(f"   v{__version__}", dim=True)
            + "\n\n"
        )
    path = _command_path(ctx)
    # Filled menu-title: reverse-video cyan bar. Click strips ANSI when
    # stdout is not a TTY, so the same output works in pipes.
    bar = click.style(f"  {path}  ", fg="black", bg="cyan", bold=True)
    prefix = "  " if is_root else ""
    formatter.write(prefix + bar + "\n\n")


class OpentracesCommand(click.Command):
    """Click ``Command`` with canonical help layout.

    Accepts three extra kwargs beyond ``click.Command``:
      * ``examples``      — list of command-line strings rendered under
                            an ``Examples:`` section.
      * ``see_also``      — list of ``(command, blurb)`` pairs rendered
                            under a ``See also:`` section.
      * ``option_groups`` — list of ``(heading, [option_names])`` tuples
                            for grouping options. Any options not placed
                            by a group fall under a residual ``Options:``
                            section.
    """

    def __init__(
        self,
        *args,
        examples: list[str] | None = None,
        see_also: list[tuple[str, str]] | None = None,
        option_groups: list[tuple[str, list[str]]] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.examples = list(examples or [])
        self.see_also = list(see_also or [])
        self.option_groups = list(option_groups or [])

    def _section(self, formatter: click.HelpFormatter, title: str):
        return formatter.section(_bold(title))

    def format_help(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        _render_header(ctx, formatter)
        self.format_usage(ctx, formatter)
        self.format_help_text(ctx, formatter)
        self.format_options(ctx, formatter)
        self.format_epilog(ctx, formatter)

    def format_help_text(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        super().format_help_text(ctx, formatter)
        # Dim horizontal rule separates the header block (Usage +
        # description) from the body (options, examples, see-also).
        # Rendered only when the command has a docstring so pure groups
        # with no description don't emit an orphan rule.
        if self.help:
            formatter.write_paragraph()
            rule_width = min(formatter.width, 72)
            formatter.write(click.style("─" * rule_width, dim=True) + "\n")

    def format_options(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        params = [p for p in self.get_params(ctx) if isinstance(p, click.Option)]

        def rows_for(opts: list[click.Option]) -> list[tuple[str, str]]:
            rows: list[tuple[str, str]] = []
            for opt in opts:
                rv = opt.get_help_record(ctx)
                if rv is not None:
                    rows.append(rv)
            return rows

        if self.option_groups:
            by_name = {p.name: p for p in params}
            placed: set[str] = set()
            for heading, names in self.option_groups:
                group_opts = [by_name[n] for n in names if n in by_name]
                placed.update(o.name for o in group_opts)
                rows = rows_for(group_opts)
                if rows:
                    with self._section(formatter, heading):
                        formatter.write_dl(rows)
            residual = [p for p in params if p.name not in placed]
            rows = rows_for(residual)
            if rows:
                with self._section(formatter, "Options"):
                    formatter.write_dl(rows)
            return

        rows = rows_for(params)
        if rows:
            with self._section(formatter, "Options"):
                formatter.write_dl(rows)

    def format_epilog(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        if self.examples:
            with self._section(formatter, "Examples"):
                # Raw-write so long one-liners don't get word-wrapped in the
                # middle of a command; Click's write_text wraps at the column
                # width and makes examples hard to copy-paste.
                indent = " " * formatter.current_indent
                for ex in self.examples:
                    formatter.write(f"{indent}$ {ex}\n")
        if self.see_also:
            with self._section(formatter, "See also"):
                rows = [(_cyan(cmd), hint) for cmd, hint in self.see_also]
                formatter.write_dl(rows)
        # Preserve any literal ``epilog=`` passed through vanilla Click.
        if self.epilog:
            super().format_epilog(ctx, formatter)


class OpentracesGroup(click.Group, OpentracesCommand):
    """Group with the canonical layout and colored command listing.

    Sets ``command_class`` / ``group_class`` so every descendant command
    or group inherits the template without the call-site having to pass
    ``cls=`` on each ``@group.command`` decoration.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.command_class = OpentracesCommand
        self.group_class = OpentracesGroup

    def format_help(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        _render_header(ctx, formatter)
        self.format_usage(ctx, formatter)
        self.format_help_text(ctx, formatter)
        self.format_options(ctx, formatter)
        self.format_commands(ctx, formatter)
        self.format_epilog(ctx, formatter)

    def format_options(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        # Bypass ``MultiCommand.format_options`` — it secretly calls
        # ``format_commands`` at the end, which would double-render the
        # command listing since we call ``format_commands`` explicitly in
        # ``format_help``. Delegate to ``OpentracesCommand.format_options``
        # directly for the options-only half.
        OpentracesCommand.format_options(self, ctx, formatter)

    def format_commands(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        rows: list[tuple[str, str]] = []
        for name in self.list_commands(ctx):
            cmd = self.get_command(ctx, name)
            if cmd is None or cmd.hidden:
                continue
            help_str = cmd.get_short_help_str(limit=formatter.width) or ""
            rows.append((_cyan(name), help_str))
        if rows:
            with self._section(formatter, "Commands"):
                formatter.write_dl(rows)
