"""U2 - eval runner.

Drives the progressive-discovery loop (``trace query -> map -> slice -> get``)
over a planted corpus and produces an :class:`EvalReport` carrying, per query
row, both **performance** (cold/p50/p95 via the reused ``tests/perf`` engine)
and **outcome** (recall@k / MRR / NDCG / tau / recency-hit via
``score_outcome``), checked against each row's documented ``expected_phase_a``.

Lifecycle (corpus is built ONCE and queried warm - never rebuilt per run, so we
never reintroduce the 087 cold-build onto the hot path):

  plan_corpus -> materialize -> warm index+projection -> per-row {outcome, perf}
  -> discovery-loop smoke -> EvalReport (+ deterministic outcome digest).

The outcome half is deterministic (hash-stable); only the perf half is
wall-clock. Run via ``python -m tests.search_eval.runner --tier dev``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from opentraces.core import search_diag  # noqa: E402
from opentraces.core.search_projection import query_search_projection_page  # noqa: E402
from opentraces.core.trace_index import query_index_page  # noqa: E402
from tests.perf.harness.measure import CommandPlan, measure_command_factory  # noqa: E402
from tests.perf.harness.models import PerfBudget, PerfScenario  # noqa: E402
from tests.search_eval import score_outcome  # noqa: E402
from tests.search_eval.generator import (  # noqa: E402
    CorpusPlan,
    QueryRow,
    load_profile,
    materialize_corpus,
    plan_corpus,
)

OTD = str(REPO_ROOT / "otd")
OUTCOME_LIMIT = 100        # generous fetch so >=k distinct traces surface
PERF_LIMIT = 20           # the natural agent-facing page size
# qmd boundedness (R3): a query may scan up to BOUND_FACTOR x its matches
# (plus a small floor); scanning ~corpus to return few = the O(corpus) cliff.
BOUND_FLOOR = 50
BOUND_FACTOR = 4
# Latency cliff budgets (R1): generous ceilings that catch >=10x regressions,
# never jitter (Decision Audit #4 - absolute ms flake, so these are loose). A
# row expected to be BOUNDED must also be within its cliff; the documented
# O(corpus) rows (facet/concept) are exempt until U6.
CLIFF_MS = {"lex": 3000.0, "files": 5000.0, "semantic": 10000.0}
ARTIFACTS_DIR = REPO_ROOT / "tests" / "search_eval" / "artifacts"

# reps/warmups per tier (real-scale queries are heavier -> fewer reps)
_TIER_REPS = {"dev": (3, 1), "real-scale": (2, 1), "xl": (1, 1)}


@dataclass
class RowResult:
    id: str
    archetype: str
    seed_case: str | None
    mode: str
    query: str
    expected_phase_a: str
    total: int
    perf: dict[str, Any]
    outcome: dict[str, Any]
    boundedness: dict[str, Any]
    bounded_expected: bool
    bounded_ok: bool
    cliff_ok: bool            # within latency cliff, OR a documented O(corpus) row
    status: str               # "green" if all targets met else "red"
    invariant_ok: bool        # status matches expected_phase_a
    note: str


@dataclass
class EvalReport:
    tier: str
    seed: int
    snapshot_key: str
    profile_digest: str
    corpus_size: int
    rows: list[RowResult]
    loop_smoke: dict[str, Any]
    outcome_digest: str
    invariants_ok: bool
    generated_at: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# query plumbing
# --------------------------------------------------------------------------- #
def _query_args(row: QueryRow, limit: int) -> list[str]:
    args = ["trace", "query", "--json", "--limit", str(limit)]
    if row.mode == "lex":
        args += ["--lex", row.query]
    elif row.mode == "semantic":
        args += ["--semantic", row.query]
    elif row.mode == "files":
        args += ["--files", row.query]
    else:
        raise ValueError(f"unknown query mode {row.mode!r}")
    if row.extra_flags.get("include_superseded"):
        args.append("--include-superseded")
    if row.extra_flags.get("sort"):
        args += ["--sort", row.extra_flags["sort"]]
    if row.extra_flags.get("min_score") is not None:
        args += ["--min-score", str(row.extra_flags["min_score"])]
    return args


def _run_query_json(args: list[str], project_dir: Path, env: dict[str, str]) -> dict[str, Any]:
    proc = subprocess.run(
        [OTD, *args], cwd=str(project_dir), env=env,
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"query failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr[-400:]}")
    return json.loads(proc.stdout)


def _ranked_traces(payload: dict[str, Any]) -> list[str]:
    cands = payload.get("candidates") or []
    return score_outcome.distinct_traces(c.get("trace_id") for c in cands)


def _capture_boundedness(row: QueryRow) -> dict[str, Any]:
    """Run the query in-process with OT_SEARCH_DIAG to read the rows-scanned
    counter (R3, the qmd invariant). Deterministic - the corpus is fixed."""

    kwargs: dict[str, Any] = {
        "limit": OUTCOME_LIMIT,
        "latest_generation": not row.extra_flags.get("include_superseded", False),
    }
    if row.extra_flags.get("sort"):
        kwargs["sort"] = row.extra_flags["sort"]
    if row.extra_flags.get("min_score") is not None:
        kwargs["min_score"] = row.extra_flags["min_score"]
    os.environ["OT_SEARCH_DIAG"] = "1"
    try:
        if row.mode == "semantic":
            page = query_search_projection_page(semantic=row.query, **kwargs)
        elif row.mode == "files":
            page = query_index_page(files=row.query, **kwargs)
        else:
            page = query_index_page(lex=row.query, **kwargs)
        diag = search_diag.snapshot()
    finally:
        os.environ.pop("OT_SEARCH_DIAG", None)

    matched = int(page.total)
    rows_scanned = int(diag.get("rows_scanned", 0))
    corpus = int(diag.get("corpus_docs", 0))
    bound_limit = max(BOUND_FLOOR, BOUND_FACTOR * matched)
    bounded = rows_scanned <= bound_limit
    return {
        "path": diag.get("path"),
        "rows_scanned": rows_scanned,
        "corpus_docs": corpus,
        "matched": matched,
        "page_size": len(page.candidates),
        "page_le_limit": len(page.candidates) <= OUTCOME_LIMIT,
        "bound_limit": bound_limit,
        "bounded": bounded,
    }


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
def run_eval(
    tier: str = "dev",
    seed: int = 1,
    *,
    profile: dict[str, Any] | None = None,
    base_dir: Path | None = None,
    keep: bool = False,
) -> EvalReport:
    profile = profile or load_profile()
    plan: CorpusPlan = plan_corpus(profile, seed, tier)

    base_dir = base_dir or Path(tempfile.mkdtemp(prefix=f"seval-{tier}-"))
    home_dir = base_dir / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    mat = materialize_corpus(plan, base_dir / "work", home_dir)
    env = dict(os.environ)
    env["HOME"] = str(mat.home_dir)
    project_dir = mat.project_dir

    reps, warmups = _TIER_REPS.get(tier, (2, 1))

    # --- warm the index + projection once (cold build happens here) --------- #
    for warm in (["trace", "query", "--json", "--limit", "5", "--lex", "init"],
                 ["trace", "query", "--json", "--limit", "5", "--semantic", "init"],
                 ["trace", "query", "--json", "--limit", "5", "--files", "*.py"]):
        try:
            _run_query_json(warm, project_dir, env)
        except Exception:
            pass

    rows: list[RowResult] = []
    for row in plan.queries:
        # outcome capture (generous limit, distinct-trace dedup)
        payload = _run_query_json(_query_args(row, OUTCOME_LIMIT), project_dir, env)
        ranked = _ranked_traces(payload)
        total = int(payload.get("total") or 0)
        outcome = score_outcome.score_row(
            ranked, row.gold_trace_ids, row.gold_kind, row.gold_latest, row.targets
        )

        # perf timing (constant plan -> warm, no rebuild)
        scenario = PerfScenario(
            name=f"search-eval-{tier}-{row.id}", domain="search", lane="nightly",
            adapter="subprocess", target="cli.trace_query", fixture="search_corpus",
            tier=tier, repetitions=reps, warmups=warmups,
            params={"mode": row.mode}, budget=PerfBudget(),
        )
        perf_args = _query_args(row, PERF_LIMIT)

        def factory(_idx: int, _args=perf_args) -> CommandPlan:
            return CommandPlan(cmd=[OTD, *_args], cwd=project_dir, env=env,
                               metadata={"scenario": scenario.name})

        perf = measure_command_factory(scenario, factory)
        cliff_ms = CLIFF_MS.get(row.mode, 5000.0)
        within_cliff = perf.p95_ms <= cliff_ms
        perf_summary = {
            "cold_ms": round(perf.cold_ms, 2),
            "p50_ms": round(perf.p50_ms, 2),
            "p95_ms": round(perf.p95_ms, 2),
            "mean_ms": round(perf.mean_ms, 2),
            "peak_rss_mb": round(perf.peak_rss_mb, 2) if perf.peak_rss_mb else None,
            "cliff_ms": cliff_ms,
            "within_cliff": within_cliff,
        }

        boundedness = _capture_boundedness(row)
        bounded_ok = (boundedness["bounded"] == row.bounded_expected) and boundedness["page_le_limit"]
        # a row expected to be bounded must also be within its latency cliff;
        # documented O(corpus) rows are exempt (their latency is the U6 gap).
        cliff_ok = within_cliff or not row.bounded_expected

        status = "green" if outcome["all_targets_met"] else "red"
        invariant_ok = (status == "green") == (row.expected_phase_a == "green")
        rows.append(RowResult(
            id=row.id, archetype=row.archetype, seed_case=row.seed_case,
            mode=row.mode, query=row.query, expected_phase_a=row.expected_phase_a,
            total=total, perf=perf_summary, outcome=outcome,
            boundedness=boundedness, bounded_expected=row.bounded_expected,
            bounded_ok=bounded_ok, cliff_ok=cliff_ok,
            status=status, invariant_ok=invariant_ok, note=row.note,
        ))

    loop_smoke = _discovery_loop_smoke(plan, project_dir, env)
    outcome_digest = _outcome_digest(rows)
    invariants_ok = (
        all(r.invariant_ok for r in rows)
        and all(r.bounded_ok for r in rows)
        and all(r.cliff_ok for r in rows)
        and loop_smoke.get("ok", False)
    )

    report = EvalReport(
        tier=tier, seed=seed, snapshot_key=plan.snapshot_key,
        profile_digest=plan.profile_digest, corpus_size=len(plan.traces),
        rows=rows, loop_smoke=loop_smoke, outcome_digest=outcome_digest,
        invariants_ok=invariants_ok,
        meta={"generator_version": plan.generator_version, "code_version": plan.code_version,
              "outcome_limit": OUTCOME_LIMIT, "perf_limit": PERF_LIMIT,
              "reps": reps, "warmups": warmups, "base_dir": str(base_dir)},
    )
    _write_artifacts(report)
    if not keep:
        import shutil
        shutil.rmtree(base_dir, ignore_errors=True)
    return report


def _discovery_loop_smoke(plan: CorpusPlan, project_dir: Path, env: dict[str, str]) -> dict[str, Any]:
    """Exercise query -> map -> slice -> get on one descriptive gold trace,
    proving the full loop runs end-to-end (Decision Audit #7)."""

    row = next((q for q in plan.queries if q.archetype == "descriptive"), plan.queries[0])
    try:
        payload = _run_query_json(_query_args(row, 10), project_dir, env)
        cands = payload.get("candidates") or []
        if not cands:
            return {"ok": False, "reason": "no candidates for loop seed"}
        trace_id = cands[0].get("trace_id")
        steps = ["query"]
        for verb, extra in (("map", []), ("slice", ["--template", "bursts"]), ("get", [])):
            args = ["trace", verb, trace_id, "--json", *extra]
            proc = subprocess.run([OTD, *args], cwd=str(project_dir), env=env,
                                  capture_output=True, text=True, timeout=300)
            if proc.returncode == 0:
                steps.append(verb)
        return {"ok": len(steps) >= 3, "trace_id": trace_id, "stages": steps}
    except Exception as exc:  # pragma: no cover - smoke only
        return {"ok": False, "reason": str(exc)}


def _outcome_digest(rows: list[RowResult]) -> str:
    """Deterministic hash over outcome metrics only (excludes wall-clock perf)."""

    payload = []
    for r in rows:
        oc = dict(r.outcome)
        oc.pop("distinct_returned", None)  # can shift with corpus noise; keep core metrics
        payload.append({
            "id": r.id, "total": r.total, "status": r.status,
            "recall_at_k": oc.get("recall_at_k"), "mrr": oc.get("mrr"),
            "ndcg_at_k": oc.get("ndcg_at_k"), "first_rank": oc.get("first_rank"),
            "kendall_tau": oc.get("kendall_tau"), "recency_hit": oc.get("recency_hit"),
            "passes": oc.get("passes"),
            "bounded": r.boundedness.get("bounded"), "bounded_ok": r.bounded_ok,
            "path": r.boundedness.get("path"),
        })
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


def _write_artifacts(report: EvalReport) -> None:
    out_dir = ARTIFACTS_DIR / report.tier
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(report)
    (out_dir / "report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the search evaluation (U2).")
    parser.add_argument("--tier", default="dev", choices=["dev", "real-scale", "xl"])
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--keep", action="store_true", help="keep the temp corpus dir")
    parser.add_argument("--no-report", action="store_true", help="skip SEARCH-EVAL.md render")
    args = parser.parse_args(argv)

    report = run_eval(args.tier, args.seed, keep=args.keep)
    greens = sum(1 for r in report.rows if r.status == "green")
    reds = len(report.rows) - greens
    unbounded = sum(1 for r in report.rows if not r.boundedness.get("bounded"))
    print(f"tier={report.tier} corpus={report.corpus_size} rows={len(report.rows)} "
          f"green={greens} red={reds} O(corpus)={unbounded} invariants_ok={report.invariants_ok}")
    print(f"outcome_digest={report.outcome_digest[:27]}… snapshot_key={report.snapshot_key[:27]}…")

    if not args.no_report:
        from tests.search_eval.report import render_report
        path = render_report(report)
        print(f"report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
