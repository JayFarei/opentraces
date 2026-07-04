"""LocalDriver — the default Tier 0 substrate.

A box is a HOME-isolated filesystem sandbox under ``.otbox/boxes/<id>/``.
The opentraces CLI runs as a real subprocess against the repo ``.venv``
(spec R4). Isolation is by ``HOME`` redirection plus pinned git config
env vars, so the developer's real ``~/.opentraces`` / shell profile /
git config are never touched. Fully offline, deterministic, no Docker.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Sequence

from ..env import isolated_env
from .base import Driver, ExecResult, timed


def maxrss_to_kb(raw_maxrss: int | None, platform: str = sys.platform) -> int | None:
    """Normalize a raw ``rusage.ru_maxrss`` reading to KILOBYTES.

    The kernel's ``ru_maxrss`` unit is platform-dependent: macOS / Darwin
    report BYTES, Linux reports KILOBYTES. This is the exact unit footgun
    that bit the reconciler memory check (a KB reading treated as bytes, or
    vice-versa, is off by 1024x — the difference between a passing and a
    wildly-failing budget). Always returns KB (or ``None`` for a missing
    reading) so callers never re-derive the convention.
    """
    if raw_maxrss is None:
        return None
    if platform == "darwin":
        return int(raw_maxrss) // 1024
    return int(raw_maxrss)


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
        live_hf: bool = False,
    ) -> ExecResult:
        argv = [str(a) for a in argv]
        run_cwd = str(cwd) if cwd is not None else str(box.project)
        env = isolated_env(box, env_extra, live_hf=live_hf)
        timed_out = False
        max_rss_kb: int | None = None
        returncode = 0
        stdout = ""
        stderr = ""
        # Popen + os.wait4 (not subprocess.run) so we capture the child's OWN
        # peak RSS from its rusage, per-process — RUSAGE_CHILDREN deltas would
        # be cumulative across every prior child in this runner process and
        # under-report a well-behaved command to ~0. This is the portable RSS
        # measurement the scale-world perf ceilings need.
        with timed() as clock:
            try:
                proc = subprocess.Popen(  # noqa: S603 - argv is a list, not shell
                    argv,
                    cwd=run_cwd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except FileNotFoundError as exc:
                # A missing binary is a non-zero RESULT, not a harness crash —
                # callers that probe candidate binaries (e.g. _prereqs.py's
                # python3.14 -> python3.12 fall-through) rely on exec returning
                # ok=False so they can try the next candidate. Surfaced on the
                # first CI nightly: python3.14 exists locally but not on the
                # ubuntu runner, so the unguarded probe crashed the whole sweep.
                return ExecResult(
                    argv=argv,
                    returncode=127,
                    stdout="",
                    stderr=f"[otbox] executable not found: {exc.filename or argv[0]}",
                    duration_s=0.0,
                    cwd=run_cwd,
                    timed_out=False,
                    max_rss_kb=None,
                )

            # Drain both pipes concurrently so a chatty child can never
            # deadlock against the OS pipe buffer while we block in wait4.
            out_buf: list[str] = []
            err_buf: list[str] = []
            t_out = threading.Thread(target=lambda: out_buf.append(proc.stdout.read()))
            t_err = threading.Thread(target=lambda: err_buf.append(proc.stderr.read()))
            t_out.start()
            t_err.start()

            killed = {"timeout": False}
            killer: threading.Timer | None = None
            if timeout is not None:
                def _kill() -> None:
                    killed["timeout"] = True
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001 - already gone is fine
                        pass

                killer = threading.Timer(timeout, _kill)
                killer.start()

            try:
                _pid, status, rusage = os.wait4(proc.pid, 0)
            finally:
                if killer is not None:
                    killer.cancel()
            t_out.join()
            t_err.join()
            proc.stdout.close()
            proc.stderr.close()
            stdout = out_buf[0] if out_buf and out_buf[0] is not None else ""
            stderr = err_buf[0] if err_buf and err_buf[0] is not None else ""
            # Reap the Popen bookkeeping ourselves: os.wait4 already collected
            # the child, so stamp returncode so Popen's finalizer never re-waits
            # (a second waitpid on a reaped pid raises ChildProcessError).
            exit_code = os.waitstatus_to_exitcode(status)
            proc.returncode = exit_code
            returncode = exit_code
            max_rss_kb = maxrss_to_kb(rusage.ru_maxrss)
            if timeout is not None and killed["timeout"]:
                timed_out = True
                returncode = 124
                stderr = stderr + f"\n[otbox] command timed out after {timeout}s"
        return ExecResult(
            argv=argv,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration_s=clock.duration,
            cwd=run_cwd,
            timed_out=timed_out,
            max_rss_kb=max_rss_kb,
        )

    def popen(
        self,
        box,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env_extra: dict | None = None,
        live_hf: bool = False,
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
            env=isolated_env(box, env_extra, live_hf=live_hf),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def teardown(self, box) -> None:
        if box.root.exists():
            shutil.rmtree(box.root)
