"""Real direct-PTY control for A6's materialized workspace coordinate.

Run from the repository root with::

    .venv/bin/python tests/manual/verify_a6_direct_pty_workspace.py

This intentionally provisions ``agent-ready`` but runs no inference.  A real
``AgentTerminalDrive`` and ``termctrl`` session cross the direct
``ssh -tt -F /dev/null`` boundary, then the non-sudo product identity proves
relative capture output and one Git edit/add/commit in the workspace that
Crabbox actually materialized.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path, PurePosixPath

from opentraces.core.arena.box import CrabboxRuntime
from opentraces.core.arena.drives.actions import RunActionSequence
from opentraces.core.arena.drives.agent import AgentTerminalDrive
from opentraces.core.arena.run_store import RunStore


MARKER = "A6_DIRECT_PTY_WORKSPACE_OK"
PWD_MARKER = "A6_DIRECT_PTY_PWD="


def verify() -> None:
    repository = Path(__file__).resolve().parents[2]
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="opentraces-a6-direct-pty-") as directory:
        root = Path(directory)
        evidence = root / "evidence"
        runtime = CrabboxRuntime(home=Path.home(), ssh_config=root / "missing-ssh-config")
        runtime.configure_run_evidence(evidence)
        box = None
        os.chdir(repository)
        try:
            box = runtime.lease()
            seeded = runtime.exec(
                box,
                [
                    "sh",
                    "-c",
                    "set -eu; "
                    "git init --initial-branch=main >/dev/null; "
                    "git config user.name 'A6 transport setup'; "
                    "git config user.email 'a6-transport@example.invalid'; "
                    "printf '%s\\n' seed > seed.txt; "
                    "git add seed.txt; "
                    "git commit -m 'Seed direct PTY workspace' >/dev/null",
                ],
                timeout=30,
                timing_path=evidence / "remote-seed-timing.json",
            )
            if seeded.returncode != 0:
                raise AssertionError("the transport could not seed the direct-PTY workspace")

            runtime.materialize(box, "agent-ready", repository=repository)
            observed = runtime.exec(
                box,
                ["pwd", "-P"],
                timeout=30,
                timing_path=evidence / "materialized-workspace-timing.json",
            )
            workspace = observed.stdout.strip()
            if (
                observed.returncode != 0
                or not workspace
                or not PurePosixPath(workspace).is_absolute()
            ):
                raise AssertionError("the materialized workspace coordinate was not observable")
            transport_harness = runtime.copy_into_box(
                box,
                repository / "tests/manual/a6_direct_pty_transport.sh",
                "/tmp/a6-direct-pty-transport.sh",
            )
            product_harness = runtime.copy_into_box(
                box,
                repository / "tests/manual/a6_direct_pty_harness.sh",
                "/tmp/a6-direct-pty-harness.sh",
            )

            store = RunStore(root / "runs" / "v1")
            draft = store.begin()
            actions = RunActionSequence(draft=draft, run_started_monotonic=time.monotonic())
            drive = AgentTerminalDrive(
                box=box,
                draft=draft,
                actions=actions,
                poll_interval=0.1,
            )
            result = drive.run(
                harness_argv=[
                    "/bin/sh",
                    transport_harness,
                    workspace,
                    product_harness,
                ],
                prompt="begin",
                expect_regex=MARKER,
                timeout=30,
            )
            transcript = (draft.path / result.transcript_ref).read_text(encoding="utf-8")
            pwd_match = re.search(rf"{PWD_MARKER}([^\r\n]+)", transcript)
            if pwd_match is None:
                raise AssertionError("the direct SSH/PTTY control did not report its remote pwd")
            direct_pty_pwd = pwd_match.group(1)
            if direct_pty_pwd != workspace:
                raise AssertionError(
                    "the direct SSH/PTTY agent drive started outside the materialized "
                    f"workspace: observed={direct_pty_pwd!r}, expected={workspace!r}"
                )
            if result.status != "pass":
                raise AssertionError(
                    "the direct SSH/PTTY agent drive did not enter the materialized workspace: "
                    f"{result.reason}; transcript={transcript!r}"
                )

            collected = runtime.collect(
                box,
                [".opentraces/bench-capture/direct-pty-control/capture_result.json"],
                destination=evidence / "collected",
                repository=repository,
            )
            capture_result = collected.get("capture_result.json")
            if capture_result is None or not capture_result.is_file():
                raise AssertionError("direct-PTY relative capture output was not collectable")

            transport = runtime.exec(
                box,
                ["sh", "-c", 'test "$(stat -c %U "$HOME")" = crabbox'],
                timeout=30,
                timing_path=evidence / "transport-home-timing.json",
            )
            if transport.returncode != 0:
                raise AssertionError("direct PTY execution mutated transport home custody")
        finally:
            try:
                if box is not None:
                    runtime.release(box)
            finally:
                os.chdir(original_cwd)


if __name__ == "__main__":
    verify()
    print("A6 direct PTY workspace control: PASS")
