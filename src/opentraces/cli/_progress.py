"""CLI-side progress wiring for the shared ``--progress`` contract (issue #88).

This is the only layer that knows about Click. It owns:

* ``PROGRESS_CHOICE`` + the reusable ``--progress`` option decorator, so future
  long-running commands adopt the contract without reinventing the flag.
* ``build_cli_progress(command, mode)`` — turns a mode string into a concrete
  reporter whose emit sink writes to **stderr** via ``click.echo(..., err=True)``.
  We deliberately route through ``click.echo(err=True)`` rather than binding
  ``sys.stderr`` at import time: ``click.echo`` resolves Click's *current*
  stderr on every call, so ``CliRunner`` stderr capture (and any ``2>&1``
  redirect) sees the lines. stdout is never touched — the ``--json`` payload
  stays a single clean object.

Modes:

* ``auto``  → ``plain`` when stderr is an interactive TTY (resolved at call
  time), else ``never`` — so CI / agents are quiet by default and opt in.
* ``plain`` → human stage/heartbeat lines on stderr.
* ``json``  → JSONL events on stderr.
* ``never`` → :class:`NullProgress` (no thread, no output).
"""

from __future__ import annotations

import sys
from typing import Callable

import click

from ..core.progress import (
    NullProgress,
    ProgressLike,
    ProgressReporter,
    render_json,
    render_plain,
)

PROGRESS_MODES = ("auto", "plain", "json", "never")
PROGRESS_CHOICE = click.Choice(list(PROGRESS_MODES))

_PROGRESS_HELP = (
    "Progress reporting for this long-running build. 'auto' (default) prints "
    "human progress to stderr only when stderr is a TTY and stays quiet in "
    "CI / piped runs; 'plain' forces human stderr lines; 'json' emits stable "
    "{\"event\":\"progress\",...} JSONL on stderr for agents; 'never' disables "
    "it. Progress is stderr-only — stdout (incl. --json) is never polluted, "
    "and read-only 'trace query' is unaffected."
)


def _stderr_isatty() -> bool:
    """Whether the *current* stderr is an interactive terminal (call-time)."""

    try:
        return bool(sys.stderr.isatty())
    except (AttributeError, ValueError):
        return False


def progress_option(func: Callable) -> Callable:
    """Reusable ``--progress`` option decorator (the shared facility)."""

    return click.option(
        "--progress",
        "progress_mode",
        type=PROGRESS_CHOICE,
        default="auto",
        show_default=True,
        help=_PROGRESS_HELP,
    )(func)


def build_cli_progress(command: str, mode: str) -> ProgressLike:
    """Build a reporter for ``command`` from a ``--progress`` mode string."""

    resolved = mode
    if resolved == "auto":
        resolved = "plain" if _stderr_isatty() else "never"

    if resolved == "never":
        return NullProgress()

    if resolved == "json":
        def _emit(event: dict) -> None:
            click.echo(render_json(event), err=True)
    else:  # plain
        def _emit(event: dict) -> None:
            click.echo(render_plain(event), err=True)

    return ProgressReporter(command, emit=_emit)
