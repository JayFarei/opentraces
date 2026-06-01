"""Per-finding experiment driver (br/69 Exp 4 + the user's reframe).

Input: the per-finding-extraction workflow's findings JSON (agent CANDIDATES, NOT labels).
Steps:
  1. Build each trace's evidence blob (what the review actually read).
  2. Run the DETERMINISTIC citation-groundedness leg (zero circularity): which candidate
     hallucinations are fabricated citations (machine-caught) vs semantic misreads (routed to
     ratification).
  3. Emit the per-finding dataset + the honest counts (does per-finding manufacture a candidate
     negative class the whole-trace label discarded?).
  4. Assemble the ~8-finding HUMAN ratification queue: the residual semantic candidates + matched
     confident positives, ranked by agent confidence + diversity. The human ratifies — never the agent.

Usage: python process_experiment.py <findings.json>
"""
import json
import sys
from collections import Counter
from pathlib import Path

from opentraces.consumers.verifier_factory.groundedness import check_finding, repo_file_basenames

findings_path = sys.argv[1]
wf = json.load(open(findings_path))
data = wf.get("data", wf)  # the workflow returns {data: [...]}
by_trace = {t["trace_id"]: t for t in data}
trace_ids = list(by_trace)

# ground-truth citation universe = the repo filesystem (zero circularity, no bucket needed).
# Checking against the repo (not the truncated trace packet) fixes the false-fabrication bug.
REPO = str(Path(__file__).resolve().parents[2])
present = repo_file_basenames(REPO)
print(f"# ground-truth universe: {len(present)} real file basenames in the repo")

# per-finding dataset with deterministic groundedness
rows = []
for tid in trace_ids:
    findings = by_trace[tid].get("findings", []) or []
    for f in findings:
        v = check_finding(f, present)
        rows.append({
            "trace_id": tid, "finding_id": f.get("finding_id"),
            "kind": f.get("finding_kind"), "suspicion": f.get("groundedness_suspicion"),
            "confidence": f.get("confidence"), "claim_quote": (f.get("claim_quote") or "")[:300],
            "cited_paths": f.get("cited_paths", []), "why": (f.get("why") or "")[:200],
            "citation_grounded": v.citation_grounded, "det_label": v.label,
            "unread_citations": list(v.unread_citations),
        })

n = len(rows)
susp = [r for r in rows if r["suspicion"] in ("suspect_misread", "fabricated_citation")]
det_fab = [r for r in rows if r["det_label"] == "fabricated_citation"]
# residual = agent-suspected NON-grounded that the deterministic leg did NOT catch -> ratification
residual = [r for r in susp if r["det_label"] != "fabricated_citation"]
positives = [r for r in rows if r["suspicion"] == "grounded"]

print(f"# Per-finding experiment — review ({len(trace_ids)} traces)")
print(f"total findings (per-finding granularity): {n}   (vs 10 whole-trace labels)")
print(f"suspicion breakdown: {dict(Counter(r['suspicion'] for r in rows))}")
print(f"candidate negatives (agent-suspected non-grounded): {len(susp)}")
print(f"DETERMINISTIC fabricated-citation catches (zero circularity): {len(det_fab)}")
for r in det_fab:
    print(f"   [det-neg] {r['trace_id'][:8]}/{r['finding_id']} unread={r['unread_citations']} :: {r['claim_quote'][:80]!r}")
print(f"residual SEMANTIC candidates (need human ratification): {len(residual)}")

# 4. ratification queue: residual semantic candidates + matched confident positives
def conf(r):
    try: return float(r.get("confidence") or 0)
    except Exception: return 0.0
residual_sorted = sorted(residual, key=conf, reverse=True)
pos_sorted = sorted(positives, key=conf, reverse=True)
queue = residual_sorted[:5] + pos_sorted[:3]
print(f"\n# RATIFICATION QUEUE (~{len(queue)} findings for a human to confirm — agent never self-labels)")
for i, r in enumerate(queue, 1):
    print(f"  Q{i} [{r['suspicion']}|det:{r['det_label']}|conf {conf(r):.2f}] {r['trace_id'][:8]}/{r['finding_id']}")
    print(f"      claim: {r['claim_quote'][:160]!r}")
    print(f"      why:   {r['why'][:140]}")

out = {"n_findings": n, "suspicion": dict(Counter(r['suspicion'] for r in rows)),
       "candidate_negatives": len(susp), "deterministic_fabrications": len(det_fab),
       "residual_semantic": len(residual), "rows": rows, "ratification_queue": queue}
json.dump(out, open("runs/skill-verifier-factory/per_finding_dataset.json", "w"), indent=2)
print("\nwrote runs/skill-verifier-factory/per_finding_dataset.json")
