"""authored_text drop: precondition measurements (epic #169 tier D, TASK 2).

The redundant ``authored_text`` field is emitted per patch at
``core/trails/snapshots.py``. It may be dropped ONLY after FOUR preconditions
hold, each re-observed here on scratch fixtures (no live bucket / ref touched):

  P1 schema-optional / absent-tolerant  — readers do not crash when it is absent.
  P2 bucket_digest-excluded             — dropping leaves bucket_digest byte-identical.
  P3 blob reconstruction                — every reader can rebuild the exact text
                                          from the pinned content blobs.
  P4 retained in the portable bucket    — the source content survives offline.

These tests pin the EVIDENCE that drives the verdict. Where a precondition does
NOT hold the test asserts the blocking fact directly, so a future drop attempt
trips here first. See the tier-D report for the verdict.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    read_events,
    reconstruct_authored_text,
)
from opentraces.core.trails.event_log import EVENT_LOG_REF
from opentraces.core.trails.models import sha256_text
from opentraces.core.trails.snapshots import _patch_drafts_for_step, write_worktree_tree


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _emit_patch_for(repo: Path, before: str, after: str) -> dict:
    """Run the production patch emitter and return the trace_patch_created payload."""
    (repo / "mod.py").write_text(before)
    before_tree = write_worktree_tree(repo)
    (repo / "mod.py").write_text(after)
    after_tree = write_worktree_tree(repo)

    drafts = _patch_drafts_for_step(
        repo,
        trace_id="t-authored",
        generation_index=0,
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="call-1",
        before_snapshot_id="snap-before",
        after_snapshot_id="snap-after",
        before_tree_id=before_tree,
        after_tree_id=after_tree,
        capture_method=["hook_pretooluse", "hook_posttooluse"],
        limitations=[],
    )
    assert len(drafts) == 1, "fixture is one file / one hunk"
    return drafts[0].payload


# --------------------------------------------------------------------------- #
# P3 — blob reconstruction is exact, including the non-contiguous-add case the
# naive "after_blob[start:end]" extraction gets WRONG.
# --------------------------------------------------------------------------- #

def test_p3_reconstruct_authored_text_is_exact_contiguous(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    payload = _emit_patch_for(repo, "a = 1\n", "a = 1\nb = 2\nc = 3\n")

    rebuilt = reconstruct_authored_text(
        repo,
        after_blob_id=payload["after_blob_id"],
        before_blob_id=payload["before_blob_id"],
        affected_range=payload["affected_range"],
    )
    assert rebuilt == payload["authored_text"]
    assert sha256_text(rebuilt) == payload["raw_authored_hash"]


def test_p3_reconstruct_is_exact_for_noncontiguous_adds(tmp_path: Path) -> None:
    """Added lines interleaved with context: naive after-blob slicing is lossy;
    the re-diff reconstruction reproduces authored_text exactly."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    before = "a\nb\nc\n"
    after = "a\nNEW1\nb\nNEW2\nc\n"
    payload = _emit_patch_for(repo, before, after)

    authored = payload["authored_text"]
    rng = payload["affected_range"]
    # The emitted field concatenates ONLY the '+' lines (non-contiguous).
    assert authored == "NEW1\nNEW2"

    # Naive extraction of after_blob[start:end] is WRONG (includes context 'b').
    after_lines = after.splitlines()
    naive = "\n".join(after_lines[rng["start_line"] - 1 : rng["end_line"]])
    assert naive == "NEW1\nb\nNEW2"
    assert naive != authored, "demonstrates why blob slicing is insufficient"

    # The faithful reconstruction (re-diff of pinned blobs) reproduces it exactly.
    rebuilt = reconstruct_authored_text(
        repo,
        after_blob_id=payload["after_blob_id"],
        before_blob_id=payload["before_blob_id"],
        affected_range=rng,
    )
    assert rebuilt == authored
    assert sha256_text(rebuilt) == payload["raw_authored_hash"]


def test_p3_reconstruct_for_new_file_no_before_blob(tmp_path: Path) -> None:
    """A created file (before_blob_id is None) still reconstructs from after_blob."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    # Before tree has only keep.py; mod.py is created in the after tree, so its
    # patch carries before_blob_id=None (genuine creation).
    (repo / "keep.py").write_text("k = 0\n")
    before_tree = write_worktree_tree(repo)
    (repo / "mod.py").write_text("x = 1\ny = 2\n")
    after_tree = write_worktree_tree(repo)

    drafts = _patch_drafts_for_step(
        repo,
        trace_id="t-authored",
        generation_index=0,
        step_index=1,
        agent_step_id="step_1",
        tool_call_id="call-1",
        before_snapshot_id="snap-before",
        after_snapshot_id="snap-after",
        before_tree_id=before_tree,
        after_tree_id=after_tree,
        capture_method=["hook_pretooluse", "hook_posttooluse"],
        limitations=[],
    )
    payload = next(d.payload for d in drafts if d.payload["file_path"] == "mod.py")
    assert payload["before_blob_id"] is None

    rebuilt = reconstruct_authored_text(
        repo,
        after_blob_id=payload["after_blob_id"],
        before_blob_id=None,
        affected_range=payload["affected_range"],
    )
    assert rebuilt == payload["authored_text"]


# --------------------------------------------------------------------------- #
# P4 — the after_blob is pinned in the canonical event log, so the source
# content survives in the portable bucket (offline, no harness/network).
# --------------------------------------------------------------------------- #

def test_p4_after_blob_is_pinned_in_event_log(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    payload = _emit_patch_for(repo, "a = 1\n", "a = 1\nb = 2\n")
    after_blob_hex = payload["after_blob_id"]["hex"]

    # Append the patch event, then GC everything unreachable from refs. If the
    # blob were not pinned by the event log it would be collected and the
    # reconstruction would fail offline.
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="t-authored",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload=payload,
            )
        ],
        writer="test",
    )
    # Drop the worktree/index references; only refs (incl. the event log) remain.
    subprocess.run(["git", "reset", "-q", "--hard"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "gc.reflogExpire=now", "-c", "gc.reflogExpireUnreachable=now",
         "gc", "--prune=now", "-q"],
        cwd=repo,
        check=True,
    )

    # The event log ref keeps the blob reachable.
    assert _git(repo, "rev-parse", EVENT_LOG_REF)
    assert subprocess.run(
        ["git", "cat-file", "-e", after_blob_hex], cwd=repo
    ).returncode == 0, "after_blob must survive GC via the event log pin"

    # And reconstruction still works post-GC (offline, blob-only).
    events = read_events(repo, verify=False)
    patch_payload = next(
        e.payload for e in events if e.event_type == "trace_patch_created"
    )
    rebuilt = reconstruct_authored_text(
        repo,
        after_blob_id=patch_payload["after_blob_id"],
        before_blob_id=patch_payload.get("before_blob_id"),
        affected_range=patch_payload["affected_range"],
    )
    assert rebuilt == payload["authored_text"]


# --------------------------------------------------------------------------- #
# P1 — readers tolerate absence (no crash). The survival reader degrades to
# "missing_authored_text"; reconstruction recovers the text the field held.
# --------------------------------------------------------------------------- #

def test_p1_survival_reader_tolerates_absent_authored_text(tmp_path: Path) -> None:
    from opentraces.core.trails.sync import _authored_lines

    # Absent / empty / wrong-type all degrade gracefully to [] (no crash).
    assert _authored_lines({}) == []
    assert _authored_lines({"authored_text": ""}) == []
    assert _authored_lines({"authored_text": None}) == []
    assert _authored_lines({"authored_text": 123}) == []
    assert _authored_lines({"authored_text": "x\ny"}) == ["x", "y"]


# --------------------------------------------------------------------------- #
# P2 — bucket_digest is NOT invariant under dropping authored_text. The events
# mirror head (event_log_head) is content-derived from the full event payloads
# and flows into bucket_digest via the trail_events digest. Dropping the field
# changes the canonical event ref, hence the digest. This BLOCKS a byte-
# identical drop. Re-observed end-to-end on two isolated scratch buckets.
# --------------------------------------------------------------------------- #

def _bucket_digest_for_variant(repo: Path, *, with_authored: bool) -> str:
    payload = {
        "trace_patch_id": "tracepatch-sha256:deadbeefdeadbeef",
        "file_path": "a.py",
        "affected_range": {"start_line": 1, "end_line": 2},
        "raw_authored_hash": sha256_text("X = 1\nY = 2\n"),
        "git_clean_hash": sha256_text("X = 1\nY = 2"),
        "limitations": [],
    }
    if with_authored:
        payload["authored_text"] = "X = 1\nY = 2\n"

    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="t-digest",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload=payload,
            )
        ],
        writer="test",
    )

    from opentraces.core.bucket_events import sync_events_mirror
    from opentraces.core.bucket_store import bucket_manifest

    sync_events_mirror(repo, repo_id="proj-digest")
    manifest = bucket_manifest(write=False, heal=False)
    return manifest["bucket_digest"]


def test_p2_dropping_authored_text_changes_bucket_digest(tmp_path, monkeypatch) -> None:
    """The drop is blocked: bucket_digest differs with vs without the field."""
    import opentraces.core.paths as paths_mod

    def digest_in_fresh_bucket(name: str, *, with_authored: bool) -> str:
        ot_dir = tmp_path / name / ".opentraces"
        ot_dir.mkdir(parents=True)
        monkeypatch.setattr(paths_mod, "OPENTRACES_DIR", ot_dir)
        repo = tmp_path / name / "repo"
        _init_repo(repo)
        return _bucket_digest_for_variant(repo, with_authored=with_authored)

    digest_with = digest_in_fresh_bucket("with", with_authored=True)
    digest_without = digest_in_fresh_bucket("without", with_authored=False)

    # If these were equal, authored_text would be digest-excluded and the drop
    # would be byte-safe. They are NOT — so P2 is unproven and the drop is
    # blocked. This assertion pins the blocking fact.
    assert digest_with != digest_without, (
        "bucket_digest unexpectedly invariant under authored_text drop — "
        "re-evaluate P2; the drop may now be safe"
    )
