"""Empirically confirm the spec's honest finding on the REAL bucket (Phase-2 calibration).

For each seed skill: build the degenerate (all-deterministic) rubric from its archetype and
calibrate (a) with NO labels and (b) with EMULATED labels (transparent stand-in, not real
gold). Print the status + key metrics. Demonstrates: BLOCKED is the honest default; the
machine flips toward calibrated/provisional when (emulated) labels arrive.
"""
import json

from opentraces.consumers.skill_intelligence import pipeline as si
from opentraces.core.bucket_store import iter_trace_record_objects
from opentraces.consumers.verifier_factory import (
    GENERIC_ARCHETYPE_ID, calibrate_rubric, emulate_human_labels, get_archetype,
    generic_archetype_for, rubric_from_archetype,
)

EXAMPLES = [
    ("goal-forge", "goal_contract_downstream_outcome_v1"),
    ("tdd", "tdd_red_green_refactor_v1"),
    ("review", "review_grounded_findings_v1"),
    ("docs-update", "docs_update_reflects_change_v1"),
    ("architecture-patterns", GENERIC_ARCHETYPE_ID),
]

print("# loading bucket ...")
records = {o.trace_id: o.record for o in iter_trace_record_objects()}
print(f"  records={len(records)}\n")

print(f"{'skill':22} {'no-labels status':30} {'+emulated status':26} auc  n_eff_neg")
for skill, aid in EXAMPLES:
    arch = generic_archetype_for(skill) if aid == GENERIC_ARCHETYPE_ID else get_archetype(aid)
    rub = rubric_from_archetype(arch)
    eps = si.build_episode_rows(selected_skill=skill, min_rows=0)
    recs = {tid: records[tid] for tid in (str(e.get("source_trace_id") or "") for e in eps) if tid in records}

    bare = calibrate_rubric(rub, episodes=eps, records=recs)
    emulated = emulate_human_labels(eps, recs)
    n_pos = sum(emulated.values()); n_neg = len(emulated) - n_pos
    labeled = calibrate_rubric(rub, episodes=eps, records=recs, human_labels=emulated,
                               gold_is_emulated=True)
    auc = labeled.auc_human
    print(f"{skill:22} {bare.status:30} {labeled.status:26} "
          f"{('%.2f' % auc) if auc is not None else '  - ':>4} {labeled.n_eff_neg:>6} "
          f"(emul +{n_pos}/-{n_neg})")

print("\nNote: emulated labels are a transparent stand-in to exercise the pipeline, NOT real")
print("human gold; any 'calibrated' here is a pipeline demo and stays recommended=False.")
