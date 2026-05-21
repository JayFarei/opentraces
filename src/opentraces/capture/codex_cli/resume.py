"""Resume support for Codex CLI traces."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import click


class CodexResumeError(Exception):
    """Typed failure from the Codex CLI resumer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CodexCliResumer:
    """Hand a captured trace back to ``codex resume <session_id>``."""

    agent_name = "codex-cli"
    supports_at_step = False

    def resume_session(
        self,
        session_id: str,
        *,
        project_cwd: Path,
        dry_run: bool = False,
    ) -> int:
        cmd = shutil.which("codex")
        if not cmd:
            click.echo(
                "codex CLI not on PATH. Install Codex CLI first.",
                err=True,
            )
            click.echo(f"Once installed, run: codex resume {session_id}", err=True)
            return 127

        argv = [cmd, "resume", session_id]
        if dry_run:
            click.echo(" ".join(argv))
            return 0

        return subprocess.call(argv, cwd=project_cwd)

    def resolve_at_step(
        self,
        trace_id_prefix: str,
        step_id: str,
        staging: Path,
        *,
        project_cwd: Path,
        state: object,
        materialize: bool = True,
    ) -> object:
        raise CodexResumeError(
            "UNSUPPORTED_AT_STEP",
            "--at-step resume is not supported for codex-cli traces.",
        )
