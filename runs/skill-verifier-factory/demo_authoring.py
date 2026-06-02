"""End-to-end verifier-creator demo against the real bucket (skill: review)."""
import json, tempfile
from pathlib import Path

from opentraces.consumers.skill_intelligence import pipeline as si
from opentraces.core.bucket_store import iter_trace_record_objects
from opentraces.consumers.verifier_factory import (
    list_candidates, get_skill_examples, draft_archetype,
    author_archetype, score_authored, ArchetypeSpecError,
)

SKILL = "review"
print(f"# Loading real bucket evidence for skill={SKILL!r} ...")
episodes = si.build_episode_rows(selected_skill=SKILL, min_rows=0)
records = {o.trace_id: o.record for o in iter_trace_record_objects()}
print(f"  episodes={len(episodes)}  records_loaded={len(records)}\n")

print("## Tool 1: list_candidates (which markers are deficient)")
cand = list_candidates(skills=[SKILL], min_usable_episodes=4)
s = cand["skills"][0]; a = s["archetypes"][0]
print(f"  skill={s['skill_id']} usable={s['usable_episodes']} distinct_traces={s['distinct_source_traces']} leakage_safe={s['leakage_safe_split']}")
print(f"  archetype={a['archetype_id']} recommended={a['recommended']} evidence_strength={a['evidence_strength']}")
print(f"  deficient_marker_frequency={json.dumps(a['deficient_marker_frequency'])}\n")

print("## Tool 2: get_skill_examples (real example vs counterexample traces)")
ex = get_skill_examples(SKILL, a["archetype_id"], episodes=episodes, records=records, max_examples=2)
print(f"  examples_total={ex['examples_total']}  counterexamples_total={ex['counterexamples_total']}")
for tag, items in (("EXAMPLE", ex["examples"]), ("COUNTEREXAMPLE", ex["counterexamples"])):
    for it in items[:1]:
        print(f"  [{tag}] trace={it['trace_id'][:24]} completeness={it['contract_completeness']}")
        print(f"          present={it['present_markers']}")
        print(f"          deficient={it['deficient_markers']}")
        print(f"          pull window: {it['slice_command']}")
print()

print("## Tool 3: draft_archetype (editable, approval-required starting point)")
draft = draft_archetype(SKILL, a["archetype_id"], episodes=episodes, records=records)
print(f"  approval_required={draft['approval_required']}  elements={len(draft['archetype']['contract_elements'])}\n")

print("## Tool 4: author_archetype (agent contextualizes a DECLARATIVE spec)")
spec = {
    "archetype_id": "review_grounded_contextualized_v1",
    "skill": SKILL,
    "title": "Review with grounded, cited findings (contextualized)",
    "semantic_target": "findings cite real files/lines in the diff, verdict matches the landed outcome",
    "baseline_skill": "# review (baseline)\n\nReview the change.\n",
    "contract_elements": [
        {"element_id": "grounded", "label": "References real touched files",
         "marker": "rl.review.grounded_findings", "weight": 1.5,
         "detectors": {"has_files": True, "command_families": ["search_read"],
                       "text_any": ["file", "diff", "hunk"]}},
        {"element_id": "cited", "label": "Cites file:line evidence",
         "marker": "rl.review.cite_file_line",
         "detectors": {"command_any": [".py:", ".ts:", "line "],
                       "text_any": ["line", "evidence", "cite", "reference"]}},
        {"element_id": "landed", "label": "Verdict consistent with landed outcome",
         "marker": "rl.review.downstream_outcome",
         "detectors": {"committed": True, "outcome_reward_min": 0.5, "outcome_label_in": ["success"]}},
    ],
    "creator_questions": [], "live_legs": [],
}
authored = author_archetype(spec)
print(f"  built archetype markers={list(authored.archetype.markers)}")
print(f"  permissiveness warnings={list(authored.warnings)}  auto_promote={authored.auto_promote}\n")

print("## Tool 5: score_authored (deterministic SkillOpt gate)")
with tempfile.TemporaryDirectory() as d:
    pkg = score_authored(authored, out_dir=Path(d) / "pkg", episodes=episodes)
    print(f"  Dsel {pkg.dsel_before:.3f} -> {pkg.dsel_after:.3f}   Dtest(held-out)={pkg.dtest_score:.3f}")
    print(f"  accepted={pkg.accepted}  gate_basis={pkg.gate_basis}  recommended={pkg.recommended}")
    print(f"  addressable_markers={list(pkg.addressable_markers)}")
    print(f"  split={pkg.split_counts}  limitations={list(pkg.limitations)}\n")

print("## Trust boundary in action")
try:
    author_archetype({**spec, "reward": 1.0})
    print("  injecting reward: NOT REJECTED  <-- BUG")
except ArchetypeSpecError as e:
    print(f"  injecting `reward` into spec -> REJECTED: {e}")

broad = {**spec, "archetype_id": "loose_v1",
         "contract_elements": [{"element_id": "any", "label": "touched anything",
                                "marker": "rl.review.any", "detectors": {"has_files": True}}]}
authored_broad = author_archetype(broad)
with tempfile.TemporaryDirectory() as d:
    pkg2 = score_authored(authored_broad, out_dir=Path(d) / "pkg", episodes=episodes)
    print(f"  over-permissive spec -> warnings={authored_broad.warnings[:1]}")
    print(f"  over-permissive package -> recommended={pkg2.recommended} (flagged: {[l for l in pkg2.limitations if 'permissive' in l][:1]})")
