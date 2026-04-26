"""Phase 5 ``trail attach`` — manual recovery from hook failure."""
from __future__ import annotations

import subprocess
from pathlib import Path

from opentraces.core.trails import (
    GitObjectID,
    TrailEventDraft,
    append_event_batch,
    explain_trace_step,
    read_events,
)
from opentraces.core.trails.attach import attach_trace_to_commit


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
    authored_text: str,
    affected_range: dict[str, int],
) -> None:
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
                    "snapshot_before_id": "snapshot-pre-fixture",
                    "snapshot_after_id": "snapshot-post-fixture",
                    "file_path": file_path,
                    "affected_range": affected_range,
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


def _commit_authored_change(
    repo: Path, *, file_path: str, content: str, message: str
) -> str:
    target = repo / file_path
    target.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def test_hook_failure_plus_manual_attach_does_not_rewrite_source_events(
    tmp_path: Path,
) -> None:
    """Plan §Phase 5 edge fixture #6 — manual attach.

    The hook captured the trace step (window + patch events landed) but
    the post-commit correlator never ran (hook failure). The user runs
    ``trail attach --trace tr1 --commit <sha>`` to retroactively connect
    the trace patch to the Git commit.

    Contract:

    * ``attach_trace_to_commit`` appends new TrailEvents only — original
      hook events are byte-identical after attach (capture_method,
      content_hash, event_id all unchanged).
    * The new anchor event carries ``capture_method=["manual_attach"]``
      so downstream consumers can distinguish manually-attached anchors
      from automatically-correlated ones.
    * ``trail explain`` resolves through the new anchor as if it had
      been emitted by the post-commit correlator — the substrate is
      transparent to whoever produced the evidence.
    * Re-running attach is idempotent: no duplicate anchor or search
      events are appended.
    """
    _init_repo(tmp_path)

    target_path = "auth.py"
    before_text = "def authorize():\n    return False\n"
    (tmp_path / target_path).write_text(before_text)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed auth"], cwd=tmp_path, check=True
    )
    before_blob = GitObjectID(hex=_hash_object(tmp_path, before_text))

    # Emit the hook patch BEFORE the commit lands. This mimics the
    # in-session state where the hook captured the proposed change but
    # the post-commit correlator never ran.
    after_text = "def authorize():\n    return True\n"
    after_blob = GitObjectID(hex=_hash_object(tmp_path, after_text))
    _emit_hook_patch(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        file_path=target_path,
        trace_patch_id="tracepatch-sha256:fixture-tr1-step1-attach",
        before_blob=before_blob,
        after_blob=after_blob,
        authored_text="    return True\n",
        affected_range={"start_line": 2, "end_line": 2},
    )

    # Capture the pre-attach event log.
    events_before = read_events(tmp_path)
    assert any(e.event_type == "trace_patch_created" for e in events_before)
    assert not any(e.event_type == "git_anchor_created" for e in events_before)

    commit_sha = _commit_authored_change(
        tmp_path,
        file_path=target_path,
        content=after_text,
        message="apply authorize true",
    )

    created = attach_trace_to_commit(tmp_path, "tr1", commit_sha)
    assert len(created) == 1
    assert created[0]["evidence_tier"] == "exact_range_hash"
    assert created[0]["evidence_firmness"] == "firm"
    assert created[0]["source"] == "manual-attach"

    events_after = read_events(tmp_path)

    # Original events are byte-identical — append-only honesty.
    assert len(events_after) > len(events_before)
    for prior, replayed in zip(events_before, events_after):
        assert prior.event_id == replayed.event_id
        assert prior.event_sequence == replayed.event_sequence
        assert prior.content_hash == replayed.content_hash
        assert prior.capture_method == replayed.capture_method

    # New events are tagged with manual_attach.
    new_events = events_after[len(events_before):]
    new_event_types = [e.event_type for e in new_events]
    assert "git_anchor_search_completed" in new_event_types
    assert "git_anchor_created" in new_event_types
    for event in new_events:
        assert event.capture_method == ["manual_attach"]

    # Search event records the algorithms attempted and the result.
    search_event = next(
        e for e in new_events if e.event_type == "git_anchor_search_completed"
    )
    assert search_event.payload["result"] == "anchored"
    assert search_event.payload["search_head"]["hex"] == commit_sha
    assert search_event.payload["trace_patch_id"] == (
        "tracepatch-sha256:fixture-tr1-step1-attach"
    )

    # trail explain resolves through the manually-attached anchor.
    explanation = explain_trace_step(tmp_path, "tr1", 1)
    assert explanation["relation"] == "anchored_in_git"
    assert explanation["evidence_tier"] == "exact_range_hash"
    assert explanation["git_anchor_id"] == created[0]["git_anchor_id"]

    # Idempotent: re-running attach produces no new events.
    second_run = attach_trace_to_commit(tmp_path, "tr1", commit_sha)
    assert second_run == []
    events_third_read = read_events(tmp_path)
    assert len(events_third_read) == len(events_after)


def test_attach_with_no_matching_commit_emits_search_unknown(tmp_path: Path) -> None:
    """When the commit doesn't contain the patch, attach emits an honest
    ``git_anchor_search_completed result=unknown`` event and creates no
    fake anchor — the orphan honesty contract holds across capture
    methods.
    """
    _init_repo(tmp_path)
    before_blob = GitObjectID(hex=_hash_object(tmp_path, "old\n"))
    after_blob = GitObjectID(hex=_hash_object(tmp_path, "new\n"))
    _emit_hook_patch(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        file_path="phantom.py",
        trace_patch_id="tracepatch-sha256:fixture-phantom",
        before_blob=before_blob,
        after_blob=after_blob,
        authored_text="never_appears_in_any_commit\n",
        affected_range={"start_line": 1, "end_line": 1},
    )
    head_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()

    created = attach_trace_to_commit(tmp_path, "tr1", head_sha)
    assert created == []

    events = read_events(tmp_path)
    search_events = [
        e for e in events if e.event_type == "git_anchor_search_completed"
    ]
    assert len(search_events) == 1
    assert search_events[0].payload["result"] == "unknown"
    assert search_events[0].capture_method == ["manual_attach"]
    assert not any(e.event_type == "git_anchor_created" for e in events)


def test_attach_only_searches_patches_for_specified_trace(tmp_path: Path) -> None:
    """``trail attach --trace tr1`` must only consider tr1's patches.

    A second trace's patches must NOT receive search events from this
    attach call. Source trace records and other traces' TrailEvents
    remain untouched — the contract is per-trace, not global.
    """
    _init_repo(tmp_path)
    target_path = "auth.py"
    before_text = "def authorize():\n    return False\n"
    (tmp_path / target_path).write_text(before_text)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True
    )
    before_blob = GitObjectID(hex=_hash_object(tmp_path, before_text))
    after_text = "def authorize():\n    return True\n"
    after_blob = GitObjectID(hex=_hash_object(tmp_path, after_text))

    _emit_hook_patch(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        file_path=target_path,
        trace_patch_id="tracepatch-sha256:fixture-tr1",
        before_blob=before_blob,
        after_blob=after_blob,
        authored_text="    return True\n",
        affected_range={"start_line": 2, "end_line": 2},
    )
    _emit_hook_patch(
        tmp_path,
        trace_id="tr2",
        step_index=1,
        file_path=target_path,
        trace_patch_id="tracepatch-sha256:fixture-tr2",
        before_blob=before_blob,
        after_blob=after_blob,
        authored_text="    return True\n",
        affected_range={"start_line": 2, "end_line": 2},
    )
    commit_sha = _commit_authored_change(
        tmp_path,
        file_path=target_path,
        content=after_text,
        message="apply true",
    )

    attach_trace_to_commit(tmp_path, "tr1", commit_sha)
    events = read_events(tmp_path)
    new_search_events = [
        e for e in events if e.event_type == "git_anchor_search_completed"
    ]
    # Exactly one search — tr1's patch — even though tr2 has an
    # identical-content patch in the log.
    assert len(new_search_events) == 1
    assert new_search_events[0].trace_id == "tr1"
    new_anchor_events = [
        e for e in events if e.event_type == "git_anchor_created"
    ]
    assert len(new_anchor_events) == 1
    assert new_anchor_events[0].trace_id == "tr1"
