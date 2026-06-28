"""Issue #158 — OTel ctx data lands in the bucket, usable + spine-joined.

Hermetic unit coverage for the new substrate pieces (the live-bucket end-to-end
proof is the grounded verifier at ``tests/manual/verify_158.py``):

  * per-message raw content blobs (un-dangle ``content_hash``) + round-trip
  * raw-body reconstruction pairing (content/metadata, not filename)
  * the per-step OTel layer/node builder (manifest shape + raw blob write + usage)
  * doctor end-to-end coverage field
  * ``doctor --json`` is pure JSON (no ``---OPENTRACES_JSON---`` preamble, B6)
  * additive-only: raw message blobs are bucket_digest-invisible
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from opentraces.core import bucket_layout
from opentraces.core.context_tree.raw_blobs import (
    deref_message_content,
    deref_message_payload,
    materialize_message_blobs,
    message_content_hash,
    write_message_content_blob,
)


# --------------------------------------------------------------------------- #
# raw_blobs: the one missing write
# --------------------------------------------------------------------------- #


def test_message_blob_roundtrip_is_literal():
    slug = "proj-test"
    msg = {"role": "user", "content": [{"type": "text", "text": "hello é world"}]}
    ch = write_message_content_blob(slug, msg)
    assert ch.startswith("sha256:")
    raw = deref_message_content(slug, ch)
    assert raw is not None
    # DoD#2 literal round-trip: sha256(deref(content_hash)) == content_hash.
    assert "sha256:" + hashlib.sha256(raw).hexdigest() == ch
    # And the text is recoverable + non-empty.
    payload = deref_message_payload(slug, ch)
    assert payload == msg
    assert json.dumps(payload)


def test_message_content_hash_matches_jsonl_canonicalization():
    # The OTel + JSONL paths must hash identically so the manifest entry and
    # the raw blob agree. Mirror the JSONL path's _sha256(json.dumps(...)).
    msg = {"role": "assistant", "content": "abc"}
    expected = "sha256:" + hashlib.sha256(
        json.dumps(msg, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert message_content_hash(msg) == expected


def test_materialize_dedups_across_steps():
    slug = "proj-dedup"
    a = {"role": "user", "content": "same"}
    b = {"role": "assistant", "content": "different"}
    # 'a' appears 5x across steps, 'b' once -> 2 unique blobs.
    report = materialize_message_blobs(slug, [a, a, a, b, a])
    assert report["deduped"] == 2
    assert report["written"] == 2
    raw_root = bucket_layout.blobs_v1_root() / slug / "raw"
    assert sum(1 for _ in raw_root.rglob("*.json.gz")) == 2


def test_deref_missing_returns_none():
    assert deref_message_content("nope", "sha256:" + "0" * 64) is None


# --------------------------------------------------------------------------- #
# reconstruct: pair by content/metadata, not filename
# --------------------------------------------------------------------------- #


def _write_session_raw_bodies(raw_dir: Path, session_id: str, n_steps: int) -> None:
    """Lay down a synthetic but realistic request/response file set.

    Requests are UUID-named with ``metadata.user_id.session_id`` + a
    ``diagnostics.previous_message_id`` chain; responses are ``req_<id>``-named
    with the produced ``id`` + ``usage`` — exactly the filename mismatch that
    broke the watcher's stem pairing (B1).
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    prev_msg = None
    sk = session_id[:8]  # unique filename namespace per session (real files use UUIDs)

    base = 1_000_000.0
    for k in range(n_steps):
        # messages grow each step; the last message differs per step.
        messages = [{"role": "user", "content": f"turn {i}"} for i in range(k + 1)]
        req = {
            "model": "claude-opus-4-8",
            "system": [{"type": "text", "text": "you are a test"}],
            "tools": [{"name": "Read", "input_schema": {"type": "object", "properties": {}}}],
            "messages": messages,
            "max_tokens": 64000,
            "metadata": {"user_id": json.dumps({"session_id": session_id})},
            "diagnostics": {"previous_message_id": prev_msg},
        }
        req_path = raw_dir / f"client-{sk}-{k:04d}.request.json"
        req_path.write_text(json.dumps(req))
        import os

        os.utime(req_path, (base + k, base + k))  # deterministic temporal order
        produced = f"msg_{sk}_{k:04d}"
        resp = {
            "id": produced,
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": f"reply {k}"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10 + k, "output_tokens": 5, "cache_read_input_tokens": k},
        }
        (raw_dir / f"req_{sk}_{k:04d}.response.json").write_text(json.dumps(resp))
        prev_msg = produced


def test_reconstruct_pairs_steps_by_chain(tmp_path):
    from opentraces.capture.otlp.reconstruct import reconstruct_steps_from_raw_bodies

    raw = tmp_path / "raw-bodies"
    sid = "11111111-2222-3333-4444-555555555555"
    _write_session_raw_bodies(raw, sid, n_steps=4)
    # a second session's files must NOT leak in
    _write_session_raw_bodies(raw, "99999999-0000-0000-0000-000000000000", n_steps=2)

    steps = reconstruct_steps_from_raw_bodies(sid, raw)
    assert len(steps) == 4
    # ordered, with growing message arrays
    assert [len(s.request_body["messages"]) for s in steps] == [1, 2, 3, 4]
    # steps 0..2 pair to the response they produced (last step has no successor)
    assert steps[0].response_body is not None
    assert steps[0].response_body["usage"]["input_tokens"] == 10
    assert steps[0].request_id == f"req_{sid[:8]}_0000"
    # tools carry input_schema
    assert steps[1].request_body["tools"][0]["input_schema"]


# --------------------------------------------------------------------------- #
# the per-step builder: manifest shape + raw blobs + usage + per-step nodes
# --------------------------------------------------------------------------- #


def test_builder_emits_per_step_nodes_manifest_and_blobs():
    from opentraces.capture.otlp.emitter import _build_layers_and_nodes_from_steps

    slug = "proj-builder"
    steps = [
        {
            "step_index": 0,
            "transcript_uuid": "u0",
            "request_id": "req_0",
            "request_body": {
                "model": "claude-opus-4-8",
                "system": [{"type": "text", "text": "sys"}],
                "tools": [{"name": "Read", "input_schema": {"type": "object"}}],
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
            },
            "response_body": {"usage": {"input_tokens": 7, "output_tokens": 3}},
        },
        {
            "step_index": 1,
            "transcript_uuid": "u1",
            "request_id": "req_1",
            "request_body": {
                "model": "claude-opus-4-8",
                "system": [{"type": "text", "text": "sys"}],
                "tools": [{"name": "Read", "input_schema": {"type": "object"}}],
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "yo"},
                    {"role": "user", "content": "again"},
                ],
                "max_tokens": 100,
            },
            "response_body": {"usage": {"input_tokens": 9, "output_tokens": 4}},
        },
    ]
    layers, nodes, blob_report = _build_layers_and_nodes_from_steps(
        steps, trace_id="trace-xyz", project_slug=slug
    )
    # One node per llm-request (NOT collapsed).
    assert len(nodes) == 2
    assert nodes[0].branch_type == "root" and nodes[1].branch_type == "linear"
    assert nodes[1].parent_node_id == nodes[0].node_id
    assert [n.step_index for n in nodes] == [0, 1]
    assert all(n.capture_completeness == "full" for n in nodes)

    by_id = {layer.layer_id: layer for layer in layers}
    # messages layer is a MANIFEST of {role, uuid, parent_uuid, content_hash}
    m0 = by_id[nodes[0].messages_layer_id]
    assert m0.layer_type == "messages" and m0.capture_method == "otel"
    entries = m0.content["messages"]
    assert entries and all(set(e) >= {"role", "content_hash"} for e in entries)
    # the two steps' manifests differ (DoD#1 at the data layer)
    assert nodes[0].messages_layer_id != nodes[1].messages_layer_id
    # tool_registry carries input_schema; runtime_state carries usage
    tr0 = by_id[nodes[0].tool_registry_layer_id]
    assert tr0.content["tools"][0]["input_schema"]
    rs0 = by_id[nodes[0].runtime_state_layer_id]
    assert rs0.content["usage"]["input_tokens"] == 7

    # raw blobs materialized + every manifest content_hash resolves (round-trip)
    assert blob_report["written"] >= 1
    for layer_id, layer in by_id.items():
        if layer.layer_type != "messages":
            continue
        for e in layer.content["messages"]:
            ch = e["content_hash"]
            raw = deref_message_content(slug, ch)
            assert raw is not None
            assert "sha256:" + hashlib.sha256(raw).hexdigest() == ch


# --------------------------------------------------------------------------- #
# doctor: end-to-end coverage + B6 pure-JSON
# --------------------------------------------------------------------------- #


def test_doctor_coverage_counts_bucket_nodes(tmp_path, monkeypatch):
    from opentraces.core import doctor
    from opentraces.core.config import get_project_dir

    proj = tmp_path / "covproj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    # opt in (the .opentraces.json marker is the ground truth) so
    # get_project_dir resolves the canonical slug.
    (proj / ".opentraces.json").write_text(json.dumps({"project_id": "covtest012345"}))
    slug = get_project_dir(proj).name
    # fabricate a context tree in the bucket for this slug
    nodes_path = bucket_layout.context_tree_nodes_path(slug, "trace-cov")
    nodes_path.parent.mkdir(parents=True, exist_ok=True)
    nodes_path.write_text("\n".join(json.dumps({"node_id": f"n{i}"}) for i in range(3)))

    cov = doctor._context_tree_coverage(proj, {"captures_total": 50})
    assert cov["context_nodes_in_bucket"] == 3
    assert cov["context_traces_in_bucket"] == 1
    assert cov["landed"] is True
    assert cov["captures_total"] == 50


def test_doctor_json_is_pure_json_no_sentinel():
    from click.testing import CliRunner

    from opentraces.cli import SENTINEL, main

    res = CliRunner().invoke(main, ["doctor", "--json"])
    assert res.exit_code in (0, 1, 2, 3)  # doctor exit reflects health, not crash
    assert SENTINEL not in res.output, "explicit --json must not emit the sentinel preamble"
    # the whole stdout must be valid JSON
    json.loads(res.output)


# --------------------------------------------------------------------------- #
# additive-only: raw message blobs are bucket_digest-invisible
# --------------------------------------------------------------------------- #


def test_raw_message_blobs_do_not_change_bucket_digest():
    from opentraces.core.bucket_store import bucket_manifest

    before = bucket_manifest(write=False)
    digest_before = before.get("bucket_digest") or before.get("digest")
    # write a pile of raw message blobs under a project slug
    materialize_message_blobs(
        "proj-digest", [{"role": "user", "content": f"m{i}"} for i in range(20)]
    )
    after = bucket_manifest(write=False)
    digest_after = after.get("bucket_digest") or after.get("digest")
    assert digest_before == digest_after, "raw/ message blobs must be digest-invisible"
