"""HTTP-shaped adapters for graph + blame data.

Serializes the graph renderer's ``Commit``/``TraceContribution`` dataclasses
and the blame/attribution cache into JSON payloads shaped for the React
viewer. Intentionally read-only; all mutation lives in ``server.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..text import graph_renderer as _gr
from ..text.trace_tree import build_tree


_PCT_GREEN = 70
_PCT_YELLOW = 30


def _pct(ratio: float | None) -> int:
    if ratio is None:
        return 0
    return max(0, min(100, int(round(ratio * 100))))


def _coverage_role(pct: int) -> str:
    if pct >= _PCT_GREEN:
        return "green"
    if pct >= _PCT_YELLOW:
        return "yellow"
    return "red"


def _full_entities_text(c: _gr.Commit, trace_id: str) -> str:
    """Human-readable "+ Added X, Y, Z / ~ Modified A, B" with no truncation."""
    if not c.entity_breakdowns or trace_id not in c.entity_breakdowns:
        return ""
    buckets: dict[str, list[str]] = {"added": [], "modified": [], "deleted": [], "renamed": []}
    seen_per_bucket: dict[str, set[str]] = {k: set() for k in buckets}
    for _fpath, entries in c.entity_breakdowns[trace_id]:
        for ct, _et, name in entries:
            if not name:
                continue
            bucket = buckets.setdefault(ct, [])
            seen = seen_per_bucket.setdefault(ct, set())
            if name in seen:
                continue
            seen.add(name)
            bucket.append(name)
    parts: list[str] = []
    if buckets["added"]:
        parts.append("+ Added " + ", ".join(buckets["added"]))
    if buckets["modified"]:
        parts.append("~ Modified " + ", ".join(buckets["modified"]))
    if buckets["deleted"]:
        parts.append("- Deleted " + ", ".join(buckets["deleted"]))
    if buckets["renamed"]:
        parts.append("\u21B7 Renamed " + ", ".join(buckets["renamed"]))
    return " · ".join(parts)


def serialize_commits(commits: list[_gr.Commit]) -> list[dict[str, Any]]:
    """Shape each Commit for the graph view left pane."""
    out: list[dict[str, Any]] = []
    for c in commits:
        pct = 0
        if c.traces:
            # All contribs share the same attributed_ratio for a commit.
            pct = _pct(c.traces[0].attributed_ratio)
        traces_out: list[dict[str, Any]] = []
        for t in c.traces:
            changes = _entity_summary_text(c, t.trace_id)
            row: dict[str, Any] = {
                "id": _gr.short_trace_id(t.trace_id) if hasattr(_gr, "short_trace_id") else t.trace_id[:8],
                "trace_id": t.trace_id,
                "canonical": t.canonical,
                "lines": t.line_count,
                "short_name": t.short_name,
            }
            if changes:
                row["changes"] = changes
            traces_out.append(row)
        out.append({
            "id": c.short_sha,
            "sha": c.sha,
            "msg": c.subject,
            "timestamp": c.timestamp,
            "pct": pct,
            "coverage_role": _coverage_role(pct),
            "traces": traces_out,
        })
    return out


def load_graph(cwd: Path, *, limit: int, page: int, trace_id: str | None,
               since: str | None, until: str | None,
               show_entities: bool) -> list[dict[str, Any]]:
    opts = _gr.RenderOptions(
        width=80, color=False,
        mode="trace" if trace_id else "commit",
        pivot_trace_id=trace_id,
        show_entities=show_entities,
        limit=limit, page=page,
        since=since, until=until,
    )
    commits = _gr.load_commits_from_repo(cwd, opts)
    return serialize_commits(commits)


def serialize_tree(record: Any, step_labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
    def _node_to_dict(node) -> dict[str, Any]:
        return {
            "id": node.id,
            "parent_id": node.parent_id,
            "kind": node.kind,
            "step_index": node.step_index,
            "timestamp": node.timestamp,
            "preview": node.preview,
            "label": node.label,
            "on_active_path": node.on_active_path,
            "entity_ref": node.entity_ref,
            "role": node.role,
            "children": [_node_to_dict(child) for child in node.children],
        }

    return [_node_to_dict(node) for node in build_tree(record, step_labels=step_labels)]


def _trace_meta(cwd: Path, trace_id: str) -> tuple[str | None, str | None]:
    """Return (short_name, model) for a trace id, if resolvable."""
    try:
        from ...core.trace_meta import resolve_trace_meta
        meta = resolve_trace_meta(cwd, trace_id)
        if meta is None:
            return None, None
        return meta.short_name, getattr(meta, "model", None)
    except Exception:
        return None, None


def _entity_summary_text(c: _gr.Commit, trace_id: str) -> str:
    """Human-readable, un-truncated entity summary for a trace in a commit."""
    full = _full_entities_text(c, trace_id)
    if full:
        return full
    # Fall back to the truncated summary when the entity cache isn't
    # populated enough to provide a breakdown.
    if not c.entity_summaries:
        return ""
    pair = c.entity_summaries.get(trace_id)
    if not pair:
        return ""
    counts, names = pair
    if not counts or counts == "\u2014":
        return names or ""
    verb = "Added" if counts.startswith("+") else "Modified"
    if names:
        return f"{counts.split()[0][0] or '~'} {verb} {names}"
    return counts


def load_blame(cwd: Path, sha: str) -> dict[str, Any] | None:
    """Return the forward-blame payload for a single commit sha."""
    from ...core.cache import AttributionCache
    from ...enrichment.git.blame import diff_line_count, resolve_sha

    full_sha = resolve_sha(sha, cwd) or sha
    cache = AttributionCache(cwd)
    data = cache.read_attribution(full_sha)
    if not data:
        return None

    cov = data.get("coverage") or {}
    attributed = int(cov.get("attributed") or 0)
    total_whole = int(cov.get("total") or 0)
    diff_total = diff_line_count(cwd, full_sha) or total_whole
    denom = diff_total or total_whole or 1
    pct = _pct(attributed / denom) if denom else 0

    # Per-file breakdown from attribution cache.
    files_out: list[dict[str, Any]] = []
    files_map = data.get("files") or {}
    for fpath, finfo in files_map.items():
        if not isinstance(finfo, dict):
            continue
        total = int(finfo.get("total") or 0)
        attr = int(finfo.get("attributed") or 0)
        files_out.append({"path": fpath, "total": total, "attr": attr})
    files_out.sort(key=lambda f: -f["attr"])

    # Per-trace sessions. Pull contrib + entity summaries via the renderer
    # pipeline for consistent formatting with the graph view.
    opts = _gr.RenderOptions(width=80, color=False, mode="commit",
                             show_entities=True, limit=1, page=1)
    commits = _gr.load_commits_from_repo(cwd, opts) if False else []
    # We already have attribution cache data; reuse it rather than re-running
    # git log for one sha.
    from ..text.graph_renderer import _attach_attribution, Commit  # noqa: SLF001
    stub = Commit(sha=full_sha, short_sha=full_sha[:8],
                  subject=data.get("subject") or "", timestamp="", parents=[])
    try:
        _attach_attribution([stub], cwd, show_entities=True)
    except Exception:
        pass

    sessions_out: list[dict[str, Any]] = []
    for t in stub.traces:
        lines = int(t.line_count or 0)
        tpct = _pct((lines / denom) if denom else None)
        short_name, model = _trace_meta(cwd, t.trace_id)
        sessions_out.append({
            "id": t.trace_id[:8],
            "trace_id": t.trace_id,
            "name": t.short_name or short_name or "",
            "lines": lines,
            "pct": f"{tpct}%",
            "model": model or "",
            "entities": _entity_summary_text(stub, t.trace_id) or "(no entity overlap)",
            "canonical": t.canonical,
        })
    sessions_out.sort(key=lambda s: -s["lines"])

    return {
        "sha": full_sha,
        "id": full_sha[:8],
        "msg": stub.subject or data.get("subject") or "",
        "pct": pct,
        "coverage": f"{pct}%",
        "attributed": f"{attributed}/{denom} lines",
        "fileCount": len(files_out),
        "sessions": sessions_out,
        "files": files_out,
    }


_SHA_RE = re.compile(r"^[0-9a-f]{4,40}$")


def load_inverse_blame(cwd: Path, trace_id: str) -> dict[str, Any]:
    """Return the set of commits a trace contributed to + aggregated files."""
    from ...core.cache import AttributionCache
    from ...enrichment.git.blame import diff_line_count

    cache = AttributionCache(cwd)

    commits_out: list[dict[str, Any]] = []
    files_agg: dict[str, int] = {}
    total_lines = 0
    model: str | None = None
    short_name: str | None = None

    for sha in cache.list_attributed_shas():
        data = cache.read_attribution(sha)
        if not data:
            continue
        # Match on full id or prefix.
        match = None
        for tr in (data.get("traces") or []):
            tid = tr.get("trace_id") or ""
            if tid == trace_id or tid.startswith(trace_id):
                match = tr
                break
        if not match:
            continue
        lines = int(match.get("line_count") or 0)
        total_lines += lines
        diff_total = diff_line_count(cwd, sha) or 1
        pct = _pct(lines / diff_total) if diff_total else 0
        # Resolve commit subject via git.
        subject = _commit_subject(cwd, sha)
        commits_out.append({
            "id": sha[:8],
            "sha": sha,
            "msg": subject,
            "linesInCommit": lines,
            "totalCommitLines": diff_total,
            "pct": f"{pct}%",
        })
        for f in (match.get("files") or []):
            files_agg[f] = files_agg.get(f, 0) + lines  # approx, no per-file breakdown

    sn, m = _trace_meta(cwd, trace_id)
    short_name, model = sn, m

    files_out = [{"path": p, "lines": n} for p, n in
                 sorted(files_agg.items(), key=lambda kv: -kv[1])]

    # Newest commits first (by sha ordering isn't chronological — rely on git).
    commits_out.sort(key=lambda c: -c["linesInCommit"])

    return {
        "trace": {
            "id": trace_id[:8],
            "trace_id": trace_id,
            "name": short_name or "",
            "lines": total_lines,
            "model": model or "",
        },
        "commits": commits_out,
        "files": files_out,
    }


def _commit_subject(cwd: Path, sha: str) -> str:
    import subprocess
    try:
        out = subprocess.run(
            ["git", "log", "-n", "1", "--format=%s", sha],
            cwd=cwd, capture_output=True, text=True, check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except FileNotFoundError:
        return ""
