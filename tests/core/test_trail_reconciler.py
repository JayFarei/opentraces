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
from opentraces.core.trails.explain import explain_trace_step


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
    assert latest_patch.payload["trace_patch_id"] == earliest_patch.payload["trace_patch_id"]
    assert latest_patch.payload["file_path"] == "auth.py"
    explanation = explain_trace_step(tmp_path, "tr1", 1)
    patch_sources = [
        source
        for source in explanation["source_events"]
        if source["event_type"] == "trace_patch_created"
    ]
    assert patch_sources[-1]["event_id"] == latest_patch.event_id
    assert "watcher_backstop" in patch_sources[-1]["capture_method"]

    attributed_events = [e for e in events if e.event_type == "watcher_observation_attributed"]
    assert len(attributed_events) == 1
    attribution = attributed_events[0]
    assert attribution.payload["result"] == "attributed"
    assert attribution.payload["capture_limitations"] == []
    assert attribution.trace_id == "tr1"
    assert attribution.step_index == 1
    assert attribution.payload["upgraded_trace_patch_id"] == latest_patch.payload["trace_patch_id"]
    assert attribution.capture_method == ["watcher_backstop"]


def test_watcher_corroboration_requires_matching_blob_identity(tmp_path: Path) -> None:
    """Same path and same firm window is not enough for corroboration.

    The watcher must have observed the same before/after blob transition as
    the hook patch before the reconciler can upgrade that patch with
    ``watcher_backstop``. A mismatched watcher observation is still attributed
    to the unique firm window, but it becomes its own watcher-backed patch.
    """
    _init_repo(tmp_path)

    target = tmp_path / "auth.py"
    before_text = "def authorize():\n    return False\n"
    after_text = "def authorize():\n    return True\n"
    target.write_text(before_text)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed auth"], cwd=tmp_path, check=True)
    hook_before = GitObjectID(hex=_hash_object(tmp_path, before_text))
    hook_after = GitObjectID(hex=_hash_object(tmp_path, after_text))

    open_step_window(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="tc1",
        capture_method=["hook_pretooluse"],
        event_time="2026-04-26T10:00:00Z",
    )
    target.write_text(after_text)
    close_step_window_with_snapshot(
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
        trace_patch_id="tracepatch-sha256:hook",
        before_blob=hook_before,
        after_blob=hook_after,
        snapshot_before_id="snapshot-pre",
        snapshot_after_id="snapshot-post",
        authored_text="    return True\n",
    )

    watcher_before = GitObjectID(hex=_hash_object(tmp_path, "unrelated before\n"))
    watcher_after = GitObjectID(hex=_hash_object(tmp_path, "unrelated after\n"))
    obs = append_filesystem_mutation_observed(
        tmp_path,
        path="auth.py",
        observed_at_start="2026-04-26T10:00:03Z",
        observed_at_end="2026-04-26T10:00:04Z",
        before_blob_id=watcher_before,
        after_blob_id=watcher_after,
    )

    summary = reconcile_watcher_observations(tmp_path)
    assert summary["attributed"] == 1
    assert summary["patches_upgraded"] == 0
    assert summary["patches_created"] == 1

    events = read_events(tmp_path)
    upgraded_hook = [
        e
        for e in events
        if e.event_type == "trace_patch_created"
        and e.payload.get("trace_patch_id") == "tracepatch-sha256:hook"
        and "watcher_backstop" in e.capture_method
    ]
    assert upgraded_hook == []
    created = [
        e
        for e in events
        if e.event_type == "trace_patch_created"
        and e.payload.get("source_observation_event_id") == obs.event_id
    ]
    assert len(created) == 1
    assert created[0].payload["before_blob_id"] == watcher_before.model_dump(mode="json")
    assert created[0].payload["after_blob_id"] == watcher_after.model_dump(mode="json")


def test_unique_watcher_observation_without_hook_patch_emits_trace_patch(
    tmp_path: Path,
) -> None:
    """A unique firm window plus blob evidence is enough to create a patch.

    The watcher still does not assign attribution at observation time; the
    reconciler appends the ``trace_patch_created`` event after it has proved
    the observation is inside exactly one firm step window.
    """
    _init_repo(tmp_path)
    before_blob = GitObjectID(hex=_hash_object(tmp_path, "x = 1\n"))
    after_blob = GitObjectID(hex=_hash_object(tmp_path, "x = 2\n"))

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
    obs = append_filesystem_mutation_observed(
        tmp_path,
        path="generated.py",
        observed_at_start="2026-04-26T10:00:03Z",
        observed_at_end="2026-04-26T10:00:07Z",
        before_blob_id=before_blob,
        after_blob_id=after_blob,
    )

    summary = reconcile_watcher_observations(tmp_path)
    assert summary["attributed"] == 1
    assert summary["patches_created"] == 1
    assert summary["patches_upgraded"] == 0

    events = read_events(tmp_path)
    created = [
        e
        for e in events
        if e.event_type == "trace_patch_created"
        and e.payload.get("source_observation_event_id") == obs.event_id
    ]
    assert len(created) == 1
    patch = created[0]
    assert patch.trace_id == "tr1"
    assert patch.step_index == 1
    assert patch.capture_method == ["watcher_backstop"]
    assert patch.payload["file_path"] == "generated.py"
    assert patch.payload["tool_call_id"] == "tc1"
    assert patch.payload["authored_text"] == "x = 2\n"

    attribution = [e for e in events if e.event_type == "watcher_observation_attributed"][0]
    assert attribution.payload["created_trace_patch_id"] == patch.payload["trace_patch_id"]
    assert attribution.payload["tool_call_id"] == "tc1"


def test_duplicate_watcher_observations_share_one_content_derived_patch(
    tmp_path: Path,
) -> None:
    """Patch identity comes from content/window identity, not observation ID."""
    _init_repo(tmp_path)
    before_blob = GitObjectID(hex=_hash_object(tmp_path, "x = 1\n"))
    after_blob = GitObjectID(hex=_hash_object(tmp_path, "x = 2\n"))

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
    first = append_filesystem_mutation_observed(
        tmp_path,
        path="generated.py",
        observed_at_start="2026-04-26T10:00:03Z",
        observed_at_end="2026-04-26T10:00:07Z",
        before_blob_id=before_blob,
        after_blob_id=after_blob,
    )
    second = append_filesystem_mutation_observed(
        tmp_path,
        path="generated.py",
        observed_at_start="2026-04-26T10:00:04Z",
        observed_at_end="2026-04-26T10:00:08Z",
        before_blob_id=before_blob,
        after_blob_id=after_blob,
    )

    summary = reconcile_watcher_observations(tmp_path)
    assert summary["attributed"] == 2
    assert summary["patches_created"] == 1

    events = read_events(tmp_path)
    patches = [e for e in events if e.event_type == "trace_patch_created"]
    attributions = [e for e in events if e.event_type == "watcher_observation_attributed"]
    assert len(patches) == 1
    assert {e.payload["observation_event_id"] for e in attributions} == {
        first.event_id,
        second.event_id,
    }


def test_multiple_tool_windows_in_one_step_remain_distinct(tmp_path: Path) -> None:
    """Step identity alone is too coarse; tool windows are the boundary."""
    _init_repo(tmp_path)
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
        event_time="2026-04-26T10:00:05Z",
    )
    open_step_window(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="tc2",
        capture_method=["hook_pretooluse"],
        event_time="2026-04-26T10:00:06Z",
    )
    close_step_window_with_snapshot(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="tc2",
        capture_method=["hook_posttooluse"],
        event_time="2026-04-26T10:00:10Z",
    )
    obs = append_filesystem_mutation_observed(
        tmp_path,
        path="first-tool.txt",
        observed_at_start="2026-04-26T10:00:02Z",
        observed_at_end="2026-04-26T10:00:03Z",
        after_blob_id=after_blob,
    )

    summary = reconcile_watcher_observations(tmp_path)
    assert summary["attributed"] == 1
    assert summary["unbounded_mutation_window"] == 0

    events = read_events(tmp_path)
    attribution = [
        e
        for e in events
        if e.event_type == "watcher_observation_attributed"
        and e.payload["observation_event_id"] == obs.event_id
    ][0]
    assert attribution.trace_id == "tr1"
    assert attribution.step_index == 1
    assert attribution.payload["tool_call_id"] == "tc1"


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
    attributions = [e for e in events if e.event_type == "watcher_observation_attributed"]
    assert len(attributions) == 1
    assert attributions[0].payload["result"] == "unattributed"
    assert attributions[0].payload["capture_limitations"] == ["unbounded_mutation_window"]


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
    assert is_known_capture_limitation("incomplete_step_window_capture")
    assert not is_known_capture_limitation("invented_tag")
    assert_known_capture_limitations(["hook_only", "watcher_buffer_overflow"])
    with pytest.raises(ValueError, match="unknown capture_limitations"):
        assert_known_capture_limitations(["something_made_up"])


def test_watcher_observation_alone_does_not_assign_attribution(
    tmp_path: Path,
) -> None:
    """Plan §Phase 5 edge fixture #1.

    The watcher records a mutation with rich blob-id evidence, but no
    step window has been opened. The reconciler must NOT fabricate
    attribution. It records the gap as ``unbounded_mutation_window`` so
    the omission stays queryable, and refuses to mint a
    ``trace_patch_created`` from a watcher event alone.

    Adversarial dimensions stressed:
    * Observation gets an attribution row (not silently dropped).
    * generation_index defaults to 0 on the unattributed envelope.
    * ``attribution.trace_id is None`` and ``step_index is None`` ANSWER
      the missing-context truthfully rather than picking a default.
    * Even with non-null before+after blob ids, reconciler refuses to
      synthesize a patch — the watcher only observes.
    """
    _init_repo(tmp_path)
    before_blob = GitObjectID(hex=_hash_object(tmp_path, "before\n"))
    after_blob = GitObjectID(hex=_hash_object(tmp_path, "after\n"))

    obs = append_filesystem_mutation_observed(
        tmp_path,
        path="lonely.txt",
        observed_at_start="2026-04-26T10:00:00Z",
        observed_at_end="2026-04-26T10:00:01Z",
        before_blob_id=before_blob,
        after_blob_id=after_blob,
    )

    summary = reconcile_watcher_observations(tmp_path)
    assert summary["observations_processed"] == 1
    assert summary["unbounded_mutation_window"] == 1
    assert summary["attributed"] == 0
    assert summary["concurrent_writer_overlap"] == 0
    assert summary["background_process_overlap"] == 0
    assert summary["patches_upgraded"] == 0

    events = read_events(tmp_path)
    attributions = [e for e in events if e.event_type == "watcher_observation_attributed"]
    assert len(attributions) == 1
    attribution = attributions[0]
    assert attribution.payload["result"] == "unattributed"
    assert attribution.payload["capture_limitations"] == ["unbounded_mutation_window"]
    assert attribution.trace_id is None
    assert attribution.step_index is None
    assert attribution.generation_index == 0
    assert attribution.payload["observation_event_id"] == obs.event_id
    assert attribution.payload["path"] == "lonely.txt"
    assert "candidate_windows" not in attribution.payload
    assert "upgraded_trace_patch_id" not in attribution.payload

    # Watcher alone never mints a trace_patch_created. Even with rich blob
    # evidence, the reconciler refuses to fabricate attribution.
    patch_events = [e for e in events if e.event_type == "trace_patch_created"]
    assert patch_events == []


def test_background_process_overlap_records_limitation(tmp_path: Path) -> None:
    """Plan §Phase 5 edge fixture #4.

    A hook emitted a ``trace_patch_created`` for ``formatted.py``. Two
    watcher observations land inside the same firm window: one carries
    ``concurrent_activity=True`` (e.g., a formatter daemon also writing
    to the file), the other carries ``concurrent_activity=False``.

    The reconciler must produce one attribution row per observation:

    * The concurrent-activity observation is recorded as ``ambiguous``
      with ``capture_limitations=[background_process_overlap]``. Unlike
      the concurrent-writer-overlap case, this attribution KEEPS the
      writer identity (trace_id, step_index) because the window itself
      is unique — only the contributing process is uncertain.
    * The clean observation is recorded as ``attributed`` and triggers
      exactly one patch upgrade.

    The patch is upgraded once, not twice — the in-loop update of
    ``index["upgraded_patches"]`` is load-bearing for byte-stable
    replay.
    """
    _init_repo(tmp_path)
    target = tmp_path / "formatted.py"
    before_text = "x = 1\n"
    target.write_text(before_text)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed formatted"], cwd=tmp_path, check=True)
    before_blob = GitObjectID(hex=_hash_object(tmp_path, before_text))
    after_text = "x = 2\n"
    target.write_text(after_text)
    after_blob = GitObjectID(hex=_hash_object(tmp_path, after_text))

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

    _emit_hook_patch(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        file_path="formatted.py",
        trace_patch_id="tracepatch-sha256:fixture-tr1-step1-formatted",
        before_blob=before_blob,
        after_blob=after_blob,
        snapshot_before_id="snapshot-pre-fixture",
        snapshot_after_id="snapshot-post-fixture",
        authored_text="x = 2\n",
    )

    obs_ambiguous = append_filesystem_mutation_observed(
        tmp_path,
        path="formatted.py",
        observed_at_start="2026-04-26T10:00:02Z",
        observed_at_end="2026-04-26T10:00:04Z",
        before_blob_id=before_blob,
        after_blob_id=after_blob,
        concurrent_activity=True,
    )
    obs_clean = append_filesystem_mutation_observed(
        tmp_path,
        path="formatted.py",
        observed_at_start="2026-04-26T10:00:06Z",
        observed_at_end="2026-04-26T10:00:08Z",
        before_blob_id=before_blob,
        after_blob_id=after_blob,
        concurrent_activity=False,
    )

    summary = reconcile_watcher_observations(tmp_path)
    assert summary["background_process_overlap"] == 1
    assert summary["attributed"] == 1
    assert summary["patches_upgraded"] == 1, "patch upgraded exactly once"
    assert summary["concurrent_writer_overlap"] == 0
    assert summary["unbounded_mutation_window"] == 0

    events = read_events(tmp_path)
    attributions = [e for e in events if e.event_type == "watcher_observation_attributed"]
    assert len(attributions) == 2
    by_obs = {a.payload["observation_event_id"]: a for a in attributions}
    ambiguous = by_obs[obs_ambiguous.event_id]
    clean = by_obs[obs_clean.event_id]

    # Ambiguous: window is unique, but a background process contributed.
    # Keep the writer identity; the limitation flags the noise.
    assert ambiguous.payload["result"] == "ambiguous"
    assert ambiguous.payload["capture_limitations"] == ["background_process_overlap"]
    assert ambiguous.trace_id == "tr1"
    assert ambiguous.step_index == 1
    assert "candidate_windows" not in ambiguous.payload
    assert "upgraded_trace_patch_id" not in ambiguous.payload

    # Clean: triggers the patch upgrade.
    assert clean.payload["result"] == "attributed"
    assert clean.payload["capture_limitations"] == []
    assert clean.trace_id == "tr1"
    assert clean.step_index == 1
    assert (
        clean.payload["upgraded_trace_patch_id"] == "tracepatch-sha256:fixture-tr1-step1-formatted"
    )

    # Exactly one patch event carries watcher_backstop — the in-loop
    # upgrade dedup prevents a second upgrade for the same trace_patch_id.
    upgraded_patches = [
        e
        for e in events
        if e.event_type == "trace_patch_created" and "watcher_backstop" in e.capture_method
    ]
    assert len(upgraded_patches) == 1


def test_reconciler_is_idempotent(tmp_path: Path) -> None:
    """Plan §Phase 5 edge fixture #5 — three-pass idempotency.

    The reconciler is exercised across three passes:

    1. Initial: open window, hook patch, append two observations (one
       inside the window, one outside). Run reconciler. Snapshot.
    2. Replay: run reconciler again with no new events. Event log is
       byte-identical with the snapshot.
    3. Interleaved append: add a new in-window observation, a
       buffer-overflow-tagged observation, and a retroactively-
       timestamped observation. Run reconciler. Original attributions
       are byte-identical; only the three new observations get new
       attribution events.

    Pins: ``observation_event_id`` is the only idempotency key, and
    observation timestamps (not append order) drive containment.
    """
    _init_repo(tmp_path)
    target = tmp_path / "auth.py"
    target.write_text("def authorize():\n    return False\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True)
    before_blob = GitObjectID(hex=_hash_object(tmp_path, "before\n"))
    after_blob = GitObjectID(hex=_hash_object(tmp_path, "after\n"))

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
    _emit_hook_patch(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        file_path="auth.py",
        trace_patch_id="tracepatch-sha256:idempotent",
        before_blob=before_blob,
        after_blob=after_blob,
        snapshot_before_id="snapshot-pre",
        snapshot_after_id="snapshot-post",
        authored_text="    return True\n",
    )

    # Pass 1: in-window observation (matches the hook patch's path) +
    # out-of-window observation.
    append_filesystem_mutation_observed(
        tmp_path,
        path="auth.py",
        observed_at_start="2026-04-26T10:00:03Z",
        observed_at_end="2026-04-26T10:00:07Z",
        before_blob_id=before_blob,
        after_blob_id=after_blob,
    )
    append_filesystem_mutation_observed(
        tmp_path,
        path="other.txt",
        observed_at_start="2026-04-26T11:00:00Z",
        observed_at_end="2026-04-26T11:00:01Z",
        after_blob_id=after_blob,
    )
    summary_pass1 = reconcile_watcher_observations(tmp_path)
    events_pass1 = read_events(tmp_path)
    assert summary_pass1["observations_processed"] == 2
    assert summary_pass1["attributed"] == 1
    assert summary_pass1["unbounded_mutation_window"] == 1
    assert summary_pass1["patches_upgraded"] == 1

    pass1_event_ids = [e.event_id for e in events_pass1]
    pass1_event_sequences = [e.event_sequence for e in events_pass1]
    pass1_content_hashes = [e.content_hash for e in events_pass1]

    # Pass 2: replay with no new events.
    summary_pass2 = reconcile_watcher_observations(tmp_path)
    events_pass2 = read_events(tmp_path)
    assert summary_pass2["observations_processed"] == 0
    for bucket in (
        "attributed",
        "concurrent_writer_overlap",
        "unbounded_mutation_window",
        "background_process_overlap",
        "patches_upgraded",
    ):
        assert summary_pass2[bucket] == 0
    assert len(events_pass2) == len(events_pass1)
    assert [e.event_id for e in events_pass2] == pass1_event_ids
    assert [e.event_sequence for e in events_pass2] == pass1_event_sequences
    assert [e.content_hash for e in events_pass2] == pass1_content_hashes

    # Pass 3: interleaved appends.
    new_in_window = append_filesystem_mutation_observed(
        tmp_path,
        path="auth.py",
        observed_at_start="2026-04-26T10:00:04Z",
        observed_at_end="2026-04-26T10:00:06Z",
        after_blob_id=after_blob,
    )
    buffer_overflow_obs = append_filesystem_mutation_observed(
        tmp_path,
        path="auth.py",
        observed_at_start="2026-04-26T10:00:05Z",
        observed_at_end="2026-04-26T10:00:08Z",
        after_blob_id=after_blob,
        capture_limitations=["watcher_buffer_overflow"],
    )
    retroactive_obs = append_filesystem_mutation_observed(
        tmp_path,
        path="auth.py",
        observed_at_start="2026-04-25T12:00:00Z",
        observed_at_end="2026-04-25T12:00:01Z",
        after_blob_id=after_blob,
    )
    summary_pass3 = reconcile_watcher_observations(tmp_path)
    events_pass3 = read_events(tmp_path)
    assert summary_pass3["observations_processed"] == 3

    # Original pass-1 events are byte-identical. The newly-appended
    # observation events also live in the log unchanged.
    pass1_count = len(events_pass1)
    for prior, replayed in zip(events_pass1, events_pass3[:pass1_count]):
        assert prior.event_id == replayed.event_id
        assert prior.content_hash == replayed.content_hash
        assert prior.capture_method == replayed.capture_method

    # Exactly three new attribution events (one per new observation).
    pass3_attributions = [
        e for e in events_pass3 if e.event_type == "watcher_observation_attributed"
    ]
    pass1_attributions = [
        e for e in events_pass1 if e.event_type == "watcher_observation_attributed"
    ]
    assert len(pass3_attributions) - len(pass1_attributions) == 3

    by_obs = {a.payload["observation_event_id"]: a for a in pass3_attributions}
    new_in_window_attribution = by_obs[new_in_window.event_id]
    overflow_attribution = by_obs[buffer_overflow_obs.event_id]
    retro_attribution = by_obs[retroactive_obs.event_id]

    # New in-window observation attributes to tr1 step 1 (the hook patch
    # is already upgraded; second-attribution path emits no new patch).
    assert new_in_window_attribution.payload["result"] == "attributed"
    assert new_in_window_attribution.trace_id == "tr1"
    assert "upgraded_trace_patch_id" not in new_in_window_attribution.payload, (
        "patch upgrade should fire only once across the trace+step+path"
    )

    # Buffer-overflow-tagged observation: capture_limitations on the
    # observation payload is orthogonal to attribution result.
    assert overflow_attribution.payload["result"] == "attributed"
    assert overflow_attribution.trace_id == "tr1"

    # Retroactively-timestamped observation falls outside the wall-clock
    # window even though it was appended last — observation timestamps,
    # not append order, drive containment.
    assert retro_attribution.payload["result"] == "unattributed"
    assert retro_attribution.payload["capture_limitations"] == ["unbounded_mutation_window"]

    # The patch was upgraded exactly once across all three passes.
    upgraded_patches = [
        e
        for e in events_pass3
        if e.event_type == "trace_patch_created" and "watcher_backstop" in e.capture_method
    ]
    assert len(upgraded_patches) == 1


def test_mutation_outside_step_windows_records_unbounded_mutation_window(
    tmp_path: Path,
) -> None:
    """Plan §Phase 5 edge fixture #3.

    Three observations stress the interval-boundary semantics of
    ``_interval_within``:

    * After-close: ``obs.start > win.end`` → unattributed.
    * Crosses-open: ``obs.start < win.start`` (even when ``obs.end ==
      win.start``) → unattributed because the obs began before the window.
    * Wider-than-window: obs spans the window (``obs.start <= win.start``
      *and* ``obs.end > win.end``) → unattributed because containment is
      unidirectional — the window must contain the observation, not the
      reverse.

    Together these pin that ``_interval_within`` is strict containment,
    not bidirectional overlap. A sloppy implementation using
    ``not (a.end < b.start or a.start > b.end)`` would falsely attribute
    crosses-open and wider-than-window cases.
    """
    _init_repo(tmp_path)
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
        event_time="2026-04-26T10:00:05Z",
    )

    after_close = append_filesystem_mutation_observed(
        tmp_path,
        path="late.txt",
        observed_at_start="2026-04-26T10:00:05.000001Z",
        observed_at_end="2026-04-26T10:00:06Z",
        after_blob_id=after_blob,
    )
    crosses_open = append_filesystem_mutation_observed(
        tmp_path,
        path="early.txt",
        observed_at_start="2026-04-26T09:59:59Z",
        observed_at_end="2026-04-26T10:00:00Z",
        after_blob_id=after_blob,
    )
    wider_than_window = append_filesystem_mutation_observed(
        tmp_path,
        path="wide.txt",
        observed_at_start="2026-04-26T10:00:00Z",
        observed_at_end="2026-04-26T10:00:06Z",
        after_blob_id=after_blob,
    )

    summary = reconcile_watcher_observations(tmp_path)
    assert summary["unbounded_mutation_window"] == 3
    assert summary["attributed"] == 0
    assert summary["concurrent_writer_overlap"] == 0
    assert summary["patches_upgraded"] == 0

    events = read_events(tmp_path)
    attributions = [e for e in events if e.event_type == "watcher_observation_attributed"]
    assert len(attributions) == 3
    assert {a.payload["result"] for a in attributions} == {"unattributed"}
    for attribution in attributions:
        assert attribution.payload["capture_limitations"] == ["unbounded_mutation_window"]
        assert attribution.trace_id is None
        assert attribution.step_index is None

    observation_ids_seen = {a.payload["observation_event_id"] for a in attributions}
    assert observation_ids_seen == {
        after_close.event_id,
        crosses_open.event_id,
        wider_than_window.event_id,
    }

    patch_events = [e for e in events if e.event_type == "trace_patch_created"]
    assert patch_events == []


def test_mutation_overlapping_two_writers_records_concurrent_writer_overlap(
    tmp_path: Path,
) -> None:
    """Plan §Phase 5 edge fixture #2.

    Two writers' firm step windows both fully contain one mutation; the
    reconciler must record ``concurrent_writer_overlap`` and not pick a
    winner. A third "open but never closed" window must be excluded from
    the candidate set per ``_matching_windows`` requiring both endpoints.
    """
    _init_repo(tmp_path)
    after_blob = GitObjectID(hex=_hash_object(tmp_path, "x\n"))

    # Writer A: tr1 step 1, window 10:00:00 → 10:00:10
    open_step_window(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="tcA",
        capture_method=["hook_pretooluse"],
        event_time="2026-04-26T10:00:00Z",
    )
    close_step_window_with_snapshot(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="tcA",
        capture_method=["hook_posttooluse"],
        event_time="2026-04-26T10:00:10Z",
    )

    # Writer B: tr2 step 1, window 10:00:02 → 10:00:09
    open_step_window(
        tmp_path,
        trace_id="tr2",
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="tcB",
        capture_method=["hook_pretooluse"],
        event_time="2026-04-26T10:00:02Z",
    )
    close_step_window_with_snapshot(
        tmp_path,
        trace_id="tr2",
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="tcB",
        capture_method=["hook_posttooluse"],
        event_time="2026-04-26T10:00:09Z",
    )

    # Writer C: opened but never closed. Must NOT count as a candidate.
    open_step_window(
        tmp_path,
        trace_id="tr3",
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="tcC",
        capture_method=["hook_pretooluse"],
        event_time="2026-04-26T10:00:00Z",
    )

    # Observation 10:00:03 → 10:00:08, fully inside both A and B (and C if
    # it had a close, but C never closed).
    append_filesystem_mutation_observed(
        tmp_path,
        path="shared.py",
        observed_at_start="2026-04-26T10:00:03Z",
        observed_at_end="2026-04-26T10:00:08Z",
        after_blob_id=after_blob,
    )

    summary = reconcile_watcher_observations(tmp_path)
    assert summary["concurrent_writer_overlap"] == 1
    assert summary["attributed"] == 0
    assert summary["unbounded_mutation_window"] == 0
    assert summary["patches_upgraded"] == 0

    events = read_events(tmp_path)
    attributions = [e for e in events if e.event_type == "watcher_observation_attributed"]
    assert len(attributions) == 1
    attribution = attributions[0]
    assert attribution.payload["result"] == "ambiguous"
    assert attribution.payload["capture_limitations"] == ["concurrent_writer_overlap"]
    assert attribution.trace_id is None
    assert attribution.step_index is None

    # Exactly two candidates — open-but-unclosed C is excluded.
    candidates = attribution.payload["candidate_windows"]
    assert len(candidates) == 2
    candidate_keys = {(c["trace_id"], c["generation_index"], c["step_index"]) for c in candidates}
    assert candidate_keys == {("tr1", 0, 1), ("tr2", 0, 1)}

    patch_events = [e for e in events if e.event_type == "trace_patch_created"]
    assert patch_events == []
