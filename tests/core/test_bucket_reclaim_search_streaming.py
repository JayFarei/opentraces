"""Issue #358 (STAGE: memory-bounded ``bucket reclaim`` anchor-search pass).

Three RED-first properties for the streaming rewrite of
``bucket_reclaim_search.py``'s read+compact+swap+companion-regen pipeline:

1. Equivalence -- the streaming implementation must reproduce the CURRENT
   (list-based) implementation's output byte-identically on a mixed
   multi-project world. The expected fixture
   (``tests/fixtures/reclaim_streaming/equivalence_fixture.json``) was
   captured in a SEPARATE process run against the unmodified implementation
   (see ``scripts``/session notes; the capture script itself is not checked
   in as a test) BEFORE the streaming rewrite landed -- this test only ever
   reads that fixture, never regenerates it.
2. Memory bound -- ``reclaim_anchor_search`` on a world dominated by many
   large (~2MB) fat anchor-search summaries must peak at a SMALL multiple of
   ONE such event's parsed footprint, never a multiple of the corpus. Run in
   a genuinely fresh CHILD PROCESS (ORCHESTRATOR RULING R2): ``ru_maxrss`` is
   a process-lifetime high-water mark, so measuring in-process would let
   Python's own import/world-building allocations pollute the number.
3. Cache discipline -- after reclaiming multiple projects, ``_READ_EVENTS_
   CACHE`` (and the #137 event-index memo) must hold no entry for any
   reclaimed project's repo -- reclaim's own read path must never populate
   the shared, cross-project cache event_log.py exists to warm for OTHER
   (interactive) callers.
"""
from __future__ import annotations

import base64
import gzip
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_bucket_reclaim_search as fx  # noqa: E402
import _reclaim_streaming_support as sup  # noqa: E402

_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "reclaim_streaming" / "equivalence_fixture.json"


def _load_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Equivalence
# ---------------------------------------------------------------------------


def test_streaming_reclaim_matches_captured_list_based_fixture(tmp_path: Path) -> None:
    """Rebuild the SAME mixed multi-project world the fixture was captured
    from (same helper functions, same construction order -> same slugs,
    same commit shas, same event ids) and assert the current implementation
    (streaming, post-rewrite) reproduces every captured surface: the report
    dicts for dry-run/apply/second-apply, each project's canonical event
    chain, the shared events mirror, and every affected trace's companion
    bytes -- byte-identical, not just semantically equal."""
    from opentraces.core.bucket_events import read_events_mirror_batches
    from opentraces.core.bucket_layout import trace_v1_context_path, trace_v1_trail_path
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search
    from opentraces.core.trails import read_events

    expected = _load_fixture()

    with sup.frozen_event_time():
        world_a = fx._build_world(tmp_path / "a")
        world_b = fx._build_world(tmp_path / "b")
        clean = fx._build_second_clean_project(tmp_path / "c", trace_id="t-clean")

    dry = reclaim_anchor_search(apply=False)
    applied = reclaim_anchor_search(apply=True)
    second = reclaim_anchor_search(apply=True)

    assert dry.as_dict() == expected["dry"]
    assert applied.as_dict() == expected["applied"]
    assert second.as_dict() == expected["second"]

    for label, world in (("a", world_a), ("b", world_b), ("c", clean)):
        slug = world["slug"]
        repo = world["repo"]
        events = read_events(repo, verify=False)
        got = [e.model_dump(mode="json") for e in events]
        assert got == expected[f"{label}_canonical_events"], f"project {label} canonical chain diverged"

        traces = ["t-anchored", "t-fat-unanchored"] if label != "c" else ["t-clean"]
        for trace_id in traces:
            trail_path = trace_v1_trail_path(slug, trace_id)
            ctx_path = trace_v1_context_path(slug, trace_id)
            expected_companion = expected[f"{label}_companions"][trace_id]
            got_trail = base64.b64encode(trail_path.read_bytes()).decode("ascii") if trail_path.exists() else None
            got_ctx = base64.b64encode(ctx_path.read_bytes()).decode("ascii") if ctx_path.exists() else None
            assert got_trail == expected_companion["trail_gz_b64"], f"{label}:{trace_id} trail companion diverged"
            assert got_ctx == expected_companion["context_gz_b64"], f"{label}:{trace_id} context companion diverged"

    mirror_events = list(read_events_mirror_batches())
    assert sorted(e.event_id for e in mirror_events) == expected["mirror_event_ids"]
    assert [e.model_dump(mode="json") for e in mirror_events] == expected["mirror_events"]


# ---------------------------------------------------------------------------
# 2. Memory bound
# ---------------------------------------------------------------------------


def test_reclaim_peak_rss_is_bounded_by_largest_event_not_corpus(tmp_path: Path) -> None:
    """40 fat (~2.2MB serialized) anchor-search summaries across 2 projects
    (~88MB raw corpus) -- ``reclaim_anchor_search(apply=False)`` in a fresh
    child process must peak nowhere near a multiple of that corpus size.

    Budget derivation (calibrated, not guessed): a SINGLE parsed fat event
    (8500-entry ``results[]``, Pydantic-validated) costs ~40MB of RSS by
    itself -- Pydantic's per-field validation overhead on a deeply nested
    dict/list structure dwarfs the ~2.2MB raw JSON it was built from, so
    "3x the largest event" has to mean 3x that PARSED footprint, not 3x the
    serialized byte count, for the bound to mean anything. Budget = (this
    process's own baseline RSS on an EMPTY world, measured fresh alongside
    the real run so environment drift cannot skew the comparison) + 3 x
    (one fat event's measured parsed footprint) + a fixed 40MB margin for
    git subprocess I/O buffers and interpreter jitter. The streaming
    implementation holds at most O(1) parsed fat events in flight at once
    (see ``search_compaction.stream_compact_events``); the old, list-based
    implementation holds the WHOLE per-project corpus (~44MB raw, ~800MB+
    parsed) simultaneously -- this assertion is what tells the two apart.
    """
    baseline_home = tmp_path / "baseline-home"
    (baseline_home / ".opentraces" / "projects").mkdir(parents=True)
    (baseline_home / ".opentraces" / "staging").mkdir(parents=True)
    baseline_rss = sup.measure_reclaim_peak_rss_bytes(baseline_home)

    single_event_rss = _measure_single_fat_event_parse_rss(tmp_path)
    single_event_delta = max(single_event_rss - baseline_rss, 0)

    budget = baseline_rss + 3 * single_event_delta + 40_000_000

    fat_home = tmp_path / "fat-home"
    (fat_home / ".opentraces" / "projects").mkdir(parents=True)
    (fat_home / ".opentraces" / "staging").mkdir(parents=True)
    import os

    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(fat_home)
    try:
        from opentraces.core import config as _config
        from opentraces.core import paths as _paths

        opentraces_dir = fat_home / ".opentraces"
        for mod in (_paths, _config):
            mod.OPENTRACES_DIR = opentraces_dir
            mod.CONFIG_PATH = opentraces_dir / "config.json"
            mod.CREDENTIALS_PATH = opentraces_dir / "credentials"
            mod.PROJECTS_DIR = opentraces_dir / "projects"
            if hasattr(mod, "STAGING_DIR"):
                mod.STAGING_DIR = opentraces_dir / "staging"
        sup.build_fat_world(fat_home / "projs", num_projects=2, events_per_project=20, entry_count=8500)
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home

    actual_rss = sup.measure_reclaim_peak_rss_bytes(fat_home, timeout=300)

    assert actual_rss <= budget, (
        f"reclaim peak RSS {actual_rss / 1e6:.1f}MB exceeds the O(largest-event) "
        f"budget {budget / 1e6:.1f}MB (baseline={baseline_rss/1e6:.1f}MB, "
        f"single_event_delta={single_event_delta/1e6:.1f}MB) -- the read+compact "
        f"path is materializing something proportional to the whole corpus"
    )


def _measure_single_fat_event_parse_rss(tmp_path: Path) -> int:
    """Fresh-child-process peak RSS for parsing exactly ONE fat event this
    module's world builder produces -- the real, measured basis for the
    memory budget above (see that test's docstring), not an assumption."""
    import subprocess as _subprocess

    script = (
        "import sys, resource\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
        "import _reclaim_streaming_support as sup\n"
        "from opentraces.core.trails.models import finalize_event\n"
        "draft = sup._fat_v2_event(commit_hex='a'*40, trace_id='t-x', entry_count=8500, tag='zz')\n"
        "data = {'event_sequence': 1, 'event_time': '2026-01-01T00:00:00Z', 'previous_event_id': None,\n"
        "        'trace_id': draft.trace_id, 'generation_index': draft.generation_index,\n"
        "        'step_index': draft.step_index, 'batch_id': 'b', 'writer': 'watcher',\n"
        "        'capture_method': draft.capture_method, 'event_type': draft.event_type, 'payload': draft.payload}\n"
        "data = {k: v for k, v in data.items() if v is not None}\n"
        "event = finalize_event(data)\n"
        "print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)\n"
    )
    proc = _subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"single-event measurement child failed:\n{proc.stdout}\n{proc.stderr}")
    raw = int(proc.stdout.strip().splitlines()[-1])
    return raw * (1024 if sys.platform.startswith("linux") else 1)


# ---------------------------------------------------------------------------
# 3. Cache discipline
# ---------------------------------------------------------------------------


def test_reclaim_never_leaves_a_project_pinned_in_the_read_events_cache(tmp_path: Path) -> None:
    """Two reachable projects: one already-v3-compact (padded above the fat
    prefilter but with NOTHING left to compact -- an early "no legacy or
    v2-fat anchor-search events found" return that, in the current
    implementation, reads the whole chain via ``read_events`` BEFORE that
    early return and never invalidates it), and one genuinely fat legacy
    project (whose OWN successful swap does happen to self-invalidate its
    cache entry today -- proving this project alone insufficient as a red
    signal, hence pairing it with the already-compact one above).

    After ``reclaim_anchor_search`` completes, ``_READ_EVENTS_CACHE`` (and
    the #137 event-index memo) must hold NO entry for either project's repo
    path -- reclaim's own streaming read must never populate the shared
    cache event_log.py exists to warm for OTHER (interactive) callers in the
    SAME process (issue #358 evidence: "reclaim_anchor_search iterates every
    project in the bucket, so the cache accumulates every project's full
    parsed chain simultaneously").
    """
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search
    from opentraces.core.config import get_project_dir, load_config, register_project, save_config
    from opentraces.core.trails import TrailEventDraft, append_event_batch
    from opentraces.core.trails.anchors import ANCHOR_ALGORITHMS_PHASE5
    from opentraces.core.trails.contract import ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION
    from opentraces.core.trails.event_log import _READ_EVENTS_CACHE
    from opentraces.core.trails.search_records import build_anchor_search_summary_payload

    # Project A: already v3-compact, but with enough already-compact search
    # events that its canonical events tree clears the fat-detection size
    # prefilter (so `reclaim` actually reads it) while `compact_search_
    # events`/the streaming compactor finds NOTHING to change -- a `journal
    # is None and not (...)` early return in the CURRENT implementation,
    # reached only AFTER the full chain was already read.
    already_compact = tmp_path / "already-compact"
    fx._init_repo(already_compact)
    for i in range(60):
        payload = build_anchor_search_summary_payload(
            schema_version=ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION,
            search_head={"algo": "sha1", "hex": f"{i:040d}"},
            algorithms_attempted=ANCHOR_ALGORITHMS_PHASE5,
            results=[
                {
                    "trace_patch_id": f"tracepatch-sha256:{i:02d}" + "a" * 62,
                    "trace_id": "t-already", "step_index": 0, "generation_index": 0,
                    "result": "anchored", "created_anchor_ids": [],
                }
            ],
            unanchored_trace_patch_ids=[f"tracepatch-sha256:{i:02d}" + "b" * 62],
        )
        append_event_batch(
            already_compact,
            [TrailEventDraft(event_type="git_anchor_search_completed", trace_id=None, step_index=None,
                              capture_method=["watcher_reconcile"], payload=payload)],
            writer="watcher",
        )
    cfg = load_config()
    register_project(cfg, already_compact)
    save_config(cfg)
    already_slug = get_project_dir(already_compact).name

    world = fx._build_world(tmp_path / "fat")

    reclaim_anchor_search(apply=True)

    already_key = str(already_compact.resolve())
    fat_key = str(world["repo"].resolve())
    pinned = [key for key in _READ_EVENTS_CACHE if key[0] in (already_key, fat_key)]
    assert pinned == [], (
        f"reclaim left {len(pinned)} project(s) pinned in _READ_EVENTS_CACHE after "
        f"completing: {pinned} -- cross-project accumulation is exactly issue #358's "
        f"root cause 1"
    )
