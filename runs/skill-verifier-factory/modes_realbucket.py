"""Two-mode demo on the real bucket: AUTOVERIFY (self-align) vs MANUAL (+ emulated labels).

Shows the trust ceiling in action: autoverify self-aligns a rubric from each skill's goal and
caps at provisional/blocked (recommended=False); supplying (emulated) human labels lifts it to
calibrated. Emulated labels are a transparent stand-in, NOT real gold.
"""
from opentraces.consumers.skill_intelligence import pipeline as si
from opentraces.core.bucket_store import iter_trace_record_objects
from opentraces.consumers.verifier_factory import autoverify, emulate_human_labels

SKILLS = ["goal-forge", "tdd", "review", "docs-update", "architecture-patterns"]

print("# loading bucket ...")
records = {o.trace_id: o.record for o in iter_trace_record_objects()}
print(f"  records={len(records)}\n")

print(f"{'skill':22} {'AUTOVERIFY (self-align)':30} rec   {'MANUAL (+emulated gold)':28} rec")
for skill in SKILLS:
    eps = si.build_episode_rows(selected_skill=skill, min_rows=0)
    recs = {t: records[t] for t in (str(e.get('source_trace_id') or '') for e in eps) if t in records}

    auto = autoverify(skill, episodes=eps, records=recs)
    labels = emulate_human_labels(eps, recs)
    # emulated gold is a transparent stand-in: gold_is_emulated=True keeps the ceiling at
    # provisional (it can never launder into `calibrated`). Real human labels would omit the flag.
    manual = autoverify(skill, episodes=eps, records=recs, human_labels=labels, gold_is_emulated=True)

    print(f"{skill:22} {auto.calibration.status:30} {str(auto.recommended):5} "
          f"{manual.calibration.status:28} {str(manual.recommended):5}")

print("\nAUTOVERIFY caps at provisional_weak_only (or blocked) and recommended=False by construction.")
print("MANUAL (+ human gold) can reach calibrated. Emulated labels are a flagged demo, not gold.")
