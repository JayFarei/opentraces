"""#130 session-open baseline + exact-reconstruct tripwire (epic #169 tier D).

The origin snapshot (``snapshot_role='origin'`` / ``step_index=-1``) captures the
TRUE session-open world: ``start_git_head`` + ``public_base_sha`` +
``start_tree_id``. The start diff is NOT stored — it is derived on read as
``diff(public_base_tree, start_tree)``. The load-bearing property these tests
re-observe (not narrate) is that applying that derived diff back onto the public
base reproduces ``start_tree_id`` byte-for-byte: reconstruction is EXACT.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from opentraces.capture.codex_cli.parse import CodexCliParser
from opentraces.core.trails import (
    ORIGIN_STEP_INDEX,
    SNAPSHOT_ROLE_ORIGIN,
    emit_origin_snapshot,
    emit_step_window_events_from_record,
    read_events,
    reconstruct_origin_tree,
    write_worktree_tree,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_base(repo: Path) -> dict[str, str]:
    (repo / "app.py").write_text("print('base')\n")
    (repo / "README.md").write_text("# base\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    return {"algo": "sha1", "hex": _git(repo, "rev-parse", "HEAD")}


def _head_oid(repo: Path) -> dict[str, str]:
    return {"algo": "sha1", "hex": _git(repo, "rev-parse", "HEAD")}


def test_reconstruct_is_exact_for_dirty_session_open(tmp_path: Path) -> None:
    """Public base committed; session opens with uncommitted local edits.

    start diff = the uncommitted edits; applying it back onto the public base
    must reproduce the session-open worktree tree exactly.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    public_base = _commit_base(repo)

    # Session-open world: edit an existing file, add a new file, delete one.
    (repo / "app.py").write_text("print('session open dirty')\n")
    (repo / "new_module.py").write_text("VALUE = 42\n")
    (repo / "README.md").unlink()

    start_tree_id = write_worktree_tree(repo)
    assert start_tree_id["hex"] != _git(repo, "rev-parse", "HEAD^{tree}")

    result = reconstruct_origin_tree(repo, public_base=public_base, start_tree_id=start_tree_id)

    # Re-observed exact reconstruction.
    assert result.start_diff.strip(), "a dirty session open must have a non-empty start diff"
    assert result.recomputed_tree_hex == start_tree_id["hex"], (
        f"reconstruction drifted: recomputed={result.recomputed_tree_hex} "
        f"start={start_tree_id['hex']}"
    )
    assert result.exact is True


def test_reconstruct_is_exact_with_no_public_base(tmp_path: Path) -> None:
    """Repo with no commits: public base is the empty tree, start diff = whole worktree."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("a = 1\n")
    (repo / "pkg" / "b.py").parent.mkdir()
    (repo / "pkg" / "b.py").write_text("b = 2\n")

    start_tree_id = write_worktree_tree(repo)
    result = reconstruct_origin_tree(repo, public_base=None, start_tree_id=start_tree_id)

    assert result.recomputed_tree_hex == start_tree_id["hex"]
    assert result.exact is True


def test_reconstruct_is_exact_for_clean_session_open(tmp_path: Path) -> None:
    """Worktree clean at session open: start tree == public base tree, empty diff."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    public_base = _commit_base(repo)

    start_tree_id = write_worktree_tree(repo)
    assert start_tree_id["hex"] == _git(repo, "rev-parse", "HEAD^{tree}")

    result = reconstruct_origin_tree(repo, public_base=public_base, start_tree_id=start_tree_id)
    assert result.start_diff.strip() == ""
    assert result.recomputed_tree_hex == start_tree_id["hex"]
    assert result.exact is True


def test_reconstruct_is_exact_with_binary_content(tmp_path: Path) -> None:
    """Binary edits must round-trip exactly (proves the --binary derive/apply path)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    public_base = _commit_base(repo)

    # Binary blob with NULs and high bytes in the session-open worktree.
    (repo / "blob.bin").write_bytes(bytes(range(256)) * 4)
    (repo / "app.py").write_bytes(b"print('x')\x00\xff\n")

    start_tree_id = write_worktree_tree(repo)
    result = reconstruct_origin_tree(repo, public_base=public_base, start_tree_id=start_tree_id)

    assert result.recomputed_tree_hex == start_tree_id["hex"]
    assert result.exact is True


def test_emit_origin_snapshot_roundtrips_and_is_idempotent(tmp_path: Path) -> None:
    """emit_origin_snapshot writes a role=origin / step_index=-1 event whose
    captured baseline reconstructs the session-open tree exactly, and a second
    emit is a no-op."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    public_base = _commit_base(repo)

    (repo / "app.py").write_text("print('open')\n")
    start_tree_id = write_worktree_tree(repo)
    start_head = _head_oid(repo)

    first = emit_origin_snapshot(
        repo,
        trace_id="trace-origin",
        start_tree_id=start_tree_id,
        start_git_head=start_head,
        public_base=public_base,
        capture_method=["session_start"],
        generation_index=0,
    )
    second = emit_origin_snapshot(
        repo,
        trace_id="trace-origin",
        start_tree_id=start_tree_id,
        start_git_head=start_head,
        public_base=public_base,
        capture_method=["session_start"],
        generation_index=0,
    )
    assert first.snapshot_id == second.snapshot_id

    events = read_events(repo, verify=False)
    origins = [
        e
        for e in events
        if e.event_type == "trace_snapshot_created"
        and e.payload.get("snapshot_role") == SNAPSHOT_ROLE_ORIGIN
    ]
    assert len(origins) == 1, "emit must be idempotent (exactly one origin snapshot)"
    payload = origins[0].payload
    assert origins[0].step_index == ORIGIN_STEP_INDEX
    assert payload["start_tree_id"]["hex"] == start_tree_id["hex"]
    assert payload["start_git_head"]["hex"] == start_head["hex"]
    assert payload["public_base_sha"]["hex"] == public_base["hex"]

    # Reconstruct from the captured baseline (no out-of-band data).
    result = reconstruct_origin_tree(
        repo,
        public_base=payload["public_base_sha"],
        start_tree_id=payload["start_tree_id"],
    )
    assert result.exact is True
    assert result.recomputed_tree_hex == start_tree_id["hex"]

    # The advisory origin ref pins the session-open tree.
    assert _git(repo, "rev-parse", first.ref) == start_tree_id["hex"]


def _codex_session_with_one_edit(repo: Path, tmp_path: Path):
    """Build a parsed Codex record with one Edit step + hook trail metadata."""
    pre_tree = {"algo": "sha1", "hex": _git(repo, "write-tree")}
    head = _head_oid(repo)
    (repo / "app.py").write_text("print('edited')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    post_tree = {"algo": "sha1", "hex": _git(repo, "write-tree")}
    subprocess.run(["git", "reset", "-q"], cwd=repo, check=True)

    session_id = "codex-origin-session"
    transcript = tmp_path / "rollout-2026-05-21T09-00-00-trail.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "timestamp": "2026-05-21T09:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": session_id,
                        "timestamp": "2026-05-21T09:00:00Z",
                        "cwd": str(repo),
                        "cli_version": "0.132.0",
                        "source": "tui",
                        "model_provider": "openai",
                    },
                },
                {
                    "timestamp": "2026-05-21T09:00:02Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Edit app.py"}],
                    },
                },
                {
                    "timestamp": "2026-05-21T09:00:03Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "Edit",
                        "arguments": json.dumps(
                            {
                                "file_path": str(repo / "app.py"),
                                "old_string": "print('base')",
                                "new_string": "print('edited')",
                            }
                        ),
                        "call_id": "call-edit",
                    },
                },
                {
                    "timestamp": "2026-05-21T09:00:04Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "call-edit",
                        "output": "ok",
                    },
                },
            ]
        )
        + "\n"
    )

    sidecar = repo / ".opentraces" / "codex-cli" / "hooks" / f"{session_id}.jsonl"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "type": "opentraces_hook",
                    "event": "PreToolUse",
                    "timestamp": "2026-05-21T09:00:03Z",
                    "data": {
                        "session_id": session_id,
                        "tool_use_id": "call-edit",
                        "tool": "Edit",
                        "tool_input": {"file_path": str(repo / "app.py")},
                        "trail": {
                            "worktree_root": str(repo),
                            "tree_id": pre_tree,
                            "git_head": head,
                        },
                    },
                },
                {
                    "type": "opentraces_hook",
                    "event": "PostToolUse",
                    "timestamp": "2026-05-21T09:00:04Z",
                    "data": {
                        "session_id": session_id,
                        "tool_use_id": "call-edit",
                        "tool": "Edit",
                        "tool_input": {"file_path": str(repo / "app.py")},
                        "tool_response": {"status": "ok"},
                        "capture_status": "captured",
                        "limitations": [],
                        "trail": {
                            "worktree_root": str(repo),
                            "tree_id": post_tree,
                            "git_head": head,
                        },
                    },
                },
            ]
        )
        + "\n"
    )

    record = CodexCliParser().parse_session(transcript)
    assert record is not None
    record.trace_id = "trace-codex-origin"
    record.generation_index = 0
    return record, pre_tree


def test_record_emitter_wires_origin_snapshot(tmp_path: Path) -> None:
    """emit_step_window_events_from_record emits exactly one origin snapshot at
    step_index=-1, derived from the first PreToolUse tree, reconstructable."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_base(repo)

    record, pre_tree = _codex_session_with_one_edit(repo, tmp_path)
    result = emit_step_window_events_from_record(repo, record)
    assert result.emitted_events

    events = read_events(repo, verify=False)
    origins = [
        e
        for e in events
        if e.event_type == "trace_snapshot_created"
        and e.payload.get("snapshot_role") == SNAPSHOT_ROLE_ORIGIN
    ]
    assert len(origins) == 1
    origin = origins[0]
    assert origin.step_index == ORIGIN_STEP_INDEX
    assert origin.payload["start_tree_id"]["hex"] == pre_tree["hex"]

    # The origin must sort before every real step (lowest sequence in the log).
    min_step_seq = min(
        e.event_sequence
        for e in events
        if e.event_type == "trace_snapshot_created"
        and e.payload.get("snapshot_role") in {"before", "after"}
    )
    assert origin.event_sequence < min_step_seq

    result = reconstruct_origin_tree(
        repo,
        public_base=origin.payload.get("public_base_sha"),
        start_tree_id=origin.payload["start_tree_id"],
    )
    assert result.exact is True


def test_record_emitter_origin_is_idempotent(tmp_path: Path) -> None:
    """Re-emitting the same record does not duplicate the origin snapshot."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_base(repo)
    record, _ = _codex_session_with_one_edit(repo, tmp_path)

    emit_step_window_events_from_record(repo, record)
    emit_step_window_events_from_record(repo, record)

    events = read_events(repo, verify=False)
    origins = [
        e
        for e in events
        if e.event_type == "trace_snapshot_created"
        and e.payload.get("snapshot_role") == SNAPSHOT_ROLE_ORIGIN
    ]
    assert len(origins) == 1


def test_idempotent_retry_recovers_patch_after_pre_staging_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry projects the already-appended bounded trace slice.

    This models a crash after Trail append but before the staging JSONL write:
    the retry's append is idempotently empty, yet the patch must still reach
    ``TraceRecord.patches`` without consulting project-wide Trail history.
    """
    from opentraces.core import ingest
    from opentraces.core.trails import event_log

    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_base(repo)
    record, _ = _codex_session_with_one_edit(repo, tmp_path)

    first = emit_step_window_events_from_record(repo, record)
    first_patch_ids = {
        event.payload.get("trace_patch_id")
        for event in first.emitted_events
        if event.event_type == "trace_patch_created"
    }
    assert first_patch_ids
    assert not (tmp_path / "staged.jsonl").exists(), (
        "fixture models the crash window before any staged record exists"
    )

    def _global_read_forbidden(*_args, **_kwargs):
        raise AssertionError("retry patch recovery read global Trail history")

    monkeypatch.setattr(event_log, "read_events", _global_read_forbidden)
    retry = emit_step_window_events_from_record(repo, record)

    assert retry.emitted_events == []
    recovered = ingest._backfill_patches_from_trail_events(
        repo,
        record.trace_id,
        record.generation_index,
        events=retry.projection_events,
    )
    assert {patch.patch_id for patch in recovered} == first_patch_ids
