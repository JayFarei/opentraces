"""Unit tests for the canonical trace-corpus resolver (issue #89).

Locks the precedence invariant the three consumers now share: winner per
trace_id is the freshest source by mtime, ties broken toward the bucket tier,
and the choice is independent of candidate insertion order. The bucket-vs-project
case must reproduce the legacy index rule exactly (project wins iff strictly
newer than the bucket object).
"""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path

from opentraces.core import bucket_store as bs
from opentraces.core import paths
from opentraces.core import trace_corpus as tc


def _src(trace_id: str, layer: str, mtime: int, *, project="p", path="x") -> tc.CorpusSource:
    return tc.CorpusSource(
        project_slug=project,
        trace_id=trace_id,
        layer=layer,
        source_layer="canonical" if layer != tc.LAYER_STAGING else "staging",
        path=Path(f"/{path}/{trace_id}.{layer}"),
        mtime_ns=mtime,
        cheap_digest=f"{layer}:{mtime}",
    )


def test_bucket_tier_outranks_project_and_staging() -> None:
    assert tc._priority(tc.LAYER_BUCKET) > tc._priority(tc.LAYER_PROJECT)
    assert tc._priority(tc.LAYER_PROJECT) == tc._priority(tc.LAYER_STAGING)


def test_beats_reproduces_legacy_index_rule() -> None:
    # Legacy rule: project source skipped when project_mtime <= bucket_mtime,
    # i.e. the project copy wins iff it is STRICTLY newer than the bucket object.
    bucket = _src("t", tc.LAYER_BUCKET, mtime=1000)

    older = _src("t", tc.LAYER_PROJECT, mtime=999)
    equal = _src("t", tc.LAYER_PROJECT, mtime=1000)
    newer = _src("t", tc.LAYER_PROJECT, mtime=1001)

    assert tc._beats(older, bucket) is False  # bucket wins
    assert tc._beats(equal, bucket) is False  # tie -> bucket wins (higher tier)
    assert tc._beats(newer, bucket) is True   # strictly newer project wins
    # And symmetric: bucket only beats project when project is not newer.
    assert tc._beats(bucket, older) is True
    assert tc._beats(bucket, equal) is True
    assert tc._beats(bucket, newer) is False


def test_iter_sources_winner_is_insertion_order_independent(monkeypatch) -> None:
    # Three candidates for the same trace_id across tiers + mtimes; the winner
    # must not depend on the order candidates are enumerated.
    candidates = [
        _src("t", tc.LAYER_BUCKET, mtime=1000),
        _src("t", tc.LAYER_PROJECT, mtime=1001),   # strictly newer -> should win
        _src("t", tc.LAYER_STAGING, mtime=1000),
    ]
    winners = set()
    for perm in itertools.permutations(candidates):
        monkeypatch.setattr(tc, "_bucket_candidates", lambda: [])
        monkeypatch.setattr(tc, "_jsonl_candidates", lambda perm=perm: list(perm))
        resolved = list(tc.iter_sources())
        assert len(resolved) == 1
        winners.add((resolved[0].layer, resolved[0].mtime_ns))
    assert winners == {(tc.LAYER_PROJECT, 1001)}


def test_iter_sources_dedupes_by_trace_id_and_keeps_distinct(monkeypatch) -> None:
    bucket_cands = [
        _src("a", tc.LAYER_BUCKET, mtime=10),
        _src("b", tc.LAYER_BUCKET, mtime=10),
    ]
    jsonl_cands = [
        _src("a", tc.LAYER_PROJECT, mtime=5),    # older -> bucket "a" wins
        _src("c", tc.LAYER_PROJECT, mtime=5),    # unique -> kept
    ]
    monkeypatch.setattr(tc, "_bucket_candidates", lambda: bucket_cands)
    monkeypatch.setattr(tc, "_jsonl_candidates", lambda: jsonl_cands)
    resolved = {s.trace_id: s for s in tc.iter_sources()}
    assert set(resolved) == {"a", "b", "c"}
    assert resolved["a"].layer == tc.LAYER_BUCKET  # older project copy lost
    # deterministic order: sorted by trace_id
    assert [s.trace_id for s in tc.iter_sources()] == ["a", "b", "c"]


def test_iter_sources_winner_is_deterministic_on_mtime_tie(monkeypatch) -> None:
    # Two same-tier, same-mtime candidates: the winner must be deterministic
    # (the path tiebreak in _beats), not whichever was enumerated first.
    a = _src("t", tc.LAYER_PROJECT, mtime=1000, path="aaa")
    b = _src("t", tc.LAYER_PROJECT, mtime=1000, path="bbb")
    winners = set()
    for perm in ([a, b], [b, a]):
        monkeypatch.setattr(tc, "_bucket_candidates", lambda: [])
        monkeypatch.setattr(tc, "_jsonl_candidates", lambda perm=perm: list(perm))
        resolved = list(tc.iter_sources())
        assert len(resolved) == 1
        winners.add(str(resolved[0].path))
    assert len(winners) == 1, "tie winner must not depend on enumeration order"


def test_resolve_matches_iter_sources_object_beats_newer_mirror(tmp_path, monkeypatch) -> None:
    # Regression for the resolver-lens defect: the v2 object must beat a plan-079
    # legacy mirror for the same project unconditionally in BOTH the bulk path
    # (iter_sources) and the single-trace path (resolve), even when the mirror's
    # file mtime is newer. Otherwise the search index and map/get/slice disagree.
    # Fully sandbox every layer the resolver reads so the test is hermetic
    # (no real ~/.opentraces bucket/projects scan, no chance of leaking writes).
    monkeypatch.setattr(paths, "bucket_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(paths, "STAGING_DIR", tmp_path / "staging", raising=False)
    proj, tid = "proj", "abc-123"

    object_rel = f"objects/traces/v1/{proj}/{tid}/object.json"
    object_path = tmp_path / object_rel
    object_path.parent.mkdir(parents=True)
    envelope = {
        "schema_version": bs.TRACE_RECORD_BUCKET_SCHEMA,
        "project_slug": proj,
        "source_layer": "canonical",
        "trace_id": tid,
        "record_hash": "h_object",
    }
    object_path.write_text(json.dumps(envelope), encoding="utf-8")
    pointer = object_path.parent / "current.json"
    pointer.write_text(
        json.dumps(
            {
                "schema_version": bs.TRACE_RECORD_POINTER_SCHEMA,
                "project_slug": proj,
                "source_layer": "canonical",
                "trace_id": tid,
                "record_hash": "h_object",
                "object_path": object_rel,
            }
        ),
        encoding="utf-8",
    )
    mirror = tmp_path / "trace-records" / proj / f"{tid}.json"
    mirror.parent.mkdir(parents=True)
    mirror.write_text(json.dumps({**envelope, "record_hash": "h_mirror"}), encoding="utf-8")

    # Make the mirror file strictly NEWER than the object.
    os.utime(object_path, ns=(1_000_000_000, 1_000_000_000))
    os.utime(mirror, ns=(2_000_000_000, 2_000_000_000))

    bulk = {s.trace_id: s for s in tc.iter_sources()}
    single = tc.resolve(tid)
    assert single is not None
    assert single.cheap_digest == "h_object", "single-trace resolve picked the stale mirror"
    assert bulk[tid].cheap_digest == "h_object"
    assert single.cheap_digest == bulk[tid].cheap_digest  # consumers agree


def test_incremental_refresh_loader_delegates_to_resolver(monkeypatch) -> None:
    # Regression for the codex-found defect: the incremental search-snapshot
    # refresh (_load_doc_for_trace_id) must pick the SAME source as the resolver,
    # not its own ad-hoc bucket-first/no-mirror union. Proven by delegation: it
    # routes the resolver's winning source's fields into _doc_from_record.
    from types import SimpleNamespace

    from opentraces.core import trace_search_snapshot as tss

    winner = _src("t", tc.LAYER_PROJECT, mtime=999)
    loaded = SimpleNamespace(
        record=SimpleNamespace(trace_id="t"),
        project_slug="proj-from-resolver",
        source_layer="canonical",
        path=Path("/resolved/t.jsonl"),
    )
    monkeypatch.setattr(tc, "resolve", lambda tid: winner if tid == "t" else None)
    monkeypatch.setattr(tc, "load_record", lambda s: loaded if s is winner else None)
    monkeypatch.setattr(
        tss, "_doc_from_record", lambda record, **kw: {"record": record, **kw}
    )

    doc = tss._load_doc_for_trace_id("t")
    assert doc == {
        "record": loaded.record,
        "project_slug": "proj-from-resolver",
        "source_layer": "canonical",
        "trace_path": Path("/resolved/t.jsonl"),
    }
    assert tss._load_doc_for_trace_id("absent") is None


def test_resolve_picks_freshest_single_trace(monkeypatch) -> None:
    monkeypatch.setattr(
        tc, "_bucket_candidates_for", lambda tid: [_src(tid, tc.LAYER_BUCKET, mtime=100)]
    )
    monkeypatch.setattr(
        tc, "_jsonl_candidates_for", lambda tid: [_src(tid, tc.LAYER_PROJECT, mtime=200)]
    )
    winner = tc.resolve("t")
    assert winner is not None
    assert winner.layer == tc.LAYER_PROJECT and winner.mtime_ns == 200

    monkeypatch.setattr(tc, "_bucket_candidates_for", lambda tid: [])
    monkeypatch.setattr(tc, "_jsonl_candidates_for", lambda tid: [])
    assert tc.resolve("missing") is None
    assert tc.resolve("") is None
