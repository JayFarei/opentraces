"""Default-CI otbox journey backing test for the Skill Verifier Factory.

Proves the end-to-end loop the factory exists to enable: mine evidence -> emit a
trace-grounded verifier package -> the package's deterministic verifier drives the
SkillOpt strict-Dsel gate to accept a candidate skill on a held-out split, with the
held-out Dtest reported. Fully deterministic: synthetic episodes + the FakeReRollout
runner (the echo-equivalent), no tmux / agent / network, so it runs in default CI.

The higher-fidelity real-agent leg (driving ``OtboxReRolloutRunner`` against a
captured-session checkpoint) is the opt-in ``skillopt-online-loop-echo`` gold journey
plus the opt-in real-gold suite; this verifier journey deliberately stays in the
deterministic default-CI lane.
"""

from __future__ import annotations

import json

import pytest

from opentraces.consumers.skill_opt.engine import RolloutRow, run_optimization
from opentraces.consumers.skill_opt.proposers import default_proposer
from opentraces.consumers.skill_opt.rerollout import (
    FakeReRolloutRunner,
    ReRolloutTask,
    make_rerollout_gate,
)
from opentraces.consumers.verifier_factory import emit_verifier_package, get_archetype


def _episodes(skill: str, n: int = 16) -> list[dict]:
    eps: list[dict] = []
    for i in range(n):
        committed = i % 2 == 0
        success = i % 3 != 0
        intent = "do the thing"
        if i % 2 == 0:
            intent += " with explicit outcome, verification test, check"
        if i % 4 == 0:
            intent += " constraint must not drift; if blocked stop and escalate; refactor; cite line file"
        eps.append(
            {
                "episode_id": f"episode:{skill}-{i:03d}",
                "source_trace_id": f"trace-{skill}-{i:03d}",
                "source_unit_id": f"tu:trace-{skill}-{i:03d}:skill:metadata:0",
                "skill_id": skill,
                "agent": "codex-cli" if i % 2 else "claude-code",
                "skill_source": "codex_cli_skill_body_read",
                "user_intent": intent,
                "task_summary": f"Skill invocation {skill}",
                "files_touched_json": json.dumps(
                    ["src/app.py", "tests/test_app.py"] if i % 2 == 0 else []
                ),
                "tools_used_json": json.dumps(["pytest"] if i % 2 else ["apply_patch"]),
                "outcome_label": "success" if success else "failure",
                "outcome_reward": 0.8 if committed and success else (0.5 if committed else 0.0),
                "confidence": "high",
            }
        )
    return eps


def test_verifier_package_feeds_skillopt_gate_default_ci(tmp_path):
    """The emitted verifier drives a strict Dsel improvement + held-out Dtest."""
    pkg = emit_verifier_package(
        skill="goal-forge",
        archetype_id="goal_contract_downstream_outcome_v1",
        out_dir=tmp_path / "pkg",
        episodes=_episodes("goal-forge"),
        seed="otbox",
    )
    assert pkg.dsel_before == 0.0
    assert pkg.dsel_after == pytest.approx(1.0)
    assert pkg.dtest_score == pytest.approx(1.0)
    assert pkg.accepted is True

    # Independently re-derive the gate from the package's addressable markers and a
    # fresh held-out task set, proving the package's verifier (not just the run that
    # produced it) drives acceptance.
    markers = pkg.addressable_markers
    train = [
        RolloutRow(trace_id=f"tr{i}", score=0.0, reward=0.0, failure_tags=markers)
        for i in range(6)
    ]
    holdout = [ReRolloutTask(task_id=f"h{i}", prompt="p", required_markers=markers) for i in range(4)]
    runner = FakeReRolloutRunner()
    result = run_optimization(
        get_archetype("goal_contract_downstream_outcome_v1").baseline_skill,
        train,
        propose=default_proposer,
        budget=len(markers),
        budget_floor=len(markers),
        schedule="constant",
        max_steps=len(markers) + 1,
        epochs=1,
        seed="otbox",
        gate_fn=make_rerollout_gate(runner, holdout),
        test_fn=make_rerollout_gate(runner, holdout),
    )
    assert result.initial_score == 0.0
    assert result.best_score == pytest.approx(1.0)
    assert result.test_score == pytest.approx(1.0)
    assert result.accepted > 0


def test_verifier_factory_task_shape_is_otbox_compatible(tmp_path):
    """The factory's held-out tasks map cleanly onto the otbox ReRolloutTask shape."""
    from opentraces.consumers.skill_opt.rerollout import FakeReRolloutRunner as _Runner

    pkg = emit_verifier_package(
        skill="review",
        archetype_id="review_grounded_findings_v1",
        out_dir=tmp_path / "pkg",
        episodes=_episodes("review"),
        seed="otbox",
    )
    incumbent = get_archetype("review_grounded_findings_v1").baseline_skill
    candidate = incumbent + "\n" + "\n".join(f"rule[{m}]" for m in pkg.addressable_markers)
    task = ReRolloutTask(
        task_id="verifier-otbox",
        prompt="apply the review contract",
        required_markers=pkg.addressable_markers,
    )
    runner = _Runner()
    assert runner.run(incumbent, task).reward < runner.run(candidate, task).reward
