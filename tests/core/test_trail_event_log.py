"""Trace Trails event-log core behavior."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from opentraces.core.trails import (
    EVENT_LOG_REF,
    TrailEventDraft,
    append_event_batch,
    event_log_status,
    read_events,
)
import opentraces.core.trails.event_log as event_log


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _init_sha256_repo(repo: Path) -> None:
    proc = subprocess.run(
        ["git", "init", "-q", "--object-format=sha256"],
        cwd=repo,
        capture_output=True,
    )
    if proc.returncode != 0:
        pytest.skip("git does not support sha256 object-format repositories")
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def test_event_envelope_rejects_bare_sha_and_empty_capture_method() -> None:
    with pytest.raises(ValidationError, match="capture_method"):
        TrailEventDraft(
            event_type="trace_snapshot_created",
            trace_id="tr1",
            step_index=1,
            capture_method=[],
            payload={"snapshot_id": "s1"},
        )

    with pytest.raises(ValidationError, match="commit_sha"):
        TrailEventDraft(
            event_type="git_anchor_created",
            trace_id="tr1",
            step_index=1,
            capture_method=["manual_attach"],
            payload={
                "git_anchor_id": "ga1",
                "trace_patch_id": "tp1",
                "commit_sha": "a" * 40,
            },
        )


def test_event_log_batches_are_linear_fast_forward_commits(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="trace_snapshot_created",
                trace_id="tr1",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={"snapshot_id": "s1", "limitations": []},
            ),
        ],
        writer="test-fixture",
    )
    first_head = _git(tmp_path, "rev-parse", "refs/opentraces/local/events/v1")
    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr1",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={"trace_patch_id": "tp1", "limitations": []},
            ),
        ],
        writer="test-fixture",
    )

    lines = _git(
        tmp_path,
        "rev-list",
        "--reverse",
        "--parents",
        "refs/opentraces/local/events/v1",
    ).splitlines()
    assert len(lines) == 2
    assert lines[1].split()[1:] == [first_head]

    status = event_log_status(tmp_path)
    assert status["state"] == "ok"
    assert status["batch_count"] == 2
    assert status["event_count"] == 2
    assert status["batch_parents_linear"] is True
    assert status["content_hashes_valid"] is True

    events = read_events(tmp_path)
    assert [event.event_sequence for event in events] == [1, 2]
    assert events[1].previous_event_id == events[0].event_id


def test_append_retries_when_event_log_ref_moves(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    original_update = event_log._update_event_log_ref
    injected = False

    def race_once(cwd: Path, commit_sha: str, expected_head: str | None) -> bool:
        nonlocal injected
        if not injected:
            injected = True
            append_event_batch(
                cwd,
                [
                    TrailEventDraft(
                        event_type="trace_snapshot_created",
                        trace_id="racer",
                        step_index=1,
                        capture_method=["hook_posttooluse"],
                        payload={"snapshot_id": "racer-snapshot", "limitations": []},
                    ),
                ],
                writer="racer",
            )
        return original_update(cwd, commit_sha, expected_head)

    monkeypatch.setattr(event_log, "_update_event_log_ref", race_once)

    emitted = append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="main",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={"trace_patch_id": "main-patch", "limitations": []},
            ),
        ],
        writer="main",
    )

    events = read_events(tmp_path)
    assert [event.trace_id for event in events] == ["racer", "main"]
    assert [event.event_sequence for event in events] == [1, 2]
    assert emitted[0].event_sequence == 2
    assert emitted[0].previous_event_id == events[0].event_id
    assert event_log_status(tmp_path)["state"] == "ok"


def test_append_creates_event_log_ref_in_sha256_repo(tmp_path: Path) -> None:
    _init_sha256_repo(tmp_path)

    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="trace_snapshot_created",
                trace_id="sha256-repo",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={"snapshot_id": "sha256-snapshot", "limitations": []},
            ),
        ],
        writer="test-fixture",
    )

    head = _git(tmp_path, "rev-parse", EVENT_LOG_REF)
    assert len(head) == 64
    assert event_log_status(tmp_path)["state"] == "ok"


def _append_simple(repo: Path, trace_id: str, snapshot_id: str) -> None:
    append_event_batch(
        repo,
        [TrailEventDraft(
            event_type="trace_snapshot_created", trace_id=trace_id, step_index=1,
            capture_method=["hook_posttooluse"],
            payload={"snapshot_id": snapshot_id, "limitations": []},
        )],
        writer="test-fixture",
    )


def test_incremental_verify_equals_full_verify(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _append_simple(tmp_path, "tA", "s1")
    _append_simple(tmp_path, "tA", "s2")
    # First verify persists a clean watermark.
    first = event_log_status(tmp_path)
    assert first["state"] == "ok"
    assert event_log._load_verify_watermark(tmp_path) is not None

    # Append more; the watermark head is now an ancestor of HEAD.
    event_log.invalidate_read_events_cache(tmp_path)
    _append_simple(tmp_path, "tB", "s3")
    _append_simple(tmp_path, "tB", "s4")
    event_log.invalidate_read_events_cache(tmp_path)

    wm = event_log._load_verify_watermark(tmp_path)
    head = event_log._ref_head(tmp_path)
    assert wm is not None and wm["head"] != head
    # The incremental path must produce a clean result over only the delta.
    inc = event_log._try_incremental_verify(tmp_path, head, wm)
    assert inc is not None, "incremental verify should engage for a descendant head"

    # Full verify from scratch (no watermark, cold caches) must match exactly.
    wm_path = event_log._verify_watermark_path(tmp_path)
    if wm_path and wm_path.exists():
        wm_path.unlink()
    event_log.invalidate_read_events_cache(tmp_path)
    full = event_log.verify_event_log(tmp_path)

    assert inc == full
    assert full["event_count"] == 4
    assert full["content_hashes_valid"] and full["event_chain_valid"]
    assert full["errors"] == []


def test_incremental_verify_detects_corruption_in_new_events(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _append_simple(tmp_path, "tA", "s1")
    # Clean baseline establishes the watermark.
    assert event_log_status(tmp_path)["state"] == "ok"
    base_head = _git(tmp_path, "rev-parse", EVENT_LOG_REF)

    # Append a second batch, then tamper it while preserving base_head as the
    # parent — so the watermark head IS an ancestor and the incremental path
    # engages on the new (corrupt) event.
    event_log.invalidate_read_events_cache(tmp_path)
    _append_simple(tmp_path, "tB", "s2")
    head = _git(tmp_path, "rev-parse", EVENT_LOG_REF)
    raw = _git(tmp_path, "show", f"{head}:events/000000000002.json")
    tampered = json.loads(raw)
    tampered["payload"]["snapshot_id"] = "tampered"
    tampered_blob = subprocess.check_output(
        ["git", "hash-object", "-w", "--stdin"], cwd=tmp_path,
        input=json.dumps(tampered, sort_keys=True, indent=2) + "\n", text=True,
    ).strip()
    with tempfile.TemporaryDirectory() as td:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(Path(td) / "index")
        subprocess.run(["git", "read-tree", head], cwd=tmp_path, env=env, check=True)
        subprocess.run(
            ["git", "update-index", "--cacheinfo",
             f"100644,{tampered_blob},events/000000000002.json"],
            cwd=tmp_path, env=env, check=True,
        )
        tree = subprocess.check_output(
            ["git", "write-tree"], cwd=tmp_path, env=env, text=True
        ).strip()
    new_head = subprocess.check_output(
        ["git", "commit-tree", tree, "-p", base_head, "-m", "tampered"],
        cwd=tmp_path, text=True,
    ).strip()
    subprocess.run(["git", "update-ref", EVENT_LOG_REF, new_head, head],
                   cwd=tmp_path, check=True)
    event_log.invalidate_read_events_cache(tmp_path)

    status = event_log_status(tmp_path)
    assert status["state"] == "invalid"
    assert status["content_hashes_valid"] is False
    assert any("content_hash mismatch" in e for e in status["errors"])


def test_verify_detects_tampered_event_content_hash(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="trace_snapshot_created",
                trace_id="tr1",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={"snapshot_id": "s1", "limitations": []},
            ),
        ],
        writer="test-fixture",
    )
    head = _git(tmp_path, "rev-parse", EVENT_LOG_REF)
    raw = _git(tmp_path, "show", f"{head}:events/000000000001.json")
    tampered = json.loads(raw)
    tampered["payload"]["snapshot_id"] = "tampered"
    tampered_blob = subprocess.check_output(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=tmp_path,
        input=json.dumps(tampered, sort_keys=True, indent=2) + "\n",
        text=True,
    ).strip()

    with tempfile.TemporaryDirectory() as td:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(Path(td) / "index")
        subprocess.run(["git", "read-tree", head], cwd=tmp_path, env=env, check=True)
        subprocess.run(
            [
                "git",
                "update-index",
                "--cacheinfo",
                f"100644,{tampered_blob},events/000000000001.json",
            ],
            cwd=tmp_path,
            env=env,
            check=True,
        )
        tree = subprocess.check_output(["git", "write-tree"], cwd=tmp_path, env=env, text=True).strip()
    new_head = subprocess.check_output(
        ["git", "commit-tree", tree, "-m", "tampered trail batch"],
        cwd=tmp_path,
        text=True,
    ).strip()
    subprocess.run(["git", "update-ref", EVENT_LOG_REF, new_head, head], cwd=tmp_path, check=True)

    status = event_log_status(tmp_path)
    assert status["state"] == "invalid"
    assert status["content_hashes_valid"] is False
    assert any("content_hash mismatch" in error for error in status["errors"])


def test_phase1_gc_safety_for_event_log_blob_edges(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    dangling_blob = subprocess.check_output(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=tmp_path,
        input="agent-authored object retained only by trail event log\n",
        text=True,
    ).strip()

    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-gc",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": "tracepatch-sha256:gc",
                    "file_path": "generated.txt",
                    "affected_range": {"start_line": 1, "end_line": 1},
                    "raw_authored_hash": "sha256:raw",
                    "git_clean_hash": "sha256:clean",
                    "after_blob_id": {"algo": "sha1", "hex": dangling_blob},
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    subprocess.run(["git", "reflog", "expire", "--expire=now", "--all"], cwd=tmp_path, check=True)
    subprocess.run(["git", "gc", "--prune=now", "--aggressive"], cwd=tmp_path, check=True)

    subprocess.run(["git", "cat-file", "-e", dangling_blob], cwd=tmp_path, check=True)
    tree_entries = _git(tmp_path, "ls-tree", "-r", "--name-only", EVENT_LOG_REF)
    assert f"objects/blobs/{dangling_blob}" in tree_entries.splitlines()


def test_incremental_verify_bails_on_duplicate_suffix_sequence(tmp_path: Path) -> None:
    """#65 codex P2 sentinel: a suffix event whose sequence is <= the verify
    watermark (bad restore / external writer rewinding the counter) must make
    the incremental fast-path bail (return None) so the full verify runs and
    reports the corruption — never silently bless the head."""
    _init_repo(tmp_path)
    _append_simple(tmp_path, "tA", "s1")
    _append_simple(tmp_path, "tA", "s2")
    assert event_log_status(tmp_path)["state"] == "ok"
    wm = event_log._load_verify_watermark(tmp_path)
    assert wm is not None and int(wm["last_event_sequence"]) == 2

    # Append a suffix batch, then corrupt its ledger shape: rewrite the
    # watermark to claim a HIGHER sequence so the (valid) suffix events land
    # at-or-below it — the duplicate/rewind shape from the watermark's view.
    event_log.invalidate_read_events_cache(tmp_path)
    _append_simple(tmp_path, "tB", "s3")
    event_log.invalidate_read_events_cache(tmp_path)
    head = event_log._ref_head(tmp_path)
    forged = dict(wm)
    forged["last_event_sequence"] = 3  # claims seq 3 already verified
    inc = event_log._try_incremental_verify(tmp_path, head, forged)
    assert inc is None, (
        "suffix event at/below the watermark sequence must bail to full verify"
    )
