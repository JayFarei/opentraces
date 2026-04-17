"""Persistence + backfill round-trip tests for the post-commit pipeline.

These are the tests we didn't have when the "blame empty" bug shipped:
assertions that ``git_links`` and lifecycle updates mutate the on-disk
JSONL, not just the in-memory TraceRecord. Plus a backfill scenario
that exercises the historical-commit walker the hook can't reach.

Layout:

* ``test_live_hook_persists_git_links`` — run_for_repo writes git_links
  back to the trace file after correlation.
* ``test_backfill_merges_wider_window`` — commits landing hours after a
  session still get linked via ``backfill(window_hours=...)``.
* ``test_backfill_is_idempotent`` — running it twice produces identical
  JSONL and no duplicate notes lines.
* ``test_persist_updates_is_noop_when_file_missing`` — stale trace_ids
  in the results list don't blow up the run.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from opentraces.capture.git import post_commit as _pc


@pytest.fixture
def ot_home(tmp_path, monkeypatch):
    root = tmp_path / "ot-home"
    ot_dir = root / ".opentraces"
    projects = ot_dir / "projects"
    projects.mkdir(parents=True)
    import opentraces.core.config as _config
    import opentraces.core.paths as _paths
    monkeypatch.setattr(_paths, "OPENTRACES_DIR", ot_dir)
    monkeypatch.setattr(_paths, "PROJECTS_DIR", projects)
    monkeypatch.setattr(_paths, "CONFIG_PATH", ot_dir / "config.json")
    monkeypatch.setattr(_paths, "CREDENTIALS_PATH", ot_dir / "credentials")
    monkeypatch.setattr(_config, "PROJECTS_DIR", projects)
    monkeypatch.setattr(_config, "OPENTRACES_DIR", ot_dir)
    return root


def _now_iso(offset_seconds: float = 0.0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    ).isoformat()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"],
                   cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _opt_in(repo: Path) -> None:
    from opentraces.core.config import _write_marker
    _write_marker(repo, uuid.uuid4().hex, {})


def _build_record(*, trace_id: str, file_path: str, content: str,
                  timestamp_end: str | None = None) -> dict:
    now = timestamp_end or _now_iso()
    return {
        "schema_version": "0.1.0",
        "trace_id": trace_id,
        "session_id": trace_id,
        "content_hash": "0" * 64,
        "timestamp_start": _now_iso(-10.0),
        "timestamp_end": now,
        "task": {"description": "test"},
        "agent": {"name": "claude-code", "model": "claude-opus-4-6"},
        "environment": {},
        "system_prompts": {},
        "tool_definitions": [],
        "steps": [{
            "step_index": 1,
            "role": "agent",
            "content": None,
            "model": "claude-opus-4-6",
            "agent_role": "main",
            "call_type": "main",
            "tools_available": [],
            "tool_calls": [{
                "tool_call_id": f"toolu_{trace_id[:12]}",
                "tool_name": "Write",
                "input": {"file_path": file_path, "content": content},
            }],
            "observations": [{
                "source_call_id": f"toolu_{trace_id[:12]}",
                "content": "ok",
                "status": "ok",
            }],
            "snippets": [],
            "timestamp": now,
        }],
        "outcome": {},
        "metrics": {},
        "security": {},
        "attribution": None,
        "dependencies": [],
        "metadata": {},
        "git_links": [],
        "lifecycle": "provisional",
    }


def _stage(repo: Path, record: dict) -> Path:
    from opentraces.core.config import get_project_traces_dir
    staging = get_project_traces_dir(repo)
    staging.mkdir(parents=True, exist_ok=True)
    p = staging / f"{record['trace_id']}.jsonl"
    p.write_text(json.dumps(record) + "\n")
    return p


def _commit(repo: Path, path: str, content: str, msg: str) -> str:
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    subprocess.run(["git", "add", path], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
    ).strip()


def _read_jsonl(path: Path) -> dict:
    return json.loads(path.read_text().strip().splitlines()[0])


def _notes_lines(repo: Path, sha: str) -> list[str]:
    r = subprocess.run(
        ["git", "notes", "--ref=refs/notes/opentraces", "show", sha],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


class TestLivePersistence:
    def test_hook_persists_git_links_to_jsonl(self, tmp_path, monkeypatch, ot_home):
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        trace_id = "a" * 16
        # Use realistic agent output — the correlator's specificity
        # threshold rejects short common lines to avoid phantom matches.
        content = (
            "def configure_dispatcher(app, handlers):\n"
            "    app.state.handlers = dict(handlers)\n"
        )
        p = _stage(repo, _build_record(
            trace_id=trace_id, file_path="hello.txt", content=content,
        ))
        sha = _commit(repo, "hello.txt", content, "add hello")

        _pc.run_for_repo(repo)

        rec = _read_jsonl(p)
        assert len(rec["git_links"]) == 1, rec["git_links"]
        link = rec["git_links"][0]
        assert link["revision"] == sha
        assert link["tier"] == "tool_emitted"
        assert rec["lifecycle"] == "final"

        notes = _notes_lines(repo, sha)
        assert any(trace_id in n for n in notes), notes

    def test_hook_persistence_is_idempotent(self, tmp_path, monkeypatch, ot_home):
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        trace_id = "b" * 16
        content = (
            "def write_persistence_record(state, payload):\n"
            "    state.store.append(dict(payload))\n"
        )
        p = _stage(repo, _build_record(
            trace_id=trace_id, file_path="a.txt", content=content,
        ))
        sha = _commit(repo, "a.txt", content, "add a")

        _pc.run_for_repo(repo)
        first = _read_jsonl(p)
        _pc.run_for_repo(repo)
        second = _read_jsonl(p)

        # Record content unchanged across runs.
        assert first == second
        # Notes also dedupe.
        assert len(_notes_lines(repo, sha)) == 1


class TestBackfillRoundTrip:
    def test_backfill_picks_up_commits_outside_live_window(
        self, tmp_path, monkeypatch, ot_home,
    ):
        """Live hook window is 2h; backfill's default is 24h. A commit
        landing 3h after a session should still pair via backfill."""
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        trace_id = "c" * 16
        # Session ended 3h before commit — outside live window, inside backfill.
        old_ts = _now_iso(-3 * 3600)
        content = (
            "def record_backfill_window(start, end, strategy):\n"
            "    return (start, end, strategy)\n"
        )
        p = _stage(repo, _build_record(
            trace_id=trace_id, file_path="w.txt",
            content=content, timestamp_end=old_ts,
        ))
        sha = _commit(repo, "w.txt", content, "add w")

        # Live hook should NOT find this trace in its 2h window.
        _pc.run_for_repo(repo)
        rec = _read_jsonl(p)
        assert rec["git_links"] == []

        # Backfill with a wider window picks it up.
        report = _pc.backfill(repo, max_commits=50, window_hours=6.0)
        assert report.traces_linked >= 1
        assert report.traces_persisted >= 1

        rec = _read_jsonl(p)
        assert any(link["revision"] == sha and link["tier"] == "tool_emitted"
                   for link in rec["git_links"]), rec["git_links"]

    def test_backfill_skips_traces_that_ended_after_commit(
        self, tmp_path, monkeypatch, ot_home,
    ):
        """Causality rule: a trace cannot have contributed to a commit
        that landed *before* the trace started. The old symmetric window
        (``abs(tts - ts) <= window``) credited future sessions to past
        commits whenever the agent's novel edit text coincidentally
        matched a diff hunk's added region — the root cause of a user
        bug where a 4-hour session appeared in the blame view of 20+
        commits that predated it."""
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        trace_id = "f" * 16
        content = (
            "def anachronism_detector(trace_ts, commit_ts):\n"
            "    assert trace_ts <= commit_ts, 'cannot credit a later trace'\n"
        )
        # Commit first.
        sha = _commit(repo, "a.txt", content, "add a first")
        # Trace ends AFTER the commit — 1h after.
        future_ts = _now_iso(+3600)
        p = _stage(repo, _build_record(
            trace_id=trace_id, file_path="a.txt",
            content=content, timestamp_end=future_ts,
        ))

        # Even with a permissive window, the causality filter must refuse.
        report = _pc.backfill(repo, max_commits=50, window_hours=48.0)
        rec = _read_jsonl(p)
        # No tier links for this (sha, trace) pair — the trace post-dates
        # the commit, so it cannot have contributed.
        assert all(link["revision"] != sha for link in rec["git_links"]), (
            f"trace {trace_id[:8]} was credited for commit {sha[:8]} "
            f"despite being dated after it; got {rec['git_links']}"
        )

    def test_backfill_is_idempotent(self, tmp_path, monkeypatch, ot_home):
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        trace_id = "d" * 16
        content = (
            "def apply_idempotent_migration(connection):\n"
            "    connection.execute('CREATE TABLE IF NOT EXISTS x(id INT)')\n"
        )
        p = _stage(repo, _build_record(
            trace_id=trace_id, file_path="i.txt",
            content=content, timestamp_end=_now_iso(-3 * 3600),
        ))
        sha = _commit(repo, "i.txt", content, "add i")

        _pc.backfill(repo, max_commits=50, window_hours=6.0)
        first = _read_jsonl(p)
        notes_first = _notes_lines(repo, sha)

        _pc.backfill(repo, max_commits=50, window_hours=6.0)
        second = _read_jsonl(p)
        notes_second = _notes_lines(repo, sha)

        assert first == second
        assert notes_first == notes_second
        # Only one note line per trace, even after two backfills.
        assert len([n for n in notes_second if trace_id in n]) == 1

    def test_persist_updates_ignores_missing_files(
        self, tmp_path, monkeypatch, ot_home,
    ):
        """Stale trace_ids in the results list must not crash the run — the
        writeback skips missing files silently (the common cause: a trace
        moved from inbox to staged between ``run()`` and the writeback)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        # Real staging dir, but our synthetic trace is never persisted —
        # model an in-memory record whose JSONL file doesn't exist.
        from opentraces.core.config import get_project_traces_dir
        staging = get_project_traces_dir(repo)
        staging.mkdir(parents=True, exist_ok=True)

        from opentraces_schema import TraceRecord
        ghost = _build_record(
            trace_id="ghost-trace-id-12345",
            file_path="x.txt", content="x",
        )
        record = TraceRecord.model_validate(ghost)
        # File does NOT exist; writeback must silently skip it.
        written = _pc.persist_trace_updates(staging, [record])
        assert written == 0
