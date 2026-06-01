"""U7 - scaling-slope gate (the qmd invariant at scale).

Compares the per-row p95 latency across two corpus tiers. For a *bounded* query
(FTS lex/semantic, concept JOIN) latency tracks #matches, not #corpus, so it
stays ~flat as the corpus grows: p95(large) / p95(small) <= K. A *documented
O(corpus)* row (the --files facet scan) is exempt - its slope is allowed to
track corpus growth until that path is bounded too.

This reads the per-tier ``artifacts/<tier>/report.json`` the runner writes, so
running ``search-eval`` at two tiers (e.g. real-scale then xl) and then this
gate proves the invariant. Absolute ms are noisy, so the gate is generous
(catch a >=Kx regression, not jitter) per Decision Audit #4.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = REPO_ROOT / "tests" / "search_eval" / "artifacts"
# Generous slope ceiling: bounded latency must not grow more than SLOPE_K x
# across the tier step. R2 targets <=2.5x at 10k; we keep a little headroom for
# wall-clock jitter at small absolute ms and surface the measured ratio either way.
SLOPE_K = 3.0


def _load_reports() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not ARTIFACTS_DIR.is_dir():
        return out
    for tier_dir in ARTIFACTS_DIR.iterdir():
        rp = tier_dir / "report.json"
        if rp.is_file():
            try:
                out[tier_dir.name] = json.loads(rp.read_text())
            except ValueError:
                continue
    return out


def compute_slope(reports: dict[str, dict[str, Any]] | None = None,
                  slope_k: float = SLOPE_K) -> dict[str, Any] | None:
    """Compute the slope between the two largest-corpus tiers available."""

    reports = reports if reports is not None else _load_reports()
    if len(reports) < 2:
        return None
    ordered = sorted(reports.values(), key=lambda r: r.get("corpus_size", 0))
    small, large = ordered[-2], ordered[-1]
    small_by_id = {row["id"]: row for row in small["rows"]}

    rows: list[dict[str, Any]] = []
    for lrow in large["rows"]:
        srow = small_by_id.get(lrow["id"])
        if not srow:
            continue
        s_p95 = float(srow["perf"].get("p95_ms") or 0.0)
        l_p95 = float(lrow["perf"].get("p95_ms") or 0.0)
        ratio = (l_p95 / s_p95) if s_p95 > 0 else None
        bounded = bool((lrow.get("boundedness") or {}).get("bounded"))
        ok = (not bounded) or (ratio is not None and ratio <= slope_k)
        rows.append({
            "id": lrow["id"],
            "archetype": lrow["archetype"],
            "bounded": bounded,
            "small_p95_ms": round(s_p95, 2),
            "large_p95_ms": round(l_p95, 2),
            "ratio": round(ratio, 2) if ratio is not None else None,
            "ok": ok,
        })
    return {
        "small_tier": small["tier"],
        "large_tier": large["tier"],
        "small_corpus": small.get("corpus_size"),
        "large_corpus": large.get("corpus_size"),
        "corpus_growth": round(large.get("corpus_size", 0) / max(small.get("corpus_size", 1), 1), 2),
        "slope_k": slope_k,
        "rows": rows,
        "bounded_all_ok": all(r["ok"] for r in rows if r["bounded"]),
        "all_ok": all(r["ok"] for r in rows),
    }


def render_slope_section(slope: dict[str, Any] | None) -> list[str]:
    if not slope:
        return []
    out = [
        f"## Scaling slope: `{slope['small_tier']}` → `{slope['large_tier']}` "
        f"({slope['small_corpus']} → {slope['large_corpus']} traces, "
        f"{slope['corpus_growth']}× growth)",
        "",
        f"The qmd invariant (R2): for a fixed result size, bounded-query p95 stays "
        f"~flat as the corpus grows (ratio ≤ {slope['slope_k']}×). O(corpus) rows "
        f"(the --files facet scan) are exempt and track corpus growth.",
        "",
        f"- **bounded queries within slope: {'yes' if slope['bounded_all_ok'] else 'NO'}**",
        "",
        "| Row | Archetype | bounded | small p95 | large p95 | ratio | OK |",
        "|-----|-----------|---------|----------:|----------:|------:|----|",
    ]
    for r in sorted(slope["rows"], key=lambda r: (not r["bounded"], r["id"])):
        out.append(
            f"| {r['id']} | {r['archetype']} | "
            f"{'bounded' if r['bounded'] else 'O(corpus)'} | "
            f"{r['small_p95_ms']} | {r['large_p95_ms']} | "
            f"{r['ratio'] if r['ratio'] is not None else '-'} | "
            f"{'ok' if r['ok'] else 'OVER'} |"
        )
    out.append("")
    return out


def main(argv: list[str] | None = None) -> int:
    slope = compute_slope()
    if not slope:
        print("slope: need >=2 tiers; run `make search-eval` and `make search-eval-real` first")
        return 0
    print(f"slope {slope['small_tier']}->{slope['large_tier']} "
          f"({slope['corpus_growth']}x corpus): bounded_all_ok={slope['bounded_all_ok']}")
    for r in slope["rows"]:
        if not r["ok"]:
            print(f"  OVER: {r['id']} ratio={r['ratio']} (bounded={r['bounded']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
