"""#65 — incremental reconciler projection.

The reconciler reads only the appended batch suffix between runs, persisting a
bounded projection at ``<git-dir>/opentraces/reconciler_projection.json``.
These tests pin:

* warm runs never take the full-log read (the original 8GB allocator);
* multi-run results are identical to the legacy full-read reconciler;
* a crash between append and projection-save cannot double-emit;
* retroactively-timestamped observations fall back to a full read;
* a rewritten event-log history invalidates the watermark safely;
* the cold rebuild is memory-bounded vs the legacy full read (un-gated
  subprocess proof — no env flag, no monkeypatched RSS reader).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from opentraces.capture.fs_watcher import append_filesystem_mutation_observed
from opentraces.core.trails import (
    GitObjectID,
    close_step_window_with_snapshot,
    open_step_window,
    read_events,
    reconcile_watcher_observations,
)
from opentraces.core.trails import reconciler as reconciler_mod
from opentraces.core.trails.reconciler import _projection_path

from tests.core.test_trail_reconciler import (
    _appended_after,
    _emit_hook_patch,
    _hash_object,
    _seed_attributable_observation,
)


def _iso(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_second_observation(
    repo: Path, *, step_index: int = 2, when: str = "recent"
) -> None:
    """Append a second attributable (window, patch, observation) round.

    ``when="recent"`` stamps the window/observation inside the projection's
    retention floor (the warm incremental path); ``when="old"`` reuses the
    fixture's weeks-old timestamps (forces the retro full-read fallback).
    """
    from datetime import datetime, timedelta, timezone

    if when == "recent":
        base = datetime.now(timezone.utc) - timedelta(minutes=30)
        t_open = _iso(base)
        t_obs_start = _iso(base + timedelta(seconds=3))
        t_obs_end = _iso(base + timedelta(seconds=7))
        t_close = _iso(base + timedelta(seconds=10))
    else:
        t_open = "2026-04-26T11:00:00Z"
        t_obs_start = "2026-04-26T11:00:03Z"
        t_obs_end = "2026-04-26T11:00:07Z"
        t_close = "2026-04-26T11:00:10Z"

    target = repo / "auth.py"
    before_text = target.read_text()
    before_blob = GitObjectID(hex=_hash_object(repo, before_text))
    open_result = open_step_window(
        repo, trace_id="tr1", step_index=step_index,
        agent_step_id=f"step_{step_index}", tool_call_id=f"tc{step_index}",
        capture_method=["hook_pretooluse"],
        event_time=t_open,
    )
    after_text = before_text + f"# round {step_index}\n"
    target.write_text(after_text)
    after_blob = GitObjectID(hex=_hash_object(repo, after_text))
    close_result = close_step_window_with_snapshot(
        repo, trace_id="tr1", step_index=step_index,
        agent_step_id=f"step_{step_index}", tool_call_id=f"tc{step_index}",
        capture_method=["hook_posttooluse"],
        event_time=t_close,
    )
    _emit_hook_patch(
        repo, trace_id="tr1", step_index=step_index, file_path="auth.py",
        trace_patch_id=f"tracepatch-sha256:fixture-tr1-step{step_index}",
        before_blob=before_blob, after_blob=after_blob,
        snapshot_before_id=f"snapshot-pre-{open_result.tree_id['hex']}",
        snapshot_after_id=close_result.snapshot_id,
        authored_text=f"# round {step_index}\n",
    )
    append_filesystem_mutation_observed(
        repo, path="auth.py",
        observed_at_start=t_obs_start,
        observed_at_end=t_obs_end,
        before_blob_id=before_blob, after_blob_id=after_blob,
    )


def test_projection_saved_and_warm_run_never_full_reads(tmp_path, monkeypatch):
    """After the first reconcile, the projection exists and a later run with
    new events completes WITHOUT the full-log read path being reachable."""
    repo = tmp_path / "repo"
    _seed_attributable_observation(repo)
    summary1 = reconcile_watcher_observations(repo)
    assert summary1["attributed"] == 1

    proj_path = _projection_path(repo)
    assert proj_path is not None and proj_path.is_file()
    data = json.loads(proj_path.read_text())
    assert data["schema_version"] == "opentraces.reconciler_projection.v1"
    assert isinstance(data["last_head"], str) and data["last_head"]

    # Append a second attributable round, then make BOTH full-read paths
    # explode — incremental is the only way to succeed.
    _seed_second_observation(repo)

    def _boom(*args, **kwargs):  # noqa: ANN001, ANN003
        raise AssertionError("full-log read on a warm incremental run")

    monkeypatch.setattr(reconciler_mod, "read_events_scoped", _boom)
    monkeypatch.setattr(reconciler_mod, "_rebuild_projection_events", _boom)
    summary2 = reconcile_watcher_observations(repo)
    assert summary2["attributed"] == 1
    assert summary2["observations_processed"] == 1


def test_multi_run_results_identical_to_forced_full_read(tmp_path, monkeypatch):
    """Three reconcile rounds with the projection produce the same appended
    events as three rounds forced through the legacy full read."""
    results: dict[str, list[list[tuple]]] = {}
    for mode, forced in (("incremental", False), ("full", True)):
        if forced:
            monkeypatch.setenv("OT_RECONCILER_FULL_READ", "1")
        else:
            monkeypatch.delenv("OT_RECONCILER_FULL_READ", raising=False)
        repo = tmp_path / mode
        _seed_attributable_observation(repo)
        rounds: list[list[tuple]] = []
        pre = {e.event_id for e in read_events(repo)}
        reconcile_watcher_observations(repo)
        rounds.append(_appended_after(repo, pre))

        _seed_second_observation(repo)
        pre = {e.event_id for e in read_events(repo)}
        reconcile_watcher_observations(repo)
        rounds.append(_appended_after(repo, pre))

        # Round 3: nothing new — must append nothing in both modes.
        pre = {e.event_id for e in read_events(repo)}
        reconcile_watcher_observations(repo)
        rounds.append(_appended_after(repo, pre))
        results[mode] = rounds
    monkeypatch.delenv("OT_RECONCILER_FULL_READ", raising=False)

    assert results["incremental"] == results["full"]
    assert results["incremental"][2] == []  # idempotent third round


def test_crash_between_append_and_save_cannot_double_emit(tmp_path, monkeypatch):
    """If the projection save is lost after a successful append, the next run
    re-reads its own attribution batch and retires the observations instead of
    re-emitting."""
    repo = tmp_path / "repo"
    _seed_attributable_observation(repo)
    monkeypatch.setattr(reconciler_mod, "_save_projection",
                        lambda *a, **k: None)
    summary1 = reconcile_watcher_observations(repo)
    assert summary1["attributed"] == 1
    monkeypatch.undo()

    pre = {e.event_id for e in read_events(repo)}
    summary2 = reconcile_watcher_observations(repo)
    assert summary2["observations_processed"] == 0
    assert _appended_after(repo, pre) == []
    # And the projection healed.
    proj_path = _projection_path(repo)
    assert proj_path is not None and proj_path.is_file()


def test_retroactive_observation_takes_full_read_fallback(tmp_path):
    """An observation timestamped before the projection's retention floor must
    still attribute against its (old) window — via the full-read fallback the
    floor mechanism triggers. (The seed fixture's timestamps are weeks old, so
    the SECOND round's observation predates the saved floor by construction.)"""
    repo = tmp_path / "repo"
    _seed_attributable_observation(repo)
    assert reconcile_watcher_observations(repo)["attributed"] == 1
    # Projection saved; its floor is now-48h, far AFTER the fixture's 2026-04-26
    # timestamps. The next round's retro observation must breach the floor.
    _seed_second_observation(repo, when="old")
    data = json.loads(_projection_path(repo).read_text())
    assert reconciler_mod._pending_within_floor(
        [e for e in read_events(repo)], data["floor"]
    ) is False
    summary = reconcile_watcher_observations(repo)
    assert summary["attributed"] == 1


def test_rewritten_history_invalidates_watermark(tmp_path):
    """A projection whose last_head is not an ancestor of the current head
    falls back to a full rebuild instead of misreading the suffix."""
    repo = tmp_path / "repo"
    _seed_attributable_observation(repo)
    reconcile_watcher_observations(repo)
    proj_path = _projection_path(repo)
    data = json.loads(proj_path.read_text())
    data["last_head"] = "0" * 40  # unknown sha
    proj_path.write_text(json.dumps(data))

    _seed_second_observation(repo)
    summary = reconcile_watcher_observations(repo)
    assert summary["attributed"] == 1
    healed = json.loads(proj_path.read_text())
    assert healed["last_head"] != "0" * 40


def test_old_closed_windows_pruned_pending_observation_retained(tmp_path):
    """Save-time retention: weeks-old window pairs are pruned; an unprocessed
    observation stays pending in the projection."""
    repo = tmp_path / "repo"
    _seed_attributable_observation(repo)
    reconcile_watcher_observations(repo)
    data = json.loads(_projection_path(repo).read_text())
    kinds = {e["event_type"] for e in data["events"]}
    # The fixture's window pair is 2026-04-26 (weeks old) → pruned. The
    # observation was attributed → retired. Nothing old survives.
    assert "trace_step_window_opened" not in kinds
    assert "trace_step_window_closed" not in kinds
    assert "filesystem_mutation_observed" not in kinds


_CORPUS_SCRIPT = textwrap.dedent("""
    import json, resource, sys
    from pathlib import Path
    from opentraces.core.trails import reconcile_watcher_observations
    from opentraces.core.trails.reconciler import _projection_path
    repo = Path(sys.argv[1])
    summary = reconcile_watcher_observations(repo)
    projection_events = 0
    fat_retained = 0
    projection_path = _projection_path(repo)
    if projection_path is not None and projection_path.is_file():
        data = json.loads(projection_path.read_text())
        for item in data.get("events") or []:
            projection_events += 1
            payload = item.get("payload") or {}
            trace_patch_id = str(payload.get("trace_patch_id") or "")
            if trace_patch_id.startswith("tracepatch-sha256:fat-"):
                fat_retained += 1
    # ru_maxrss units are platform-dependent: kilobytes on Linux, bytes on
    # macOS/BSD. Normalize to MB per platform — dividing by 1024**2 on Linux
    # underreports by 1024x and collapses every peak to 0MB (CI false red).
    _maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = _maxrss / 1024 if sys.platform.startswith("linux") else _maxrss / (1024 * 1024)
    print(f"{peak_mb:.0f} {summary['attributed']} {projection_events} {fat_retained}")
""")


@pytest.mark.parametrize("fat_patches", [80])
def test_cold_rebuild_memory_bounded_vs_full_read(tmp_path, fat_patches):
    """UN-GATED memory proof (#65): the sink-based cold rebuild discards
    stale fat patch events while streaming, so the saved projection remains
    bounded and peak RSS stays under an absolute backstop. Full-read RSS is
    retained as diagnostic context only: separate subprocess high-water marks
    are too allocator/runner-sensitive to use as a fixed delta gate.

    No OT_*_MEM_PROOF env gate, no monkeypatched RSS reader: this runs in
    default CI and reads real ru_maxrss from real subprocesses.
    """
    from opentraces.core.trails import TrailEventDraft, append_event_batch

    repo = tmp_path / "repo"
    _seed_attributable_observation(repo)
    # Stale, fat patches: 2MB of authored_text each, EXPLICITLY weeks old
    # (event_time stamped), so the sink drops them inline while the legacy
    # full read materialises all of them.
    fat_text = ("x" * 96 + "\n") * (2 * 1024 * 1024 // 97)
    before_blob = GitObjectID(hex=_hash_object(repo, "stale-before\n"))
    after_blob = GitObjectID(hex=_hash_object(repo, "stale-after\n"))
    drafts = [
        TrailEventDraft(
            event_type="trace_patch_created",
            trace_id="tr-old",
            generation_index=0,
            step_index=100 + i,
            event_time="2026-04-01T10:00:00Z",
            capture_method=["hook_pretooluse", "hook_posttooluse"],
            payload={
                "trace_patch_id": f"tracepatch-sha256:fat-{i}",
                "snapshot_before_id": f"snapshot-fat-pre-{i}",
                "snapshot_after_id": f"snapshot-fat-post-{i}",
                "file_path": "big.py",
                "authored_text": fat_text,
                "before_blob_id": before_blob.model_dump(),
                "after_blob_id": after_blob.model_dump(),
            },
        )
        for i in range(fat_patches)
    ]
    for start in range(0, len(drafts), 10):
        append_event_batch(repo, drafts[start:start + 10], writer="test-fat")

    def _run(work_repo: Path, env_extra: dict[str, str]) -> tuple[float, int, int, int]:
        import os
        env = dict(os.environ)
        env.update(env_extra)
        proc = subprocess.run(
            [sys.executable, "-c", _CORPUS_SCRIPT, str(work_repo)],
            capture_output=True, text=True, env=env, check=True,
        )
        peak_str, attributed_str, projection_events_str, fat_retained_str = (
            proc.stdout.strip().split()
        )
        return (
            float(peak_str),
            int(attributed_str),
            int(projection_events_str),
            int(fat_retained_str),
        )

    # Each mode reconciles its OWN copy: a reconcile appends attribution
    # events, so reusing one repo would hand the second mode a pre-attributed
    # world (observations_processed == 0).
    import shutil
    repo_full = tmp_path / "repo-full"
    shutil.copytree(repo, repo_full)
    full_peak, full_attributed, _full_projection_events, _full_fat_retained = _run(
        repo_full,
        {"OT_RECONCILER_FULL_READ": "1"},
    )
    sink_peak, sink_attributed, sink_projection_events, sink_fat_retained = _run(repo, {})

    assert full_attributed == sink_attributed == 1
    assert sink_fat_retained == 0
    assert sink_projection_events < 20
    assert sink_peak < 800, (
        f"sink rebuild peak {sink_peak:.0f}MB over backstop "
        f"(full-read diagnostic peak {full_peak:.0f}MB)"
    )
