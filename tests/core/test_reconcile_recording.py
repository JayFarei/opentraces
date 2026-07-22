"""Plan 090 — anchor-search summary recording (Approach A').

reconcile_commit_anchors now records ONE per-commit ``git_anchor_search_completed``
summary event carrying per-patch ``results`` instead of N per-patch events. These
guards pin the requirements:

- C2/R2: bounded growth — a reconcile appends exactly K+1 events (K anchors + 1
  summary); a 0-anchor reconcile appends exactly 1; a re-reconcile appends 0.
- C3/R5: the SET of git_anchor_created PAYLOADS (by content_hash) is identical to
  a hand-rolled legacy per-patch reconcile, including the late-patch scenario.
- The dual-shape iter_search_records reader yields identical normalized records
  from BOTH the legacy per-patch shape and the v2 summary shape (R7).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from opentraces.core.trails import (
    GitObjectID,
    TrailEventDraft,
    append_event_batch,
    iter_search_records,
    read_events,
    reconcile_commit_anchors,
)
import opentraces.core.trails.event_log as event_log
from opentraces.core.trails.models import TrailEvent, ATTRIBUTION_VERSION


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _hash_object(repo: Path, content: str) -> str:
    return subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo, input=content, text=True, capture_output=True, check=True,
    ).stdout.strip()


def _emit_patch(repo: Path, *, trace_id: str, trace_patch_id: str, file_path: str,
                authored: str, step_index: int = 1) -> None:
    after = GitObjectID(hex=_hash_object(repo, authored))
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
                "after_blob_id": after.model_dump(mode="json"),
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


# --------------------------------------------------------------------------- #
# C2/R2 — bounded growth
# --------------------------------------------------------------------------- #

def test_zero_anchor_reconcile_appends_exactly_one_summary_event(tmp_path):
    repo = tmp_path / "r"
    _init_repo(repo)
    for i in range(5):
        _emit_patch(repo, trace_id=f"tr{i}", trace_patch_id=f"tp-{i}",
                    file_path=f"mod_{i}.py", authored=f"UNIQUE_{i}\n")
    head = _commit_files(repo, {"unrelated.py": "print('x')\n"}, "no match")

    before = len(read_events(repo, verify=False))
    event_log.invalidate_read_events_cache(repo)
    created = reconcile_commit_anchors(repo, head)
    event_log.invalidate_read_events_cache(repo)
    after = len(read_events(repo, verify=False))

    assert created == []
    assert after - before == 1, "0-anchor reconcile must append exactly 1 summary event"
    summaries = _search_summaries(repo)
    assert len(summaries) == 1
    payload = summaries[0].payload
    assert payload["summary"] is True
    assert payload["searched"] == 5
    assert payload["anchored"] == 0
    assert payload["unknown"] == 5
    # #358 v3: results[] is ANCHORED-ONLY — a fully-unknown reconcile carries
    # zero result dicts. Coverage takes over: the through-pointer claims the
    # 5 searched-but-unknown patches without a per-patch dict for any of them.
    assert payload["results"] == []
    assert payload["coverage"]["scope_trace_id"] is None
    assert payload["coverage"]["through_trace_patch_id"] == "tp-4"


def test_k_anchor_reconcile_appends_k_plus_one_events(tmp_path):
    repo = tmp_path / "r"
    _init_repo(repo)
    # 5 patches; 2 will match the commit.
    for i in range(5):
        _emit_patch(repo, trace_id=f"tr{i}", trace_patch_id=f"tp-{i}",
                    file_path=f"mod_{i}.py", authored=f"LINE_{i} = {i}\n")
    head = _commit_files(
        repo,
        {"mod_0.py": "LINE_0 = 0\n", "mod_1.py": "LINE_1 = 1\n"},
        "land two",
    )

    before = len(read_events(repo, verify=False))
    event_log.invalidate_read_events_cache(repo)
    created = reconcile_commit_anchors(repo, head)
    event_log.invalidate_read_events_cache(repo)
    after = len(read_events(repo, verify=False))

    assert len(created) == 2
    assert after - before == 3, "K=2 reconcile must append exactly K+1=3 events"
    payload = _search_summaries(repo)[0].payload
    assert payload["searched"] == 5
    assert payload["anchored"] == 2
    assert payload["unknown"] == 3


def test_re_reconcile_same_commit_appends_zero(tmp_path):
    repo = tmp_path / "r"
    _init_repo(repo)
    _emit_patch(repo, trace_id="tr0", trace_patch_id="tp-0",
                file_path="mod.py", authored="LINE = 1\n")
    head = _commit_files(repo, {"mod.py": "LINE = 1\n"}, "land")

    reconcile_commit_anchors(repo, head)
    event_log.invalidate_read_events_cache(repo)
    before = len(read_events(repo, verify=False))
    event_log.invalidate_read_events_cache(repo)
    created = reconcile_commit_anchors(repo, head)
    event_log.invalidate_read_events_cache(repo)
    after = len(read_events(repo, verify=False))

    assert created == []
    assert after - before == 0, "idempotent re-reconcile must append nothing"


# --------------------------------------------------------------------------- #
# C3/R5 — anchor-set equivalence vs a legacy per-patch reconcile
# --------------------------------------------------------------------------- #

def _legacy_per_patch_reconcile(repo: Path, commit: str) -> set[str]:
    """Re-derive the legacy per-patch anchor PAYLOAD set by replaying the same
    matcher the new reconcile uses, but recording per patch. We don't write
    events here — we just collect the anchor payloads' content_hashes so we can
    compare the SET against the new summary-recording reconcile (R5).
    """
    from opentraces.core.trails.anchors import (
        _find_exact_anchor,
        _find_structural_anchor,
        _oid,
        _stable_patch_id,
    )
    from opentraces.core.trails.ids import (
        GIT_ANCHOR_CANONICALIZATION,
        content_ref,
        git_anchor_ref,
        id_from_payload,
        trace_patch_ref,
    )
    from opentraces.enrichment.attribution import _parse_diff_hunks_with_content
    from opentraces.core.trails.models import payload_content_hash

    diff = subprocess.check_output(
        ["git", "show", "--format=", "--no-color", "-U3", commit], cwd=repo, text=True
    )
    hunks = _parse_diff_hunks_with_content(diff)
    commit_id = {"algo": "sha1", "hex": commit}
    hashes: set[str] = set()
    for event in read_events(repo, verify=False):
        if event.event_type != "trace_patch_created":
            continue
        patch = event.payload
        tp_id = id_from_payload(patch, "trace_patch")
        if not tp_id:
            continue
        match = _find_exact_anchor(patch, hunks)
        tier = "exact_range_hash"
        firmness = "firm"
        lims: list[str] = []
        if match is None:
            structural = _find_structural_anchor(patch, hunks)
            if structural is not None:
                match = {"path": structural["path"], "range": structural["range"]}
                tier = "structural_match"
                firmness = "provisional"
                lims.append("structural_match_below_exact_threshold")
        if not match:
            continue
        ref = content_ref(
            kind="git_anchor",
            canonicalization=GIT_ANCHOR_CANONICALIZATION,
            relation="anchored_in_git",
            material={
                "trace_patch_ref": trace_patch_ref(tp_id),
                "commit_id": commit_id,
                "path": match["path"],
                "range": match["range"],
                "evidence_tier": tier,
            },
        )
        ga_id = ref["id"]
        payload = {
            "git_anchor_id": ga_id,
            "git_anchor_ref": git_anchor_ref(ga_id),
            "trace_patch_id": tp_id,
            "trace_patch_ref": trace_patch_ref(tp_id),
            "commit_id": commit_id,
            "path": match["path"],
            "range": match["range"],
            "blob_id": _oid(repo, f"{commit}:{match['path']}"),
            "patch_id": _stable_patch_id(repo, commit),
            "observed_ref": commit,
            "relation": "anchored_in_git",
            "evidence_tier": tier,
            "evidence_firmness": firmness,
            "source": "post-commit-correlator",
            "limitations": lims,
        }
        hashes.add(payload_content_hash(payload))
    return hashes


def test_anchor_payload_set_matches_legacy_per_patch(tmp_path):
    repo = tmp_path / "r"
    _init_repo(repo)
    for i in range(6):
        _emit_patch(repo, trace_id=f"tr{i}", trace_patch_id=f"tp-{i}",
                    file_path=f"mod_{i}.py", authored=f"VALUE_{i} = {i}\n")
    head = _commit_files(
        repo,
        {"mod_0.py": "VALUE_0 = 0\n", "mod_2.py": "VALUE_2 = 2\n",
         "mod_5.py": "VALUE_5 = 5\n"},
        "land three",
    )

    expected = _legacy_per_patch_reconcile(repo, head)
    event_log.invalidate_read_events_cache(repo)

    from opentraces.core.trails.models import payload_content_hash
    created = reconcile_commit_anchors(repo, head, writer="post-commit-correlator")
    new_hashes = {payload_content_hash(p) for p in created}

    assert new_hashes == expected, "R5: anchor payload set must match legacy per-patch"
    assert len(created) == 3


def test_late_patch_is_still_anchored_on_re_reconcile(tmp_path):
    """R5 scenario 3: a patch ingested AFTER a commit's first reconcile must
    still be anchored when that commit is reconciled again (no commit-level
    skip)."""
    repo = tmp_path / "r"
    _init_repo(repo)
    _emit_patch(repo, trace_id="tr-early", trace_patch_id="tp-early",
                file_path="early.py", authored="EARLY = 1\n")
    head = _commit_files(
        repo, {"early.py": "EARLY = 1\n", "late.py": "LATE = 2\n"}, "land both"
    )
    first = reconcile_commit_anchors(repo, head)
    assert {a["trace_patch_id"] for a in first} == {"tp-early"}

    # Late patch ingested after the first reconcile, matching the same commit.
    _emit_patch(repo, trace_id="tr-late", trace_patch_id="tp-late",
                file_path="late.py", authored="LATE = 2\n")
    event_log.invalidate_read_events_cache(repo)
    second = reconcile_commit_anchors(repo, head)
    assert {a["trace_patch_id"] for a in second} == {"tp-late"}, (
        "late patch must be searched + anchored on re-reconcile (per-patch dedup)"
    )


# --------------------------------------------------------------------------- #
# Dual-shape reader (R7): legacy per-patch and v2 summary normalize identically
# --------------------------------------------------------------------------- #

def test_iter_search_records_legacy_and_summary_yield_same_records():
    legacy = TrailEvent.model_validate({
        "event_id": "trailevent-sha256:" + "0" * 64,
        "event_sequence": 10,
        "event_time": "2026-01-01T00:00:00Z",
        "trace_id": "tr1",
        "step_index": 3,
        "generation_index": 0,
        "batch_id": "b",
        "writer": "w",
        "capture_method": ["post_commit_correlator"],
        "event_type": "git_anchor_search_completed",
        "payload": {
            "trace_patch_id": "abc",
            "search_head": {"algo": "sha1", "hex": "f" * 40},
            "algorithms_attempted": ["exact_range_hash", "structural_match"],
            "result": "unknown",
            "created_anchor_ids": [],
        },
        "content_hash": "sha256:" + "0" * 64,
        "ATTRIBUTION_VERSION": ATTRIBUTION_VERSION,
    })
    summary = TrailEvent.model_validate({
        "event_id": "trailevent-sha256:" + "1" * 64,
        "event_sequence": 11,
        "event_time": "2026-01-01T00:00:00Z",
        "trace_id": None,
        "step_index": None,
        "generation_index": 0,
        "batch_id": "b",
        "writer": "w",
        "capture_method": ["post_commit_correlator"],
        "event_type": "git_anchor_search_completed",
        "payload": {
            "summary": True,
            "schema_version": "opentraces.trail.anchor_search.v2",
            "search_head": {"algo": "sha1", "hex": "f" * 40},
            "algorithms_attempted": ["exact_range_hash", "structural_match"],
            "searched": 1,
            "anchored": 0,
            "unknown": 1,
            "results": [
                {"trace_patch_id": "abc", "trace_id": "tr1", "step_index": 3,
                 "generation_index": 0, "result": "unknown", "created_anchor_ids": []},
            ],
        },
        "content_hash": "sha256:" + "1" * 64,
        "ATTRIBUTION_VERSION": ATTRIBUTION_VERSION,
    })

    legacy_records = list(iter_search_records(legacy))
    summary_records = list(iter_search_records(summary))
    assert len(legacy_records) == len(summary_records) == 1

    def _key(r):
        return (
            r["trace_patch_id"], r["trace_id"], r["step_index"],
            r["search_head_sha"], tuple(r["algorithms_attempted"]),
            r["result"], tuple(r["created_anchor_ids"]), r["attribution_version"],
        )

    assert _key(legacy_records[0]) == _key(summary_records[0])


# --------------------------------------------------------------------------- #
# pkg-44 #44 — wall-clock budget handoff.
#
# A budgeted reconcile that trips its deadline appends a PARTIAL summary
# covering only the patches searched so far. A later UNBUDGETED reconcile
# searches the remainder. The union must cover every patch exactly once with no
# duplicate (patch, commit, attribution_version), and the anchors created across
# the two partial runs must equal the unbudgeted single-run baseline.
# --------------------------------------------------------------------------- #

def _all_search_records(repo: Path, commit: str) -> list[dict]:
    """Every per-patch search record for ``commit``, fanned through the dual
    reader (legacy per-patch OR v2 summary)."""
    records: list[dict] = []
    for event in read_events(repo, verify=False):
        if event.event_type != "git_anchor_search_completed":
            continue
        for rec in iter_search_records(event):
            if rec["search_head_sha"] == commit:
                records.append(rec)
    return records


def test_budget_handoff_partial_then_remainder_no_dup(tmp_path):
    repo = tmp_path / "r"
    _init_repo(repo)
    # 6 patches that all match the landing commit (deterministic order: the
    # reconcile visits patch_events in event-log order).
    n = 6
    new_content = {}
    for i in range(n):
        authored = f"LINE_{i} = {i}\n"
        _emit_patch(repo, trace_id=f"tr{i}", trace_patch_id=f"tp-{i}",
                    file_path=f"mod_{i}.py", authored=authored)
        new_content[f"mod_{i}.py"] = authored
    head = _commit_files(repo, new_content, "land six")
    event_log.invalidate_read_events_cache(repo)

    # --- Run 1: deadline already in the past -> partial summary, zero searched.
    import time
    summary1: dict = {}
    created1 = reconcile_commit_anchors(
        repo, head, summary_out=summary1,
        deadline=time.monotonic() - 1.0,
    )
    event_log.invalidate_read_events_cache(repo)
    # An already-expired deadline trips at the TOP of the very first iteration:
    # nothing searched, partial summary recorded with zero results... OR, if the
    # summary has no results, NO summary event is appended (drafts empty). Either
    # way the budget surface must report budget_exhausted with patches_remaining.
    assert created1 == []
    assert summary1["budget_exhausted"] is True
    assert summary1["searches_recorded"] == 0
    assert summary1["patches_remaining"] == n

    # --- Run 2: a tighter budget that lets ~half through.
    # Use a deadline a few ms out so a couple patches get searched then it trips.
    # We can't time-pin deterministically, so instead drive the remainder with
    # an UNBUDGETED run and assert the union is complete and duplicate-free.
    summary2: dict = {}
    created2 = reconcile_commit_anchors(repo, head, summary_out=summary2)
    event_log.invalidate_read_events_cache(repo)
    assert summary2.get("budget_exhausted") is False
    assert len(created2) == n, "the unbudgeted remainder run must anchor all six"

    # Union of search records across both runs: every patch exactly once.
    records = _all_search_records(repo, head)
    keys = [
        (r["trace_patch_id"], r["search_head_sha"], r["attribution_version"])
        for r in records
    ]
    assert len(keys) == len(set(keys)), "no duplicate (patch, commit, version)"
    searched_patches = {r["trace_patch_id"] for r in records}
    assert searched_patches == {f"tp-{i}" for i in range(n)}, (
        "union of partial + remainder must cover every patch exactly once"
    )

    # Anchors created across the two partial runs equal the single-run
    # baseline. The full payload content_hash embeds the per-repo commit/blob/
    # patch ids, so the cross-repo equivalence is over the (trace_patch_id,
    # path, range, evidence_tier) IDENTITY the anchor expresses — every patch
    # anchored exactly once, same tier, same range.
    def _identity(p: dict) -> tuple:
        return (
            p["trace_patch_id"], p["path"],
            p["range"]["start_line"], p["range"]["end_line"],
            p["evidence_tier"],
        )

    union_identity = sorted(_identity(p) for p in (created1 + created2))
    assert len(union_identity) == len(set(union_identity)), (
        "each patch must be anchored exactly once across the two partial runs"
    )

    # Baseline: fresh repo, identical seeding, ONE unbudgeted reconcile.
    base = tmp_path / "base"
    _init_repo(base)
    base_content = {}
    for i in range(n):
        authored = f"LINE_{i} = {i}\n"
        _emit_patch(base, trace_id=f"tr{i}", trace_patch_id=f"tp-{i}",
                    file_path=f"mod_{i}.py", authored=authored)
        base_content[f"mod_{i}.py"] = authored
    base_head = _commit_files(base, base_content, "land six")
    event_log.invalidate_read_events_cache(base)
    base_created = reconcile_commit_anchors(base, base_head)
    base_identity = sorted(_identity(p) for p in base_created)

    assert union_identity == base_identity, (
        "partial+remainder anchor identity set must equal the single-run baseline"
    )


def test_budget_mid_run_records_only_searched_subset(tmp_path, monkeypatch):
    """A deadline that trips AFTER the k-th patch records a PARTIAL summary whose
    ``searches_recorded`` equals exactly the patches searched before the trip,
    and the second (unbudgeted) run records only the remainder."""
    import opentraces.core.trails.anchors as anchors_mod

    repo = tmp_path / "r"
    _init_repo(repo)
    n = 5
    new_content = {}
    for i in range(n):
        authored = f"VAL_{i} = {i}\n"
        _emit_patch(repo, trace_id=f"tr{i}", trace_patch_id=f"tp-{i}",
                    file_path=f"mod_{i}.py", authored=authored)
        new_content[f"mod_{i}.py"] = authored
    head = _commit_files(repo, new_content, "land five")
    event_log.invalidate_read_events_cache(repo)

    # Fake monotonic clock: stays below the deadline for the first 3 loop-top
    # checks, then jumps past it. Each loop-top calls time.monotonic() once.
    deadline = 100.0
    ticks = iter([10.0, 10.0, 10.0, 999.0, 999.0, 999.0])

    def _fake_monotonic():
        try:
            return next(ticks)
        except StopIteration:
            return 999.0

    monkeypatch.setattr(anchors_mod.time, "monotonic", _fake_monotonic)

    summary1: dict = {}
    created1 = reconcile_commit_anchors(
        repo, head, summary_out=summary1, deadline=deadline,
    )
    event_log.invalidate_read_events_cache(repo)

    assert summary1["budget_exhausted"] is True
    # 3 loop-top checks under deadline -> 3 patches searched; trips on the 4th.
    assert summary1["searches_recorded"] == 3
    assert summary1["patches_searched"] == 3
    assert summary1["patches_remaining"] == n - 3
    assert len(created1) == 3

    # Unbudgeted remainder run searches the other 2 only.
    monkeypatch.undo()
    summary2: dict = {}
    created2 = reconcile_commit_anchors(repo, head, summary_out=summary2)
    event_log.invalidate_read_events_cache(repo)
    assert summary2["budget_exhausted"] is False
    assert summary2["searches_recorded"] == 2
    assert len(created2) == 2

    # Complete, duplicate-free union.
    records = _all_search_records(repo, head)
    keys = [
        (r["trace_patch_id"], r["search_head_sha"], r["attribution_version"])
        for r in records
    ]
    assert len(keys) == len(set(keys)) == n
