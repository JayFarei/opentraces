"""CLI ``ctx`` group: navigate the Context Tree substrate.

Eleven verbs (``tree``, ``show``, ``step``, ``reads``, ``writes``, ``diff``,
``compactions``, ``prune``, ``resume``, ``resolve``, ``anchor-for-step``)
mirroring the ``cli/trace.py`` and ``cli/trail.py`` shape: Click group,
``--json`` toggle, ``--project`` path option, short text default,
schema-version-stamped JSON envelopes.

The CLI is the navigation surface for what the LLM saw at every step
of an agent session, sitting alongside ``opentraces trace`` (what the
agent did) and ``opentraces trail`` (what changed in the world). See
``kb/plans/077-context-tree-substrate.md`` §"CLI surface" for the
contract that downstream consumers (replay, RL, viewer, dashboards)
drive through these envelopes without us building them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from ._help import OpentracesCommand, OpentracesGroup
from ._options import dump_json as _dump_json, project_dir_option
from ..core.context_tree.contract import (
    CONTEXT_READS_SCHEMA_VERSION,
    CONTEXT_RESOLVE_SCHEMA_VERSION,
    CONTEXT_RESUME_SCHEMA_VERSION,
    CONTEXT_TREE_SCHEMA_VERSION,
    CONTEXT_WRITES_SCHEMA_VERSION,
)

# Soft import for Agent F's resume / prune helpers. The CLI wires the
# verbs regardless so the surface is testable in isolation; the wrapper
# returns a structured empty-state envelope if the helper isn't yet
# present in the working tree.
try:  # pragma: no cover - exercised via integration journeys
    from ..core.context_tree.resume import context_resume_packet as _resume_packet
except ImportError:  # pragma: no cover - fallback path
    _resume_packet = None  # type: ignore[assignment]

try:  # pragma: no cover
    from ..core.context_tree.prune import prune_session_to_node as _prune_session
except ImportError:  # pragma: no cover
    _prune_session = None  # type: ignore[assignment]

# Default text-output budget for ``ctx show`` summary mode. Hardcoded in
# v1; downstream can opt into unbounded content via ``--full``.
DEFAULT_SHOW_BUDGET_BYTES = 4096


# --------------------------------------------------------------------------- #
# Group + tiny helpers
# --------------------------------------------------------------------------- #


@click.group("ctx", cls=OpentracesGroup)
def ctx_group() -> None:
    """Navigate the Context Tree: what the LLM saw at each step."""


def _project_repo(project_dir: Path | None) -> Path:
    """Resolve the project directory for query operations (default CWD)."""
    return Path(project_dir or Path.cwd()).resolve()


def _emit(payload: dict[str, Any]) -> None:
    """Stable JSON envelope emitter (mirrors cli/trace.py style)."""
    click.echo(_dump_json(payload))


def _empty_state(schema_version: str, reason: str, **extras: Any) -> dict[str, Any]:
    """Return a standard empty-state envelope (rc=0, never raises)."""
    payload: dict[str, Any] = {
        "schema_version": schema_version,
        "limitations": [reason],
    }
    payload.update(extras)
    return payload


def _missing_resource(message: str) -> None:
    """rc=3: requested resource (trace, node, layer) doesn't exist."""
    click.echo(message, err=True)
    sys.exit(3)


def _summarize_layer(layer: Any) -> dict[str, Any]:
    """Per-layer summary used by ``ctx show`` without ``--full``."""
    if layer is None:
        return {"layer_id": None, "completeness": None, "byte_count": 0}
    content_bytes = len(json.dumps(layer.content, separators=(",", ":")))
    return {
        "layer_id": layer.layer_id,
        "layer_type": layer.layer_type,
        "completeness": layer.completeness,
        "capture_method": layer.capture_method,
        "byte_count": content_bytes,
    }


def _truncate_for_text(text: str, budget: int = DEFAULT_SHOW_BUDGET_BYTES) -> str:
    if len(text) <= budget:
        return text
    return text[:budget] + "\n... (use --full for the full content)"


# --------------------------------------------------------------------------- #
# ctx list / ctx info (plan 080 — replaces removed bucket context-tree subgroup)
# --------------------------------------------------------------------------- #


# Manifest v2 schema_version (plan 080 §4) used for the ctx list/info envelopes
# so downstream consumers can lock against a stable shape.
_CTX_LIST_SCHEMA_VERSION = "opentraces.ctx.list.v2"
_CTX_INFO_SCHEMA_VERSION = "opentraces.ctx.info.v2"


def _read_bucket_manifest_no_blobs() -> dict[str, Any]:
    """Read the bucket manifest without triggering any blob loads.

    Per plan 080 §7 + the bench-ctx-list-10k-traces perf gate, ``ctx
    list`` must NOT open any layer blob file. This helper only reads
    ``manifest.json`` directly — never resolves layer refs or per-trace
    JSONL.
    """
    from ..core.bucket_store import bucket_manifest_path

    path = bucket_manifest_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


class _BackendUnavailable(RuntimeError):
    """Raised when D1's bucket_backend module isn't available yet."""


def _read_bucket_manifest_via_backend(remote: str) -> dict[str, Any]:
    """Read ``manifest.json`` through the D1 backend abstraction.

    Plan 080 §7 — ``ctx list`` and ``ctx info`` gain ``--remote
    <hf-repo>`` and route their manifest read through the backend so
    local and remote shapes are bytewise-equal.
    """
    try:
        backend = _cached_backend(remote)
    except ImportError as exc:
        raise _BackendUnavailable(
            f"--remote requires the bucket backend module (in flight as Track D1): {exc}"
        ) from exc
    payload = backend.get_manifest()
    return payload if isinstance(payload, dict) else {}


# Backend instances are cached per-`remote` per process: a single ``ctx
# show --remote`` resolves multiple layer blobs, and each fresh
# ``get_backend`` call would otherwise produce a new RemoteHubBackend
# with an empty cache, re-downloading ``manifest.json`` for every blob.
_BACKEND_CACHE: dict[str, Any] = {}


def _cached_backend(remote: str) -> Any:
    cached = _BACKEND_CACHE.get(remote)
    if cached is not None:
        return cached
    from ..core.bucket_backend import get_backend

    backend = get_backend(remote)
    _BACKEND_CACHE[remote] = backend
    return backend


def _project_slug_for_node(node: Any, *, remote: str | None = None) -> str | None:
    """Resolve a node's ``project_slug``: direct attr, else manifest join.

    The node carries a ``trace_id``; the canonical trace -> project_slug
    join lives in the bucket manifest's ``traces[]``. When ``remote`` is
    set the manifest is read from the remote backend (the lazy-fetch
    caller already needs this); otherwise the local manifest is consulted.
    """
    project_slug = getattr(node, "project_slug", None)
    if project_slug:
        return project_slug
    trace_id = getattr(node, "trace_id", None)
    if not trace_id:
        return None
    manifest: dict[str, Any] = {}
    try:
        if remote:
            manifest = _cached_backend(remote).get_manifest()
        else:
            from ..core.bucket_backend import LocalBucketBackend

            manifest = LocalBucketBackend().get_manifest()
    except Exception:
        return None
    for entry in manifest.get("traces") or []:
        if isinstance(entry, dict) and entry.get("trace_id") == trace_id:
            return entry.get("project_slug")
    return None


def _cache_read_layer_blob(*, node: Any, layer_id: str) -> Any:
    """Read a layer blob from the LOCAL bucket cache (no network).

    Plan 080 §6/§7 — ``ctx show`` resolution order is
    projection(local) -> bucket blob cache(cache) -> remote(remote). This
    is the middle rung: a blob the projection didn't carry but that a
    prior ``--remote`` fetch (or ``bucket prefetch``) wrote to
    ``bucket/blobs/v1/<project>/context/<hh>/<hash>.json.gz``. Returns a
    rehydrated ``ContextLayer`` on a cache hit, else ``None``. Issue #57:
    without this rung the remote->cache and offline-after-warm arms are
    unreachable (the cache that ``_lazy_fetch_layer_blob`` writes was
    never read back).
    """
    project_slug = _project_slug_for_node(node)
    if not project_slug:
        return None
    try:
        from ..core.bucket_layout import blobs_v1_context_path

        blob_path = blobs_v1_context_path(project_slug, layer_id)
    except Exception:
        return None
    if not blob_path.exists():
        return None
    try:
        from ..core.bucket_backend import LocalBucketBackend

        payload = LocalBucketBackend().get_layer_blob(layer_id, project_slug)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        from ..core.context_tree.models import ContextLayer

        return ContextLayer.model_validate(payload)
    except Exception:
        return None


def _lazy_fetch_layer_blob(
    *,
    remote: str,
    node: Any,
    layer_id: str,
    layer_type: str,
) -> Any:
    """Lazy-fetch a single layer blob from the remote backend.

    Plan 080 §7 — ``ctx show --remote`` reaches here when the local
    projection didn't materialise a blob. The fetched blob is decoded
    into a ``ContextLayer`` and ALSO written to the local bucket cache
    at ``bucket/blobs/v1/<project>/context/<hh>/<hash>.json.gz`` so the
    next call (potentially ``--offline``) can be served from cache.
    """
    try:
        backend = _cached_backend(remote)
    except ImportError as exc:
        raise _BackendUnavailable(
            f"--remote requires the bucket backend module (in flight as Track D1): {exc}"
        ) from exc
    # The backend's get_layer_blob takes (layer_id, project_slug) per
    # the D1 contract. Resolve project_slug from the manifest using the
    # node's trace_id (the canonical join key).
    project_slug = getattr(node, "project_slug", None)
    if not project_slug:
        trace_id = getattr(node, "trace_id", None)
        if trace_id:
            try:
                manifest = backend.get_manifest()
            except Exception:
                manifest = {}
            for entry in manifest.get("traces") or []:
                if isinstance(entry, dict) and entry.get("trace_id") == trace_id:
                    project_slug = entry.get("project_slug")
                    break
    if not project_slug:
        return None
    try:
        payload = backend.get_layer_blob(layer_id, project_slug)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    # Persist the fetched blob into the local bucket cache so the next
    # ``ctx show --offline`` call hits the cache (plan 080 §6).
    try:
        from ..core.bucket_store import (
            _atomic_write_gzip,
            _canonical_json,
            blobs_v1_context_path,
        )

        local_blob = blobs_v1_context_path(project_slug, layer_id)
        if not local_blob.exists():
            data = _canonical_json(payload, pretty=False).encode("utf-8")
            _atomic_write_gzip(local_blob, data)
    except Exception:
        # Caching is best-effort; the in-memory ContextLayer is still
        # what the CLI verb renders, so failure here doesn't break the
        # current call.
        pass

    # Re-hydrate into a ContextLayer instance so _summarize_layer works.
    try:
        from ..core.context_tree.models import ContextLayer

        return ContextLayer.model_validate(payload)
    except Exception:
        return None


@ctx_group.command(
    "list",
    cls=OpentracesCommand,
    examples=[
        "opentraces ctx list",
        "opentraces ctx list --json",
        "opentraces ctx list --remote user/dataset",
    ],
    see_also=[
        ("opentraces ctx info", "show one trace's summary without blob loads."),
        ("opentraces bucket status", "broader bucket-level health."),
    ],
    option_groups=[
        ("Scope", ["remote"]),
        ("Output", ["as_json"]),
    ],
)
@click.option(
    "--remote",
    "remote",
    default=None,
    help=(
        "Read the manifest from a remote HF dataset bucket "
        "(plan 080 §7). Format: 'user/repo'."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def ctx_list_cmd(remote: str | None, as_json: bool) -> None:
    """List traces in the local bucket without loading any layer blobs.

    Per plan 080 §7, ``ctx list`` reads ``bucket/manifest.json`` only.
    It must NOT trigger blob fetches (gated by the
    ``bench-ctx-list-10k-traces`` performance test). Output is the
    manifest's ``traces[]`` array projected to a stable envelope.

    Pass ``--remote <hf-repo>`` to read the manifest from a remote
    HF dataset bucket instead. Output shape is bytewise-equal between
    local and remote reads (symmetric-access gate).
    """
    if remote:
        from ..core.bucket_remote import BucketRemoteError
        from ..core.bucket_store import BucketLayoutError

        try:
            manifest = _read_bucket_manifest_via_backend(remote)
        except _BackendUnavailable as exc:
            click.echo(str(exc), err=True)
            sys.exit(3)
        except (BucketRemoteError, BucketLayoutError) as exc:
            click.echo(f"Remote read failed: {exc}", err=True)
            sys.exit(3)
    else:
        manifest = _read_bucket_manifest_no_blobs()
    traces_in: list[dict[str, Any]] = list(manifest.get("traces") or [])
    rows: list[dict[str, Any]] = []
    for entry in traces_in:
        if not isinstance(entry, dict):
            continue
        summary = entry.get("summary") or {}
        rows.append(
            {
                "project_slug": entry.get("project_slug"),
                "trace_id": entry.get("trace_id"),
                "title": entry.get("title"),
                "summary": {
                    "step_count": summary.get("step_count", 0),
                    "patch_count": summary.get("patch_count", 0),
                    "anchored_count": summary.get("anchored_count", 0),
                    "node_count": summary.get("node_count", 0),
                    "capture_methods": list(summary.get("capture_methods") or []),
                },
                "lifecycle": entry.get("lifecycle"),
            }
        )

    payload = {
        "schema_version": _CTX_LIST_SCHEMA_VERSION,
        "traces": rows,
    }
    if as_json:
        _emit(payload)
        return
    if not rows:
        click.echo("No traces in local bucket.")
        return
    click.echo(f"Traces: {len(rows)}")
    for row in rows:
        s = row["summary"]
        title = row.get("title") or "-"
        click.echo(
            f"  {row.get('trace_id', '?'):42}  "
            f"steps={s['step_count']:>3}  "
            f"patches={s['patch_count']:>3}  "
            f"anchored={s['anchored_count']:>3}  "
            f"nodes={s['node_count']:>4}  "
            f"{row.get('lifecycle') or '-':12}  {title}"
        )


@ctx_group.command(
    "info",
    cls=OpentracesCommand,
    examples=[
        "opentraces ctx info <trace-id>",
        "opentraces ctx info <trace-id> --json",
        "opentraces ctx info <trace-id> --remote user/dataset",
    ],
    see_also=[
        ("opentraces ctx list", "list every trace in the bucket."),
        ("opentraces ctx tree", "walk the parentUuid-rooted Context Tree."),
    ],
    option_groups=[
        ("Scope", ["trace_id", "remote"]),
        ("Output", ["as_json"]),
    ],
)
@click.argument("trace_id")
@click.option(
    "--remote",
    "remote",
    default=None,
    help=(
        "Read the manifest from a remote HF dataset bucket "
        "(plan 080 §7). Format: 'user/repo'."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def ctx_info_cmd(trace_id: str, remote: str | None, as_json: bool) -> None:
    """Show one trace's summary from the local bucket without blob loads.

    Per plan 080 §7, ``ctx info <trace>`` reads ``bucket/manifest.json``
    first (cheap projection); falls back to the per-trace
    ``bucket/traces/v1/<project>/<trace>/trace.json`` when present.
    No blob loads.

    Pass ``--remote <hf-repo>`` to fetch the manifest from a remote HF
    dataset bucket instead. Output shape is bytewise-equal between
    local and remote reads (symmetric-access gate).
    """
    if remote:
        from ..core.bucket_remote import BucketRemoteError
        from ..core.bucket_store import BucketLayoutError

        try:
            manifest = _read_bucket_manifest_via_backend(remote)
        except _BackendUnavailable as exc:
            click.echo(str(exc), err=True)
            sys.exit(3)
        except (BucketRemoteError, BucketLayoutError) as exc:
            click.echo(f"Remote read failed: {exc}", err=True)
            sys.exit(3)
    else:
        manifest = _read_bucket_manifest_no_blobs()
    matched: dict[str, Any] | None = None
    for entry in manifest.get("traces") or []:
        if isinstance(entry, dict) and entry.get("trace_id") == trace_id:
            matched = entry
            break

    if matched is None:
        if as_json:
            _emit(
                _empty_state(
                    _CTX_INFO_SCHEMA_VERSION,
                    "trace_not_found_in_bucket",
                    trace_id=trace_id,
                )
            )
            sys.exit(3)
        _missing_resource(f"trace {trace_id!r} not found in bucket")

    summary = matched.get("summary") or {}
    info = {
        "project_slug": matched.get("project_slug"),
        "trace_id": matched.get("trace_id"),
        "title": matched.get("title"),
        "lifecycle": matched.get("lifecycle"),
        "trace_path": matched.get("trace_path"),
        "files": matched.get("files") or {},
        "summary": {
            "step_count": summary.get("step_count", 0),
            "patch_count": summary.get("patch_count", 0),
            "anchored_count": summary.get("anchored_count", 0),
            "node_count": summary.get("node_count", 0),
            "capture_methods": list(summary.get("capture_methods") or []),
        },
        "remote_sync_eligible": matched.get("remote_sync_eligible"),
        "digest": matched.get("digest"),
    }
    payload = {
        "schema_version": _CTX_INFO_SCHEMA_VERSION,
        "info": info,
    }
    if as_json:
        _emit(payload)
        return
    s = info["summary"]
    click.echo(f"trace_id:        {info['trace_id']}")
    click.echo(f"project_slug:    {info['project_slug']}")
    click.echo(f"title:           {info.get('title') or '-'}")
    click.echo(f"lifecycle:       {info.get('lifecycle') or '-'}")
    click.echo(f"step_count:      {s['step_count']}")
    click.echo(f"patch_count:     {s['patch_count']}")
    click.echo(f"anchored_count:  {s['anchored_count']}")
    click.echo(f"node_count:      {s['node_count']}")
    click.echo(f"capture_methods: {', '.join(s['capture_methods']) or '-'}")
    if info.get("trace_path"):
        click.echo(f"trace_path:      {info['trace_path']}")


# --------------------------------------------------------------------------- #
# ctx tree
# --------------------------------------------------------------------------- #


@ctx_group.command(
    "tree",
    cls=OpentracesCommand,
    examples=[
        "opentraces ctx tree <trace-id>",
        "opentraces ctx tree <trace-id> --show-orphans --json",
    ],
    see_also=[
        ("opentraces ctx show", "drill into a single ContextNode."),
        ("opentraces ctx step", "look up the ContextNode for a step index."),
    ],
    option_groups=[
        ("Scope", ["trace_id", "project_dir"]),
        ("Output", ["leaf", "depth", "show_orphans", "as_json"]),
    ],
)
@click.argument("trace_id")
@click.option("--leaf", "leaf", default=None, help="Walk back from a specific leaf node id.")
@click.option("--depth", type=int, default=None, help="Cap the displayed depth (default: full path).")
@click.option("--show-orphans", "show_orphans", is_flag=True, help="Include orphan rewind subtrees.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@project_dir_option
def ctx_tree_cmd(
    trace_id: str,
    leaf: str | None,
    depth: int | None,
    show_orphans: bool,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Print the parentUuid-rooted Context Tree for a trace."""
    from ..core.context_tree.query import build_context_tree_projection

    repo = _project_repo(project_dir)
    projection = build_context_tree_projection(repo)
    node_ids = projection.nodes_by_trace.get(trace_id, [])
    if not node_ids:
        payload = _empty_state(
            CONTEXT_TREE_SCHEMA_VERSION,
            "context_tree_not_captured",
            trace_id=trace_id,
            nodes=[],
            orphan_branch_roots=[],
            subagent_session_ids=[],
            root_node_id=None,
            active_leaf_id=None,
        )
        if as_json:
            _emit(payload)
        else:
            click.echo(f"No Context Tree projected for trace {trace_id}")
            click.echo("  limitation: context_tree_not_captured")
        return

    active_path = projection.active_path(trace_id, leaf_id=leaf)
    if depth is not None and depth >= 0:
        active_path = active_path[: depth + 1]
    root_id = active_path[0].node_id if active_path else None
    leaf_id = active_path[-1].node_id if active_path else None
    orphan_roots = projection.orphan_branch_roots_by_trace.get(trace_id, [])
    subagent_ids = projection.subagent_session_ids_by_trace.get(trace_id, [])

    nodes_payload = [
        {
            "node_id": n.node_id,
            "parent_node_id": n.parent_node_id,
            "branch_type": n.branch_type,
            "step_index": n.step_index,
            "transcript_uuid": n.transcript_uuid,
        }
        for n in active_path
    ]
    if show_orphans:
        for orphan_root in orphan_roots:
            for n in projection.orphan_subtree(orphan_root):
                nodes_payload.append(
                    {
                        "node_id": n.node_id,
                        "parent_node_id": n.parent_node_id,
                        "branch_type": n.branch_type,
                        "step_index": n.step_index,
                        "transcript_uuid": n.transcript_uuid,
                        "orphan": True,
                    }
                )

    payload = {
        "schema_version": CONTEXT_TREE_SCHEMA_VERSION,
        "trace_id": trace_id,
        "root_node_id": root_id,
        "active_leaf_id": leaf_id,
        "nodes": nodes_payload,
        "orphan_branch_roots": orphan_roots,
        "subagent_session_ids": subagent_ids,
    }
    if as_json:
        _emit(payload)
        return
    # Indented text rendering of the active path
    short = lambda nid: (nid[:18] + "…") if nid and len(nid) > 18 else (nid or "-")
    for depth_i, node in enumerate(active_path):
        prefix = "  " * depth_i
        step = f"step={node.step_index}" if node.step_index is not None else "step=-"
        click.echo(f"{prefix}{short(node.node_id)}  {node.branch_type:14}  {step}")
    if show_orphans and orphan_roots:
        click.echo("Orphan branches:")
        for orphan_root in orphan_roots:
            for n in projection.orphan_subtree(orphan_root):
                click.echo(f"  ~ {short(n.node_id)}  {n.branch_type}")


# --------------------------------------------------------------------------- #
# ctx show
# --------------------------------------------------------------------------- #


@ctx_group.command(
    "show",
    cls=OpentracesCommand,
    examples=[
        "opentraces ctx show <node-id>",
        "opentraces ctx show <node-id> --full --json",
        "opentraces ctx show <node-id> --remote user/dataset",
        "opentraces ctx show <node-id> --offline",
    ],
    see_also=[
        ("opentraces ctx step", "alias: show ContextNode for a step index."),
        ("opentraces ctx diff", "diff two ContextNodes layer-by-layer."),
    ],
    option_groups=[
        ("Scope", ["node_id", "project_dir", "remote", "offline"]),
        ("Output", ["layers", "full", "as_json"]),
    ],
)
@click.argument("node_id")
@click.option(
    "--layers",
    "layers_filter",
    default=None,
    help="Comma-separated layer types to include (default: all).",
)
@click.option("--full", "as_full", is_flag=True, help="Inline full layer content (default: summary).")
@click.option(
    "--remote",
    "remote",
    default=None,
    help=(
        "Lazy-fetch missing layer blobs from a remote HF dataset bucket "
        "(plan 080 §7). Format: 'user/repo'."
    ),
)
@click.option(
    "--offline",
    "offline",
    is_flag=True,
    help=(
        "Fail with rc=4 if a referenced layer blob isn't already local "
        "(plan 080 §7 offline semantics)."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@project_dir_option
def ctx_show_cmd(
    node_id: str,
    layers_filter: str | None,
    as_full: bool,
    remote: str | None,
    offline: bool,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Show a ContextNode and its layer references.

    Pass ``--remote <hf-repo>`` to lazy-fetch any missing layer blob
    from a remote HF dataset bucket. Pass ``--offline`` to fail
    fast (rc=4) when a referenced blob isn't already in the local
    bucket cache; useful for "prefetch first, then run offline"
    workflows. ``--remote`` + ``--offline`` together means "use the
    local cache only; do NOT contact the remote".
    """
    from ..core.context_tree.query import build_context_tree_projection

    repo = _project_repo(project_dir)
    projection = build_context_tree_projection(repo)
    node = projection.nodes_by_id.get(node_id)
    if node is None:
        if as_json:
            _emit(_empty_state(
                CONTEXT_TREE_SCHEMA_VERSION,
                "context_layer_unavailable",
                node_id=node_id,
            ))
            return
        _missing_resource(f"ContextNode not found: {node_id}")

    keep_types: set[str] | None = None
    if layers_filter:
        keep_types = {p.strip() for p in layers_filter.split(",") if p.strip()}

    layer_pairs = (
        ("system", node.system_layer_id),
        ("messages", node.messages_layer_id),
        ("tool_registry", node.tool_registry_layer_id),
        ("runtime_state", node.runtime_state_layer_id),
    )
    layers_out: dict[str, Any] = {}
    layer_provenance: dict[str, str] = {}
    for layer_type, layer_id in layer_pairs:
        if keep_types is not None and layer_type not in keep_types:
            continue
        layer = projection.layers_by_id.get(layer_id)
        # Plan 080 §7 — provenance: "local" if the projection already
        # has it, "cache" if we resolved it from the local bucket cache,
        # "remote" if we lazy-fetched from the remote backend.
        fetched_from = "local" if layer is not None else None
        # Plan 080 §6/§7 resolution order: projection(local) -> bucket
        # blob cache(cache) -> remote(remote). The cache rung sits BEFORE
        # the offline/remote branches so a warmed blob (from a prior
        # --remote fetch or `bucket prefetch`) is served offline (issue
        # #57: this is the rung that makes the offline-after-warm and
        # repeat-fetch-is-cache arms reachable).
        if layer is None and layer_id:
            cached = _cache_read_layer_blob(node=node, layer_id=layer_id)
            if cached is not None:
                layer = cached
                fetched_from = "cache"
        # Plan 080 §7 lazy-fetch path. With ``--remote`` we fetch
        # missing blobs from the remote. ``--offline`` is the HARD
        # opt-out: never contact the network, fail rc=4 with a clean
        # error envelope pointing at ``bucket prefetch``.
        if layer is None and layer_id:
            if offline:
                error_payload = {
                    "schema_version": CONTEXT_TREE_SCHEMA_VERSION,
                    "status": "error",
                    "node_id": node_id,
                    "error": {
                        "kind": "blob_missing_offline",
                        "missing_layer_id": layer_id,
                        "missing_layer_type": layer_type,
                        "hint": (
                            f"layer blob not in local cache; run "
                            f"'opentraces bucket prefetch {node.trace_id}' "
                            f"to warm the cache, or drop --offline to "
                            f"fetch from the remote."
                        ),
                    },
                }
                if as_json:
                    _emit(error_payload)
                else:
                    click.echo(
                        f"Offline: layer blob {layer_id} ({layer_type}) "
                        f"not in local bucket cache. Run "
                        f"'opentraces bucket prefetch {node.trace_id}' "
                        f"to warm the cache, or drop --offline.",
                        err=True,
                    )
                sys.exit(4)
            if remote:
                try:
                    layer = _lazy_fetch_layer_blob(
                        remote=remote,
                        node=node,
                        layer_id=layer_id,
                        layer_type=layer_type,
                    )
                except _BackendUnavailable as exc:
                    click.echo(str(exc), err=True)
                    sys.exit(3)
                if layer is not None:
                    fetched_from = "remote"
        entry = _summarize_layer(layer)
        if fetched_from is not None:
            entry["fetched_from"] = fetched_from
        if as_full and layer is not None:
            entry["content"] = layer.content
        layers_out[layer_type] = entry
        if fetched_from is not None:
            layer_provenance[layer_type] = fetched_from

    payload = {
        "schema_version": CONTEXT_TREE_SCHEMA_VERSION,
        "node": {
            "node_id": node.node_id,
            "parent_node_id": node.parent_node_id,
            "branch_type": node.branch_type,
            "trace_id": node.trace_id,
            "step_index": node.step_index,
            "transcript_uuid": node.transcript_uuid,
            "transcript_offset": node.transcript_offset,
            "capture_completeness": node.capture_completeness,
            "subagent_session_id": node.subagent_session_id,
            "trail_anchor_hint": node.trail_anchor_hint,
        },
        "layers": layers_out,
    }
    if as_json:
        _emit(payload)
        return

    click.echo(f"Node {node.node_id}")
    click.echo(f"  trace:           {node.trace_id}")
    click.echo(f"  step_index:      {node.step_index}")
    click.echo(f"  branch_type:     {node.branch_type}")
    click.echo(f"  parent:          {node.parent_node_id or '-'}")
    click.echo(f"  completeness:    {node.capture_completeness}")
    click.echo("Layers:")
    for layer_type, entry in layers_out.items():
        click.echo(
            f"  {layer_type:15}  {entry['completeness'] or '-':12}  "
            f"{entry['byte_count']:>8}B  {entry['layer_id'] or '-'}"
        )
        if as_full and "content" in entry:
            text = _dump_json(entry["content"])
            click.echo(text)
        elif "content" in entry:
            text = _dump_json(entry["content"])
            click.echo(_truncate_for_text(text))


# --------------------------------------------------------------------------- #
# ctx step
# --------------------------------------------------------------------------- #


@ctx_group.command(
    "step",
    cls=OpentracesCommand,
    examples=[
        "opentraces ctx step <trace-id> 7",
        "opentraces ctx step <trace-id> 7 --json",
    ],
    see_also=[
        ("opentraces ctx show", "show a ContextNode by id."),
        ("opentraces ctx tree", "walk the whole tree for a trace."),
    ],
    option_groups=[
        ("Scope", ["trace_id", "step_index", "project_dir"]),
        ("Output", ["as_json"]),
    ],
)
@click.argument("trace_id")
@click.argument("step_index", type=int)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@project_dir_option
def ctx_step_cmd(
    trace_id: str,
    step_index: int,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Look up the ContextNode for a given step index (alias for show)."""
    from ..core.context_tree.query import build_context_tree_projection

    repo = _project_repo(project_dir)
    projection = build_context_tree_projection(repo)
    node = projection.node_for_step(trace_id, step_index)
    if node is None:
        if as_json:
            _emit(_empty_state(
                CONTEXT_TREE_SCHEMA_VERSION,
                "context_layer_unavailable",
                trace_id=trace_id,
                step_index=step_index,
            ))
            return
        _missing_resource(
            f"No ContextNode for trace {trace_id} step {step_index}"
        )

    # Delegate to ctx_show_cmd's payload shape (without --full)
    layer_pairs = (
        ("system", node.system_layer_id),
        ("messages", node.messages_layer_id),
        ("tool_registry", node.tool_registry_layer_id),
        ("runtime_state", node.runtime_state_layer_id),
    )
    layers_out = {
        lt: _summarize_layer(projection.layers_by_id.get(lid))
        for lt, lid in layer_pairs
    }
    payload = {
        "schema_version": CONTEXT_TREE_SCHEMA_VERSION,
        "node_id": node.node_id,
        "node": {
            "node_id": node.node_id,
            "parent_node_id": node.parent_node_id,
            "branch_type": node.branch_type,
            "trace_id": node.trace_id,
            "step_index": node.step_index,
            "transcript_uuid": node.transcript_uuid,
            "transcript_offset": node.transcript_offset,
            "capture_completeness": node.capture_completeness,
            "subagent_session_id": node.subagent_session_id,
            "trail_anchor_hint": node.trail_anchor_hint,
        },
        "layers": layers_out,
    }
    if as_json:
        _emit(payload)
        return
    click.echo(f"trace={trace_id} step={step_index} node={node.node_id}")


# --------------------------------------------------------------------------- #
# ctx reads / writes
# --------------------------------------------------------------------------- #


def _io_range_options(func):
    func = click.option(
        "--step", "step", type=int, default=None, help="Exact step index (overrides --from/--to)."
    )(func)
    func = click.option(
        "--from-step", "from_step", type=int, default=None, help="First step index in range."
    )(func)
    func = click.option(
        "--to-step", "to_step", type=int, default=None, help="Last step index in range."
    )(func)
    return func


@ctx_group.command(
    "reads",
    cls=OpentracesCommand,
    examples=[
        "opentraces ctx reads <trace-id> --from-step 5 --to-step 9",
        "opentraces ctx reads <trace-id> --step 7 --json",
    ],
    see_also=[
        ("opentraces ctx writes", "list write-direction tool calls for the same range."),
    ],
    option_groups=[
        ("Scope", ["trace_id", "project_dir"]),
        ("Range", ["step", "from_step", "to_step"]),
        ("Output", ["as_json"]),
    ],
)
@click.argument("trace_id")
@_io_range_options
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@project_dir_option
def ctx_reads_cmd(
    trace_id: str,
    step: int | None,
    from_step: int | None,
    to_step: int | None,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """List tool_result-shaped reads (environment-to-agent) for a trace."""
    from ..core.context_tree.query import build_context_tree_projection

    repo = _project_repo(project_dir)
    projection = build_context_tree_projection(repo)
    if trace_id not in projection.nodes_by_trace:
        payload = _empty_state(
            CONTEXT_READS_SCHEMA_VERSION,
            "context_tree_not_captured",
            trace_id=trace_id,
            reads=[],
        )
        if as_json:
            _emit(payload)
            return
        click.echo(f"No Context Tree projected for trace {trace_id}")
        return

    if step is not None:
        from_step = step
        to_step = step
    reads = projection.reads_for_trace(trace_id, from_step=from_step, to_step=to_step)
    payload = {
        "schema_version": CONTEXT_READS_SCHEMA_VERSION,
        "trace_id": trace_id,
        "from_step": from_step,
        "to_step": to_step,
        "reads": reads,
    }
    if as_json:
        _emit(payload)
        return
    for entry in reads:
        click.echo(
            f"step={entry.get('step_index')}  uuid={entry.get('uuid')}  "
            f"role={entry.get('role')}  content_hash={entry.get('content_hash')}"
        )


@ctx_group.command(
    "writes",
    cls=OpentracesCommand,
    examples=[
        "opentraces ctx writes <trace-id> --from-step 5 --to-step 9",
        "opentraces ctx writes <trace-id> --with-trail-anchor --json",
    ],
    see_also=[
        ("opentraces ctx reads", "list read-direction tool calls for the same range."),
        ("opentraces trail blame commit", "find the commit anchoring a write."),
    ],
    option_groups=[
        ("Scope", ["trace_id", "project_dir"]),
        ("Range", ["step", "from_step", "to_step"]),
        ("Output", ["with_trail_anchor", "as_json"]),
    ],
)
@click.argument("trace_id")
@_io_range_options
@click.option(
    "--with-trail-anchor",
    "with_trail_anchor",
    is_flag=True,
    help="Annotate each write with the resolved trail_anchor_hint on its node.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@project_dir_option
def ctx_writes_cmd(
    trace_id: str,
    step: int | None,
    from_step: int | None,
    to_step: int | None,
    with_trail_anchor: bool,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """List Edit/Write-shaped writes (agent-to-environment) for a trace."""
    from ..core.context_tree.query import build_context_tree_projection

    repo = _project_repo(project_dir)
    projection = build_context_tree_projection(repo)
    if trace_id not in projection.nodes_by_trace:
        payload = _empty_state(
            CONTEXT_WRITES_SCHEMA_VERSION,
            "context_tree_not_captured",
            trace_id=trace_id,
            writes=[],
        )
        if as_json:
            _emit(payload)
            return
        click.echo(f"No Context Tree projected for trace {trace_id}")
        return

    if step is not None:
        from_step = step
        to_step = step
    writes = projection.writes_for_trace(trace_id, from_step=from_step, to_step=to_step)
    if with_trail_anchor:
        for entry in writes:
            nid = entry.get("node_id")
            if nid:
                node = projection.nodes_by_id.get(nid)
                if node is not None:
                    entry["trail_anchor_hint"] = node.trail_anchor_hint

    payload = {
        "schema_version": CONTEXT_WRITES_SCHEMA_VERSION,
        "trace_id": trace_id,
        "from_step": from_step,
        "to_step": to_step,
        "writes": writes,
    }
    if as_json:
        _emit(payload)
        return
    for entry in writes:
        anchor = entry.get("trail_anchor_hint") or {}
        commit = anchor.get("commit_id") if isinstance(anchor, dict) else None
        click.echo(
            f"step={entry.get('step_index')}  uuid={entry.get('uuid')}  "
            f"role={entry.get('role')}  commit={commit or '-'}"
        )


# --------------------------------------------------------------------------- #
# ctx diff
# --------------------------------------------------------------------------- #


@ctx_group.command(
    "diff",
    cls=OpentracesCommand,
    examples=[
        "opentraces ctx diff <node-a> <node-b>",
        "opentraces ctx diff <node-a> <node-b> --layer messages --json",
    ],
    see_also=[
        ("opentraces ctx show", "inspect a single ContextNode."),
    ],
    option_groups=[
        ("Scope", ["node_a", "node_b", "project_dir"]),
        ("Filter", ["layer"]),
        ("Output", ["as_json"]),
    ],
)
@click.argument("node_a")
@click.argument("node_b")
@click.option(
    "--layer",
    "layer_filter",
    default=None,
    help="Restrict the diff to one layer_type (system|messages|tool_registry|runtime_state).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@project_dir_option
def ctx_diff_cmd(
    node_a: str,
    node_b: str,
    layer_filter: str | None,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Diff two ContextNodes layer-by-layer."""
    from ..core.context_tree.query import build_context_tree_projection

    repo = _project_repo(project_dir)
    projection = build_context_tree_projection(repo)
    if node_a not in projection.nodes_by_id or node_b not in projection.nodes_by_id:
        if as_json:
            _emit(_empty_state(
                CONTEXT_TREE_SCHEMA_VERSION,
                "context_layer_unavailable",
                node_a=node_a,
                node_b=node_b,
            ))
            return
        _missing_resource(f"One or both ContextNodes not found: {node_a} {node_b}")

    diff = projection.layer_diff(node_a, node_b)
    if layer_filter:
        diff = {
            **diff,
            "changed": [
                entry for entry in diff.get("changed", [])
                if entry.get("layer_type") == layer_filter
            ],
        }
    payload = {
        "schema_version": CONTEXT_TREE_SCHEMA_VERSION,
        "node_a": node_a,
        "node_b": node_b,
        "diff": diff,
    }
    if as_json:
        _emit(payload)
        return
    if not diff.get("changed"):
        click.echo("No differences between layer references.")
        return
    for entry in diff.get("changed", []):
        click.echo(
            f"{entry['layer_type']:15}  "
            f"before={entry.get('before_layer_id') or '-'}  "
            f"after={entry.get('after_layer_id') or '-'}"
        )
        added = entry.get("added_keys") or []
        removed = entry.get("removed_keys") or []
        if added:
            click.echo(f"  +keys: {', '.join(added)}")
        if removed:
            click.echo(f"  -keys: {', '.join(removed)}")


# --------------------------------------------------------------------------- #
# ctx compactions
# --------------------------------------------------------------------------- #


@ctx_group.command(
    "compactions",
    cls=OpentracesCommand,
    examples=[
        "opentraces ctx compactions <trace-id>",
        "opentraces ctx compactions <trace-id> --index 0 --show-loss --json",
    ],
    see_also=[
        ("opentraces ctx diff", "diff the pre and post compaction nodes."),
    ],
    option_groups=[
        ("Scope", ["trace_id", "project_dir"]),
        ("Filter", ["index", "show_loss"]),
        ("Output", ["as_json"]),
    ],
)
@click.argument("trace_id")
@click.option("--index", "index", type=int, default=None, help="Show one compaction by 0-based index.")
@click.option(
    "--show-loss",
    "show_loss",
    is_flag=True,
    help="Include the lossy-diff payload for each compaction.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@project_dir_option
def ctx_compactions_cmd(
    trace_id: str,
    index: int | None,
    show_loss: bool,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """List compaction boundaries for a trace."""
    from ..core.context_tree.query import build_context_tree_projection

    repo = _project_repo(project_dir)
    projection = build_context_tree_projection(repo)
    if trace_id not in projection.nodes_by_trace:
        payload = _empty_state(
            CONTEXT_TREE_SCHEMA_VERSION,
            "context_tree_not_captured",
            trace_id=trace_id,
            compactions=[],
        )
        if as_json:
            _emit(payload)
            return
        click.echo(f"No Context Tree projected for trace {trace_id}")
        return

    events = projection.compaction_events_by_trace.get(trace_id, [])
    if index is not None:
        if index < 0 or index >= len(events):
            if as_json:
                _emit(_empty_state(
                    CONTEXT_TREE_SCHEMA_VERSION,
                    "context_layer_unavailable",
                    trace_id=trace_id,
                    index=index,
                    compactions=[],
                ))
                return
            _missing_resource(f"compaction index {index} out of range (have {len(events)})")
        events = [events[index]]

    rows: list[dict[str, Any]] = []
    for i, evt in enumerate(events):
        offset = index if index is not None else i
        entry: dict[str, Any] = {
            "index": offset,
            "pre_node_id": evt.get("pre_node_id"),
            "post_node_id": evt.get("post_node_id"),
            "lossy_diff_layer_id": evt.get("lossy_diff_layer_id"),
            "compacted_span_first_uuid": evt.get("compacted_span_first_uuid"),
            "compacted_span_last_uuid": evt.get("compacted_span_last_uuid"),
            "removed_message_uuids": evt.get("lossy_diff_removed_uuids", []),
        }
        if show_loss:
            entry["loss_detail"] = projection.compaction_loss(
                trace_id, offset
            )
        rows.append(entry)

    payload = {
        "schema_version": CONTEXT_TREE_SCHEMA_VERSION,
        "trace_id": trace_id,
        "compactions": rows,
    }
    if as_json:
        _emit(payload)
        return
    for entry in rows:
        click.echo(
            f"#{entry['index']}  pre={entry['pre_node_id'] or '-'}  "
            f"post={entry['post_node_id'] or '-'}  "
            f"removed={len(entry['removed_message_uuids'])}"
        )


# --------------------------------------------------------------------------- #
# ctx prune
# --------------------------------------------------------------------------- #


@ctx_group.command(
    "prune",
    cls=OpentracesCommand,
    examples=[
        "opentraces ctx prune <node-id> --source-jsonl path/to/session.jsonl",
        "opentraces ctx prune <node-id> --source-jsonl path/to/session.jsonl --to-session rewind-step7 --write --json",
    ],
    see_also=[
        ("opentraces ctx resume", "produce the resume packet for the same node."),
    ],
    option_groups=[
        ("Scope", ["node_id", "project_dir"]),
        ("Source", ["source_jsonl", "to_session"]),
        ("Action", ["write", "as_json"]),
    ],
)
@click.argument("node_id")
@click.option(
    "--source-jsonl",
    "source_jsonl",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Original Claude Code session JSONL to project from.",
)
@click.option(
    "--to-session",
    "to_session",
    default=None,
    help="Friendly stem for the new session id (uuid4 suffix is appended).",
)
@click.option(
    "--write",
    "write",
    is_flag=True,
    help="Actually write the new JSONL (default: dry-run; reports the target).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@project_dir_option
def ctx_prune_cmd(
    node_id: str,
    source_jsonl: Path,
    to_session: str | None,
    write: bool,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Project a Claude Code JSONL down to the active path ending at NODE_ID."""
    if _prune_session is None:  # pragma: no cover
        payload = {
            "schema_version": CONTEXT_RESUME_SCHEMA_VERSION,
            "error": "prune_session_to_node not yet shipped",
            "limitations": ["context_layer_unavailable"],
        }
        _emit(payload)
        sys.exit(3)

    repo = _project_repo(project_dir)
    try:
        result = _prune_session(
            repo,
            node_id,
            source_jsonl,
            to_session=to_session,
            write=write,
        )
    except FileExistsError as exc:
        payload = {
            "schema_version": CONTEXT_RESUME_SCHEMA_VERSION,
            "error": "destination_exists",
            "message": str(exc),
            "limitations": ["context_layer_unavailable"],
        }
        if as_json:
            _emit(payload)
        else:
            click.echo(str(exc), err=True)
        sys.exit(4)

    if as_json:
        _emit(result)
        return

    if result.get("error"):
        click.echo(f"prune failed: {result['error']}", err=True)
        sys.exit(3)
    verb = "wrote" if write else "would write"
    click.echo(
        f"{verb} {result.get('record_count', 0)} records to "
        f"{result.get('jsonl_path')}"
    )
    if not write:
        click.echo("  (dry-run; pass --write to materialize)")


# --------------------------------------------------------------------------- #
# ctx resume
# --------------------------------------------------------------------------- #


@ctx_group.command(
    "resume",
    cls=OpentracesCommand,
    examples=[
        "opentraces ctx resume <node-id>",
        "opentraces ctx resume <node-id> --json",
    ],
    see_also=[
        ("opentraces ctx prune", "rewrite a JSONL down to one branch."),
    ],
    option_groups=[
        ("Scope", ["node_id", "project_dir"]),
        ("Output", ["packet_only", "as_json"]),
    ],
)
@click.argument("node_id")
@click.option(
    "--packet-only",
    "packet_only",
    is_flag=True,
    default=True,
    help="Emit the structured packet only (default); informational text suppressed.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@project_dir_option
def ctx_resume_cmd(
    node_id: str,
    packet_only: bool,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Produce the structured context_resume_packet for NODE_ID."""
    if _resume_packet is None:  # pragma: no cover
        payload = {
            "schema_version": CONTEXT_RESUME_SCHEMA_VERSION,
            "node_id": node_id,
            "error": "context_resume_packet not yet shipped",
            "limitations": ["context_layer_unavailable"],
        }
        _emit(payload)
        sys.exit(3)

    repo = _project_repo(project_dir)
    packet = _resume_packet(repo, node_id)
    if as_json:
        _emit(packet)
        return

    if packet.get("error"):
        click.echo(f"resume packet unavailable: {packet['error']}", err=True)
        for lim in packet.get("limitations") or []:
            click.echo(f"  limitation: {lim}", err=True)
        sys.exit(3)
    click.echo(f"node:            {packet.get('node_id')}")
    click.echo(f"trace:           {packet.get('trace_id')}")
    click.echo(f"step_index:      {packet.get('step_index')}")
    click.echo(f"branch_type:     {packet.get('branch_type')}")
    click.echo(f"completeness:    {packet.get('capture_completeness')}")
    messages = packet.get("messages") or []
    click.echo(f"messages:        {len(messages)}")
    env = packet.get("env") or {}
    click.echo(f"env.cwd:         {env.get('cwd') or '-'}")
    click.echo(f"env.model:       {env.get('model') or '-'}")
    anchor = packet.get("trail_anchor_hint") or {}
    if anchor:
        click.echo(f"trail.commit_id: {anchor.get('commit_id') or '-'}")
    for lim in packet.get("capture_limitations") or []:
        click.echo(f"  limitation: {lim}")
    click.echo("# Suggested:")
    click.echo(f"#   claude --resume <session-id-from-ctx-prune>")


# --------------------------------------------------------------------------- #
# ctx resolve
# --------------------------------------------------------------------------- #


@ctx_group.command(
    "resolve",
    cls=OpentracesCommand,
    examples=[
        "opentraces ctx resolve ot://context-node/sha256/<id> --json",
        "opentraces ctx resolve ot://context-tree/<trace-id>/active-path --json",
    ],
    see_also=[
        ("opentraces trail resolve", "the analogous Trail resolver."),
    ],
    option_groups=[
        ("Scope", ["resource", "project_dir"]),
        ("Output", ["as_json"]),
    ],
)
@click.argument("resource")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@project_dir_option
def ctx_resolve_cmd(
    resource: str,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Resolve an ot://context-* URI through the unified resolver."""
    from ..core.trails.resources import resolve_resource

    repo = _project_repo(project_dir)
    try:
        payload = resolve_resource(repo, resource)
    except ValueError as exc:
        # Distinguish "unknown shape" (rc=2) from "valid shape, missing
        # object" (rc=3). resolve_resource raises ValueError in both
        # cases; the message text is the only signal. Empty-state envelope
        # contract from the plan: ``ctx-resolve-empty-state`` journey
        # expects ``limitations: ["context_layer_unavailable"]`` rather
        # than a traceback, so we surface that even for the missing-object
        # path.
        payload = {
            "schema_version": CONTEXT_RESOLVE_SCHEMA_VERSION,
            "resource": resource,
            "error": str(exc),
            "limitations": ["context_layer_unavailable"],
        }
        if as_json:
            _emit(payload)
        else:
            click.echo(f"Context resource unavailable: {resource}: {exc}", err=True)
        sys.exit(3)

    if "schema_version" not in payload:
        payload = {"schema_version": CONTEXT_RESOLVE_SCHEMA_VERSION, **payload}
    if as_json:
        _emit(payload)
        return
    click.echo(f"{payload.get('resource_type', resource)}")
    for lim in payload.get("limitations") or []:
        click.echo(f"  limitation: {lim}")


# --------------------------------------------------------------------------- #
# ctx anchor-for-step
# --------------------------------------------------------------------------- #


@ctx_group.command(
    "anchor-for-step",
    cls=OpentracesCommand,
    examples=[
        "opentraces ctx anchor-for-step <trace-id> 7",
    ],
    see_also=[
        ("opentraces trail blame commit", "show what the agent did in that commit."),
    ],
    option_groups=[
        ("Scope", ["trace_id", "step_index", "project_dir"]),
    ],
)
@click.argument("trace_id")
@click.argument("step_index", type=int)
@project_dir_option
def ctx_anchor_for_step_cmd(
    trace_id: str,
    step_index: int,
    project_dir: Path | None,
) -> None:
    """Print the trail_anchor_hint.commit_id for the ContextNode at STEP_INDEX.

    Scalar output (no JSON envelope) so the temporal-anchor-precision
    journey can pipe directly into git log / shell comparison. Missing
    anchor exits rc=3 with a stderr message.
    """
    from ..core.context_tree.query import build_context_tree_projection

    repo = _project_repo(project_dir)
    projection = build_context_tree_projection(repo)
    node = projection.node_for_step(trace_id, step_index)
    if node is None:
        click.echo(
            f"No ContextNode for trace {trace_id} step {step_index}",
            err=True,
        )
        sys.exit(3)
    anchor = node.trail_anchor_hint or {}
    commit_id = anchor.get("commit_id") if isinstance(anchor, dict) else None
    if not commit_id:
        click.echo(
            f"No trail_anchor_hint.commit_id for trace {trace_id} step {step_index}",
            err=True,
        )
        sys.exit(3)
    click.echo(commit_id)
