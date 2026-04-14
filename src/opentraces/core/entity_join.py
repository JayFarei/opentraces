"""Shape-tolerant entity-cache loader + trace join.

Two entity-diff cache shapes currently exist in the wild:

- **snake_case / fixture binary** (phase-2 test doubles):
  ``{"entities": [{"change_type": "...", "entity_name": "...", ...}]}``
- **camelCase / real upstream binary** (plan-043 Phase 2 vendor):
  ``{"changes": [{"changeType": "...", "entityName": "...", ...}]}``

The Phase 4 blame path only read the snake_case shape, so on real
upstream caches ``--entities`` found zero matches. This module
normalises both into a canonical ``EntityChange`` and joins the entity
stream to the attribution cache to produce per-trace contribution
breakdowns used by the blame/graph renderers.

Public API:

    EntityChange(...)                    — canonical dataclass
    TraceContribution(...)               — joined trace contribution
    load_entities(cache_dir, sha)        — shape-tolerant loader
    join_entities_to_traces(project_cwd, sha) -> list[TraceContribution]
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .cache import AttributionCache


# ---------------------------------------------------------------------------
# dataclasses
# ---------------------------------------------------------------------------

@dataclass
class EntityChange:
    change_type: str          # added | modified | deleted | renamed
    entity_type: str          # function | class | struct | chunk | ...
    entity_name: str
    file_path: str
    entity_id: str | None = None
    old_entity_name: str | None = None
    old_file_path: str | None = None
    after_content: str | None = None
    before_content: str | None = None
    # Legacy fixtures sometimes pre-tag the trace_id directly on the entity
    # record. When present we honour it as the credit target (join step
    # skips dominance lookup). camelCase: traceId.
    trace_id_hint: str | None = None


@dataclass
class TraceContribution:
    trace_id: str
    line_count: int
    line_ratio: float
    entities: list[EntityChange] = field(default_factory=list)
    chunks: list[tuple[int, int, str]] = field(default_factory=list)
    # A stable short handle for display — just trace_id[:8].
    @property
    def short_id(self) -> str:
        return self.trace_id[:8] if self.trace_id else "?"


# ---------------------------------------------------------------------------
# loader
# ---------------------------------------------------------------------------

def _normalize_record(r: dict) -> EntityChange | None:
    """Accept either snake_case or camelCase; return EntityChange."""
    if not isinstance(r, dict):
        return None
    # camelCase -> canonical. Fall back to snake_case keys.
    change_type = r.get("changeType") or r.get("change_type") or "modified"
    entity_type = (
        r.get("entityType")
        or r.get("entity_type")
        or r.get("entity_kind")   # legacy fixture name
        or ""
    )
    entity_name = r.get("entityName") or r.get("entity_name") or ""
    file_path = r.get("filePath") or r.get("file_path") or r.get("file") or ""
    return EntityChange(
        change_type=str(change_type).lower(),
        entity_type=str(entity_type),
        entity_name=str(entity_name),
        file_path=str(file_path),
        entity_id=r.get("entityId") or r.get("entity_id"),
        old_entity_name=r.get("oldEntityName") or r.get("old_entity_name"),
        old_file_path=r.get("oldFilePath") or r.get("old_file_path"),
        after_content=r.get("afterContent") or r.get("after_content"),
        before_content=r.get("beforeContent") or r.get("before_content"),
        trace_id_hint=r.get("traceId") or r.get("trace_id"),
    )


def load_entities(cache_dir: Path, sha: str) -> list[EntityChange]:
    """Load entity changes for ``sha`` from a cache dir, tolerant of shape.

    ``cache_dir`` is the ``entities/`` directory or its parent (we try both).
    Missing file -> []. Invalid JSON -> [].
    """
    # Accept either the entities/ dir or the project root.
    candidates = [cache_dir / f"{sha}.json", cache_dir / "entities" / f"{sha}.json"]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        return []
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    # Both shapes accepted; if both present we prefer the richer "changes".
    items = raw.get("changes")
    if not isinstance(items, list) or not items:
        items = raw.get("entities") or []
    out: list[EntityChange] = []
    for r in items:
        n = _normalize_record(r)
        if n is not None:
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# join
# ---------------------------------------------------------------------------

_CHUNK_RE = re.compile(r"^lines\s+(\d+)\s*-\s*(\d+)$", re.IGNORECASE)


def _inflate_lines(cache: AttributionCache, finfo: dict) -> list[dict]:
    """Inflate file.lines from blob store if spilled."""
    lines = finfo.get("lines") if isinstance(finfo, dict) else None
    if isinstance(lines, list):
        return lines
    blob = (finfo or {}).get("blob_sha256")
    if blob:
        raw = cache.read_blob(blob)
        if raw is not None:
            try:
                parsed = json.loads(raw.decode("utf-8"))
                if isinstance(parsed, list):
                    return parsed
            except (ValueError, UnicodeDecodeError):
                return []
    return []


def _dominant_trace_in_range(
    lines: list[dict], start: int | None = None, end: int | None = None,
) -> tuple[str | None, list[str]]:
    """Return (dominant_trace_id, tied_trace_ids).

    When `start` and `end` are provided, only count lines with n in that
    inclusive range. Empty trace_ids and pre-audit/missing lines are ignored.
    """
    counts: Counter[str] = Counter()
    for ln in lines:
        if not isinstance(ln, dict):
            continue
        tid = ln.get("trace_id") or ""
        cons = ln.get("consistency") or "attributed"
        if not tid or cons != "attributed":
            continue
        n = int(ln.get("n") or 0)
        if start is not None and n < start:
            continue
        if end is not None and n > end:
            continue
        counts[tid] += 1
    if not counts:
        return None, []
    top_count = counts.most_common(1)[0][1]
    tied = [t for t, c in counts.items() if c == top_count]
    return tied[0], tied


def join_entities_to_traces(
    project_cwd: Path, sha: str,
) -> list[TraceContribution]:
    """Build per-trace contributions for commit ``sha``.

    Algorithm per entity:
    1. ``chunk`` + name matches ``^lines N-M$``: restrict dominant-trace
       lookup to that line range in ``files[file_path].lines``.
    2. ``after_content`` non-null: dominant across whole file (we don't
       attempt to locate the inserted region; whole-file attribution is
       the same granularity the renderer exposes).
    3. Fallback: dominant across whole file.

    Ties: credit entity to every tied trace_id.
    """
    cache = AttributionCache(Path(project_cwd).resolve())
    attribution = cache.read_attribution(sha) or {}
    entities = load_entities(cache.root / "entities", sha)

    # Base trace rows from the attribution cache — we preserve ordering.
    trace_rows = attribution.get("traces") or []
    total_cov = (attribution.get("coverage") or {}).get("total") or 0

    contribs: dict[str, TraceContribution] = {}
    for row in trace_rows:
        tid = row.get("trace_id") or ""
        if not tid:
            continue
        lc = int(row.get("line_count") or 0)
        ratio = (lc / total_cov) if total_cov else 0.0
        contribs[tid] = TraceContribution(
            trace_id=tid, line_count=lc, line_ratio=ratio,
        )

    files = attribution.get("files") or {}
    # Cache inflated lines per file path to avoid re-reading blobs.
    _inflated: dict[str, list[dict]] = {}

    def _lines_for(path: str) -> list[dict]:
        if path not in _inflated:
            _inflated[path] = _inflate_lines(cache, files.get(path) or {})
        return _inflated[path]

    for ent in entities:
        # Legacy-fixture hint: entity records carried an explicit trace_id.
        # Honour it verbatim (create a synthetic contrib if the trace_id
        # isn't in the attribution table — the test fixtures cover this).
        if ent.trace_id_hint:
            tid = ent.trace_id_hint
            c = contribs.get(tid)
            if c is None:
                c = TraceContribution(trace_id=tid, line_count=0, line_ratio=0.0)
                contribs[tid] = c
            c.entities.append(ent)
            continue

        file_lines = _lines_for(ent.file_path)
        if not file_lines:
            continue

        if ent.entity_type == "chunk":
            m = _CHUNK_RE.match(ent.entity_name or "")
            if m:
                start, end = int(m.group(1)), int(m.group(2))
                _, tied = _dominant_trace_in_range(file_lines, start, end)
                for tid in tied or []:
                    c = contribs.get(tid)
                    if c is None:
                        continue
                    c.chunks.append((start, end, ent.change_type))
                continue

        # Named entity: whole-file dominance (keeps the renderer bullet
        # semantics consistent — "this trace is the one that touched foo()").
        _, tied = _dominant_trace_in_range(file_lines)
        for tid in tied or []:
            c = contribs.get(tid)
            if c is None:
                continue
            c.entities.append(ent)

    # Preserve original row order, then append any synthetic contribs
    # (from trace_id_hint) that weren't in the attribution table.
    row_order = [r.get("trace_id") for r in trace_rows if r.get("trace_id")]
    seen = set(row_order)
    ordered: list[TraceContribution] = [
        contribs[t] for t in row_order if t in contribs
    ]
    for tid, c in contribs.items():
        if tid not in seen:
            ordered.append(c)
    return ordered
