"""Phase-0 bucket loader + deterministic stratified sampler (issue #141).

Reads ~/.opentraces/bucket/traces/v1/<project>/<trace>/trace.json (pretty-
printed full TraceRecord, migration-aware via load_record_dict). Produces a
committed deterministic sample manifest (trace IDs + project/agent/length +
nasty-case flags) so diversity is auditable, not asserted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from opentraces_schema import load_record_dict

import proto

BUCKET = Path(os.path.expanduser("~/.opentraces/bucket/traces/v1"))
HERE = Path(__file__).resolve().parent


def load_record(path: Path):
    return load_record_dict(json.loads(path.read_text(encoding="utf-8")))


def length_bucket(n: int) -> str:
    if n < 30:
        return "tiny"
    if n > 800:
        return "huge"
    return "medium"


def trace_flags(record) -> dict[str, Any]:
    steps = record.steps
    human_asks = proto.s1_human_ask_starts(steps)
    edit_steps = [i for i, s in enumerate(steps) if any(n in proto.EDIT_TOOLS for n in proto._tool_names(s))]
    err_steps = sum(1 for s in steps if proto._obs_error(s))
    subagent_tools = {"Agent", "spawn_agent", "wait_agent", "close_agent", "Task", "mcp__codex__codex"}
    subagent_steps = sum(
        1
        for s in steps
        if any(n in subagent_tools for n in proto._tool_names(s))
        or (getattr(s, "call_type", None) == "subagent")
    )
    control_leak = any(
        proto._TAG_BLOCK.match(proto._content(s).strip()) or proto._CONT.match(proto._content(s).strip())
        for s in steps
        if proto._role(s) == "user" and proto._content(s).strip()
    )
    meta = record.metadata or {}
    resume = bool(
        meta.get("is_compacted")
        or meta.get("compaction_events")
        or meta.get("supersedes")
        or meta.get("superseded_trace_ids")
    )
    return {
        "n_steps": len(steps),
        "human_ask_count": len(human_asks),
        "edit_count": len(edit_steps),
        "error_steps": err_steps,
        "subagent_steps": subagent_steps,
        "autonomous": len(human_asks) <= 1,          # only the implicit step-0 boundary
        "error_heavy": err_steps >= 5,
        "no_edits": len(edit_steps) == 0,
        "subagent_heavy": subagent_steps >= 3,
        "resume": resume,
        "control_leak": control_leak,
    }


def build_index() -> list[dict[str, Any]]:
    rows = []
    for p in sorted(BUCKET.glob("*/*/trace.json")):
        try:
            r = load_record(p)
        except Exception as exc:  # noqa: BLE001
            rows.append({"trace_id": p.parts[-2], "path": str(p), "load_error": repr(exc)[:200]})
            continue
        flags = trace_flags(r)
        rows.append(
            {
                "trace_id": r.trace_id,
                "path": str(p),
                "project": p.parts[-3],
                "agent": r.agent.name or "unknown",
                "length_bucket": length_bucket(flags["n_steps"]),
                **flags,
            }
        )
    return rows


NASTY = ["autonomous", "error_heavy", "no_edits", "subagent_heavy", "resume", "control_leak"]


def stratified_sample(index: list[dict], target: int, *, exclude: set[str] | None = None) -> list[str]:
    """Deterministic stratified pick (no RNG).

    Strategy: bucket every loadable trace by (project, agent, length_bucket),
    walk strata round-robin taking one each pass (sorted within stratum by
    trace_id) until `target` reached. Then force-include at least 2 traces per
    NASTY flag if not already present. Fully deterministic.
    """
    exclude = exclude or set()
    pool = [r for r in index if "load_error" not in r and r["trace_id"] not in exclude]
    strata: dict[tuple, list[dict]] = {}
    for r in pool:
        strata.setdefault((r["project"], r["agent"], r["length_bucket"]), []).append(r)
    for k in strata:
        strata[k].sort(key=lambda r: r["trace_id"])

    chosen: list[str] = []
    chosen_set: set[str] = set()
    keys = sorted(strata.keys())
    depth = 0
    progressed = True
    while len(chosen) < target and progressed:
        progressed = False
        for k in keys:
            if len(chosen) >= target:
                break
            if depth < len(strata[k]):
                tid = strata[k][depth]["trace_id"]
                if tid not in chosen_set:
                    chosen.append(tid)
                    chosen_set.add(tid)
                    progressed = True
        depth += 1

    # Force nasty-case coverage (>=2 each where the corpus has them).
    by_id = {r["trace_id"]: r for r in pool}
    for flag in NASTY:
        have = sum(1 for tid in chosen if by_id.get(tid, {}).get(flag))
        if have >= 2:
            continue
        candidates = sorted(
            (r["trace_id"] for r in pool if r.get(flag) and r["trace_id"] not in chosen_set),
            key=lambda t: t,
        )
        for tid in candidates[: max(0, 2 - have)]:
            chosen.append(tid)
            chosen_set.add(tid)
    return chosen


def manifest_for(ids: list[str], index: list[dict]) -> dict[str, Any]:
    by_id = {r["trace_id"]: r for r in index}
    rows = []
    for tid in ids:
        r = by_id.get(tid, {})
        rows.append(
            {
                "trace_id": tid,
                "project": r.get("project"),
                "agent": r.get("agent"),
                "length_bucket": r.get("length_bucket"),
                "n_steps": r.get("n_steps"),
                "flags": {f: bool(r.get(f)) for f in NASTY},
            }
        )
    # stratum tallies for auditability
    def tally(key):
        c: dict[str, int] = {}
        for row in rows:
            c[str(row.get(key))] = c.get(str(row.get(key)), 0) + 1
        return dict(sorted(c.items()))

    flag_tally = {f: sum(1 for row in rows if row["flags"][f]) for f in NASTY}
    return {
        "count": len(rows),
        "by_project": tally("project"),
        "by_agent": tally("agent"),
        "by_length_bucket": tally("length_bucket"),
        "nasty_flag_coverage": flag_tally,
        "traces": rows,
    }


if __name__ == "__main__":
    index = build_index()
    (HERE / "sample_index.json").write_text(json.dumps(index, indent=2))
    loadable = [r for r in index if "load_error" not in r]
    errors = [r for r in index if "load_error" in r]
    print(f"index: {len(index)} traces ({len(loadable)} loadable, {len(errors)} load errors)")

    det_ids = stratified_sample(index, 200)
    det_manifest = manifest_for(det_ids, index)
    (HERE / "manifest_det.json").write_text(json.dumps(det_manifest, indent=2))
    print(f"deterministic (S1/S2) sample: {det_manifest['count']}")
    print("  agents:", det_manifest["by_agent"])
    print("  length:", det_manifest["by_length_bucket"])
    print("  projects:", len(det_manifest["by_project"]))
    print("  nasty:", det_manifest["nasty_flag_coverage"])

    # cheap-LLM / reviewer subset: stratified 36 drawn from the det sample.
    llm_ids = stratified_sample([r for r in index if r.get("trace_id") in set(det_ids)], 36)
    llm_manifest = manifest_for(llm_ids, index)
    (HERE / "manifest_llm.json").write_text(json.dumps(llm_manifest, indent=2))
    print(f"cheap-LLM (S3/S4 + reviewer) subset: {llm_manifest['count']}")
    print("  agents:", llm_manifest["by_agent"])
    print("  length:", llm_manifest["by_length_bucket"])
    print("  nasty:", llm_manifest["nasty_flag_coverage"])
