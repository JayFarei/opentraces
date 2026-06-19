"""Inverse blame — commits a single trace contributed to.

The *forward* blame view (``ot blame <sha>``) asks "which traces touched
this commit?". Inverse blame is the reverse: "which commits carry output
from this trace?". Two data sources feed the answer, both keyed by
``trace_id``:

* **Attribution cache** — per-commit JSON under
  ``~/.opentraces/projects/<slug>/attributions/<sha>.json``. When a
  commit's file contents can be traced back to an agent's Edit via
  ``git blame`` against the audit ref, the trace appears in that
  commit's ``traces`` list with a ``line_count``. Fine-grained.
* **Trace ``git_links``** — list of (tier, revision) pairs persisted
  on the ``TraceRecord`` JSONL by the post-commit hook and
  ``git-backfill``. Coarse: ``tool_emitted`` /
  ``tool_emitted_with_divergence`` / ``overlapping`` / ``orphan``.

``compute()`` merges both sources, keyed by commit sha, and always
prefers attribution-cache line counts when present. Overlapping /
orphan tiers are filtered by default because in the per-session framing
they'd drown real signal with time-window coincidence (the filter trimmed
an observed 162→36 commit set during early validation).

Consumed by ``cli/blame.py`` (the trace-mode ``trail blame`` CLI).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

CONTRIBUTION_TIERS = ("tool_emitted", "tool_emitted_with_divergence")
_TIER_RANK = {"tool_emitted": 0, "tool_emitted_with_divergence": 1, "overlapping": 2}


@dataclass
class InverseBlameCommit:
    sha: str
    short_sha: str
    subject: str
    lines_in_commit: int
    total_commit_lines: int
    pct: int  # 0-100; 0 when unknown
    source: Literal["attribution", "git_links", "trail_events"]
    tier: str | None  # tool_emitted / tool_emitted_with_divergence / overlapping / None


@dataclass
class InverseBlameResult:
    trace_id: str
    short_id: str
    short_name: str
    model: str
    total_lines: int  # sum of attribution-cache line counts across commits
    commits: list[InverseBlameCommit] = field(default_factory=list)
    files: list[tuple[str, int]] = field(default_factory=list)  # (path, line_count)

    @property
    def attribution_commits(self) -> list[InverseBlameCommit]:
        return [c for c in self.commits if c.source == "attribution"]

    @property
    def git_link_commits(self) -> list[InverseBlameCommit]:
        return [c for c in self.commits if c.source == "git_links"]

    @property
    def trail_event_commits(self) -> list[InverseBlameCommit]:
        return [c for c in self.commits if c.source == "trail_events"]


def compute(
    project_cwd: Path,
    trace_id: str,
    *,
    include_overlapping: bool = False,
) -> InverseBlameResult:
    """Return the inverse-blame result for ``trace_id``.

    ``trace_id`` may be a full id or any prefix; matching is prefix-based
    against both attribution-cache entries and the trace's own ``trace_id``
    field (via ``TraceRecord.model_validate`` on the staged JSONL). When
    ``include_overlapping=True`` the coarse "overlapping" tier is also
    surfaced — useful for diagnostic views, hidden by default so the
    happy path ("which commits did this session contribute to?") stays
    signal-heavy.
    """
    from ..enrichment.git.blame import diff_line_count
    from .cache import AttributionCache
    from .config import get_project_traces_dir
    from .inbox import load_trace_records
    from .trace_meta import resolve_trace_meta, short_trace_id

    project_cwd = Path(project_cwd).resolve()
    cache = AttributionCache(project_cwd)

    # --- Load ALL matching trace records. Forks of one Claude Code
    # session produce multiple canonical trace_ids sharing one session_id;
    # when the caller hands us a session-shaped identifier we want the
    # merged view across every fork rather than arbitrarily picking one.
    # Match on trace_id OR session_id in either direction so any of the
    # three identifier forms (canonical trace_id, legacy session_id,
    # attribution-cache key) resolves.
    staging = get_project_traces_dir(project_cwd)
    records: list = []
    if staging.exists():
        for r in load_trace_records(staging):
            sid = getattr(r, "session_id", "") or ""
            if (
                r.trace_id == trace_id
                or r.trace_id.startswith(trace_id)
                or trace_id.startswith(r.trace_id)
                or (sid and (sid == trace_id or sid.startswith(trace_id)
                             or trace_id.startswith(sid)))
            ):
                records.append(r)
    primary = records[0] if records else None

    # Identifier set: the input, plus every matching fork's trace_id and
    # session_id. Matching any of these against the attribution cache
    # counts as a hit.
    id_candidates: set[str] = {trace_id}
    for r in records:
        id_candidates.add(r.trace_id)
        sid = getattr(r, "session_id", None)
        if sid:
            id_candidates.add(sid)

    def _matches(cache_tid: str) -> bool:
        """True iff the attribution cache's trace_id field corresponds to
        the target trace (by full-equality or prefix on either side)."""
        for cand in id_candidates:
            if not cand:
                continue
            if cache_tid == cand:
                return True
            if cache_tid.startswith(cand) or cand.startswith(cache_tid):
                return True
        return False

    # --- Attribution cache (fine) -------------------------------------
    by_sha: dict[str, InverseBlameCommit] = {}
    files_agg: dict[str, int] = {}
    total_lines = 0
    canonical_trace_id = primary.trace_id if primary is not None else trace_id

    try:
        from .trails import build_trail_query_projection

        trail_projection = build_trail_query_projection(project_cwd)
        trail_resolved = trail_projection.resolve_trace_prefix(trace_id)
        if trail_resolved:
            canonical_trace_id = trail_resolved
            id_candidates.add(trail_resolved)
    except Exception:
        trail_projection = None

    for sha in cache.list_attributed_shas():
        data = cache.read_attribution(sha)
        if not data:
            continue
        match = None
        for tr in (data.get("traces") or []):
            tid = tr.get("trace_id") or ""
            if not tid:
                continue
            if _matches(tid):
                match = tr
                break
        if not match:
            continue
        lines = int(match.get("line_count") or 0)
        total_lines += lines
        diff_total = diff_line_count(project_cwd, sha)
        pct = _pct_int(lines / diff_total) if diff_total > 0 else 0
        by_sha[sha] = InverseBlameCommit(
            sha=sha,
            short_sha=sha[:8],
            subject=_commit_subject(project_cwd, sha),
            lines_in_commit=lines,
            total_commit_lines=diff_total,
            pct=pct,
            source="attribution",
            tier=None,
        )
        for f in (match.get("files") or []):
            files_agg[f] = files_agg.get(f, 0) + lines

    # --- git_links (coarse) -------------------------------------------
    # Union across all matching fork records: one session_id may have
    # N canonical trace_ids, each with its own git_links written by the
    # post-commit hook. The caller asked about "this session" — they
    # want the merged set of commits any fork contributed to, not just
    # whichever fork we picked first.
    if records:
        allowed = set(CONTRIBUTION_TIERS)
        if include_overlapping:
            allowed.add("overlapping")
        best_by_sha: dict[str, str] = {}
        for r in records:
            for link in r.git_links:
                sha = (link.revision or "").strip()
                if len(sha) < 7:
                    continue
                tier = link.tier or ""
                if tier not in allowed:
                    continue
                prior = best_by_sha.get(sha)
                if prior is None or _TIER_RANK.get(tier, 99) < _TIER_RANK.get(prior, 99):
                    best_by_sha[sha] = tier
        for sha, tier in best_by_sha.items():
            if sha in by_sha:
                # Attribution already has a line count; just annotate tier.
                by_sha[sha].tier = tier
                continue
            diff_total = diff_line_count(project_cwd, sha) or 0
            by_sha[sha] = InverseBlameCommit(
                sha=sha,
                short_sha=sha[:8],
                subject=_commit_subject(project_cwd, sha),
                lines_in_commit=0,
                total_commit_lines=diff_total,
                pct=0,
                source="git_links",
                tier=tier,
            )

    # --- Trace Trails (canonical local event log) -----------------------
    if trail_projection is not None:
        trail_rows: list[dict] = []
        seen_anchor_ids: set[str] = set()
        for candidate in sorted(id_candidates):
            for row in trail_projection.anchors_for_trace(candidate):
                anchor_id = row.get("git_anchor_id")
                if anchor_id and anchor_id in seen_anchor_ids:
                    continue
                if anchor_id:
                    seen_anchor_ids.add(anchor_id)
                trail_rows.append(row)
        for row in trail_rows:
            sha = (row.get("commit_sha") or "").strip()
            if not sha:
                continue
            lines = int(row.get("line_count") or 0)
            if row.get("file_path"):
                files_agg[row["file_path"]] = files_agg.get(row["file_path"], 0) + lines
            if sha in by_sha and by_sha[sha].source == "attribution":
                if by_sha[sha].tier is None:
                    by_sha[sha].tier = row.get("evidence_tier") or "trail_events"
                continue
            diff_total = diff_line_count(project_cwd, sha) or 0
            by_sha[sha] = InverseBlameCommit(
                sha=sha,
                short_sha=sha[:8],
                subject=_commit_subject(project_cwd, sha),
                lines_in_commit=lines,
                total_commit_lines=diff_total,
                pct=_pct_int(lines / diff_total) if diff_total > 0 else 0,
                source="trail_events",
                tier=row.get("evidence_tier") or "trail_events",
            )

    # Sort by evidence strength, then line count / tier / sha so order is
    # deterministic across runs.
    commits = sorted(
        by_sha.values(),
        key=lambda c: (
            {"attribution": 0, "trail_events": 1, "git_links": 2}.get(c.source, 9),
            -c.lines_in_commit,
            _TIER_RANK.get(c.tier or "", 99),
            c.sha,
        ),
    )

    meta = resolve_trace_meta(project_cwd, trace_id)
    short_name = meta.short_name if meta and meta.short_name else ""
    model = (meta.model if meta else None) or ""

    resolved = canonical_trace_id or trace_id
    return InverseBlameResult(
        trace_id=resolved,
        short_id=short_trace_id(resolved),
        short_name=short_name,
        model=model,
        total_lines=total_lines,
        commits=commits,
        files=sorted(files_agg.items(), key=lambda kv: -kv[1]),
    )


def _pct_int(ratio: float | None) -> int:
    if ratio is None:
        return 0
    return max(0, min(100, int(round(ratio * 100))))


def _commit_subject(cwd: Path, sha: str) -> str:
    try:
        out = subprocess.run(
            ["git", "log", "-n", "1", "--format=%s", sha],
            cwd=cwd, capture_output=True, text=True, check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except FileNotFoundError:
        return ""
