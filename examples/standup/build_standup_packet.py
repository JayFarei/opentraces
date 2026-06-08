#!/usr/bin/env python3
"""Standup consumer — prototype.

Reads a single day's traces out of the live private bucket, groups them by
*human* project (collapsing per-worktree slugs), and emits a deterministic
"standup briefing packet" that a writer model can turn into a shareable daily
standup.

This is the *deterministic data layer* of the workflow->consumer pattern: same
shape as pr-intent-summary-v1's build_rows.py, but the scope is a date window
instead of a branch range, and the row grain is one project (with nested
trace evidence) instead of one commit.

Usage:
    python build_standup_packet.py [--date YYYY-MM-DD] [--out DIR]

Default date is "yesterday" relative to today.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from opentraces.core.bucket_store import iter_trace_record_objects

# --- project naming -------------------------------------------------------

_WORKTREE_SUFFIX = re.compile(r"-[0-9a-f]{6,}$")

# Friendly names for slugs whose base isn't self-explanatory.
_FRIENDLY = {
    "2026-03-27-community-traces-hf": "opentraces (community-traces-hf)",
    "research": "research",
    "svc-client-convert": "svc-client-convert",
    "client-humandur": "client-humandur (humanduration)",
    "kb": "kb (knowledge base)",
}


def project_name(slug: str) -> str:
    base = _WORKTREE_SUFFIX.sub("", slug).rstrip("-")
    return _FRIENDLY.get(base, base or slug)


# --- evidence extraction --------------------------------------------------

_FILE_BUCKETS = [
    ("tests", lambda p: "/test" in p or p.startswith("test") or "tests/" in p),
    ("docs", lambda p: p.endswith((".md", ".mdx", ".rst")) or "/docs/" in p),
    ("schema", lambda p: "schema" in p),
    ("cli", lambda p: "/cli/" in p),
    ("core", lambda p: "/core/" in p),
    ("config", lambda p: p.endswith((".toml", ".json", ".yaml", ".yml", ".cfg", ".ini"))),
]


def _categorize_files(files: list[str]) -> dict[str, int]:
    out: Counter[str] = Counter()
    for f in files:
        placed = False
        for name, pred in _FILE_BUCKETS:
            if pred(f):
                out[name] += 1
                placed = True
                break
        if not placed:
            out["other"] += 1
    return dict(out)


def _trace_evidence(o: Any) -> dict[str, Any]:
    r = o.record
    files = sorted({p.file_path for p in r.patches if getattr(p, "file_path", None)})
    fn: dict[str, int] = r.metadata.get("function_names") or {}
    skills = [
        s.get("skill_name")
        for s in (r.metadata.get("skill_invocations") or [])
        if s.get("skill_name")
    ]
    vcs = r.environment.vcs.model_dump() if r.environment and r.environment.vcs else {}
    cts = r.context_tree_summary or {}
    dur_s = r.metrics.total_duration_s if r.metrics else None
    return {
        "trace_id": r.trace_id,
        "short_id": r.trace_id[:8],
        "goal": (r.task.description or "").strip() if r.task else "",
        "agent": f"{r.agent.name} {r.agent.version}" if r.agent else None,
        "model": r.agent.model if r.agent else None,
        "branch": vcs.get("branch"),
        "base_commit": (vcs.get("base_commit") or "")[:10] or None,
        "has_diff": bool(vcs.get("diff")),
        "started": r.timestamp_start,
        "ended": r.timestamp_end,
        "duration_min": round(dur_s / 60, 1) if dur_s else None,
        "steps": r.metrics.total_steps if r.metrics else None,
        "files_touched_count": len(files),
        "files_touched": files,
        "file_categories": _categorize_files(files),
        "tools": dict(sorted(fn.items(), key=lambda kv: -kv[1])[:6]),
        "skills": skills,
        "compactions": cts.get("compaction_count"),
        "context_nodes": cts.get("node_count"),
        "capture_method": cts.get("capture_method"),
        "supersedes": r.metadata.get("supersedes"),
        "superseded_by": bool(r.metadata.get("superseded_trace_ids")),
    }


def _project_rollup(traces: list[dict]) -> dict[str, Any]:
    all_files: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    skills: Counter[str] = Counter()
    branches: Counter[str] = Counter()
    total_min = 0.0
    total_steps = 0
    for t in traces:
        for f in t["files_touched"]:
            all_files[f] += 1
        for k, v in t["tools"].items():
            tools[k] += v
        for s in t["skills"]:
            skills[s] += 1
        if t["branch"]:
            branches[t["branch"]] += 1
        total_min += t["duration_min"] or 0
        total_steps += t["steps"] or 0
    return {
        "trace_count": len(traces),
        "total_active_min": round(total_min, 1),
        "total_active_hours": round(total_min / 60, 1),
        "total_steps": total_steps,
        "branches": dict(branches),
        "top_files": dict(all_files.most_common(12)),
        "top_tools": dict(tools.most_common(6)),
        "skills": dict(skills),
    }


def _merge_chain(group: list) -> dict[str, Any]:
    """Union evidence across a resume chain.

    Resumes re-emit cumulative state, but the terminal head can be a fresh
    continuation that lost earlier patches. Take the union of files, the
    elementwise-max of tool counts, and the max of steps/duration so the
    representative reflects everything the effort actually did.
    """
    files: set[str] = set()
    tools: Counter[str] = Counter()
    skills: set[str] = set()
    max_steps = 0
    max_dur = 0.0
    for o in group:
        r = o.record
        files.update(p.file_path for p in r.patches if getattr(p, "file_path", None))
        for k, v in (r.metadata.get("function_names") or {}).items():
            tools[k] = max(tools[k], v)
        skills.update(
            s.get("skill_name")
            for s in (r.metadata.get("skill_invocations") or [])
            if s.get("skill_name")
        )
        if r.metrics:
            max_steps = max(max_steps, r.metrics.total_steps or 0)
            max_dur = max(max_dur, r.metrics.total_duration_s or 0)
    return {
        "files": sorted(files),
        "tools": dict(sorted(tools.items(), key=lambda kv: -kv[1])[:6]),
        "skills": sorted(skills),
        "steps": max_steps,
        "duration_min": round(max_dur / 60, 1) if max_dur else None,
    }


def _collapse_supersessions(objs: list) -> tuple[list, int, dict[str, int], dict[str, dict]]:
    """Collapse resume/supersede chains to their terminal head.

    Codex resumes re-emit the whole session as a new generation; without this
    we'd count one logical effort N times and inflate every rollup. Group by
    session_id and keep the representative = highest generation_index, tie-broken
    by most steps. Returns (representatives, collapsed_count).
    """
    by_session: dict[str, list] = {}
    standalone: list = []
    for o in objs:
        sid = o.record.session_id
        if sid:
            by_session.setdefault(sid, []).append(o)
        else:
            standalone.append(o)

    reps: list = []
    collapsed = 0
    collapsed_by_id: dict[str, int] = {}
    merged_by_id: dict[str, dict] = {}
    for group in by_session.values():
        if len(group) == 1:
            reps.append(group[0])
            continue
        rep = max(
            group,
            key=lambda o: (
                o.record.generation_index or 0,
                o.record.metrics.total_steps if o.record.metrics else 0,
            ),
        )
        collapsed_by_id[rep.record.trace_id] = len(group) - 1
        merged_by_id[rep.record.trace_id] = _merge_chain(group)
        reps.append(rep)
        collapsed += len(group) - 1
    return reps + standalone, collapsed, collapsed_by_id, merged_by_id


def build_packet(target_day: str) -> dict[str, Any]:
    def day(r) -> str:
        ts = r.timestamp_start or r.timestamp_end or ""
        return ts[:10]

    raw_objs = [o for o in iter_trace_record_objects() if day(o.record) == target_day]
    objs, collapsed_total, collapsed_by_id, merged_by_id = _collapse_supersessions(raw_objs)
    by_project: dict[str, list] = {}
    for o in objs:
        by_project.setdefault(project_name(o.project_slug), []).append(o)

    projects = []
    for name, group in sorted(by_project.items(), key=lambda kv: -len(kv[1])):
        traces = []
        for o in group:
            ev = _trace_evidence(o)
            ev["collapsed_generations"] = collapsed_by_id.get(o.record.trace_id, 0)
            merged = merged_by_id.get(o.record.trace_id)
            if merged:  # union evidence across the resume chain
                ev["files_touched"] = merged["files"]
                ev["files_touched_count"] = len(merged["files"])
                ev["file_categories"] = _categorize_files(merged["files"])
                ev["tools"] = merged["tools"]
                ev["skills"] = merged["skills"]
                ev["steps"] = merged["steps"]
                ev["duration_min"] = merged["duration_min"]
            traces.append(ev)
        traces.sort(key=lambda t: t["started"] or "")
        projects.append(
            {
                "project": name,
                "slugs": sorted({o.project_slug for o in group}),
                "rollup": _project_rollup(traces),
                "traces": traces,
            }
        )

    return {
        "schema_version": "opentraces.standup.packet.v0",
        "date": target_day,
        "project_count": len(projects),
        "session_count": len(objs),
        "raw_trace_count": len(raw_objs),
        "collapsed_generations": collapsed_total,
        "projects": projects,
    }


# --- human-readable rendering --------------------------------------------


def render_md(packet: dict[str, Any]) -> str:
    L = [f"# Standup Briefing Packet - {packet['date']}", ""]
    L.append(
        f"_{packet['session_count']} distinct sessions across "
        f"{packet['project_count']} projects "
        f"({packet['collapsed_generations']} resume/supersede generations collapsed "
        f"from {packet['raw_trace_count']} raw traces)._"
    )
    L.append("")
    for p in packet["projects"]:
        ru = p["rollup"]
        L.append(f"## {p['project']}")
        L.append(
            f"- **{ru['trace_count']} sessions**, ~{ru['total_active_hours']}h active, "
            f"{ru['total_steps']} steps, branches: {', '.join(ru['branches']) or 'n/a'}"
        )
        if ru["skills"]:
            L.append(f"- skills: {ru['skills']}")
        L.append(f"- top files: {', '.join(list(ru['top_files'])[:8])}")
        L.append("")
        for t in p["traces"]:
            goal = (t["goal"] or "(no recorded goal)").replace("\n", " ")
            if len(goal) > 160:
                goal = goal[:157] + "..."
            gen = f" (+{t['collapsed_generations']} resumes)" if t.get("collapsed_generations") else ""
            L.append(f"  - `{t['short_id']}` [{t['agent']}]{gen} {t['duration_min']}min, "
                     f"{t['steps']} steps, {t['files_touched_count']} files")
            L.append(f"    goal: {goal}")
            if t["compactions"]:
                L.append(
                    f"    context: {t['compactions']} compactions, "
                    f"{t['context_nodes']} nodes ({t['capture_method']})"
                )
        L.append("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--out", default=str(Path(__file__).parent), help="output dir")
    args = ap.parse_args()

    target = args.date or (date.today() - timedelta(days=1)).isoformat()
    packet = build_packet(target)

    out = Path(args.out)
    (out / f"packet-{target}.json").write_text(json.dumps(packet, indent=2, default=str))
    (out / f"packet-{target}.md").write_text(render_md(packet))
    print(render_md(packet))
    print(f"\n[wrote packet-{target}.json and packet-{target}.md to {out}]")


if __name__ == "__main__":
    main()
