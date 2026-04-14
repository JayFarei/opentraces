"""Unit tests for the phase-6 first-run backfill prompt in ``ot init``.

We test the ``_plan043_finalize_identity`` helper directly — it's the
surface that ``init`` uses, and isolating it sidesteps the heavy
registration/hook-install dance of the full command.
"""
from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path

import pytest

from opentraces.cli import _plan043_finalize_identity
from opentraces.core import config as c
from opentraces.core import repo_identity as ri


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def initialized_repo(tmp_path, monkeypatch):
    """A repo with a single commit + a fake ~/.claude corpus + an
    ``.opentraces.json`` marker in place. ``home`` is redirected to
    tmp_path/home so we don't write anywhere real."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    # Also reset the PROJECTS_DIR-sensitive cache via re-import isn't needed;
    # the helper reads config.get_project_dir which calls Path.home() under
    # paths.PROJECTS_DIR at import time — so let's monkey-patch that too.
    from opentraces.core import paths as _paths
    monkeypatch.setattr(_paths, "PROJECTS_DIR", fake_home / ".opentraces" / "projects")
    monkeypatch.setattr(_paths, "OPENTRACES_DIR", fake_home / ".opentraces")
    monkeypatch.setattr(_paths, "CONFIG_PATH", fake_home / ".opentraces" / "config.json")
    monkeypatch.setattr(_paths, "CREDENTIALS_PATH", fake_home / ".opentraces" / "credentials")
    # config.py binds the names at import-time; patch those copies too.
    from opentraces.core import config as _cfg
    monkeypatch.setattr(_cfg, "PROJECTS_DIR", fake_home / ".opentraces" / "projects")
    monkeypatch.setattr(_cfg, "OPENTRACES_DIR", fake_home / ".opentraces")
    monkeypatch.setattr(_cfg, "CONFIG_PATH", fake_home / ".opentraces" / "config.json")
    # repo_identity imports PROJECTS_DIR at module level too.
    monkeypatch.setattr(ri, "PROJECTS_DIR", fake_home / ".opentraces" / "projects")

    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    (repo / "README.md").write_text("x\n")
    _git("add", ".", cwd=repo)
    _git("-c", "user.email=a@a", "-c", "user.name=a",
         "commit", "-q", "-m", "init", cwd=repo)

    # Drop an .opentraces.json so get_project_dir works.
    c.save_project_config(repo, {"review_policy": "review"})

    # Pre-stage a Claude JSONL corpus under the encoded current path.
    enc = ri.encode_claude_path(repo)
    corpus = fake_home / ".claude" / "projects" / enc
    corpus.mkdir(parents=True)
    (corpus / "session-a.jsonl").write_text("{}\n")

    ri._cached_root.cache_clear()
    return repo


def test_prompt_yes_records_decision(initialized_repo, monkeypatch, capsys):
    repo = initialized_repo
    # Force tty + stdin "Y\n".
    monkeypatch.setattr("sys.stdin", io.StringIO("Y\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    # click.prompt reads via click's own input; monkeypatch click's prompt.
    import click as _click
    monkeypatch.setattr(_click, "prompt", lambda *a, **kw: "Y")

    # Stub backfill so we don't need a full pipeline.
    from opentraces.core import backfill as _bf
    class _R:
        commits_processed = 1
        attributed_lines = 10
    monkeypatch.setattr(_bf, "run_full", lambda *a, **kw: _R())

    _plan043_finalize_identity(repo)
    assert c.get_first_run_backfill_decision(repo) == "Y"
    assert c.get_root_commit_sha(repo)  # populated


def test_prompt_no_records_declined(initialized_repo, monkeypatch):
    repo = initialized_repo
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    import click as _click
    monkeypatch.setattr(_click, "prompt", lambda *a, **kw: "n")
    _plan043_finalize_identity(repo)
    assert c.get_first_run_backfill_decision(repo) == "declined"


def test_prompt_never_records_never(initialized_repo, monkeypatch):
    repo = initialized_repo
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    import click as _click
    monkeypatch.setattr(_click, "prompt", lambda *a, **kw: "never")
    _plan043_finalize_identity(repo)
    assert c.get_first_run_backfill_decision(repo) == "never"


def test_non_tty_skips_prompt(initialized_repo, monkeypatch):
    repo = initialized_repo
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    # If click.prompt is called, fail loudly.
    import click as _click
    def _boom(*a, **kw):
        raise AssertionError("prompt should not be called in non-tty mode")
    monkeypatch.setattr(_click, "prompt", _boom)
    _plan043_finalize_identity(repo)
    assert c.get_first_run_backfill_decision(repo) is None
    # But the root SHA must still be recorded.
    assert c.get_root_commit_sha(repo)


def test_already_decided_does_not_reprompt(initialized_repo, monkeypatch):
    repo = initialized_repo
    c.set_first_run_backfill_decision(repo, "never")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    import click as _click
    def _boom(*a, **kw):
        raise AssertionError("prompt should not be called when decision is set")
    monkeypatch.setattr(_click, "prompt", _boom)
    _plan043_finalize_identity(repo)
    assert c.get_first_run_backfill_decision(repo) == "never"
