"""Fast-path (issue #362) tests for ``bucket_reclaim_search``.

Issue #362: the #358 anchor-search v3 WRITE path is correct/fast, but the
reclaim RECOVERY pass (``bucket reclaim --anchor-search``) is O(corpus)-slow on
large ALREADY-CLEAN buckets because the summed-size prefilter cannot tell a
large-but-clean project from a dirty one, so it re-validates the whole canonical
log four times to discover zero deltas.

Two levers are exercised here, RED-first:

* LEVER 1 -- an O(1) clean-project watermark probe + a raw-bytes suffix
  signature scan that skips a proven-clean project WITHOUT parsing a single
  event, plus LEVER 2 -- action=error surfacing hardening (a corrupt / non-
  ancestor watermark or any git failure falls back to a full walk; a genuine
  refusal still RAISES into an ``action='error'`` row).
* LEVER 4 -- a hybrid raw-parse reader (``event_log.iter_event_views``) scoped
  to the anchor-search type, gated HARD on the byte-identity test below: the
  raw-view compaction output MUST equal the fully-validated path's output
  bit-for-bit, or the lever does not land.

Every test builds its own temp bucket via the autouse HOME/bucket isolation
fixture (``tests/conftest.py``); NONE touch ``~/.opentraces``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_bucket_reclaim_search as fx  # noqa: E402

from opentraces.core.trails import TrailEventDraft, append_event_batch  # noqa: E402
from opentraces.core.trails.models import TrailEvent  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EVENT_LOG_REF = "refs/opentraces/local/events/v1"


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", _EVENT_LOG_REF],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _register(repo: Path) -> str:
    from opentraces.core.config import get_project_dir, load_config, register_project, save_config

    cfg = load_config()
    register_project(cfg, repo)
    save_config(cfg)
    return get_project_dir(repo).name


def _clean_patch(*, trace_id: str, i: int, file_path: str | None = None) -> TrailEventDraft:
    return TrailEventDraft(
        event_type="trace_patch_created",
        trace_id=trace_id,
        generation_index=0,
        step_index=i,
        capture_method=["hook_posttooluse"],
        payload={
            "trace_patch_id": f"tracepatch-sha256:{i:04d}" + "e" * 60,
            "file_path": file_path or f"f{i}.py",
            "affected_range": {"start_line": 1, "end_line": 1},
            "authored_text": "fixture body line one\nfixture body line two\n",
            "raw_authored_hash": "sha256:fixture",
            "git_clean_hash": "sha256:fixture",
            "limitations": [],
        },
    )


def _build_clean_large_world(tmp_path: Path, *, n_events: int = 60) -> dict:
    """A registered project whose canonical log is large enough to clear
    ``_FAT_PREFILTER_MIN_BYTES`` but carries NO anchor-search events at all --
    the born-clean-but-large shape the #362 probe must O(1)-skip. No mirror /
    companion projection, so ``affected`` is empty and reclaim reaches the
    fully-clean early return (which writes the watermark)."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod

    repo = tmp_path / "proj"
    fx._init_repo(repo)
    for i in range(n_events):
        append_event_batch(repo, [_clean_patch(trace_id=f"t-{i}", i=i)], writer="capture-claude-code")
    slug = _register(repo)
    tree_bytes = reclaim_mod._events_tree_bytes(repo, _head(repo))
    assert tree_bytes >= reclaim_mod._FAT_PREFILTER_MIN_BYTES, (
        f"clean world not large enough to bypass the size prefilter ({tree_bytes} bytes)"
    )
    return {"repo": repo, "slug": slug}


class _ParseCounter:
    """Count every ``TrailEvent.model_validate_json`` call across the process
    -- the probe's O(1) / clean-suffix skip must parse ZERO event blobs."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.n = 0
        original = TrailEvent.model_validate_json.__func__

        def _counting(cls, *args, **kwargs):
            self.n += 1
            return original(cls, *args, **kwargs)

        monkeypatch.setattr(TrailEvent, "model_validate_json", classmethod(_counting))


# ---------------------------------------------------------------------------
# LEVER 1 -- O(1) clean-project probe
# ---------------------------------------------------------------------------


def test_second_apply_over_compacted_project_is_o1_zero_parse(tmp_path: Path, monkeypatch) -> None:
    """After a first apply compacts the fat world and writes the watermark,
    the SECOND apply's head equals the watermark head -> O(1) skip, zero event
    parses, nothing touched."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod

    world = fx._build_world(tmp_path)
    reclaim_mod.reclaim_anchor_search(apply=True)

    counter = _ParseCounter(monkeypatch)
    result = reclaim_mod.reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == world["slug"])

    assert proj.ref_rewritten is False
    assert proj.mirror_rewritten is False
    assert proj.companions_regenerated == []
    assert counter.n == 0, f"clean O(1) skip must parse zero events, parsed {counter.n}"


def test_born_clean_large_project_probe_skips_second_run(tmp_path: Path, monkeypatch) -> None:
    """A large born-clean project: first run full-walks, proves clean, writes
    the watermark; second run O(1)-skips with zero event parses."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod

    world = _build_clean_large_world(tmp_path)
    repo = world["repo"]

    first = reclaim_mod.reclaim_anchor_search(apply=True)
    proj1 = next(p for p in first.projects if p.project_slug == world["slug"])
    assert proj1.action == "clean"
    # Watermark now records the proven-clean head.
    assert reclaim_mod._read_clean_watermark(repo) == _head(repo)

    counter = _ParseCounter(monkeypatch)
    second = reclaim_mod.reclaim_anchor_search(apply=True)
    proj2 = next(p for p in second.projects if p.project_slug == world["slug"])
    assert proj2.action == "clean"
    assert counter.n == 0, f"born-clean O(1) skip must parse zero events, parsed {counter.n}"


def test_dirty_suffix_after_clean_watermark_falls_through_and_compacts(tmp_path: Path) -> None:
    """A watermarked-clean project that then gets a NEW fat v2 batch appended:
    ``_suffix_is_dirty`` is True over ``watermark..head``, the run falls through
    to the full walk, compacts, and advances the watermark to the new head."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod

    world = _build_clean_large_world(tmp_path)
    repo, slug = world["repo"], world["slug"]
    reclaim_mod.reclaim_anchor_search(apply=True)
    watermark = reclaim_mod._read_clean_watermark(repo)
    assert watermark == _head(repo)

    # Append a NEW fat v2 summary (token-detectable regardless of size).
    fat_entries = [
        {
            "trace_patch_id": f"tracepatch-sha256:{i:04d}" + "d" * 60, "trace_id": "t-fat",
            "step_index": i, "generation_index": 0, "result": "unknown", "created_anchor_ids": [],
        }
        for i in range(6)
    ]
    append_event_batch(repo, [fx._v2_fat_search_event(commit_hex="d" * 40, entries=fat_entries)], writer="watcher")
    dirty_head = _head(repo)

    assert reclaim_mod._suffix_is_dirty(repo, watermark, dirty_head) is True

    result = reclaim_mod.reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == slug)
    assert proj.action == "compacted"
    assert proj.fat_summaries_rewritten == 1
    # Watermark advanced to the freshly-compacted head.
    assert reclaim_mod._read_clean_watermark(repo) == _head(repo)


def test_clean_suffix_after_watermark_skips_and_advances(tmp_path: Path, monkeypatch) -> None:
    """Appending a CLEAN v3-compact summary past the watermark: the suffix scan
    finds no fat/legacy, so the run skips (zero parses) and advances the
    watermark to the new head."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod
    from opentraces.core.trails.contract import ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION
    from opentraces.core.trails.search_records import build_anchor_search_summary_payload

    world = _build_clean_large_world(tmp_path)
    repo, slug = world["repo"], world["slug"]
    reclaim_mod.reclaim_anchor_search(apply=True)
    watermark = reclaim_mod._read_clean_watermark(repo)

    # A v3-compact summary carries ``unanchored_trace_patch_ids`` -> NOT dirty.
    v3_payload = build_anchor_search_summary_payload(
        schema_version=ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION,
        search_head={"algo": "sha1", "hex": "c" * 40},
        algorithms_attempted=["exact_range_hash"],
        results=[],
        unanchored_trace_patch_ids=[f"tracepatch-sha256:{i:04d}" + "f" * 60 for i in range(4)],
    )
    append_event_batch(
        repo,
        [TrailEventDraft(event_type="git_anchor_search_completed", trace_id=None, step_index=None,
                         capture_method=["watcher_reconcile"], payload=v3_payload)],
        writer="watcher",
    )
    new_head = _head(repo)
    assert reclaim_mod._suffix_is_dirty(repo, watermark, new_head) is False

    counter = _ParseCounter(monkeypatch)
    result = reclaim_mod.reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == slug)
    assert proj.action == "clean"
    assert counter.n == 0, f"clean-suffix skip must parse zero events, parsed {counter.n}"
    assert reclaim_mod._read_clean_watermark(repo) == new_head


def test_journal_present_is_never_o1_skipped(tmp_path: Path) -> None:
    """A pending reclaim journal for a project whose head == watermark head
    must STILL process (never take the O(1) probe skip) -- guards the
    resume-short-circuit-under-prefilter regression."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod

    world = _build_clean_large_world(tmp_path)
    repo, slug = world["repo"], world["slug"]
    reclaim_mod.reclaim_anchor_search(apply=True)
    head = _head(repo)
    assert reclaim_mod._read_clean_watermark(repo) == head

    # Plant a journal whose head equals the (clean) watermark head. The probe
    # must NOT skip; the journal-driven path runs a full walk instead.
    reclaim_mod._write_journal(slug, old_event_ids={"x"}, affected_trace_ids=set())

    called = {"stream": False}
    original = reclaim_mod._stream_compact_chain

    def _spy(*args, **kwargs):
        called["stream"] = True
        return original(*args, **kwargs)

    mp = pytest.MonkeyPatch()
    mp.setattr(reclaim_mod, "_stream_compact_chain", _spy)
    try:
        reclaim_mod._process_reachable_project(slug, repo, apply=True)
    finally:
        mp.undo()
    assert called["stream"] is True, "journal-present project must full-walk, not O(1)-skip"


def test_corrupt_watermark_falls_through_to_full_walk(tmp_path: Path) -> None:
    """A corrupt watermark file is read as absent -> the probe cannot skip, so
    the run full-walks (never concludes 'clean' on an unreadable watermark)."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod

    world = fx._build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    path = reclaim_mod._clean_watermark_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid json", encoding="utf-8")
    assert reclaim_mod._read_clean_watermark(repo) is None

    result = reclaim_mod.reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == slug)
    assert proj.action == "compacted"  # full walk ran; corrupt watermark ignored


def test_non_ancestor_watermark_raises_probe_error_and_full_walks(tmp_path: Path) -> None:
    """A watermark head that is NOT an ancestor of the current head (e.g. a
    history rewrite) makes ``_suffix_is_dirty`` raise ``_WatermarkProbeError``;
    the probe treats it as unknown and full-walks."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod

    world = fx._build_world(tmp_path)
    repo = world["repo"]
    head = _head(repo)

    # A syntactically-valid but non-ancestor sha (unknown object).
    bogus = "0" * 40
    with pytest.raises(reclaim_mod._WatermarkProbeError):
        reclaim_mod._suffix_is_dirty(repo, bogus, head)

    # And end-to-end: a watermark pointing at a non-ancestor sha -> full walk.
    reclaim_mod._write_clean_watermark(repo, bogus)
    result = reclaim_mod.reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == world["slug"])
    assert proj.action == "compacted"


def test_missing_ref_with_journal_still_errors(tmp_path: Path) -> None:
    """The missing-ref refusal is preserved: a pending journal whose canonical
    ref has vanished RAISES into an ``action='error'`` row -- the probe /
    watermark path never reinterprets an absent ref as 'clean'."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod

    world = fx._build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    reclaim_mod._write_journal(slug, old_event_ids={"x"}, affected_trace_ids=set())
    # Also plant a (stale) watermark to prove it cannot mask the refusal.
    reclaim_mod._write_clean_watermark(repo, _head(repo))
    subprocess.run(["git", "update-ref", "-d", _EVENT_LOG_REF], cwd=repo, check=True)

    result = reclaim_mod.reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == slug)
    assert proj.action == "error"


def test_probe_over_inclusion_is_safe(tmp_path: Path) -> None:
    """A NON-anchor event whose payload embeds the exact token string
    ``git_anchor_search_completed`` makes the raw-bytes signature flag the
    suffix DIRTY -> a full walk runs and, finding nothing to compact, returns a
    correct no-op. Over-inclusion only ever costs a safe extra walk."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod

    world = _build_clean_large_world(tmp_path)
    repo, slug = world["repo"], world["slug"]
    reclaim_mod.reclaim_anchor_search(apply=True)
    watermark = reclaim_mod._read_clean_watermark(repo)

    # A trace_patch_created whose file_path serializes to the exact quoted
    # token -- trips the substring signature, but is not an anchor-search event.
    append_event_batch(
        repo,
        [_clean_patch(trace_id="t-decoy", i=999, file_path="git_anchor_search_completed")],
        writer="capture-claude-code",
    )
    new_head = _head(repo)
    assert reclaim_mod._suffix_is_dirty(repo, watermark, new_head) is True

    result = reclaim_mod.reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == slug)
    # Full walk ran but there was nothing fat/legacy to compact.
    assert proj.action == "clean"
    assert proj.fat_summaries_rewritten == 0


# ---------------------------------------------------------------------------
# LEVER 4 -- hybrid raw-parse reader (gated on byte-identity)
# ---------------------------------------------------------------------------


def _serialize(event) -> str:
    return json.dumps(event.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"


def test_iter_event_views_byte_identical_compaction(tmp_path: Path) -> None:
    """THE load-bearing gate: compacting the SAME dirty chain through the raw
    hybrid reader (``iter_event_views``) yields output byte-for-byte identical
    to compacting it through the fully-validated reader (``iter_events``)."""
    from opentraces.core.trails.event_log import iter_event_views, iter_events
    from opentraces.core.trails.search_compaction import CompactionStats, stream_compact_events

    world = fx._build_world(tmp_path)
    repo = world["repo"]
    head = _head(repo)

    events_full = list(iter_events(repo, head))
    views = list(iter_event_views(repo, head))
    assert len(events_full) == len(views)

    stats_full = CompactionStats()
    out_full = list(stream_compact_events(iter(events_full), stats_full))
    stats_view = CompactionStats()
    out_view = list(stream_compact_events(iter(views), stats_view))

    assert [_serialize(e) for e in out_full] == [_serialize(e) for e in out_view]
    assert [e.event_id for e in out_full] == [e.event_id for e in out_view]
    assert stats_full.as_dict() == stats_view.as_dict()


def test_raw_event_view_field_parity(tmp_path: Path) -> None:
    """``_RawEventView`` exposes the same duck-typed attributes a fully-parsed
    ``TrailEvent`` does over a fat anchor-search blob."""
    from opentraces.core.trails.event_log import _RawEventView, iter_event_views, iter_events

    world = fx._build_world(tmp_path)
    repo = world["repo"]
    head = _head(repo)

    events = {e.event_sequence: e for e in iter_events(repo, head)}
    views = {v.event_sequence: v for v in iter_event_views(repo, head)}
    assert set(events) == set(views)

    saw_raw = False
    for seq, view in views.items():
        ev = events[seq]
        if isinstance(view, _RawEventView):
            saw_raw = True
        for attr in (
            "event_id", "event_type", "event_sequence", "event_time", "trace_id",
            "batch_id", "generation_index", "step_index", "capture_method",
            "SCHEMA_VERSION", "SECURITY_VERSION", "ATTRIBUTION_VERSION", "previous_event_id",
        ):
            assert getattr(view, attr) == getattr(ev, attr), f"mismatch on {attr} for seq {seq}"
        assert view.payload == ev.payload
    assert saw_raw, "fixture must contain at least one raw-parsed anchor-search view"


def test_iter_event_views_float_payload_routes_through_pydantic(tmp_path: Path) -> None:
    """A NON-anchor event with a float payload value must route through full
    ``TrailEvent.model_validate_json`` (never the raw view), so byte-identity
    holds even for float-bearing payloads outside the anchor-search scope."""
    from opentraces.core.trails.event_log import _RawEventView, iter_event_views

    repo = tmp_path / "proj"
    fx._init_repo(repo)
    append_event_batch(
        repo,
        [TrailEventDraft(event_type="trace_metric_observed", trace_id="t-f", generation_index=0,
                         step_index=0, capture_method=["hook_posttooluse"],
                         payload={"metric": "latency", "value": 1.5})],
        writer="capture-claude-code",
    )
    head = _head(repo)
    views = list(iter_event_views(repo, head))
    assert len(views) == 1
    assert not isinstance(views[0], _RawEventView)
    assert isinstance(views[0], TrailEvent)
    assert views[0].payload["value"] == 1.5
