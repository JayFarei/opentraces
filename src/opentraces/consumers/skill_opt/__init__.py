"""SkillOpt consumer: optimize a skill from scored-rollout rows (arXiv 2605.23904).

Reference implementation of the workflow -> consumer contract. ``engine`` holds
the offline edit-engine and propose-and-rank loop; ``proposers`` holds the edit
proposers; ``runner`` is the contract's ``run`` entrypoint wired to the
``skill-opt-v1`` workflow and driven by ``opentraces workflow optimize``.
"""

from .engine import (
    EditResult,
    OptimizationResult,
    RolloutRow,
    SkillOptState,
    apply_edits,
    apply_slow_update,
    budget_at,
    clip_to_budget,
    default_slow_update,
    outcome_reward,
    run_optimization,
    score_skill_on_rows,
    split_rows_by_hash,
    split_success_failure,
    tag_deficit_weights,
)
from .proposers import DeterministicOptimizerClient, default_proposer, make_llm_proposer
from .rerollout import (
    FakeReRolloutRunner,
    RealClaudeReRolloutRunner,
    ReRolloutResult,
    ReRolloutTask,
    ReRolloutUnavailable,
    make_rerollout_gate,
)
from .runner import (
    SkillOptOutcome,
    SkillOptRequest,
    lineage_path,
    promoted_skill_dir,
    promoted_skill_path,
    run,
)

__all__ = [
    "DeterministicOptimizerClient",
    "EditResult",
    "FakeReRolloutRunner",
    "OptimizationResult",
    "RealClaudeReRolloutRunner",
    "ReRolloutResult",
    "ReRolloutTask",
    "ReRolloutUnavailable",
    "RolloutRow",
    "SkillOptState",
    "SkillOptOutcome",
    "SkillOptRequest",
    "apply_edits",
    "apply_slow_update",
    "budget_at",
    "clip_to_budget",
    "default_proposer",
    "default_slow_update",
    "make_llm_proposer",
    "lineage_path",
    "make_rerollout_gate",
    "outcome_reward",
    "promoted_skill_dir",
    "promoted_skill_path",
    "run",
    "run_optimization",
    "score_skill_on_rows",
    "split_rows_by_hash",
    "split_success_failure",
    "tag_deficit_weights",
]
