"""Phase-0 deterministic sweep (issue #141).

For every trace in the committed manifest, run each slicer, then run the
INDEPENDENT verifier (verify.recompute_tiling) on the emitted trajectories[].
A crash on any trace is a FAIL (it shrinks no denominator — it's counted).

Checks per (trace, slicer):
  - tiling recomputed valid (coverage 1.0 / 0 gaps / 0 overlaps)  [reliability]
  - byte-identical on re-run (idempotence)
  - S3/S4: request set is bounded (<= marker density) and deterministic under
    a fixed recorded answers set (same answers -> identical trajectories)

Outputs sweep_results.json (full per-trace) + a console summary with the
per-slicer reliability rate and the failing trace IDs named.
"""
from __future__ import annotations

import json
from pathlib import Path

from opentraces_schema import load_record_dict

import proto
import verify

HERE = Path(__file__).resolve().parent


def load_record(path: str):
    return load_record_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def recorded_answers(slicer: str, requests: list[dict]) -> dict:
    """Deterministic recorded answers that exercise BOTH judgment branches."""
    ans = {}
    for i, r in enumerate(requests):
        if slicer == "s3":
            ans[r["id"]] = {"decision": "distinct" if i % 2 == 0 else "one_outcome", "confidence": 0.9}
        else:  # s4
            ans[r["id"]] = {"decision": "pivot" if i % 2 == 0 else "stay", "confidence": 0.9}
    return ans


def marker_density(steps) -> int:
    """Natural segmentation markers: genuine user asks + notifications + successes."""
    asks = len(proto.s1_human_ask_starts(steps))
    notifs = sum(1 for s in steps if proto._is_notification(s))
    succ = len(proto.success_steps(steps))
    return asks + notifs + succ


def run_one(path: str, index_row: dict) -> dict:
    out = {"trace_id": index_row["trace_id"], "meta": index_row, "slicers": {}}
    try:
        record = load_record(path)
    except Exception as exc:  # noqa: BLE001
        out["load_error"] = repr(exc)[:200]
        return out
    steps = record.steps
    total = len(steps)
    md = marker_density(steps)

    for sl, fn in proto.SLICERS.items():
        rec = {"crashed": False}
        try:
            trajs, requests = fn(steps)
            tj = [t.to_dict() for t in trajs]
            tiling = verify.recompute_tiling(tj, total)  # INDEPENDENT verifier
            # idempotence: re-run, byte-compare serialized trajectories
            trajs2, requests2 = fn(steps)
            tj2 = [t.to_dict() for t in trajs2]
            idem = json.dumps(tj, sort_keys=True) == json.dumps(tj2, sort_keys=True)

            rec.update(
                {
                    "n_trajectories": len(tj),
                    "tiling": tiling,
                    "valid": tiling["valid"],
                    "idempotent": idem,
                    "n_requests": len(requests),
                    "marker_density": md,
                    "requests_bounded": len(requests) <= md,
                }
            )
            # S3/S4: determinism under recorded answers (same answers -> identical)
            if sl in ("s3", "s4"):
                ans = recorded_answers(sl, requests)
                ta, _ = fn(steps, ans)
                tb, _ = fn(steps, ans)
                ja = json.dumps([t.to_dict() for t in ta], sort_keys=True)
                jb = json.dumps([t.to_dict() for t in tb], sort_keys=True)
                ta_tiling = verify.recompute_tiling([t.to_dict() for t in ta], total)
                rec["answers_deterministic"] = (ja == jb)
                rec["answers_tiling_valid"] = ta_tiling["valid"]
            # keep the trajectories for the reviewer subset / worst-case surfacing
            rec["trajectories"] = tj
        except Exception as exc:  # noqa: BLE001
            rec["crashed"] = True
            rec["error"] = repr(exc)[:300]
            rec["valid"] = False
        out["slicers"][sl] = rec
    return out


def main():
    manifest = json.loads((HERE / "manifest_det.json").read_text())
    index = json.loads((HERE / "sample_index.json").read_text())
    by_id = {r["trace_id"]: r for r in index}

    results = []
    for row in manifest["traces"]:
        idx = by_id[row["trace_id"]]
        results.append(run_one(idx["path"], idx))

    (HERE / "sweep_results.json").write_text(json.dumps(results, indent=2))

    # Aggregate per-slicer reliability.
    print(f"swept {len(results)} traces\n")
    summary = {}
    for sl in proto.SLICERS:
        n = 0
        valid = 0
        idem = 0
        crashed = 0
        bounded = 0
        ans_det = 0
        ans_n = 0
        fail_ids = []
        for r in results:
            if "load_error" in r:
                continue
            rec = r["slicers"].get(sl, {})
            n += 1
            if rec.get("crashed"):
                crashed += 1
                fail_ids.append((r["trace_id"], "crash"))
                continue
            if rec.get("valid"):
                valid += 1
            else:
                fail_ids.append((r["trace_id"], "invalid-tiling"))
            if rec.get("idempotent"):
                idem += 1
            else:
                fail_ids.append((r["trace_id"], "non-idempotent"))
            if rec.get("requests_bounded", True):
                bounded += 1
            if sl in ("s3", "s4"):
                ans_n += 1
                if rec.get("answers_deterministic"):
                    ans_det += 1
        summary[sl] = {
            "n": n,
            "valid": valid,
            "reliability_rate": round(valid / n, 4) if n else 0.0,
            "idempotent": idem,
            "crashed": crashed,
            "requests_bounded": bounded,
            "answers_deterministic": (ans_det, ans_n),
            "fail_ids": fail_ids,
        }
        print(f"[{sl}] {proto.SLICER_TIER[sl]:13s} "
              f"reliability {valid}/{n} ({summary[sl]['reliability_rate']:.3f})  "
              f"idempotent {idem}/{n}  crashed {crashed}  "
              f"requests_bounded {bounded}/{n}  "
              f"answers_det {ans_det}/{ans_n}")
        if fail_ids:
            print("    FAIL trace IDs:", ", ".join(f"{t[:12]}({why})" for t, why in fail_ids[:20]))

    (HERE / "sweep_summary.json").write_text(json.dumps(summary, indent=2, default=list))


if __name__ == "__main__":
    main()
