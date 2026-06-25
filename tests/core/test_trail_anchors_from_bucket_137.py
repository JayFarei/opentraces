"""HB#1 (#137): the capsule's companion-first Trail-anchor source.

Two properties pinned:

1. FIDELITY: ``trail_anchors_from_bucket`` returns rows byte-identical to the
   live ``build_trail_query_projection_for_trace(...).anchors_for_trace_with_survival``
   — the companion path must not change the capsule's ``trail_anchors`` content.
2. NO RE-WALK: it never calls the whole-log ``read_events`` — neither to build
   the projection (it uses the companion) nor in the survival pass (the same
   events are threaded into ``sync_patch``, so ``_sync`` never hits its
   ``read_events(repo)`` cache-miss slow path that ran once PER anchor row).
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


def test_companion_anchors_are_byte_identical_to_live(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    repo.mkdir()
    trace_id = _seed_anchored_trace(repo)
    slug = _write_companion(repo, trace_id)

    from opentraces.core.capsule.bucket_trail import trail_anchors_from_bucket

    live = build_trail_query_projection_for_trace(
        repo, trace_id
    ).anchors_for_trace_with_survival(trace_id)
    companion = trail_anchors_from_bucket(repo, slug, trace_id)

    assert companion is not None  # the companion exists
    assert len(companion) == 1  # one anchored patch
    assert companion == [dict(r) for r in live], (
        "companion-sourced trail anchors diverged from the live builder — the "
        "capsule's trail_anchors content must not depend on which source resolved it"
    )


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
