"""Issue #252 — the OTel session flush must stream, not materialize the session.

Claude Code request bodies are *wire-cumulative*: step ``k``'s body carries every
message up to ``k``. So a K-step session's bodies sum to ``O(K^2)`` content even
though the largest single body is only ``O(K)``. The pre-fix flush loaded every
parsed body at once (``reconstruct`` returned a list of full bodies;
``_steps_from_body_ref_events`` read every body up front; the builder accumulated
``all_message_payloads``; and the CLI ``--from-raw-bodies`` path re-materialized
them all in a list comprehension), so peak RSS scaled with the whole session and
bursts to tens of GB on real corpora.

These tests pin the fix: peak *traced* Python memory ATTRIBUTABLE TO THE FLUSH must
stay ``O(largest single body)`` — bounded by ``6 x max-single-body`` (a generous
JSON inflation factor) — never the ``O(K^2)`` sum. They are hermetic (tmp_path +
the autouse tmp-HOME isolation in ``conftest.py``; no real ``~/.opentraces``).

Robustness: memory is measured as the *delta* of traced allocations across the
flush (not an absolute peak), lazy imports / model builds are warmed OUTSIDE the
measured window, and a ``gc.collect()`` precedes each measurement — so the tests
give the same verdict in isolation and inside a full-suite run where another test
may have left ``tracemalloc`` running with live allocations.
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
import random
import string
import subprocess
import tracemalloc
import uuid
from pathlib import Path
from typing import Callable


# --------------------------------------------------------------------------- #
# measurement: allocation delta across a callable, baseline-independent
# --------------------------------------------------------------------------- #


def _peak_delta(fn: Callable[[], object]) -> tuple[object, int]:
    """Run ``fn`` and return ``(result, peak_traced_bytes_allocated_by_fn)``.

    Measures the DELTA between the traced-memory peak during ``fn`` and the live
    traced baseline immediately before it, so allocations left tracked by earlier
    tests (when ``tracemalloc`` is already running) never inflate the number.
    Starts/stops the tracer only if it was not already running.
    """
    was_tracing = tracemalloc.is_tracing()
    if not was_tracing:
        tracemalloc.start()
    try:
        gc.collect()
        before_current, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        result = fn()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        if not was_tracing:
            tracemalloc.stop()
    return result, max(0, peak - before_current)


# --------------------------------------------------------------------------- #
# synthetic wire-cumulative session (UUID-named requests, req_<id> responses)
# --------------------------------------------------------------------------- #


def _big_text(index: int, n_bytes: int) -> str:
    """Deterministic, ~unique, low-redundancy text of ~n_bytes for message ``index``."""
    rnd = random.Random(90_000 + index)
    alphabet = string.ascii_letters + string.digits
    return f"m{index}:" + "".join(rnd.choice(alphabet) for _ in range(n_bytes))


def _write_big_session(
    raw_dir: Path, session_id: str, *, k_steps: int, msg_bytes: int
) -> tuple[list[Path], list[Path], int]:
    """Lay down a wire-cumulative session; return (request_paths, response_paths, max_body_bytes).

    Message ``i`` is the SAME across steps (a cumulative prefix), so total unique
    content is ``k_steps * msg_bytes`` while the sum of all bodies is
    ``~ k_steps^2/2 * msg_bytes``. Requests are UUID-named; responses are
    ``req_<id>``-named and chained via ``diagnostics.previous_message_id`` == the
    prior response ``id`` — the exact filename mismatch reconstruct pairs around.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    messages_pool = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": _big_text(i, msg_bytes)}
        for i in range(k_steps)
    ]
    req_paths: list[Path] = []
    resp_paths: list[Path] = []
    prev_msg: str | None = None
    base = 1_000_000.0
    max_body = 0
    for k in range(k_steps):
        req = {
            "model": "claude-opus-4-8",
            "system": [{"type": "text", "text": "you are a test"}],
            "tools": [{"name": "Read", "input_schema": {"type": "object", "properties": {}}}],
            "messages": messages_pool[: k + 1],
            "max_tokens": 64000,
            "metadata": {"user_id": json.dumps({"session_id": session_id})},
            "diagnostics": {"previous_message_id": prev_msg},
        }
        req_path = raw_dir / f"{uuid.uuid4()}.request.json"
        blob = json.dumps(req)
        req_path.write_text(blob)
        max_body = max(max_body, len(blob.encode("utf-8")))
        os.utime(req_path, (base + k, base + k))
        req_paths.append(req_path)

        produced = f"msg_{k:04d}_{uuid.uuid4().hex[:8]}"
        resp = {
            "id": produced,
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": f"reply {k}"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10 + k, "output_tokens": 5, "cache_read_input_tokens": k},
        }
        resp_path = raw_dir / f"req_{produced}.response.json"
        resp_path.write_text(json.dumps(resp))
        resp_paths.append(resp_path)
        prev_msg = produced
    return req_paths, resp_paths, max_body


def _warm_builder(tmp_path: Path) -> None:
    """Run reconstruct + builder once on a tiny session to warm lazy imports.

    ``_build_layers_and_nodes_from_steps`` lazily imports ``build_layer`` /
    ``build_node`` and triggers first-time pydantic model construction + gzip
    machinery; doing it here keeps those one-time allocations OUT of the measured
    window so the delta reflects only per-body streaming cost.
    """
    from opentraces.capture.otlp.emitter import _build_layers_and_nodes_from_steps
    from opentraces.capture.otlp.reconstruct import reconstruct_steps_from_raw_bodies

    warm_raw = tmp_path / "warm-raw"
    sid = "00000000-0000-0000-0000-000000000000"
    _write_big_session(warm_raw, sid, k_steps=2, msg_bytes=1_000)
    steps_iter = (s.to_snapshot_step() for s in reconstruct_steps_from_raw_bodies(sid, warm_raw))
    _build_layers_and_nodes_from_steps(
        steps_iter, trace_id="warm", project_slug="proj-warm"
    )
    gc.collect()


# --------------------------------------------------------------------------- #
# memory: reconstruct -> builder must stay O(largest body)
# --------------------------------------------------------------------------- #


def test_reconstruct_flush_streams_memory(tmp_path):
    from opentraces.capture.otlp.emitter import _build_layers_and_nodes_from_steps
    from opentraces.capture.otlp.reconstruct import reconstruct_steps_from_raw_bodies

    _warm_builder(tmp_path)

    raw = tmp_path / "raw-bodies"
    sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    k_steps, msg_bytes = 25, 400_000
    _reqs, _resps, max_body = _write_big_session(raw, sid, k_steps=k_steps, msg_bytes=msg_bytes)
    bound = max_body * 6

    def _run():
        steps_iter = (s.to_snapshot_step() for s in reconstruct_steps_from_raw_bodies(sid, raw))
        return _build_layers_and_nodes_from_steps(
            steps_iter, trace_id="trace-mem", project_slug="proj-mem-recon"
        )

    (layers, nodes, blob_report), peak = _peak_delta(_run)

    assert len(nodes) == k_steps
    # A streamed flush peaks at ~one body; a materialized one peaks at the O(K^2) sum.
    assert peak < bound, (
        f"peak={peak/1e6:.1f}MB exceeded bound={bound/1e6:.1f}MB "
        f"(max_body={max_body/1e6:.2f}MB, K={k_steps}); "
        f"the flush is materializing the whole session, not streaming it"
    )


def test_body_ref_flush_streams_memory(tmp_path):
    from opentraces.capture.otlp.emitter import (
        _build_layers_and_nodes_from_steps,
        _steps_from_body_ref_events,
    )

    _warm_builder(tmp_path)

    raw = tmp_path / "raw-bodies"
    sid = "11112222-3333-4444-5555-666677778888"
    k_steps, msg_bytes = 25, 400_000
    req_paths, resp_paths, max_body = _write_big_session(
        raw, sid, k_steps=k_steps, msg_bytes=msg_bytes
    )
    bound = max_body * 6

    # Build the body-ref log the live receiver would have accumulated: one
    # request + one response event per llm-request, ordered by sequence.
    events: list[dict] = []
    for k, (rq, rs) in enumerate(zip(req_paths, resp_paths)):
        events.append(
            {"kind": "request", "body_ref": str(rq), "sequence": 2 * k, "prompt_id": f"p{k}"}
        )
        events.append(
            {
                "kind": "response",
                "body_ref": str(rs),
                "sequence": 2 * k + 1,
                "prompt_id": f"p{k}",
                "request_id": f"req_{k:04d}",
            }
        )

    def _run():
        steps_iter = _steps_from_body_ref_events(events, raw)
        return _build_layers_and_nodes_from_steps(
            steps_iter, trace_id="trace-mem2", project_slug="proj-mem-bodyref"
        )

    (layers, nodes, blob_report), peak = _peak_delta(_run)

    assert len(nodes) == k_steps
    assert peak < bound, (
        f"peak={peak/1e6:.1f}MB exceeded bound={bound/1e6:.1f}MB "
        f"(max_body={max_body/1e6:.2f}MB, K={k_steps}); body-ref path not streaming"
    )


def test_cli_from_raw_bodies_flush_streams_memory(tmp_path):
    """The path exactly as ``capture-otlp flush --from-raw-bodies`` drives it.

    The CLI now hands ``flush_session_to_project`` a *generator* over
    ``reconstruct_steps_from_raw_bodies`` (not a list comprehension), so the whole
    end-to-end flush — reconstruct -> builder -> event log -> bucket projection —
    must stay bounded by ``6 x max-single-body``, never the ``O(K^2)`` session sum.
    """
    from opentraces.capture.otlp.emitter import flush_session_to_project
    from opentraces.capture.otlp.reconstruct import reconstruct_steps_from_raw_bodies

    _warm_builder(tmp_path)

    # A real git repo so append_event_batch has refs/ (mirrors the other suites).
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
    (project / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "README.md"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=project, check=True)

    raw = tmp_path / "raw-bodies"
    sid = "deadbeef-1111-2222-3333-444455556666"
    k_steps, msg_bytes = 25, 400_000
    _reqs, _resps, max_body = _write_big_session(raw, sid, k_steps=k_steps, msg_bytes=msg_bytes)
    bound = max_body * 6

    def _run():
        # Byte-for-byte the CLI call site (cli/capture_otlp.py): a generator, so
        # the session is never materialized in a list.
        recon = reconstruct_steps_from_raw_bodies(sid, raw)
        return flush_session_to_project(
            project_dir=project,
            trace_id="trace-cli",
            session_id=sid,
            steps=(s.to_snapshot_step() for s in recon),
            raw_bodies_dir=raw,
            raw_body_retention="keep_all",  # do not delete the fixture mid-run
        )

    report, peak = _peak_delta(_run)

    assert report["ok"] is True
    assert report["nodes_count"] == k_steps
    assert peak < bound, (
        f"peak={peak/1e6:.1f}MB exceeded bound={bound/1e6:.1f}MB "
        f"(max_body={max_body/1e6:.2f}MB, K={k_steps}); the CLI --from-raw-bodies "
        f"flush is materializing the whole session, not streaming it"
    )


# --------------------------------------------------------------------------- #
# correctness: the streamed path produces the same well-formed output
# --------------------------------------------------------------------------- #


def test_streamed_reconstruct_correctness(tmp_path):
    from opentraces.capture.otlp.emitter import _build_layers_and_nodes_from_steps
    from opentraces.capture.otlp.reconstruct import reconstruct_steps_from_raw_bodies
    from opentraces.core.context_tree.raw_blobs import deref_message_content

    raw = tmp_path / "raw-bodies"
    sid = "cafecafe-0000-1111-2222-333344445555"
    _reqs, _resps, _max = _write_big_session(raw, sid, k_steps=3, msg_bytes=2_000)

    # The lazy result still supports len()/indexing (pre-existing callers rely on it).
    recon = reconstruct_steps_from_raw_bodies(sid, raw)
    assert len(recon) == 3
    assert [len(recon[k].request_body["messages"]) for k in range(3)] == [1, 2, 3]
    assert recon[0].response_body is not None
    assert recon[0].response_body["usage"]["input_tokens"] == 10

    slug = "proj-stream-correct"
    steps_iter = (s.to_snapshot_step() for s in reconstruct_steps_from_raw_bodies(sid, raw))
    layers, nodes, blob_report = _build_layers_and_nodes_from_steps(
        steps_iter, trace_id="trace-c", project_slug=slug
    )

    # ordered, chained nodes; per-step message manifests differ
    assert [n.step_index for n in nodes] == [0, 1, 2]
    assert nodes[0].branch_type == "root"
    assert nodes[1].parent_node_id == nodes[0].node_id
    assert nodes[2].parent_node_id == nodes[1].node_id
    assert len({n.messages_layer_id for n in nodes}) == 3

    # every manifest content_hash resolves + round-trips (blob materialized)
    by_id = {layer.layer_id: layer for layer in layers}
    assert blob_report["written"] == 3  # 3 unique messages across the cumulative prefixes
    assert blob_report["deduped"] == 3
    for node in nodes:
        for e in by_id[node.messages_layer_id].content["messages"]:
            ch = e["content_hash"]
            raw_bytes = deref_message_content(slug, ch)
            assert raw_bytes is not None
            assert "sha256:" + hashlib.sha256(raw_bytes).hexdigest() == ch
