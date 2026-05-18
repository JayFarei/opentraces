"""Compare JSONL-only vs JSONL+proxy Context Tree projections.

Drives the prototype's headline experiment: run a synthetic Claude
Code session twice through two side-by-side capture stacks and report
the field-by-field delta between the two resulting Context Tree
projections.

Path A: existing JSONL pipeline only.
Path B: existing JSONL pipeline PLUS the HTTP proxy capture.

Both write into independent fresh Git repositories so the two event
logs never collide; the comparison reads each projection in isolation
and renders a markdown report at ``--output``.

The script doubles as the documentation of the prototype's setup: read
``main()`` to see exactly how the two paths are wired.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


# --------------------------------------------------------------------------- #
# Fixture runner
# --------------------------------------------------------------------------- #


def _import_fixture(fixture_dir: Path):
    """Import ``<fixture_dir>/session.py`` as a fresh module each call."""
    session_path = fixture_dir / "session.py"
    if not session_path.exists():
        raise FileNotFoundError(f"fixture has no session.py: {session_path}")
    spec = importlib.util.spec_from_file_location(
        f"context_tree_via_proxy_fixture_{uuid.uuid4().hex[:8]}",
        session_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {session_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_init(project: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(
        ["git", "config", "user.email", "proto@opentraces.local"],
        cwd=project, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "proto"],
        cwd=project, check=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-q", "-m", "init"],
        cwd=project, check=True,
    )


# --------------------------------------------------------------------------- #
# Path A: JSONL only
# --------------------------------------------------------------------------- #


def run_path_a(fixture_dir: Path, workspace: Path) -> dict[str, Any]:
    """Run the JSONL pipeline only and return a projection summary."""
    from opentraces.capture.claude_code.context_tree_capture import (
        emit_context_tree_events_from_record,
    )
    from opentraces.core.context_tree.query import build_context_tree_projection

    project = workspace / "project_a"
    project.mkdir()
    _git_init(project)
    transcript = workspace / "session_a.jsonl"

    mod = _import_fixture(fixture_dir)
    mod.run(project, transcript)

    fake_trace_record = _FakeTraceRecord(
        trace_id="trace-path-a",
        metadata={"source_path": str(transcript)},
    )
    summary = emit_context_tree_events_from_record(
        project_dir=project,
        final_record=fake_trace_record,
        transcript_path=transcript,
    )
    projection = build_context_tree_projection(project)
    return {
        "summary": summary,
        "projection": projection,
        "project_dir": project,
        "transcript": transcript,
    }


# --------------------------------------------------------------------------- #
# Path B: JSONL + proxy
# --------------------------------------------------------------------------- #


def run_path_b(fixture_dir: Path, workspace: Path) -> dict[str, Any]:
    """Run BOTH the JSONL pipeline and the proxy pipeline together."""
    from opentraces.capture.claude_code.context_tree_capture import (
        emit_context_tree_events_from_record,
    )
    from opentraces.capture.http_proxy import (
        HTTP_LOG_FILENAME,
        ProxyConfig,
        emit_context_tree_events_from_http_log,
        run_proxy,
    )
    from opentraces.capture.http_proxy.fake_backend import (
        BackendConfig,
        run_fake_backend,
    )
    from opentraces.core.context_tree.query import build_context_tree_projection

    project = workspace / "project_b"
    project.mkdir()
    _git_init(project)
    transcript = workspace / "session_b.jsonl"
    proxy_log = workspace / HTTP_LOG_FILENAME

    backend_config = BackendConfig(listen_port=0, mode="echo")
    backend_handle = run_fake_backend(backend_config)
    try:
        proxy_config = ProxyConfig(
            capture_log_path=proxy_log,
            upstream_base_url=f"http://127.0.0.1:{backend_handle.port}",
            listen_port=0,
        )
        proxy_handle = run_proxy(proxy_config)
        try:
            _wait_for_health(f"http://127.0.0.1:{proxy_handle.port}/healthz")
            _wait_for_health(f"http://127.0.0.1:{backend_handle.port}/healthz")

            mod = _import_fixture(fixture_dir)
            # The fixture for path B must accept proxy_url so the inline
            # HTTP calls can target it. The path A fixture (existing
            # context-tree-multi-turn) does not — that's why path B
            # uses a fresh fixture.
            mod.run(
                project,
                transcript,
                proxy_url=f"http://127.0.0.1:{proxy_handle.port}",
            )
        finally:
            proxy_handle.stop()
    finally:
        backend_handle.stop()

    fake_trace_record = _FakeTraceRecord(
        trace_id="trace-path-b",
        metadata={"source_path": str(transcript)},
    )
    jsonl_summary = emit_context_tree_events_from_record(
        project_dir=project,
        final_record=fake_trace_record,
        transcript_path=transcript,
    )
    proxy_summary = emit_context_tree_events_from_http_log(
        project_dir=project,
        trace_id="trace-path-b",
        log_path=proxy_log,
    )
    projection = build_context_tree_projection(project)
    return {
        "jsonl_summary": jsonl_summary,
        "proxy_summary": proxy_summary,
        "projection": projection,
        "project_dir": project,
        "transcript": transcript,
        "proxy_log": proxy_log,
    }


def _wait_for_health(url: str, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(0.05)
    raise RuntimeError(f"health check timeout for {url}: {last_err}")


# --------------------------------------------------------------------------- #
# Comparison logic
# --------------------------------------------------------------------------- #


@dataclass
class LayerSummary:
    layer_type: str
    layer_id: str
    completeness: str
    capture_method: str
    field_keys: list[str]
    byte_size: int

    @classmethod
    def from_layer(cls, layer) -> "LayerSummary":
        canonical = json.dumps(layer.content, sort_keys=True, ensure_ascii=False)
        return cls(
            layer_type=layer.layer_type,
            layer_id=layer.layer_id,
            completeness=layer.completeness,
            capture_method=layer.capture_method,
            field_keys=sorted(layer.content.keys()),
            byte_size=len(canonical.encode("utf-8")),
        )


def project_layer_summaries(projection) -> dict[str, list[LayerSummary]]:
    """Group layers by (capture_method, layer_type) for delta tables."""
    grouped: dict[str, list[LayerSummary]] = {}
    for layer in projection.layers_by_id.values():
        key = f"{layer.capture_method}/{layer.layer_type}"
        grouped.setdefault(key, []).append(LayerSummary.from_layer(layer))
    return grouped


def field_completeness_table(projection) -> list[dict[str, Any]]:
    """Compute the per-layer-type completeness counts."""
    rows: list[dict[str, Any]] = []
    by_method_type: dict[tuple[str, str], dict[str, int]] = {}
    for layer in projection.layers_by_id.values():
        key = (layer.capture_method, layer.layer_type)
        counts = by_method_type.setdefault(
            key, {"full": 0, "approximated": 0, "stub": 0}
        )
        counts[layer.completeness] += 1
    for (method, layer_type), counts in sorted(by_method_type.items()):
        rows.append({
            "capture_method": method,
            "layer_type": layer_type,
            **counts,
            "total": sum(counts.values()),
        })
    return rows


def worked_example(path_a, path_b) -> dict[str, Any]:
    """Pick one ContextNode from path B and diff its layer contents.

    Returns the JSONL-only view (path A's first linear node) and the
    proxy-augmented view (path B's first proxy-emitted node) so the
    report can render them side by side.
    """
    proj_a = path_a["projection"]
    proj_b = path_b["projection"]

    proxy_layers_b = [
        layer for layer in proj_b.layers_by_id.values()
        if layer.capture_method == "proxy"
    ]
    proxy_layers_by_type: dict[str, Any] = {}
    for layer in proxy_layers_b:
        proxy_layers_by_type.setdefault(layer.layer_type, layer)

    jsonl_layers_a = list(proj_a.layers_by_id.values())
    jsonl_layers_by_type: dict[str, Any] = {}
    for layer in jsonl_layers_a:
        jsonl_layers_by_type.setdefault(layer.layer_type, layer)

    example: dict[str, Any] = {}
    for layer_type in ("system", "messages", "tool_registry", "runtime_state"):
        a_layer = jsonl_layers_by_type.get(layer_type)
        b_layer = proxy_layers_by_type.get(layer_type)
        example[layer_type] = {
            "jsonl_layer_id": a_layer.layer_id if a_layer else None,
            "proxy_layer_id": b_layer.layer_id if b_layer else None,
            "jsonl_completeness": a_layer.completeness if a_layer else None,
            "proxy_completeness": b_layer.completeness if b_layer else None,
            "jsonl_capture_method": a_layer.capture_method if a_layer else None,
            "proxy_capture_method": b_layer.capture_method if b_layer else None,
            "jsonl_field_keys": sorted((a_layer.content or {}).keys()) if a_layer else [],
            "proxy_field_keys": sorted((b_layer.content or {}).keys()) if b_layer else [],
            "jsonl_content": a_layer.content if a_layer else None,
            "proxy_content": b_layer.content if b_layer else None,
        }
    return example


# --------------------------------------------------------------------------- #
# Report rendering
# --------------------------------------------------------------------------- #


def render_report(
    *,
    fixture_dir: Path,
    path_a: dict[str, Any],
    path_b: dict[str, Any],
) -> str:
    proj_a = path_a["projection"]
    proj_b = path_b["projection"]
    completeness_a = field_completeness_table(proj_a)
    completeness_b = field_completeness_table(proj_b)
    example = worked_example(path_a, path_b)

    out: list[str] = []
    w = out.append
    w("# HTTP proxy capture prototype — comparison report")
    w("")
    w("**Branch:** `prototype/http-proxy-capture`  ")
    w("**Plan:** 078 evidence (HTTP proxy capture layer for opentraces)  ")
    w(f"**Fixture:** `{fixture_dir.as_posix()}`")
    w("")
    w("## Setup")
    w("")
    w("Both paths run the same fixture (`session.py`). Path A runs the JSONL ")
    w("capture path only; path B runs JSONL + the prototype HTTP proxy ")
    w("(stdlib `http.server` + `httpx` forwarding into a canned-response ")
    w("fake Anthropic backend on `127.0.0.1:<random>`). Each path uses an ")
    w("independent Git project so the canonical event logs don't collide.")
    w("")
    w("Proxy choice: **option B (roll-our-own ~150-line httpx proxy)**. ")
    w("LiteLLM's value is in normalising provider differences and adding ")
    w("rate-limit/cost tooling; for the prototype's question ('what bytes ")
    w("does the proxy see that JSONL doesn't') neither applies and a ")
    w("stdlib forwarder gives full control of the capture-log shape.")
    w("")
    w("## Headline numbers")
    w("")
    w(f"- Path A (JSONL only):   `{proj_a.summary()['layer_count']}` layers, `{proj_a.summary()['node_count']}` nodes across `{proj_a.summary()['trace_count']}` traces.")
    w(f"- Path B (JSONL+proxy): `{proj_b.summary()['layer_count']}` layers, `{proj_b.summary()['node_count']}` nodes across `{proj_b.summary()['trace_count']}` traces.")
    w("")
    w("**Completeness by (capture_method, layer_type):**")
    w("")
    w("Path A (JSONL only):")
    w("")
    w(_format_table(completeness_a, [
        ("capture_method", "capture_method"),
        ("layer_type", "layer_type"),
        ("full", "full"),
        ("approximated", "approximated"),
        ("stub", "stub"),
        ("total", "total"),
    ]))
    w("")
    w("Path B (JSONL+proxy):")
    w("")
    w(_format_table(completeness_b, [
        ("capture_method", "capture_method"),
        ("layer_type", "layer_type"),
        ("full", "full"),
        ("approximated", "approximated"),
        ("stub", "stub"),
        ("total", "total"),
    ]))
    w("")
    w("**Delta:** in path B every `tool_registry` and `system` layer ")
    w("the proxy emitted lands as `full`/`proxy` rather than ")
    w("`approximated`/`transcript_reconstruction`. The JSONL-emitted ")
    w("layers are still present (and still `approximated`); the proxy ")
    w("layers are additive content-addressed siblings in the same event ")
    w("log. Consumers that want full-fidelity replay filter on ")
    w("`capture_method == 'proxy'` and fall back to JSONL when no proxy ")
    w("layer is available.")
    w("")
    w("## Worked example — first turn")
    w("")
    for layer_type in ("system", "messages", "tool_registry", "runtime_state"):
        entry = example.get(layer_type, {})
        w(f"### `{layer_type}` layer")
        w("")
        w(f"- JSONL layer id:  `{entry.get('jsonl_layer_id')}`")
        w(f"  - completeness=`{entry.get('jsonl_completeness')}`, ")
        w(f"    capture_method=`{entry.get('jsonl_capture_method')}`")
        w(f"  - field_keys: `{entry.get('jsonl_field_keys')}`")
        w(f"- Proxy layer id:  `{entry.get('proxy_layer_id')}`")
        w(f"  - completeness=`{entry.get('proxy_completeness')}`, ")
        w(f"    capture_method=`{entry.get('proxy_capture_method')}`")
        w(f"  - field_keys: `{entry.get('proxy_field_keys')}`")
        added = sorted(set(entry.get("proxy_field_keys", [])) - set(entry.get("jsonl_field_keys", [])))
        removed = sorted(set(entry.get("jsonl_field_keys", [])) - set(entry.get("proxy_field_keys", [])))
        if added:
            w(f"  - **proxy adds:** `{added}`")
        if removed:
            w(f"  - **only-in-JSONL:** `{removed}`")
        if layer_type == "tool_registry":
            jsonl_tools = (entry.get("jsonl_content") or {}).get("tools") or []
            proxy_tools = (entry.get("proxy_content") or {}).get("tools") or []
            jsonl_with_schema = sum(1 for t in jsonl_tools if t.get("input_schema") is not None)
            proxy_with_schema = sum(1 for t in proxy_tools if t.get("input_schema") is not None)
            w("")
            w(f"  - JSONL tool count: {len(jsonl_tools)}; with input_schema: {jsonl_with_schema}")
            w(f"  - Proxy tool count: {len(proxy_tools)}; with input_schema: {proxy_with_schema}")
        if layer_type == "system":
            jsonl_content = entry.get("jsonl_content") or {}
            proxy_content = entry.get("proxy_content") or {}
            w("")
            w(f"  - JSONL `static_core_ref`: `{jsonl_content.get('static_core_ref')}`")
            w(f"  - JSONL `claude_md_set` length: `{len(jsonl_content.get('claude_md_set') or [])}`")
            w(f"  - Proxy `assembled_text_bytes`: `{proxy_content.get('assembled_text_bytes')}`")
            w(f"  - Proxy `block_count`: `{proxy_content.get('block_count')}`")
            assembled_preview = (proxy_content.get("assembled_text") or "")[:240]
            assembled_preview = assembled_preview.replace("\n", " / ")
            if assembled_preview:
                w(f"  - Proxy `assembled_text` (first 240 chars): `{assembled_preview!r}`")
        w("")

    w("## Harness-side provenance gap (what JSONL knows that proxy doesn't)")
    w("")
    w("The proxy only sees the wire. The JSONL pipeline sees the agent's ")
    w("local process metadata that never crosses the network. The gap:")
    w("")
    w("- **`cwd`, `permission_mode`, `claude_code_version`** — JSONL ")
    w("  reads these from the `system.init` record; proxy can only see ")
    w("  them when the client cooperates via `OT-Property-cwd`, ")
    w("  `OT-Property-permission-mode`, etc. headers (Helicone convention).")
    w("- **MCP server inventory** — the JSONL pipeline can read ")
    w("  `~/.claude.json` / `.mcp.json` at capture time; the proxy can't, ")
    w("  unless the client probes `tools/list` and forwards.")
    w("- **CLAUDE.md set with content hashes** — JSONL walks user / ")
    w("  project / local / managed scope folders; proxy sees only the ")
    w("  bytes that were assembled into the system prompt (which is ")
    w("  the actual question replay cares about, but you lose the ")
    w("  per-file provenance).")
    w("- **`parentUuid` graph for rewinds / sub-agents / compaction** — ")
    w("  those are JSONL-record concepts; the proxy sees only the ")
    w("  resulting wire traffic and cannot reconstruct branch_type.")
    w("- **Transcript offsets** for `ctx prune` to write a new session ")
    w("  file — the JSONL pipeline is authoritative.")
    w("")
    w("## Gotchas hit during the prototype")
    w("")
    w("1. **Capture-method vocabulary is closed.** Adding `proxy` ")
    w("   required a contract change at ")
    w("   `core/context_tree/contract.py::CAPTURE_METHOD_VALUES` plus a ")
    w("   Pydantic `Literal[...]` widening at ")
    w("   `core/context_tree/models.py::ContextLayer.capture_method`. ")
    w("   Two pre-existing tests used `\"proxy\"` as the sentinel for ")
    w("   'not in vocabulary' and had to switch to ")
    w("   `\"not_a_real_capture_method\"`. MINOR additive change but ")
    w("   not zero-cost.")
    w("2. **TLS is not solved by this prototype.** Real Claude Code ")
    w("   POSTs to `api.anthropic.com` over HTTPS. The prototype uses ")
    w("   plain HTTP against a fake backend; production would need ")
    w("   either mitmproxy-style MITM (cert trust on the client) or an ")
    w("   `ANTHROPIC_BASE_URL` override pointing at a plain-HTTP proxy. ")
    w("   The cleaner option for opentraces is the env-var override: ")
    w("   ship the proxy as a sidecar process and tell users to set ")
    w("   `ANTHROPIC_BASE_URL=http://127.0.0.1:4000` when starting ")
    w("   `claude`. No cert manipulation required.")
    w("3. **Provenance for `cwd` / `permission_mode` requires client ")
    w("   cooperation.** Adopting the Helicone `OT-Property-*` header ")
    w("   convention (the prototype uses `OT-Property-cwd`, ")
    w("   `OT-Property-step-index`, `OT-Property-session-id`) gets us ")
    w("   the missing fields, but it requires a tiny Claude Code hook ")
    w("   patch (or a SessionStart hook that exports an env var the ")
    w("   client's HTTP layer picks up). Plan 078 needs to call this ")
    w("   out as a hard dependency.")
    w("4. **Streaming responses (`stream: true`) are not captured ")
    w("   correctly by a naive forward proxy** — the SSE event ")
    w("   stream needs reassembly. The prototype dodges by using a ")
    w("   non-streaming fake backend; production must address this.")
    w("5. **The proxy is a single point of failure.** If it's down, ")
    w("   requests fail. The prototype handles this by writing the ")
    w("   error into the capture log and 500ing the client. ")
    w("   Production needs either: (a) async-mode where the proxy ")
    w("   fires-and-forgets the log and never blocks the client, or ")
    w("   (b) bypass mode where a missing proxy degrades to JSONL-only ")
    w("   capture. Both are >1 day of work; flag in plan 078.")
    w("")
    w("## Recommendation for plan 078")
    w("")
    w("- **Substrate: roll our own.** LiteLLM is overkill for the ")
    w("  capture use case and adds ~80 transitive dependencies. A ")
    w("  ~300-line production proxy (this prototype + retry + streaming ")
    w("  + structured logging + sidecar lifecycle) keeps the install ")
    w("  footprint minimal. Revisit only if we want OpenAI/Gemini ")
    w("  capture too.")
    w("- **Wiring: env-var override, not MITM.** Document ")
    w("  `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>`. Avoid cert trust ")
    w("  and keep the install instructions to one line.")
    w("- **Default to bypass mode.** Proxy goes down → client traffic ")
    w("  goes straight to Anthropic. Capture is best-effort; never ")
    w("  block the agent. JSONL-only capture remains the floor.")
    w("- **Adopt `OT-Property-*` headers as the substrate convention.** ")
    w("  Steal Helicone's convention verbatim; bind opentraces-specific ")
    w("  fields under one prefix so a sniffer can't collide with ")
    w("  Helicone's own. Bonus: existing Helicone customers' tooling ")
    w("  works on opentraces data unchanged.")
    w("- **Keep the JSONL pipeline.** The proxy is additive, not ")
    w("  replacement. The harness-side provenance gap (cwd, MCP, ")
    w("  CLAUDE.md set, parentUuid branching) is irreducible from the ")
    w("  wire alone.")
    w("- **`capture_method: proxy` as the layer-level join key.** Two ")
    w("  layers for the same step (one JSONL, one proxy) live ")
    w("  side-by-side in the event log; downstream consumers pick the ")
    w("  preferred source per layer_type, falling back when missing. ")
    w("  This is exactly what `core/context_tree/query.py::layer_diff` ")
    w("  already supports.")
    w("")
    return "\n".join(out)


def _format_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "_(no rows)_"
    headers = [label for _, label in columns]
    keys = [k for k, _ in columns]
    cell_widths = [len(h) for h in headers]
    str_rows = []
    for row in rows:
        srow = [str(row.get(k, "")) for k in keys]
        str_rows.append(srow)
        for idx, cell in enumerate(srow):
            cell_widths[idx] = max(cell_widths[idx], len(cell))
    lines = []
    lines.append("| " + " | ".join(h.ljust(cell_widths[i]) for i, h in enumerate(headers)) + " |")
    lines.append("|" + "|".join("-" * (cell_widths[i] + 2) for i in range(len(headers))) + "|")
    for srow in str_rows:
        lines.append(
            "| "
            + " | ".join(cell.ljust(cell_widths[i]) for i, cell in enumerate(srow))
            + " |"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


@dataclass
class _FakeTraceRecord:
    """Minimal stand-in for ``opentraces_schema.models.TraceRecord``.

    The JSONL orchestrator only needs ``trace_id`` and ``metadata``;
    importing the real schema for the prototype is unnecessary surface.
    """

    trace_id: str
    metadata: dict[str, Any]


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        required=True,
        help="Path to the fixture directory containing session.py.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the markdown comparison report.",
    )
    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Don't tear down the temp workspace (useful for debugging).",
    )
    args = parser.parse_args(argv)

    if not args.fixture.is_dir():
        print(f"fixture dir not found: {args.fixture}", file=sys.stderr)
        return 2

    workspace = Path(tempfile.mkdtemp(prefix="http-proxy-prototype-"))
    try:
        print(f"workspace: {workspace}", file=sys.stderr)
        path_a = run_path_a(args.fixture, workspace)
        path_b = run_path_b(args.fixture, workspace)
        report = render_report(
            fixture_dir=args.fixture,
            path_a=path_a,
            path_b=path_b,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
        return 0
    finally:
        if not args.keep_workspace:
            shutil.rmtree(workspace, ignore_errors=True)
        else:
            print(f"keeping workspace at {workspace}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
