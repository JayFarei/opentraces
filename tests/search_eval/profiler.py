"""U0 - Bucket profiler.

Read an opentraces bucket (the local ``~/.opentraces/bucket`` by default) and
emit a deterministic, PII-free **size profile** to
``tests/search_eval/real-bucket-profile.json``. The profile is *numbers only*:
trace count, steps-per-trace distribution, project/home count, generation
(supersession) distribution, and the manifest-derived event/blob counters.

The committed profile is the size target the U1 planting generator matches; it
is never the literal fixture (Decision Audit #3). The generator hashes the
``profile`` block (not ``provenance``) into the content-addressed snapshot key,
so two refreshes that change only ``provenance.generated_at`` do not perturb
determinism.

This module is intentionally decoupled from the opentraces package paths
machinery: it reads the bucket object store directly from the filesystem so it
can profile the live home, a fixture bucket, or a restored snapshot without
mutating process-global path state. Stdlib only.

Usage::

    python tests/search_eval/profiler.py --bucket ~/.opentraces/bucket \\
        --out tests/search_eval/real-bucket-profile.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any

PROFILE_SCHEMA = "opentraces.search_eval.profile.v1"

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUCKET = Path(os.path.expanduser("~/.opentraces/bucket"))
DEFAULT_OUT = REPO_ROOT / "tests" / "search_eval" / "real-bucket-profile.json"

_TRACE_OBJECTS_GLOB = "objects/traces/v1/*/*/current.json"
_POINTER_SCHEMA = "opentraces.bucket.trace_record_pointer.v1"


# --------------------------------------------------------------------------- #
# distribution helpers (deterministic; mirror tests/perf percentile semantics)
# --------------------------------------------------------------------------- #
def _percentile(values: list[int], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, math.ceil(len(ordered) * q) - 1)
    return float(ordered[idx])


def _distribution(values: list[int]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "total": 0,
            "min": 0,
            "p25": 0.0,
            "p50": 0.0,
            "p75": 0.0,
            "p90": 0.0,
            "p99": 0.0,
            "max": 0,
            "mean": 0.0,
        }
    total = sum(values)
    return {
        "count": len(values),
        "total": total,
        "min": min(values),
        "p25": _percentile(values, 0.25),
        "p50": _percentile(values, 0.50),
        "p75": _percentile(values, 0.75),
        "p90": _percentile(values, 0.90),
        "p99": _percentile(values, 0.99),
        "max": max(values),
        "mean": round(total / len(values), 4),
    }


# --------------------------------------------------------------------------- #
# object-store reader (decoupled from opentraces.core.paths)
# --------------------------------------------------------------------------- #
def _resolve_envelope_path(bucket_root: Path, pointer_path: Path) -> Path | None:
    """Resolve a ``current.json`` pointer to its ``<hash>.json`` envelope."""

    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if pointer.get("schema_version") != _POINTER_SCHEMA:
        return None
    object_path = pointer.get("object_path")
    if not isinstance(object_path, str) or not object_path:
        return None
    candidate = Path(object_path)
    if not candidate.is_absolute():
        candidate = bucket_root / object_path
    return candidate if candidate.is_file() else None


def _read_record_fields(envelope_path: Path) -> dict[str, Any] | None:
    """Pull only the numeric fields we profile from one envelope (no Pydantic)."""

    try:
        envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    record = envelope.get("record")
    if not isinstance(record, dict):
        return None
    steps = record.get("steps")
    step_count = len(steps) if isinstance(steps, list) else 0
    patches = record.get("patches")
    patch_count = len(patches) if isinstance(patches, list) else 0
    metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
    metric_steps = metrics.get("total_steps")
    files = _distinct_files(steps if isinstance(steps, list) else [])
    return {
        "project_slug": str(envelope.get("project_slug") or record.get("project_slug") or ""),
        "trace_id": str(record.get("trace_id") or envelope.get("trace_id") or ""),
        "session_id": str(record.get("session_id") or ""),
        "step_count": int(metric_steps) if isinstance(metric_steps, int) else step_count,
        "patch_count": patch_count,
        "generation_index": int(record.get("generation_index") or 0),
        "distinct_files": files,
        "has_timestamps": bool(record.get("timestamp_start") and record.get("timestamp_end")),
    }


def _distinct_files(steps: list[Any]) -> int:
    """Count distinct file paths touched by Edit/Read/Write tool calls in steps."""

    paths: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        for call in step.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            payload = call.get("input") or call.get("arguments") or {}
            if isinstance(payload, dict):
                fp = payload.get("file_path") or payload.get("path") or payload.get("notebook_path")
                if isinstance(fp, str) and fp:
                    paths.add(fp)
    return len(paths)


# --------------------------------------------------------------------------- #
# manifest (cheap summary fields)
# --------------------------------------------------------------------------- #
def _load_manifest(bucket_root: Path) -> dict[str, Any]:
    manifest_path = bucket_root / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _manifest_counters(manifest: dict[str, Any]) -> dict[str, Any]:
    def _block(name: str) -> dict[str, Any]:
        block = manifest.get(name)
        return block if isinstance(block, dict) else {}

    events = _block("events_v1") or _block("trail_events")
    context = _block("context_trees")
    raw = _block("raw_sources")
    return {
        "events": {
            "event_count": int(
                (_block("trail_events").get("event_count"))
                or events.get("latest_event_sequence")
                or 0
            ),
            "batch_count": int(events.get("batch_count") or 0),
            "repository_count": int(_block("trail_events").get("repository_count") or 0),
        },
        "context_tree": {
            "trace_coverage": int(context.get("trace_count") or 0),
            "unique_blobs": int(context.get("unique_layer_blob_count") or 0),
            "sum_layer_refs": int(context.get("sum_layer_refs_count") or 0),
        },
        "raw_sources": int(raw.get("object_count") or 0),
        "manifest_object_count": int(_block("trace_records").get("object_count") or 0),
        "manifest_digest": str(manifest.get("bucket_digest") or manifest.get("digest") or ""),
    }


# --------------------------------------------------------------------------- #
# profile builder
# --------------------------------------------------------------------------- #
def profile_bucket(bucket_root: Path) -> dict[str, Any]:
    """Read ``bucket_root`` and return the deterministic numeric profile dict."""

    bucket_root = bucket_root.expanduser().resolve()
    pointers = sorted((bucket_root).glob(_TRACE_OBJECTS_GLOB))

    step_counts: list[int] = []
    file_counts: list[int] = []
    generations: list[int] = []
    project_counts: dict[str, int] = {}
    session_generations: dict[str, set[int]] = {}
    traces_with_patches = 0
    traces_with_timestamps = 0
    skipped = 0
    seen: set[tuple[str, str]] = set()

    for pointer in pointers:
        envelope_path = _resolve_envelope_path(bucket_root, pointer)
        if envelope_path is None:
            skipped += 1
            continue
        fields = _read_record_fields(envelope_path)
        if fields is None or not fields["trace_id"]:
            skipped += 1
            continue
        key = (fields["project_slug"], fields["trace_id"])
        if key in seen:
            continue
        seen.add(key)

        step_counts.append(fields["step_count"])
        file_counts.append(fields["distinct_files"])
        generations.append(fields["generation_index"])
        project_counts[fields["project_slug"]] = project_counts.get(fields["project_slug"], 0) + 1
        if fields["patch_count"] > 0:
            traces_with_patches += 1
        if fields["has_timestamps"]:
            traces_with_timestamps += 1
        if fields["session_id"]:
            session_generations.setdefault(fields["session_id"], set()).add(
                fields["generation_index"]
            )

    trace_count = len(step_counts)
    superseded = sum(1 for g in generations if g > 0)
    multi_gen_sessions = sum(1 for gens in session_generations.values() if len(gens) > 1)
    manifest = _load_manifest(bucket_root)
    counters = _manifest_counters(manifest)

    profile = {
        "trace_count": trace_count,
        "project_count": len(project_counts),
        "projects": dict(sorted(project_counts.items())),
        "steps_per_trace": _distribution(step_counts),
        "distinct_files_per_trace": _distribution(file_counts),
        "generation_index": {
            **_distribution(generations),
            "superseded_fraction": round(superseded / trace_count, 4) if trace_count else 0.0,
        },
        "session_count": len(session_generations),
        "multi_generation_session_count": multi_gen_sessions,
        "fraction_with_patches": round(traces_with_patches / trace_count, 4)
        if trace_count
        else 0.0,
        "fraction_with_timestamps": round(traces_with_timestamps / trace_count, 4)
        if trace_count
        else 0.0,
        "context_tree": counters["context_tree"],
        "events": counters["events"],
        "raw_sources": counters["raw_sources"],
    }

    provenance = {
        "bucket_root": str(bucket_root),
        "manifest_object_count": counters["manifest_object_count"],
        "manifest_digest": counters["manifest_digest"],
        "pointers_seen": len(pointers),
        "pointers_skipped": skipped,
        # generated_at is intentionally OMITTED so the committed profile is
        # byte-stable across refreshes of an unchanged bucket; pass --stamp to
        # add it for human traceability (it is never hashed by the generator).
    }

    return {
        "schema_version": PROFILE_SCHEMA,
        "provenance": provenance,
        "profile": profile,
    }


def canonical_json(payload: Any) -> str:
    """Stable, sorted, pretty JSON (mirrors core.bucket_store._canonical_json)."""

    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_profile(profile: dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(canonical_json(profile), encoding="utf-8")


def profile_digest(profile: dict[str, Any]) -> str:
    """sha256 over the ``profile`` block only (the generator's size-key input)."""

    import hashlib

    block = profile.get("profile", profile)
    canonical = json.dumps(block, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Profile an opentraces bucket (U0).")
    parser.add_argument("--bucket", type=Path, default=DEFAULT_BUCKET, help="bucket root dir")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="profile output path")
    parser.add_argument(
        "--stamp",
        action="store_true",
        help="add provenance.generated_at (never hashed; off by default for byte-stability)",
    )
    parser.add_argument(
        "--print", dest="print_only", action="store_true", help="print profile, do not write"
    )
    args = parser.parse_args(argv)

    profile = profile_bucket(args.bucket)
    if args.stamp:
        from datetime import datetime, timezone

        profile["provenance"]["generated_at"] = (
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
    profile["provenance"]["profile_digest"] = profile_digest(profile)

    if args.print_only:
        print(canonical_json(profile), end="")
        return 0

    write_profile(profile, args.out)
    p = profile["profile"]
    print(
        f"wrote {args.out} :: {p['trace_count']} traces / {p['project_count']} projects / "
        f"steps p50={p['steps_per_trace']['p50']:.0f} mean={p['steps_per_trace']['mean']:.1f} "
        f"max={p['steps_per_trace']['max']} / supersession={p['generation_index']['superseded_fraction']:.3f} "
        f"/ digest={profile['provenance']['profile_digest'][:19]}…"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
