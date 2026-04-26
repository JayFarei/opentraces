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
        assert [event.event_type for event in events] == [
            "trace_step_window_opened",
            "trace_snapshot_created",
            "trace_step_window_closed",
            "trace_step_window_opened",
            "trace_snapshot_created",
            "trace_step_window_closed",
        ]
        snapshots = [
            event for event in events
            if event.event_type == "trace_snapshot_created"
        ]
        assert {event.step_index for event in snapshots} == {read_step, write_step}
        assert snapshots[0].payload["tree_id"] == before_tree
        assert snapshots[0].payload["capture_status"] == "hook_only"
        assert "hook_only" in snapshots[0].payload["limitations"]
        assert snapshots[1].payload["tree_id"] == after_tree
        assert snapshots[1].payload["capture_status"] == "captured"
        for snapshot in snapshots:
            subprocess.run(
                ["git", "cat-file", "-e", snapshot.payload["tree_id"]["hex"]],
                cwd=project_dir,
                check=True,
            )
            ref = (
                f"refs/opentraces/local/traces/{result.trace_id}/1"
                f"/snapshots/step_{snapshot.step_index}"
            )
            subprocess.run(["git", "rev-parse", "--verify", ref], cwd=project_dir, check=True)

        refreshed = ingest_one_session(session_path, project_dir, reparse=True)
        assert refreshed.action == "refreshed"
        assert refreshed.trace_id == result.trace_id
        assert [
            event.event_id for event in read_events(project_dir)
            if event.trace_id == result.trace_id
        ] == [event.event_id for event in events]

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
        from types import SimpleNamespace

        from opentraces.core.config import Config, get_project_state_path
        from opentraces.core.ingest import ingest_one_session

        project_cfg = json.loads((project_dir / ".opentraces.json").read_text())
        project_cfg["review_policy"] = "auto"
        (project_dir / ".opentraces.json").write_text(json.dumps(project_cfg))

        monkeypatch.setattr(
            "opentraces.core.pipeline.two_pass_scan",
            lambda record: (
                SimpleNamespace(matches=[]),
                SimpleNamespace(matches=[]),
            ),
        )
        monkeypatch.setattr(
            "opentraces.core.pipeline.apply_redactions",
            lambda record: 0,
        )
        monkeypatch.setattr(
            "opentraces.core.pipeline.classify_trace_record",
            lambda record, sensitivity: SimpleNamespace(flags=["manual-review"]),
        )

        session_id = "sess-auto-review"
        path = _write_jsonl(project_dir, session_id, turns=3)
        result = ingest_one_session(path, project_dir, cfg=Config())

        assert result.action == "new"

        from opentraces.core.state import StateManager

        state = StateManager(state_path=get_project_state_path(project_dir))
        entry = state.get_trace(result.trace_id)
        assert entry is not None
        assert entry.status == TraceStatus.STAGED.value
