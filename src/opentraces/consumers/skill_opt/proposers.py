"""Edit proposers for the SkillOpt optimizer loop (arXiv 2605.23904).

A *proposer* is the reflection step (Algorithm 1 lines 8-12): given the current
skill, a batch of scored-rollout train rows, the edit budget, and the
rejected-edit buffer, it returns a ranked list of candidate edit dicts (the
four-op patch grammar in :mod:`.engine`). The optimizer loop clips that list to
the scheduled budget, applies it, and gates the candidate.

Two proposers live here:

* :func:`default_proposer` , deterministic, no model call. It splits rows into
  success/failure by reward, ranks the failure tags by reward deficit, skips
  tags already addressed or previously rejected, and proposes one ``append``
  rule each. This is the proposer ``workflow optimize`` wires by default and the
  one tests assert against, so the loop is reproducible with zero network.
* :func:`make_llm_proposer` , the full Appendix C.2 chain (failure analysis ->
  success analysis -> hierarchical merge -> ranking) over a caller-supplied
  ``complete(prompt) -> str`` client, consuming the rejected buffer as negative
  feedback. :class:`DeterministicOptimizerClient` is the offline fake the tests
  and the default CLI path use, so no real model is required to exercise it.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Sequence

from .engine import (
    RULE_MARKER,
    SLOW_END,
    SLOW_START,
    RolloutRow,
    _skill_addresses_tag,
    failure_tags_of,
    split_success_failure,
    tag_deficit_weights,
)

# Edits encode their addressed tag in the rule marker, so the rejected buffer can
# be read back into a tag set without extra bookkeeping.
_RULE_RE = re.compile(r"rule\[(?P<tag>.+?)\]")


def _rejected_tags(rejected: Sequence[dict]) -> set[str]:
    """Tags whose rule was already tried and rejected (negative feedback)."""
    tags: set[str] = set()
    for entry in rejected or ():
        for edit in entry.get("edits", ()):
            for match in _RULE_RE.finditer(str(edit.get("content", ""))):
                tags.add(match.group("tag"))
    return tags


def _rule_edit(tag: str, *, support: int, source: str = "failure") -> dict:
    return {
        "op": "append",
        "content": (
            f"- {RULE_MARKER.format(tag=tag)}: address recurring failure `{tag}` "
            f"observed across {support} low-reward rollout(s)."
        ),
        "support_count": support,
        "source_type": source,
    }


def default_proposer(
    current_skill: str,
    train_rows: list[RolloutRow],
    _budget_hint: int,
    rejected: Sequence[dict] = (),
    *,
    meta_skill: str = "",
) -> list[dict]:
    """Deterministic reflection over the failure minibatch.

    Splits rows by reward, ranks the failure rows' tags by reward deficit
    (:func:`.engine.failure_tags_of`), and proposes one ``append`` rule per tag
    that is neither already addressed by the skill nor in the rejected buffer.
    Skipping covered and rejected tags makes the loop converge and turns the
    buffer into real negative feedback.
    """
    del meta_skill
    _success, failure = split_success_failure(train_rows)
    if not failure:
        return []
    rows = failure
    counts: dict[str, int] = {}
    for row in rows:
        for tag in row.failure_tags:
            counts[tag] = counts.get(tag, 0) + 1
    blocked = _rejected_tags(rejected)
    edits: list[dict] = []
    for tag in failure_tags_of(rows):
        if _skill_addresses_tag(current_skill, tag) or tag in blocked:
            continue
        edits.append(_rule_edit(tag, support=counts.get(tag, 0)))
    return edits


# ---------------------------------------------------------------------------
# LLM-backed proposer: the full Appendix C.2 chain
# ---------------------------------------------------------------------------
#
# Each stage is a strict-JSON completion. We append a machine-readable
# ``STAGE``/``CONTEXT`` tail to every prompt: a real optimizer model follows the
# natural-language instructions and the offline DeterministicOptimizerClient
# reads the tail, so the same chain runs with or without a live model.

_STAGES = (
    "failure_analysis",   # C.2.1 analyst_error
    "success_analysis",   # C.2.2 analyst_success
    "merge_failure",      # C.2.3 merge_failure
    "merge_success",      # C.2.4 merge_success
    "merge_final",        # C.2.5 merge_final
    "ranking",            # C.2.6 ranking
)

_STAGE_INSTRUCTIONS = {
    "failure_analysis": (
        "You are a failure-analysis agent. Identify the COMMON failure patterns "
        "across the failed rollouts and propose at most {budget} generalizable "
        "skill edits. Do not repeat edits listed as previously rejected. Never "
        f"edit text between {SLOW_START} and {SLOW_END}."
    ),
    "success_analysis": (
        "You are a success-pattern analyst. Encode behaviors common across "
        "successful rollouts that are NOT already in the skill. Be conservative."
    ),
    "merge_failure": "Merge the failure-driven patches into one non-redundant patch.",
    "merge_success": "Merge the success-driven patches into one conservative patch.",
    "merge_final": (
        "Final merge: failure patches take priority; add success patches only "
        "where they cover patterns the failure patches do not."
    ),
    "ranking": (
        "Rank the merged edits by systematic impact, complementarity, generality, "
        "and actionability; return them in priority order."
    ),
}


def _stage_prompt(stage: str, current_skill: str, context: dict, *, meta_skill: str = "") -> str:
    meta_block = (
        f"OPTIMIZER META-SKILL (teacher-only; do not copy into the deployed skill):\n{meta_skill}\n\n"
        if meta_skill
        else ""
    )
    return (
        f"{_STAGE_INSTRUCTIONS[stage].format(budget=context.get('budget', 0))}\n\n"
        f"{meta_block}"
        f"CURRENT SKILL:\n{current_skill}\n\n"
        'Respond ONLY with JSON: {"edits": [{"op": "...", "target": "...", '
        '"content": "...", "support_count": <int>, "source_type": "..."}]}\n\n'
        f"STAGE: {stage}\nCONTEXT: {json.dumps(context, sort_keys=True)}\n"
    )


def _call_edits(complete: Callable[[str], str], prompt: str) -> list[dict]:
    try:
        payload = json.loads(complete(prompt))
    except Exception:
        return []
    edits = payload.get("edits") if isinstance(payload, dict) else None
    return [e for e in (edits or []) if isinstance(e, dict) and e.get("op")]


def make_llm_proposer(
    complete: Callable[[str], str],
) -> Callable[[str, list[RolloutRow], int, Sequence[dict]], list[dict]]:
    """Build a proposer that runs the full C.2 chain over ``complete``.

    failure analysis + success analysis (separate minibatches) -> per-source
    merge -> failure-prioritized final merge -> ranking. The rejected buffer is
    passed into the failure-analysis context as negative feedback.
    """

    def _proposer(
        current_skill: str,
        train_rows: list[RolloutRow],
        budget_hint: int,
        rejected: Sequence[dict] = (),
        *,
        meta_skill: str = "",
    ) -> list[dict]:
        success_rows, failure_rows = split_success_failure(train_rows)
        weights = tag_deficit_weights(failure_rows or train_rows)
        covered = sorted(t for t in weights if _skill_addresses_tag(current_skill, t))

        failure_ctx = {
            "stage": "failure_analysis",
            "budget": budget_hint,
            "tags": failure_tags_of(failure_rows or train_rows),
            "tag_weights": {t: round(w, 6) for t, w in weights.items()},
            "tag_counts": _tag_counts(failure_rows or train_rows),
            "covered": covered,
            "rejected": sorted(_rejected_tags(rejected)),
        }
        failure_edits = _call_edits(
            complete, _stage_prompt("failure_analysis", current_skill, failure_ctx, meta_skill=meta_skill)
        )
        success_ctx = {
            "stage": "success_analysis",
            "success_tags": failure_tags_of(success_rows) if success_rows else [],
            "covered": covered,
        }
        success_edits = _call_edits(
            complete, _stage_prompt("success_analysis", current_skill, success_ctx, meta_skill=meta_skill)
        )

        merged_failure = _call_edits(
            complete, _stage_prompt("merge_failure", current_skill, {"edits": failure_edits}, meta_skill=meta_skill)
        )
        merged_success = _call_edits(
            complete, _stage_prompt("merge_success", current_skill, {"edits": success_edits}, meta_skill=meta_skill)
        )
        pool = _call_edits(
            complete,
            _stage_prompt(
                "merge_final",
                current_skill,
                {"failure_edits": merged_failure, "success_edits": merged_success},
                meta_skill=meta_skill,
            ),
        )
        ranked = _call_edits(
            complete,
            _stage_prompt("ranking", current_skill, {"edits": pool, "budget": budget_hint}, meta_skill=meta_skill),
        )
        return ranked

    return _proposer


class DeterministicOptimizerClient:
    """Offline ``complete(prompt) -> str`` fake that drives the C.2 chain.

    Reads the ``STAGE``/``CONTEXT`` tail of each prompt and returns deterministic
    JSON, so the full chain runs with no network or model. Records the stages it
    saw in :attr:`stages` for test assertions. This is what the default
    ``workflow optimize`` LLM path and the tests use; a real model would ignore
    the scaffold tail and follow the natural-language instructions instead.
    """

    def __init__(self) -> None:
        self.stages: list[str] = []

    def __call__(self, prompt: str) -> str:
        stage, context = self._parse(prompt)
        self.stages.append(stage)
        if stage == "failure_analysis":
            blocked = set(context.get("covered", [])) | set(context.get("rejected", []))
            weights = context.get("tag_weights", {})
            counts = context.get("tag_counts", {})
            edits = [
                _rule_edit(tag, support=max(1, int(counts.get(tag) or round(weights.get(tag, 1) or 1))))
                for tag in context.get("tags", [])
                if tag not in blocked
            ]
            return json.dumps({"edits": edits})
        if stage == "success_analysis":
            # Conservative: reinforce nothing the failure path will already add.
            return json.dumps({"edits": []})
        if stage == "merge_failure":
            return json.dumps({"edits": _dedup(context.get("edits", []))})
        if stage == "merge_success":
            return json.dumps({"edits": _dedup(context.get("edits", []))})
        if stage == "merge_final":
            return json.dumps(
                {"edits": _dedup(list(context.get("failure_edits", [])) + list(context.get("success_edits", [])))}
            )
        if stage == "ranking":
            ranked = sorted(
                context.get("edits", []),
                key=lambda e: (-int(e.get("support_count", 0)), str(e.get("content", ""))),
            )
            return json.dumps({"edits": ranked})
        return json.dumps({"edits": []})

    @staticmethod
    def _parse(prompt: str) -> tuple[str, dict]:
        stage = ""
        context: dict = {}
        for line in prompt.splitlines():
            if line.startswith("STAGE: "):
                stage = line[len("STAGE: "):].strip()
            elif line.startswith("CONTEXT: "):
                try:
                    context = json.loads(line[len("CONTEXT: "):])
                except Exception:
                    context = {}
        return stage, context


def _dedup(edits: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for edit in edits:
        key = str(edit.get("content", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(edit)
    return out


def _tag_counts(rows: list[RolloutRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for tag in row.failure_tags:
            counts[tag] = counts.get(tag, 0) + 1
    return counts
