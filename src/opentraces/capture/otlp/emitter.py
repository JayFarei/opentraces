"""Emitter glue: MapperResult + raw bodies -> ContextLayer/Node events.

Per plan 078 §"Capture flow". Sibling of the JSONL emitter at
``capture/claude_code/context_tree_capture.py::emit_context_tree_events_from_record``
but reads from OTel-derived session state instead of a parentUuid walker.

Session state is held in :class:`OTLPCaptureBuffer`, accumulated per
``session.id`` as OTLP envelopes arrive and raw-body pairs land on
disk. Calling :func:`flush_session_to_project` walks the buffer, builds
content-addressed layers + nodes via the substrate's own factories,
and appends ``context_*`` events to the canonical Git event log with
``capture_method=[CAPTURE_METHOD_OTEL]``.

The buffer purposely stays project-agnostic. OTel emission carries
``session.id`` and ``prompt.id`` but not the host project path; the
flush call supplies ``project_dir`` + ``trace_id`` at write time so the
emitter never has to guess which Git repo the captured session belongs
to. Wiring (CLI / dataset workflow / OS lifecycle) decides the mapping.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Iterable, Iterator

from ...core.context_tree import CAPTURE_METHOD_OTEL
from ...core.context_tree.raw_blobs import (
    materialize_message_blobs_incremental,
    message_content_hash,
)
from .mapper import map_otlp_envelope, map_raw_request_body

logger = logging.getLogger("opentraces.otlp.emitter")


# --------------------------------------------------------------------------- #
# Per-session accumulator
# --------------------------------------------------------------------------- #


@dataclass
class _SessionState:
    """Accumulated mapper output for one OTel session.id."""

    session_id: str
    runtime_state: dict[str, Any] = field(default_factory=dict)
    plugins: list[dict[str, Any]] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    hooks: list[dict[str, Any]] = field(default_factory=list)
    system_hint: dict[str, Any] | None = None
    nodes_by_prompt: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw_bodies_by_request: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Ordered ``api_request_body`` / ``api_response_body`` body-ref log events
    # (issue #158 W3). Each carries kind / body_ref / sequence / prompt_id (and
    # request_id on responses) so the flush can pair request+response bodies
    # per-llm-request, producing one node per llm-request instead of per turn.
    body_ref_events: list[dict[str, Any]] = field(default_factory=list)
    last_envelope_at: float = field(default_factory=time.time)
    generation: int = 0


class OTLPCaptureBuffer:
    """Thread-safe per-session accumulator for OTel-derived state.

    The receiver's capture callback calls ``handle_envelope``; the
    raw-body watcher's pair callback calls ``handle_body_pair``. Both
    are fast (no I/O, no event-log writes) so the receiver thread
    never blocks on substrate work.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions: dict[str, _SessionState] = {}
        # request_id -> (session_id, prompt_id) reverse index so body
        # pair lookup is O(1) instead of O(sessions x prompts).
        self._request_index: dict[str, tuple[str, str]] = {}

    # ---- ingress ---------------------------------------------------------- #

    def handle_envelope(self, envelope: dict[str, Any]) -> None:
        """Parse one OTLP envelope via the mapper and merge into session state."""
        try:
            result = map_otlp_envelope(envelope)
        except Exception:
            logger.exception("mapper raised on envelope signal=%s", envelope.get("signal"))
            return
        if not result.session_id:
            return
        with self._lock:
            state = self._sessions.setdefault(
                result.session_id, _SessionState(session_id=result.session_id)
            )
            state.generation += 1
            state.last_envelope_at = time.time()
            if result.runtime_state_updates:
                state.runtime_state.update(result.runtime_state_updates)
            if result.tool_registry_updates:
                if "plugins" in result.tool_registry_updates:
                    state.plugins.extend(result.tool_registry_updates["plugins"])
                if "tools" in result.tool_registry_updates:
                    state.runtime_state.setdefault("tools", []).extend(
                        result.tool_registry_updates["tools"]
                    )
            if "mcp_servers" in result.runtime_state_updates:
                state.mcp_servers.extend(result.runtime_state_updates["mcp_servers"])
            if "hooks" in result.runtime_state_updates:
                state.hooks.extend(result.runtime_state_updates["hooks"])
            if result.system_layer_hint and not state.system_hint:
                state.system_hint = dict(result.system_layer_hint)
            if result.node_observation and result.prompt_id:
                merged = state.nodes_by_prompt.setdefault(result.prompt_id, {})
                merged.update(result.node_observation)
                if result.request_id:
                    merged.setdefault("request_id", result.request_id)
                    self._request_index[result.request_id] = (
                        result.session_id, result.prompt_id,
                    )
            if result.body_ref_event:
                state.body_ref_events.append(
                    {**result.body_ref_event, "prompt_id": result.prompt_id}
                )

    def handle_body_pair(
        self, request_id: str, request_body: dict[str, Any], response_body: dict[str, Any]
    ) -> None:
        """Bind a raw-body file pair (from raw_body_watcher) to its session/prompt."""
        decomposed = map_raw_request_body(request_body)
        body_record = {
            "decomposed": decomposed,
            "raw_request": request_body,
            "raw_response": response_body,
        }
        with self._lock:
            indexed = self._request_index.get(request_id)
            if indexed is not None:
                session_id, prompt_id = indexed
                state = self._sessions.get(session_id)
                if state is not None:
                    body_record["prompt_id"] = prompt_id
                    state.raw_bodies_by_request[request_id] = body_record
                    return
            # No matching envelope yet; stash under a synthetic session
            # keyed by request_id so the body isn't lost.
            synthetic_id = f"_orphan_{request_id}"
            synthetic = self._sessions.setdefault(
                synthetic_id, _SessionState(session_id=synthetic_id)
            )
            body_record["prompt_id"] = None
            synthetic.raw_bodies_by_request[request_id] = body_record

    # ---- introspection ---------------------------------------------------- #

    def session_ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions.keys())

    def snapshot_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return None
            return {
                "session_id": state.session_id,
                "runtime_state": dict(state.runtime_state),
                "plugins": list(state.plugins),
                "mcp_servers": list(state.mcp_servers),
                "hooks": list(state.hooks),
                "system_hint": dict(state.system_hint) if state.system_hint else None,
                "nodes_by_prompt": {k: dict(v) for k, v in state.nodes_by_prompt.items()},
                "raw_bodies_by_request": {k: dict(v) for k, v in state.raw_bodies_by_request.items()},
                "body_ref_events": [dict(e) for e in state.body_ref_events],
                "last_envelope_at": state.last_envelope_at,
                "snapshot_generation": state.generation,
                "accepted_envelopes": state.generation,
            }


# --------------------------------------------------------------------------- #
# Layer + node materialization (writer-side, called from flush_session_to_project)
# --------------------------------------------------------------------------- #


def _estimate_context_tokens(usage: dict[str, Any], messages: list[Any]) -> int:
    """Per-step context-size estimate: prefer wire usage, fall back to chars.

    The wire ``usage`` block on the response is the authoritative count of
    what the model actually saw (input + both cache tiers). When no response
    paired, fall back to the JSONL-path heuristic (``len(text)//4``).
    """
    if usage:
        total = 0
        for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            v = usage.get(key)
            if isinstance(v, (int, float)):
                total += int(v)
        if total:
            return total
    chars = 0
    for msg in messages:
        try:
            chars += len(json.dumps(msg, ensure_ascii=False))
        except (TypeError, ValueError):
            continue
    return chars // 4


def _build_messages_manifest(messages: list[Any]) -> tuple[list[dict[str, Any]], str, str]:
    """Build the per-step messages manifest (JSONL-parity shape).

    Each entry is ``{role, uuid, parent_uuid, content_hash}``. The wire has no
    per-message uuids, so we use the message's own ``content_hash`` as ``uuid``
    and the prior entry's as ``parent_uuid`` — deterministic, content-addressed,
    and chainable. ``content_hash`` is the load-bearing field: it indexes the
    raw/ content blob materialized alongside.
    """
    entries: list[dict[str, Any]] = []
    prev_hash: str | None = None
    for msg in messages:
        content_hash = message_content_hash(msg)
        entries.append(
            {
                "role": msg.get("role") if isinstance(msg, dict) else None,
                "uuid": content_hash,
                "parent_uuid": prev_hash,
                "content_hash": content_hash,
            }
        )
        prev_hash = content_hash
    span_first = entries[0]["uuid"] if entries else ""
    span_last = entries[-1]["uuid"] if entries else ""
    return entries, span_first, span_last


def _build_layers_and_nodes_from_steps(
    steps: Iterable[dict[str, Any]],
    *,
    trace_id: str,
    project_slug: str,
) -> tuple[list[Any], list[Any], dict[str, Any]]:
    """Build per-llm-request ContextLayer + ContextNode objects + raw blobs.

    ``steps`` is an ordered *iterable* of per-llm-request dicts (one node each),
    each carrying ``request_body`` (system / tools / messages the model saw) and an
    optional ``response_body`` (usage / output). This is the per-step shape both
    the raw-body reconstruction path and the live buffer produce — keying by
    llm-request (not per-turn ``prompt.id``) so two steps in one turn become two
    distinct nodes with distinct message manifests.

    Streams the input (issue #252): each step's (wire-cumulative) body is consumed
    to build its layers/node, its per-message blobs are materialized *incrementally*
    against a shared content-hash set, and the body is then dropped before the next
    step — so peak memory is ``O(one body)``, not ``O(sum of all bodies)``. The
    resulting layers/nodes hold only content-addressed manifests (message *text*
    lives on disk in the raw/ blobs), so accumulating them stays ``O(N x small)``.

    Materializes per-message raw content blobs under ``project_slug`` so each
    manifest ``content_hash`` resolves to its text (content-addressed, deduped
    across steps). Returns ``(layers, nodes, blob_report)``.
    """
    from ...core.context_tree import build_layer, build_node

    layers: list[Any] = []
    nodes: list[Any] = []
    # Shared across all steps so dedup counting matches a one-shot
    # ``materialize_message_blobs`` over the concatenated payloads.
    seen_hashes: dict[str, None] = {}
    blobs_written = 0

    parent_id: str | None = None
    for idx, step in enumerate(steps):
        request_body = step.get("request_body") or {}
        response_body = step.get("response_body") or {}
        has_body = bool(request_body)
        system_blocks = request_body.get("system") or []
        messages = request_body.get("messages") or []
        tools = request_body.get("tools") or []
        usage = (response_body or {}).get("usage") or {}

        manifest, span_first, span_last = _build_messages_manifest(messages)

        runtime_state_content: dict[str, Any] = {
            "model": request_body.get("model"),
            "max_tokens": request_body.get("max_tokens"),
            "stream": request_body.get("stream"),
            "temperature": request_body.get("temperature"),
            "top_p": request_body.get("top_p"),
            "top_k": request_body.get("top_k"),
            "usage": usage,
        }

        layer_specs = (
            (
                "system",
                {
                    "assembled_prompt": system_blocks,
                    "block_count": len(system_blocks),
                },
                bool(has_body and system_blocks),
            ),
            (
                "messages",
                {
                    "messages": manifest,
                    "total_token_estimate": _estimate_context_tokens(usage, messages),
                    "span_first_uuid": span_first,
                    "span_last_uuid": span_last,
                    "is_summary": False,
                    "summary_of_span": None,
                    "finish_reasons": step.get("finish_reasons") or [],
                },
                bool(has_body and manifest),
            ),
            (
                "tool_registry",
                {
                    "tools": tools,
                    "tool_names": [t.get("name") for t in tools if isinstance(t, dict)],
                    "plugins": [],
                    "plugin_names": [],
                },
                bool(has_body and tools),
            ),
            (
                "runtime_state",
                runtime_state_content,
                bool(has_body and runtime_state_content.get("model")),
            ),
        )
        built = {
            layer_type: build_layer(
                layer_type=layer_type,
                content=content,
                completeness="full" if full_gate else "approximated",
                capture_method=CAPTURE_METHOD_OTEL,
            )
            for layer_type, content, full_gate in layer_specs
        }
        layers.extend(built.values())

        node = build_node(
            parent_node_id=parent_id,
            branch_type="root" if parent_id is None else "linear",
            trace_id=trace_id,
            transcript_uuid=step.get("transcript_uuid") or f"otel-step-{idx}",
            transcript_offset=idx,
            step_index=step.get("step_index", idx),
            system_layer_id=built["system"].layer_id,
            messages_layer_id=built["messages"].layer_id,
            tool_registry_layer_id=built["tool_registry"].layer_id,
            runtime_state_layer_id=built["runtime_state"].layer_id,
            capture_completeness="full" if has_body else "approximated",
        )
        nodes.append(node)
        parent_id = node.node_id

        # Materialize this step's per-message content blobs immediately (the one
        # missing write): each manifest content_hash resolves to its text, deduped
        # across steps via the shared ``seen_hashes`` set. Done per-step so the
        # message payloads are never accumulated across the whole session.
        blobs_written += materialize_message_blobs_incremental(
            project_slug, messages, seen=seen_hashes
        )

        # Drop this step's body before the next iteration so peak memory stays
        # O(one body) — nothing above retains ``request_body`` / ``messages``
        # (layers hold only content-addressed manifests, not message text).
        del step, request_body, response_body, messages, system_blocks, tools

    blob_report = {
        "written": blobs_written,
        "deduped": len(seen_hashes),
        "content_hashes": list(seen_hashes.keys()),
    }
    return layers, nodes, blob_report


def _steps_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Adapt a legacy buffer snapshot (per-prompt) into per-step inputs.

    Used for the in-process buffer / staging-snapshot path. The legacy snapshot
    keyed nodes by ``prompt.id`` (per-turn) with bodies in
    ``raw_bodies_by_request``; when bodies are absent the steps reconstruct as
    ``approximated`` (the honest empty-capture state). The live receiver fix
    (W3) populates per-step body refs so this path also becomes per-llm-request.
    """
    raw_bodies = snapshot.get("raw_bodies_by_request") or {}
    steps: list[dict[str, Any]] = []
    for idx, (prompt_id, draft) in enumerate(snapshot.get("nodes_by_prompt", {}).items()):
        request_id = draft.get("request_id")
        decomposed = raw_bodies.get(request_id, {}).get("decomposed") if request_id else None
        request_body: dict[str, Any] = {}
        if decomposed:
            request_body = {
                "system": decomposed.get("system"),
                "messages": decomposed.get("messages"),
                "tools": decomposed.get("tools"),
                **(decomposed.get("runtime_params") or {}),
            }
        steps.append(
            {
                "step_index": idx,
                "transcript_uuid": draft.get("transcript_uuid") or prompt_id,
                "request_id": request_id,
                "request_body": request_body,
                "response_body": None,
                "finish_reasons": draft.get("finish_reasons") or [],
            }
        )
    return steps


def _read_json_file(path: Path | None) -> Any | None:
    """Defensively read+parse one raw-body JSON file. Missing/invalid -> None."""
    if path is None:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _resolve_body_path(body_ref: Any, raw_bodies_dir: Path | None) -> Path | None:
    """Resolve a body_ref (absolute path or bare filename) to an existing file.

    Claude Code's ``OTEL_LOG_RAW_API_BODIES=file:<dir>`` emits ``body_ref`` as a
    path. Prefer it verbatim when absolute and present; otherwise resolve its
    filename against ``raw_bodies_dir`` (the dir the receiver is configured for).
    """
    if not body_ref:
        return None
    p = Path(str(body_ref))
    if p.is_absolute() and p.exists():
        return p
    if raw_bodies_dir is not None:
        cand = Path(raw_bodies_dir) / p.name
        if cand.exists():
            return cand
    if p.exists():
        return p
    return None


def _seq_key(value: Any) -> tuple[int, int]:
    """Sort key for an ``event.sequence``; missing sequences sort last, stably."""
    if isinstance(value, int) and not isinstance(value, bool):
        return (0, value)
    return (1, 0)


def _steps_from_body_ref_events(
    events: list[dict[str, Any]], raw_bodies_dir: Path | None
) -> Iterator[dict[str, Any]]:
    """Build per-llm-request steps from OTel api_*_body body-ref log events (W3).

    Each ``api_request_body`` log marks one llm-request (one node); the matching
    ``api_response_body`` log carries the canonical ``request_id`` and the
    per-step ``usage``. Events are grouped by ``prompt.id`` (per-turn) and
    ordered by ``event.sequence`` within the turn; each request pairs with the
    first not-yet-consumed response after it in that ordering (a response is
    consumed on match, so two requests can never bind to the same response).

    Streams (issue #252): the request/response *body-ref pairing* runs eagerly
    (the events are tiny), but the full request / response bodies are read off
    disk lazily — one step at a time, at yield time — so the whole session's
    (wire-cumulative) bodies are never held at once. A missing body file yields
    an empty body for that side.

    Steps are globally ordered by request ``event.sequence`` (session-monotonic)
    so ``step_index`` reflects true chronological per-llm-request order. Yields
    dicts matching ``ReconstructedStep.to_snapshot_step``'s shape, so they feed
    ``_build_layers_and_nodes_from_steps`` unchanged — turning the live snapshot
    path into per-llm-request capture instead of per-turn.
    """
    groups: dict[Any, list[dict[str, Any]]] = {}
    for ev in events:
        groups.setdefault(ev.get("prompt_id"), []).append(ev)

    # Eagerly pair requests to responses using only the tiny ref events (no body
    # reads yet). Each entry carries the resolved body-ref paths + ids.
    pairings: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for group in groups.values():
        ordered = sorted(group, key=lambda e: _seq_key(e.get("sequence")))
        consumed: set[int] = set()
        for idx, ev in enumerate(ordered):
            if ev.get("kind") != "request":
                continue
            response = None
            for resp_idx in range(idx + 1, len(ordered)):
                if resp_idx in consumed:
                    continue
                if ordered[resp_idx].get("kind") == "response":
                    response = ordered[resp_idx]
                    consumed.add(resp_idx)
                    break
            ref_name = Path(str(ev.get("body_ref") or "")).name
            pairings.append(
                (
                    _seq_key(ev.get("sequence")),
                    {
                        "request_body_ref": ev.get("body_ref"),
                        "ref_name": ref_name,
                        "response_body_ref": response.get("body_ref") if response else None,
                        "request_id": response.get("request_id") if response else None,
                    },
                )
            )

    pairings.sort(key=lambda t: t[0])

    def _generate() -> Iterator[dict[str, Any]]:
        for step_index, (_key, meta) in enumerate(pairings):
            request_body = _read_json_file(
                _resolve_body_path(meta["request_body_ref"], raw_bodies_dir)
            )
            if not isinstance(request_body, dict):
                request_body = {}
            response_body = None
            if meta["response_body_ref"] is not None:
                response_body = _read_json_file(
                    _resolve_body_path(meta["response_body_ref"], raw_bodies_dir)
                )
                if not isinstance(response_body, dict):
                    response_body = None
            # request_id comes from the response *event* (present whenever a
            # response was paired), independent of whether its body parsed.
            request_id = meta["request_id"]
            finish_reasons: list[str] = []
            if response_body and response_body.get("stop_reason"):
                finish_reasons = [response_body["stop_reason"]]
            ref_name = meta["ref_name"]
            transcript_uuid = (
                ref_name[: -len(".request.json")]
                if ref_name.endswith(".request.json")
                else (request_id or ref_name or None)
            )
            previous_message_id = (request_body.get("diagnostics") or {}).get(
                "previous_message_id"
            )
            yield {
                "step_index": step_index,
                "transcript_uuid": transcript_uuid,
                "request_id": request_id,
                "request_body": request_body,
                "response_body": response_body,
                "previous_message_id": previous_message_id,
                "finish_reasons": finish_reasons,
            }

    return _generate()


# --------------------------------------------------------------------------- #
# Public flush — write accumulated OTel state to a project's event log
# --------------------------------------------------------------------------- #


def load_snapshot_from_disk(path: Path) -> dict[str, Any]:
    """Read a session snapshot JSON file previously written by ``write_snapshot``."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def flush_session_to_project(
    *,
    project_dir: Path,
    trace_id: str,
    session_id: str,
    buffer: OTLPCaptureBuffer | None = None,
    snapshot: dict[str, Any] | None = None,
    steps: Iterable[dict[str, Any]] | None = None,
    raw_bodies_dir: Path | None = None,
    raw_body_retention: str | None = None,
    project_to_bucket: bool = True,
) -> dict[str, Any]:
    """Append context_* events for one OTel session into project_dir's event log.

    ``steps`` may be a one-shot generator (issue #252): it is consumed exactly
    once, by ``_build_layers_and_nodes_from_steps``, which streams it so a whole
    wire-cumulative session is never materialized at once. Do not re-iterate it.

    Either ``buffer`` (in-process) or ``snapshot`` (cross-process,
    rehydrated from disk via :func:`load_snapshot_from_disk`) must be
    supplied. Returns a small report dict the CLI / doctor surface uses:
    ``{ok, session_id, trace_id, layers_count, nodes_count, drafts_appended,
    raw_bodies_deleted}``.

    Uses the substrate's own ``append_event_batch`` with
    ``capture_method=[CAPTURE_METHOD_OTEL]`` and ``writer="otlp-receiver"``.

    Plan 080 §5 raw-body lifecycle: on the success path (events
    appended, ``ok=True``), the source raw-body JSON pairs under
    ``raw_bodies_dir/<request_id>.{request,response}.json`` are deleted
    when ``raw_body_retention`` is ``"delete"`` (default). For
    ``keep_*`` policies the files are left in place; the watcher's
    periodic sweep enforces the TTL. Deletion is best-effort: a
    missing file or OS error never fails the flush.
    """
    from ...core.context_tree import (
        CONTEXT_LAYER_CAPTURED,
        CONTEXT_NODE_OBSERVED,
        CONTEXT_TREE_RECONCILED,
    )
    from ...core.trails.event_log import append_event_batch
    from ...core.trails.models import TrailEventDraft

    # Resolve the per-step inputs from whichever source was supplied:
    #   steps=     explicit per-llm-request list (raw-body reconstruction, W1)
    #   snapshot=  cross-process staging snapshot (live daemon)
    #   buffer=    in-process accumulator
    if steps is None:
        if snapshot is None:
            if buffer is None:
                return {"ok": False, "reason": "no-source", "session_id": session_id}
            snapshot = buffer.snapshot_session(session_id)
        if snapshot is None:
            return {"ok": False, "reason": "no-such-session", "session_id": session_id}
        # Per-llm-request capture (W3): when the live receiver recorded
        # api_*_body body-ref log events, pair request+response bodies per
        # llm-request so one turn with N llm-requests becomes N distinct nodes.
        # Otherwise fall back to the legacy per-prompt snapshot adaptation.
        body_ref_events = snapshot.get("body_ref_events") or []
        if body_ref_events:
            resolved_raw_dir = (
                Path(raw_bodies_dir)
                if raw_bodies_dir is not None
                else _default_raw_bodies_dir()
            )
            steps = _steps_from_body_ref_events(body_ref_events, resolved_raw_dir)
        else:
            steps = _steps_from_snapshot(snapshot)

    project_slug = _project_slug_for(project_dir)
    layers, nodes, blob_report = _build_layers_and_nodes_from_steps(
        steps, trace_id=trace_id, project_slug=project_slug
    )
    if not nodes:
        return {
            "ok": False,
            "reason": "no-nodes-built",
            "session_id": session_id,
            "trace_id": trace_id,
            "layers_count": len(layers),
            "nodes_count": 0,
        }

    drafts: list[TrailEventDraft] = []
    seen_layer_ids: set[str] = set()
    for layer in layers:
        if layer.layer_id in seen_layer_ids:
            continue
        seen_layer_ids.add(layer.layer_id)
        drafts.append(TrailEventDraft(
            event_type=CONTEXT_LAYER_CAPTURED,
            payload=layer.model_dump(mode="json"),
            trace_id=trace_id,
            capture_method=[CAPTURE_METHOD_OTEL],
        ))
    for node in nodes:
        drafts.append(TrailEventDraft(
            event_type=CONTEXT_NODE_OBSERVED,
            payload=node.model_dump(mode="json"),
            trace_id=trace_id,
            step_index=node.step_index,
            capture_method=[CAPTURE_METHOD_OTEL],
        ))
    drafts.append(TrailEventDraft(
        event_type=CONTEXT_TREE_RECONCILED,
        payload={
            "trace_id": trace_id,
            "node_count": len(nodes),
            "layer_count": len(seen_layer_ids),
            "active_path_leaf_id": nodes[-1].node_id,
            "orphan_branch_roots": [],
            "subagent_session_ids": [],
            "capture_limitations": [],
            "source": "otel",
            "session_id": session_id,
        },
        trace_id=trace_id,
        capture_method=[CAPTURE_METHOD_OTEL],
    ))

    appended = append_event_batch(project_dir, drafts, writer="otlp-receiver")

    # Project the just-appended events into the bucket companion so ctx reads
    # are bucket-backed (and survive a hidden git ref — issue #158 W4/DoD#5).
    # The watcher's own projection is watermark- AND change-gated and can skip
    # an OTel-only change, so flush projects directly. Best-effort.
    projected = False
    if project_to_bucket:
        try:
            from ...core.bucket_store import project_context_tree_to_bucket

            project_context_tree_to_bucket(
                Path(project_dir), project_slug=project_slug, trace_id=trace_id
            )
            projected = True
        except Exception:  # noqa: BLE001 - projection failure must not lose events
            logger.warning("bucket projection after flush failed", exc_info=True)

    # Cross-substrate spine join (W4): stamp context_tree_summary +
    # Step.context_node_id on the target trace's bucket envelope when it
    # exists. No-op when the trace has no JSONL-side envelope yet.
    spine_joined = False
    try:
        spine_joined = _join_trace_spine(
            project_slug=project_slug,
            trace_id=trace_id,
            session_id=session_id,
            nodes=nodes,
        )
    except Exception:  # noqa: BLE001
        logger.warning("spine join after flush failed", exc_info=True)

    # Plan 080 §5: delete raw-body source files on success when
    # retention policy is "delete" (default). Only the live snapshot path
    # records request_ids; the raw-body reconstruction path never deletes
    # the captain's captured bodies.
    resolved_dir = Path(raw_bodies_dir) if raw_bodies_dir is not None else _default_raw_bodies_dir()
    resolved_policy = _resolve_retention_policy(raw_body_retention)
    raw_bodies_deleted = 0
    raw_bodies_kept = 0
    request_ids = sorted((snapshot or {}).get("raw_bodies_by_request", {}).keys())
    if resolved_dir is not None and request_ids:
        if resolved_policy == "delete":
            raw_bodies_deleted = _delete_raw_body_pairs(resolved_dir, request_ids)
        else:
            raw_bodies_kept = len(request_ids)

    return {
        "ok": True,
        "session_id": session_id,
        "trace_id": trace_id,
        "layers_count": len(seen_layer_ids),
        "nodes_count": len(nodes),
        "full_nodes": sum(
            node.capture_completeness == "full" for node in nodes
        ),
        "approximated_nodes": sum(
            node.capture_completeness == "approximated" for node in nodes
        ),
        "stub_nodes": sum(
            node.capture_completeness == "stub" for node in nodes
        ),
        "drafts_appended": len(appended),
        "message_blobs_written": blob_report.get("written", 0),
        "message_blobs_deduped": blob_report.get("deduped", 0),
        "projected_to_bucket": projected,
        "spine_joined": spine_joined,
        "raw_bodies_deleted": raw_bodies_deleted,
        "raw_bodies_kept": raw_bodies_kept,
        "raw_body_retention": resolved_policy,
    }


def _project_slug_for(project_dir: Path) -> str:
    """Project slug used for context bucket paths (matches ingest.py:446)."""
    try:
        from ...core.config import get_project_dir

        return get_project_dir(Path(project_dir)).name
    except Exception:  # noqa: BLE001
        return Path(project_dir).name


def _join_trace_spine(
    *,
    project_slug: str,
    trace_id: str,
    session_id: str,
    nodes: list[Any],
) -> bool:
    """Stamp the trace's bucket envelope with the OTel context-tree linkage.

    Sets only ``TraceRecord.context_tree_summary`` on the already-persisted
    ``trace.json`` envelope, so OTel-flushed context becomes visible to
    ``trace get`` / ``trace map``. Returns False (no-op) when the trace has no
    envelope yet — the ctx nodes still carry ``trace_id`` + ``step_index`` so
    the cross-substrate key is shared regardless.

    ``Step.context_node_id`` is intentionally NOT stamped here: the OTel
    reconstruction ``step_index`` is a per-llm-request ordinal that does not
    share the JSONL ``Step.step_index`` domain, so a raw-equality stamp would
    attach OTel nodes to unrelated steps and clobber the authoritative JSONL
    spine link set at ingest time. The forward join (ctx nodes carry
    ``trace_id`` + ``step_index``) is unaffected.

    The field written here (``context_tree_summary``) is OUTSIDE the per-trace
    ``digest_material`` (bucket_envelope.py), so this rewrite is
    bucket_digest-invariant. Serialization matches
    ``_write_per_trace_envelope`` (``record.model_dump(mode="json")`` via
    ``_atomic_write_json``) so the bytes stay canonical.
    """
    import json as _json

    from opentraces_schema import TraceRecord

    from ...core._bucket_io import _atomic_write_json
    from ...core.bucket_layout import trace_v1_json_path

    path = trace_v1_json_path(project_slug, trace_id)
    if not path.exists():
        return False
    try:
        raw = _json.loads(path.read_text(encoding="utf-8"))
        record = TraceRecord.model_validate(raw)
    except (OSError, ValueError):
        return False

    summary = dict(getattr(record, "context_tree_summary", None) or {})
    summary.update(
        {
            "otel_node_count": len(nodes),
            "otel_active_path_leaf_id": nodes[-1].node_id if nodes else None,
            "session_id": session_id,
            "otel_flushed": True,
        }
    )
    methods = list(summary.get("capture_methods") or [])
    if CAPTURE_METHOD_OTEL not in methods:
        methods.append(CAPTURE_METHOD_OTEL)
    summary["capture_methods"] = methods
    record.context_tree_summary = summary
    _atomic_write_json(path, record.model_dump(mode="json"))
    return True


def _default_raw_bodies_dir() -> Path | None:
    """Best-effort lookup of the default raw bodies dir.

    Returns None if `core.paths` is unavailable (treat as "no dir to
    clean up"). Used when the caller does not explicitly pass
    ``raw_bodies_dir`` so the default-delete behavior still applies.
    """
    try:
        from ...core.paths import raw_bodies_dir as _rb_dir
    except Exception:
        return None
    try:
        return _rb_dir()
    except Exception:
        return None


def _resolve_retention_policy(explicit: str | None) -> str:
    """Return the retention policy string. Falls back to config or "delete"."""
    if explicit:
        return explicit
    try:
        from ...core.config import (
            get_capture_otlp_raw_body_retention,
            load_config,
        )
    except Exception:
        return "delete"
    try:
        return get_capture_otlp_raw_body_retention(load_config())
    except Exception:
        return "delete"


def _delete_raw_body_pairs(raw_bodies_dir: Path, request_ids: list[str]) -> int:
    """Delete ``<request_id>.{request,response}.json`` pairs. Best-effort.

    Returns the count of files actually removed (0..2N). Missing files
    or OS errors are logged at DEBUG and swallowed so the flush success
    path is never broken by a stale watcher / cleanup race.
    """
    if not raw_bodies_dir.exists():
        return 0
    deleted = 0
    for rid in request_ids:
        for suffix in (".request.json", ".response.json"):
            p = raw_bodies_dir / f"{rid}{suffix}"
            try:
                p.unlink()
                deleted += 1
            except FileNotFoundError:
                continue
            except OSError as exc:
                logger.debug("could not delete raw body %s: %s", p, exc)
    return deleted


# --------------------------------------------------------------------------- #
# Persistence helper — write a snapshot to disk for inspection / replay
# --------------------------------------------------------------------------- #


def write_snapshot(
    buffer: OTLPCaptureBuffer,
    session_id: str,
    out_path: Path,
    *,
    quiescent: bool = False,
) -> bool:
    """Dump a session snapshot to JSON. Used by `capture-otlp status --dump`."""
    snap = buffer.snapshot_session(session_id)
    if snap is None:
        return False
    snap["snapshot_quiescent"] = quiescent
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
    tmp.replace(out_path)
    return True
