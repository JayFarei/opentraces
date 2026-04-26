"""Phase 5 watcher reconciler — Trace Trails."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from opentraces.capture.fs_watcher import append_filesystem_mutation_observed
from opentraces.core.trails import (
    GitObjectID,
    TrailEventDraft,
    append_event_batch,
    assert_known_capture_limitations,
    close_step_window_with_snapshot,
    is_known_capture_limitation,
    open_step_window,
    read_events,
    reconcile_watcher_observations,
)


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _hash_object(repo: Path, content: str) -> str:
    proc = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=content,
        text=True,
        capture_output=True,
        check=True,
    )
    return proc.stdout.strip()


def _emit_hook_patch(
    repo: Path,
    *,
    trace_id: str,
    step_index: int,
    file_path: str,
    trace_patch_id: str,
    before_blob: GitObjectID,
    after_blob: GitObjectID,
    snapshot_before_id: str,
    snapshot_after_id: str,
    authored_text: str,
) -> None:
    """Mimic the hook emission of a Phase 2 ``trace_patch_created`` event.

    Production code reaches this through ``emit_step_window_events_from_record``
    during ingest. Tests emit it directly to keep the reconciler under test
    decoupled from the hook ingest pipeline.
    """
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=trace_id,
                generation_index=0,
                step_index=step_index,
                capture_method=["hook_pretooluse", "hook_posttooluse"],
                payload={
                    "trace_patch_id": trace_patch_id,
                    "snapshot_before_id": snapshot_before_id,
                    "snapshot_after_id": snapshot_after_id,
                    "file_path": file_path,
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": authored_text,
                    "raw_authored_hash": "sha256:fixture",
                    "git_clean_hash": "sha256:fixture",
                    "before_blob_id": before_blob.model_dump(mode="json"),
                    "after_blob_id": after_blob.model_dump(mode="json"),
                    "limitations": [],
                },
            )
        ],
        writer="capture-claude-code",
    )


def test_watcher_observation_inside_unique_step_window_attributes_patch(
    tmp_path: Path,
) -> None:
    """Tracer: watcher saw the same mutation as the hook → corroboration.

    The hook emits ``trace_patch_created`` with ``capture_method =
    [hook_pretooluse, hook_posttooluse]``. The watcher independently
    observes the same file mutation inside the firm step window. After the
    reconciler runs, the latest ``trace_patch_created`` event for that
    patch carries ``watcher_backstop`` alongside the original hook tags,
    and a ``watcher_observation_attributed`` event records the decision.
    """
    _init_repo(tmp_path)

    target = tmp_path / "auth.py"
    before_text = "def authorize():\n    return False\n"
    target.write_text(before_text)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed auth"], cwd=tmp_path, check=True)
    before_blob = GitObjectID(hex=_hash_object(tmp_path, before_text))

    open_result = open_step_window(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="tc1",
        capture_method=["hook_pretooluse"],
        event_time="2026-04-26T10:00:00Z",
    )

    after_text = "def authorize():\n    return True\n"
    target.write_text(after_text)
    after_blob = GitObjectID(hex=_hash_object(tmp_path, after_text))

    close_result = close_step_window_with_snapshot(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="tc1",
        capture_method=["hook_posttooluse"],
        event_time="2026-04-26T10:00:10Z",
    )

    _emit_hook_patch(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        file_path="auth.py",
        trace_patch_id="tracepatch-sha256:fixture-tr1-step1",
        before_blob=before_blob,
        after_blob=after_blob,
        snapshot_before_id=f"snapshot-pre-{open_result.tree_id['hex']}",
        snapshot_after_id=close_result.snapshot_id,
        authored_text="    return True\n",
    )

    append_filesystem_mutation_observed(
        tmp_path,
        path="auth.py",
        observed_at_start="2026-04-26T10:00:03Z",
        observed_at_end="2026-04-26T10:00:07Z",
        before_blob_id=before_blob,
        after_blob_id=after_blob,
    )

    summary = reconcile_watcher_observations(tmp_path)
    assert summary["attributed"] == 1
    assert summary["patches_upgraded"] == 1
    assert summary["unbounded_mutation_window"] == 0
    assert summary["concurrent_writer_overlap"] == 0

    events = read_events(tmp_path)
    patch_events = [e for e in events if e.event_type == "trace_patch_created"]
    assert len(patch_events) == 2, "hook patch + reconciler upgrade"
    latest_patch = max(patch_events, key=lambda e: e.event_sequence)
    assert "hook_posttooluse" in latest_patch.capture_method
    assert "watcher_backstop" in latest_patch.capture_method
    earliest_patch = min(patch_events, key=lambda e: e.event_sequence)
    assert "watcher_backstop" not in earliest_patch.capture_method
    # Append-only honesty: the original hook patch event is preserved
    # untouched. The upgrade is a NEW event with a different content_hash
    # and event_id; the original capture_method, content_hash, and
    # event_id all stay byte-equivalent across reconciler runs.
    assert earliest_patch.capture_method == ["hook_pretooluse", "hook_posttooluse"]
    assert earliest_patch.event_id != latest_patch.event_id
    assert earliest_patch.content_hash == latest_patch.content_hash, (
        "payload content_hash is unchanged because the upgrade re-emits "
        "the same payload; only capture_method on the envelope changes"
    )
    assert (
        latest_patch.payload["trace_patch_id"]
        == earliest_patch.payload["trace_patch_id"]
    )
    assert latest_patch.payload["file_path"] == "auth.py"

    attributed_events = [
        e for e in events if e.event_type == "watcher_observation_attributed"
    ]
    assert len(attributed_events) == 1
    attribution = attributed_events[0]
    assert attribution.payload["result"] == "attributed"
    assert attribution.payload["capture_limitations"] == []
    assert attribution.trace_id == "tr1"
    assert attribution.step_index == 1
    assert (
        attribution.payload["upgraded_trace_patch_id"]
        == latest_patch.payload["trace_patch_id"]
    )
    assert attribution.capture_method == ["watcher_backstop"]


def test_watcher_observation_event_carries_no_attribution_fields(tmp_path: Path) -> None:
    """The watcher payload is agent-agnostic by contract.

    A ``filesystem_mutation_observed`` event has no trace_id, step_index, or
    agent_step_id on the envelope, and its payload exposes only the
    observed-tuple fields. Attribution belongs to the reconciler.
    """
    _init_repo(tmp_path)
    after_blob_hex = _hash_object(tmp_path, "x\n")
    event = append_filesystem_mutation_observed(
        tmp_path,
        path="new.txt",
        observed_at_start="2026-04-26T11:00:00Z",
        observed_at_end="2026-04-26T11:00:01Z",
        after_blob_id=GitObjectID(hex=after_blob_hex),
    )
    assert event.trace_id is None
    assert event.step_index is None
    assert event.capture_method == ["watcher_backstop"]
    assert "trace_id" not in event.payload
    assert "step_index" not in event.payload
    assert "agent_step_id" not in event.payload
    assert set(event.payload.keys()) >= {
        "path",
        "observed_at_start",
        "observed_at_end",
        "before_blob_id",
        "after_blob_id",
    }


def test_reconciler_replay_is_byte_stable(tmp_path: Path) -> None:
    """Smoke idempotency: a second reconciler run on the same event set
    appends nothing new and the canonical event log is byte-equivalent.

    Fixture 5 (``test_reconciler_is_idempotent``) goes deeper, exercising
    interleaved appends and replay ordering. This is the cheap guard that
    lives alongside the tracer so we catch a regression the moment any
    reconciler change drops the dedup keyed by ``observation_event_id``.
    """
    _init_repo(tmp_path)
    target = tmp_path / "auth.py"
    target.write_text("def authorize():\n    return False\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed auth"], cwd=tmp_path, check=True)
    after_blob = GitObjectID(hex=_hash_object(tmp_path, "x\n"))

    open_step_window(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="tc1",
        capture_method=["hook_pretooluse"],
        event_time="2026-04-26T10:00:00Z",
    )
    close_step_window_with_snapshot(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="tc1",
        capture_method=["hook_posttooluse"],
        event_time="2026-04-26T10:00:10Z",
    )
    append_filesystem_mutation_observed(
        tmp_path,
        path="unrelated.txt",
        observed_at_start="2026-04-26T10:00:03Z",
        observed_at_end="2026-04-26T10:00:07Z",
        after_blob_id=after_blob,
    )

    summary_first = reconcile_watcher_observations(tmp_path)
    events_first = read_events(tmp_path)
    summary_second = reconcile_watcher_observations(tmp_path)
    events_second = read_events(tmp_path)

    assert summary_first["observations_processed"] == 1
    assert summary_second["observations_processed"] == 0
    assert summary_second["attributed"] == 0
    assert summary_second["unbounded_mutation_window"] == 0
    assert len(events_first) == len(events_second)
    for left, right in zip(events_first, events_second):
        assert left.event_id == right.event_id
        assert left.event_sequence == right.event_sequence
        assert left.content_hash == right.content_hash


def test_non_firm_window_is_invisible_to_attribution(tmp_path: Path) -> None:
    """A reconstructed-after-the-fact window must NOT receive attribution.

    Plan §Phase 5 (line 226) requires "fully inside exactly one writer's
    *firm* step window." A window emitted with
    ``boundary_firmness="provisional"`` should be skipped by
    ``_matching_windows`` so the observation falls into
    ``unbounded_mutation_window`` instead.
    """
    _init_repo(tmp_path)
    after_blob = GitObjectID(hex=_hash_object(tmp_path, "x\n"))

    open_step_window(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="tc1",
        capture_method=["manual_attach"],
        event_time="2026-04-26T10:00:00Z",
        boundary_firmness="provisional",
    )
    close_step_window_with_snapshot(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="tc1",
        capture_method=["manual_attach"],
        event_time="2026-04-26T10:00:10Z",
        boundary_firmness="provisional",
    )
    append_filesystem_mutation_observed(
        tmp_path,
        path="auth.py",
        observed_at_start="2026-04-26T10:00:03Z",
        observed_at_end="2026-04-26T10:00:07Z",
        after_blob_id=after_blob,
    )

    summary = reconcile_watcher_observations(tmp_path)
    assert summary["attributed"] == 0
    assert summary["unbounded_mutation_window"] == 1
    events = read_events(tmp_path)
    attributions = [
        e for e in events if e.event_type == "watcher_observation_attributed"
    ]
    assert len(attributions) == 1
    assert attributions[0].payload["result"] == "unattributed"
    assert attributions[0].payload["capture_limitations"] == [
        "unbounded_mutation_window"
    ]


def test_watcher_emission_validates_payload_at_write_time(tmp_path: Path) -> None:
    """Malformed observations must be rejected up front.

    Validating at write time means the reconciler can trust the canonical
    event log and never has to defensively parse timestamps mid-loop.
    """
    _init_repo(tmp_path)
    after_blob = GitObjectID(hex=_hash_object(tmp_path, "x\n"))

    with pytest.raises(ValueError, match="non-empty"):
        append_filesystem_mutation_observed(
            tmp_path,
            path="",
            observed_at_start="2026-04-26T10:00:00Z",
            observed_at_end="2026-04-26T10:00:01Z",
            after_blob_id=after_blob,
        )

    with pytest.raises(ValueError, match="ISO-8601"):
        append_filesystem_mutation_observed(
            tmp_path,
            path="x.txt",
            observed_at_start="not a timestamp",
            observed_at_end="2026-04-26T10:00:01Z",
            after_blob_id=after_blob,
        )

    with pytest.raises(ValueError, match="must not precede"):
        append_filesystem_mutation_observed(
            tmp_path,
            path="x.txt",
            observed_at_start="2026-04-26T10:00:05Z",
            observed_at_end="2026-04-26T10:00:01Z",
            after_blob_id=after_blob,
        )


def test_capture_limitations_vocabulary_is_closed() -> None:
    """The Phase 5 closed vocabulary must reject unknown tags up front."""
    assert is_known_capture_limitation("concurrent_writer_overlap")
    assert is_known_capture_limitation("unbounded_mutation_window")
    assert not is_known_capture_limitation("invented_tag")
    assert_known_capture_limitations(["hook_only", "watcher_buffer_overflow"])
    with pytest.raises(ValueError, match="unknown capture_limitations"):
        assert_known_capture_limitations(["something_made_up"])
