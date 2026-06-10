"""Default-CI proof for SkillOpt's otbox-backed online harness."""

from __future__ import annotations

import shutil
import os
from pathlib import Path

import pytest

from opentraces.consumers.skill_opt import engine as so
from opentraces.consumers.skill_opt import proposers as opt

from tests.otbox.drivers import get_driver
from tests.otbox.skillopt_harness import (
    OtboxHarness,
    OtboxReRolloutRunner,
    OtboxSkillOptTask,
)
from tests.otbox.skillopt_real_suite import held_out_status_suite, run_real_gold

ECHO_BINARY = Path(__file__).resolve().parent / "simulated_users" / "_echo_binary.py"


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    """otbox boxes provide the isolation; do not redirect HOME again here."""
    yield


@pytest.fixture
def driver():
    if not shutil.which("tmux"):
        pytest.skip("tmux not installed on PATH")
    # run_simulated_session drives the binary through terminal-control;
    # absent on CI -> the rollout verdict is never PASS, reward stays 0, and
    # the test asserts 1.0. Skip cleanly when the tool is missing.
    if not shutil.which("termctrl"):
        pytest.skip("termctrl (terminal-control) not installed on PATH")
    return get_driver("local")


def _task(task_id: str, marker: str) -> OtboxSkillOptTask:
    return OtboxSkillOptTask(
        task_id=task_id,
        prompt="Add a farewell helper to src/app.py",
        required_markers=(marker,),
        expect_regex=r"(?i)I'?ll add",
        setup_files={"src/app.py": "def greet():\n    return 'hello'\n"},
    )


def test_otbox_rerollout_runner_injects_candidate_skill(driver, tmp_path):
    runner = OtboxReRolloutRunner(
        driver,
        binary=str(ECHO_BINARY),
        output_root=tmp_path / "rerollout",
    )
    task = _task("sel-a", "rl.A").to_rerollout_task()

    incumbent = runner.run("# init\n", task)
    candidate = runner.run("# init\n- " + so.RULE_MARKER.format(tag="rl.A"), task)

    assert incumbent.reward == 0.0
    assert candidate.reward == 1.0
    assert "checkpoint=c-installed-source" in candidate.detail


def test_otbox_harness_drives_online_loop_with_echo(driver, tmp_path):
    runner = OtboxReRolloutRunner(
        driver,
        binary=str(ECHO_BINARY),
        output_root=tmp_path / "online-loop",
    )
    harness = OtboxHarness(
        runner,
        train_tasks=[_task("train-a", "rl.A")],
        selection_tasks=[_task("sel-a", "rl.A")],
        test_tasks=[_task("test-a", "rl.A")],
    )

    res = so.run_optimization(
        "# init\n",
        [],
        propose=opt.default_proposer,
        harness=harness,
        train_tasks=harness.train_tasks,
        selection_tasks=harness.selection_tasks,
        test_tasks=harness.test_tasks,
        budget=1,
        budget_floor=1,
        schedule="constant",
        max_steps=2,
        epochs=1,
        slow_update=None,
        meta_update=None,
    )

    assert res.initial_score == 0.0
    assert res.best_score == 1.0
    assert res.test_score == 1.0
    assert res.accepted == 1
    assert so.RULE_MARKER.format(tag="rl.A") in res.best_skill


def test_real_suite_defines_disjoint_verifiable_splits():
    train, selection, test = held_out_status_suite()
    assert {t.task_id for t in train}.isdisjoint({t.task_id for t in selection})
    assert {t.task_id for t in selection}.isdisjoint({t.task_id for t in test})
    assert all(t.verify_path and t.verify_contains for t in [*train, *selection, *test])


@pytest.mark.skipif(os.environ.get("OT_REAL_REPL") != "1", reason="real agent opt-in")
def test_real_skillopt_gold_run_opt_in(tmp_path):
    summary = run_real_gold("claude", tmp_path / "real-gold", timeout_s=180)
    assert summary["best_selection_score"] > summary["initial_selection_score"]
    assert summary["held_out_test_score"] == 1.0
    assert summary["accepted"] >= 1
