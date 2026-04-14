"""Hidden ``ot __complete`` dynamic-completion resolver.

Installed shell scripts delegate every completion query here. The script
walks the Click command tree based on the partial argv and prints newline-
separated candidates (subcommand names and option flags).
"""

from __future__ import annotations

import click


def _walk(root: click.Command, tokens: list[str]) -> tuple[click.Command, str]:
    """Walk the command tree following ``tokens``; return (command, partial).

    The final token (possibly empty) is treated as the in-progress word.
    """
    if not tokens:
        return root, ""
    current: click.Command = root
    # Consume full tokens that match subcommand names; the last token is the
    # partial being completed.
    for i, tok in enumerate(tokens[:-1]):
        if isinstance(current, click.Group) and tok in current.commands:
            current = current.commands[tok]
        else:
            # Unknown token, stop descending; remaining tokens considered args.
            return current, tokens[-1]
    return current, tokens[-1]


def _candidates(cmd: click.Command, partial: str) -> list[str]:
    out: list[str] = []
    if isinstance(cmd, click.Group):
        for name, sub in cmd.commands.items():
            if getattr(sub, "hidden", False):
                continue
            if name.startswith(partial):
                out.append(name)
    # Option flags
    if partial.startswith("-") or partial == "":
        for param in cmd.params:
            if isinstance(param, click.Option):
                for opt in param.opts + param.secondary_opts:
                    if opt.startswith(partial):
                        out.append(opt)
    return sorted(set(out))


@click.command(
    "__complete",
    hidden=True,
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("tokens", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def complete_cmd(ctx: click.Context, tokens: tuple[str, ...]) -> None:
    """Resolve completions for the given partial command line."""
    # Lazy import to avoid circular imports at module load time.
    from . import main as root

    cmd, partial = _walk(root, list(tokens))
    for c in _candidates(cmd, partial):
        click.echo(c)
