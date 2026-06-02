"""Run the verifier factory for one (skill, archetype) against the real bucket.

Usage: python run_one.py <skill> <archetype_id> [out_dir]
Prints a single JSON object with the gate metrics + mined evidence signals.
"""
import json
import sys
import tempfile
from pathlib import Path

from opentraces.consumers.skill_intelligence import pipeline as si
from opentraces.core.bucket_store import iter_trace_record_objects
from opentraces.consumers.verifier_factory import (
    get_skill_examples,
    list_candidates,
    resolve_archetype,
    score_authored,
)

skill = sys.argv[1]
archetype_id = sys.argv[2]
out_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(tempfile.mkdtemp())

episodes = si.build_episode_rows(selected_skill=skill, min_rows=0)
records = {o.trace_id: o.record for o in iter_trace_record_objects()}

cand = list_candidates(skills=[skill], min_usable_episodes=4)
centry = cand["skills"][0]
carch = centry["archetypes"][0]

archetype = resolve_archetype(skill, archetype_id)
ex = get_skill_examples(skill, archetype_id, episodes=episodes, records=records)
pkg = score_authored(archetype, out_dir=out_dir / archetype_id, episodes=episodes)

print(json.dumps({
    "skill": skill,
    "archetype_id": archetype_id,
    "is_generic": pkg.is_generic,
    "usable_episodes": centry["usable_episodes"],
    "distinct_traces": centry["distinct_source_traces"],
    "leakage_safe": centry["leakage_safe_split"],
    "evidence_strength": carch["evidence_strength"],
    "deficient_marker_frequency": carch["deficient_marker_frequency"],
    "examples_total": ex["examples_total"],
    "counterexamples_total": ex["counterexamples_total"],
    "markers": list(archetype.markers),
    "dsel_before": pkg.dsel_before,
    "dsel_after": pkg.dsel_after,
    "dtest_score": pkg.dtest_score,
    "accepted": pkg.accepted,
    "recommended": pkg.recommended,
    "gate_basis": pkg.gate_basis,
    "addressable_markers": list(pkg.addressable_markers),
    "split": pkg.split_counts,
    "limitations": list(pkg.limitations),
    "spec_path": str(pkg.spec_path),
}))
