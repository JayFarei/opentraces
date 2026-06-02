"""Test-side otbox harness adapter for SkillOpt online-loop tests.

This module deliberately lives under ``tests/otbox``. The shipped SkillOpt
consumer imports only the generic Harness/ReRolloutRunner protocols from
``src/``; otbox remains a dev/CI substrate and is not promoted to an end-user
surface.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from opentraces.consumers.skill_opt.engine import RULE_MARKER, RolloutRow, outcome_reward
from opentraces.consumers.skill_opt.rerollout import ReRolloutResult, ReRolloutTask

from tests.otbox.checkpoints import resolve_checkpoint
from tests.otbox.drivers.base import Driver
from tests.otbox.simulated_users.runner import Turn, run_simulated_session


@dataclass(frozen=True)
class OtboxSkillOptTask:
    """A small verifiable task for the test-side online SkillOpt harness."""

    task_id: str
    prompt: str
    required_markers: tuple[str, ...]
    expect_regex: str = ".*"
    setup_files: dict[str, str] = field(default_factory=dict)
    verify_path: str = ""
    verify_contains: str = ""
    source_trace_id: str | None = None

    def to_rerollout_task(self) -> ReRolloutTask:
        return ReRolloutTask(
            task_id=self.task_id,
            prompt=self.prompt,
            setup_files=self.setup_files,
            verify_path=self.verify_path,
            verify_contains=self.verify_contains or self.expect_regex,
            required_markers=self.required_markers,
            source_trace_id=self.source_trace_id,
        )


class OtboxReRolloutRunner:
    """Re-roll one candidate skill in a fork of ``c-installed-source``."""

    def __init__(
        self,
        driver: Driver,
        *,
        binary: str,
        agent: str = "echo",
        checkpoint: str = "c-installed-source",
        output_root: Path,
        env_extra: dict[str, str] | None = None,
        turn_timeout_s: float = 10.0,
    ) -> None:
        self.driver = driver
        self.binary = binary
        self.agent = agent
        self.checkpoint = checkpoint
        self.output_root = output_root
        self.env_extra = env_extra or {}
        self.turn_timeout_s = turn_timeout_s
        self._run_count = 0

    def run(self, skill_text: str, task: ReRolloutTask) -> ReRolloutResult:
        cp = resolve_checkpoint(self.driver, self.checkpoint)
        box = cp.box
        self._run_count += 1
        out_dir = self.output_root / f"{self._run_count:02d}-{task.task_id}"
        try:
            self.driver.put_text(box, str(Path(box.project) / "CLAUDE.md"), skill_text)
            with tempfile.TemporaryDirectory(prefix="otbox-skillopt-template-") as tmp:
                template = Path(tmp)
                for rel, content in task.setup_files.items():
                    target = template / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                result = run_simulated_session(
                    self.driver,
                    box,
                    self.binary,
                    [
                        Turn(
                            task.prompt,
                            ".*" if task.verify_path else (task.verify_contains or ".*"),
                            timeout_s=self.turn_timeout_s,
                        )
                    ],
                    initial_state_dir=template if task.setup_files else None,
                    output_dir=out_dir,
                    env_extra=self.env_extra,
                    agent=self.agent,
                )
            reward = self._captured_trace_reward(box)
            if reward is None:
                reward = self._scenario_reward(
                    skill_text, task, result.verdict == "PASS", Path(box.project)
                )
            detail = (
                f"checkpoint={self.checkpoint} verdict={result.verdict} "
                f"turns={result.turn_count} log={result.pane_log_path} "
                f"{result.error_message}".strip()
            )
            return ReRolloutResult(task.task_id, reward, detail)
        finally:
            if box.root.exists():
                self.driver.teardown(box)

    @staticmethod
    def _scenario_reward(
        skill_text: str, task: ReRolloutTask, passed: bool, project: Path
    ) -> float:
        if not passed:
            return 0.0
        if task.verify_path:
            path = project / task.verify_path
            ok = path.exists() and task.verify_contains in path.read_text(encoding="utf-8")
            return 1.0 if ok else 0.0
        if not task.required_markers:
            return 1.0
        hit = sum(1 for marker in task.required_markers if RULE_MARKER.format(tag=marker) in skill_text)
        return round(hit / len(task.required_markers), 6)

    @staticmethod
    def _captured_trace_reward(box) -> float | None:
        root = Path(box.opentraces_dir) / "bucket" / "objects" / "traces" / "v1"
        if not root.exists():
            return None
        rewards: list[float] = []
        for path in root.rglob("current.json"):
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            record = envelope.get("record") or envelope.get("trace") or {}
            outcome = record.get("outcome") or {}
            rewards.append(
                outcome_reward(
                    success=outcome.get("success"),
                    committed=bool(outcome.get("committed", False)),
                    survival_state=record.get("survival_state") or outcome.get("survival_state"),
                )
            )
        return max(rewards) if rewards else None


class OtboxHarness:
    """Harness adapter that collects and scores SkillOpt tasks via otbox."""

    def __init__(
        self,
        runner: OtboxReRolloutRunner,
        *,
        train_tasks: Sequence[OtboxSkillOptTask],
        selection_tasks: Sequence[OtboxSkillOptTask],
        test_tasks: Sequence[OtboxSkillOptTask],
    ) -> None:
        self.runner = runner
        self.train_tasks = list(train_tasks)
        self.selection_tasks = list(selection_tasks)
        self.test_tasks = list(test_tasks)

    def collect_rollouts(self, skill_text: str, tasks: Sequence[object]) -> list[RolloutRow]:
        rows: list[RolloutRow] = []
        for task in tasks:
            if not isinstance(task, OtboxSkillOptTask):
                continue
            result = self.runner.run(skill_text, task.to_rerollout_task())
            missing = tuple(
                marker
                for marker in task.required_markers
                if RULE_MARKER.format(tag=marker) not in skill_text
            )
            rows.append(
                RolloutRow(
                    trace_id=task.task_id,
                    score=result.reward,
                    reward=result.reward,
                    failure_tags=missing if result.reward < 1.0 else (),
                    success_tags=task.required_markers if result.reward >= 1.0 else (),
                    summary=result.detail,
                )
            )
        return rows

    def score(self, skill_text: str, tasks: Sequence[object]) -> float:
        rewards: list[float] = []
        for task in tasks:
            if isinstance(task, OtboxSkillOptTask):
                rewards.append(self.runner.run(skill_text, task.to_rerollout_task()).reward)
        if not rewards:
            return 0.0
        return round(sum(rewards) / len(rewards), 6)
