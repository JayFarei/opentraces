"""Phase-0 reviewer-input prep + mechanical worst-case surfacer (issue #141).

Produces, for the 36-trace cheap-LLM subset:
  - review_input/<trace_id>.json : compact step-view + the 4 trajectory sets +
    S3/S4 JudgmentRequests, sized to fit an agent's context.
The reviewer agent reads these and scores sentiment conformance + 3-persona
utility per slicer.

Also computes mechanical quality signals over BOTH samples and surfaces
anti-cherry-pick worst cases (giant single-trajectory traces, S1 openers that
slipped the LOCKED blocklist, over-segmentation), independent of the LLM panel.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from opentraces_schema import load_record_dict

import proto
import verify

HERE = Path(__file__).resolve().parent
RIN = HERE / "review_input"

# Openers that are NOT in the LOCKED S1 blocklist but are clearly non-genuine
# (surfaced honestly as a known S1 limitation, not a tiling bug).
_LEAKY_OPENER = re.compile(r"^\s*(<(system-reminder|local-command|command-name|command-message)\b|\[Request interrupted)")


def load_record(path: str):
    return load_record_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def step_digest(steps, mark: set[int], cap: int = 240) -> list[str]:
    """Compact one-line-per-step digest. For long traces keep all marked steps
    (+/-1 neighbor) and evenly sample the rest, with omission markers."""
    n = len(steps)
    keep = set()
    for m in mark:
        for d in (-1, 0, 1):
            if 0 <= m + d < n:
                keep.add(m + d)
    # always keep user + success steps
    for i, s in enumerate(steps):
        if proto._role(s) == "user":
            keep.add(i)
    for i, _ in proto.success_steps(steps):
        keep.add(i)
    if len(keep) < cap:
        step = max(1, n // (cap - len(keep) + 1))
        for i in range(0, n, step):
            keep.add(i)
    lines = []
    prev = -1
    for i in sorted(keep):
        if i > prev + 1:
            lines.append(f"   ... ({i - prev - 1} steps omitted) ...")
        s = steps[i]
        role = proto._role(s)
        tools = ",".join(sorted(set(proto._tool_names(s))))[:50]
        if role == "user":
            prev_text = proto._content(s).strip().replace("\n", " ")[:90]
        else:
            prev_text = (proto._content(s) or proto._obs_text(s) or "").strip().replace("\n", " ")[:70]
        lines.append(f"{i:4d} {role[:5]:5s} [{tools}] {prev_text}")
        prev = i
    return lines


def mechanical_signals(steps, slicer: str):
    fn = proto.SLICERS[slicer]
    trajs, requests = fn(steps)
    tj = [t.to_dict() for t in trajs]
    total = len(steps)
    tiling = verify.recompute_tiling(tj, total)
    # leaky S1 openers
    leaky = 0
    if slicer == "s1":
        starts = {t["start"] for t in tj}
        for i in starts:
            if i < total and proto._role(steps[i]) == "user":
                if _LEAKY_OPENER.match(proto._content(steps[i]).strip()):
                    leaky += 1
    seg_per_100 = round(len(tj) / max(total, 1) * 100, 2)
    return {
        "n_trajectories": len(tj),
        "valid": tiling["valid"],
        "single_trajectory": len(tj) == 1 and total > 30,
        "leaky_openers": leaky,
        "seg_per_100_steps": seg_per_100,
        "n_requests": len(requests),
        "trajectories": tj,
        "requests": requests,
    }


def main():
    RIN.mkdir(exist_ok=True)
    index = {r["trace_id"]: r for r in json.loads((HERE / "sample_index.json").read_text())}
    llm_manifest = json.loads((HERE / "manifest_llm.json").read_text())
    det_manifest = json.loads((HERE / "manifest_det.json").read_text())

    # --- mechanical signals over the full 200 (for the report aggregate) ---
    agg = {sl: {"single_trajectory": [], "leaky_openers": 0, "leaky_traces": [], "seg": []} for sl in proto.SLICERS}
    for row in det_manifest["traces"]:
        idx = index[row["trace_id"]]
        steps = load_record(idx["path"]).steps
        for sl in proto.SLICERS:
            sig = mechanical_signals(steps, sl)
            if sig["single_trajectory"]:
                agg[sl]["single_trajectory"].append(row["trace_id"])
            if sig["leaky_openers"]:
                agg[sl]["leaky_openers"] += sig["leaky_openers"]
                agg[sl]["leaky_traces"].append((row["trace_id"], sig["leaky_openers"]))
            agg[sl]["seg"].append(sig["seg_per_100_steps"])
    for sl in agg:
        seg = agg[sl]["seg"]
        agg[sl]["mean_seg_per_100"] = round(sum(seg) / len(seg), 3) if seg else 0
        agg[sl]["single_trajectory_count"] = len(agg[sl]["single_trajectory"])
        agg[sl].pop("seg")
    (HERE / "mechanical_signals.json").write_text(json.dumps(agg, indent=2, default=list))

    # --- review inputs for the 36 subset ---
    review_rows = []
    for row in llm_manifest["traces"]:
        tid = row["trace_id"]
        idx = index[tid]
        record = load_record(idx["path"])
        steps = record.steps
        total = len(steps)
        slicer_out = {}
        marks: set[int] = set()
        for sl in proto.SLICERS:
            sig = mechanical_signals(steps, sl)
            slicer_out[sl] = {
                "tier": proto.SLICER_TIER[sl],
                "trajectories": sig["trajectories"],
                "requests": sig["requests"],
                "n_trajectories": sig["n_trajectories"],
            }
            for t in sig["trajectories"]:
                marks.add(t["start"])
                marks.add(t["end"])
        digest = step_digest(steps, marks)
        payload = {
            "trace_id": tid,
            "project": row["project"],
            "agent": row["agent"],
            "length_bucket": row["length_bucket"],
            "total_steps": total,
            "flags": row["flags"],
            "step_view": digest,
            "slicers": slicer_out,
        }
        (RIN / f"{tid}.json").write_text(json.dumps(payload, indent=2))
        review_rows.append(tid)
    (HERE / "review_manifest.json").write_text(json.dumps({"traces": review_rows}, indent=2))
    print(f"wrote {len(review_rows)} review inputs to review_input/")
    print("mechanical signals (200-sample):")
    for sl in proto.SLICERS:
        a = agg[sl]
        print(f"  [{sl}] single_traj={a['single_trajectory_count']}  leaky_openers={a['leaky_openers']}  mean_seg/100={a['mean_seg_per_100']}")


if __name__ == "__main__":
    main()
