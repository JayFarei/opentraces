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


class CtxGroup(OpentracesGroup):
    """``ctx`` as a bare-noun-addressable group (v7 #164).

    ``ctx <trace>[:<step>|:last]`` renders the context projection of a
    trace directly, with no subcommand — the bare noun IS the read. A
    positional token that is NOT a registered subcommand is treated as
    that ``<trace>:<step>`` ref; the retained subcommands (``list`` /
    ``info`` and the hidden plumbing) still dispatch normally.

    ``trace:step`` is the universal cross-substrate address: the same
    token resolves across ``ctx``, ``trace get``, and ``trail``.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        first_pos: str | None = None
        for tok in args:
            if tok == "--":
                break
            if not tok.startswith("-"):
                first_pos = tok
                break
        if first_pos is not None and first_pos in self.commands:
            # Real subcommand dispatch: behave as a vanilla group. The
            # bare-noun ``ref`` param is absent on this path.
            ctx.params.setdefault("ref", None)
            return super().parse_args(ctx, args)
        # Bare-noun form (a ref) OR no args: parse the group's own options
        # and pull the single leftover positional out as the ref. A group
        # parser normally stops at the first positional (reserving it as the
        # subcommand name); allow interspersing so ``ctx <ref> --json`` parses
        # ``--json`` rather than leaving it stranded next to the ref.
        parser = self.make_parser(ctx)
        parser.allow_interspersed_args = True
        opts, extra, _order = parser.parse_args(args=list(args))
        for param in self.get_params(ctx):
            if isinstance(param, click.Argument):
                continue
            param.handle_parse_result(ctx, opts, extra)
        ref: str | None = None
        if extra:
            if len(extra) > 1:
                ctx.fail(
                    "ctx takes a single '<trace>[:<step>|:last]' ref; got "
                    f"{extra!r}. Decompose with '--json | jq' instead."
                )
            ref = extra[0]
        ctx.params["ref"] = ref
        # Click 8.2+ renamed the internal ``protected_args`` slot; guard both.
        if hasattr(ctx, "_protected_args"):
            ctx._protected_args = []
        ctx.args = []
        return []


@click.group("ctx", cls=CtxGroup, invoke_without_command=True)
@click.option(
    "--layer",
    "layer",
    type=click.Choice(["system", "messages", "tools", "runtime"]),
    default=None,
    help="Render ONE layer readably (default: the bounded four-layer card).",
)
@click.option(
    "--with-dropped",
    "with_dropped",
    is_flag=True,
    help="Include compaction-dropped content (audit-complete union).",
)
@click.option(
    "--remote",
    "remote",
    default=None,
    help="Read from a remote HF dataset bucket (plan 080 §7). Format: 'user/repo'.",
)
@click.option(
    "--offline",
    "offline",
    is_flag=True,
    help="Fail rc=4 if a referenced blob isn't already in the local cache.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@project_dir_option
@click.pass_context
def ctx_group(
    ctx: click.Context,
    ref: str | None,
    layer: str | None,
    with_dropped: bool,
    remote: str | None,
    offline: bool,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Context records: what did the model see? Per session, per step.

    \b
    ctx <trace>              the context overview (shape + capture method)
    ctx <trace>:<step>       the model input at that step
    ctx <trace>:last         the final / active step
        [--layer system|messages|tools|runtime]   render one layer readably
        [--with-dropped] [--json] [--remote R]

    ``trace:step`` is the universal address — the same token resolves
    across ``ctx``, ``trace get``, and ``trail``. A ``trace:step`` ref on
    stdin (``trace query --json | ctx --json``) is hydrated too.
    """
    if ctx.invoked_subcommand is not None:
        return
    _run_bare_ctx(
        ref=ref,
        layer=layer,
        with_dropped=with_dropped,
        remote=remote,
        offline=offline,
        as_json=as_json,
        project_dir=project_dir,
    )


def _project_repo(project_dir: Path | None) -> Path:
    """Resolve the project directory for query operations (default CWD)."""
    return Path(project_dir or Path.cwd()).resolve()


def _emit(payload: dict[str, Any]) -> None:
    """Stable JSON envelope emitter (mirrors cli/trace.py style).

    ADR-0007 lint L5: every ctx ``--json`` view carries a uniform
    ``{status, schema_version, ...}`` header. We inject a top-level
    ``status: "ok"`` when the caller has not already stamped a
    ``status`` / ``ok`` verdict (error payloads set ``status: "error"``
    themselves), so the header is present and ordered status-first
    without editing every payload site.
    """
    if "status" not in payload and "ok" not in payload:
        payload = {"status": "ok", **payload}
    click.echo(_dump_json(payload))


def _empty_state(schema_version: str, reason: str, **extras: Any) -> dict[str, Any]:
    """Return a standard empty-state envelope (rc=0, never raises)."""
    payload: dict[str, Any] = {
        "status": "ok",
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
# ctx <trace>[:step] — the bare-noun context read (v7 #164)
# --------------------------------------------------------------------------- #

# One frozen envelope for every bare-noun view (overview / step / layer) so a
# downstream consumer locks against a single shape. ``_emit`` stamps the
# top-level ``status`` (L5); this constant is the ``schema_version`` header.
_CTX_VIEW_SCHEMA_VERSION = "opentraces.ctx.view.v1"

# Human-facing --layer aliases -> the substrate's ContextLayer type names.
_LAYER_ALIAS: dict[str, str] = {
    "system": "system",
    "messages": "messages",
    "tools": "tool_registry",
    "runtime": "runtime_state",
}

# Bounded-by-default knobs. Size is a labelled ``len//4`` estimate, never a
# real token count (fidelity ladder: honest by construction).
_TOKEN_DIVISOR = 4
_MESSAGES_PAGE = 20  # rows per --layer messages page
_SPARK_CHARS = "▁▂▃▄▅▆▇█"
_SPARK_WIDTH = 16


def _spark(values: list[int]) -> str:
    """Render a compact unicode sparkline from a list of magnitudes."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    n = len(_SPARK_CHARS) - 1
    return "".join(_SPARK_CHARS[min(n, int((v - lo) / span * n))] for v in values)


def _est_tokens(byte_count: int) -> int:
    return byte_count // _TOKEN_DIVISOR


def _layer_content(layer: Any) -> Any:
    return getattr(layer, "content", None) if layer is not None else None


def _count_messages(content: Any) -> int | None:
    """Structural message count (works even for structure-only jsonl layers).

    Recognises both the current messages-layer shape (a ``messages`` list) and
    the legacy transcript-reconstruction shape (a ``steps`` list), so counts
    are exact for the bulk of already-captured bucket traces rather than "?".
    """
    if isinstance(content, list):
        return len(content)
    if isinstance(content, dict):
        for key in ("messages", "steps"):
            msgs = content.get(key)
            if isinstance(msgs, list):
                return len(msgs)
    return None


def _count_tools(content: Any) -> int | None:
    if isinstance(content, list):
        return len(content)
    if isinstance(content, dict):
        for key in ("tools", "tool_registry", "tool_definitions"):
            val = content.get(key)
            if isinstance(val, list):
                return len(val)
    return None


class _BareBackend:
    """Bounded per-trace resolver shared by every bare-noun view.

    Rides the #121 seam: the per-trace projection
    (``build_context_tree_projection_for_trace`` / ``resolve_node_traces``)
    with the issue-#158 bucket-companion fallback for when the project's
    Git event ref is hidden or unwritten. Never a whole-log ``read_events``.
    """

    def __init__(self, trace_id: str, project_dir: Path | None) -> None:
        from ..core.context_tree.query import build_context_tree_projection_for_trace

        self.trace_id = trace_id
        self.repo = _project_repo(project_dir)
        self.bucket_slug = _bucket_slug_for_project(project_dir)
        self.projection = build_context_tree_projection_for_trace(self.repo, trace_id)
        self._proj_nodes = self.projection.nodes_by_trace.get(trace_id, [])

    # -- node resolution ---------------------------------------------------
    def active_nodes(self) -> list[Any]:
        if self._proj_nodes:
            return list(self.projection.active_path(self.trace_id))
        if self.bucket_slug:
            nodes = _read_bucket_nodes(self.bucket_slug, self.trace_id)
            return sorted(
                nodes,
                key=lambda n: (n.step_index if n.step_index is not None else -1),
            )
        return []

    def node_for_step(self, step: int) -> Any | None:
        node = self.projection.node_for_step(self.trace_id, step)
        if node is None and self.bucket_slug:
            bnodes = [
                n
                for n in _read_bucket_nodes(self.bucket_slug, self.trace_id)
                if n.step_index == step
            ]
            node = _prefer_otel_node(bnodes, self.bucket_slug) if bnodes else None
        return node

    def active_leaf(self) -> Any | None:
        path = self.active_nodes()
        return path[-1] if path else None

    def get_layer(self, layer_id: str | None) -> Any | None:
        if not layer_id:
            return None
        layer = self.projection.layers_by_id.get(layer_id)
        if layer is None and self.bucket_slug:
            layer = _read_bucket_layer(self.bucket_slug, layer_id)
        return layer

    # -- honest fidelity ---------------------------------------------------
    def node_layers(self, node: Any) -> list[tuple[str, Any]]:
        pairs = (
            ("system", node.system_layer_id),
            ("messages", node.messages_layer_id),
            ("tool_registry", node.tool_registry_layer_id),
            ("runtime_state", node.runtime_state_layer_id),
        )
        return [(lt, self.get_layer(lid)) for lt, lid in pairs]

    def node_capture_method(self, node: Any) -> str:
        methods = {
            getattr(layer, "capture_method", None)
            for _lt, layer in self.node_layers(node)
            if layer is not None
        }
        methods.discard(None)
        if "otel" in methods:
            return "otel"
        if methods:
            return sorted(methods)[0]
        return getattr(node, "capture_completeness", None) or "unknown"


def _fidelity_for(capture_method: str) -> str:
    """Fold a capture_method label into the two-rung fidelity ladder."""
    return "otel" if capture_method == "otel" else "jsonl"


def _parse_ctx_ref(ref: str) -> tuple[str, str | int | None] | None:
    """Parse ``<trace>[:<step>|:last]``. Returns ``(trace_id, selector)`` or
    ``None`` on a malformed ref (caller -> rc=2, bad shape)."""
    if ":" not in ref:
        return (ref.strip(), None) if ref.strip() else None
    trace_id, _sep, sel = ref.partition(":")
    trace_id = trace_id.strip()
    sel = sel.strip()
    if not trace_id:
        return None
    if sel == "":
        return (trace_id, None)
    if sel == "last":
        return (trace_id, "last")
    try:
        return (trace_id, int(sel))
    except ValueError:
        return None


def _bad_shape(message: str) -> None:
    """rc=2: a valid command but a malformed ref / argument shape."""
    click.echo(message, err=True)
    sys.exit(2)


def _overview_summary(
    trace_id: str, project_dir: Path | None, remote: str | None
) -> dict[str, Any] | None:
    """Cheap (blob-free) overview counts + capture methods from the manifest,
    falling back to the per-trace projection when the manifest has no row."""
    manifest: dict[str, Any]
    if remote:
        try:
            manifest = _read_bucket_manifest_via_backend(remote)
        except _BackendUnavailable as exc:
            click.echo(str(exc), err=True)
            sys.exit(3)
    else:
        manifest = _read_bucket_manifest_no_blobs()
    entry = None
    for row in manifest.get("traces") or []:
        if isinstance(row, dict) and row.get("trace_id") == trace_id:
            entry = row
            break
    if entry is None and not remote:
        from ..core.bucket_store import trace_v2_summary_by_id

        entry = trace_v2_summary_by_id(trace_id)
    if entry is not None:
        summary = entry.get("summary") or {}
        methods = list(summary.get("capture_methods") or [])
        return {
            "trace_id": trace_id,
            "title": entry.get("title"),
            "step_count": int(summary.get("step_count") or 0),
            "node_count": int(summary.get("node_count") or 0),
            "capture_methods": methods,
            "source": "manifest",
        }
    return None


def _run_bare_ctx(
    *,
    ref: str | None,
    layer: str | None,
    with_dropped: bool,
    remote: str | None,
    offline: bool,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Dispatch the bare-noun ``ctx <ref>`` read (overview / step / layer)."""
    import opentraces.cli as _cli

    emit_json = bool(as_json) or bool(getattr(_cli, "_json_mode", False))

    refs = _resolve_refs(ref)
    if refs is None:
        # No ref and nothing on stdin: show the group help (like any group).
        ctx = click.get_current_context()
        click.echo(ctx.get_help())
        return

    multi = len(refs) > 1
    for one in refs:
        parsed = _parse_ctx_ref(one)
        if parsed is None:
            _bad_shape(
                f"Malformed ref {one!r}. Expected '<trace>', '<trace>:<step>', "
                "or '<trace>:last'."
            )
        trace_id, selector = parsed
        if selector is None:
            _render_overview(
                trace_id, project_dir, remote, emit_json=emit_json
            )
        else:
            _render_step_or_layer(
                trace_id,
                selector,
                layer=layer,
                with_dropped=with_dropped,
                remote=remote,
                offline=offline,
                emit_json=emit_json,
                project_dir=project_dir,
            )


def _resolve_refs(ref: str | None) -> list[str] | None:
    """Return the ref list: the CLI arg, else refs read off stdin (the
    ``trace query --json | ctx`` composition), else ``None``."""
    if ref:
        return [ref]
    if sys.stdin is None or sys.stdin.isatty():
        return None
    raw = sys.stdin.read()
    if not raw.strip():
        return None
    return _extract_refs_from_stdin(raw)


def _extract_refs_from_stdin(raw: str) -> list[str] | None:
    """Extract ``trace:step`` refs from piped stdin: a JSON envelope
    (``trace query``/``trail`` output), JSON-lines, or bare tokens."""
    refs: list[str] = []

    def _harvest(obj: Any) -> None:
        if isinstance(obj, dict):
            for key in ("ref", "trace_ref", "trace_step", "id"):
                val = obj.get(key)
                if isinstance(val, str) and val:
                    refs.append(val)
                    return
            tid = obj.get("trace_id")
            if isinstance(tid, str) and tid:
                step = obj.get("step") if obj.get("step") is not None else obj.get("step_index")
                refs.append(f"{tid}:{step}" if isinstance(step, int) else tid)
                return
            for val in obj.values():
                _harvest(val)
        elif isinstance(obj, list):
            for item in obj:
                _harvest(item)

    stripped = raw.strip()
    try:
        _harvest(json.loads(stripped))
    except ValueError:
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                _harvest(json.loads(line))
            except ValueError:
                refs.append(line.split()[0])
    # De-dup, preserve order.
    seen: set[str] = set()
    out = [r for r in refs if not (r in seen or seen.add(r))]
    return out or None


def _render_overview(
    trace_id: str,
    project_dir: Path | None,
    remote: str | None,
    *,
    emit_json: bool,
) -> None:
    summary = _overview_summary(trace_id, project_dir, remote)
    sparkline: str | None = None
    peak_step: int | None = None
    peak_tokens: int | None = None
    capture_methods: list[str] = summary["capture_methods"] if summary else []

    # Fall back to the per-trace projection for counts when the manifest has
    # no row (git-ref-only worlds), and derive the otel sparkline from the
    # already-in-memory active-path layer sizes (never extra blob reads).
    backend: _BareBackend | None = None
    if summary is None or ("otel" in capture_methods):
        if not remote:
            backend = _BareBackend(trace_id, project_dir)
    if summary is None:
        if backend is None or not backend.active_nodes():
            _missing_resource(f"No Context Tree for trace {trace_id!r}")
        nodes = backend.active_nodes()
        methods = set()
        for n in nodes:
            methods.add(backend.node_capture_method(n))
        capture_methods = sorted(m for m in methods if m and m != "unknown")
        summary = {
            "trace_id": trace_id,
            "title": None,
            "step_count": (nodes[-1].step_index + 1) if nodes and nodes[-1].step_index is not None else len(nodes),
            "node_count": len(nodes),
            "capture_methods": capture_methods,
            "source": "projection",
        }

    capture_method = "otel" if "otel" in capture_methods else (
        capture_methods[0] if capture_methods else "unknown"
    )
    fidelity = _fidelity_for(capture_method)

    # otel-only, best-effort sparkline from the active path's messages-layer
    # sizes (jsonl stores no content -> NO chart, per the fidelity ladder).
    if fidelity == "otel" and backend is not None and backend.active_nodes():
        sizes: list[int] = []
        steps: list[int | None] = []
        for n in backend.active_nodes():
            layer = backend.get_layer(n.messages_layer_id)
            if layer is not None:
                sizes.append(len(_dump_json(_layer_content(layer))))
                steps.append(n.step_index)
        if sizes:
            peak_idx = max(range(len(sizes)), key=lambda i: sizes[i])
            peak_step = steps[peak_idx]
            peak_tokens = _est_tokens(sizes[peak_idx])
            # Downsample to the sparkline width.
            if len(sizes) > _SPARK_WIDTH:
                stride = len(sizes) / _SPARK_WIDTH
                sampled = [sizes[int(i * stride)] for i in range(_SPARK_WIDTH)]
            else:
                sampled = sizes
            sparkline = _spark(sampled)

    step_count = summary["step_count"]
    last_step = step_count - 1 if step_count else 0
    jump_step = peak_step if peak_step is not None else last_step
    look = [
        f"opentraces ctx {trace_id}:{jump_step}",
        f"opentraces ctx {trace_id}:last",
    ]
    size_block: dict[str, Any] = {
        "estimate_available": fidelity == "otel",
        "unit": "tokens",
        "method": "len//4",
        "sparkline": sparkline,
        "peak_step": peak_step,
        "peak_tokens": peak_tokens,
    }
    if fidelity != "otel":
        size_block["note"] = (
            "structure-only (jsonl capture): message content is not stored, "
            "so no size chart is drawn"
        )

    payload = {
        "schema_version": _CTX_VIEW_SCHEMA_VERSION,
        "view": "overview",
        "trace_id": trace_id,
        "title": summary.get("title"),
        "capture_method": capture_method,
        "fidelity": fidelity,
        "step_count": step_count,
        "node_count": summary["node_count"],
        "size": size_block,
        "look": look,
    }
    if emit_json:
        _emit(payload)
        return

    label = "otel" if fidelity == "otel" else "jsonl (structure-only)"
    head = f"ctx {trace_id} · {label} · {step_count} steps"
    if peak_tokens is not None:
        head += f" · context ~{peak_tokens:,} tok (est)"
    click.echo(head)
    if sparkline:
        peak_txt = f"  peak @ step {peak_step}" if peak_step is not None else ""
        click.echo(f"  size  {sparkline}{peak_txt}")
    elif fidelity != "otel":
        click.echo(
            "  no size chart — jsonl capture stores structure only, not "
            "message content"
        )
    click.echo(f"  look → {look[0]}   {look[1]}")


def _render_step_or_layer(
    trace_id: str,
    selector: str | int,
    *,
    layer: str | None,
    with_dropped: bool,
    remote: str | None,
    offline: bool,
    emit_json: bool,
    project_dir: Path | None,
) -> None:
    backend = _BareBackend(trace_id, project_dir)
    if selector == "last":
        node = backend.active_leaf()
    else:
        node = backend.node_for_step(int(selector))
    if node is None:
        where = "last step" if selector == "last" else f"step {selector}"
        _missing_resource(f"No ContextNode for trace {trace_id} {where}")

    step_index = node.step_index
    capture_method = backend.node_capture_method(node)
    fidelity = _fidelity_for(capture_method)

    if layer:
        _render_layer(
            backend,
            node,
            trace_id=trace_id,
            step_index=step_index,
            layer_alias=layer,
            with_dropped=with_dropped,
            remote=remote,
            offline=offline,
            emit_json=emit_json,
            capture_method=capture_method,
            fidelity=fidelity,
        )
        return

    # The bounded step CARD (default: all four layers, summarized).
    layers = backend.node_layers(node)
    layers_out: dict[str, Any] = {}
    total_bytes = 0
    system_present = False
    msg_count: int | None = None
    tool_count: int | None = None
    for lt, lyr in layers:
        entry = _summarize_layer(lyr)
        layers_out[lt] = entry
        total_bytes += entry.get("byte_count", 0)
        if lt == "system" and lyr is not None:
            system_present = _layer_content(lyr) not in (None, "", {}, [])
        if lt == "messages":
            msg_count = _count_messages(_layer_content(lyr))
        if lt == "tool_registry":
            tool_count = _count_tools(_layer_content(lyr))

    est = _est_tokens(total_bytes)
    read = [
        f"opentraces ctx {trace_id}:{step_index} --layer messages",
        f"opentraces ctx {trace_id}:{step_index} --layer system",
        f"opentraces ctx {trace_id}:{step_index} --layer tools",
    ]
    payload = {
        "schema_version": _CTX_VIEW_SCHEMA_VERSION,
        "view": "step",
        "trace_id": trace_id,
        "step_index": step_index,
        "node_id": node.node_id,
        "capture_method": capture_method,
        "fidelity": fidelity,
        "saw": {
            "system": system_present,
            "messages": msg_count,
            "tools": tool_count,
        },
        "size": {"estimate": est, "unit": "tokens", "method": "len//4"},
        "layers": layers_out,
        "read": read,
    }
    if emit_json:
        _emit(payload)
        return

    msg_txt = "?" if msg_count is None else str(msg_count)
    tool_txt = "?" if tool_count is None else str(tool_count)
    sys_txt = "system prompt" if system_present else "no system layer"
    click.echo(
        f"ctx {trace_id}:{step_index} · step {step_index} · "
        f"{capture_method} · ~{est:,} tok (est)"
    )
    click.echo(f"  saw:  {sys_txt} · {msg_txt} messages · {tool_txt} tools")
    if fidelity != "otel":
        click.echo(
            "  fidelity: structure-only (jsonl) — counts are exact, message "
            "content is not stored"
        )
    click.echo(f"  read → --layer messages   --layer system   --layer tools")


def _render_layer(
    backend: _BareBackend,
    node: Any,
    *,
    trace_id: str,
    step_index: int | None,
    layer_alias: str,
    with_dropped: bool,
    remote: str | None,
    offline: bool,
    emit_json: bool,
    capture_method: str,
    fidelity: str,
) -> None:
    layer_type = _LAYER_ALIAS[layer_alias]
    layer_id = {
        "system": node.system_layer_id,
        "messages": node.messages_layer_id,
        "tool_registry": node.tool_registry_layer_id,
        "runtime_state": node.runtime_state_layer_id,
    }[layer_type]
    layer = backend.get_layer(layer_id)

    # Remote lazy-fetch / offline (mirrors ctx show's resolution order).
    fetched_from = "local" if layer is not None else None
    if layer is None and layer_id:
        if offline:
            _emit_offline_error(
                node=node,
                layer_id=layer_id,
                layer_type=layer_type,
                emit_json=emit_json,
            )
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
    content = _layer_content(layer)
    if layer_type == "messages" and content is not None:
        content = _hydrate_messages_content(
            content,
            backend.bucket_slug,
            remote=None if offline else remote,
            node=node,
        )

    # Extract the message list whether the (possibly hydrated) content is a
    # bare list, a current ``{messages: [...]}`` envelope, or the legacy
    # transcript-reconstruction ``{steps: [...]}`` envelope, then page it.
    msg_list: list[Any] | None = None
    msg_key: str | None = None
    if layer_type == "messages":
        if isinstance(content, list):
            msg_list = content
        elif isinstance(content, dict):
            for key in ("messages", "steps"):
                if isinstance(content.get(key), list):
                    msg_list = content[key]
                    msg_key = key
                    break
    page = None
    shown_content = content
    shown_msgs: list[Any] = []
    if msg_list is not None:
        shown_msgs = msg_list[:_MESSAGES_PAGE]
        page = {
            "shown": len(shown_msgs),
            "total": len(msg_list),
            "offset": 0,
            "limit": _MESSAGES_PAGE,
        }
        if isinstance(content, dict) and msg_key is not None:
            shown_content = {**content, msg_key: shown_msgs}
        else:
            shown_content = shown_msgs

    payload: dict[str, Any] = {
        "schema_version": _CTX_VIEW_SCHEMA_VERSION,
        "view": "layer",
        "trace_id": trace_id,
        "step_index": step_index,
        "node_id": node.node_id,
        "layer": layer_alias,
        "layer_type": layer_type,
        "capture_method": capture_method,
        "fidelity": fidelity,
        "completeness": entry.get("completeness"),
        "byte_count": entry.get("byte_count", 0),
        "estimate_tokens": _est_tokens(entry.get("byte_count", 0)),
        "content": shown_content,
    }
    if fetched_from is not None:
        payload["fetched_from"] = fetched_from
    if page is not None:
        payload["page"] = page
    if with_dropped:
        payload["with_dropped"] = _dropped_summary(backend, trace_id)
    if emit_json:
        _emit(payload)
        return

    click.echo(
        f"ctx {trace_id}:{step_index} --layer {layer_alias} · "
        f"{capture_method} · {entry.get('completeness') or '-'} · "
        f"~{_est_tokens(entry.get('byte_count', 0)):,} tok (est)"
    )
    if content is None:
        click.echo("  (layer not captured / no content)")
        return
    if msg_list is not None:
        for i, msg in enumerate(shown_msgs):
            role = msg.get("role", "?") if isinstance(msg, dict) else "?"
            preview = _truncate_for_text(_dump_json(msg), 240).replace("\n", " ")
            click.echo(f"  [{i}] {role}: {preview}")
        click.echo(f"  showing {page['shown']} of {page['total']} messages")
        if page["total"] > page["shown"]:
            click.echo(
                f"  read → opentraces ctx {trace_id}:{step_index} "
                f"--layer messages --json | jq '.content'"
            )
    else:
        click.echo(_truncate_for_text(_dump_json(content)))
    if fidelity != "otel" and layer_type == "messages":
        click.echo(
            "  fidelity: structure-only (jsonl) — message content not stored"
        )


def _emit_offline_error(
    *, node: Any, layer_id: str, layer_type: str, emit_json: bool
) -> None:
    """rc=4 no-clobber/offline refusal (mirrors ctx show --offline)."""
    hint = (
        f"layer blob not in local cache; run "
        f"'opentraces bucket prefetch {node.trace_id}' to warm it, or drop "
        "--offline to fetch from the remote."
    )
    if emit_json:
        _emit(
            {
                "schema_version": _CTX_VIEW_SCHEMA_VERSION,
                "status": "error",
                "view": "layer",
                "node_id": node.node_id,
                "error": {
                    "code": "BLOB_MISSING_OFFLINE",
                    "kind": "blob_missing_offline",
                    "missing_layer_id": layer_id,
                    "missing_layer_type": layer_type,
                    "hint": hint,
                },
            }
        )
    else:
        click.echo(f"Offline: {hint}", err=True)
    sys.exit(4)


def _dropped_summary(backend: _BareBackend, trace_id: str) -> dict[str, Any]:
    """Compaction-drop summary for ``--with-dropped``.

    Honest by construction: a non-compacted trace reports ``count: 0`` (the
    union equals the window — no fabrication); a compacted trace surfaces the
    recorded compaction events. Full dropped-content REHYDRATION is a v1.1
    follow-up (the events + the audit-complete union of stored request
    bodies); this reports the compaction boundaries that make it recoverable.
    """
    events = getattr(backend.projection, "compaction_events_by_trace", {}).get(
        trace_id, []
    )
    return {
        "count": len(events),
        "compaction_boundaries": [
            {
                "node_id": getattr(e, "node_id", None),
                "step_index": getattr(e, "step_index", None),
            }
            for e in events
        ],
        "rehydration": "v1.1: dropped-content union not yet materialized",
    }


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


# --------------------------------------------------------------------------- #
# Bucket-backed reads (issue #158 W4): resolve nodes + layers + per-message
# text straight from the bucket companion, so ctx reads survive a hidden git
# event ref (DoD#5) and surface the message TEXT behind each content_hash.
# --------------------------------------------------------------------------- #


def _bucket_slug_for_project(project_dir: Path | None) -> str | None:
    """Canonical context bucket slug for a project dir (matches ingest/flush)."""
    try:
        from ..core.config import get_project_dir

        target = Path(project_dir) if project_dir else Path.cwd()
        return get_project_dir(target).name
    except Exception:
        return None


def _read_bucket_nodes(slug: str, trace_id: str) -> list[Any]:
    """Read a trace's ContextNodes from the bucket companion (nodes.jsonl)."""
    from ..core.bucket_layout import context_tree_nodes_path
    from ..core.context_tree.models import ContextNode

    path = context_tree_nodes_path(slug, trace_id)
    if not path.exists():
        return []
    out: list[Any] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(ContextNode.model_validate(json.loads(line)))
        except (ValueError, json.JSONDecodeError):
            continue
    return out


def _read_bucket_layer(slug: str, layer_id: str) -> Any:
    """Read one layer blob from the bucket companion -> ContextLayer or None."""
    if not slug or not layer_id:
        return None
    import gzip

    from ..core.bucket_layout import blobs_v1_context_path
    from ..core.context_tree.models import ContextLayer

    path = blobs_v1_context_path(slug, layer_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(gzip.decompress(path.read_bytes()))
        return ContextLayer.model_validate(payload)
    except (ValueError, OSError, json.JSONDecodeError):
        return None


def _find_node_in_bucket(slug: str, node_id: str) -> Any:
    """Scan a project's bucket context trees for ``node_id`` -> ContextNode."""
    from urllib.parse import unquote

    from ..core.bucket_layout import _path_part, contexts_root

    base = contexts_root() / _path_part(slug)
    if not base.exists():
        return None
    for trace_dir in base.iterdir():
        if not trace_dir.is_dir():
            continue
        for node in _read_bucket_nodes(slug, unquote(trace_dir.name)):
            if node.node_id == node_id:
                return node
    return None


def _prefer_otel_node(nodes: list[Any], slug: str) -> Any:
    """Pick the OTel-captured node when several nodes share a step.

    A trace can carry both JSONL (``transcript_reconstruction``, approximated)
    and OTel (``otel``, full per-step) context. The OTel node is higher fidelity
    so it is the one ctx presents (issue #158 W4).
    """
    if not nodes:
        return None
    for node in nodes:
        layer = _read_bucket_layer(slug, node.messages_layer_id)
        if layer is not None and getattr(layer, "capture_method", None) == "otel":
            return node
    return nodes[0]


def _flatten_message_text(payload: Any) -> str:
    """Best-effort human-readable text for a message payload (deref result)."""
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""
    content = payload.get("content")
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif block.get("type") and isinstance(block.get("content"), str):
                    parts.append(block["content"])
            elif isinstance(block, str):
                parts.append(block)
    return "\n".join(parts)


def _hydrate_messages_content(
    content: dict[str, Any],
    slug: str | None,
    *,
    remote: str | None = None,
    node: Any = None,
) -> dict[str, Any]:
    """Resolve each manifest ``content_hash`` to its message text from raw/ blobs.

    Returns a copy of the messages-layer content with every entry gaining a
    ``content`` (the parsed message payload) and ``text`` (flattened) field when
    its blob resolves. ``hydrated`` records how many resolved — the agent-facing
    proof that content_hash is no longer a dangling pointer (issue #158).

    Local resolution always tries the LOCAL raw/ blob cache first. When
    ``remote`` is set (and not ``--offline``), any ``content_hash`` the local
    cache lacks is lazy-fetched from the remote backend — mirroring
    ``_lazy_fetch_layer_blob`` for layer blobs. The raw/ message blobs are
    lazy-on-pull (``bucket_remote._is_lazy_blob_path`` classifies
    ``blobs/v1/<slug>/raw/**`` as fetched on demand by ``ctx show``), so this
    is the path that actually fetches them. The fetched bytes are verified
    against their ``content_hash`` and written into the LOCAL cache so a later
    ``--offline`` read resolves from disk.
    """
    if not slug or not isinstance(content, dict):
        return content
    from ..core.context_tree.raw_blobs import deref_message_payload

    entries = content.get("messages")
    if not isinstance(entries, list):
        return content

    # Resolve the remote backend's project_slug once (issue #158 track C).
    # The local deref + local cache write key off ``slug``; the remote fetch
    # keys off the slug the remote bucket stores the blob under (same in the
    # symmetric local/remote model, but resolved from the node to mirror
    # ``_lazy_fetch_layer_blob`` exactly).
    remote_slug: str | None = None
    if remote and node is not None:
        remote_slug = _project_slug_for_node(node, remote=remote) or slug

    out = dict(content)
    new_entries: list[Any] = []
    resolved = 0
    for entry in entries:
        if not isinstance(entry, dict):
            new_entries.append(entry)
            continue
        e = dict(entry)
        ch = e.get("content_hash")
        if ch:
            payload = deref_message_payload(slug, ch)
            if payload is None and remote and remote_slug:
                payload = _lazy_fetch_message_blob(
                    remote=remote,
                    content_hash=ch,
                    local_slug=slug,
                    remote_slug=remote_slug,
                )
            if payload is not None:
                e["content"] = payload
                e["text"] = _flatten_message_text(payload)
                resolved += 1
        new_entries.append(e)
    out["messages"] = new_entries
    out["hydrated"] = {"resolved": resolved, "total": len(entries)}
    return out


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


def _lazy_fetch_message_blob(
    *,
    remote: str,
    content_hash: str,
    local_slug: str,
    remote_slug: str,
) -> Any:
    """Lazy-fetch one per-message raw blob from the remote backend.

    Issue #158 (track C) — the raw/ message-content sibling of
    ``_lazy_fetch_layer_blob``. ``ctx show --full --remote`` reaches here when
    the LOCAL raw/ cache lacks a ``content_hash``. The fetched bytes are the
    exact canonical-JSON bytes the ``content_hash`` was taken over, so we
    re-hash to prove the round trip, write them into the LOCAL bucket cache
    (deterministic gzip, ``mtime=0`` — so a later ``--offline`` read hits the
    cache), and return the parsed message payload. Returns ``None`` on a miss,
    a corrupt blob, or a hash mismatch (best-effort, never raises).
    """
    try:
        backend = _cached_backend(remote)
    except ImportError:
        # --remote requires the D1 backend module; for message-text
        # hydration this is best-effort, so degrade silently rather than
        # aborting the whole `ctx show`.
        return None
    try:
        raw = backend.get_raw_message_blob(content_hash, remote_slug)
    except Exception:
        return None
    if not raw:
        return None
    # Round-trip: the blob is content-addressed by ``content_hash`` over the
    # exact bytes. A mismatch means a corrupt or forged blob — skip it.
    import hashlib

    if "sha256:" + hashlib.sha256(raw).hexdigest() != content_hash:
        return None
    # Persist into the LOCAL cache so the next ``--offline`` deref resolves
    # from disk (same deterministic gzip writer the layer-blob cache uses).
    try:
        from ..core._bucket_io import _atomic_write_gzip
        from ..core.bucket_layout import blobs_v1_raw_path

        local_blob = blobs_v1_raw_path(local_slug, content_hash, suffix=".json.gz")
        if not local_blob.exists():
            _atomic_write_gzip(local_blob, raw)
    except Exception:
        # Caching is best-effort; the parsed payload is still returned so the
        # current call hydrates even if the cache write fails.
        pass
    try:
        return json.loads(raw)
    except (ValueError, json.JSONDecodeError):
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

    # Issue #54 — documented ``trace.json`` fallback. On a LOCAL read, when the
    # manifest carries no row for ``trace_id`` (a drifted / never-written
    # manifest) but the per-trace envelope exists on disk, derive the row from
    # that envelope via ``_per_trace_v2_summary`` so the info block is
    # byte-identical to the manifest-row block. Remote reads have no on-disk
    # envelope, so the fallback is local-only.
    if matched is None and not remote:
        from ..core.bucket_store import trace_v2_summary_by_id

        matched = trace_v2_summary_by_id(trace_id)

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
    hidden=True,  # v7 #164 — superseded by the bare-noun `ctx <trace>` overview; callable, off --help
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
    from ..core.context_tree.query import build_context_tree_projection_for_trace

    repo = _project_repo(project_dir)
    projection = build_context_tree_projection_for_trace(repo, trace_id)
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
    def short(nid):
        return (nid[:18] + "…") if nid and len(nid) > 18 else (nid or "-")

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
    hidden=True,  # v7 #164 — superseded by `ctx <trace>:<step>`; callable, off --help
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
    from ..core.context_tree.query import (
        build_context_tree_projection_for_trace,
        resolve_node_traces,
    )

    repo = _project_repo(project_dir)
    bucket_slug = _bucket_slug_for_project(project_dir)
    # Issue #121: bound the read — resolve the owning trace via a scoped
    # (context_node_observed-only) event read, then project just that trace,
    # instead of materialising the whole event log. Byte-identical output:
    # the per-trace projection contains exactly this node + its 4 layer ids.
    resolved = resolve_node_traces(repo, {node_id})
    owner_trace = resolved.get(node_id)
    projection = (
        build_context_tree_projection_for_trace(repo, owner_trace)
        if owner_trace is not None
        else None
    )
    node = projection.nodes_by_id.get(node_id) if projection is not None else None
    # Issue #158 W4 / DoD#5: when the git event ref is unavailable (hidden or
    # never written), resolve the node from the bucket companion so ctx reads
    # are bucket-backed, not git-replay-backed.
    if node is None and bucket_slug:
        node = _find_node_in_bucket(bucket_slug, node_id)
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
        layer = projection.layers_by_id.get(layer_id) if projection is not None else None
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
        # Issue #158 W4: bucket-companion rung, keyed by the project-derived
        # slug (works even when the manifest has no row for the trace yet, and
        # when the git event ref is hidden — DoD#5).
        if layer is None and layer_id and bucket_slug:
            from_bucket = _read_bucket_layer(bucket_slug, layer_id)
            if from_bucket is not None:
                layer = from_bucket
                fetched_from = "bucket"
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
            content = layer.content
            # Issue #158 W4 / DoD#1: hydrate the messages manifest — resolve
            # each content_hash to its message text from the raw/ blobs. With
            # ``--remote`` the missing raw/ blobs are lazy-fetched (track C);
            # ``--offline`` blocks the network exactly like the layer-blob
            # path above (passing remote=None keeps the read local-only).
            if layer_type == "messages":
                content = _hydrate_messages_content(
                    content,
                    bucket_slug,
                    remote=None if offline else remote,
                    node=node,
                )
            entry["content"] = content
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
    # Issue #42 (ctx-output-parity): capture_method is part of the JSON
    # envelope's honest-provenance labels, so the text renderer must
    # surface it too — a human reader must see e.g.
    # `transcript_reconstruction` next to `approximated`. The header row
    # names the columns so the labels themselves are visible in text mode.
    click.echo(
        f"  {'layer':15}  {'completeness':12}  {'capture_method':26}  "
        f"{'bytes':>9}  layer_id"
    )
    for layer_type, entry in layers_out.items():
        click.echo(
            f"  {layer_type:15}  {entry['completeness'] or '-':12}  "
            f"{entry.get('capture_method') or '-':26}  "
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
    hidden=True,  # v7 #164 — superseded by `ctx <trace>:<step>`; callable, off --help
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
    from ..core.context_tree.query import build_context_tree_projection_for_trace

    repo = _project_repo(project_dir)
    bucket_slug = _bucket_slug_for_project(project_dir)
    projection = build_context_tree_projection_for_trace(repo, trace_id)
    node = projection.node_for_step(trace_id, step_index)
    # Issue #158 W4 / DoD#5: bucket-companion fallback when the git event ref
    # is unavailable. Prefer the OTel (full) node when both capture methods
    # left a node at this step.
    if node is None and bucket_slug:
        bnodes = [n for n in _read_bucket_nodes(bucket_slug, trace_id) if n.step_index == step_index]
        node = _prefer_otel_node(bnodes, bucket_slug) if bnodes else None
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

    def _layer(lid: str) -> Any:
        layer = projection.layers_by_id.get(lid)
        if layer is None and bucket_slug:
            layer = _read_bucket_layer(bucket_slug, lid)
        return layer

    layers_out = {lt: _summarize_layer(_layer(lid)) for lt, lid in layer_pairs}
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
    hidden=True,  # v7 #164 — decomposition is `ctx <trace>:<step> --json | jq`; callable, off --help
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
    from ..core.context_tree.query import build_context_tree_projection_for_trace

    repo = _project_repo(project_dir)
    projection = build_context_tree_projection_for_trace(repo, trace_id)
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
    hidden=True,  # v7 #164 — decomposition is `ctx <trace>:<step> --json | jq`; callable, off --help
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
    from ..core.context_tree.query import build_context_tree_projection_for_trace

    repo = _project_repo(project_dir)
    projection = build_context_tree_projection_for_trace(repo, trace_id)
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
    hidden=True,  # plan 087 — Context Tree substrate plumbing; callable, off --help
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
    from ..core.context_tree.query import (
        build_context_tree_projection_for_trace,
        resolve_node_traces,
    )

    repo = _project_repo(project_dir)
    # Issue #121: bound the read. The two nodes MAY belong to different
    # traces, so resolve both owning traces via a scoped event read, then
    # build per-trace projection(s) and merge their nodes/layers into one
    # projection for layer_diff. Byte-identical: layer_diff is a pure
    # function of the two nodes + their layer contents, reproduced exactly
    # by the per-trace subset. (content-addressed dedup is order-independent.)
    resolved = resolve_node_traces(repo, {node_a, node_b})
    owner_a = resolved.get(node_a)
    owner_b = resolved.get(node_b)
    projection = None
    if owner_a is not None and owner_b is not None:
        projection = build_context_tree_projection_for_trace(repo, owner_a)
        if owner_b != owner_a:
            other = build_context_tree_projection_for_trace(repo, owner_b)
            for nid, n in other.nodes_by_id.items():
                projection.nodes_by_id.setdefault(nid, n)
            for lid, layer in other.layers_by_id.items():
                projection.layers_by_id.setdefault(lid, layer)
    if (
        projection is None
        or node_a not in projection.nodes_by_id
        or node_b not in projection.nodes_by_id
    ):
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
    hidden=True,  # plan 087 — Context Tree substrate diagnostic; callable, off --help
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
    from ..core.context_tree.query import build_context_tree_projection_for_trace

    repo = _project_repo(project_dir)
    projection = build_context_tree_projection_for_trace(repo, trace_id)
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
    hidden=True,  # plan 087 — resume-from-step-N plumbing; callable, off --help
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
    help=(
        "Friendly stem for the new session id, used verbatim (sanitized); "
        "an existing destination errors rc=4 instead of overwriting. "
        "Omit for an anonymous sess-<uuid> id."
    ),
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
    hidden=True,  # plan 087 — RL/research resume-packet plumbing; callable, off --help
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
    click.echo("#   claude --resume <session-id-from-ctx-prune>")


# --------------------------------------------------------------------------- #
# ctx resolve
# --------------------------------------------------------------------------- #


@ctx_group.command(
    "resolve",
    cls=OpentracesCommand,
    hidden=True,  # plan 087 — ot:// URI resolution plumbing; callable, off --help
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
    hidden=True,  # plan 087 — low-level scalar lookup plumbing; callable, off --help
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
    from ..core.context_tree.query import build_context_tree_projection_for_trace

    repo = _project_repo(project_dir)
    projection = build_context_tree_projection_for_trace(repo, trace_id)
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
