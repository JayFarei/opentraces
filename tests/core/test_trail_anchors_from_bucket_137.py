"""HB#1 (#137): the capsule's companion-first Trail-anchor source.

The capsule is a CARRIED, zero-reachability snapshot, so export carries the
RECORDED anchor rows and does NOT recompute current liveness at seal time
(current survival is a reachability-bearing consumer operation, ``trail track``).
Properties pinned:

1. RECORDED, NOT RECOMPUTED: ``trail_anchors_from_bucket`` returns the projection's
   ``anchors_for_trace`` rows (companion-sourced) stamped
   ``survival_recomputed_at_export=False`` + the ``survival_not_recomputed_at_export``
   limitation + a ``survival_as_of`` — equal to ``recorded_anchor_rows`` over the
   live projection, and it never runs ``sync_patch`` (no per-anchor live walk).
2. NO RE-WALK: it never calls the whole-log ``read_events`` (it uses the
   companion) and never the per-anchor survival walk.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from opentraces.core.trails import event_log
from opentraces.core.trails import query as query_mod
from opentraces.core.trails import sync as sync_mod
from opentraces.core.trails.event_log import append_event_batch
from opentraces.core.trails.models import TrailEventDraft, sha256_text
from opentraces.core.trails.query import build_trail_query_projection_for_trace


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def _enroll(repo: Path) -> None:
    from opentraces.core.config import get_project_traces_dir

    marker = {"marker_version": "2", "project_id": "0123456789abcdef0123456789abcdef"}
    (repo / ".opentraces.json").write_text(json.dumps(marker))
    get_project_traces_dir(repo).mkdir(parents=True, exist_ok=True)


def _seed_anchored_trace(repo: Path) -> str:
    _init_repo(repo)
    _enroll(repo)
    (repo / "real.py").write_text("alpha\nbody\n")
    sha = _commit(repo, "real")
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="t-real",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": "tracepatch-sha256:realpatch01",
                    "file_path": "real.py",
                    "affected_range": {"start_line": 1, "end_line": 2},
                    "authored_text": "alpha\nbody\n",
                    "raw_authored_hash": sha256_text("alpha\nbody\n"),
                    "git_clean_hash": sha256_text("alpha\nbody"),
                    "limitations": [],
                },
            ),
            TrailEventDraft(
                event_type="git_anchor_created",
                trace_id="t-real",
                step_index=1,
                capture_method=["manual_attach"],
                payload={
                    "git_anchor_id": "gitanchor-sha256:realpatch01",
                    "trace_patch_id": "tracepatch-sha256:realpatch01",
                    "commit_id": {"algo": "sha1", "hex": sha},
                    "path": "real.py",
                    "range": {"start_line": 1, "end_line": 2},
                    "relation": "anchored_in_git",
                    "evidence_tier": "exact_range_hash",
                    "evidence_firmness": "firm",
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )
    return "t-real"


def _write_companion(repo: Path, trace_id: str) -> str:
    from opentraces.core.bucket_envelope import project_per_trace_exports
    from opentraces.core.config import get_project_dir

    slug = get_project_dir(repo).name
    project_per_trace_exports(repo, project_slug=slug, trace_id=trace_id)
    return slug


def test_companion_anchors_match_recorded_rows(tmp_path: Path) -> None:
    """Companion-sourced rows equal the RECORDED rows over the live projection
    (same anchors, no live survival recompute), and they are stamped
    not-recomputed."""
    repo = tmp_path / "proj"
    repo.mkdir()
    trace_id = _seed_anchored_trace(repo)
    slug = _write_companion(repo, trace_id)

    from opentraces.core.capsule.bucket_trail import (
        recorded_anchor_rows,
        trail_anchors_from_bucket,
    )

    live_recorded = recorded_anchor_rows(
        build_trail_query_projection_for_trace(repo, trace_id), trace_id
    )
    companion = trail_anchors_from_bucket(repo, slug, trace_id)

    assert companion is not None
    assert len(companion) == 1  # one anchored patch carried
    assert companion == live_recorded, (
        "companion-sourced trail anchors diverged from the recorded rows — the "
        "capsule's trail_anchors must not depend on which source resolved it"
    )
    # Carried-snapshot contract: declared not-recomputed, no live-walked survival.
    row = companion[0]
    assert row["survival_recomputed_at_export"] is False
    assert "survival_not_recomputed_at_export" in row["trail_limitations"]
    assert "survival_as_of" in row
    assert not row.get("current_survival")  # no live walk happened


def test_companion_path_does_no_whole_log_read(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "proj"
    repo.mkdir()
    trace_id = _seed_anchored_trace(repo)
    slug = _write_companion(repo, trace_id)

    from opentraces.core.capsule.bucket_trail import trail_anchors_from_bucket

    # Any whole-log read from the companion path is the per-row re-walk we removed.
    def _boom(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("trail_anchors_from_bucket called the whole-log read_events")

    monkeypatch.setattr(query_mod, "read_events", _boom)
    monkeypatch.setattr(sync_mod, "read_events", _boom)
    monkeypatch.setattr(event_log, "read_events", _boom)

    rows = trail_anchors_from_bucket(repo, slug, trace_id)
    assert rows is not None and len(rows) == 1


def test_missing_companion_returns_none(tmp_path: Path) -> None:
    """No companion (uncaptured / foreign trace) ⇒ None, so the caller falls back
    to the live read."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _seed_anchored_trace(repo)
    from opentraces.core.capsule.bucket_trail import trail_anchors_from_bucket
    from opentraces.core.config import get_project_dir

    slug = get_project_dir(repo).name
    assert trail_anchors_from_bucket(repo, slug, "nonexistent-trace") is None


def test_corrupt_companion_falls_back_to_live(tmp_path: Path) -> None:
    """Adversarial-review fix: a single unparseable line distrusts the WHOLE
    companion (all-or-nothing) → None → live fallback, never a silently-truncated
    anchor set."""
    import gzip

    from opentraces.core.bucket_layout import trace_v1_trail_path
    from opentraces.core.capsule.bucket_trail import trail_anchors_from_bucket

    repo = tmp_path / "proj"
    repo.mkdir()
    trace_id = _seed_anchored_trace(repo)
    slug = _write_companion(repo, trace_id)

    path = trace_v1_trail_path(slug, trace_id)
    lines = gzip.open(path, "rb").read().decode("utf-8").splitlines()
    assert lines  # the companion has content
    lines[-1] = "{ this is not valid json"  # corrupt one line; gzip stays valid
    with gzip.open(path, "wb") as fh:
        fh.write(("\n".join(lines) + "\n").encode("utf-8"))

    assert trail_anchors_from_bucket(repo, slug, trace_id) is None


def test_companion_path_does_not_poison_survival_cache(tmp_path: Path) -> None:
    """Adversarial-review fix: companion-sourced survival is computed FRESH with
    the global ``(patch, head)`` cache bypassed, so a (possibly stale) companion
    can never queue a ``patch_survival_cached`` event that later poisons a live
    read."""
    from opentraces.core.capsule.bucket_trail import trail_anchors_from_bucket
    from opentraces.core.trails.event_log import read_events_scoped

    repo = tmp_path / "proj"
    repo.mkdir()
    trace_id = _seed_anchored_trace(repo)
    slug = _write_companion(repo, trace_id)

    rows = trail_anchors_from_bucket(repo, slug, trace_id)
    assert rows is not None and len(rows) == 1
    cached = read_events_scoped(repo, event_types={"patch_survival_cached"})
    assert cached == [], (
        "companion-sourced survival wrote the global survival cache — a stale "
        "companion would poison later live reads"
    )


def test_companion_stream_skips_events_the_projection_never_reads(
    tmp_path: Path,
) -> None:
    """#208 perf-core round 2: the companion carries every non-context TrailEvent
    for the trace, not just anchor evidence — a real trail.jsonl.gz can be mostly
    ``filesystem_mutation_observed`` / window-open-close / snapshot noise. The
    streaming reader must drop those before ``TrailEvent`` construction (the
    memory win) while producing IDENTICAL anchor rows to a read that kept them,
    proving the filter is content-neutral for what this reader actually surfaces.
    """
    from opentraces.core.capsule.bucket_trail import (
        _read_trail_companion,
        trail_anchors_from_bucket,
    )

    repo = tmp_path / "proj"
    repo.mkdir()
    trace_id = _seed_anchored_trace(repo)
    # A large volume of an event type _build_projection_from_events ignores —
    # exactly the noise a heavily-anchored real trace accumulates.
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="filesystem_mutation_observed",
                trace_id=trace_id,
                step_index=1,
                capture_method=["fs_watcher"],
                payload={"path": f"noise-{i}.py", "kind": "modified"},
            )
            for i in range(50)
        ],
        writer="test-fixture",
    )
    slug = _write_companion(repo, trace_id)

    events = _read_trail_companion(slug, trace_id)
    assert events is not None
    assert {e.event_type for e in events} == {"trace_patch_created", "git_anchor_created"}
    assert len(events) == 2  # the 50 filesystem_mutation_observed rows never parsed

    # The anchors this reader actually surfaces are unaffected by the noise.
    rows = trail_anchors_from_bucket(repo, slug, trace_id)
    assert rows is not None and len(rows) == 1


def test_corrupt_ignored_event_type_does_not_distrust_companion(
    tmp_path: Path,
) -> None:
    """A malformed line of a type the projection never reads (here a payload
    that fails ``TrailEvent`` validation) cannot hide a dropped anchor, so it
    must not trip the all-or-nothing distrust — unlike a corrupt line of a
    *consumed* type (``test_corrupt_companion_falls_back_to_live``)."""
    import gzip
    import json as _json

    from opentraces.core.bucket_layout import trace_v1_trail_path
    from opentraces.core.capsule.bucket_trail import trail_anchors_from_bucket

    repo = tmp_path / "proj"
    repo.mkdir()
    trace_id = _seed_anchored_trace(repo)
    slug = _write_companion(repo, trace_id)

    path = trace_v1_trail_path(slug, trace_id)
    lines = gzip.open(path, "rb").read().decode("utf-8").splitlines()
    assert lines
    # Valid JSON, but not a valid TrailEvent (missing every required field) —
    # of a type the projection ignores. Appended, not substituted, so the two
    # real anchor-relevant lines survive untouched.
    lines.append(_json.dumps({"event_type": "filesystem_mutation_observed"}))
    with gzip.open(path, "wb") as fh:
        fh.write(("\n".join(lines) + "\n").encode("utf-8"))

    rows = trail_anchors_from_bucket(repo, slug, trace_id)
    assert rows is not None and len(rows) == 1, (
        "a malformed line of an ignored type must not fall back to the live read"
    )
