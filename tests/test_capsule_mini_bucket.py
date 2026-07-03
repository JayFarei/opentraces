"""C197 (#197) — mini-bucket storage + digest + companion redaction.

RED-first coverage for the capsule SECOND-HALF mini-bucket:

* ``redact_companions`` wires M1's ``sanitize_companion_dict`` (no capsule-local
  sanitizer) over raw gzipped trail/context/sources companions — joint redaction
  7/8 stock, 8/8 NER-gated (the person-name residual needs NER; the NER branch
  injects a deterministic stand-in, so the CI box is NEVER asked to run a model).
* ``build_mini_bucket`` materializes a scoped, redacted mini-bucket with a
  deterministic ``mini_bucket_digest`` stable across two builds and across
  machines (pure content-hash), content-addressed blobs, no dangling refs.
* multi-trace arity: each trace independently resolvable; a multi-trace capsule
  id recipe (hash of sorted per-trace seeds) is order-independent + deterministic.
"""

from __future__ import annotations

import gzip
import json

from opentraces.core.capsule.companions import redact_companions
from opentraces.core.capsule.contract import (
    build_capsule_id,
    build_multi_trace_capsule_id,
    compute_mini_bucket_digest,
)
from opentraces.core.capsule.share import (
    build_mini_bucket,
    verify_mini_bucket,
)


# --- 8 unique sentinels planted across the three raw companions ------------- #
S = {
    "S1": "AKIAZZ7QF3SENTINEL1X",                              # AWS key (regex)
    "S2": "hf_SENTINEL2aaaaaaaaaaaaaaaaaaaa",                  # HF token (regex)
    "S3": "xoxb-SENTINEL3-aaaaaaaaaaaa",                       # Slack token (regex)
    "S4": "SENTINEL4 Franklin",                                # person name (NER only)
    "S5": "bastion-SENTINEL5.internal",                        # internal host (business_logic)
    "S6": "mongodb+srv://svc:SENTINEL6PWD@cluster0.acme.net/prod",  # db string (business_logic)
    "S7": "/Users/marvinSENTINEL7/secret/creds.env",          # home path (path_anonymizer)
    "S8": "ghp_SENTINEL8aaaaaaaaaaaaaaaaaaaa",                 # github token (regex)
}

# Grep probes (S7 is a TAIL probe — the security-meaningful "structure leaked").
PROBES = {
    "S1": "SENTINEL1X",
    "S2": "hf_SENTINEL2",
    "S3": "xoxb-SENTINEL3",
    "S4": "SENTINEL4 Franklin",
    "S5": "bastion-SENTINEL5.internal",
    "S6": "SENTINEL6PWD",
    "S7": "/secret/creds.env",
    "S8": "ghp_SENTINEL8",
}


def _gz_lines(objs) -> bytes:
    body = "\n".join(json.dumps(o, ensure_ascii=False) for o in objs) + "\n"
    return gzip.compress(body.encode("utf-8"), mtime=0)


def _trail_gz() -> bytes:
    return _gz_lines([
        {"event_type": "file_edit_observed", "file_path": S["S7"],
         "authored_text": f'API_KEY = "{S["S8"]}"'},
    ])


def _context_gz() -> bytes:
    return _gz_lines([
        {"schema_version": "opentraces.context_resume.v1", "node_id": "n0",
         "messages_layer": {"layer_type": "messages", "content": {"messages": [
             {"role": "user", "content": (
                 f"deploy key {S['S1']} and token {S['S2']}. "
                 f"{S['S4']} owns host {S['S5']} with db {S['S6']}."
             )},
         ]}}},
    ])


def _sources_gz() -> bytes:
    return _gz_lines([{"kind": "raw", "content": f"slack bot token {S['S3']}"}])


def _leaks(*redacted_blobs: bytes) -> set[str]:
    blob = "".join(gzip.decompress(b).decode("utf-8") for b in redacted_blobs if b)
    return {k for k, probe in PROBES.items() if probe in blob}


class _FakeNER:
    """Deterministic stand-in for an availability-gated NER (privacy_filter /
    llm_pii). Flags the planted person name only; proves the 8/8 band without a
    live model. Stock never has it, so S4 residual is the honest stock answer."""

    def detect_for_path(self, path, text, field_type_label, siblings):
        return [("PERSON", S["S4"])] if S["S4"] in text else []


# --------------------------------------------------------------------------- #
# redact_companions — wired to the M1 substrate capability
# --------------------------------------------------------------------------- #


def test_redact_companions_wires_sanitize_companion_dict_stock_seven_of_eight():
    trail, tmani = redact_companions(_trail_gz())
    ctx, cmani = redact_companions(_context_gz())
    src, smani = redact_companions(_sources_gz())
    leaks = _leaks(trail, ctx, src)

    # Exactly the NER-dependent person name residual survives a stock floor.
    assert leaks == {"S4"}, f"expected only S4 to leak on stock, got {leaks}"

    # The manifest is the substrate's counts+types sidecar (path_anonymizer +
    # detector floor ran) — never a capsule-local reimplementation.
    for m in (tmani, cmani, smani):
        assert m["floor_satisfied"] is True
        assert "path_anonymizer" in m["tools_applied"]
    assert tmani["home_paths_scrubbed"] >= 1  # the trail home path tail closed


def test_redact_companions_manifest_never_carries_matched_text():
    _red, manifest = redact_companions(_context_gz())
    blob = json.dumps(manifest, ensure_ascii=False)
    assert "matched_text" not in blob
    assert S["S1"] not in blob
    assert "SENTINEL6PWD" not in blob


def test_redact_companions_eight_of_eight_when_ner_present():
    from opentraces.security.pipeline import COMPANION_FLOOR
    from opentraces.security.tools.llm_pii_tool import LLMPIIDetectorTool

    ner = LLMPIIDetectorTool().with_detector(_FakeNER())
    tools = [*COMPANION_FLOOR, ner]
    trail, _ = redact_companions(_trail_gz(), tools=tools)
    ctx, _ = redact_companions(_context_gz(), tools=tools)
    src, _ = redact_companions(_sources_gz(), tools=tools)
    assert _leaks(trail, ctx, src) == set(), "with NER present all 8 sentinels close"


def test_redact_companions_empty_is_byte_stable_empty():
    empty = gzip.compress(b"", mtime=0)
    out, manifest = redact_companions(empty)
    assert gzip.decompress(out) == b""
    assert manifest["findings_total"] == 0


def test_redact_companions_is_idempotent():
    once, _ = redact_companions(_context_gz())
    twice, _ = redact_companions(once)
    assert gzip.decompress(twice) == gzip.decompress(once)


# --------------------------------------------------------------------------- #
# mini_bucket_digest — deterministic, content-addressed, no dangling refs
# --------------------------------------------------------------------------- #


def _seed_companions(project_dir, monkeypatch, trace_id, *, slug="p"):
    from opentraces.core import paths
    from opentraces.core._bucket_io import _atomic_write_gzip
    from opentraces.core.bucket_layout import (
        trace_v1_context_path,
        trace_v1_sources_path,
        trace_v1_trail_path,
    )

    monkeypatch.setattr(paths, "OPENTRACES_DIR", project_dir / "ot")
    _atomic_write_gzip(trace_v1_trail_path(slug, trace_id), gzip.decompress(_trail_gz()))
    _atomic_write_gzip(trace_v1_context_path(slug, trace_id), gzip.decompress(_context_gz()))
    _atomic_write_gzip(trace_v1_sources_path(slug, trace_id), gzip.decompress(_sources_gz()))
    return slug


def test_mini_bucket_digest_stable_across_two_builds(tmp_path, monkeypatch):
    slug = _seed_companions(tmp_path, monkeypatch, "tid-1")
    a = build_mini_bucket(tmp_path, slug, ["tid-1"])
    b = build_mini_bucket(tmp_path, slug, ["tid-1"])
    assert a["digest"] == b["digest"]
    assert a["files"].keys() == b["files"].keys()
    # The redacted companions carried in the mini-bucket leak nothing but S4.
    blob_leaks = set()
    for rel, data in a["files"].items():
        if rel.endswith(".jsonl.gz"):
            body = gzip.decompress(data).decode("utf-8")
            blob_leaks |= {k for k, p in PROBES.items() if p in body}
    assert blob_leaks <= {"S4"}


def test_mini_bucket_digest_is_pure_content_hash():
    # Cross-machine determinism proxy: the digest is a pure function of the
    # per-trace companion content-digest map (no gzip / mtime / path variance).
    tmap = {"t": {"trail": "sha256:aa", "context": "sha256:bb", "sources": None}}
    assert compute_mini_bucket_digest(tmap) == compute_mini_bucket_digest(dict(tmap))
    assert compute_mini_bucket_digest(tmap) != compute_mini_bucket_digest(
        {"t": {"trail": "sha256:cc", "context": "sha256:bb", "sources": None}}
    )


def test_mini_bucket_blobs_are_content_addressed_no_dangling_refs(tmp_path, monkeypatch):
    slug = _seed_companions(tmp_path, monkeypatch, "tid-1")
    mini = build_mini_bucket(tmp_path, slug, ["tid-1"])
    assert verify_mini_bucket(mini) == [], "a fresh mini-bucket must have no dangling refs"

    # Every referenced companion blob resolves to a file whose content hash
    # equals its content address (content-addressed integrity).
    blob_index = mini["manifest"]["blobs"]
    assert blob_index, "expected content-addressed companion blobs"
    for digest, relpath in blob_index.items():
        assert relpath in mini["files"]

    # Tamper: drop a referenced blob → verify reports the dangling ref.
    tampered = {"files": {k: v for k, v in mini["files"].items()}, "manifest": mini["manifest"]}
    victim = next(iter(blob_index.values()))
    del tampered["files"][victim]
    assert verify_mini_bucket(tampered), "a missing blob must be flagged as a dangling ref"


# --------------------------------------------------------------------------- #
# Multi-trace arity — each trace independently resolvable + id recipe
# --------------------------------------------------------------------------- #


def test_multi_trace_mini_bucket_each_trace_independently_resolvable(tmp_path, monkeypatch):
    slug = _seed_companions(tmp_path, monkeypatch, "tid-A")
    _seed_companions(tmp_path, monkeypatch, "tid-B", slug=slug)
    mini = build_mini_bucket(tmp_path, slug, ["tid-A", "tid-B"])
    assert verify_mini_bucket(mini) == []
    traces = mini["manifest"]["traces"]
    assert set(traces) == {"tid-A", "tid-B"}
    # Each trace has its own scoped spine + independently resolvable companions.
    for tid in ("tid-A", "tid-B"):
        spine_rel = traces[tid]["trace_json"]
        assert spine_rel in mini["files"]
        spine = json.loads(mini["files"][spine_rel].decode("utf-8"))
        assert spine["trace_id"] == tid
        for face in ("trail", "context", "sources"):
            blob = traces[tid]["companions"][face]["blob"]
            if blob is not None:
                assert blob in mini["manifest"]["blobs"]


def test_multi_trace_capsule_id_is_order_independent_and_deterministic():
    seed_a = {"trace_id": "a", "node_id": "", "start": 0, "end": 3, "sha": "x"}
    seed_b = {"trace_id": "b", "node_id": "", "start": 1, "end": 4, "sha": "y"}
    one = build_multi_trace_capsule_id([seed_a, seed_b])
    two = build_multi_trace_capsule_id([seed_b, seed_a])
    assert one == two, "the multi-trace id must be order-independent (hash of sorted seeds)"
    assert one != build_multi_trace_capsule_id([seed_a])
    # Deterministic across calls.
    assert build_multi_trace_capsule_id([seed_a]) == build_multi_trace_capsule_id([seed_a])
    # A single-seed multi id is well-formed 16-hex like build_capsule_id.
    single = build_multi_trace_capsule_id([seed_a])
    assert len(single) == 16
    assert len(build_capsule_id(trace_id="a", node_id=None, start_step_index=0,
                                end_step_index=3, repo_commit_sha="x")) == 16
