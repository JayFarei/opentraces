"""LocalDriver — the default Tier 0 substrate.

A box is a HOME-isolated filesystem sandbox under ``.otbox/boxes/<id>/``.
The opentraces CLI runs as a real subprocess against the repo ``.venv``
(spec R4). Isolation is by ``HOME`` redirection plus pinned git config
env vars, so the developer's real ``~/.opentraces`` / shell profile /
git config are never touched. Fully offline, deterministic, no Docker.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from ..env import isolated_env
from .base import Driver, ExecResult, timed


class LocalDriver(Driver):
    name = "local"
    tier = 0

    def provision(self, box) -> None:
        for path in (
            box.root,
            box.home,
            box.opentraces_dir,
            box.project,
            box.fake_remote,
            box.logs,
        ):
            path.mkdir(parents=True, exist_ok=True)
        # A hermetic, box-local git identity so seeded commits never
        # depend on (or mutate) the developer's global git config.
        gitconfig = box.home / ".gitconfig"
        if not gitconfig.exists():
            gitconfig.write_text(
                "[user]\n"
                "\tname = otbox\n"
                "\temail = otbox@opentraces.local\n"
                "[init]\n"
                "\tdefaultBranch = main\n"
                "[commit]\n"
                "\tgpgsign = false\n"
                "[advice]\n"
                "\tdetachedHead = false\n"
            )

    def exec(
        self,
        box,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env_extra: dict | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        argv = [str(a) for a in argv]
        run_cwd = str(cwd) if cwd is not None else str(box.project)
        env = isolated_env(box, env_extra)
        timed_out = False
        with timed() as clock:
            try:
                proc = subprocess.run(
                    argv,
                    cwd=run_cwd,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                returncode = 124
                stdout = exc.stdout or "" if isinstance(exc.stdout, str) else ""
                stderr = (exc.stderr or "" if isinstance(exc.stderr, str) else "") + \
                    f"\n[otbox] command timed out after {timeout}s"
        return ExecResult(
            argv=argv,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration_s=clock.duration,
            cwd=run_cwd,
            timed_out=timed_out,
        )

    def popen(
        self,
        box,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env_extra: dict | None = None,
    ) -> subprocess.Popen:
        """Start a long-running process inside the box (e.g. `ot web`).

        Used by journey ``service`` steps. The journey runner owns the
        returned handle's lifecycle and terminates it at journey end.
        """
        argv = [str(a) for a in argv]
        run_cwd = str(cwd) if cwd is not None else str(box.project)
        return subprocess.Popen(  # noqa: S603 - argv is a list, not shell
            argv,
            cwd=run_cwd,
            env=isolated_env(box, env_extra),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def teardown(self, box) -> None:
        if box.root.exists():
            shutil.rmtree(box.root)
