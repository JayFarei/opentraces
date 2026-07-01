"""Tests for the session ingestion core (plan: live-session ingestion, Phase 1).

Every ingest re-derives the trace from JSONL offset 0 to current EOF
(full re-derivation, not an incremental delta). State per-session is
just ``observed_size`` + ``observed_mtime`` for "has the file grown?"
and an ordered ``generations`` list.

Terminal-status policy when the JSONL has grown:
  UPLOADED / REJECTED / COMMITTED / DISCARDED → open new generation with
    ``supersedes`` pointing at the prior trace_id. Latest content in gen N
    is the FULL history (0..EOF), not just the delta — consumers see the
    new generation as a replacement, not an append.
  INBOX / PARSED / STAGED / REVIEWING / APPROVED → refresh in place,
    trace_id unchanged.
  BLOCKED → no-op (a secret in the early transcript is still there;
    resumes can't untaint).

Trace ID scheme:
  Every generation: a fresh UUIDv4 minted by ``_trace_id_for``. Generation
  number lives in the SessionRecord, not in the id string. The same session
  can own multiple generations, each with its own canonical trace_id.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from opentraces.core.state import StateManager, TraceStatus

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _is_uuid(s: str) -> bool:
    return bool(_UUID_RE.match(s))


# --------------------------------------------------------------------------- #
# Fixtures: synthetic Claude Code JSONL
# --------------------------------------------------------------------------- #

def _turn(i: int, session_id: str, *, tool_id: str | None = None) -> list[dict]:
    """One user→assistant→tool_result triple for session_id."""
    ts = f"2026-04-15T07:00:{i:02d}Z"
    lines: list[dict] = [
        {
            "type": "user",
            "sessionId": session_id,
            "timestamp": ts,
            "message": {"role": "user", "content": f"prompt {i}"},
        },
    ]
    tool_id = tool_id or f"tu_{i}"
    lines.append({
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tool_id, "name": "Read",
                 "input": {"file_path": "x.py"}},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 10},
        },
    })
    lines.append({
        "type": "user",
        "sessionId": session_id,
        "timestamp": ts,
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": "ok"},
            ],
        },
    })
    return lines


def _write_jsonl(project_dir: Path, session_id: str, turns: int) -> Path:
    """Create a realistic Claude Code session JSONL under the proper path."""
    claude_root = project_dir / ".claude_projects_fake"
    # encode_claude_path is not used here — this test drives ingest_one_session
    # directly by path, so the JSONL can live anywhere we want.
    claude_root.mkdir(parents=True, exist_ok=True)
    path = claude_root / f"{session_id}.jsonl"
    with path.open("w") as f:
        for i in range(1, turns + 1):
            for line in _turn(i, session_id):
                f.write(json.dumps(line) + "\n")
    return path


def _append_turns(path: Path, session_id: str, *, start: int, count: int) -> None:
    with path.open("a") as f:
        for i in range(start, start + count):
            for line in _turn(i, session_id):
                f.write(json.dumps(line) + "\n")


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "x.py").write_text("VALUE = 'old'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


@pytest.fixture
def project_dir(tmp_path):
    """A minimal opted-in project dir.

    The autouse ``_isolate_opentraces_global_state`` fixture in
    ``tests/conftest.py`` already redirects OPENTRACES_DIR / PROJECTS_DIR
    to a tmp location, so all we need here is the per-project marker file.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".opentraces.json").write_text(json.dumps({
        "marker_version": "2",
        "project_id": "test-project-0000",
        "review_policy": "review",
        "push_policy": "manual",
        "remotes": {"origin": {"url": "test/test", "visibility": "private"}},
        "active_remote": "origin",
        "agents": ["claude-code"],
    }))
    return proj


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #

class TestIngestOneSession:
    def test_new_session_creates_generation_one_in_inbox(self, project_dir) -> None:
        from opentraces.core.ingest import ingest_one_session

        session_id = "sess-alpha"
        path = _write_jsonl(project_dir, session_id, turns=3)

        result = ingest_one_session(path, project_dir)

        assert result.action == "new"
        assert result.session_id == session_id
        assert _is_uuid(result.trace_id)
        assert result.error is None

        # State reflects the new session + one generation.
        from opentraces.core.state import StateManager
        from opentraces.core.config import get_project_state_path
        state = StateManager(state_path=get_project_state_path(project_dir))
        sess = state.get_session(session_id)
        assert sess is not None
        assert sess.source_path == str(path)
        assert sess.observed_size == path.stat().st_size
        assert len(sess.generations) == 1

        gen = sess.generations[0]
        assert gen.generation == 1
        assert gen.trace_id == result.trace_id
        assert _is_uuid(gen.trace_id)
        assert gen.status_at_capture == TraceStatus.STAGED.value
        assert gen.supersedes is None

        # Trace is in INBOX.
        entry = state.get_trace(gen.trace_id)
        assert entry is not None
        assert entry.status == TraceStatus.STAGED.value

        # Staging JSONL was written.
        from opentraces.core.config import get_project_traces_dir
        staging = get_project_traces_dir(project_dir) / f"{gen.trace_id}.jsonl"
        assert staging.exists()
        assert staging.stat().st_size > 0

    def test_hook_boundaries_emit_trace_trail_snapshots_for_captured_session(
        self, project_dir, tmp_path
    ) -> None:
        from click.testing import CliRunner

        from opentraces.cli import main
        from opentraces.core.config import get_project_traces_dir
        from opentraces.core.ingest import ingest_one_session
        from opentraces.core.trails import read_events, write_worktree_tree

        _init_git_repo(project_dir)
        session_id = "sess-trail"
        x_path = project_dir / "x.py"
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project_dir, text=True
        ).strip()
        head_id = {"algo": "sha1", "hex": head}
        before_tree = write_worktree_tree(project_dir)
        x_path.write_text("VALUE = 'new-from-hooked-session'\n")
        after_tree = write_worktree_tree(project_dir)

        def hook_event(
            event: str,
            tool_id: str,
            tool_name: str,
            timestamp: str,
            tree_id: dict,
            **extra: object,
        ) -> dict:
            data = {
                "tool": tool_name,
                "tool_use_id": tool_id,
                "tool_input": {"file_path": str(x_path)},
                "trail": {
                    "worktree_root": str(project_dir),
                    "tree_id": tree_id,
                    "git_head": head_id,
                },
            }
            data.update(extra)
            return {
                "type": "opentraces_hook",
                "event": event,
                "timestamp": timestamp,
                "data": data,
            }

        session_path = tmp_path / "corpus" / f"{session_id}.jsonl"
        session_path.parent.mkdir(parents=True)
        lines = [
            *_turn(1, session_id, tool_id="read_1"),
            hook_event(
                "PreToolUse", "read_1", "Read", "2026-04-15T07:00:11Z", before_tree
            ),
            hook_event(
                "PostToolUse", "read_1", "Read", "2026-04-15T07:00:12Z",
                before_tree, capture_status="hook_only", limitations=["hook_only"],
            ),
            *_turn(2, session_id, tool_id="write_2"),
            hook_event(
                "PreToolUse", "write_2", "Write", "2026-04-15T07:00:21Z", before_tree
            ),
            hook_event(
                "PostToolUse", "write_2", "Write", "2026-04-15T07:00:22Z",
                after_tree, file_path=str(x_path), start_line=1, end_line=1,
                content_hash="murmur3:0", confidence="high",
            ),
            {
                "type": "opentraces_hook",
                "event": "Stop",
                "timestamp": "2026-04-15T07:00:30Z",
                "data": {
                    "session_id": session_id,
                    "agent_type": "main",
                    "permission_mode": "default",
                    "git": {"sha": head, "dirty": True, "changed_paths": ["x.py"]},
                    "trail": {
                        "worktree_root": str(project_dir),
                        "tree_id": after_tree,
                        "git_head": head_id,
                    },
                },
            },
        ]
        with session_path.open("w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")

        result = ingest_one_session(session_path, project_dir)

        assert result.action == "new"
        assert result.trace_id is not None
        staging = get_project_traces_dir(project_dir) / f"{result.trace_id}.jsonl"
        record = json.loads(staging.read_text())
        step_by_tool = {
            tc["tool_call_id"]: step["step_index"]
            for step in record["steps"]
            for tc in step.get("tool_calls", [])
        }
        read_step = step_by_tool["read_1"]
        write_step = step_by_tool["write_2"]

        events = [
            event for event in read_events(project_dir)
            if event.trace_id == result.trace_id
        ]
        trail_events = [
            event for event in events
            if event.event_type.startswith("trace_")
        ]
        assert [event.event_type for event in trail_events] == [
            # #130: the session-open baseline (snapshot_role=origin, step_index=-1)
            # leads the log, before the first step window.
            "trace_snapshot_created",
            "trace_step_window_opened",
            "trace_snapshot_created",
            "trace_snapshot_created",
            "trace_step_window_closed",
            "trace_step_window_opened",
            "trace_snapshot_created",
            "trace_snapshot_created",
            "trace_step_window_closed",
            "trace_patch_created",
            "trace_session_closed",
        ]
        origin_snapshots = [
            event for event in events
            if event.event_type == "trace_snapshot_created"
            and event.payload.get("snapshot_role") == "origin"
        ]
        assert len(origin_snapshots) == 1
        assert origin_snapshots[0].step_index == -1
        assert origin_snapshots[0].payload["tree_id"] == before_tree
        snapshots = [
            event for event in events
            if event.event_type == "trace_snapshot_created"
        ]
        after_snapshots = [
            event for event in snapshots
            if event.payload["snapshot_role"] == "after"
        ]
        assert {event.step_index for event in after_snapshots} == {read_step, write_step}
        assert after_snapshots[0].payload["tree_id"] == before_tree
        assert after_snapshots[0].payload["capture_status"] == "hook_only"
        assert "hook_only" in after_snapshots[0].payload["limitations"]
        assert after_snapshots[1].payload["tree_id"] == after_tree
        assert after_snapshots[1].payload["capture_status"] == "captured"
        for snapshot in snapshots:
            subprocess.run(
                ["git", "cat-file", "-e", snapshot.payload["tree_id"]["hex"]],
                cwd=project_dir,
                check=True,
            )
        for snapshot in after_snapshots:
            ref = (
                f"refs/opentraces/local/traces/{result.trace_id}/1"
                f"/snapshots/step_{snapshot.step_index}"
            )
            subprocess.run(["git", "rev-parse", "--verify", ref], cwd=project_dir, check=True)
        patch_events = [
            event for event in events
            if event.event_type == "trace_patch_created"
        ]
        assert len(patch_events) == 1
        assert patch_events[0].step_index == write_step
        assert "new-from-hooked-session" in patch_events[0].payload["authored_text"]
        session_closed = trail_events[-1]
        assert session_closed.capture_method == ["hook_stop"]
        assert session_closed.payload["tree_id"] == after_tree

        refreshed = ingest_one_session(session_path, project_dir, reparse=True)
        assert refreshed.action == "refreshed"
        assert refreshed.trace_id == result.trace_id
        assert [
            event.event_id for event in read_events(project_dir)
            if event.trace_id == result.trace_id
            and event.event_type.startswith("trace_")
        ] == [event.event_id for event in trail_events]

        diff = CliRunner().invoke(
            main,
            [
                "trail", "diff",
                "--trace", result.trace_id,
                "--from-step", str(read_step),
                "--to-step", str(write_step),
                "--json",
                "--project", str(project_dir),
            ],
        )
        assert diff.exit_code == 0, diff.output
        payload = json.loads(diff.output)
        assert payload["from_tree_id"] == before_tree
        assert payload["to_tree_id"] == after_tree
        assert "new-from-hooked-session" in payload["trace_patch"]["patch"]

        subprocess.run(["git", "add", "x.py"], cwd=project_dir, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "apply session change"], cwd=project_dir, check=True)
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project_dir, text=True
        ).strip()

        hook = CliRunner().invoke(
            main,
            ["_run-post-commit-hook", str(project_dir)],
        )
        assert hook.exit_code == 0, hook.output

        all_events = read_events(project_dir)
        refreshed_events = [
            event for event in all_events
            if event.trace_id == result.trace_id
        ]
        # plan 090: the v2 anchor-search summary spans traces (top-level
        # trace_id None), so select it from the full log and assert this trace's
        # patch is recorded in its results[].
        search_events = [
            event for event in all_events
            if event.event_type == "git_anchor_search_completed"
        ]
        anchor_events = [
            event for event in refreshed_events
            if event.event_type == "git_anchor_created"
        ]
        assert len(search_events) == 1
        assert search_events[0].capture_method == ["post_commit_correlator"]
        result_for_patch = next(
            r for r in search_events[0].payload["results"]
            if r["trace_patch_id"] == patch_events[0].payload["trace_patch_id"]
        )
        assert result_for_patch["result"] == "anchored"
        assert result_for_patch["trace_id"] == result.trace_id
        assert len(anchor_events) == 1
        assert anchor_events[0].capture_method == ["post_commit_correlator"]
        assert anchor_events[0].payload["trace_patch_id"] == (
            patch_events[0].payload["trace_patch_id"]
        )
        assert anchor_events[0].payload["commit_id"]["hex"] == commit
        assert anchor_events[0].payload["evidence_tier"] == "exact_range_hash"
        log_entries = [
            json.loads(line)
            for line in (project_dir / ".git" / "opentraces-hook.log").read_text().splitlines()
            if line.strip()
        ]
        assert log_entries[-1]["trail_anchors_created"] == 1
        assert log_entries[-1]["trail_anchor_error"] is None

    def test_incomplete_hook_capture_emits_loss_event(self, project_dir, tmp_path) -> None:
        from opentraces.core.ingest import ingest_one_session
        from opentraces.core.trails import read_events, write_worktree_tree

        _init_git_repo(project_dir)
        session_id = "sess-incomplete-trails"
        tree = write_worktree_tree(project_dir)
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project_dir, text=True
        ).strip()
        head_id = {"algo": "sha1", "hex": head}
        session_path = tmp_path / "corpus" / f"{session_id}.jsonl"
        session_path.parent.mkdir(parents=True)
        lines = [
            *_turn(1, session_id, tool_id="read_1"),
            {
                "type": "opentraces_hook",
                "event": "PreToolUse",
                "timestamp": "2026-04-15T07:00:11Z",
                "data": {
                    "tool": "Read",
                    "tool_use_id": "read_1",
                    "tool_input": {"file_path": str(project_dir / "x.py")},
                    "trail": {
                        "worktree_root": str(project_dir),
                        "tree_id": tree,
                        "git_head": head_id,
                    },
                },
            },
            *_turn(2, session_id, tool_id="read_2"),
        ]
        with session_path.open("w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")

        result = ingest_one_session(session_path, project_dir)

        assert result.action == "new"
        incomplete = [
            event for event in read_events(project_dir)
            if event.trace_id == result.trace_id
            and event.event_type == "trace_step_capture_incomplete"
        ]
        assert len(incomplete) == 1
        assert incomplete[0].payload["skipped_tool_calls"] == 2
        assert incomplete[0].payload["reasons"] == {"missing_pre_or_post_hook": 2}

    def test_ingest_reconciles_existing_filesystem_observation(
        self, project_dir, tmp_path
    ) -> None:
        from opentraces.capture.fs_watcher import append_filesystem_mutation_observed
        from opentraces.core.ingest import ingest_one_session
        from opentraces.core.trails import GitObjectID, read_events, write_worktree_tree

        _init_git_repo(project_dir)
        session_id = "sess-watch-reconcile"
        x_path = project_dir / "x.py"
        before_text = x_path.read_text()
        before_blob = subprocess.check_output(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=project_dir,
            input=before_text,
            text=True,
        ).strip()
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project_dir, text=True
        ).strip()
        head_id = {"algo": "sha1", "hex": head}
        before_tree = write_worktree_tree(project_dir)
        after_text = "VALUE = 'watcher-backed'\n"
        x_path.write_text(after_text)
        after_blob = subprocess.check_output(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=project_dir,
            input=after_text,
            text=True,
        ).strip()
        after_tree = write_worktree_tree(project_dir)
        append_filesystem_mutation_observed(
            project_dir,
            path="x.py",
            observed_at_start="2026-04-15T07:00:21.100000Z",
            observed_at_end="2026-04-15T07:00:21.900000Z",
            before_blob_id=GitObjectID(algo="sha1", hex=before_blob),
            after_blob_id=GitObjectID(algo="sha1", hex=after_blob),
            writer="test-hook-boundary-observer",
        )

        def hook_event(
            event: str,
            timestamp: str,
            tree_id: dict,
            **extra: object,
        ) -> dict:
            data = {
                "tool": "Write",
                "tool_use_id": "write_1",
                "tool_input": {"file_path": str(x_path), "content": after_text},
                "trail": {
                    "worktree_root": str(project_dir),
                    "tree_id": tree_id,
                    "git_head": head_id,
                },
            }
            data.update(extra)
            return {
                "type": "opentraces_hook",
                "event": event,
                "timestamp": timestamp,
                "data": data,
            }

        session_path = tmp_path / "corpus" / f"{session_id}.jsonl"
        session_path.parent.mkdir(parents=True)
        turn = _turn(1, session_id, tool_id="write_1")
        turn[1]["message"]["content"][0]["name"] = "Write"
        turn[1]["message"]["content"][0]["input"] = {
            "file_path": str(x_path),
            "content": after_text,
        }
        lines = [
            *turn,
            hook_event("PreToolUse", "2026-04-15T07:00:21Z", before_tree),
            hook_event(
                "PostToolUse",
                "2026-04-15T07:00:22Z",
                after_tree,
                file_path=str(x_path),
                start_line=1,
                end_line=1,
                content_hash="murmur3:0",
                confidence="high",
            ),
        ]
        with session_path.open("w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")

        result = ingest_one_session(session_path, project_dir)

        assert result.action == "new"
        events = read_events(project_dir)
        attributed = [
            event for event in events
            if event.event_type == "watcher_observation_attributed"
        ]
        assert len(attributed) == 1
        assert attributed[0].trace_id == result.trace_id
        assert attributed[0].payload["result"] == "attributed"
        upgraded = [
            event for event in events
            if event.event_type == "trace_patch_created"
            and event.trace_id == result.trace_id
            and "watcher_backstop" in event.capture_method
        ]
        assert len(upgraded) == 1
        assert upgraded[0].payload["file_path"] == "x.py"

    def test_ingest_writes_per_trace_envelope_and_manifest_row(
        self, project_dir
    ) -> None:
        """Issue #31 — write-side capture parity.

        Ingest must project the per-trace v2 envelope at capture time, so the
        manifest-only readers (``bucket manifest`` / ``ctx list``) agree with
        ``bucket status`` / the index immediately — without waiting for a
        later ``bucket repair``. Assert ``trace.json`` exists on disk and the
        trace appears in ``bucket_manifest(write=False)['traces']``.
        """
        from opentraces.core.bucket_store import (
            bucket_manifest,
            trace_v1_json_path,
        )
        from opentraces.core.config import get_project_dir
        from opentraces.core.ingest import ingest_one_session

        path = _write_jsonl(project_dir, "sess-envelope", turns=3)
        result = ingest_one_session(path, project_dir)
        assert result.error is None

        project_slug = get_project_dir(project_dir).name
        assert trace_v1_json_path(project_slug, result.trace_id).exists()

        manifest = bucket_manifest(write=False, include_objects=False)
        trace_ids = [row["trace_id"] for row in manifest["traces"]]
        assert result.trace_id in trace_ids

    def test_ingest_writes_manifest_json_so_ctx_readers_see_the_trace(
        self, project_dir
    ) -> None:
        """Issue #54 — capture materializes ``bucket/manifest.json`` on disk.

        ``ctx info`` / ``ctx list`` are manifest-only readers that open
        ``manifest.json`` directly (``heal=False``, no in-memory reconcile).
        Issue #31 wrote the per-trace envelope at capture time but never the
        top-level manifest, so a freshly captured trace was invisible to those
        readers until a ``bucket manifest`` / ``bucket repair`` heal verb ran.
        After ingest the manifest must exist AND the captured trace's
        ``traces[]`` row must be byte-identical to the row a subsequent full
        ``bucket_manifest(write=True)`` regeneration produces (idempotent
        same-bytes writers; digest excludes volatile timestamps).
        """
        import json as _json

        from opentraces.core.bucket_store import (
            bucket_manifest,
            bucket_manifest_path,
        )
        from opentraces.core.ingest import ingest_one_session

        path = _write_jsonl(project_dir, "sess-manifest-disk", turns=3)
        result = ingest_one_session(path, project_dir)
        assert result.error is None

        # Manifest exists on disk after the capture chain — no heal verb ran.
        manifest_path = bucket_manifest_path()
        assert manifest_path.exists()
        on_disk = _json.loads(manifest_path.read_text(encoding="utf-8"))
        assert on_disk.get("schema_version") == "opentraces.bucket.manifest.v2"
        capture_row = next(
            (r for r in on_disk["traces"] if r["trace_id"] == result.trace_id),
            None,
        )
        assert capture_row is not None

        # A full regeneration produces a byte-identical row for this trace.
        regen = bucket_manifest(write=True, include_objects=False)
        regen_row = next(
            (r for r in regen["traces"] if r["trace_id"] == result.trace_id),
            None,
        )
        assert regen_row is not None
        assert _json.dumps(capture_row, sort_keys=True) == _json.dumps(
            regen_row, sort_keys=True
        )

    def test_ingest_manifest_upsert_is_bounded_to_one_trace(
        self, project_dir, monkeypatch
    ) -> None:
        """Issue #54 — the capture-time manifest upsert is O(one trace).

        It must NOT call ``iter_trace_record_objects`` / ``trace_record_snapshot``
        or otherwise regenerate the whole manifest on the ingest hot path (the
        #44 post-commit latency class). The single-trace upsert reuses the
        per-trace envelope this ingest just wrote, never sweeping the object
        store.
        """
        from opentraces.core import bucket_store
        from opentraces.core.ingest import ingest_one_session

        sweep_calls = {"objects": 0, "snapshot": 0}

        real_objects = bucket_store.iter_trace_record_objects
        real_snapshot = bucket_store.trace_record_snapshot

        def _objects(*args, **kwargs):
            sweep_calls["objects"] += 1
            return real_objects(*args, **kwargs)

        def _snapshot(*args, **kwargs):
            sweep_calls["snapshot"] += 1
            return real_snapshot(*args, **kwargs)

        monkeypatch.setattr(bucket_store, "iter_trace_record_objects", _objects)
        monkeypatch.setattr(bucket_store, "trace_record_snapshot", _snapshot)
        # ingest.py imports these lazily from bucket_store, so patching the
        # module attribute is sufficient; pin the import surface explicitly too.
        monkeypatch.setattr(
            "opentraces.core.bucket_store.iter_trace_record_objects", _objects
        )
        monkeypatch.setattr(
            "opentraces.core.bucket_store.trace_record_snapshot", _snapshot
        )

        path = _write_jsonl(project_dir, "sess-bounded-upsert", turns=3)
        result = ingest_one_session(path, project_dir)
        assert result.error is None

        assert sweep_calls["objects"] == 0, "manifest upsert swept the object store"
        assert sweep_calls["snapshot"] == 0, "manifest upsert took a full snapshot"

    def test_record_only_ingest_writes_no_manifest_row(
        self, project_dir
    ) -> None:
        """Issue #54 / PR #63 — ``--trace-record-only`` defers projection.

        The record-only fast path writes the in-place JSONL + TraceRecord
        object + raw-source link but no per-trace envelope, so the bounded
        capture-time manifest upsert must also be skipped: no ``traces[]`` row,
        and ``manifest.json`` is not materialized by this path. Later
        ``bucket manifest --heal`` / ``bucket repair`` performs the deferred
        projection (covered by the #31 self-heal sentinels).
        """
        import json as _json

        from opentraces.core.bucket_store import bucket_manifest_path
        from opentraces.core.ingest import ingest_one_session

        path = _write_jsonl(project_dir, "sess-record-only-no-row", turns=3)
        result = ingest_one_session(path, project_dir, trace_record_only=True)
        assert result.error is None

        manifest_path = bucket_manifest_path()
        if manifest_path.exists():
            on_disk = _json.loads(manifest_path.read_text(encoding="utf-8"))
            trace_ids = [r["trace_id"] for r in on_disk.get("traces") or []]
            assert result.trace_id not in trace_ids

    def test_unchanged_file_is_noop(self, project_dir) -> None:
        from opentraces.core.ingest import ingest_one_session

        path = _write_jsonl(project_dir, "sess-beta", turns=3)
        first = ingest_one_session(path, project_dir)
        # Second ingest without changes → no-op, same canonical trace_id.
        result = ingest_one_session(path, project_dir)
        assert result.action == "noop"
        assert result.trace_id == first.trace_id
        assert _is_uuid(result.trace_id)

    def test_grown_file_inbox_generation_refreshed_in_place(self, project_dir) -> None:
        from opentraces.core.ingest import ingest_one_session

        session_id = "sess-gamma"
        path = _write_jsonl(project_dir, session_id, turns=3)
        first = ingest_one_session(path, project_dir)
        original_trace_id = first.trace_id

        # JSONL grows — still INBOX.
        _append_turns(path, session_id, start=4, count=2)
        result = ingest_one_session(path, project_dir)

        assert result.action == "refreshed"
        assert result.trace_id == original_trace_id

        from opentraces.core.state import StateManager
        from opentraces.core.config import get_project_state_path
        state = StateManager(state_path=get_project_state_path(project_dir))
        sess = state.get_session(session_id)
        assert len(sess.generations) == 1, \
            "refresh must not create a new generation"
        assert sess.generations[0].trace_id == original_trace_id
        assert sess.observed_size == path.stat().st_size

    def test_grown_file_after_upload_opens_new_generation(self, project_dir) -> None:
        from opentraces.core.ingest import ingest_one_session

        session_id = "sess-delta"
        path = _write_jsonl(project_dir, session_id, turns=3)
        gen1 = ingest_one_session(path, project_dir)
        gen1_trace_id = gen1.trace_id

        # Mark gen 1 as UPLOADED (terminal).
        from opentraces.core.state import StateManager
        from opentraces.core.config import get_project_state_path
        state = StateManager(state_path=get_project_state_path(project_dir))
        state.set_trace_status(gen1_trace_id, TraceStatus.UPLOADED)

        # JSONL grows (user resumed the session).
        _append_turns(path, session_id, start=4, count=2)
        result = ingest_one_session(path, project_dir)

        assert result.action == "new_generation"
        assert _is_uuid(result.trace_id)
        assert result.trace_id != gen1_trace_id
        assert result.supersedes == gen1_trace_id
        assert result.supersedes_reason == "resume"

        state = StateManager(state_path=get_project_state_path(project_dir))
        sess = state.get_session(session_id)
        assert len(sess.generations) == 2
        gen2 = sess.generations[1]
        assert gen2.generation == 2
        assert gen2.trace_id == result.trace_id
        assert _is_uuid(gen2.trace_id)
        assert gen2.supersedes == gen1_trace_id
        assert gen2.status_at_capture == TraceStatus.STAGED.value

        # gen 1 staging file left alone; gen 2 staging file written.
        from opentraces.core.config import get_project_traces_dir
        staging_dir = get_project_traces_dir(project_dir)
        assert (staging_dir / f"{gen1_trace_id}.jsonl").exists()
        assert (staging_dir / f"{gen2.trace_id}.jsonl").exists()

        # gen 1's trace state is still UPLOADED.
        assert state.get_trace(gen1_trace_id).status == TraceStatus.UPLOADED.value
        # gen 2 is in INBOX.
        assert state.get_trace(gen2.trace_id).status == TraceStatus.STAGED.value

    @pytest.mark.parametrize("terminal_status", [
        TraceStatus.UPLOADED,
        TraceStatus.REJECTED,
        TraceStatus.COMMITTED,
    ])
    def test_all_terminal_statuses_trigger_new_generation(
        self, project_dir, terminal_status
    ) -> None:
        from opentraces.core.ingest import ingest_one_session

        session_id = f"sess-{terminal_status.value}"
        path = _write_jsonl(project_dir, session_id, turns=3)
        gen1 = ingest_one_session(path, project_dir)

        from opentraces.core.state import StateManager
        from opentraces.core.config import get_project_state_path
        state = StateManager(state_path=get_project_state_path(project_dir))
        state.set_trace_status(gen1.trace_id, terminal_status)

        _append_turns(path, session_id, start=4, count=2)
        result = ingest_one_session(path, project_dir)

        assert result.action == "new_generation"
        assert _is_uuid(result.trace_id)
        assert result.trace_id != gen1.trace_id
        assert result.supersedes == gen1.trace_id

    def test_blocked_status_is_noop(self, project_dir) -> None:
        """A BLOCKED trace must not spawn new generations; security stays blocked."""
        from opentraces.core.ingest import ingest_one_session

        session_id = "sess-blocked"
        path = _write_jsonl(project_dir, session_id, turns=3)
        first = ingest_one_session(path, project_dir)

        from opentraces.core.state import StateManager
        from opentraces.core.config import get_project_state_path
        state = StateManager(state_path=get_project_state_path(project_dir))
        state.block_trace(first.trace_id, reason="test-block")

        _append_turns(path, session_id, start=4, count=2)
        result = ingest_one_session(path, project_dir)

        assert result.action == "noop"
        state = StateManager(state_path=get_project_state_path(project_dir))
        sess = state.get_session(session_id)
        assert len(sess.generations) == 1, \
            "BLOCKED sessions must not open new generations"


# --------------------------------------------------------------------------- #
# scan_project
# --------------------------------------------------------------------------- #

class TestScanProject:
    def test_scan_empty_corpus_returns_empty_report(
        self, project_dir, monkeypatch
    ) -> None:
        from opentraces.core.ingest import scan_project

        # Force the corpus discovery to return no sessions (no ~/.claude
        # corpus for this fake project_dir).
        monkeypatch.setattr(
            "opentraces.core.ingest.discover_claude_jsonl_corpus",
            lambda repo: [],
        )
        report = scan_project(project_dir)
        assert report.results == []

    def test_scan_iterates_every_session_in_corpus(
        self, project_dir, monkeypatch
    ) -> None:
        from opentraces.core.ingest import scan_project

        p1 = _write_jsonl(project_dir, "sess-A", turns=3)
        p2 = _write_jsonl(project_dir, "sess-B", turns=3)

        monkeypatch.setattr(
            "opentraces.core.ingest.discover_claude_jsonl_corpus",
            lambda repo: [p1, p2],
        )
        report = scan_project(project_dir)

        session_ids = {r.session_id for r in report.results}
        assert session_ids == {"sess-A", "sess-B"}
        assert all(r.action == "new" for r in report.results)

    def test_scan_defers_watcher_reconciliation_by_default(
        self, project_dir, monkeypatch
    ) -> None:
        from opentraces.core.ingest import scan_project

        path = _write_jsonl(project_dir, "sess-no-reconcile", turns=3)
        monkeypatch.setattr(
            "opentraces.core.ingest.discover_claude_jsonl_corpus",
            lambda repo: [path],
        )

        calls: list[Path] = []

        def _reconcile(repo):
            calls.append(Path(repo))
            return {
                "observations_processed": 0,
                "patches_created": 0,
                "patches_upgraded": 0,
            }

        monkeypatch.setattr(
            "opentraces.core.trails.reconcile_watcher_observations",
            _reconcile,
        )

        report = scan_project(project_dir)

        assert report.created == 1
        assert calls == []

    def test_scan_trace_record_only_skips_substrate_hot_path(
        self, project_dir, monkeypatch
    ) -> None:
        from opentraces.core import ingest as ingest_mod

        path = _write_jsonl(project_dir, "sess-trace-record-only", turns=3)
        monkeypatch.setattr(
            "opentraces.core.ingest.discover_claude_jsonl_corpus",
            lambda repo: [path],
        )

        trail_calls = {"n": 0}
        warm_calls: list[dict] = []

        def _emit(*args, **kwargs):
            trail_calls["n"] += 1

        def _warm(*args, **kwargs):
            warm_calls.append(dict(kwargs))

        monkeypatch.setattr(
            "opentraces.core.trails.emit_step_window_events_from_record",
            _emit,
        )
        monkeypatch.setattr(ingest_mod, "keep_index_warm", _warm)

        report = ingest_mod.scan_project(project_dir, trace_record_only=True)

        assert report.created == 1
        # Record-only still skips the Trail substrate hot path...
        assert trail_calls["n"] == 0
        # ...but MUST still warm the INDEX marker (index only, not the heavier
        # projection) so the next ``trace query`` short-circuits warm instead of
        # paying the whole-corpus cold sync (Bug A). See the cold-state guards in
        # tests/core/test_index_keep_warm.py.
        assert [c.get("query_sources") for c in warm_calls] == [("index",)]

    def test_scan_isolates_per_session_failures(
        self, project_dir, monkeypatch, caplog
    ) -> None:
        from opentraces.core.ingest import scan_project

        good = _write_jsonl(project_dir, "sess-good", turns=3)
        bad = project_dir / "sess-bad.jsonl"
        bad.write_text("{this is not valid json\n")

        monkeypatch.setattr(
            "opentraces.core.ingest.discover_claude_jsonl_corpus",
            lambda repo: [good, bad],
        )
        report = scan_project(project_dir)

        actions = {r.session_id: r.action for r in report.results}
        assert actions.get("sess-good") == "new"
        # The bad file should appear as errored or skipped — not propagate out
        # and kill the scan.
        assert any(r.action in ("error", "skipped") for r in report.results)


class TestIngestGenerationIndex:
    def test_ingest_generation_index_increments(self, project_dir) -> None:
        """Re-ingesting a grown session after a terminal status must stamp
        the outgoing TraceRecord's ``generation_index`` with the session's
        monotonic generation counter, and produce a different content_hash
        than the prior generation (since generation_index is part of the
        hashed payload).

        Note: the plan copy claimed ``first trace has generation_index=0``
        but the state-layer counter is 1-based (see
        ``_ingest_locked`` where ``next_generation = 1`` on the first
        generation). The field is populated from
        ``generation_record.generation`` per the plan's primary
        instruction, so the observed values are 1 and 2.
        """
        import json as _json

        from opentraces.core.config import get_project_traces_dir, get_project_state_path
        from opentraces.core.ingest import ingest_one_session
        from opentraces.core.state import StateManager, TraceStatus

        session_id = "sess-genidx"
        path = _write_jsonl(project_dir, session_id, turns=3)

        first = ingest_one_session(path, project_dir)
        assert first.action == "new"

        # Read back the first staged trace.
        staging_dir = get_project_traces_dir(project_dir)
        gen1_file = staging_dir / f"{first.trace_id}.jsonl"
        gen1_payload = _json.loads(gen1_file.read_text().strip())
        assert gen1_payload["generation_index"] == 1
        gen1_hash = gen1_payload["content_hash"]

        # Mark first gen as UPLOADED so the next ingest opens a new gen.
        state = StateManager(state_path=get_project_state_path(project_dir))
        state.set_trace_status(
            first.trace_id, TraceStatus.UPLOADED,
            session_id=session_id, file_path=str(gen1_file),
        )

        # Grow the JSONL.
        _append_turns(path, session_id, start=4, count=2)

        second = ingest_one_session(path, project_dir)
        assert second.action == "new_generation"
        assert second.trace_id != first.trace_id

        gen2_file = staging_dir / f"{second.trace_id}.jsonl"
        gen2_payload = _json.loads(gen2_file.read_text().strip())
        assert gen2_payload["generation_index"] == 2
        gen2_hash = gen2_payload["content_hash"]

        # Content hashes must differ — even beyond the new turns,
        # generation_index itself is part of the hashed payload.
        assert gen1_hash != gen2_hash


class TestReviewHelpers:
    def test_review_helpers_preserve_existing_session_ids(
        self, project_dir
    ) -> None:
        from opentraces.core.config import get_project_state_path
        from opentraces.core.review import (
            commit_bulk,
            reject_trace,
            stage_trace,
            unstage_trace,
        )

        state = StateManager(state_path=get_project_state_path(project_dir))
        seeded = [
            ("trace-stage", "sess-stage", TraceStatus.PARSED),
            ("trace-unstage", "sess-unstage", TraceStatus.STAGED),
            ("trace-reject", "sess-reject", TraceStatus.STAGED),
            ("trace-commit-a", "sess-commit-a", TraceStatus.STAGED),
            ("trace-commit-b", "sess-commit-b", TraceStatus.STAGED),
        ]
        for trace_id, session_id, status in seeded:
            state.set_trace_status(trace_id, status, session_id=session_id)

        stage_trace(state, "trace-stage")
        unstage_trace(state, "trace-unstage")
        reject_trace(state, "trace-reject", with_session_kwarg=True)
        commit_bulk(state, ["trace-commit-a", "trace-commit-b"], "bulk")

        assert state.get_trace("trace-stage").session_id == "sess-stage"
        assert state.get_trace("trace-unstage").session_id == "sess-unstage"
        assert state.get_trace("trace-reject").session_id == "sess-reject"
        assert state.get_trace("trace-commit-a").session_id == "sess-commit-a"
        assert state.get_trace("trace-commit-b").session_id == "sess-commit-b"


class TestProcessedFileOffsets:
    def test_should_reprocess_resets_offset_after_truncation(
        self, project_dir
    ) -> None:
        from opentraces.core.config import get_project_state_path
        from opentraces.core.state import ProcessedFile

        session_id = "sess-truncate"
        path = _write_jsonl(project_dir, session_id, turns=3)
        state = StateManager(state_path=get_project_state_path(project_dir))

        stat = path.stat()
        state.mark_file_processed(
            ProcessedFile(
                file_path=str(path),
                inode=stat.st_ino,
                mtime=stat.st_mtime,
                last_byte_offset=stat.st_size,
            )
        )

        # Rewrite the file in place with fewer bytes. The prior byte offset
        # is now stale and must not be reused.
        path.write_text(
            "\n".join(json.dumps(line) for line in _turn(1, session_id)) + "\n"
        )

        should_process, offset = state.should_reprocess(str(path))
        assert should_process is True
        assert offset == 0


class TestAutoReviewPromotion:
    def test_classifier_flags_force_review_in_auto_policy(
        self, project_dir, monkeypatch
    ) -> None:
        """A flagged classifier verdict promotes the trace to STAGED even
        under ``review_policy=auto``. Stubs the classifier tool's ``judge``
        method to return a synthetic flag — the rest of the pipeline runs
        normally."""
        from opentraces.core.config import Config, get_project_state_path
        from opentraces.core.ingest import ingest_one_session
        from opentraces.security.tools import Verdict
        from opentraces.security.tools.classifier_tool import ClassifierJudge

        project_cfg = json.loads((project_dir / ".opentraces.json").read_text())
        project_cfg["review_policy"] = "auto"
        (project_dir / ".opentraces.json").write_text(json.dumps(project_cfg))

        def _stub_judge(self, record, ctx):
            return Verdict(
                name="classifier",
                summary="flagged for review",
                decision="flagged",
                payload={
                    "flags": [
                        {
                            "pattern": "manual_review",
                            "matched_text": "internal",
                            "reason": "test stub",
                            "severity": "medium",
                        }
                    ],
                    "risk_score": 0.5,
                    "sensitivity": "medium",
                },
            )

        monkeypatch.setattr(ClassifierJudge, "judge", _stub_judge)

        session_id = "sess-auto-review"
        path = _write_jsonl(project_dir, session_id, turns=3)
        cfg = Config()
        cfg.security.classifier.enabled = True
        result = ingest_one_session(path, project_dir, cfg=cfg)

        assert result.action == "new"

        from opentraces.core.state import StateManager

        state = StateManager(state_path=get_project_state_path(project_dir))
        entry = state.get_trace(result.trace_id)
        assert entry is not None
        assert entry.status == TraceStatus.STAGED.value


# --------------------------------------------------------------------------- #
# Per-project exclusion gate (release-gate CAP-4: `excluded` honored at the
# shared ingest choke point, not just by the Pi bridge)
# --------------------------------------------------------------------------- #

class TestExcludedProjectGate:
    def _exclude(self, project_dir: Path) -> bytes:
        """Flip the project's marker to excluded; return its exact bytes."""
        marker = project_dir / ".opentraces.json"
        data = json.loads(marker.read_text())
        data["excluded"] = True
        marker.write_text(json.dumps(data))
        return marker.read_bytes()

    def test_ingest_skips_excluded_project_and_stages_nothing(
        self, project_dir
    ) -> None:
        from opentraces.core.config import PROJECTS_DIR
        from opentraces.core.ingest import ingest_one_session

        marker_before = self._exclude(project_dir)
        path = _write_jsonl(project_dir, "sess-excluded", turns=3)

        result = ingest_one_session(path, project_dir)

        assert result.action == "skipped"
        assert "excluded" in (result.error or "")
        assert result.trace_id is None
        # Nothing staged anywhere under the global projects dir.
        assert list(PROJECTS_DIR.glob("**/traces/*.jsonl")) == []
        # The marker is byte-identical — no agent backfill, no rewrite.
        assert (project_dir / ".opentraces.json").read_bytes() == marker_before

    def test_ingest_skips_bare_excluded_marker_without_minting_state(
        self, tmp_path
    ) -> None:
        """A bare committed ``{"excluded": true}`` marker (no project_id)
        must skip cleanly — not error out, not mint a project_id, not
        create per-project global state."""
        from opentraces.core.config import PROJECTS_DIR
        from opentraces.core.ingest import ingest_one_session

        proj = tmp_path / "bare-excluded"
        proj.mkdir()
        marker = proj / ".opentraces.json"
        marker.write_text('{"excluded": true}')
        marker_before = marker.read_bytes()
        path = _write_jsonl(proj, "sess-bare-excluded", turns=3)

        result = ingest_one_session(path, proj)

        assert result.action == "skipped"
        assert "excluded" in (result.error or "")
        assert marker.read_bytes() == marker_before
        # No slug dir, no ingest-locks, nothing minted for this project.
        assert list(PROJECTS_DIR.iterdir()) == []

    def test_scan_project_excluded_returns_empty_report(
        self, project_dir, monkeypatch
    ) -> None:
        from opentraces.core.ingest import scan_project

        marker_before = self._exclude(project_dir)
        path = _write_jsonl(project_dir, "sess-excluded-scan", turns=3)

        # Discovery must not even be consulted for an excluded project.
        def _boom(_repo):
            raise AssertionError("corpus discovery ran for an excluded project")

        monkeypatch.setattr(
            "opentraces.core.ingest.discover_claude_jsonl_corpus", _boom
        )
        report = scan_project(project_dir)

        assert report.results == []
        assert (project_dir / ".opentraces.json").read_bytes() == marker_before


# --------------------------------------------------------------------------- #
# Marker read-purity on the capture hot path (issue #60 item 2)
# --------------------------------------------------------------------------- #

def test_ingest_does_not_rewrite_marker_bytes(tmp_path) -> None:
    """The capture hot path must never rewrite the user's (often
    git-committed) ``.opentraces.json``. A legacy-shaped marker triggers
    in-memory normalization on load — that normalization must NOT be
    persisted by a pure read; only explicit write verbs heal the file."""
    from opentraces.core.ingest import ingest_one_session

    proj = tmp_path / "legacy-marker-proj"
    proj.mkdir()
    marker = proj / ".opentraces.json"
    marker.write_text(json.dumps({
        "marker_version": "1",
        "project_id": "legacy-proj-0001",
        "review_policy": "review",
        "remote": "test/legacy",
        "visibility": "private",
        "agents": ["claude-code"],
    }, indent=2))
    marker_before = marker.read_bytes()
    path = _write_jsonl(proj, "sess-marker-purity", turns=3)

    result = ingest_one_session(path, proj)

    assert result.action == "new"
    assert marker.read_bytes() == marker_before
