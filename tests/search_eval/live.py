"""U8 - `--live` ad-hoc mode (ungated, against the real ~/.opentraces bucket).

The gated synthetic path proves the harness deterministically; this runs the
*actual* Seed Evaluation Dataset queries (plan 088, mined from the operator's
live bucket) against the real bucket and reports latency + whether the known
gold trace is found and where it ranks. Gold here is heuristic (the recorded
seed trace-id prefixes), so the run is ungated - a "where do I stand on my real
data" snapshot that confirms the U4/U5/U6 capabilities land on real traces, not
just planted ones.

Writes ``tests/search_eval/LIVE-EVAL.md`` (kept separate from the gated
SEARCH-EVAL.md). Stdlib only.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from tests.search_eval.score_outcome import distinct_traces

REPO_ROOT = Path(__file__).resolve().parents[2]
OTD = str(REPO_ROOT / "otd")
LIVE_OUTPUT = REPO_ROOT / "tests" / "search_eval" / "LIVE-EVAL.md"

# The 8-case Seed Evaluation Dataset (plan 088), verified against the operator's
# live ~/.opentraces bucket. gold = the recorded trace-id prefix.
LIVE_SEED_CASES = [
    {"seed": "S1", "intent": "reference (descriptive)", "gold": "9fdd8e12",
     "args": ["--lex", "slice trajectory"]},
    {"seed": "S2", "intent": "precedent (semantic)", "gold": "9fdd8e12",
     "args": ["--semantic", "break a trajectory into per-intent slices"]},
    {"seed": "S3", "intent": "needle (bare identifier in URL)", "gold": "c4e5dee0",
     "args": ["--lex", "nicobailon"]},
    {"seed": "S3b", "intent": "needle (hyphenated identifier in URL)", "gold": "c4e5dee0",
     "args": ["--lex", "pi-subagents"]},
    {"seed": "S4", "intent": "chronological (--sort time)", "gold": "c4e5dee0",
     "args": ["--lex", "nicobailon", "--sort", "time"]},
    {"seed": "S5", "intent": "recency (--sort recency)", "gold": "43a7f9f6",
     "args": ["--semantic", "latest work on the security stack", "--sort", "recency"]},
    {"seed": "S6", "intent": "meta / reference", "gold": "ab7d8ecc",
     "args": ["--lex", "fast search over previous traces"]},
    {"seed": "S7", "intent": "reference (phrase)", "gold": "8547c32d",
     "args": ["--lex", "how many lines of code"]},
]


def run_live(home: str | None = None, limit: int = 20) -> list[dict]:
    home = home or os.path.expanduser("~")
    env = dict(os.environ)
    env["HOME"] = home
    rows: list[dict] = []
    for case in LIVE_SEED_CASES:
        args = ["trace", "query", "--json", "--limit", str(limit), *case["args"]]
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                [OTD, *args], cwd=str(REPO_ROOT), env=env,
                capture_output=True, text=True, timeout=600,
            )
            ms = (time.perf_counter() - start) * 1000.0
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()
                raise RuntimeError(
                    f"query failed ({proc.returncode}): {' '.join(args)}"
                    + (f" :: {detail[:120]}" if detail else "")
                )
            payload = json.loads(proc.stdout) if proc.stdout else {}
            ranked = distinct_traces(
                c.get("trace_id") for c in (payload.get("candidates") or [])
            )
            rank = next(
                (i + 1 for i, tid in enumerate(ranked) if str(tid).startswith(case["gold"])),
                None,
            )
            rows.append({
                "seed": case["seed"], "intent": case["intent"],
                "query": " ".join(case["args"]), "gold": case["gold"],
                "total": payload.get("total"), "ms": round(ms, 1),
                "gold_found": rank is not None, "rank": rank,
            })
        except Exception as exc:  # ungated, best-effort
            rows.append({
                "seed": case["seed"], "intent": case["intent"],
                "query": " ".join(case["args"]), "gold": case["gold"],
                "error": str(exc)[:120],
            })
    return rows


def render_live(rows: list[dict], home: str) -> str:
    out = [
        "# Search Evaluation — LIVE (ungated, real bucket)",
        "",
        f"The Seed Evaluation Dataset (plan 088) run against the real bucket at "
        f"`{home}/.opentraces`. Ungated: gold is the recorded seed trace-id prefix, "
        "so this is a 'where do I stand on my real data' snapshot, not a CI gate. "
        "Regenerate with `make search-eval-live`.",
        "",
        "| Seed | Intent | Query | Total | Gold found | Rank | ms |",
        "|------|--------|-------|------:|------------|-----:|---:|",
    ]
    for r in rows:
        if "error" in r:
            out.append(f"| {r['seed']} | {r['intent']} | `{r['query']}` | - | ERROR | - | {r['error']} |")
            continue
        out.append(
            f"| {r['seed']} | {r['intent']} | `{r['query']}` | {r['total']} | "
            f"{'yes' if r['gold_found'] else 'NO'} | {r['rank'] if r['rank'] else '-'} | {r['ms']} |"
        )
    out.append("")
    out.append(
        "_Live gold is heuristic (recorded trace-id prefixes); the deterministic "
        "gated proof lives in SEARCH-EVAL.md._"
    )
    out.append("")
    out.append(
        "**Known limitation — S2 paraphrase recall.** `--semantic` is concept-"
        "expansion + lexical (FTS), not embedding-based, and FTS matches all terms "
        "(precise + bounded). When the query *re-words* the target (S2: \"break a "
        "trajectory into per-intent slices\" vs the gold's \"slice trajectory per "
        "intent\") the words don't overlap, so the gold isn't found. A bounded "
        "OR + bm25 experiment did surface it at rank 4 but scanned the whole "
        "OR-match set (~18s for common terms), violating the <2s budget; true "
        "paraphrase-at-speed needs embedding-semantic (out of the stdlib-only "
        "scope) or TF-IDF term pruning (a tracked enhancement). The gated S2 case "
        "uses a word-aligned query and proves the fast, bounded semantic path "
        "(<2s at real-scale, was ~90s)."
    )
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the live seed-case eval (U8).")
    parser.add_argument("--home", default=os.path.expanduser("~"))
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)

    rows = run_live(args.home, args.limit)
    LIVE_OUTPUT.write_text(render_live(rows, args.home), encoding="utf-8")
    found = sum(1 for r in rows if r.get("gold_found"))
    print(f"live: {found}/{len(rows)} seed golds found against {args.home}/.opentraces")
    for r in rows:
        if "error" in r:
            print(f"  {r['seed']:4} ERROR {r['error']}")
        else:
            print(f"  {r['seed']:4} total={str(r['total']):>5} found={r['gold_found']!s:5} "
                  f"rank={r['rank']} {r['ms']:.0f}ms  {r['query']}")
    print(f"wrote {LIVE_OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
