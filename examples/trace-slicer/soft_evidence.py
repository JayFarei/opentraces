"""Trace Slicer Library — on-demand soft-evidence panel (issue #141, U5).

NOT a CI gate. Runs the four shipped slicers (opentraces.core.slicing) over a
deterministic sample of real bucket traces and emits a conformance + utility
report. The mechanical signals (trajectory counts, segmentation density,
single-trajectory and S1 control-leak counts) are deterministic and always
computed; the per-persona utility + sentiment-conformance scoring is an
OPTIONAL LLM pass (``--llm``) routed through ``core.llm_provider.detect_provider``
(the same provider judge backend the cheap-LLM slicers use). The utility score
is advisory and never gates anything.

Usage:
    python examples/trace-slicer/soft_evidence.py [--sample N] [--llm] [--out report.md]
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from opentraces_schema import load_record_dict

from opentraces.core import slicing
from opentraces.core.slicing import steps as S

BUCKET = Path(os.path.expanduser("~/.opentraces/bucket/traces/v1"))
_LEAKY = re.compile(r"^\s*(<(system-reminder|local-command|command-name)\b|\[Request interrupted)")
SLICERS = ("s1", "s2", "s3", "s4")
SENTIMENT = {
    "s1": "each trajectory = one genuine human ask + the work serving it",
    "s2": "each trajectory isolates a coherent code-change burst with its read/verify",
    "s3": "each trajectory ends on a verified outcome; same-artifact successes don't over-segment",
    "s4": "each trajectory = one coherent sub-goal with one deliverable",
}


def sample(n: int) -> list[Path]:
    paths = sorted(BUCKET.glob("*/*/trace.json"))
    if not paths:
        return []
    step = max(1, len(paths) // n)
    return paths[::step][:n]


def mechanical(steps, slicer: str) -> dict:
    rc, env = slicing.partition_trace(
        trace_id="x", slicer_name=slicer, steps=steps, judge="deterministic"
    )
    trajs = env["trajectories"]
    total = env["total_steps"] or 1
    leaky = 0
    if slicer == "s1":
        for t in trajs:
            i = t["start"]
            if i < len(steps) and S.role(steps[i]) == "user" and _LEAKY.match(S.content(steps[i]).strip()):
                leaky += 1
    return {
        "n_trajectories": len(trajs),
        "valid": env["tiling"]["valid"],
        "seg_per_100": round(len(trajs) / total * 100, 2),
        "single_trajectory": len(trajs) == 1 and total > 30,
        "leaky_openers": leaky,
    }


def llm_score(provider, steps, slicer: str, trajs: list[dict]) -> dict | None:
    view = "\n".join(
        f"{i} {S.role(s)} [{','.join(S.tool_names(s))}] {(S.content(s) or S.obs_text(s) or '')[:60]}"
        for i, s in enumerate(steps[:120])
    )
    spans = [(t["start"], t["end"], t["label"][:40]) for t in trajs]
    schema = {
        "type": "object",
        "properties": {
            "conformance": {"type": "number"},
            "utility_ml": {"type": "number"},
            "utility_obs": {"type": "number"},
            "utility_eval": {"type": "number"},
        },
        "required": ["conformance", "utility_ml", "utility_obs", "utility_eval"],
    }
    try:
        res = provider.call_structured(
            system_prompt="You score a trace-slicing result. Be honest and critical; scores in [0,1].",
            user_message=(
                f"Slicer {slicer} sentiment: {SENTIMENT[slicer]}.\nStep view:\n{view}\n\n"
                f"Produced trajectories (start,end,label): {spans}\n\n"
                "Score sentiment conformance + utility to an ML / observability / evaluation engineer."
            ),
            json_schema=schema,
        )
        return res.payload
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--llm", action="store_true", help="run the advisory LLM conformance+utility pass")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    provider = None
    if args.llm:
        from opentraces.core.llm_provider import detect_provider

        provider = detect_provider()
        if provider is None:
            print("# no LLM provider available; running mechanical-only")

    paths = sample(args.sample)
    agg = {sl: {"n": 0, "valid": 0, "seg": [], "single": 0, "leaky": 0,
                "conf": [], "ml": [], "obs": [], "eval": []} for sl in SLICERS}
    for p in paths:
        try:
            rec = load_record_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
        for sl in SLICERS:
            m = mechanical(rec.steps, sl)
            a = agg[sl]
            a["n"] += 1
            a["valid"] += int(m["valid"])
            a["seg"].append(m["seg_per_100"])
            a["single"] += int(m["single_trajectory"])
            a["leaky"] += m["leaky_openers"]
            if provider is not None:
                env = slicing.partition_trace(trace_id="x", slicer_name=sl, steps=rec.steps, judge="deterministic")[1]
                sc = llm_score(provider, rec.steps, sl, env["trajectories"])
                if sc:
                    a["conf"].append(sc["conformance"]); a["ml"].append(sc["utility_ml"])
                    a["obs"].append(sc["utility_obs"]); a["eval"].append(sc["utility_eval"])

    def mean(xs):
        return round(sum(xs) / len(xs), 3) if xs else None

    lines = ["# Trace Slicer — soft-evidence panel (advisory, NOT a CI gate)\n",
             f"Sample: {len(paths)} bucket traces, shipped slicers (opentraces.core.slicing).\n",
             "| Slicer | tiling valid | mean seg/100 | single-traj | s1 leaky openers | conformance | utility ml/obs/eval |",
             "|---|---|---|---|---|---|---|"]
    for sl in SLICERS:
        a = agg[sl]
        util = f"{mean(a['ml'])}/{mean(a['obs'])}/{mean(a['eval'])}" if a["conf"] else "— (run --llm)"
        lines.append(
            f"| {sl} | {a['valid']}/{a['n']} | {mean(a['seg'])} | {a['single']} | "
            f"{a['leaky'] if sl=='s1' else '—'} | {mean(a['conf']) if a['conf'] else '—'} | {util} |"
        )
    out = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(out)
        print(f"wrote {args.out}")
    else:
        print(out)


if __name__ == "__main__":
    main()
