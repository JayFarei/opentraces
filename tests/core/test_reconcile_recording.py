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
    assert len(payload["results"]) == 5


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
