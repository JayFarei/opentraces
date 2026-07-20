"""Issue #358 — anchor-search summary v3 (coverage-claim write path).

v2's ``git_anchor_search_completed`` summary carried one ``results[]`` dict per
SEARCHED Trace Patch, unknown outcomes included. On a mature repo the
never-anchored majority of those dicts fanned into every trace companion the
summary touched (26GB of 27GB in one observed bucket). v3 keeps ``results[]``
ANCHORED-ONLY and replaces the dropped unknown dicts with one ``coverage``
through-pointer claim (see design doc / ``search_records.iter_coverage_claims``).

These tests pin:
- the v3 wire shape (scalars unchanged, results anchored-only, coverage present)
  and that it is materially smaller than the v2-equivalent for the same run;
- that coverage-claim consumption — not just exact per-patch keys, which v3
  never records for an unknown outcome — is what makes a second identical run
  perform ZERO actual search work (instrumented via a call-count spy, not just
  an event/created count);
- a late-ingested patch is still searched despite an established claim;
- an ATTRIBUTION_VERSION bump forces re-search despite an established claim;
- a trace-scoped claim (R5, ``trail attach``) never leaks coverage to another
  trace's patch;
- a deadline break's partial coverage lets the remainder run search ONLY the
  unsearched tail (no livelock), even though every patch in the run is unknown
  (so v2-style exact keys would never have existed for them either);
- the tri-shape reader (legacy / v2 / v3-compact / v3-coverage) at the unit
  level, and that a v3 event fans out to anchored traces only.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    read_events,
    reconcile_commit_anchors,
)
import opentraces.core.trails.anchors as anchors_mod
import opentraces.core.trails.event_log as event_log
from opentraces.core.trails.contract import ANCHOR_SEARCH_SCHEMA_VERSION
from opentraces.core.trails.models import ATTRIBUTION_VERSION, TrailEvent
from opentraces.core.trails.search_records import (
    build_anchor_search_summary_payload,
    iter_coverage_claims,
    iter_search_records,
    summary_search_touches_trace,
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


def _count_calls(monkeypatch, module, name: str) -> dict[str, int]:
    """Wrap ``module.name`` with a call counter; returns the live counter dict."""
    original = getattr(module, name)
    calls = {"n": 0}

    def _wrapper(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, _wrapper)
    return calls


# --------------------------------------------------------------------------- #
# 1. Write path: scalars unchanged, results anchored-only, coverage present,
#    payload materially smaller than the v2-equivalent for the same run.
# --------------------------------------------------------------------------- #

def test_write_path_drops_unknown_dicts_and_carries_coverage(tmp_path):
    repo = tmp_path / "r"
    _init_repo(repo)
    n = 12
    for i in range(n):
        _emit_patch(repo, trace_id=f"tr{i}", trace_patch_id=f"tp-{i}",
                    file_path=f"mod_{i}.py", authored=f"UNIQUE_{i}\n")
    # Only tp-0 and tp-1 land in the commit; the other 10 stay unknown.
    head = _commit_files(
        repo, {"mod_0.py": "UNIQUE_0\n", "mod_1.py": "UNIQUE_1\n"}, "land two",
    )

    created = reconcile_commit_anchors(repo, head)
    assert len(created) == 2

    payload = _search_summaries(repo)[0].payload
    assert payload["schema_version"] == ANCHOR_SEARCH_SCHEMA_VERSION
    assert payload["searched"] == n
    assert payload["anchored"] == 2
    assert payload["unknown"] == n - 2
    assert len(payload["results"]) == 2, "results[] must carry ONLY anchored entries"
    assert {r["trace_patch_id"] for r in payload["results"]} == {"tp-0", "tp-1"}
    assert all(r["result"] == "anchored" for r in payload["results"])

    coverage = payload["coverage"]
    assert coverage["scope_trace_id"] is None, "unscoped run -> null scope"
    assert coverage["through_trace_patch_id"] == f"tp-{n - 1}", (
        "the claim covers through the LAST processed patch in sequence order"
    )

    # Size bound: the v2-equivalent (full mixed results[], no coverage) for the
    # SAME run is materially larger — the unknown dicts this run would have
    # carried under v2 are exactly what v3 replaces with one coverage claim.
    v2_equivalent = build_anchor_search_summary_payload(
        schema_version="opentraces.trail.anchor_search.v2",
        search_head=payload["search_head"],
        algorithms_attempted=payload["algorithms_attempted"],
        results=[
            {"trace_patch_id": f"tp-{i}", "trace_id": f"tr{i}", "step_index": 1,
             "generation_index": 0,
             "result": "anchored" if i in (0, 1) else "unknown",
             "created_anchor_ids": []}
            for i in range(n)
        ],
    )
    assert len(json.dumps(payload)) < len(json.dumps(v2_equivalent)) / 2


def test_build_summary_payload_without_coverage_keeps_v2_shape():
    """Pins that omitting ``coverage`` (legacy/v2 callers, search_compaction.py)
    leaves the full mixed results[] untouched — the anchored-only filter is
    strictly opt-in via ``coverage``, so compaction's byte-identical-functional-
    stream guarantee (test_search_compaction.py) is never at risk from this
    change."""
    results = [
        {"trace_patch_id": "a", "trace_id": "t1", "step_index": 0,
         "generation_index": 0, "result": "anchored", "created_anchor_ids": []},
        {"trace_patch_id": "b", "trace_id": "t2", "step_index": 0,
         "generation_index": 0, "result": "unknown", "created_anchor_ids": []},
    ]
    payload = build_anchor_search_summary_payload(
        schema_version=ANCHOR_SEARCH_SCHEMA_VERSION,
        search_head={"algo": "sha1", "hex": "c" * 40},
        algorithms_attempted=["exact_range_hash"],
        results=results,
    )
    assert payload["results"] == results
    assert "coverage" not in payload
    assert payload["searched"] == 2
    assert payload["anchored"] == 1
    assert payload["unknown"] == 1


# --------------------------------------------------------------------------- #
# 2. Dedup: coverage-claim consumption, not just exact keys, drives zero
#    re-search — instrumented via a call-count spy on _find_exact_anchor.
# --------------------------------------------------------------------------- #

def test_second_identical_run_performs_zero_actual_search(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    _init_repo(repo)
    for i in range(4):
        _emit_patch(repo, trace_id=f"tr{i}", trace_patch_id=f"tp-{i}",
                    file_path=f"m{i}.py", authored=f"V{i}\n")
    head = _commit_files(repo, {"m0.py": "V0\n"}, "land one")  # tp-1..3 unknown

    first = reconcile_commit_anchors(repo, head)
    assert len(first) == 1
    first_payload = _search_summaries(repo)[0].payload
    assert len(first_payload["results"]) == 1, "sanity: v3 already dropped the 3 unknowns"
    event_log.invalidate_read_events_cache(repo)

    calls = _count_calls(monkeypatch, anchors_mod, "_find_exact_anchor")
    second = reconcile_commit_anchors(repo, head)
    assert second == []
    assert calls["n"] == 0, (
        "the coverage claim must skip EVERY patch on an identical re-run, "
        "including the 3 unknown ones v3 never gave an exact key -- without "
        "claim consumption those 3 would be re-searched every run"
    )
    event_log.invalidate_read_events_cache(repo)
    assert len(_search_summaries(repo)) == 1, "no new summary event on a fully-covered re-run"


def test_late_ingested_patch_is_still_searched_despite_coverage(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    _init_repo(repo)
    _emit_patch(repo, trace_id="tr-a", trace_patch_id="tp-a", file_path="a.py", authored="A = 1\n")
    head = _commit_files(repo, {"a.py": "A = 1\n", "b.py": "B = 2\n"}, "land a")

    first = reconcile_commit_anchors(repo, head)
    assert {a["trace_patch_id"] for a in first} == {"tp-a"}
    event_log.invalidate_read_events_cache(repo)

    _emit_patch(repo, trace_id="tr-b", trace_patch_id="tp-b", file_path="b.py", authored="B = 2\n")
    event_log.invalidate_read_events_cache(repo)

    calls = _count_calls(monkeypatch, anchors_mod, "_find_exact_anchor")
    second = reconcile_commit_anchors(repo, head)
    assert calls["n"] == 1, "the late-ingested patch has a HIGHER sequence than the claim boundary"
    assert {a["trace_patch_id"] for a in second} == {"tp-b"}


def test_attribution_version_bump_forces_re_search(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    _init_repo(repo)
    for i in range(3):
        _emit_patch(repo, trace_id=f"tr{i}", trace_patch_id=f"tp-{i}",
                    file_path=f"m{i}.py", authored=f"V{i}\n")
    # No patch lands -> all 3 stay unknown, so the ONLY thing that could
    # suppress a re-search is the coverage claim (an anchor, unaffected by
    # ATTRIBUTION_VERSION, would confound this from the actual claim check).
    head = _commit_files(repo, {"unrelated.py": "noop\n"}, "no match")

    first = reconcile_commit_anchors(repo, head, attribution_version="0.1.0")
    assert first == []
    event_log.invalidate_read_events_cache(repo)

    calls = _count_calls(monkeypatch, anchors_mod, "_find_exact_anchor")
    second = reconcile_commit_anchors(repo, head, attribution_version="0.2.0")
    assert calls["n"] == 3, "a bumped ATTRIBUTION_VERSION must re-search every patch"
    assert second == []


def test_trace_scoped_coverage_claim_does_not_leak_to_other_trace(tmp_path, monkeypatch):
    """R5: attach.py's trace-scoped run claims coverage ONLY for its own trace.
    A later unscoped run must still search a DIFFERENT trace's patch even
    though that patch's canonical-log POSITION is earlier than the scoped
    claim's boundary (the leak a position-only check, without a scope
    check, would produce)."""
    repo = tmp_path / "r"
    _init_repo(repo)
    # tp-y (trace tr-y) is emitted FIRST (lower event_sequence) so it sits
    # BEFORE tp-x in canonical order -- a scope-blind position check would
    # wrongly treat it as covered once tr-x's scoped claim lands past it.
    _emit_patch(repo, trace_id="tr-y", trace_patch_id="tp-y", file_path="y.py", authored="Y = 2\n")
    _emit_patch(repo, trace_id="tr-x", trace_patch_id="tp-x", file_path="x.py", authored="X = 1\n")
    head = _commit_files(repo, {"x.py": "X = 1\n", "y.py": "Y = 2\n"}, "land both")

    from opentraces.core.trails.attach import attach_trace_to_commit

    scoped = attach_trace_to_commit(repo, "tr-x", head)
    assert {a["trace_patch_id"] for a in scoped} == {"tp-x"}
    scoped_payload = _search_summaries(repo)[0].payload
    assert scoped_payload["coverage"]["scope_trace_id"] == "tr-x"
    event_log.invalidate_read_events_cache(repo)

    calls = _count_calls(monkeypatch, anchors_mod, "_find_exact_anchor")
    unscoped = reconcile_commit_anchors(repo, head)
    assert calls["n"] == 1, "tr-y's patch must be searched -- a scoped claim must never leak"
    assert {a["trace_patch_id"] for a in unscoped} == {"tp-y"}


# --------------------------------------------------------------------------- #
# 3. Deadline break: partial coverage lets the remainder run search ONLY the
#    unsearched tail (no livelock) -- proven with an all-unknown scenario, so
#    v2-style exact keys would never have existed for the searched prefix.
# --------------------------------------------------------------------------- #

def test_deadline_break_resumes_tail_without_livelock(tmp_path, monkeypatch):
    import time as real_time

    repo = tmp_path / "r"
    _init_repo(repo)
    n = 5
    for i in range(n):
        _emit_patch(repo, trace_id=f"tr{i}", trace_patch_id=f"tp-{i}",
                    file_path=f"m{i}.py", authored=f"NEVER_MATCHES_{i}\n")
    head = _commit_files(repo, {"unrelated.py": "noop\n"}, "no match")  # all 5 unknown
    event_log.invalidate_read_events_cache(repo)

    deadline = 100.0
    ticks = iter([10.0, 10.0, 10.0, 999.0])

    def _fake_monotonic():
        try:
            return next(ticks)
        except StopIteration:
            return 999.0

    monkeypatch.setattr(anchors_mod.time, "monotonic", _fake_monotonic)
    summary1: dict = {}
    created1 = reconcile_commit_anchors(repo, head, summary_out=summary1, deadline=deadline)
    assert created1 == []
    assert summary1["budget_exhausted"] is True
    assert summary1["patches_searched"] == 3
    monkeypatch.undo()
    event_log.invalidate_read_events_cache(repo)

    payload1 = _search_summaries(repo)[0].payload
    assert payload1["results"] == [], "sanity: v3 never wrote an exact key for these 3 unknowns"
    assert payload1["coverage"]["through_trace_patch_id"] == "tp-2"

    calls = _count_calls(monkeypatch, anchors_mod, "_find_exact_anchor")
    summary2: dict = {}
    created2 = reconcile_commit_anchors(repo, head, summary_out=summary2)
    assert created2 == []
    assert calls["n"] == 2, (
        "the remainder run must search ONLY tp-3/tp-4 -- without coverage-claim "
        "consumption tp-0..tp-2 have no exact key under v3 and would be "
        "re-searched every tick (the livelock #65 already fixed for the old shape)"
    )
    assert summary2["patches_searched"] == 2


# --------------------------------------------------------------------------- #
# 4. Reader: tri-shape equivalence + iter_coverage_claims, at the unit level.
# --------------------------------------------------------------------------- #

def _summary_event(digit: str, payload: dict) -> TrailEvent:
    hex64 = digit * 64
    return TrailEvent.model_validate({
        "event_id": f"trailevent-sha256:{hex64}",
        "event_sequence": 1,
        "event_time": "2026-01-01T00:00:00Z",
        "trace_id": None,
        "step_index": None,
        "generation_index": 0,
        "batch_id": "b",
        "writer": "w",
        "capture_method": ["post_commit_correlator"],
        "event_type": "git_anchor_search_completed",
        "payload": payload,
        "content_hash": f"sha256:{hex64}",
        "ATTRIBUTION_VERSION": ATTRIBUTION_VERSION,
    })


def _dedup_key(record: dict) -> tuple:
    return (
        record["trace_patch_id"], record["search_head_sha"],
        record["attribution_version"], record["result"],
    )


def test_iter_search_records_v3_compact_yields_full_and_minimal_records():
    """v3-compact (results anchored-only + exact unanchored_trace_patch_ids)
    must yield the SAME dedup key set as an equivalent v2 fat summary."""
    v2 = _summary_event("2", {
        "summary": True,
        "schema_version": "opentraces.trail.anchor_search.v2",
        "search_head": {"algo": "sha1", "hex": "f" * 40},
        "algorithms_attempted": ["exact_range_hash"],
        "searched": 2, "anchored": 1, "unknown": 1,
        "results": [
            {"trace_patch_id": "abc", "trace_id": "tr1", "step_index": 1,
             "generation_index": 0, "result": "anchored", "created_anchor_ids": ["g1"]},
            {"trace_patch_id": "def", "trace_id": "tr2", "step_index": 2,
             "generation_index": 0, "result": "unknown", "created_anchor_ids": []},
        ],
    })
    v3_compact = _summary_event("3", {
        "summary": True,
        "schema_version": ANCHOR_SEARCH_SCHEMA_VERSION,
        "search_head": {"algo": "sha1", "hex": "f" * 40},
        "algorithms_attempted": ["exact_range_hash"],
        "searched": 2, "anchored": 1, "unknown": 1,
        "results": [
            {"trace_patch_id": "abc", "trace_id": "tr1", "step_index": 1,
             "generation_index": 0, "result": "anchored", "created_anchor_ids": ["g1"]},
        ],
        "unanchored_trace_patch_ids": ["def"],
    })

    v2_keys = sorted(_dedup_key(r) for r in iter_search_records(v2))
    v3_keys = sorted(_dedup_key(r) for r in iter_search_records(v3_compact))
    assert v2_keys == v3_keys
    assert list(iter_coverage_claims(v3_compact)) == []


def test_iter_search_records_v3_coverage_yields_anchored_only_no_claim_records():
    v3 = _summary_event("4", {
        "summary": True,
        "schema_version": ANCHOR_SEARCH_SCHEMA_VERSION,
        "search_head": {"algo": "sha1", "hex": "e" * 40},
        "algorithms_attempted": ["exact_range_hash"],
        "searched": 3, "anchored": 1, "unknown": 2,
        "results": [
            {"trace_patch_id": "onlyone", "trace_id": "tr1", "step_index": 1,
             "generation_index": 0, "result": "anchored", "created_anchor_ids": ["g1"]},
        ],
        "coverage": {"through_trace_patch_id": "zzz", "scope_trace_id": None},
    })
    records = list(iter_search_records(v3))
    assert len(records) == 1
    assert records[0]["trace_patch_id"] == "onlyone"
    assert records[0]["result"] == "anchored"

    claims = list(iter_coverage_claims(v3))
    assert len(claims) == 1
    assert claims[0] == {
        "search_head_sha": "e" * 40,
        "scope_trace_id": None,
        "through_trace_patch_id": "zzz",
        "attribution_version": ATTRIBUTION_VERSION,
    }


def test_iter_coverage_claims_ignores_legacy_and_v2():
    legacy = _summary_event("5", {
        "trace_patch_id": "abc",
        "search_head": {"algo": "sha1", "hex": "1" * 40},
        "algorithms_attempted": ["exact_range_hash"],
        "result": "unknown",
        "created_anchor_ids": [],
    })
    v2 = _summary_event("6", {
        "summary": True,
        "schema_version": "opentraces.trail.anchor_search.v2",
        "search_head": {"algo": "sha1", "hex": "1" * 40},
        "algorithms_attempted": ["exact_range_hash"],
        "searched": 1, "anchored": 0, "unknown": 1,
        "results": [
            {"trace_patch_id": "abc", "trace_id": "tr1", "step_index": 1,
             "generation_index": 0, "result": "unknown", "created_anchor_ids": []},
        ],
    })
    assert list(iter_coverage_claims(legacy)) == []
    assert list(iter_coverage_claims(v2)) == []


# --------------------------------------------------------------------------- #
# 5. Fan-out: a v3 event lands ONLY in anchored traces (naturally, since
#    results[] is anchored-only).
# --------------------------------------------------------------------------- #

def test_v3_summary_search_touches_trace_is_anchored_only(tmp_path):
    repo = tmp_path / "r"
    _init_repo(repo)
    _emit_patch(repo, trace_id="tr-anchored", trace_patch_id="tp-anchored",
                file_path="a.py", authored="A = 1\n")
    _emit_patch(repo, trace_id="tr-unknown", trace_patch_id="tp-unknown",
                file_path="b.py", authored="unmatched content\n")
    head = _commit_files(repo, {"a.py": "A = 1\n"}, "land one")

    reconcile_commit_anchors(repo, head)
    event = _search_summaries(repo)[0]

    assert summary_search_touches_trace(event, "tr-anchored") is True
    assert summary_search_touches_trace(event, "tr-unknown") is False, (
        "v3 keeps results[] anchored-only, so fan-out never touches a trace "
        "whose only patch in this summary was unknown"
    )
