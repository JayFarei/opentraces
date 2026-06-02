"""Self-measurement: does the verifier DISCRIMINATE, and is it gameable?

Two questions the green Dsel/Dtest=1.0 numbers do NOT answer:
  A) Do the (non-outcome) contract detectors actually track the trace's INDEPENDENT
     outcome? i.e. do process-quality markers separate landed/success traces from
     not? If not, the verifier is keyword-matching noise.
  B) Can a garbage skill that just stuffs the marker tokens score 1.0? (reward hacking)
"""
import statistics as st
from opentraces.consumers.skill_intelligence import pipeline as si
from opentraces.core.bucket_store import iter_trace_record_objects
from opentraces.consumers.verifier_factory import resolve_archetype, scorers
from opentraces.consumers.verifier_factory.archetypes import GENERIC_ARCHETYPE_ID

EXAMPLES = [
    ("goal-forge", "goal_contract_downstream_outcome_v1"),
    ("tdd", "tdd_red_green_refactor_v1"),
    ("review", "review_grounded_findings_v1"),
    ("docs-update", "docs_update_reflects_change_v1"),
    ("architecture-patterns", GENERIC_ARCHETYPE_ID),
]

print("# loading records once ...")
records = {o.trace_id: o.record for o in iter_trace_record_objects()}
print(f"  records={len(records)}\n")

print("## (A) Discrimination: mean PROCESS completeness (excluding the outcome/landed element)")
print("##     split by the trace's INDEPENDENT outcome label (success vs failure)")
print(f"{'skill':22} {'n':>4} {'succ_mean':>9} {'fail_mean':>9} {'separation':>10}  verdict")
for skill, aid in EXAMPLES:
    arch = resolve_archetype(skill, aid)
    # the element whose detector is defined BY the outcome (circular) — exclude it
    outcome_markers = {el.marker for el in arch.contract_elements
                       if el.detectors.committed or el.detectors.outcome_reward_min is not None
                       or el.detectors.outcome_label_in}
    process_markers = [m for m in arch.markers if m not in outcome_markers]
    eps = si.build_episode_rows(selected_skill=skill, min_rows=0)
    succ, fail = [], []
    for ep in eps:
        rec = records.get(str(ep.get("source_trace_id") or ""))
        es = scorers.score_episode(arch, ep, rec)
        # process-only completeness = fraction of process markers present
        present = set(es.present_markers)
        if not process_markers:
            continue
        pc = sum(1 for m in process_markers if m in present) / len(process_markers)
        (succ if str(ep.get("outcome_label")) == "success" else fail).append(pc)
    sm = round(st.mean(succ), 3) if succ else float("nan")
    fm = round(st.mean(fail), 3) if fail else float("nan")
    sep = round(sm - fm, 3) if succ and fail else float("nan")
    verdict = ("no signal (no separation)" if (succ and fail and abs(sep) < 0.05)
               else "some signal" if (succ and fail and sep > 0.05)
               else "INVERTED" if (succ and fail and sep < -0.05)
               else "cannot split (one class empty)")
    print(f"{skill:22} {len(eps):>4} {sm:>9} {fm:>9} {sep:>10}  {verdict}")

print("\n## (B) Reward-hacking: can a garbage skill stuffed with marker tokens score 1.0?")
arch = resolve_archetype("review", "review_grounded_findings_v1")
garbage = "I will do nothing useful.\n" + "\n".join(f"rule[{m}]" for m in arch.markers)
print(f"  garbage-skill-with-all-markers score_skill = {scorers.score_skill(arch, garbage)}")
print(f"  (the deterministic gate that produced Dsel/Dtest=1.0 would PASS this skill)")
