#!/usr/bin/env python3
"""Extract *just enough* narrative signal from each session to write a standup.

Not metrics. The three things an engineer reconstructs from memory:
  1. What was asked  -> the opening task + the human's follow-up turns (pivots).
  2. What the agent did / said landed -> its closing summary message.
  3. What touched the tree -> top files + whether they look committed.

Prints a compact, readable "narrative source" per project, collapsing
resume/supersede chains to their terminal head (same rule as the packet builder).
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from opentraces.core.bucket_store import iter_trace_record_objects

_WORKTREE = re.compile(r"-[0-9a-f]{6,}$")
_FRIENDLY = {
    "2026-03-27-community-traces-hf": "opentraces",
    "client-humandur": "client-humandur",
    "kb": "kb (knowledge base)",
}


def project_name(slug: str) -> str:
    base = _WORKTREE.sub("", slug).rstrip("-")
    return _FRIENDLY.get(base, base or slug)


def day(r) -> str:
    ts = r.timestamp_start or r.timestamp_end or ""
    return ts[:10]


def clean(s: str) -> str:
    s = (s or "").replace("\n", " ").strip()
    return re.sub(r"\s+", " ", s)


def user_turns(r) -> list[str]:
    out = []
    seen = set()
    for s in r.steps:
        if s.role == "user" and isinstance(s.content, str):
            c = clean(s.content)
            # drop injected wrappers / boilerplate prompt scaffolding
            if not c or c.startswith("<") or len(c) < 4:
                continue
            key = c[:80].lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
    return out


def last_assistant(r) -> str | None:
    for s in reversed(r.steps):
        if s.role in ("assistant", "agent") and isinstance(s.content, str):
            c = s.content.strip()
            if len(c) > 60:
                return clean(c)
    return None


def collapse(objs):
    by_session: dict[str, list] = {}
    standalone = []
    for o in objs:
        sid = o.record.session_id
        (by_session.setdefault(sid, []) if sid else standalone).append(o) if sid else standalone.append(o)
    reps = []
    extra = {}
    for sid, g in by_session.items():
        if len(g) == 1:
            reps.append(g[0])
            continue
        rep = max(g, key=lambda o: (o.record.generation_index or 0,
                                    o.record.metrics.total_steps if o.record.metrics else 0))
        extra[rep.record.trace_id] = len(g) - 1
        reps.append(rep)
    return reps + standalone, extra


def main():
    target = (date.today() - timedelta(days=1)).isoformat()
    objs = [o for o in iter_trace_record_objects() if day(o.record) == target]
    reps, extra = collapse(objs)

    by_proj: dict[str, list] = {}
    for o in reps:
        by_proj.setdefault(project_name(o.project_slug), []).append(o)

    out = [f"# Narrative source — {target}", ""]
    for proj, group in sorted(by_proj.items(), key=lambda kv: -len(kv[1])):
        out.append(f"\n{'='*70}\n## {proj}  ({len(group)} sessions)\n{'='*70}")
        for o in sorted(group, key=lambda o: o.record.timestamp_start or ""):
            r = o.record
            ut = user_turns(r)
            la = last_assistant(r)
            dur = r.metrics.total_duration_s / 60 if r.metrics else 0
            files = sorted({p.file_path for p in r.patches if getattr(p, "file_path", None)})
            res = f" +{extra[r.trace_id]} resumes" if r.trace_id in extra else ""
            out.append(f"\n### {r.trace_id[:8]} · {r.agent.name}{res} · {dur:.0f}min · {len(files)} files")
            out.append(f"ASKED ({len(ut)} turns):")
            for u in ut[:8]:
                out.append(f"  • {u[:200]}")
            if files:
                out.append(f"FILES: {', '.join(f.split('/')[-1] for f in files[:10])}")
            out.append(f"AGENT WRAP-UP: {(la or '(no closing summary)')[:600]}")
    text = "\n".join(out)
    Path(__file__).parent.joinpath(f"narrative-source-{target}.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
