"""U2 - SEARCH-EVAL.md renderer.

Reads the per-tier ``artifacts/<tier>/report.json`` written by the runner and
renders one ``tests/search_eval/SEARCH-EVAL.md`` showing **performance +
outcome per archetype per tier**, so "where do we stand" and "did we regress"
read off the same artifact (R10). Self-contained, stdlib only - mirrors
``tests/perf/render_baseline_report.py``.

RED on S2/S3/S4/S5 is expected and surfaced explicitly in Phase A: those are
the executable spec for the ranking capabilities (U4/U5/U6) that land
red -> green against this report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = REPO_ROOT / "tests" / "search_eval" / "artifacts"
OUTPUT_PATH = REPO_ROOT / "tests" / "search_eval" / "SEARCH-EVAL.md"
_TIER_ORDER = {"dev": 0, "real-scale": 1, "xl": 2}


def _fmt(v: Any, nd: int = 2) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _status_mark(status: str) -> str:
    return "GREEN" if status == "green" else "RED"


def _outcome_cell(oc: dict[str, Any]) -> str:
    """A compact outcome summary for the row's archetype."""

    parts = [f"recall@{oc.get('k')}={_fmt(oc.get('recall_at_k'))}"]
    if oc.get("kendall_tau") is not None:
        parts.append(f"tau={_fmt(oc.get('kendall_tau'))}")
    if oc.get("recency_hit") is not None:
        parts.append(f"rec@1={_fmt(oc['recency_hit'].get('among_gold'))}")
    parts.append(f"mrr={_fmt(oc.get('mrr'))}")
    if oc.get("first_rank") is not None:
        parts.append(f"rank={oc.get('first_rank')}")
    return ", ".join(parts)


def _load_reports() -> list[dict[str, Any]]:
    reports = []
    if not ARTIFACTS_DIR.is_dir():
        return reports
    for tier_dir in sorted(ARTIFACTS_DIR.iterdir(), key=lambda p: _TIER_ORDER.get(p.name, 9)):
        rp = tier_dir / "report.json"
        if rp.is_file():
            try:
                reports.append(json.loads(rp.read_text()))
            except ValueError:
                continue
    return reports


def _seed_table(rows: list[dict[str, Any]]) -> list[str]:
    seeded = [r for r in rows if r.get("seed_case")]
    seeded.sort(key=lambda r: r["seed_case"])
    if not seeded:
        return ["_(no seed-tagged rows)_", ""]
    lines = [
        "| Seed | Query | Total | Outcome | p95 ms | Status | Expected | OK |",
        "|------|-------|------:|---------|-------:|--------|----------|----|",
    ]
    for r in seeded:
        oc = r["outcome"]
        lines.append(
            f"| {r['seed_case']} | `--{r['mode']} {r['query']}` | {r['total']} | "
            f"{_outcome_cell(oc)} | {_fmt(r['perf'].get('p95_ms'))} | "
            f"{_status_mark(r['status'])} | {r['expected_phase_a']} | "
            f"{'ok' if r['invariant_ok'] else 'MISMATCH'} |"
        )
    lines.append("")
    return lines


def _rows_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Row | Archetype | Mode | Total | Outcome | p95 | Status | Bounded | OK |",
        "|-----|-----------|------|------:|---------|----:|--------|---------|----|",
    ]
    for r in rows:
        perf = r["perf"]
        bnd = r.get("boundedness") or {}
        bmark = "bounded" if bnd.get("bounded") else "O(corpus)"
        ok = "ok" if (r["invariant_ok"] and r.get("bounded_ok", True)) else "MISMATCH"
        lines.append(
            f"| {r['id']} | {r['archetype']} | {r['mode']} | {r['total']} | "
            f"{_outcome_cell(r['outcome'])} | {_fmt(perf.get('p95_ms'))} | "
            f"{_status_mark(r['status'])}/{r['expected_phase_a']} | "
            f"{bmark} ({bnd.get('rows_scanned')}/{bnd.get('corpus_docs')}) | {ok} |"
        )
    lines.append("")
    return lines


def _boundedness_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "The qmd invariant (R3): a query may scan ~its matches, not the whole "
        "corpus. `rows_scanned ~ corpus` with few matches = the O(corpus) cliff "
        "(U6 target). Deterministic, so it gates at any tier regardless of ms.",
        "",
        "| Row | Path | matched | rows_scanned | corpus | bounded | expected | OK |",
        "|-----|------|--------:|-------------:|-------:|---------|----------|----|",
    ]
    for r in rows:
        bnd = r.get("boundedness") or {}
        lines.append(
            f"| {r['id']} | {bnd.get('path')} | {bnd.get('matched')} | "
            f"{bnd.get('rows_scanned')} | {bnd.get('corpus_docs')} | "
            f"{'bounded' if bnd.get('bounded') else 'O(corpus)'} | "
            f"{'bounded' if r.get('bounded_expected') else 'O(corpus)'} | "
            f"{'ok' if r.get('bounded_ok') else 'MISMATCH'} |"
        )
    lines.append("")
    return lines


def _archetype_table(rows: list[dict[str, Any]]) -> list[str]:
    by_arch: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_arch.setdefault(r["archetype"], []).append(r)
    lines = [
        "| Archetype | n | mean recall | mean p95 ms | green | red |",
        "|-----------|--:|------------:|------------:|------:|----:|",
    ]
    for arch in sorted(by_arch):
        rs = by_arch[arch]
        recalls = [r["outcome"].get("recall_at_k") for r in rs if r["outcome"].get("recall_at_k") is not None]
        p95s = [r["perf"].get("p95_ms") for r in rs if r["perf"].get("p95_ms") is not None]
        green = sum(1 for r in rs if r["status"] == "green")
        mean_recall = sum(recalls) / len(recalls) if recalls else 0.0
        mean_p95 = sum(p95s) / len(p95s) if p95s else 0.0
        lines.append(
            f"| {arch} | {len(rs)} | {_fmt(mean_recall)} | {_fmt(mean_p95)} | "
            f"{green} | {len(rs) - green} |"
        )
    lines.append("")
    return lines


def render_markdown(reports: list[dict[str, Any]]) -> str:
    out: list[str] = []
    out.append("# Search Evaluation (plan 088)")
    out.append("")
    out.append(
        "Performance **and** outcome metrics for Trace Spotlight's progressive-discovery "
        "loop (`trace query -> map -> slice -> get`), measured over a deterministic, "
        "real-bucket-sized planted corpus. Regenerate with `make search-eval` "
        "(dev tier) or `make search-eval-real` (real-scale)."
    )
    out.append("")
    out.append(
        "> **RED is expected in Phase A.** The harness is the executable spec: "
        "S2 (semantic latency), S3/S4 (identifier-needle recall + time order), and S5 "
        "(recency) are designed to fail until the ranking capabilities (U4 `--sort`, "
        "U5 recency weighting, U6 index-bounded scorer + URL/identifier tokenization) "
        "land red -> green against this report. `OK` checks that observed RED/GREEN "
        "matches each row's documented expectation."
    )
    out.append("")

    if not reports:
        out.append("_No eval artifacts found. Run `make search-eval`._")
        out.append("")
        return "\n".join(out)

    for rep in reports:
        rows = rep["rows"]
        greens = sum(1 for r in rows if r["status"] == "green")
        out.append(f"## Tier: `{rep['tier']}` (seed {rep['seed']})")
        out.append("")
        out.append(
            f"- corpus: **{rep['corpus_size']}** traces, **{len(rows)}** query rows "
            f"({greens} green / {len(rows) - greens} red)"
        )
        out.append(f"- snapshot key: `{rep['snapshot_key']}`")
        out.append(f"- outcome digest (deterministic): `{rep['outcome_digest']}`")
        out.append(f"- profile digest: `{rep['profile_digest']}`")
        loop = rep.get("loop_smoke") or {}
        out.append(
            f"- discovery-loop smoke: {'ok' if loop.get('ok') else 'FAILED'} "
            f"(stages: {', '.join(loop.get('stages', [])) or loop.get('reason', 'n/a')})"
        )
        inv = rep.get("invariants_ok")
        out.append(f"- **invariants_ok: {'yes' if inv else 'NO — see MISMATCH rows'}**")
        out.append("")
        out.append("### Seed Evaluation Dataset (S1–S8)")
        out.append("")
        out.extend(_seed_table(rows))
        out.append("### By archetype")
        out.append("")
        out.extend(_archetype_table(rows))
        out.append("### Boundedness (qmd invariant, R3)")
        out.append("")
        out.extend(_boundedness_table(rows))
        out.append("### All query rows")
        out.append("")
        out.extend(_rows_table(rows))

    out.append("---")
    out.append(
        "_Outcome metrics are deterministic (recall/MRR/NDCG/tau/recency-hit over distinct "
        "traces); perf metrics are wall-clock (gate on scaling slope + counters, not absolute "
        "ms — see budgets). Generated by `tests/search_eval/report.py`._"
    )
    out.append("")
    return "\n".join(out)


def render_combined(out_path: Path = OUTPUT_PATH) -> Path:
    reports = _load_reports()
    out_path.write_text(render_markdown(reports), encoding="utf-8")
    return out_path


def render_report(report: Any = None, out_path: Path = OUTPUT_PATH) -> Path:
    """Entry point used by the runner: (re)render the combined report from all
    tier artifacts currently on disk."""

    return render_combined(out_path)


def main(argv: list[str] | None = None) -> int:
    path = render_combined()
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
