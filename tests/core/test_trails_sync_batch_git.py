"""Cluster G — P3: ``BatchSyncContext`` amortizes git ops across syncs.

When ``sync_patch`` is called many times in a single batch the git ops
dominate the wall-clock time. ``BatchSyncContext`` resolves "is X
reachable from HEAD" via one ``git rev-list HEAD`` instead of N
``git merge-base --is-ancestor`` invocations, and routes blob reads
through a long-lived ``git cat-file --batch`` pipe instead of
spawning ``git show`` per call.

These tests pin the contract by counting subprocess invocations.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from opentraces.core.trails import (
    BatchSyncContext,
    TrailEventDraft,
    append_event_batch,
    read_events,
    sync_patch,
)
from opentraces.core.trails.models import sha256_text


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True
    )


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


def _seed_patch_anchor(
    repo: Path,
    *,
    trace_id: str,
    patch_id: str,
    anchor_commit: str,
    file_path: str,
    text: str,
) -> None:
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=trace_id,
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": f"tracepatch-sha256:{patch_id}",
                    "file_path": file_path,
                    "affected_range": {"start_line": 1, "end_line": 2},
                    "authored_text": text,
                    "raw_authored_hash": sha256_text(text),
                    "git_clean_hash": sha256_text(text.strip()),
                    "limitations": [],
                },
            ),
            TrailEventDraft(
                event_type="git_anchor_created",
                trace_id=trace_id,
                step_index=1,
                capture_method=["manual_attach"],
                payload={
                    "git_anchor_id": f"gitanchor-sha256:{patch_id}",
                    "trace_patch_id": f"tracepatch-sha256:{patch_id}",
                    "commit_id": {"algo": "sha1", "hex": anchor_commit},
                    "path": file_path,
                    "range": {"start_line": 1, "end_line": 2},
                    "relation": "anchored_in_git",
                    "evidence_tier": "exact_range_hash",
                    "evidence_firmness": "firm",
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )


def test_batch_ctx_amortises_ancestry_probes(tmp_path: Path) -> None:
    """With a BatchSyncContext, ``merge-base --is-ancestor`` is replaced
    by one ``git rev-list HEAD`` resolved in memory. We exercise the
    HEAD-against-orphan-anchor path so the ancestry probe actually
    fires (the descendant walk short-circuits the probe when the
    anchor is provably reachable).

    Setup: anchors point at synthetic SHAs that are NOT in HEAD's
    ancestry. ``_compute_survival`` reaches the ``observed_ref == "HEAD"``
    branch and consults the batch context's cached ancestry set.
    """
    _init_repo(tmp_path)
    (tmp_path / "seed.py").write_text("seed\n")
    _commit(tmp_path, "seed")

    patch_ids: list[str] = []
    for index in range(10):
        # Synthetic patch with an anchor that points at an unreachable
        # SHA (16 chars index + 24 zeros = 40-char hex). The repo's HEAD
        # never includes this commit so ``is_ancestor_of_head`` resolves
        # against the cached ancestry set.
        patch_id_hex = f"{index:016x}{'0' * 48}"[:64]
        anchor_sha = f"d{index:039x}"[:40]
        append_event_batch(
            tmp_path,
            [
                TrailEventDraft(
                    event_type="trace_patch_created",
                    trace_id=f"t-{index:02d}",
                    step_index=1,
                    capture_method=["hook_posttooluse"],
                    payload={
                        "trace_patch_id": f"tracepatch-sha256:{patch_id_hex}",
                        "file_path": "seed.py",
                        "affected_range": {"start_line": 1, "end_line": 1},
                        "authored_text": "seed\n",
                        "raw_authored_hash": sha256_text("seed\n"),
                        "git_clean_hash": sha256_text("seed"),
                        "limitations": [],
                    },
                ),
                TrailEventDraft(
                    event_type="git_anchor_created",
                    trace_id=f"t-{index:02d}",
                    step_index=1,
                    capture_method=["manual_attach"],
                    payload={
                        "git_anchor_id": f"gitanchor-sha256:{patch_id_hex}",
                        "trace_patch_id": f"tracepatch-sha256:{patch_id_hex}",
                        "commit_id": {"algo": "sha1", "hex": anchor_sha},
                        "path": "seed.py",
                        "range": {"start_line": 1, "end_line": 1},
                        "relation": "anchored_in_git",
                        "evidence_tier": "exact_range_hash",
                        "evidence_firmness": "firm",
                        "limitations": [],
                    },
                ),
            ],
            writer="test-fixture",
        )
        patch_ids.append(f"tracepatch-sha256:{patch_id_hex}")

    events = read_events(tmp_path, verify=False)

    with BatchSyncContext(repo=tmp_path) as ctx:
        for patch_id in patch_ids:
            sync_patch(tmp_path, patch_id, events=events, batch_ctx=ctx)
        # Every probe should have been served from the cached ancestry set.
        assert ctx.calls["ancestry_probes"] >= 10, ctx.calls
        assert ctx.calls["ancestry_uncached_probes"] == 0, (
            "BatchSyncContext failed to amortise ancestry probes; "
            f"counters={ctx.calls}"
        )


def test_batch_ctx_uses_cat_file_pipe(tmp_path: Path) -> None:
    """``BatchSyncContext.show_file`` uses a long-lived ``git cat-file
    --batch`` pipe instead of spawning ``git show`` per call.

    We prove this by patching ``subprocess.run`` to count ``git show``
    invocations and ``subprocess.Popen`` to count cat-file process spawns.
    """
    _init_repo(tmp_path)
    patch_ids: list[str] = []
    for index in range(10):
        file_path = f"file_{index:02d}.py"
        text = f"alpha {index}\nline\n"
        (tmp_path / file_path).write_text(text)
        head = _commit(tmp_path, f"seed {index}")
        patch_id = f"{index:02d}{'c' * 62}"[:64]
        _seed_patch_anchor(
            tmp_path,
            trace_id=f"t-{index:02d}",
            patch_id=patch_id,
            anchor_commit=head,
            file_path=file_path,
            text=text,
        )
        patch_ids.append(f"tracepatch-sha256:{patch_id}")

    events = read_events(tmp_path, verify=False)

    # Count spawns of the survival-side cat-file pipe specifically (not
    # the unrelated event-log cat-file usage in append_event_batch which
    # shares the ``subprocess`` module). We instrument
    # ``_CatFilePipe._ensure`` directly: it transitions ``self.proc``
    # from ``None`` to a live Popen — exactly once per pipe lifetime.
    from opentraces.core.trails import sync as sync_module

    real_ensure = sync_module._CatFilePipe._ensure
    pipe_spawn_count = 0

    def counting_ensure(self):
        nonlocal pipe_spawn_count
        was_none = self.proc is None
        result = real_ensure(self)
        if was_none and self.proc is not None:
            pipe_spawn_count += 1
        return result

    with patch.object(sync_module._CatFilePipe, "_ensure", counting_ensure):
        with BatchSyncContext(repo=tmp_path) as ctx:
            for patch_id in patch_ids:
                sync_patch(tmp_path, patch_id, events=events, batch_ctx=ctx)

    # We did at least 10 ``show_file`` calls. Without the pipe each would
    # have been a ``git show`` invocation; with the pipe we expect a single
    # cat-file pipe spawn for the whole batch.
    assert ctx.calls["show_file"] >= 10, ctx.calls
    assert pipe_spawn_count == 1, (
        f"expected exactly 1 cat-file pipe spawn, got {pipe_spawn_count} "
        f"(show_file_count={ctx.calls['show_file']})"
    )
    assert ctx.calls["show_file_uncached"] == 0, (
        "show_file fell back to per-call git show; uncached="
        f"{ctx.calls['show_file_uncached']}"
    )
