"""Live re-rollout gate for SkillOpt (arXiv 2605.23904, slice 3).

The offline proxy in :mod:`.engine` scores a candidate skill by how well it
addresses the failure modes of already-captured traces. The paper's real gate
instead *re-rolls* the candidate skill: it runs the frozen agent on held-out
tasks with the candidate skill injected, scores the fresh outcomes, and accepts
only when the re-rolled reward strictly beats the incumbent's. This module
provides that gate.

A :class:`ReRolloutRunner` executes one (skill, task) rollout and returns a
reward in [0,1]. Two runners ship:

* :class:`FakeReRolloutRunner` , deterministic, no agent. Reward rises with how
  many of a task's required rule markers the candidate skill contains. Used by
  tests and any CI path so the gate is exercised with zero network/agent.
* :class:`RealClaudeReRolloutRunner` , renders the candidate skill into a
  sandbox as ``CLAUDE.md``, runs the real ``claude`` binary on the task prompt,
  and scores the fresh result with a deterministic verifier. Used only when a
  live re-rollout is explicitly requested.

:func:`make_rerollout_gate` turns a runner + held-out tasks into the
``gate_fn`` that :func:`.engine.run_optimization` accepts.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .engine import RULE_MARKER


class ReRolloutUnavailable(RuntimeError):
    """Raised when a live re-rollout cannot run (e.g. the agent binary is absent)."""


@dataclass(frozen=True)
class ReRolloutTask:
    """A held-out task to re-roll a candidate skill on.

    ``prompt`` is what the agent is asked to do; ``setup_files`` seed the sandbox;
    ``verify_path``/``verify_contains`` define the deterministic success check for
    the real runner; ``required_markers`` are the rule markers a skill must carry
    to help (used by the fake runner and to link the task to trace failure tags).
    ``source_trace_id`` records the real bucket trace the task was derived from.
    """

    task_id: str
    prompt: str
    setup_files: dict[str, str] = field(default_factory=dict)
    verify_path: str = ""
    verify_contains: str = ""
    required_markers: tuple[str, ...] = ()
    source_trace_id: str | None = None


@dataclass(frozen=True)
class ReRolloutResult:
    task_id: str
    reward: float
    detail: str = ""


class ReRolloutRunner(Protocol):
    def run(self, skill_text: str, task: ReRolloutTask) -> ReRolloutResult:
        ...


class FakeReRolloutRunner:
    """Deterministic runner: reward = fraction of a task's required markers the
    candidate skill contains. No agent, no network, so the gate is testable."""

    def run(self, skill_text: str, task: ReRolloutTask) -> ReRolloutResult:
        if not task.required_markers:
            return ReRolloutResult(task.task_id, 0.0, "no required markers")
        hit = sum(1 for m in task.required_markers if RULE_MARKER.format(tag=m) in skill_text)
        reward = round(hit / len(task.required_markers), 6)
        return ReRolloutResult(task.task_id, reward, f"{hit}/{len(task.required_markers)} markers")


class RealClaudeReRolloutRunner:
    """Render the candidate skill into a sandbox and run the real ``claude`` CLI.

    Writes the candidate skill to ``CLAUDE.md`` (Claude Code reads it from the
    working directory) and the task's ``setup_files``, runs
    ``claude --print --permission-mode bypassPermissions <prompt>`` in that
    sandbox, then scores 1.0/0.0 by the deterministic verifier
    (``verify_path`` contains ``verify_contains`` after the run). This is the real
    re-rolled reward for the (skill, task) pair.
    """

    def __init__(self, *, binary: str = "claude", timeout_s: int = 300) -> None:
        self.binary = binary
        self.timeout_s = timeout_s

    def run(self, skill_text: str, task: ReRolloutTask) -> ReRolloutResult:
        exe = shutil.which(self.binary)
        if not exe:
            raise ReRolloutUnavailable(f"{self.binary} binary not found on PATH")
        with tempfile.TemporaryDirectory(prefix="ot-rerollout-") as tmp:
            sandbox = Path(tmp)
            (sandbox / "CLAUDE.md").write_text(skill_text, encoding="utf-8")
            for rel, content in task.setup_files.items():
                target = sandbox / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            try:
                proc = subprocess.run(
                    [exe, "--print", "--permission-mode", "bypassPermissions", task.prompt],
                    cwd=str(sandbox),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                raise ReRolloutUnavailable(f"claude timed out after {self.timeout_s}s") from exc
            ok = False
            if task.verify_path:
                vp = sandbox / task.verify_path
                ok = vp.exists() and (task.verify_contains in vp.read_text(encoding="utf-8"))
            reward = 1.0 if ok else 0.0
            tail = (proc.stdout or "")[-400:]
            return ReRolloutResult(task.task_id, reward, f"rc={proc.returncode} verify={ok} :: {tail}")


def make_rerollout_gate(
    runner: ReRolloutRunner, tasks: list[ReRolloutTask]
) -> Callable[[str], float]:
    """Build the ``gate_fn`` that scores a skill by mean re-rolled reward over
    the held-out tasks. Plug into :func:`.engine.run_optimization`."""

    def _gate(skill_text: str) -> float:
        if not tasks:
            return 0.0
        total = sum(runner.run(skill_text, t).reward for t in tasks)
        return round(total / len(tasks), 6)

    return _gate
