"""Dump the FULL autoverify breakdown for one skill (real bucket) as JSON — case-study input.

Usage: python autoverify_case.py <skill>
Emits: the self-aligned rubric (criteria + detectors + the agent criterion's rubric_text), the
bare autoverify calibration (status/blockers/per-criterion/auc/rho/n_eff_neg), and the
+emulated-gold calibration (transparent stand-in), plus example/counterexample counts.
"""
import json
import sys

from opentraces.consumers.skill_intelligence import pipeline as si
from opentraces.core.bucket_store import iter_trace_record_objects
from opentraces.consumers.verifier_factory import (
    autoverify, autoverify_draft_rubric, emulate_human_labels, get_skill_examples,
)

skill = sys.argv[1]
episodes = si.build_episode_rows(selected_skill=skill, min_rows=0)
records = {o.trace_id: o.record for o in iter_trace_record_objects()
          if o.trace_id in {str(e.get("source_trace_id") or "") for e in episodes}}

rub = autoverify_draft_rubric(skill, episodes=episodes, records=records)
bare = autoverify(skill, episodes=episodes, records=records)
labels = emulate_human_labels(episodes, records)
emul = autoverify(skill, episodes=episodes, records=records, human_labels=labels, gold_is_emulated=True)
# get_skill_examples resolves an ARCHETYPE id; pass None so it uses the generic scaffold
# (the autoverify rubric_id is not a registered archetype).
ex = get_skill_examples(skill, None, episodes=episodes, records=records, max_examples=2)

print(json.dumps({
    "skill": skill,
    "episodes": len(episodes),
    "rubric": {
        "rubric_id": rub.rubric_id,
        "semantic_target": rub.semantic_target,
        "criteria": [
            {"id": c.id, "marker": c.marker, "judge_method": c.judge_method,
             "direction": c.direction, "weight": c.weight,
             "detectors": c.detectors.to_dict(),
             "rubric_text": c.rubric_text[:240]} for c in rub.criteria
        ],
    },
    "autoverify_bare": {
        "status": bare.calibration.status, "recommended": bare.recommended,
        "auc_human": bare.calibration.auc_human, "rho_secondary": bare.calibration.rho_secondary,
        "n_eff_neg": bare.calibration.n_eff_neg, "n_human_labels": bare.calibration.n_human_labels,
        "blockers": list(bare.calibration.blockers),
        "per_criterion": [dict(r) for r in bare.calibration.per_criterion],
    },
    "autoverify_emulated_gold": {
        "status": emul.calibration.status, "recommended": emul.recommended,
        "auc_human": emul.calibration.auc_human, "n_eff_neg": emul.calibration.n_eff_neg,
        "blockers": list(emul.calibration.blockers),
        "per_criterion": [dict(r) for r in emul.calibration.per_criterion],
        "emulated_pos": sum(labels.values()), "emulated_neg": len(labels) - sum(labels.values()),
    },
    "examples_total": ex["examples_total"],
    "counterexamples_total": ex["counterexamples_total"],
}, indent=2))
