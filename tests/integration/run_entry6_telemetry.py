"""Telemetry harness for the entry-#6 regression scenario.

Runs the canonical CLI pipeline for the original user intent (filter last-12h
traces → bursts → survival → manual dataset) and emits a structured JSON
report:

    {
      "branch": "<git rev-parse HEAD>",
      "commit_subject": "...",
      "step_timings": {...},
      "label_pass_rate": {...},
      "raw_burst": {...},
      ...
    }

Used as the iterative regression target across cluster E → F → G → H. The
output JSON is comparable across runs so deltas are obvious.

Usage
-----
    python tests/integration/run_entry6_telemetry.py [--label "baseline"] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "integration" / "fixtures" / "entry6"
LABELS = json.loads((FIXTURE / "labels.json").read_text())
OTD = REPO / "otd"


def now() -> float:
    return time.time()


def run_step(label: str, args: list[str], timeout: int = 600) -> dict[str, Any]:
    """Run one CLI step, time it, capture stdout/stderr/exit."""
    t0 = now()
    proc = subprocess.run(
        args, capture_output=True, text=True, cwd=str(REPO), timeout=timeout, check=False,
    )
    elapsed = now() - t0
    return {
        "label": label,
        "exit_code": proc.returncode,
        "wall_seconds": round(elapsed, 2),
        "stdout_bytes": len(proc.stdout),
        "stderr_bytes": len(proc.stderr),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--label", default="run", help="Label to tag this run (e.g. 'baseline', 'cluster-E').")
    p.add_argument("--out", default=None, help="Where to write the JSON report.")
    p.add_argument("--skip-step1", action="store_true", help="Skip the slow trail track step (debug).")
    args = p.parse_args()

    report: dict[str, Any] = {
        "label": args.label,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "branch": "",
        "commit_sha": "",
        "commit_subject": "",
    }
    proc = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True)
    report["commit_sha"] = proc.stdout.strip()
    proc = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%s", "HEAD"],
                          capture_output=True, text=True, check=True)
    report["commit_subject"] = proc.stdout.strip()
    proc = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True, check=True)
    report["branch"] = proc.stdout.strip()

    # Step 1: trail track --since 24h (event-log-driven; no index dependency)
    step_timings: dict[str, Any] = {}
    if not args.skip_step1:
        s1 = run_step("step1_trail_track_since_24h",
                      [str(OTD), "--json", "trail", "track", "--since", "24h", "--limit", "600"])
        step_timings["step1"] = {k: v for k, v in s1.items() if k != "stdout"}
        # Save stdout to /tmp for downstream join
        Path("/tmp/entry6_telemetry_track.jsonl").write_text(s1["stdout"])

    # Step 2: trace map --bursts × 11 traces (the 12h ID set)
    trace_ids = [
        "0b26b03f-715f-42e2-963f-d635b4a0f0e7",
        "1006913e-87c0-4800-a5f5-9f60e1f10098",
        "185b0a55-2a2a-49c7-b3c1-bbf09cadd96b",
        "20986109-6222-4997-8dfc-7e06dda4acab",
        "24bdd5d2-85b8-4297-88b4-24ed2c32dfbc",
        "30c04384-2bae-4608-a87b-81bbde43d7f6",
        "75ab6d0f-ee37-45a7-9d26-42d352f32a77",
        "837b4444-2e45-4621-88d3-4780382aea18",
        "9c034573-6a68-480e-8057-2d4b47320998",
        "f18c0935-5d31-4c98-a6aa-ce854283d31e",
        "fb7ce9e4-51d7-4acc-9994-181a19e418b9",
    ]
    t0 = now()
    bursts: list[dict[str, Any]] = []
    for tid in trace_ids:
        proc = subprocess.run(
            [str(OTD), "--json", "trace", "map", tid, "--bursts"],
            capture_output=True, text=True, cwd=str(REPO), check=False,
        )
        if proc.returncode != 0:
            continue
        try:
            d = json.loads(proc.stdout)
        except json.JSONDecodeError:
            continue
        for n in d.get("map", {}).get("nodes", []):
            if n.get("action_type") != "change_burst":
                continue
            bursts.append({
                "trace_id": d.get("trace_id"),
                "node_metadata": n.get("metadata") or {},
                "node_top_level": {k: v for k, v in n.items() if k != "metadata"},
            })
    step_timings["step2_trace_map_bursts_x_n"] = {
        "wall_seconds": round(now() - t0, 2),
        "traces_processed": len(trace_ids),
        "bursts_extracted": len(bursts),
    }

    # Find entry #6: trace 185b0a55 burst [32, 289]
    target_burst = None
    for b in bursts:
        if (b["trace_id"] == "185b0a55-2a2a-49c7-b3c1-bbf09cadd96b" and
                list((b["node_metadata"].get("step_range") or [])) == [32, 289]):
            target_burst = b
            break

    report["step_timings"] = step_timings
    report["entry6_burst_present"] = target_burst is not None
    report["entry6_raw"] = target_burst

    # Label evaluation
    label_results: dict[str, dict[str, Any]] = {}

    def assess(label_id: str, condition: bool, *, expected: Any = None, actual: Any = None,
               severity: str = "hard") -> None:
        label_results[label_id] = {
            "pass": bool(condition),
            "severity": severity,
            "expected": expected,
            "actual": actual,
        }

    if target_burst:
        meta = target_burst["node_metadata"]
        top = target_burst["node_top_level"]
        intent = meta.get("intent") or top.get("intent") or {}

        assess("burst.step_range",
               list(meta.get("step_range") or []) == LABELS["burst"]["step_range"],
               expected=LABELS["burst"]["step_range"], actual=meta.get("step_range"))

        patches = meta.get("patches") or top.get("patches") or []
        assess("burst.patch_count",
               len(patches) == LABELS["burst"]["patch_count"],
               expected=LABELS["burst"]["patch_count"], actual=len(patches))

        files = meta.get("unique_files") or top.get("unique_files") or {}
        # Normalise abs/rel duplication
        def norm(k: str) -> str:
            for prefix in (
                "/Users/06506792/src/tries/2026-03-27-community-traces-hf/",
                str(REPO) + "/",
            ):
                if k.startswith(prefix):
                    return k[len(prefix):]
            return k
        if isinstance(files, dict):
            normalised = {}
            for k, v in files.items():
                normalised[norm(k)] = normalised.get(norm(k), 0) + v
            unique_count = len(normalised)
        else:
            normalised = {}
            unique_count = len({norm(k) for k in files})

        assess("burst.unique_files_count",
               unique_count == LABELS["burst"]["unique_files_count"],
               expected=LABELS["burst"]["unique_files_count"], actual=unique_count)

        assess("burst.unique_files_set_match",
               set(normalised.keys() if isinstance(normalised, dict) else normalised) ==
               set(LABELS["burst"]["unique_files"]),
               expected=sorted(LABELS["burst"]["unique_files"]),
               actual=sorted(normalised.keys() if isinstance(normalised, dict) else normalised))

        # Hunk distribution
        if isinstance(normalised, dict):
            hunks_match = all(
                normalised.get(p) == c
                for p, c in LABELS["burst"]["files_to_hunks"].items()
            )
            assess("burst.files_to_hunks", hunks_match,
                   expected=LABELS["burst"]["files_to_hunks"], actual=normalised)

        # Burst commit sha (D5)
        burst_sha = meta.get("burst_commit_sha") or top.get("burst_commit_sha")
        if burst_sha is None:
            from collections import Counter
            shas = Counter(p.get("commit_sha") for p in patches if p.get("commit_sha"))
            burst_sha = shas.most_common(1)[0][0] if shas else None
        assess("burst.commit_sha_resolved",
               burst_sha == LABELS["burst"]["burst_commit_sha"],
               expected=LABELS["burst"]["burst_commit_sha"], actual=burst_sha)

        # Intent — Cluster E owns these
        if isinstance(intent, dict):
            trig = intent.get("trigger") or {}
            assess("intent.trigger.step",
                   trig.get("step") == LABELS["intent"]["trigger"]["step"],
                   expected=LABELS["intent"]["trigger"]["step"], actual=trig.get("step"))

            spec = intent.get("most_substantive_spec") or {}
            assess("intent.most_substantive_spec.step",
                   spec.get("step") == LABELS["intent"]["most_substantive_spec"]["step"],
                   expected=LABELS["intent"]["most_substantive_spec"]["step"], actual=spec.get("step"))

            spec_text = (spec.get("text") or "").lower()
            text_ok = any(frag.lower() in spec_text
                          for frag in LABELS["intent"]["most_substantive_spec"]["text_must_contain_any_of"])
            assess("intent.most_substantive_spec.text_match", text_ok,
                   expected="any of: " + str(LABELS["intent"]["most_substantive_spec"]["text_must_contain_any_of"]),
                   actual=spec.get("text", "")[:80])

            chain = intent.get("spec_chain") or []
            chain_steps = {item.get("step") for item in chain if isinstance(item, dict)}
            required = set(LABELS["intent"]["spec_chain_must_include_steps"])
            assess("intent.spec_chain_includes_required",
                   required.issubset(chain_steps),
                   expected=sorted(required), actual=sorted(chain_steps))

            forbidden = set(LABELS["intent"]["spec_chain_must_exclude_steps"])
            assess("intent.spec_chain_excludes_trigger",
                   not (chain_steps & forbidden),
                   expected="no overlap with " + str(sorted(forbidden)),
                   actual="overlap=" + str(sorted(chain_steps & forbidden)))

            assess("intent.commit_subject",
                   intent.get("commit_subject") == LABELS["burst"]["burst_commit_subject"],
                   expected=LABELS["burst"]["burst_commit_subject"],
                   actual=intent.get("commit_subject"))

            cb = intent.get("commit_body") or ""
            body_ok = (
                len(cb) >= LABELS["intent"]["commit_body_min_length_chars"] and
                all(frag in cb for frag in LABELS["burst"]["burst_commit_body_must_contain"])
            )
            assess("intent.commit_body_present_and_correct", body_ok,
                   expected="contains " + str(LABELS["burst"]["burst_commit_body_must_contain"]),
                   actual=f"len={len(cb)}, first_60={cb[:60]!r}")
        else:
            for label_id in (
                "intent.trigger.step",
                "intent.most_substantive_spec.step",
                "intent.most_substantive_spec.text_match",
                "intent.spec_chain_includes_required",
                "intent.spec_chain_excludes_trigger",
                "intent.commit_subject",
                "intent.commit_body_present_and_correct",
            ):
                assess(label_id, False, expected="present", actual="intent field missing (Cluster E not landed)")

    # Pass-rate summary
    total = len(label_results)
    passed = sum(1 for r in label_results.values() if r["pass"])
    report["label_results"] = label_results
    report["label_summary"] = {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 3) if total else 0.0,
    }

    # Performance budgets (target_after_cluster_G)
    perf_targets = LABELS["performance_targets"]
    s1_actual = step_timings.get("step1", {}).get("wall_seconds", -1)
    s2_actual = step_timings.get("step2_trace_map_bursts_x_n", {}).get("wall_seconds", -1)
    report["performance_budget_results"] = {
        "step1_trail_track_under_target_warm": {
            "target": perf_targets["step1_trail_track_since_24h_seconds"]["target_after_cluster_G_warm"],
            "actual": s1_actual,
            "pass": s1_actual >= 0 and s1_actual <= perf_targets["step1_trail_track_since_24h_seconds"]["target_after_cluster_G_warm"],
        },
        "step2_trace_map_unchanged_target": {
            "target": perf_targets["step2_trace_map_bursts_x11_seconds"]["target_unchanged"],
            "actual": s2_actual,
            "pass": s2_actual >= 0 and s2_actual <= perf_targets["step2_trace_map_bursts_x11_seconds"]["target_unchanged"],
        },
    }

    out_path = args.out or f"/tmp/entry6_telemetry_{args.label}.json"
    Path(out_path).write_text(json.dumps(report, indent=2, sort_keys=True, default=str))

    # Human-readable summary
    print(f"\n=== TELEMETRY [{args.label}] @ {report['branch']} {report['commit_sha'][:8]} ===")
    print(f"  branch.commit_subject: {report['commit_subject']}")
    print(f"  entry6 burst present:  {report['entry6_burst_present']}")
    print(f"\n  step timings:")
    for k, v in step_timings.items():
        if isinstance(v, dict):
            print(f"    {k}: {v.get('wall_seconds', '?')}s")
    print(f"\n  label pass rate: {passed}/{total}  ({round(100*passed/total,1) if total else 0}%)")
    print(f"\n  PASSING:")
    for k, v in label_results.items():
        if v["pass"]:
            print(f"    ✓ {k}")
    print(f"\n  FAILING:")
    for k, v in label_results.items():
        if not v["pass"]:
            actual_str = str(v.get("actual"))[:120]
            print(f"    ✗ {k}: expected={v.get('expected')!r:.<60.60} actual={actual_str}")
    print(f"\n  full report: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
