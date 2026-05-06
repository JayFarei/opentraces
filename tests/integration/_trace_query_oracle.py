"""Crawl-vs-query oracle for the Plan-59 parity harness.

The harness's load-bearing claim is: every ``ot trace query`` filter returns
exactly the set of trace ids that a from-scratch JSONL crawl would match,
runs at least 10× faster, and emits no more JSON bytes than the crawl. This
module is the bash-oracle half — pure Python so the predicate stays
file-local and survives in CI without any installed CLI binary.

Usage from a test file:

    from _trace_query_oracle import (
        FilterCase, load_oracle, run_indexed_query, run_oracle,
    )

The oracle reads every JSONL under ``paths.PROJECTS_DIR/<slug>/traces/``
and ``paths.STAGING_DIR``. It excludes test-fixture project slugs that
match ``ot-h-*`` (legacy harness convention) so a developer running the
suite locally does not measure parity against unrelated trace shards.
"""

from __future__ import annotations

import io
import json
import time
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from opentraces.core import paths
from opentraces.core.search_projection import (
    build_search_projection,
    query_search_projection_page,
)
from opentraces.core.trace_index import (
    query_index_page,
    rebuild_index,
)


# ---------------------------------------------------------------------------
# FilterCase: declarative parity contract per filter.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilterCase:
    name: str
    cli_args: list[str]
    crawl_predicate: Callable[[dict], bool]
    expected_status: str = "pass"
    xfail_reason: str | None = None
    # Optional invariants for diagnostics. The harness asserts the oracle
    # produces a non-empty set (so a passing FilterCase actually exercises
    # the index) — set ``allow_empty=True`` for cases where zero is the
    # correct answer (e.g. invalid-vocabulary smoke tests).
    allow_empty: bool = False
    invocation: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Oracle loader.
# ---------------------------------------------------------------------------


_ORACLE_CACHE: dict[str, Any] | None = None
_ORACLE_CACHE_KEY: str | None = None


def _oracle_cache_key() -> str:
    parts: list[str] = []
    projects_root = paths.PROJECTS_DIR
    if projects_root.exists():
        for project_home in sorted(p for p in projects_root.iterdir() if p.is_dir()):
            slug = project_home.name
            if slug.startswith("ot-h-"):
                continue
            traces_dir = project_home / "traces"
            if not traces_dir.exists():
                continue
            for path in sorted(traces_dir.glob("*.jsonl")):
                stat = path.stat()
                parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
    staging_root = getattr(paths, "STAGING_DIR", None)
    if staging_root and staging_root.exists():
        for path in sorted(staging_root.glob("*.jsonl")):
            stat = path.stat()
            parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(parts)


def load_oracle(*, force: bool = False) -> dict[str, dict]:
    """Return ``{trace_id: trace_dict}`` covering every JSONL on disk.

    Cached per-(content-key) within a single process so the matrix can
    parametrize over many FilterCases without reparsing JSONL each time.
    """

    global _ORACLE_CACHE, _ORACLE_CACHE_KEY
    key = _oracle_cache_key()
    if not force and _ORACLE_CACHE is not None and _ORACLE_CACHE_KEY == key:
        return _ORACLE_CACHE
    traces: dict[str, dict] = {}
    projects_root = paths.PROJECTS_DIR
    if projects_root.exists():
        for project_home in sorted(p for p in projects_root.iterdir() if p.is_dir()):
            slug = project_home.name
            if slug.startswith("ot-h-"):
                continue
            traces_dir = project_home / "traces"
            if not traces_dir.exists():
                continue
            for path in sorted(traces_dir.glob("*.jsonl")):
                _ingest_jsonl(path, traces, layer="canonical", project_slug=slug)
    staging_root = getattr(paths, "STAGING_DIR", None)
    if staging_root and staging_root.exists():
        for path in sorted(staging_root.glob("*.jsonl")):
            _ingest_jsonl(path, traces, layer="staging", project_slug="_staging")
    _ORACLE_CACHE = traces
    _ORACLE_CACHE_KEY = key
    return traces


def _ingest_jsonl(
    path: Path,
    traces: dict[str, dict],
    *,
    layer: str,
    project_slug: str,
) -> None:
    try:
        text = path.read_text()
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        trace_id = obj.get("trace_id")
        if not trace_id:
            continue
        obj["__source_layer"] = layer
        obj["__project_slug"] = project_slug
        obj["__trace_path"] = str(path)
        traces[trace_id] = obj


# ---------------------------------------------------------------------------
# Indexed and oracle runners.
# ---------------------------------------------------------------------------


def _kwargs_from_cli(cli_args: list[str]) -> dict[str, Any]:
    """Map ``["--skill", "agent-browser"]`` → ``query_index_page`` kwargs.

    Handles the subset of flags exercised by the parity matrix. Anything
    not in the map is folded into ``facet_filters`` as ``--facet name=val``
    so the matrix can still introduce new filters without modifying the
    runner.
    """

    OPTIONS = {
        "--lex": ("lex", "value"),
        "--semantic": ("semantic", "value"),
        "--skill": ("skill", "value"),
        "--tool": ("tool", "value"),
        "--files": ("files", "value"),
        "--file-kind": ("file_kind", "value"),
        "--file-op": ("file_op", "value"),
        "--signal": ("signal", "value"),
        "--provider": ("provider", "value"),
        "--cmd-family": ("cmd_family", "value"),
        "--bash-action": ("bash_action", "value"),
        "--test": ("test_framework", "value"),
        "--service": ("service", "value"),
        "--service-channel": ("service_channel", "value"),
        "--dependency": ("dependency", "value"),
        "--git-tier": ("git_tier", "value"),
        "--survival": ("survival", "value"),
        "--since": ("since", "value"),
        "--candidate-kind": ("candidate_kind", "value"),
        "--project": ("project", "value"),
        "--page-token": ("page_token", "value"),
        "--limit": ("limit", "int"),
    }
    BOOL_FLAGS = {
        "--success": ("success", True),
        "--no-success": ("success", False),
        "--unknown-success": ("success_unknown", True),
        "--committed": ("committed", True),
        "--uncommitted": ("committed", False),
        "--unknown-committed": ("committed_unknown", True),
        "--latest-generation": ("latest_generation", True),
        "--include-superseded": ("latest_generation", False),
    }
    kwargs: dict[str, Any] = {"limit": 10000, "latest_generation": True}
    facet_filters: list[str] = []
    metadata_filters: list[str] = []
    i = 0
    while i < len(cli_args):
        arg = cli_args[i]
        if arg in OPTIONS:
            key, kind = OPTIONS[arg]
            value = cli_args[i + 1]
            kwargs[key] = int(value) if kind == "int" else value
            i += 2
            continue
        if arg in BOOL_FLAGS:
            key, value = BOOL_FLAGS[arg]
            kwargs[key] = value
            i += 1
            continue
        if arg == "--facet":
            facet_filters.append(cli_args[i + 1])
            i += 2
            continue
        if arg == "--metadata":
            metadata_filters.append(cli_args[i + 1])
            i += 2
            continue
        raise ValueError(f"Unknown CLI argument in FilterCase: {arg!r}")
    if facet_filters:
        kwargs["facet_filters"] = tuple(facet_filters)
    if metadata_filters:
        kwargs["metadata_filters"] = tuple(metadata_filters)
    return kwargs


def run_indexed_query(cli_args: list[str]) -> tuple[set[str], int, float]:
    """Return ``(trace_ids, json_bytes, latency_ms)``.

    Uses ``query_index_page`` directly to avoid Click runtime overhead
    contaminating the speed measurement. Issues the entire result set in
    one page (``limit=10000``) since the matrix asserts on full sets.
    """

    kwargs = _kwargs_from_cli(cli_args)
    start = time.perf_counter()
    page = query_index_page(**kwargs)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    trace_ids = {packet.trace_id for packet in page.candidates}
    # Equivalent-shape payload to the oracle (R6): one ``{trace_id}``
    # row per match. The CandidatePacket carries richer information for
    # interactive use; the parity contract only measures the trace-id
    # set's wire size.
    payload = {"candidates": [{"trace_id": tid} for tid in sorted(trace_ids)]}
    json_bytes = len(json.dumps(payload, sort_keys=True).encode("utf-8"))
    return trace_ids, json_bytes, elapsed_ms


def run_projected_query(cli_args: list[str]) -> tuple[set[str], int, float]:
    """Return ``(trace_ids, json_bytes, latency_ms)`` from the search projection."""

    kwargs = _kwargs_from_cli(cli_args)
    start = time.perf_counter()
    page = query_search_projection_page(**kwargs)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    trace_ids = {packet.trace_id for packet in page.candidates}
    payload = {"candidates": [{"trace_id": tid} for tid in sorted(trace_ids)]}
    json_bytes = len(json.dumps(payload, sort_keys=True).encode("utf-8"))
    return trace_ids, json_bytes, elapsed_ms


def run_oracle(
    predicate: Callable[[dict], bool],
    traces: dict[str, dict] | None = None,
) -> tuple[set[str], int, float]:
    """Return ``(trace_ids, json_bytes, latency_ms)`` from a JSONL crawl."""

    if traces is None:
        traces = load_oracle()
    start = time.perf_counter()
    matched: list[str] = []
    for trace_id, trace in traces.items():
        if predicate(trace):
            matched.append(trace_id)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    payload = {
        "candidates": [{"trace_id": tid} for tid in matched]
    }
    json_bytes = len(json.dumps(payload, sort_keys=True).encode("utf-8"))
    return set(matched), json_bytes, elapsed_ms


def ensure_index_built() -> None:
    """Force-rebuild the index ahead of any case execution.

    The parity assertions assume the index is in sync with the on-disk
    JSONL. ``query_index_page`` performs incremental refresh per call but
    a fresh rebuild guarantees a clean baseline before timing.
    """

    buf = io.StringIO()
    with redirect_stdout(buf):
        rebuild_index()


def ensure_search_projection_built() -> None:
    """Force-rebuild the index and bucket-shaped search projection."""

    buf = io.StringIO()
    with redirect_stdout(buf):
        summary = rebuild_index()
    build_search_projection(index_path=summary.index_path)


# ---------------------------------------------------------------------------
# Predicate helpers (mirror the indexer's facet derivations).
# ---------------------------------------------------------------------------


def _trace_skills(trace: dict) -> set[str]:
    skills: set[str] = set()
    for step in trace.get("steps", []):
        for call in step.get("tool_calls", []):
            if "skill" not in str(call.get("tool_name", "")).lower():
                continue
            value = (call.get("input") or {}).get("name") or (call.get("input") or {}).get("skill")
            if value:
                skills.add(str(value))
    return skills


def _trace_tools(trace: dict) -> set[str]:
    return {
        str(call.get("tool_name") or "")
        for step in trace.get("steps", [])
        for call in step.get("tool_calls", [])
        if call.get("tool_name")
    }


def _trace_files(trace: dict) -> set[str]:
    """Mirror :func:`opentraces.core.trace_map._files_for_tool`.

    Read-shaped tools (``Read``, ``View``, ``Glob``, ``Grep``) read from
    keys ``("file", "path")``. Write-shaped tools (``Edit``, ``Write``,
    ``MultiEdit``) read from ``("file_path", "path")``. Other tools do
    not contribute file entries to the trace map.
    """

    files: set[str] = set()
    for step in trace.get("steps", []):
        for call in step.get("tool_calls", []):
            tool = str(call.get("tool_name") or "")
            normalized = tool.lower().replace("_", "").replace("-", "")
            inp = call.get("input") or {}
            if normalized in {"read", "view", "glob", "grep"}:
                keys = ("file", "path")
            elif normalized in {"edit", "write", "multiedit"}:
                keys = ("file_path", "path")
            else:
                continue
            for key in keys:
                value = inp.get(key)
                if isinstance(value, str) and value:
                    files.add(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str) and item:
                            files.add(item)
    return files


def _outcome_success(trace: dict) -> bool | None:
    return (trace.get("outcome") or {}).get("success")


def _outcome_committed(trace: dict) -> bool | None:
    raw = (trace.get("outcome") or {}).get("committed")
    if isinstance(raw, bool):
        return raw
    return None


def _git_tiers(trace: dict) -> set[str]:
    return {
        str(link.get("tier"))
        for link in (trace.get("git_links") or [])
        if link.get("tier")
    }


def _provider_kind(trace: dict) -> str | None:
    agent = trace.get("agent") or {}
    model = agent.get("model") or ""
    if "/" in model:
        return model.split("/", 1)[0]
    name = agent.get("name") or ""
    if name:
        return str(name).split("-", 1)[0]
    return None


def _layer(trace: dict) -> str:
    return str(trace.get("__source_layer") or "canonical")


def _project_slug(trace: dict) -> str:
    return str(trace.get("__project_slug") or "_staging")


def _has_bash_command_matching(trace: dict, predicate: Callable[[str], bool]) -> bool:
    for step in trace.get("steps", []):
        for call in step.get("tool_calls", []):
            tool = str(call.get("tool_name") or "").lower()
            if tool not in {"bash", "shell"}:
                continue
            command = str((call.get("input") or {}).get("command") or "")
            if command and predicate(command):
                return True
    return False


def _superseded_unit(trace: dict) -> bool:
    md = trace.get("metadata") or {}
    return bool(md.get("superseded_by"))


__all__ = [
    "FilterCase",
    "load_oracle",
    "run_indexed_query",
    "run_projected_query",
    "run_oracle",
    "ensure_index_built",
    "ensure_search_projection_built",
    "_trace_skills",
    "_trace_tools",
    "_trace_files",
    "_outcome_success",
    "_outcome_committed",
    "_git_tiers",
    "_provider_kind",
    "_layer",
    "_project_slug",
    "_has_bash_command_matching",
    "_superseded_unit",
]
