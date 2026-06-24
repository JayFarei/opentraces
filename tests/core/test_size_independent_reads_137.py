"""Verifier for #137 — size-independent event-log reads (verifier-driven).

This module is committed RED first and IS the loop's stop-condition. The work is
done when the RED-driver checks below go green AND the GREEN constraints stay
green (byte-identity, completeness, ref invariance).

THE AXIS THAT MATTERS IS THE BYTE-STREAM, NOT THE PARSE COUNT.
plan-120's ``test_commit_scoped_read_is_bounded`` spies on
``TrailEvent.model_validate_json`` (the JSON-parse count) and is green, yet a
zero-match per-trace read still measured 12.0s on the 919k-event ref, because
``read_events_for_trace`` / ``read_events_scoped`` still STREAM every event
blob's bytes through ``_iter_blobs_batch`` and only skip the JSON parse. So the
felt hang is the byte-stream. These checks assert how many blobs get their bytes
READ, which is the wall-time driver, and they are the axis the prior cures
missed.

Red drivers (drive to green): a single-key read must NOT stream the whole log.
Green constraints (must not regress): the bounded read returns the SAME events
as the full read (no speed-by-dropping-events), rows stay deep-equal to the full
Trail Query builder once the bounded per-trace builder lands, and a read never
mutates the canonical ref. Real-scale evidence: an opt-in probe asserts the
zero-match read is sub-second on the actual mature ref (12s today).
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from opentraces.core.trails import event_log as event_log_mod
from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    build_trail_query_projection,
)
from opentraces.core.trails.event_log import (
    _list_event_blob_entries,
    _ref_head,
    read_events,
    read_events_for_trace,
)
from opentraces.core.trails.models import sha256_text

# O(result) bound: a single-key read may touch only a handful of blobs beyond
# its own events, NEVER the whole log. The synthetic log below floods the ref
# with >= 250 noise blobs, so any whole-log stream blows far past this bound
# (the #137 red state). This is the byte-stream axis, not the parse axis.
MAX_BLOBS_FOR_BOUNDED_READ = 24
N_NOISE = 250

# Wall-time ceiling for the opt-in real-scale probe (red baseline: ~12.0s).
MATURE_ZERO_MATCH_CEILING_S = 1.0


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


def _patch_anchor_drafts(
    *, trace_id: str, patch_id: str, anchor_commit: str, file_path: str, text: str
) -> list[TrailEventDraft]:
    return [
        TrailEventDraft(
            event_type="trace_patch_created",
            trace_id=trace_id,
            step_index=1,
            capture_method=["hook_posttooluse"],
            payload={
                "trace_patch_id": f"tracepatch-sha256:{patch_id}",
                "file_path": file_path,
                "affected_range": {"start_line": 1, "end_line": 2},
                "authored_text": text,
                "raw_authored_hash": sha256_text(text),
                "git_clean_hash": sha256_text(text.strip()),
                "limitations": [],
            },
        ),
        TrailEventDraft(
            event_type="git_anchor_created",
            trace_id=trace_id,
            step_index=1,
            capture_method=["manual_attach"],
            payload={
                "git_anchor_id": f"gitanchor-sha256:{patch_id}",
                "trace_patch_id": f"tracepatch-sha256:{patch_id}",
                "commit_id": {"algo": "sha1", "hex": anchor_commit},
                "path": file_path,
                "range": {"start_line": 1, "end_line": 2},
                "relation": "anchored_in_git",
                "evidence_tier": "exact_range_hash",
                "evidence_firmness": "firm",
                "limitations": [],
            },
        ),
    ]


def _seed_world(repo: Path, *, n_noise: int = N_NOISE) -> str:
    """One real trace ('t-real') anchored on a real commit, then a flood of
    foreign noise search events so the total blob count dwarfs the target
    trace's two events. Returns the real commit sha."""
    _init_repo(repo)
    (repo / "real.py").write_text("alpha\nbody\n")
    real_sha = _commit(repo, "real commit")
    append_event_batch(
        repo,
        _patch_anchor_drafts(
            trace_id="t-real",
            patch_id="realpatch01",
            anchor_commit=real_sha,
            file_path="real.py",
            text="alpha\nbody\n",
        ),
        writer="test-fixture",
    )
    noise: list[TrailEventDraft] = []
    for i in range(n_noise):
        noise.append(
            TrailEventDraft(
                event_type="git_anchor_search_completed",
                trace_id=None,
                step_index=None,
                capture_method=["reconcile"],
                payload={
                    "schema_version": "opentraces.trail.anchor_search.v2",
                    "summary": True,
                    "search_head": {"algo": "sha1", "hex": f"{i:040x}"},
                    "algorithms_attempted": ["exact_range_hash"],
                    "searched": 1,
                    "anchored": 0,
                    "unknown": 1,
                    "results": [
                        {
                            "trace_patch_id": f"tracepatch-sha256:noise{i:05d}",
                            "result": "unknown",
                            "created_anchor_ids": [],
                        }
                    ],
                },
            )
        )
    append_event_batch(repo, noise, writer="test-fixture")
    return real_sha


def _streamed_blob_count(monkeypatch, fn):
    """Count how many event blobs get their BYTES read during ``fn()`` by
    wrapping ``_iter_blobs_batch`` (the byte-stream, the wall-time driver).
    A whole-log stream yields one per blob in the ref; a bounded read yields
    only the wanted blobs."""
    orig = event_log_mod._iter_blobs_batch
    seen = {"n": 0}

    def spy(cwd, entries, *args, **kwargs):
        for raw in orig(cwd, entries, *args, **kwargs):
            seen["n"] += 1
            yield raw

    monkeypatch.setattr(event_log_mod, "_iter_blobs_batch", spy)
    out = fn()
    monkeypatch.undo()
    return seen["n"], out


# --------------------------------------------------------------------------- #
# RED drivers — fail today on the existing read_events_for_trace, drive to green
# --------------------------------------------------------------------------- #


def test_zero_match_read_does_not_stream_whole_log(tmp_path: Path, monkeypatch) -> None:
    """A per-trace read whose result is EMPTY must not pay the whole-log byte
    stream. Empty result + full cost is the exact #137 symptom (12s for nothing).
    """
    _seed_world(tmp_path)
    total = len(_list_event_blob_entries(tmp_path, _ref_head(tmp_path)))
    assert total >= N_NOISE  # the noise flood dominates the log

    n_streamed, events = _streamed_blob_count(
        monkeypatch,
        lambda: read_events_for_trace(tmp_path, "nonexistent-" + "0" * 20),
    )
    assert events == []  # truly zero-match
    assert n_streamed <= MAX_BLOBS_FOR_BOUNDED_READ, (
        f"zero-match read streamed {n_streamed}/{total} event blobs — the cost "
        f"is the LOG SIZE, not the (empty) result. This is the #137 red state; "
        f"route the read through the index/companion to bound it."
    )


def test_known_trace_read_is_bounded(tmp_path: Path, monkeypatch) -> None:
    """Reading one real trace's events must not stream the whole noise flood to
    find them."""
    _seed_world(tmp_path)
    n_streamed, events = _streamed_blob_count(
        monkeypatch, lambda: read_events_for_trace(tmp_path, "t-real")
    )
    assert len(events) == 2  # complete: the trace's patch + anchor events
    assert n_streamed <= MAX_BLOBS_FOR_BOUNDED_READ, (
        f"known-trace read streamed {n_streamed} event blobs to return 2 — "
        f"size-dependent (the #137 red state)."
    )


# --------------------------------------------------------------------------- #
# GREEN constraints — must stay green (no speed-by-dropping-events, byte-identity)
# --------------------------------------------------------------------------- #


def test_known_trace_events_complete_and_identical(tmp_path: Path) -> None:
    """Completeness/identity constraint: the bounded read returns exactly the
    same events (same ids, same order) as filtering a full read. Speed must
    never be bought by silently dropping a trace's events."""
    _seed_world(tmp_path)
    full = [e for e in read_events(tmp_path) if e.trace_id == "t-real"]
    scoped = read_events_for_trace(tmp_path, "t-real")
    assert [e.event_id for e in scoped] == [e.event_id for e in full]
    assert len(scoped) == 2


def test_bounded_per_trace_builder_matches_full(tmp_path: Path) -> None:
    """Byte-identity differential vs the full Trail Query builder. The bounded
    per-trace builder is what #137 adds; until it exists this is a pending
    differential the loop makes real (it must NOT stay skipped once the fix
    lands — the final gate asserts the symbol exists)."""
    _seed_world(tmp_path, n_noise=20)
    full = build_trail_query_projection(tmp_path)
    expected = full.anchors_for_trace_with_survival("t-real")
    assert len(expected) == 1  # one anchored patch
    try:
        from opentraces.core.trails.query import (  # noqa: PLC0415
            build_trail_query_projection_for_trace,
        )
    except ImportError:
        pytest.skip("build_trail_query_projection_for_trace not implemented yet (#137)")
    scoped = build_trail_query_projection_for_trace(tmp_path, "t-real")
    assert scoped.anchors_for_trace_with_survival("t-real") == expected


def test_read_does_not_mutate_canonical_ref(tmp_path: Path) -> None:
    """Invariance constraint: a read leaves the canonical event ref OID
    byte-unchanged (no accidental cache-event write on the read path)."""
    _seed_world(tmp_path, n_noise=20)
    before = _ref_head(tmp_path)
    read_events_for_trace(tmp_path, "t-real")
    read_events_for_trace(tmp_path, "nonexistent-" + "0" * 20)
    assert _ref_head(tmp_path) == before


# --------------------------------------------------------------------------- #
# Real-scale evidence — opt-in probe against the ACTUAL mature ref (red ~12s)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not os.environ.get("OT_MATURE_LOG_PROBE"),
    reason="set OT_MATURE_LOG_PROBE=1 and run in a repo with a mature events ref",
)
def test_mature_log_zero_match_under_ceiling() -> None:
    """Headline real-scale check: a zero-match per-trace read on the live mature
    ref completes under the ceiling. Red baseline on this machine: ~12.0s for an
    empty result. Not portable to CI (CI has no mature ref), so it is opt-in;
    the deterministic byte-stream checks above are the portable gate."""
    repo = Path(os.environ.get("OT_MATURE_LOG_REPO", ".")).resolve()
    if _ref_head(repo) is None:
        pytest.fail(f"no events ref in {repo} — cannot run the mature-log probe")
    t0 = time.perf_counter()
    events = read_events_for_trace(repo, "nonexistent-mature-probe-" + "0" * 20)
    elapsed = time.perf_counter() - t0
    assert events == []
    assert elapsed < MATURE_ZERO_MATCH_CEILING_S, (
        f"zero-match read took {elapsed:.2f}s on the mature ref (ceiling "
        f"{MATURE_ZERO_MATCH_CEILING_S}s). #137 red baseline was ~12s; the read "
        f"is still O(total-log) in wall-time."
    )
