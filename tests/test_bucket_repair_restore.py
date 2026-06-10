"""Bucket repair / restore self-sufficiency regression tests.

Covers issues #28 / #31: a captured bucket restored onto a machine with no
live opted-in project (the cross-machine ``bucket remote pull`` shape) must
NOT lose traces when ``bucket repair`` / manifest rebuild runs. The bucket is
the self-sufficient unit — repair must source per-trace envelopes from the
bucket's own canonical stores (TraceRecord object store + events mirror), not
only from live opted-in projects.

Seeded (with inverted assertions) from the issue-28 reproduction.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from opentraces_schema import Agent, Step, TraceRecord


def _record(trace_id: str) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="codex-cli", version="0.31.0"),
        steps=[Step(step_index=1, role="user", content="task")],
    )


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


# --------------------------------------------------------------------------- #
# (a) Objects store trace with no live project survives repair (#28 / #31).
# --------------------------------------------------------------------------- #


def test_repair_preserves_objects_store_trace_with_no_live_project():
    """A trace present in the bucket objects store (written by ingest) but with
    no live opted-in project survives ``bucket repair``: it appears in
    ``manifest.traces`` and the manifest count equals the object count."""

    from opentraces.core.bucket_store import (
        bucket_manifest,
        bucket_repair,
        write_trace_record,
    )

    write_trace_record(
        _record("codex-restored"),
        project_slug="project-deadbeef",
        source_layer="canonical",
    )

    result = bucket_repair(dry_run=False)
    assert result["status"] in ("ok", "partial")
    # The bucket-sourced pass picked up the orphan trace.
    assert result["bucket_sourced_traces"] >= 1

    manifest = bucket_manifest(write=False, include_objects=False)
    object_count = manifest["trace_records"]["object_count"]
    trace_ids = [row["trace_id"] for row in manifest["traces"]]

    assert object_count == 1
    assert "codex-restored" in trace_ids
    assert len(manifest["traces"]) == object_count


# --------------------------------------------------------------------------- #
# (b) Restored events mirror is preserved when the project ref is missing (#28).
# --------------------------------------------------------------------------- #


def test_repair_preserves_restored_events_mirror_when_project_ref_missing(tmp_path):
    """A restored events mirror (``bucket/events/v1`` with batch_count=1) is NOT
    reset to ``batch_count=0`` / ``state=missing`` by ``bucket repair`` when the
    live project exists but has no opentraces event-log ref (fresh clone)."""

    from opentraces.core.bucket_layout import (
        events_v1_batches_dir,
        events_v1_index_path,
    )
    from opentraces.core.bucket_store import bucket_repair
    from opentraces.core.config import load_config, register_project, save_config

    # A live opted-in project WITHOUT the opentraces event-log ref (fresh clone).
    proj = tmp_path / "project"
    proj.mkdir()
    _init_repo(proj)
    cfg = load_config()
    register_project(cfg, proj)
    save_config(cfg)

    # A restored events mirror with one batch (as bucket remote pull leaves it).
    batches = events_v1_batches_dir()
    batches.mkdir(parents=True)
    (batches / "000000000001-b1.jsonl.gz").write_bytes(
        b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    )
    events_v1_index_path().write_text(
        json.dumps(
            {
                "schema_version": "opentraces.bucket.events.v2",
                "repo_id": "project-deadbeef",
                "event_log_head": "abc123",
                "batch_count": 1,
                "last_batch_id": "b1",
                "latest_event_sequence": 7,
                "state": "ok",
            }
        )
    )

    bucket_repair(dry_run=False)

    idx = json.loads(events_v1_index_path().read_text())
    # Restored mirror head preserved — NOT clobbered.
    assert idx["batch_count"] == 1
    assert idx["state"] == "ok"


# --------------------------------------------------------------------------- #
# (c) Issue-28 acceptance: seed live, repair, then orphan, repair again (#28).
# --------------------------------------------------------------------------- #


def test_repair_acceptance_orphaned_envelope_survives_second_repair(tmp_path):
    """End-to-end acceptance for issue #28.

    Seed a live project + repair (live walk projects the trace). Then delete
    the per-trace envelope dir AND point the registered project at a
    nonexistent path (the cross-machine restore shape) and repair again — the
    trace must still be in ``manifest.traces`` (sourced from the bucket's own
    canonical stores, not the now-missing live project)."""

    import shutil

    from opentraces.core import paths
    from opentraces.core.bucket_store import (
        bucket_manifest,
        bucket_repair,
        sync_events_mirror,
        traces_v1_dir,
        write_trace_record,
    )
    from opentraces.core.config import (
        get_project_dir,
        load_config,
        register_project,
        save_config,
    )

    proj = tmp_path / "live-project"
    proj.mkdir()
    _init_repo(proj)
    cfg = load_config()
    register_project(cfg, proj)
    save_config(cfg)
    project_slug = get_project_dir(proj).name

    write_trace_record(
        _record("trace-acc-1"),
        project_slug=project_slug,
        source_layer="canonical",
    )
    # Mirror the (empty) event log so the bucket has its own canonical store.
    sync_events_mirror(proj, repo_id=project_slug)

    bucket_repair(dry_run=False)
    manifest = bucket_manifest(write=False, include_objects=False)
    assert "trace-acc-1" in [row["trace_id"] for row in manifest["traces"]]

    # Now simulate the cross-machine restore: drop the per-trace envelope dir
    # and delete the live project entirely. ``_iter_opted_in_projects`` skips
    # registered projects whose on-disk path no longer exists, so the live walk
    # no longer sees this trace — only the bucket's own canonical stores do.
    env_dir = traces_v1_dir(project_slug, "trace-acc-1")
    if env_dir.exists():
        shutil.rmtree(env_dir)
    shutil.rmtree(proj)

    result = bucket_repair(dry_run=False)
    assert result["bucket_sourced_traces"] >= 1

    manifest2 = bucket_manifest(write=False, include_objects=False)
    trace_ids = [row["trace_id"] for row in manifest2["traces"]]
    assert "trace-acc-1" in trace_ids, (
        "orphaned trace dropped after restore-shaped repair"
    )
