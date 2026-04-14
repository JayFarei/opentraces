"""Agent-specific resume helpers for ``ot resume``.

Each agent we capture has its own native way of picking up a previous
session. Claude Code takes ``claude --resume <session_id>``; others are
printed as a hint fallback until we wire them up explicitly.

The claude-code path uses ``os.execvp`` so the opentraces process is
replaced by ``claude`` — the user lands straight in the REPL, no
dangling subprocess. For scripts / tests / smoke, ``dry_run=True``
prints the resolved argv and returns 0 without exec'ing.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import click


def resume_claude_code(
    session_id: str,
    *,
    project_cwd: Path | None = None,
    dry_run: bool = False,
    trace_id: str | None = None,
    short_name: str | None = None,
) -> int:
    """Execvp into ``claude --resume <session_id>``.

    Returns an exit code (caller should ``sys.exit`` with it). On the
    happy path this function does not return — execvp replaces the
    process. Returns 127 if ``claude`` is not on PATH, 0 for dry-run.
    """
    cmd = shutil.which("claude")
    if not cmd:
        click.echo(
            "claude CLI not on PATH. Install Claude Code first "
            "(https://docs.claude.com/en/docs/claude-code).",
            err=True,
        )
        click.echo(f"Once installed, run: claude --resume {session_id}", err=True)
        return 127

    argv = [cmd, "--resume", session_id]
    if dry_run:
        click.echo(" ".join(argv))
        return 0

    # One-line summary so the user sees what we're handing control to
    # before exec replaces the process. Stderr keeps stdout clean for
    # any redirected pipeline. Best-effort — never abort the resume.
    if trace_id:
        try:
            label = short_name or "untitled"
            click.echo(
                f"Resuming claude-code session for trace {trace_id[:8]} ({label})",
                err=True,
            )
        except Exception:
            pass

    if project_cwd is not None:
        try:
            os.chdir(project_cwd)
        except OSError:
            # Non-fatal — exec from current cwd rather than aborting.
            pass

    os.execvp(cmd, argv)
    # execvp replaces the process on success; if it returns, something
    # went catastrophically wrong.
    return 1  # pragma: no cover


def print_generic_hint(agent_name: str, session_id: str) -> None:
    """Fallback hint for agents without a native resume integration."""
    label = agent_name or "agent"
    click.echo(f"No native resume wired up for {label!r}.")
    click.echo(f"Upstream session_id: {session_id}")
