"""Real-box control for deterministic Claude harness readiness in A6.

This control provisions a fresh ``agent-ready`` lease and sends one sentinel
task through the real ``AgentTerminalDrive`` PTY.  It deliberately supplies no
credential and performs no inference: after readiness is established, an
explicit authentication refusal is an acceptable pre-action outcome.  A
first-run selector consuming the task is never acceptable.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

from opentraces.core.arena.box import CrabboxRuntime
from opentraces.core.arena.drives.actions import RunActionSequence
from opentraces.core.arena.drives.agent import AgentTerminalDrive
from opentraces.core.arena.harnesses import CLAUDE_HARNESS_EXECUTABLE
from opentraces.core.arena.run_store import RunStore


TASK_SENTINEL = "A6_AGENT_READY_TASK_SENTINEL"
COMPLETE_MARKER = "OPENTRACES_AGENT_ATTEMPT_COMPLETE"
ONBOARDING_SELECTOR = "Choose the text style"
AUTHENTICATION_REFUSAL = re.compile(
    r"not logged in|please run\s+/login|authentication (?:is )?required",
    re.IGNORECASE,
)


def verify() -> None:
    repository = Path(__file__).resolve().parents[2]
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="opentraces-a6-agent-ready-") as directory:
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
                    "git config user.name 'A6 readiness setup'; "
                    "git config user.email 'a6-readiness@example.invalid'; "
                    "printf '%s\\n' seed > seed.txt; "
                    "git add seed.txt; "
                    "git commit -m 'Seed agent readiness workspace' >/dev/null",
                ],
                timeout=30,
                timing_path=evidence / "remote-seed-timing.json",
            )
            if seeded.returncode != 0:
                raise AssertionError("could not seed the agent readiness workspace")

            runtime.materialize(box, "agent-ready", repository=repository)
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
                    "/usr/bin/sudo",
                    "-H",
                    "-n",
                    "-u",
                    "opentraces-product",
                    "--",
                    CLAUDE_HARNESS_EXECUTABLE,
                ],
                prompt=TASK_SENTINEL,
                expect_regex=(
                    rf"{re.escape(ONBOARDING_SELECTOR)}|{COMPLETE_MARKER}|"
                    r"not logged in|please run\s+/login|authentication (?:is )?required"
                ),
                timeout=15,
            )
            transcript = (draft.path / result.transcript_ref).read_text(encoding="utf-8")
            if ONBOARDING_SELECTOR.lower() in transcript.lower():
                raise AssertionError(
                    "fresh Claude onboarding absorbed the task before inference: "
                    "the agent-ready recipe did not establish harness readiness"
                )
            if TASK_SENTINEL not in transcript:
                raise AssertionError("the real PTY did not receive the sentinel task")
            if COMPLETE_MARKER not in transcript and not AUTHENTICATION_REFUSAL.search(transcript):
                raise AssertionError(
                    "the task reached neither completion nor a named pre-action refusal: "
                    f"result={result.reason!r}; transcript={transcript!r}"
                )
        finally:
            try:
                if box is not None:
                    runtime.release(box)
            finally:
                os.chdir(original_cwd)


if __name__ == "__main__":
    verify()
    print("A6 agent-ready onboarding control: PASS")
