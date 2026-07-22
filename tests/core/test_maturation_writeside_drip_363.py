"""Issue #363 — residual write-side bloat: maturation's full-window sweep must
emit an O(1) coverage claim, not an O(backlog) exact-id list.

#358/#359 stopped v2's fat per-unknown-dict results[]. But maturation's flush
(``_flush_maturation_scratch``) still emitted the v3-*compact* shape: an EXACT
``unanchored_trace_patch_ids`` list of every unknown outcome recorded this run.
On a real machine with a large PERMANENT unanchored backlog (patches whose
originating commits are not in this repo's history — deleted worktrees,
squash-merged branches, old traces), every new commit re-searches that whole
backlog against it (unknown, never anchors) and the watcher's full-window
sweep re-emits the entire id list — measured ~0.5MB/event, ~9MB/min while the
watcher was live.

The hot path is the watcher's ``mature_trails(project_cwd, deadline=...)`` call
(``commit_refs=None``). On an UNTRUNCATED full-window sweep maturation's view is
provably complete — it streams every ``trace_patch_created`` in canonical order
into contiguous-prefix chunks and searches every patch against each commit, so
untruncated ⟹ every patch at-or-before the global-max position is
search-covered for every flushed commit. That is exactly the completeness the
coverage through-pointer claim requires, so this sweep may soundly mint the O(1)
``coverage`` claim in place of the O(backlog) id list.

A commit-subset (``commit_refs=[...]``) or a deadline-truncated sweep is NOT a
complete view and stays on the conservative exact-ids compact shape — those
paths are pinned unchanged in ``tests/core/test_anchor_search_v3.py`` and
``tests/capture/test_watcher_sweep.py``.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    mature_trails,
    read_events,
    reconcile_commit_anchors,
)
import opentraces.core.trails.event_log as event_log
from opentraces.core.trails.contract import ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION
from opentraces.core.trails.models import TrailEvent
from opentraces.core.trails.search_records import (
    build_anchor_search_summary_payload,
    iter_coverage_claims,
    iter_search_records,
)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _emit_patch(repo: Path, *, trace_id: str, trace_patch_id: str, file_path: str,
                authored: str, step_index: int = 1) -> None:
    append_event_batch(
        repo,
        [TrailEventDraft(
            event_type="trace_patch_created",
            trace_id=trace_id,
            generation_index=0,
            step_index=step_index,
            capture_method=["hook_posttooluse"],
            payload={
                "trace_patch_id": trace_patch_id,
                "file_path": file_path,
                "affected_range": {"start_line": 1, "end_line": 1},
                "authored_text": authored,
                "raw_authored_hash": "sha256:fixture",
                "git_clean_hash": "sha256:fixture",
                "limitations": [],
            },
        )],
        writer="capture-claude-code",
    )
    event_log.invalidate_read_events_cache(repo)


def _commit_files(repo: Path, files: dict[str, str], message: str) -> str:
    for path, content in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    event_log.invalidate_read_events_cache(repo)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def _search_summaries(repo: Path) -> list[TrailEvent]:
    return [
        e for e in read_events(repo, verify=False)
        if e.event_type == "git_anchor_search_completed"
    ]


def _seed_orphan_backlog(repo: Path, n: int) -> None:
    """Emit ``n`` patches whose authored text never appears in any commit — the
    permanent, never-anchorable backlog #363 is about."""
    for i in range(n):
        _emit_patch(
            repo,
            trace_id=f"tr-orphan-{i:04d}",
            trace_patch_id=f"tp-orphan-{i:04d}",
            file_path=f"ghost_{i:04d}.py",
            authored=f"ORPHAN_{i:04d}_NEVER_MATCHES\n",
        )


# --------------------------------------------------------------------------- #
# 1. The core #363 fix: a full-window (commit_refs=None) untruncated sweep over
#    a never-anchorable backlog emits an O(1) coverage claim, NOT the O(backlog)
#    unanchored_trace_patch_ids list.
# --------------------------------------------------------------------------- #

def test_full_sweep_orphan_backlog_emits_coverage_not_fat_id_list(tmp_path):
    repo = tmp_path / "r"
    _init_repo(repo)
    n = 25
    _seed_orphan_backlog(repo, n)
    _commit_files(repo, {"unrelated.py": "noop\n"}, "no match")  # all orphans unknown

    summary = mature_trails(repo)  # commit_refs=None -> the watcher hot path
    assert summary.truncated is False, "no deadline -> the sweep must complete"
    event_log.invalidate_read_events_cache(repo)

    events = _search_summaries(repo)
    assert events, "the full sweep must record at least one search summary"
    for event in events:
        payload = event.payload
        assert payload["schema_version"] == ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION
        assert "unanchored_trace_patch_ids" not in payload, (
            "the full-window untruncated sweep must NOT carry the O(backlog) "
            "exact-id list -- that is the #363 write-side drip"
        )
        assert "coverage" in payload, (
            "a provably-complete sweep must mint the O(1) coverage claim"
        )
        assert payload["coverage"]["scope_trace_id"] is None, (
            "an unscoped full sweep -> null scope"
        )
        # results[] stays anchored-only (empty here: every orphan is unknown).
        assert all(r["result"] == "anchored" for r in payload["results"])
        # The claim points through the last orphan in canonical order.
        assert payload["coverage"]["through_trace_patch_id"] == f"tp-orphan-{n - 1:04d}"
        # searched scalar stays honest (counts every patch searched this run).
        assert payload["searched"] == n
        assert payload["unknown"] == n


def test_write_size_independent_of_backlog_size(tmp_path):
    """The emitted event's byte size must be O(1) in the backlog size — the
    direct discriminator of the #363 O(backlog) drip. Fixed-width ids make the
    two payloads byte-identical under the coverage-claim shape; the pre-fix
    exact-id shape grows one id per backlog patch."""
    def _max_event_bytes(repo: Path, n: int) -> int:
        _init_repo(repo)
        _seed_orphan_backlog(repo, n)
        _commit_files(repo, {"unrelated.py": "noop\n"}, "no match")
        mature_trails(repo)
        event_log.invalidate_read_events_cache(repo)
        return max(len(json.dumps(e.payload)) for e in _search_summaries(repo))

    small = _max_event_bytes(tmp_path / "small", 6)
    large = _max_event_bytes(tmp_path / "large", 60)
    assert abs(large - small) < 40, (
        f"event size scales with backlog (small={small}B, large={large}B) -- "
        "the O(backlog) unanchored_trace_patch_ids drip is still present"
    )


def test_full_sweep_coverage_claim_dedups_next_tick_and_stamps_watermark(tmp_path):
    """The coverage claim must be SOUND: a second identical full sweep re-reads
    it, treats every covered orphan as already-searched, and records ZERO new
    search summaries. And because the mint condition equals the watermark-stamp
    condition, the sweep also stamps the maturation watermark (so the quiet-tick
    gate short-circuits and never re-enters despite the gate being
    coverage-blind by design)."""
    from opentraces.core.trails.maturation import (
        _load_maturation_watermark,
        has_unsearched_recent_patches,
    )

    repo = tmp_path / "r"
    _init_repo(repo)
    _seed_orphan_backlog(repo, 12)
    _commit_files(repo, {"unrelated.py": "noop\n"}, "no match")

    mature_trails(repo)
    event_log.invalidate_read_events_cache(repo)
    first = _search_summaries(repo)
    assert first, "first sweep records coverage summaries"
    assert _load_maturation_watermark(repo) is not None, (
        "an untruncated full sweep must stamp the watermark"
    )
    assert has_unsearched_recent_patches(repo) is False, (
        "the coverage claim + stamped watermark must leave nothing unsearched"
    )

    mature_trails(repo)
    event_log.invalidate_read_events_cache(repo)
    after = _search_summaries(repo)
    assert len(after) == len(first), (
        "the coverage claim must dedup every orphan -- a second sweep records "
        "no new summary event (else the claim is not being consumed)"
    )


# --------------------------------------------------------------------------- #
# 2. Regression: anchorable patches still anchor via the full-window sweep, and
#    the tri-shape reader still parses every on-disk shape unchanged.
# --------------------------------------------------------------------------- #

def test_full_sweep_still_anchors_anchorable_patch(tmp_path):
    repo = tmp_path / "r"
    _init_repo(repo)
    # A backlog of orphans PLUS one patch whose content genuinely lands.
    _seed_orphan_backlog(repo, 10)
    _emit_patch(repo, trace_id="tr-real", trace_patch_id="tp-real",
                file_path="real.py", authored="REAL_LANDS = 1\n")
    _commit_files(repo, {"real.py": "REAL_LANDS = 1\n"}, "land the real patch")

    summary = mature_trails(repo)
    assert summary.truncated is False
    assert summary.anchors_created >= 1, "the anchorable patch must still anchor"
    event_log.invalidate_read_events_cache(repo)

    anchors = [
        e for e in read_events(repo, verify=False)
        if e.event_type == "git_anchor_created"
        and e.payload.get("trace_patch_id") == "tp-real"
    ]
    assert anchors, "tp-real must have a git_anchor_created event"

    # The commit that landed tp-real carries a coverage summary whose
    # anchored-only results[] includes tp-real.
    landed = [
        e for e in _search_summaries(repo)
        if any(r.get("trace_patch_id") == "tp-real" for r in e.payload.get("results", []))
    ]
    assert landed, "tp-real's anchored outcome must appear in results[]"
    assert "coverage" in landed[0].payload
    assert "unanchored_trace_patch_ids" not in landed[0].payload


def test_tri_shape_reader_still_parses_all_shapes():
    """Guard that the fix does not disturb the tri-shape reader: legacy per-patch,
    v2 full-mixed, v3-compact (exact ids), and v3-coverage all still parse."""
    head = {"algo": "sha1", "hex": "deadbeef"}

    class _Ev:
        def __init__(self, payload, **attrs):
            self.event_type = "git_anchor_search_completed"
            self.payload = payload
            self.event_id = "e"
            self.event_sequence = 1
            self.capture_method = ["x"]
            self.event_time = None
            self.trace_id = attrs.get("trace_id")
            self.step_index = attrs.get("step_index")
            self.generation_index = attrs.get("generation_index")
            self.ATTRIBUTION_VERSION = attrs.get("attribution_version", "0.1.0")

    # Legacy per-patch shape (no summary key).
    legacy = _Ev(
        {"search_head": head, "algorithms_attempted": ["exact_range_hash"],
         "trace_patch_id": "tp-legacy", "result": "anchored", "created_anchor_ids": []},
        trace_id="tr-l",
    )
    legacy_records = list(iter_search_records(legacy))
    assert len(legacy_records) == 1
    assert legacy_records[0]["trace_patch_id"] == "tp-legacy"

    # v2 full-mixed summary (results carries both anchored and unknown dicts).
    v2 = _Ev(build_anchor_search_summary_payload(
        schema_version="opentraces.trail.anchor_search.v2",
        search_head=head, algorithms_attempted=["exact_range_hash"],
        results=[
            {"trace_patch_id": "tp-a", "trace_id": "tr-a", "result": "anchored",
             "created_anchor_ids": []},
            {"trace_patch_id": "tp-u", "trace_id": "tr-u", "result": "unknown",
             "created_anchor_ids": []},
        ],
    ))
    v2_ids = {r["trace_patch_id"] for r in iter_search_records(v2)}
    assert v2_ids == {"tp-a", "tp-u"}
    assert list(iter_coverage_claims(v2)) == []

    # v3-compact: anchored-only results[] + exact unanchored ids.
    v3_compact = _Ev(build_anchor_search_summary_payload(
        schema_version=ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION,
        search_head=head, algorithms_attempted=["exact_range_hash"],
        results=[
            {"trace_patch_id": "tp-a", "trace_id": "tr-a", "result": "anchored",
             "created_anchor_ids": []},
            {"trace_patch_id": "tp-u", "trace_id": "tr-u", "result": "unknown",
             "created_anchor_ids": []},
        ],
        unanchored_trace_patch_ids=["tp-u"],
    ))
    compact = {r["trace_patch_id"]: r["result"] for r in iter_search_records(v3_compact)}
    assert compact == {"tp-a": "anchored", "tp-u": "unknown"}
    assert list(iter_coverage_claims(v3_compact)) == []

    # v3-coverage: anchored-only results[] + a coverage through-pointer claim.
    v3_cov = _Ev(build_anchor_search_summary_payload(
        schema_version=ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION,
        search_head=head, algorithms_attempted=["exact_range_hash"],
        results=[
            {"trace_patch_id": "tp-a", "trace_id": "tr-a", "result": "anchored",
             "created_anchor_ids": []},
            {"trace_patch_id": "tp-u", "trace_id": "tr-u", "result": "unknown",
             "created_anchor_ids": []},
        ],
        coverage={"through_trace_patch_id": "tp-u", "scope_trace_id": None},
    ))
    cov_records = [r["trace_patch_id"] for r in iter_search_records(v3_cov)]
    assert cov_records == ["tp-a"], "coverage variant yields only anchored records"
    claims = list(iter_coverage_claims(v3_cov))
    assert len(claims) == 1
    assert claims[0]["through_trace_patch_id"] == "tp-u"
    assert claims[0]["scope_trace_id"] is None


# --------------------------------------------------------------------------- #
# 3. The conservative boundary stays put: a commit-subset sweep (not a complete
#    view) keeps the exact-ids compact shape, never a coverage claim.
# --------------------------------------------------------------------------- #

def test_commit_subset_sweep_keeps_exact_ids_not_coverage(tmp_path):
    repo = tmp_path / "r"
    _init_repo(repo)
    _emit_patch(repo, trace_id="tr-x", trace_patch_id="tp-x",
                file_path="x.py", authored="X_NEVER = 1\n")
    head = _commit_files(repo, {"unrelated.py": "noop\n"}, "no match")

    mature_trails(repo, commit_refs=[head])  # explicit subset -> NOT complete
    event_log.invalidate_read_events_cache(repo)

    events = [e for e in _search_summaries(repo)
              if e.payload.get("search_head", {}).get("hex") == head]
    assert events
    payload = events[-1].payload
    assert "coverage" not in payload, (
        "a commit-subset sweep is not a complete view -- it must stay on the "
        "conservative exact-ids shape"
    )
    assert payload["unanchored_trace_patch_ids"] == ["tp-x"]
