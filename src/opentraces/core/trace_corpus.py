"""Canonical "current trace corpus" resolver (issue #89).

Single source of truth for *which* ``TraceRecord``s exist across the durable
read layers and *which* source wins per trace. Before this module the union and
precedence rule were reimplemented three times — in the search-snapshot rebuild
(:func:`trace_search_snapshot._iter_documents`), in single-trace hydration for
``trace map/get/slice`` (:func:`bucket_store.read_bucket_record_for_trace`), and
in the legacy index refresh (:func:`trace_index._iter_trace_sources`) — and they
disagreed on legacy-mirror inclusion and on bucket-vs-project precedence. All
three now consume one enumeration here.

Layers, highest precedence first:

* ``bucket object``  — ``bucket/trace-records/v1/<project>/<trace>/current.json``
* ``bucket legacy mirror`` — plan-079 ``<project>/<trace>.json`` (object wins)
* ``project JSONL``  — ``projects/<slug>/traces/<id>.jsonl`` (``source_layer`` ``"canonical"``)
* ``staging JSONL``  — ``staging/<id>.jsonl`` (``source_layer`` ``"staging"``)

Winner per ``trace_id``: the freshest source by file mtime; ties break toward
the higher-priority layer (bucket beats project/staging). This is the freshness
guarantee #89 requires — a stale durable copy must never shadow a newer one —
and it reproduces the legacy index refresh's existing mtime-skip rule exactly
(``project_mtime <= bucket_mtime`` keeps the bucket).

The enumeration is *cheap*: it reads pointer files and ``stat`` digests, never a
full record validation. Callers that need the parsed record call
:func:`load_record` on the winning source.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from . import paths

# Layer identifiers, used both as a stable label and for tie-break priority.
LAYER_BUCKET = "bucket"          # v2 object or plan-079 legacy mirror
LAYER_PROJECT = "project"        # projects/<slug>/traces/<id>.jsonl
LAYER_STAGING = "staging"        # staging/<id>.jsonl

STAGING_SLUG = "_staging"

_LAYER_PRIORITY = {LAYER_BUCKET: 2, LAYER_PROJECT: 1, LAYER_STAGING: 1}


@dataclass(frozen=True)
class CorpusSource:
    """The winning durable source for one trace.

    ``path`` is the concrete file to read (a resolved bucket object, a legacy
    mirror JSON, or a project/staging JSONL). ``source_layer`` is the value
    stamped on the hydrated record (``"canonical"`` / ``"staging"`` / a bucket
    envelope's own layer), distinct from ``layer`` which names the storage tier
    used for precedence. ``cheap_digest`` is the bucket pointer ``record_hash``
    for bucket layers or a ``stat:`` digest for JSONL — enough for the index to
    detect change without parsing the record.
    """

    project_slug: str
    trace_id: str
    layer: str
    source_layer: str
    path: Path
    mtime_ns: int
    cheap_digest: str


def _priority(layer: str) -> int:
    return _LAYER_PRIORITY.get(layer, 0)


def _beats(candidate: CorpusSource, current: CorpusSource) -> bool:
    """True if ``candidate`` should win over ``current``.

    Orders by freshest mtime, then bucket tier, then source path. The final path
    key makes this a strict total order: on an mtime + tier tie the winner is
    deterministic (greatest path) rather than insertion-order dependent, so
    :func:`iter_sources` and :func:`resolve` agree regardless of glob order.
    """

    return (candidate.mtime_ns, _priority(candidate.layer), str(candidate.path)) > (
        current.mtime_ns,
        _priority(current.layer),
        str(current.path),
    )


def _safe_mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _file_stat_digest(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"stat:{stat.st_mtime_ns}:{stat.st_size}"


# --- bucket candidates -------------------------------------------------------


def _bucket_candidates() -> list[CorpusSource]:
    """Every bucket-tier source (object + legacy mirror), deduped by the store.

    Reuses :func:`bucket_store.iter_trace_record_pointers`, which already prefers
    the v2 object over the legacy mirror per ``(project, trace_id)`` and skips
    pointers whose object no longer exists — so it is the cheap, authoritative
    bucket enumeration.
    """

    from . import bucket_store as bs

    out: list[CorpusSource] = []
    for pointer in bs.iter_trace_record_pointers():
        mtime = _safe_mtime_ns(pointer.path)
        if mtime is None:
            continue
        out.append(
            CorpusSource(
                project_slug=pointer.project_slug,
                trace_id=pointer.trace_id,
                layer=LAYER_BUCKET,
                source_layer=pointer.source_layer,
                path=pointer.path,
                mtime_ns=mtime,
                cheap_digest=pointer.record_hash,
            )
        )
    return out


def _bucket_candidates_for(trace_id: str) -> list[CorpusSource]:
    """Direct-glob the bucket object + legacy mirror for one ``trace_id``.

    The v2 object wins over the plan-079 legacy mirror for the same
    ``project_slug`` unconditionally — exactly the object-first dedup that the
    bulk path's :func:`bucket_store.iter_trace_record_pointers` applies — so
    single-trace ``resolve`` and bulk ``iter_sources`` never disagree on
    object-vs-mirror precedence (issue #89).
    """

    from . import bucket_store as bs

    part = bs._path_part(trace_id)
    objects: list[CorpusSource] = []

    root = bs.trace_records_root()
    if root.exists():
        for pointer_path in sorted(root.glob(f"*/{part}/current.json")):
            source = _bucket_source_from_pointer_file(pointer_path, trace_id)
            if source is not None:
                objects.append(source)

    object_projects = {source.project_slug for source in objects}
    mirrors: list[CorpusSource] = []
    legacy_root = bs.legacy_trace_records_root()
    if legacy_root.exists():
        for mirror_path in sorted(legacy_root.glob(f"*/{part}.json")):
            source = _bucket_source_from_object_file(mirror_path, trace_id)
            if source is not None and source.project_slug not in object_projects:
                mirrors.append(source)
    return objects + mirrors


def _bucket_source_from_pointer_file(pointer_path: Path, trace_id: str) -> CorpusSource | None:
    from . import bucket_store as bs

    try:
        raw = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if raw.get("schema_version") != bs.TRACE_RECORD_POINTER_SCHEMA:
        return None
    if str(raw.get("trace_id") or "") != trace_id:
        return None
    object_path = raw.get("object_path")
    if not isinstance(object_path, str) or not object_path:
        return None
    # Apply the SAME required-field validation as the bulk enumerator
    # (bucket_store.iter_trace_record_pointers): a record missing project_slug /
    # source_layer / record_hash is skipped by the bulk path, so resolve() must
    # skip it too or the two would disagree on corrupt bucket metadata.
    project_slug = str(raw.get("project_slug") or "")
    source_layer = str(raw.get("source_layer") or "")
    record_hash = str(raw.get("record_hash") or "")
    if not project_slug or not source_layer or not record_hash:
        return None
    resolved = paths.bucket_dir() / object_path
    mtime = _safe_mtime_ns(resolved)
    if mtime is None:
        return None
    return CorpusSource(
        project_slug=project_slug,
        trace_id=trace_id,
        layer=LAYER_BUCKET,
        source_layer=source_layer,
        path=resolved,
        mtime_ns=mtime,
        cheap_digest=record_hash,
    )


def _bucket_source_from_object_file(object_path: Path, trace_id: str) -> CorpusSource | None:
    from . import bucket_store as bs

    try:
        raw = json.loads(object_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if raw.get("schema_version") != bs.TRACE_RECORD_BUCKET_SCHEMA:
        return None
    if str(raw.get("trace_id") or "") != trace_id:
        return None
    # Same required-field validation as the bulk legacy-mirror enumerator.
    project_slug = str(raw.get("project_slug") or "")
    source_layer = str(raw.get("source_layer") or "")
    record_hash = str(raw.get("record_hash") or "")
    if not project_slug or not source_layer or not record_hash:
        return None
    mtime = _safe_mtime_ns(object_path)
    if mtime is None:
        return None
    return CorpusSource(
        project_slug=project_slug,
        trace_id=trace_id,
        layer=LAYER_BUCKET,
        source_layer=source_layer,
        path=object_path,
        mtime_ns=mtime,
        cheap_digest=record_hash,
    )


# --- project / staging candidates -------------------------------------------


def _project_homes() -> list[Path]:
    if not paths.PROJECTS_DIR.exists():
        return []
    return sorted(path for path in paths.PROJECTS_DIR.iterdir() if path.is_dir())


def _jsonl_source(project_slug: str, path: Path, source_layer: str) -> CorpusSource:
    layer = LAYER_STAGING if source_layer == "staging" else LAYER_PROJECT
    return CorpusSource(
        project_slug=project_slug,
        trace_id=path.stem,
        layer=layer,
        source_layer=source_layer,
        path=path,
        mtime_ns=_safe_mtime_ns(path) or 0,
        cheap_digest=_file_stat_digest(path),
    )


def _jsonl_candidates() -> list[CorpusSource]:
    out: list[CorpusSource] = []
    for project_home in _project_homes():
        traces_dir = project_home / "traces"
        if not traces_dir.exists():
            continue
        for path in sorted(traces_dir.glob("*.jsonl")):
            out.append(_jsonl_source(project_home.name, path, "canonical"))
    staging_root = getattr(paths, "STAGING_DIR", None)
    if staging_root and staging_root.exists() and staging_root.is_dir():
        for path in sorted(staging_root.glob("*.jsonl")):
            out.append(_jsonl_source(STAGING_SLUG, path, "staging"))
    return out


def _jsonl_candidates_for(trace_id: str) -> list[CorpusSource]:
    out: list[CorpusSource] = []
    projects_root = paths.PROJECTS_DIR
    if projects_root.exists():
        for path in sorted(projects_root.glob(f"*/traces/{trace_id}.jsonl")):
            out.append(_jsonl_source(path.parent.parent.name, path, "canonical"))
    staging_root = getattr(paths, "STAGING_DIR", None)
    if staging_root and staging_root.exists() and staging_root.is_dir():
        for path in sorted(staging_root.glob(f"{trace_id}.jsonl")):
            out.append(_jsonl_source(STAGING_SLUG, path, "staging"))
    return out


# --- public enumeration ------------------------------------------------------


def iter_sources() -> Iterator[CorpusSource]:
    """Yield the winning :class:`CorpusSource` per ``trace_id``, deduped.

    Dedup is by ``trace_id`` alone (it is a globally-unique UUID), not by
    ``(project_slug, trace_id)``. This matches the search snapshot — the
    authoritative read model — which has always deduped by ``trace_id``; the old
    legacy index keyed on the pair only as an incidental detail of its mtime
    skip. Deterministic order (sorted by ``trace_id``). Cheap: no full record
    parse.
    """

    winners: dict[str, CorpusSource] = {}
    for candidate in (*_bucket_candidates(), *_jsonl_candidates()):
        current = winners.get(candidate.trace_id)
        if current is None or _beats(candidate, current):
            winners[candidate.trace_id] = candidate
    for trace_id in sorted(winners):
        yield winners[trace_id]


def resolve(trace_id: str) -> CorpusSource | None:
    """Resolve the single winning source for ``trace_id``, or ``None``.

    Direct-globs by id so a lookup is O(projects), not O(all traces).
    """

    if not trace_id:
        return None
    candidates = [*_bucket_candidates_for(trace_id), *_jsonl_candidates_for(trace_id)]
    if not candidates:
        return None
    winner = candidates[0]
    for candidate in candidates[1:]:
        if _beats(candidate, winner):
            winner = candidate
    return winner


def load_record(source: CorpusSource):
    """Hydrate the full ``BucketTraceRecord`` for a resolved source, or ``None``."""

    from . import bucket_store as bs

    if source.layer == LAYER_BUCKET:
        return bs.read_trace_record_object(source.path)
    return bs.project_store_record_from_path(
        source.path,
        trace_id=source.trace_id,
        project_slug=source.project_slug,
        source_layer=source.source_layer,
    )
