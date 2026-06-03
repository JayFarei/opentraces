"""Resume support for Pi traces."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import click


class PiResumeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PiResumer:
    """Hand a captured trace back to Pi via ``pi --session``."""

    agent_name = "pi"
    supports_at_step = False

    def resume_session(
        self,
        session_id: str,
        *,
        project_cwd: Path,
        dry_run: bool = False,
    ) -> int:
        cmd = shutil.which("pi")
        if not cmd:
            click.echo("pi CLI not on PATH. Install/authenticate Pi first.", err=True)
            click.echo(f"Once installed, resume the session id/path: pi --session {session_id}", err=True)
            return 127
        argv = [cmd, "--session", session_id]
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
        raise PiResumeError(
            "UNSUPPORTED_AT_STEP",
            "--at-step resume is not supported for pi traces in v1.",
        )
