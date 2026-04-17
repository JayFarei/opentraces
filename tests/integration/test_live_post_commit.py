"""Plan 047 U5/U6 — live post-commit path integration tests.

The existing attribution harness (`tests/integration/harness/…`) runs
real Claude CLI sessions in tmux and drives the *backfill/audit-ref*
path. None of those scenarios exercise the **live** path:

    git commit → .git/hooks/post-commit → opentraces _run-post-commit-hook
                                          └─ run_for_repo()
                                             ├─ load_trace_records()
                                             ├─ correlate()
                                             └─ notes_store.append()

This module fills that gap without needing tmux or a real Claude
session. It seeds a scratch opted-in git repo with a synthetic
``TraceRecord`` whose tool_calls match the commit's diff, invokes
``run_for_repo`` directly (mirroring what the shell shim does), and
asserts on the three live-path artifacts:

  1. ``refs/notes/opentraces`` for the commit
  2. ``.git/opentraces-hook.log`` (structured JSON per invocation)
  3. ``opentraces blame <sha>`` headline (diff-scoped)

Scenarios:

  * ``test_posthook_single_session_tool_emitted`` — happy path
  * ``test_posthook_session_not_in_inbox_yet`` — the failure mode that
    shipped silently. Locks in behavior so we can change it
    deliberately, not accidentally.
  * ``test_posthook_multi_trace_window`` — two traces in window, both
    touch the commit's files; both get correlated.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def ot_home(tmp_path, monkeypatch):
    """Redirect opentraces' per-user roots to a tmpdir.

    ``opentraces.core.paths`` binds ``OPENTRACES_DIR`` / ``PROJECTS_DIR``
    at import time from ``Path.home()``, so setting ``HOME`` after import
    has no effect. Patch both the ``paths`` module and the re-bindings
    ``core.config`` pulled in on its own import.
    """
    root = tmp_path / "ot-home"
    ot_dir = root / ".opentraces"
    projects = ot_dir / "projects"
    projects.mkdir(parents=True)

    import opentraces.core.paths as _paths
    import opentraces.core.config as _config
    monkeypatch.setattr(_paths, "OPENTRACES_DIR", ot_dir)
    monkeypatch.setattr(_paths, "PROJECTS_DIR", projects)
    monkeypatch.setattr(_paths, "CONFIG_PATH", ot_dir / "config.json")
    monkeypatch.setattr(_paths, "CREDENTIALS_PATH", ot_dir / "credentials")
    monkeypatch.setattr(_config, "PROJECTS_DIR", projects)
    monkeypatch.setattr(_config, "OPENTRACES_DIR", ot_dir)
    return root


# --------------------------------------------------------------------------- #
# Seeding helpers — minimal TraceRecord builder + opted-in repo scaffolding
# --------------------------------------------------------------------------- #

def _init_repo(repo: Path) -> None:
    """Plain git init with a seed commit so blame has something to walk."""
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"],
                   cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _opt_in(repo: Path) -> None:
    """Write the ``.opentraces.json`` marker so ``get_project_traces_dir``
    resolves instead of raising ``NotOptedInError``."""
    from opentraces.core.config import _write_marker
    _write_marker(repo, uuid.uuid4().hex, {})


def _now_iso(offset_seconds: float = 0.0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    ).isoformat()


def _build_trace_record(
    *,
    trace_id: str,
    file_path: str,
    new_content: str,
    old_content: str = "",
    tool: str = "Write",
    timestamp_end: str | None = None,
) -> dict:
    """Assemble the minimal dict that ``TraceRecord.model_validate`` accepts
    and that ``correlator.correlate`` will tie to a commit's diff.

    Keeping this inline (vs a shared factory) so the scenarios stay
    self-documenting: you can read the TraceRecord shape next to the
    assertions that depend on it.
    """
    if tool == "Write":
        tool_input = {"file_path": file_path, "content": new_content}
    elif tool == "Edit":
        tool_input = {
            "file_path": file_path,
            "old_string": old_content,
            "new_string": new_content,
        }
    else:
        raise ValueError(f"unsupported tool: {tool}")

    now = timestamp_end or _now_iso()
    start = _now_iso(offset_seconds=-10.0)
    return {
        "schema_version": "0.1.0",
        "trace_id": trace_id,
        "session_id": trace_id,  # keep 1:1 for test simplicity
        "content_hash": "0" * 64,
        "timestamp_start": start,
        "timestamp_end": now,
        "task": {"description": "test", "source": "synthetic"},
        "agent": {"name": "claude-code", "version": "test",
                   "model": "claude-opus-4-6"},
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
                "tool_name": tool,
                "input": tool_input,
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


def _write_trace_to_inbox(repo: Path, record: dict) -> Path:
    """Drop the record as a one-line JSONL file in the project's staging
    dir — same shape the Stop hook writes in production."""
    from opentraces.core.config import get_project_traces_dir
    staging = get_project_traces_dir(repo)
    staging.mkdir(parents=True, exist_ok=True)
    out = staging / f"{record['trace_id']}.jsonl"
    out.write_text(json.dumps(record) + "\n")
    return out


def _edit_and_commit(repo: Path, rel_path: str, content: str, msg: str) -> str:
    """Write ``content`` to ``rel_path``, stage, commit. Returns full SHA."""
    p = repo / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    subprocess.run(["git", "add", rel_path], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
    ).strip()


def _read_notes(repo: Path, sha: str) -> list[str]:
    """Read the lines attached to ``sha`` on refs/notes/opentraces."""
    res = subprocess.run(
        ["git", "notes", "--ref=refs/notes/opentraces", "show", sha],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if res.returncode != 0:
        return []
    return [ln for ln in res.stdout.splitlines() if ln.strip()]


def _read_hook_log(repo: Path) -> list[dict]:
    log = repo / ".git" / "opentraces-hook.log"
    if not log.is_file():
        return []
    return [json.loads(l) for l in log.read_text().splitlines() if l.strip()]


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #

def test_posthook_single_session_tool_emitted(tmp_path, monkeypatch, ot_home):
    """One session wrote ``hello.txt`` with Write. The commit stages
    that exact content. The post-commit hook should:

      * log one structured entry with candidates=1, notes_written=true
      * write one ``opentraces:trace_id=<tid>`` line to
        refs/notes/opentraces for this commit
      * correlate() returns ``tool_emitted``
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "proj"
    repo.mkdir()
    _init_repo(repo)
    _opt_in(repo)

    trace_id = "a" * 16
    content = "hello world\nline two\n"
    record = _build_trace_record(
        trace_id=trace_id,
        file_path="hello.txt",
        new_content=content,
        tool="Write",
    )
    _write_trace_to_inbox(repo, record)

    sha = _edit_and_commit(repo, "hello.txt", content, "add hello")

    from opentraces.capture.git.post_commit import run_for_repo
    run_for_repo(repo)

    # (1) Hook log: one entry, candidates=1, notes_written=true,
    # verdict tier is tool_emitted.
    entries = _read_hook_log(repo)
    assert len(entries) == 1, entries
    entry = entries[0]
    assert entry["error"] is None, entry
    assert entry["candidates"] == 1
    assert entry["notes_written"] is True
    assert entry["sha"] == sha
    verdicts = entry["verdicts"]
    assert len(verdicts) == 1
    assert verdicts[0]["trace_id"] == trace_id
    assert verdicts[0]["tier"] == "tool_emitted"

    # (2) Notes ref got the line.
    notes = _read_notes(repo, sha)
    assert any(trace_id in n for n in notes), notes

    # (3) Blame renders the diff-scoped headline. We don't run the full
    # attribution pipeline here (that would need the audit ref), so
    # assert on the denominator helper directly — the same function the
    # CLI renders — to confirm our diff math matches the commit.
    from opentraces.enrichment.git.blame import diff_line_count
    # "hello world\nline two\n" = 2 inserted lines.
    assert diff_line_count(repo, sha) == 2


def test_posthook_session_not_in_inbox_yet(tmp_path, monkeypatch, ot_home):
    """The failure mode we shipped silently: commit fires before the
    session's Stop hook has written its TraceRecord to the inbox.

    Expected (documented, locked-in) behavior:

      * hook log has one entry with ``reason =
        no_traces_in_inbox_window`` and ``candidates=0``
      * no notes written
      * no exception surfaced

    This scenario is not a bug report — it encodes the current
    contract so any future change (e.g. reading the live JSONL
    directly) shows up as a deliberate assertion update.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "proj"
    repo.mkdir()
    _init_repo(repo)
    _opt_in(repo)

    # No TraceRecord written to the inbox.
    sha = _edit_and_commit(repo, "hello.txt", "hi\n", "add hello")

    from opentraces.capture.git.post_commit import run_for_repo
    run_for_repo(repo)

    entries = _read_hook_log(repo)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["error"] is None
    assert entry["candidates"] == 0
    # Either the staging dir wasn't created yet (pre-first-trace) or the
    # dir exists but is empty. Both are the same user-facing outcome:
    # "hook ran, nothing to correlate".
    assert entry["reason"] in ("no_traces_in_inbox_window", "no_staging_dir")
    assert entry["notes_written"] is False
    assert entry["sha"] == sha

    assert _read_notes(repo, sha) == []


def test_posthook_multi_trace_window(tmp_path, monkeypatch, ot_home):
    """Two recent sessions, both within the 1h window, both touched
    files the commit changed. The hook must correlate both and write a
    note line per trace.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "proj"
    repo.mkdir()
    _init_repo(repo)
    _opt_in(repo)

    alpha_id = "a" * 16
    beta_id = "b" * 16
    alpha_content = "alpha one\nalpha two\n"
    beta_content = "beta one\nbeta two\nbeta three\n"

    _write_trace_to_inbox(repo, _build_trace_record(
        trace_id=alpha_id, file_path="alpha.txt",
        new_content=alpha_content, tool="Write",
    ))
    _write_trace_to_inbox(repo, _build_trace_record(
        trace_id=beta_id, file_path="beta.txt",
        new_content=beta_content, tool="Write",
    ))

    # Stage both files and commit — one commit, two traces should
    # correlate.
    (repo / "alpha.txt").write_text(alpha_content)
    (repo / "beta.txt").write_text(beta_content)
    subprocess.run(["git", "add", "alpha.txt", "beta.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add both"],
                   cwd=repo, check=True)
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
    ).strip()

    from opentraces.capture.git.post_commit import run_for_repo
    run_for_repo(repo)

    entries = _read_hook_log(repo)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["error"] is None
    assert entry["candidates"] == 2
    assert entry["notes_written"] is True
    verdicts = {v["trace_id"]: v["tier"] for v in entry["verdicts"]}
    assert verdicts.get(alpha_id) == "tool_emitted"
    assert verdicts.get(beta_id) == "tool_emitted"

    notes = _read_notes(repo, sha)
    combined = "\n".join(notes)
    assert alpha_id in combined, notes
    assert beta_id in combined, notes


def test_posthook_out_of_window_trace_ignored(tmp_path, monkeypatch, ot_home):
    """A trace whose ``timestamp_end`` falls outside the 2h window
    passed to ``run_for_repo`` must not be considered a candidate.

    Regression guard against accidentally widening the window and
    matching stale traces to unrelated commits.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "proj"
    repo.mkdir()
    _init_repo(repo)
    _opt_in(repo)

    stale_id = "c" * 16
    content = "hi\n"
    _write_trace_to_inbox(repo, _build_trace_record(
        trace_id=stale_id, file_path="hello.txt",
        new_content=content, tool="Write",
        timestamp_end=(
            datetime.now(timezone.utc) - timedelta(hours=24)
        ).isoformat(),
    ))

    _edit_and_commit(repo, "hello.txt", content, "add hello")

    from opentraces.capture.git.post_commit import run_for_repo
    run_for_repo(repo)

    entries = _read_hook_log(repo)
    assert len(entries) == 1
    assert entries[0]["candidates"] == 0
    assert entries[0]["reason"] == "no_traces_in_inbox_window"
