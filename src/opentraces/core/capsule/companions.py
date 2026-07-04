"""Capsule companion redaction (#197) — the FIRST consumer that copies RAW
per-trace companions into a shareable unit, so the F1 companion-leak goes live
HERE (not in the S1 inline envelope, whose floor already covered it).

``redact_companions`` decompresses a raw ``trail/context/sources.jsonl.gz``
companion, runs M1's field-aware ``sanitize_companion_dict`` per line (routing
each leaf through ``companion_field_type`` so a ``cwd`` path-anonymizes, a chat
message goes to NER, a tool description reads as tool input), then re-serializes
deterministically (canonical JSON per line, gzip ``mtime=0``) for cross-machine
byte-identity.

Load-bearing discipline: this module does NOT reimplement sanitization and does
NOT bolt redaction onto ``bucket_trail.py`` (a reader). It is a thin adapter over
the substrate capability — the ONLY sanitizer is
:func:`opentraces.security.pipeline.sanitize_companion_dict`.

#209 (W1) adds process-parallel chunking of the per-line loop for large
companions (measured ~3 MB/s single-thread on a real 473 MB-decompressed
trail companion; regex/entropy/path-anonymizer work is pure-Python and
GIL-bound, so threads do not help — only separate processes do). The parallel
path and the serial path share ONE function, ``_sanitize_lines``: the serial
path calls it once with the whole line list as a single "chunk", the parallel
path calls it once per byte-balanced chunk in a worker process. Because it is
the exact same code either way, chunk count can never change WHICH bytes come
out the other end — only how many processes did the work — which is what
keeps the two paths byte-identical (the #209 Part A gate).

External review on PR #220 (critical 1): parallel dispatch is gated on
``tools is None`` — the DEFAULT registry path (``COMPANION_FLOOR``), which
every worker reconstructs byte-identically from the registry with zero
cross-process state. An explicit ``tools`` argument may carry a BOUND tool
instance (e.g. ``LLMPIIDetectorTool().with_detector(...)``, exercised by
``tests/test_capsule_mini_bucket.py``'s NER test) that cannot be reduced to a
bare registry name without losing the injected state — a worker re-resolving
"llm_pii" by name would silently run the UNBOUND default instead, a real
under-redaction risk, not just a byte-identity nuisance. Rather than try to
prove per-tool picklability, ANY explicit ``tools`` argument now forces the
serial in-process path unconditionally, exactly matching pre-#209 behavior
for that case (correctness over cleverness).
"""

from __future__ import annotations

import gzip
import json
import multiprocessing
import os
from itertools import repeat
from typing import Any, Sequence

from .._bucket_io import _canonical_json, _gzip_deterministic

COMPANION_REDACTION_SCHEMA_VERSION = "opentraces.capsule.companion_redaction.v1"

# Fail-closed placeholder: a line the sanitizer could not process is replaced by
# this marker (never emitted verbatim) and the floor is marked NOT satisfied.
_UNSANITIZABLE_PLACEHOLDER = "[opentraces:companion-line-unsanitizable-redacted]"

# --------------------------------------------------------------------------- #
# Parallel dispatch tuning (#209 W1)
# --------------------------------------------------------------------------- #

# Below this size, ProcessPoolExecutor startup (spawning N interpreters, each
# re-importing the package) costs more than it saves — normal traces seal in
# 2-3s total and must not pay a pool-startup tax. Only outlier companions
# (the ones this lane targets) cross this line.
_PARALLEL_THRESHOLD_BYTES = 4 * 1024 * 1024  # 4 MB

# Chunks-per-worker > 1 so a slow chunk (one huge line) doesn't leave other
# workers idle while it finishes — the pool keeps pulling the next chunk.
_CHUNKS_PER_WORKER = 4

_DEFAULT_MAX_WORKERS = 8

# Explicit "spawn" context (#209 W1 fork-safety): the default start method on
# Linux is still "fork", which duplicates the parent's full memory image —
# including any threads/locks/open handles the capsule-seal CLI process has
# already accumulated (git subprocess pipes, HF client sessions, etc.) — into
# every worker. That is the exact fork-safety hazard the issue calls out.
# "spawn" always starts a clean interpreter and only ships the picklable
# function + args across, at the cost of a per-pool import; forced uniformly
# here so behavior does not depend on which OS the seal runs on.
_MP_CONTEXT = multiprocessing.get_context("spawn")


def _worker_count() -> int:
    """Resolve the worker-process count.

    ``OPENTRACES_CAPSULE_REDACT_WORKERS`` overrides autodetection: ``0`` or
    ``1`` forces the serial path (useful for tests/CI that want deterministic
    single-process timing), any other positive integer pins the pool size.
    Unset autodetects ``min(cpu_count, 8)`` — the issue's own bench measured
    the 4.8x win at 8 workers; more workers past cpu_count only adds
    scheduling overhead on a CPU-bound (not I/O-bound) workload.
    """
    override = os.environ.get("OPENTRACES_CAPSULE_REDACT_WORKERS")
    if override is not None:
        try:
            return max(0, int(override))
        except ValueError:
            return 0
    cpu = os.cpu_count() or 1
    return max(1, min(cpu, _DEFAULT_MAX_WORKERS))


def _chunk_lines_by_size(lines: Sequence[str], *, target_chunk_size: int) -> list[list[str]]:
    """Partition ``lines`` (order preserved) into size-balanced chunks.

    Chunked by cumulative character length, NOT line count — the issue notes
    "some lines are huge": a fixed-line-count split would put a handful of
    tiny lines plus one multi-MB line in the same bucket as N-1 buckets of
    tiny lines, starving every other worker. A greedy running-total split
    means one oversized line simply becomes (most of) its own chunk instead
    of skewing a neighbor. Character length (not a UTF-8 byte re-encode) is
    used as the balancing proxy — exact byte-accuracy does not matter here,
    only "roughly equal work per worker", and re-encoding every line would be
    an extra full pass over content this large for no behavioral benefit.
    """
    chunks: list[list[str]] = []
    current: list[str] = []
    current_size = 0
    for line in lines:
        line_size = len(line)
        if current and current_size + line_size > target_chunk_size:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(line)
        current_size += line_size
    if current:
        chunks.append(current)
    return chunks


def _sanitize_lines(
    lines: Sequence[str],
    tools: Sequence[Any] | None,
) -> tuple[list[str], dict[str, Any]]:
    """Sanitize one ordered chunk of raw (possibly blank) JSONL lines.

    Shared verbatim by the serial path (one call, the whole companion as a
    single chunk) and every parallel worker (one call per byte-balanced
    chunk) — this is what makes the two paths byte-identical by construction
    rather than by convention: there is only one sanitization code path,
    chunking only changes how many times / where it runs.

    Module-level (not a closure) so it is picklable for
    ``ProcessPoolExecutor`` dispatch. Only ever called with ``tools=None``
    from a worker (see ``redact_companion_text``'s dispatch gate below) — the
    in-process serial call may still pass through actual tool instances,
    mirroring the pre-#209 behavior exactly.
    """
    from ...security.pipeline import sanitize_companion_dict

    tool_list: list[Any] | None = list(tools) if tools is not None else None

    def _sanitize(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        return (
            sanitize_companion_dict(payload, tools=tool_list)
            if tool_list is not None
            else sanitize_companion_dict(payload)
        )

    out_lines: list[str] = []
    tools_applied: list[str] = []
    by_tool: dict[str, int] = {}
    redactions = 0
    findings = 0
    home_scrubbed = 0
    floor_satisfied = True
    lines_seen = 0

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        lines_seen += 1
        try:
            obj = json.loads(stripped)
            is_json = True
        except ValueError:
            obj = None
            is_json = False

        # Fail closed: EVERY line's content is routed through the substrate
        # sanitizer before emission — a non-JSON line, a JSON scalar, or a JSON
        # array can each carry a planted secret, so none may pass through
        # verbatim. Dicts sanitize directly; anything else is wrapped so the
        # field-aware walker reaches its string leaves (secret detectors + the
        # tail-consuming path anonymizer), then unwrapped.
        try:
            if is_json and isinstance(obj, dict):
                redacted, manifest = _sanitize(obj)
                out_lines.append(_canonical_json(redacted))
            elif is_json:
                wrapped, manifest = _sanitize({"value": obj})
                out_lines.append(_canonical_json(wrapped.get("value")))
            else:
                wrapped, manifest = _sanitize({"value": stripped})
                out_lines.append(str(wrapped.get("value")))
        except Exception:  # noqa: BLE001 — a line we cannot sanitize is never leaked
            out_lines.append(_UNSANITIZABLE_PLACEHOLDER)
            floor_satisfied = False
            continue

        # Aggregate the per-line manifests into one chunk-level manifest.
        if not tools_applied:
            tools_applied = list(manifest.get("tools_applied") or [])
        redactions += int(manifest.get("redactions_applied", 0))
        findings += int(manifest.get("findings_total", 0))
        home_scrubbed += int(manifest.get("home_paths_scrubbed", 0))
        floor_satisfied = floor_satisfied and bool(manifest.get("floor_satisfied", False))
        for tool, count in (manifest.get("by_tool") or {}).items():
            by_tool[tool] = by_tool.get(tool, 0) + int(count)

    return out_lines, {
        "lines": lines_seen,
        "tools_applied": tools_applied,
        "floor_satisfied": floor_satisfied,
        "redactions_applied": redactions,
        "findings_total": findings,
        "home_paths_scrubbed": home_scrubbed,
        "by_tool": by_tool,
    }


def _run_chunks_parallel(
    chunks: list[list[str]],
    workers: int,
) -> list[tuple[list[str], dict[str, Any]]]:
    """Dispatch ``chunks`` across a process pool. Always ``tools=None`` per
    chunk (see ``redact_companion_text``): the only path ever parallelized is
    the default-registry path, so there is nothing to reduce/reconstruct
    across the process boundary — each worker just runs ``COMPANION_FLOOR``.
    """
    from concurrent.futures import ProcessPoolExecutor

    max_workers = max(1, min(workers, len(chunks)))
    with ProcessPoolExecutor(max_workers=max_workers, mp_context=_MP_CONTEXT) as pool:
        # ``map`` yields results in the SAME order as ``chunks`` regardless of
        # completion order — this ordered-reassembly guarantee is what keeps
        # concatenation below identical to a single-process pass, even though
        # workers may finish in any order.
        return list(pool.map(_sanitize_lines, chunks, repeat(None)))


def _empty_manifest(security_version: str | None) -> dict[str, Any]:
    return {
        "schema_version": COMPANION_REDACTION_SCHEMA_VERSION,
        "security_version": security_version,
        "lines": 0,
        "tools_applied": [],
        "floor_satisfied": True,
        "redactions_applied": 0,
        "findings_total": 0,
        "home_paths_scrubbed": 0,
        "by_tool": {},
    }


def redact_companion_text(
    text: str,
    *,
    tools: Sequence[Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Redact a decompressed JSONL companion body line-by-line.

    Each non-empty line is a JSON object (a TrailEvent / ContextNode / source
    row); it is sanitized via ``sanitize_companion_dict`` and re-emitted as
    canonical JSON so two seals of the same content are byte-identical. Returns
    the redacted body + an aggregate counts+types manifest (never a literal
    secret — the substrate manifest is already ``matched_text``-free).

    Large companions (>= 4 MB) on the DEFAULT registry path (``tools=None``)
    are chunked by size and sanitized across a process pool (#209 W1); every
    other case — small companions, or ANY explicit ``tools`` argument — runs
    the identical per-line logic in-process with no pool overhead. An
    explicit ``tools`` argument never parallelizes (external review, PR #220
    critical 1): a bound tool instance cannot be safely handed to a worker
    process without losing its injected state, so that case always takes the
    always-correct serial path, matching pre-#209 behavior exactly. See the
    module docstring for why this stays byte-identical either way.
    """

    # Lazy import keeps the module leaf-light and avoids importing the whole
    # security pipeline at capsule import time.
    from ...security import SECURITY_VERSION
    from ...security.pipeline import COMPANION_FLOOR

    tool_list: list[Any] | None = list(tools) if tools is not None else None

    raw_lines = text.split("\n")

    workers = _worker_count()
    use_parallel = (
        tool_list is None
        and workers >= 2
        and len(text) >= _PARALLEL_THRESHOLD_BYTES
    )
    chunks: list[list[str]] | None = None

    if use_parallel:
        num_chunks = max(1, workers * _CHUNKS_PER_WORKER)
        target_chunk_size = max(1, len(text) // num_chunks)
        chunks = _chunk_lines_by_size(raw_lines, target_chunk_size=target_chunk_size)
        if len(chunks) <= 1:
            use_parallel = False

    if use_parallel and chunks is not None:
        partials = _run_chunks_parallel(chunks, workers)
    else:
        partials = [_sanitize_lines(raw_lines, tool_list)]

    out_lines: list[str] = []
    tools_applied: list[str] = []
    by_tool: dict[str, int] = {}
    redactions = 0
    findings = 0
    home_scrubbed = 0
    floor_satisfied = True
    lines = 0

    for chunk_lines, chunk_manifest in partials:
        out_lines.extend(chunk_lines)
        lines += int(chunk_manifest.get("lines", 0))
        if not tools_applied:
            tools_applied = list(chunk_manifest.get("tools_applied") or [])
        redactions += int(chunk_manifest.get("redactions_applied", 0))
        findings += int(chunk_manifest.get("findings_total", 0))
        home_scrubbed += int(chunk_manifest.get("home_paths_scrubbed", 0))
        floor_satisfied = floor_satisfied and bool(chunk_manifest.get("floor_satisfied", False))
        for tool, count in (chunk_manifest.get("by_tool") or {}).items():
            by_tool[tool] = by_tool.get(tool, 0) + int(count)

    body = ("\n".join(out_lines) + "\n") if out_lines else ""
    if lines == 0:
        # An empty companion never runs the walker; still declare the floor set.
        tools_applied = list(COMPANION_FLOOR) if tool_list is None else []
        floor_satisfied = tool_list is None or set(COMPANION_FLOOR).issubset(
            {getattr(t, "name", t) for t in (tool_list or [])}
        )
    manifest = {
        "schema_version": COMPANION_REDACTION_SCHEMA_VERSION,
        "security_version": SECURITY_VERSION,
        "lines": lines,
        "tools_applied": tools_applied,
        "floor_satisfied": floor_satisfied,
        "redactions_applied": redactions,
        "findings_total": findings,
        "home_paths_scrubbed": home_scrubbed,
        "by_tool": by_tool,
    }
    return body, manifest


def redact_companions(
    raw_gz: bytes | None,
    *,
    tools: Sequence[Any] | None = None,
    security_version: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Decompress, redact, re-gzip one raw companion. Returns ``(gz, manifest)``.

    ``raw_gz`` is the gzipped JSONL bytes as stored on the bucket layout (or
    ``None`` / empty for a missing companion). Output gzip is deterministic
    (``mtime=0``) so the same redacted content is byte-identical across machines.
    An empty companion round-trips to an empty companion (byte-stable).
    """

    if not raw_gz:
        from ...security import SECURITY_VERSION

        return b"", _empty_manifest(security_version or SECURITY_VERSION)

    text = gzip.decompress(raw_gz).decode("utf-8")
    body, manifest = redact_companion_text(text, tools=tools)
    if security_version is not None:
        manifest["security_version"] = security_version
    return _gzip_deterministic(body.encode("utf-8")), manifest


__all__ = [
    "COMPANION_REDACTION_SCHEMA_VERSION",
    "redact_companion_text",
    "redact_companions",
]
