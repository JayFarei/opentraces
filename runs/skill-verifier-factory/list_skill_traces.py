"""List a skill's trace usages + the heuristic label, so an agent can judge each with full content.

Usage: python list_skill_traces.py <skill>
Prints JSON: [{trace_id, unit_id, intent, summary, files, tools, outcome_label, committed,
heuristic_effective}] where heuristic_effective = the current emulate_human_labels rule
(committed AND verification ran) — the rule that found 0 negatives for `review`.
"""
import json
import sys

from opentraces.consumers.skill_intelligence import pipeline as si
from opentraces.core.bucket_store import iter_trace_record_objects
from opentraces.consumers.verifier_factory import emulate_human_labels
from opentraces.consumers.verifier_factory.archetypes import evidence_from_episode

skill = sys.argv[1]
episodes = si.build_episode_rows(selected_skill=skill, min_rows=0)
records = {o.trace_id: o.record for o in iter_trace_record_objects()
          if o.trace_id in {str(e.get("source_trace_id") or "") for e in episodes}}
heur = emulate_human_labels(episodes, records)

rows = []
for ep in episodes:
    tid = str(ep.get("source_trace_id") or "")
    ev = evidence_from_episode(ep, records.get(tid))
    rows.append({
        "trace_id": tid,
        "unit_id": str(ep.get("source_unit_id") or ""),
        "intent": (ep.get("user_intent") or "")[:400],
        "summary": (ep.get("task_summary") or "")[:200],
        "files": list(ev.files)[:12],
        "tools": list(ev.tools)[:12],
        "command_families": sorted(ev.command_families),
        "outcome_label": ev.outcome_label,
        "committed": ev.committed,
        "heuristic_effective": heur.get(tid),
    })
print(json.dumps({"skill": skill, "n": len(rows),
                  "heuristic_pos": sum(heur.values()), "heuristic_neg": len(heur) - sum(heur.values()),
                  "traces": rows}, indent=2))
