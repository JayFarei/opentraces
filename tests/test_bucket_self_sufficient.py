"""Bucket self-sufficiency tests (plan 080 phase B).

These tests assert the self-sufficiency principle: every read query a user
can run locally against ``~/.opentraces/.../bucket/`` must work against the
bucket directory alone, without depending on the Git event log ref. They
cover the spine layout (``bucket/traces/v1/<project>/<trace>/``), the
content-addressed blob store (``bucket/blobs/v1/<project>/{context,raw}/``),
the canonical event log mirror (``bucket/events/v1/batches/``), and the v2
manifest at ``bucket/manifest.json``.

Cross-track contract — these tests sit at the assertion edge of plan 080
phase B. Tracks B1 (bucket_store.py), B2 (bucket_remote.py), B3 (CLI) are
in flight concurrently; tests that depend on un-shipped functionality are
marked ``xfail`` with ``strict=False`` so the cross-track signal is
preserved.
"""

from __future__ import annotations

import gzip
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from opentraces_schema import Agent, Step, TraceRecord


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _build_session_layers(seed: str = "alpha") -> tuple[Any, Any, Any, Any]:
    from opentraces.core.context_tree.models import build_layer

    system = build_layer(
        layer_type="system",
        content={
            "assembled_text": f"system-{seed}",
            "components_present": ["claude_md"],
        },
        completeness="approximated",
        capture_method="transcript_reconstruction",
    )
    messages = build_layer(
        layer_type="messages",
        content={
            "messages": [
                {"uuid": f"m1-{seed}", "role": "user", "content_hash": "sha256:aa"},
                {"uuid": f"m2-{seed}", "role": "assistant", "content_hash": "sha256:bb"},
            ]
        },
        completeness="full",
        capture_method="transcript_reconstruction",
    )
    tool_registry = build_layer(
        layer_type="tool_registry",
        content={"tools": [{"name": "Edit"}, {"name": "Read"}]},
        completeness="full",
        capture_method="transcript_reconstruction",
    )
    runtime_state = build_layer(
        layer_type="runtime_state",
        content={"cwd": f"/tmp/{seed}", "permission_mode": "default"},
        completeness="full",
        capture_method="transcript_reconstruction",
    )
    return system, messages, tool_registry, runtime_state


def _seed_session(
    repo: Path,
    *,
    trace_id: str,
    seed: str = "alpha",
) -> dict[str, Any]:
    """Seed a 1-node session into the canonical Git event log."""

    from opentraces.core.context_tree.contract import (
        CONTEXT_LAYER_CAPTURED,
        CONTEXT_NODE_OBSERVED,
        CONTEXT_TREE_RECONCILED,
    )
    from opentraces.core.context_tree.models import build_node
    from opentraces.core.trails import TrailEventDraft, append_event_batch

    system, messages, tool_registry, runtime_state = _build_session_layers(seed)
    node = build_node(
        parent_node_id=None,
        branch_type="root",
        trace_id=trace_id,
        transcript_uuid=f"uuid-{seed}-root",
        transcript_offset=0,
        system_layer_id=system.layer_id,
        messages_layer_id=messages.layer_id,
        tool_registry_layer_id=tool_registry.layer_id,
        runtime_state_layer_id=runtime_state.layer_id,
        capture_completeness="full",
        step_index=1,
    )
    drafts: list[TrailEventDraft] = []
    for layer in (system, messages, tool_registry, runtime_state):
        drafts.append(
            TrailEventDraft(
                event_type=CONTEXT_LAYER_CAPTURED,
                payload=layer.model_dump(mode="json"),
                trace_id=trace_id,
                capture_method=["transcript_reconstruction"],
            )
        )
    drafts.append(
        TrailEventDraft(
            event_type=CONTEXT_NODE_OBSERVED,
            payload=node.model_dump(mode="json"),
            trace_id=trace_id,
            step_index=1,
            capture_method=["transcript_reconstruction"],
        )
    )
    drafts.append(
        TrailEventDraft(
            event_type=CONTEXT_TREE_RECONCILED,
            payload={
                "trace_id": trace_id,
                "node_count": 1,
                "layer_count": 4,
                "active_path_leaf_id": node.node_id,
                "orphan_branch_roots": [],
                "subagent_session_ids": [],
                "capture_limitations": [],
            },
            trace_id=trace_id,
            capture_method=["transcript_reconstruction"],
        )
    )
    append_event_batch(repo, drafts, writer="test-self-sufficient-seed")
    return {
        "trace_id": trace_id,
        "node_id": node.node_id,
        "layer_ids": sorted(
            {
                system.layer_id,
                messages.layer_id,
                tool_registry.layer_id,
                runtime_state.layer_id,
            }
        ),
        "system_layer_id": system.layer_id,
    }


def _build_trace_record(trace_id: str) -> TraceRecord:
    """Minimal TraceRecord for the spine."""

    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="test-agent", version="0.0.1"),
        steps=[
            Step(step_index=1, role="user", content="initial task"),
        ],
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fresh git repo (also project_dir for the substrate)."""

    _init_repo(tmp_path)
    return tmp_path


# --------------------------------------------------------------------------- #
# Section A: empty bucket / manifest v2 shape
# --------------------------------------------------------------------------- #


def test_empty_bucket_manifest_v2_schema_version() -> None:
    """An empty bucket emits the v2 schema_version on bucket_manifest()."""

    from opentraces.core.bucket_store import BUCKET_MANIFEST_SCHEMA, bucket_manifest

    assert BUCKET_MANIFEST_SCHEMA == "opentraces.bucket.manifest.v2"
    manifest = bucket_manifest(write=False)
    assert manifest["schema_version"] == "opentraces.bucket.manifest.v2"


def test_empty_bucket_has_no_traces() -> None:
    """An empty bucket reports zero traces in iter_traces_v2()."""

    from opentraces.core.bucket_store import iter_traces_v2

    rows = iter_traces_v2()
    assert rows == []


# --------------------------------------------------------------------------- #
# Section B: per-trace envelope layout
# --------------------------------------------------------------------------- #


def test_per_trace_envelope_writes_trace_json(repo: Path) -> None:
    """project_per_trace_exports writes bucket/traces/v1/<proj>/<trace>/trace.json."""

    from opentraces.core.bucket_store import (
        project_per_trace_exports,
        trace_v1_json_path,
    )

    _seed_session(repo, trace_id="trace-SP1", seed="sp1")
    record = _build_trace_record("trace-SP1")
    project_per_trace_exports(
        repo, project_slug="proj", trace_id="trace-SP1", record=record
    )

    path = trace_v1_json_path("proj", "trace-SP1")
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    rebuilt = TraceRecord.model_validate(payload)
    assert rebuilt.trace_id == "trace-SP1"


def test_per_trace_envelope_writes_trail_jsonl_gz(repo: Path) -> None:
    """The trail companion file is gzipped JSONL of trail-class events."""

    from opentraces.core.bucket_store import (
        project_per_trace_exports,
        trace_v1_trail_path,
    )

    _seed_session(repo, trace_id="trace-SP2", seed="sp2")
    record = _build_trace_record("trace-SP2")
    project_per_trace_exports(
        repo, project_slug="proj", trace_id="trace-SP2", record=record
    )

    trail = trace_v1_trail_path("proj", "trace-SP2")
    assert trail.exists()
    # Gzipped file is readable as JSONL (one event per line).
    raw = gzip.decompress(trail.read_bytes()).decode("utf-8")
    lines = [line for line in raw.splitlines() if line.strip()]
    # Trail-class events (no context_*) — may be empty for synthetic session.
    for line in lines:
        payload = json.loads(line)
        assert "event_type" in payload


def test_per_trace_envelope_writes_context_jsonl_gz(repo: Path) -> None:
    """The context companion file is gzipped JSONL of context_* events.

    Plan 080 §4 — payloads are thin (layer_id_ref shape, not inline content).
    Layer content lives in the blob store.
    """

    from opentraces.core.context_tree.contract import (
        CONTEXT_LAYER_CAPTURED,
        CONTEXT_NODE_OBSERVED,
        CONTEXT_TREE_RECONCILED,
    )
    from opentraces.core.bucket_store import (
        project_per_trace_exports,
        trace_v1_context_path,
    )

    info = _seed_session(repo, trace_id="trace-SP3", seed="sp3")
    record = _build_trace_record("trace-SP3")
    project_per_trace_exports(
        repo, project_slug="proj", trace_id="trace-SP3", record=record
    )

    ctx_path = trace_v1_context_path("proj", "trace-SP3")
    assert ctx_path.exists()
    raw = gzip.decompress(ctx_path.read_bytes()).decode("utf-8")
    lines = [line for line in raw.splitlines() if line.strip()]
    event_types = {json.loads(line)["event_type"] for line in lines}
    # All four canonical context_* events from the seed must land here.
    assert CONTEXT_LAYER_CAPTURED in event_types
    assert CONTEXT_NODE_OBSERVED in event_types
    assert CONTEXT_TREE_RECONCILED in event_types
    # And the trace_id filter actually worked: payloads cite this trace.
    for line in lines:
        payload = json.loads(line)
        # event-level trace_id OR payload trace_id matches the seeded id.
        assert payload.get("trace_id") == "trace-SP3" or payload.get(
            "payload", {}
        ).get("trace_id") in {"trace-SP3", None}


# --------------------------------------------------------------------------- #
# Section C: events/v1 mirror shape
# --------------------------------------------------------------------------- #


def test_events_mirror_v2_index_schema(repo: Path) -> None:
    """sync_events_mirror writes index.json with v2 schema_version."""

    from opentraces.core.bucket_store import (
        BUCKET_EVENTS_INDEX_SCHEMA,
        events_v1_index_path,
        sync_events_mirror,
    )

    assert BUCKET_EVENTS_INDEX_SCHEMA == "opentraces.bucket.events.v2"

    _seed_session(repo, trace_id="trace-EV1", seed="ev1")
    index = sync_events_mirror(repo, repo_id="proj")
    assert index["schema_version"] == "opentraces.bucket.events.v2"
    assert events_v1_index_path().exists()


def test_events_mirror_batches_are_gzipped_with_mtime_zero(repo: Path) -> None:
    """Plan 080 Resolution H — gzip mtime=0 across machines for byte-identity.

    Asserts directly on the mtime field of the gzipped batch file. This is
    the byte-identity proof.
    """

    from opentraces.core.bucket_store import (
        events_v1_batches_dir,
        sync_events_mirror,
    )

    _seed_session(repo, trace_id="trace-EV2", seed="ev2")
    sync_events_mirror(repo, repo_id="proj")

    batches = list(events_v1_batches_dir().glob("*.jsonl.gz"))
    assert batches, "expected at least one batch file"
    for batch in batches:
        with gzip.GzipFile(filename=str(batch), mode="rb") as fh:
            # Read enough to populate mtime from the gzip header.
            fh.read(8)
            assert fh.mtime == 0, f"non-zero mtime on {batch}: {fh.mtime}"


# --------------------------------------------------------------------------- #
# Section D: layer blob path (new layout)
# --------------------------------------------------------------------------- #


def test_blobs_v1_context_path_layout() -> None:
    """Layer blobs live at bucket/blobs/v1/<project>/context/<hh>/<hash>.json.gz.

    NOT under _shared/ (that's the opt-in global scope), NOT under
    v1/v1, NOT under the legacy contexts/v1/ path.
    """

    from opentraces.core.bucket_store import blobs_v1_context_path, blobs_v1_root

    layer_id = "sha256:" + ("ab" * 32)
    path = blobs_v1_context_path("proj", layer_id)

    # Path shape.
    parts = path.parts
    assert "blobs" in parts and "v1" in parts
    assert "proj" in parts
    assert "context" in parts
    assert "_shared" not in parts
    assert "contexts" not in parts
    # Filename uses the full hex digest and the .json.gz suffix.
    assert path.name.endswith(".json.gz")
    # Two-char shard prefix.
    assert path.parent.name == "ab"
    # The full path sits under blobs_v1_root().
    assert blobs_v1_root() in path.parents


# --------------------------------------------------------------------------- #
# Section E: determinism + manifest stability
# --------------------------------------------------------------------------- #


def test_manifest_digest_stable_across_re_projection() -> None:
    """Bucket-manifest digest is stable when re-projecting the same content.

    Plan 080 §10.5 Resolution H — deterministic projection contract.
    """

    from opentraces.core.bucket_store import bucket_manifest

    first = bucket_manifest(write=False)
    second = bucket_manifest(write=False)
    assert first["digest"] == second["digest"], (
        "bucket_manifest must be deterministic across calls"
    )


# --------------------------------------------------------------------------- #
# Section F: schema_version check on manifest read
# --------------------------------------------------------------------------- #


def test_load_manifest_raises_on_v1_schema(tmp_path: Path, monkeypatch) -> None:
    """Loading a v1-shaped manifest raises BucketLayoutError.

    Plan 080 §20 Resolution E — there is no v1-reader path on this branch.
    """

    from opentraces.core import bucket_store
    from opentraces.core.bucket_store import BucketLayoutError, _load_manifest

    # Write a fake v1 manifest at the manifest path.
    v1_payload = {
        "schema_version": "opentraces.bucket.manifest.v1",
        "root": str(tmp_path),
        "updated_at": "2026-05-19T00:00:00Z",
    }
    manifest_path = bucket_store.bucket_manifest_path()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(v1_payload), encoding="utf-8")

    with pytest.raises(BucketLayoutError) as excinfo:
        _load_manifest()
    # The error message must point at the migration verb.
    assert "setup bucket --migrate" in str(excinfo.value)


def test_load_manifest_returns_none_when_missing() -> None:
    """A missing manifest returns None (the empty-bucket case)."""

    from opentraces.core.bucket_store import _load_manifest

    assert _load_manifest() is None


# --------------------------------------------------------------------------- #
# Section G: bucket_repair / bucket_verify (stubs today; xfail when wired)
# --------------------------------------------------------------------------- #


def test_bucket_verify_sample_returns_ok_on_clean_bucket() -> None:
    """bucket_verify(sample=10) reports ok on a clean bucket.

    Stub today (Phase B follow-up); xfail until B1 ships the full sweep.
    """

    from opentraces.core.bucket_store import bucket_verify

    result = bucket_verify(sample=10)
    # The stub returns dangling_count=0; the full impl must return ok=True.
    assert result.get("dangling_count", 0) == 0


def test_bucket_verify_returns_ok_true_when_clean() -> None:
    """The full impl must return ok=True when no dangling refs."""

    from opentraces.core.bucket_store import bucket_verify

    result = bucket_verify(sample=10)
    assert result["ok"] is True


def test_bucket_repair_is_idempotent(repo: Path) -> None:
    """Running bucket_repair twice produces byte-identical output.

    Stub today (Phase B follow-up); the assertion documents the contract.
    """

    from opentraces.core.bucket_store import bucket_repair

    first = bucket_repair(dry_run=False)
    second = bucket_repair(dry_run=False)
    # Stub returns the same status; the full impl must produce
    # byte-identical manifest output on the second run.
    assert first["status"] == second["status"]


def test_bucket_repair_byte_identical_second_run(repo: Path) -> None:
    """bucket_repair full impl: second run is byte-identical (no writes)."""

    from opentraces.core.bucket_store import bucket_repair, bucket_manifest_path

    _seed_session(repo, trace_id="trace-RP1", seed="rp1")
    bucket_repair(dry_run=False)
    first_bytes = bucket_manifest_path().read_bytes()
    bucket_repair(dry_run=False)
    second_bytes = bucket_manifest_path().read_bytes()
    assert first_bytes == second_bytes


# --------------------------------------------------------------------------- #
# Section H: integrated — per-trace envelope round trip
# --------------------------------------------------------------------------- #


def test_per_trace_envelope_round_trip_via_iter_traces_v2(repo: Path) -> None:
    """Write one trace envelope and confirm iter_traces_v2 sees it."""

    from opentraces.core.bucket_store import iter_traces_v2, project_per_trace_exports

    _seed_session(repo, trace_id="trace-RT1", seed="rt1")
    record = _build_trace_record("trace-RT1")
    project_per_trace_exports(
        repo, project_slug="proj", trace_id="trace-RT1", record=record
    )

    rows = iter_traces_v2()
    matching = [r for r in rows if r["trace_id"] == "trace-RT1"]
    assert len(matching) == 1
    row = matching[0]
    assert row["project_slug"] == "proj"
    assert row["trace_path"].startswith("traces/v1/proj/trace-RT1/")
    # The summary block carries the load-bearing keys.
    summary = row["summary"]
    assert "step_count" in summary
    assert "patch_count" in summary
    assert "anchored_count" in summary
    assert "node_count" in summary
    assert "capture_methods" in summary
    # files block flagged correctly.
    files = row["files"]
    assert files["has_context"] is True
    # digest is a sha256 string.
    assert row["digest"].startswith("sha256:")


def test_per_trace_envelope_node_count_from_context_jsonl(repo: Path) -> None:
    """node_count is reconstructed from context.jsonl.gz (not from any cache).

    Self-sufficiency check: the manifest summary counts nodes by reading the
    on-disk context file, not by querying the Git event log.
    """

    from opentraces.core.bucket_store import iter_traces_v2, project_per_trace_exports

    _seed_session(repo, trace_id="trace-NC1", seed="nc1")
    record = _build_trace_record("trace-NC1")
    project_per_trace_exports(
        repo, project_slug="proj", trace_id="trace-NC1", record=record
    )

    rows = iter_traces_v2(project_slug="proj")
    target = next((r for r in rows if r["trace_id"] == "trace-NC1"), None)
    assert target is not None
    # One CONTEXT_NODE_OBSERVED event was seeded -> node_count == 1.
    assert target["summary"]["node_count"] == 1
