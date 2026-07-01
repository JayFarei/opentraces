"""F0 Phase-0 hang cure — CLI read verbs must bound WORK, not just output.

Covers the four CLI-surface cures whose backends re-scanned a whole-corpus
primitive on a plain read:

* ``trail track --all`` — ``--limit`` (default 50 for ``--all``) bounds the
  per-patch git-survival loop to the NEWEST patches, not the oldest+slowest.
* ``trail resolve ot://file/...`` — routes through a scoped event read, never
  the whole-log ``read_events``.
* ``bucket manifest`` (read) — serves the persisted ``manifest.json`` instead
  of recomputing it.
* ``bucket verify --sample N`` — a true work budget: every check is bounded to
  the sample, never a whole-corpus walk.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.trails import TrailEventDraft, append_event_batch
from opentraces.core.trails.models import sha256_text


# --------------------------------------------------------------------------- #
# trail track --all — --limit bounds WORK to the newest patches
# --------------------------------------------------------------------------- #


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "app.py").write_text("def value():\n    return 'old'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _seed_patches(repo: Path, count: int) -> list[str]:
    """Append ``count`` trace_patch_created events in event_sequence order."""
    now = datetime.now(timezone.utc)
    patch_ids: list[str] = []
    drafts: list[TrailEventDraft] = []
    for index in range(count):
        patch_id = f"tracepatch-sha256:seq-{index:04d}"
        patch_ids.append(patch_id)
        event_time = (now - timedelta(minutes=count - index)).isoformat().replace(
            "+00:00", "Z"
        )
        drafts.append(
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=f"tr-seq-{index:04d}",
                step_index=1,
                event_time=event_time,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": patch_id,
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": f"    return 'v{index}'\n",
                    "raw_authored_hash": sha256_text(f"v{index}"),
                    "git_clean_hash": sha256_text(f"v{index}"),
                    "limitations": [],
                },
            )
        )
    append_event_batch(repo, drafts, writer="test-fixture")
    return patch_ids


def _run(args: list[str]):
    return CliRunner().invoke(main, args)


def test_track_all_defaults_to_50_most_recent(tmp_path: Path) -> None:
    """``trail track --all`` with no ``--limit`` caps at the 50 NEWEST patches
    (default work budget) — an unbounded walk over the oldest patches is what
    hung on the live log."""
    _init_repo(tmp_path)
    patch_ids = _seed_patches(tmp_path, 55)

    res = _run(["trail", "track", "--all", "--json", "--project", str(tmp_path)])
    assert res.exit_code == 0, res.output
    rows = [json.loads(line) for line in res.output.splitlines() if line.strip()]
    assert len(rows) == 50

    # The 50 emitted are the TAIL (newest by event_sequence), not the oldest.
    emitted = {row["trace_patch_id"] for row in rows}
    assert emitted == set(patch_ids[-50:])
    assert patch_ids[0] not in emitted  # the oldest (slowest) patch is dropped


def test_track_limit_selects_the_newest_tail(tmp_path: Path) -> None:
    """``--limit N`` returns the N most-recent patches (the cheap tail), not the
    oldest N the pre-cure ``[:limit]`` head slice returned."""
    _init_repo(tmp_path)
    patch_ids = _seed_patches(tmp_path, 10)

    res = _run(
        ["trail", "track", "--all", "--json", "--limit", "3", "--project", str(tmp_path)]
    )
    assert res.exit_code == 0, res.output
    rows = [json.loads(line) for line in res.output.splitlines() if line.strip()]
    assert [row["trace_patch_id"] for row in rows] == patch_ids[-3:]


# --------------------------------------------------------------------------- #
# trail resolve ot://file/... — bounded event read
# --------------------------------------------------------------------------- #


def test_trail_resolve_file_takes_the_bounded_route(tmp_path: Path, monkeypatch) -> None:
    """``resolve_resource`` for an ``ot://file`` URI reads only the scoped
    patch/anchor slice — never the whole-log ``read_events``."""
    from opentraces.core.trails import event_log as event_log_mod
    from opentraces.core.trails.resources import resolve_resource

    _init_repo(tmp_path)
    _seed_patches(tmp_path, 3)

    def _boom(*_a, **_k):  # pragma: no cover - only on regression
        raise AssertionError("whole-log read_events must not be called by resolve")

    monkeypatch.setattr(event_log_mod, "read_events", _boom)
    # No git anchor for this line, so the payload is a bounded "unknown"
    # envelope — the point is it resolves without touching read_events.
    payload = resolve_resource(tmp_path, "ot://file/app.py/line/2/origin")
    assert payload["resource_type"] == "file_line_origin"


# --------------------------------------------------------------------------- #
# bucket manifest (read) — serve the persisted manifest.json
# --------------------------------------------------------------------------- #


def _trace_record(trace_id: str):
    from opentraces_schema import Agent, Step, TraceRecord

    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
        task={"description": "seed"},
        steps=[Step(step_index=1, role="user", content="seed")],
        outcome={"success": True, "committed": False},
    )


def test_bucket_manifest_read_serves_persisted_without_recompute(monkeypatch) -> None:
    """A plain ``bucket manifest`` read serves the persisted ``manifest.json``
    (O(1)) and never invokes the O(N) recompute."""
    from opentraces.core import bucket_store

    bucket_store.write_trace_record(
        _trace_record("trace-persist-1"),
        project_slug="proj-persist",
        source_layer="canonical",
    )
    # Materialize the persisted manifest.json.
    bucket_store.bucket_manifest(write=True, include_objects=False)

    def _boom(*_a, **_k):  # pragma: no cover - only on regression
        raise AssertionError("a plain read must serve persisted, not recompute")

    monkeypatch.setattr(bucket_store, "bucket_manifest", _boom)
    res = _run(["bucket", "manifest", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["status"] == "ok"
    assert payload["manifest"]["schema_version"] == "opentraces.bucket.manifest.v2"
    assert any(
        row["trace_id"] == "trace-persist-1"
        for row in payload["manifest"].get("traces", [])
    )


def test_bucket_manifest_heal_still_recomputes(monkeypatch) -> None:
    """``--heal`` keeps the recompute+materialize path (never served from the
    persisted file)."""
    from opentraces.core import bucket_store

    bucket_store.write_trace_record(
        _trace_record("trace-heal-x"),
        project_slug="proj-heal",
        source_layer="canonical",
    )
    called = {"n": 0}
    real = bucket_store.bucket_manifest

    def _spy(*a, **k):
        called["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(bucket_store, "bucket_manifest", _spy)
    res = _run(["bucket", "manifest", "--heal", "--json"])
    assert res.exit_code == 0, res.output
    assert called["n"] == 1


# --------------------------------------------------------------------------- #
# bucket verify --sample N — a true work budget
# --------------------------------------------------------------------------- #


def test_bucket_verify_sample_bounds_the_trace_walk(monkeypatch) -> None:
    """``--sample N`` bounds check-2's per-trace walk to <= N traces and never
    runs the whole-corpus events-mirror walk."""
    from opentraces.core import bucket_maintenance, bucket_store

    for i in range(8):
        bucket_store.write_trace_record(
            _trace_record(f"tv-{i:02d}"),
            project_slug="proj-verify",
            source_layer="canonical",
        )
    # Materialize per-trace envelopes so the trace-dir glob finds them.
    bucket_store.bucket_manifest(write=True, include_objects=False)

    per_trace_calls: list[tuple] = []
    real_refs = bucket_maintenance._layer_id_refs_for_trace

    def _spy_refs(proj, tid):
        per_trace_calls.append((proj, tid))
        return real_refs(proj, tid)

    def _mirror_boom():  # pragma: no cover - only on regression
        raise AssertionError(
            "the whole-corpus events-mirror walk must not run under --sample"
        )

    monkeypatch.setattr(bucket_maintenance, "_layer_id_refs_for_trace", _spy_refs)
    monkeypatch.setattr(
        bucket_maintenance, "_layer_id_refs_from_events_mirror", _mirror_boom
    )

    result = bucket_maintenance.bucket_verify(sample=3, full=False)

    assert len(per_trace_calls) <= 3, per_trace_calls
    assert result["ok"] is True, result["errors"]


def test_bucket_verify_full_still_walks_the_events_mirror(monkeypatch) -> None:
    """``--full`` keeps the exhaustive walk, including the events-mirror pass."""
    from opentraces.core import bucket_maintenance, bucket_store

    for i in range(4):
        bucket_store.write_trace_record(
            _trace_record(f"tf-{i:02d}"),
            project_slug="proj-verify-full",
            source_layer="canonical",
        )
    bucket_store.bucket_manifest(write=True, include_objects=False)

    called = {"mirror": 0}
    real = bucket_maintenance._layer_id_refs_from_events_mirror

    def _spy():
        called["mirror"] += 1
        return real()

    monkeypatch.setattr(bucket_maintenance, "_layer_id_refs_from_events_mirror", _spy)
    result = bucket_maintenance.bucket_verify(full=True)
    assert called["mirror"] == 1
    assert result["full"] is True
