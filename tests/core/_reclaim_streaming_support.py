"""Shared world-builders and a subprocess measurement runner for issue #358's
memory-bounded ``bucket reclaim`` streaming stage.

Not a test module itself (no ``test_`` prefix, not pytest-collected) -- see
``test_bucket_reclaim_search_streaming.py`` for the tests that import it.

The ``__main__`` entry point below is invoked as a FRESH child process (never
imported) so its own ``resource.getrusage(RUSAGE_SELF).ru_maxrss`` reflects
ONLY the work this process does (world building happens in the PARENT process
beforehand, on disk, under a fixed HOME the child then points at) -- see
ORCHESTRATOR RULING R2: ``ru_maxrss`` is a process-lifetime high-water mark,
so mixing "build the fixture" and "run reclaim" in one process would let the
build's own (irrelevant) allocations pollute the measurement.
"""
from __future__ import annotations

import contextlib
import json
import os
import resource
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_FROZEN_EVENT_TIME = "2026-01-01T00:00:00Z"


@contextlib.contextmanager
def frozen_event_time(value: str = _FROZEN_EVENT_TIME):
    """Freeze BOTH sources of run-to-run nondeterminism in
    ``event_log.append_event_batch`` so two SEPARATE process runs that build
    the "same" world byte-for-byte agree:

    1. ``event_time`` -- a draft with none explicit falls back to
       ``utc_now_str()`` (wall-clock).
    2. ``batch_id`` -- always ``f"batch-{uuid.uuid4().hex}"`` (random),
       embedded verbatim in every event's own ``model_dump()`` (a
       ``TrailEvent`` field, not just append-time bookkeeping) — so even
       with (1) frozen, two runs' PRE-compaction companion bytes still
       differed by a few bytes until this was frozen too. POST-compaction
       content was never affected (``search_compaction._finalize_slot``
       always stamps the FIXED ``_COMPACTION_BATCH_ID`` constant, never a
       fresh uuid), which is why that half matched before this fix and only
       the legacy/v2-fat SOURCE shape's byte count did not.

    The uuid replacement is a deterministic, monotonically incrementing
    sequence (not a constant) — ``append_event_batch`` is called many times
    per world and the legacy-group grouping this module's fixtures exercise
    depends on DISTINCT ``batch_id``s per reconcile-run call, only their
    VALUES needing to be reproducible.

    Needed for the equivalence fixture: world construction and reclaim run
    in genuinely SEPARATE process invocations (the fixture was captured in
    one process, the test rebuilds the identical world and reclaims it in
    another), so wall-clock- or randomness-derived content would make even
    an UNCHANGED implementation fail a byte-identical comparison. Every
    other timestamp compaction can introduce
    (``search_compaction._finalize_slot``'s ``slot.event_time or utc_now_
    str()`` for a summary slot) only falls back to ``utc_now_str()`` when
    the SOURCE event's own ``event_time`` is ``None`` -- never true for
    anything this module's world builders emit, since every draft passes
    through ``append_event_batch`` first -- so freezing only the append
    path is sufficient.
    """
    import uuid as _uuid_mod

    from opentraces.core.trails import event_log

    original_time = event_log.utc_now_str
    original_uuid4 = _uuid_mod.uuid4
    counter = {"n": 0}

    def _deterministic_uuid4() -> _uuid_mod.UUID:
        counter["n"] += 1
        return _uuid_mod.UUID(int=counter["n"])

    event_log.utc_now_str = lambda: value
    # ``event_log.py`` does ``import uuid`` (module-level), so ``event_log.
    # uuid`` IS the same singleton object as the ``uuid`` module imported
    # here -- rebinding its ``uuid4`` attribute here reaches ``event_log``'s
    # own ``uuid.uuid4()`` calls too, no separate patch target needed.
    _uuid_mod.uuid4 = _deterministic_uuid4
    try:
        yield
    finally:
        event_log.utc_now_str = original_time
        _uuid_mod.uuid4 = original_uuid4


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _fat_v2_event(*, commit_hex: str, trace_id: str, entry_count: int, tag: str):
    from opentraces.core.trails import TrailEventDraft
    from opentraces.core.trails.anchors import ANCHOR_ALGORITHMS_PHASE5

    # Padding-only entries with no backing ``trace_patch_created`` -- v2-fat
    # entries carry ``trace_id`` inline, so ``_search_touch_ids`` /
    # ``summary_search_touches_trace`` resolve the trace directly, exactly
    # like ``test_bucket_reclaim_search.py``'s own ``_build_world`` fixture
    # (see its docstring). ``entry_count`` is calibrated by the caller to hit
    # a target serialized-payload size (~2MB per event here).
    entries = [
        {
            "trace_patch_id": f"tracepatch-sha256:{tag}{i:06d}" + "b" * 51,
            "trace_id": trace_id,
            "step_index": i,
            "generation_index": 0,
            "result": "unknown",
            "created_anchor_ids": [],
        }
        for i in range(entry_count)
    ]
    return TrailEventDraft(
        event_type="git_anchor_search_completed",
        trace_id=None,
        step_index=None,
        capture_method=["watcher_reconcile"],
        payload={
            "schema_version": "opentraces.trail.anchor_search.v2",
            "summary": True,
            "search_head": {"algo": "sha1", "hex": commit_hex},
            "algorithms_attempted": ANCHOR_ALGORITHMS_PHASE5,
            "searched": entry_count,
            "anchored": 0,
            "unknown": entry_count,
            "results": entries,
        },
    )


def build_fat_world(
    root: Path,
    *,
    num_projects: int = 2,
    events_per_project: int = 20,
    entry_count: int = 8500,
) -> dict:
    """Build ``num_projects`` reachable git repos, each carrying
    ``events_per_project`` v2-fat anchor-search summaries sized to
    ``entry_count`` padding entries (~2MB serialized each by default) --
    the "40 x 2MB synthetic fat summaries across 2 projects" scenario issue
    #358's memory-bound acceptance test calls for. No trace-record / bucket
    projection step (``bucket_repair``) is needed here -- this world only
    needs to exist as CANONICAL git history for ``reclaim_anchor_search`` to
    read; the memory bound under test is about the READ+COMPACT+SWAP path,
    not the companion-projection bookkeeping ``_build_world`` also does.
    """
    from opentraces.core.config import get_project_dir, load_config, register_project, save_config
    from opentraces.core.trails import append_event_batch

    slugs = []
    for p in range(num_projects):
        repo = root / f"proj{p}"
        _init_repo(repo)
        for e in range(events_per_project):
            commit_hex = f"{p}{e:039d}"[:40]
            trace_id = f"t-{p}-{e}"
            event = _fat_v2_event(
                commit_hex=commit_hex, trace_id=trace_id, entry_count=entry_count, tag=f"{p}{e:02d}"
            )
            append_event_batch(repo, [event], writer="watcher")
        cfg = load_config()
        register_project(cfg, repo)
        save_config(cfg)
        slugs.append(get_project_dir(repo).name)
    return {"root": root, "slugs": slugs}


def largest_event_bytes(entry_count: int = 8500) -> int:
    """The exact serialized size (bytes, ``model_dump(mode='json')`` + the
    same ``json.dumps(..., sort_keys=True, indent=2)`` encoding
    ``event_log._write_batch_tree``/``StreamingChainWriter.stage`` write to
    disk) of ONE fat event this module's world builder produces -- the
    acceptance test's memory budget is derived FROM this real number, not a
    guess."""
    from opentraces.core.trails.models import finalize_event

    draft = _fat_v2_event(commit_hex="a" * 40, trace_id="t-x", entry_count=entry_count, tag="zz")
    data = {
        "event_sequence": 1,
        "event_time": "2026-01-01T00:00:00Z",
        "previous_event_id": None,
        "trace_id": draft.trace_id,
        "generation_index": draft.generation_index,
        "step_index": draft.step_index,
        "batch_id": "b",
        "writer": "watcher",
        "capture_method": draft.capture_method,
        "event_type": draft.event_type,
        "payload": draft.payload,
    }
    data = {k: v for k, v in data.items() if v is not None}
    event = finalize_event(data)
    text = json.dumps(event.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"
    return len(text.encode("utf-8"))


def _child_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("OT_OPENTRACES_DIR", None)
    src = str(_PROJECT_ROOT / "src")
    schema_src = str(_PROJECT_ROOT / "packages" / "opentraces-schema" / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join([src, schema_src] + ([existing] if existing else []))
    return env


_CHILD_SCRIPT = """
import resource, sys
from opentraces.core.bucket_reclaim_search import reclaim_anchor_search
result = reclaim_anchor_search(apply=False)
usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
sys.stderr.write(repr({"projects": [p.project_slug for p in result.projects], "actions": [p.action for p in result.projects]}) + "\\n")
print(usage)
"""


def measure_reclaim_peak_rss_bytes(home: Path, *, timeout: float = 600) -> int:
    """Run ``reclaim_anchor_search(apply=False)`` in a FRESH child process
    rooted at ``home`` (a pre-built world -- see ``build_fat_world``) and
    return that child's own peak RSS in bytes.

    ``ru_maxrss`` units are platform-dependent (KB on Linux, bytes on
    macOS/BSD) -- ``sys.platform`` in the CHILD (not the parent, which may
    differ under cross-platform CI) decides the multiplier.
    """
    python = sys.executable
    proc = subprocess.run(
        [python, "-c", _CHILD_SCRIPT + "\nimport sys as _s\nprint('PLATFORM:' + _s.platform, file=sys.stderr)\n"],
        cwd=str(home),
        env=_child_env(home),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"reclaim measurement child failed (rc={proc.returncode}):\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    raw_rss = int(lines[-1])
    platform_line = next((ln for ln in proc.stderr.splitlines() if ln.startswith("PLATFORM:")), "PLATFORM:" + sys.platform)
    child_platform = platform_line.split(":", 1)[1]
    # KB on Linux, bytes on macOS/BSD (the ru_maxrss units gotcha).
    multiplier = 1024 if child_platform.startswith("linux") else 1
    return raw_rss * multiplier
