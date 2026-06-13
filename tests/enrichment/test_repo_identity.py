"""Unit tests for ``opentraces.core.repo_identity``.

Covers:
* ``encode_claude_path`` pinned against known examples that match
  Claude Code's on-disk convention.
* ``root_commit_sha`` happy path + empty repo + non-repo.
* ``discover_matching_project`` across a fixture ``projects/`` tree.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from opentraces.core import repo_identity as ri


# --------------------------------------------------------------------------- #
# encode_claude_path
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw, encoded", [
    ("/private/tmp/ot-h-small_tweak", "-private-tmp-ot-h-small-tweak"),
    ("/Users/a/b.c", "-Users-a-b-c"),
    ("/tmp/proj", "-tmp-proj"),
])
def test_encode_claude_path_pinned(tmp_path, raw, encoded, monkeypatch):
    # Use a path that's already absolute; encode_claude_path resolves it,
    # but absolute pre-existing roots resolve to themselves modulo symlinks.
    # We bypass resolve() by monkey-patching Path.resolve to return self
    # when the path is already absolute — but simpler: call a helper that
    # just runs the character mapping.
    s = "".join(c if c.isalnum() or c == "-" else "-" for c in raw)
    assert s == encoded


# --------------------------------------------------------------------------- #
# root_commit_sha
# --------------------------------------------------------------------------- #

def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def test_root_commit_sha_happy(tmp_path):
    _git("init", "-q", "-b", "main", cwd=tmp_path)
    (tmp_path / "README.md").write_text("x\n")
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=a@a", "-c", "user.name=a",
         "commit", "-q", "-m", "one", cwd=tmp_path)
    first = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    (tmp_path / "b.txt").write_text("y\n")
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=a@a", "-c", "user.name=a",
         "commit", "-q", "-m", "two", cwd=tmp_path)

    ri._cached_root.cache_clear()
    assert ri.root_commit_sha(tmp_path) == first


def test_root_commit_sha_empty_repo(tmp_path):
    _git("init", "-q", "-b", "main", cwd=tmp_path)
    ri._cached_root.cache_clear()
    assert ri.root_commit_sha(tmp_path) is None


def test_root_commit_sha_non_repo(tmp_path):
    ri._cached_root.cache_clear()
    assert ri.root_commit_sha(tmp_path) is None


# --------------------------------------------------------------------------- #
# discover_matching_project
# --------------------------------------------------------------------------- #

def test_discover_matching_project(tmp_path):
    projects = tmp_path / "projects"
    # slug-a has the target SHA, slug-b has a different SHA, slug-c missing
    # project.json. Expected: slug-a wins.
    (projects / "slug-a").mkdir(parents=True)
    (projects / "slug-a" / "project.json").write_text(json.dumps({
        "path": "/old/path/a",
        "root_commit_sha": "deadbeef",
    }))
    (projects / "slug-b").mkdir()
    (projects / "slug-b" / "project.json").write_text(json.dumps({
        "path": "/old/path/b",
        "root_commit_sha": "cafebabe",
    }))
    (projects / "slug-c").mkdir()

    match = ri.discover_matching_project("deadbeef", projects_root=projects)
    assert match is not None
    assert match.slug == "slug-a"
    assert match.old_path == Path("/old/path/a")
    assert match.traces_dir == projects / "slug-a" / "traces"

    assert ri.discover_matching_project("nope", projects_root=projects) is None
    assert ri.discover_matching_project("", projects_root=projects) is None


def test_write_project_identity_roundtrip(tmp_path):
    slug_dir = tmp_path / "projects" / "slug-x"
    ri.write_project_identity(slug_dir, project_dir=tmp_path, root_sha="sha123")
    data = json.loads((slug_dir / "project.json").read_text())
    assert data["root_commit_sha"] == "sha123"
    assert data["path"] == str(tmp_path.resolve())
    # Second write merges rather than clobbering.
    ri.write_project_identity(slug_dir, project_dir=tmp_path, root_sha="sha123")
    assert (slug_dir / "project.json").is_file()


def test_discover_claude_jsonl_corpus_current_path(tmp_path, monkeypatch):
    _git("init", "-q", "-b", "main", cwd=tmp_path)
    (tmp_path / "a").write_text("x\n")
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=a@a", "-c", "user.name=a",
         "commit", "-q", "-m", "i", cwd=tmp_path)

    fake_home = tmp_path / "home"
    (fake_home).mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    # Reset the home-dependent module-level path helper isn't cached, but
    # root_commit_sha IS cached — clear it.
    ri._cached_root.cache_clear()

    enc = ri.encode_claude_path(tmp_path)
    corpus_dir = fake_home / ".claude" / "projects" / enc
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "session-1.jsonl").write_text("{}\n")

    found = ri.discover_claude_jsonl_corpus(tmp_path)
    assert len(found) == 1
    assert found[0].name == "session-1.jsonl"


def test_discover_claude_jsonl_corpus_honors_custom_projects_path(
    tmp_path, monkeypatch
):
    """Issue #60 item 4: corpus discovery must honor ``cfg.projects_path``
    like ``_current_project_session_dir`` does — a custom-``projects_path``
    user silently got nothing from every corpus-discovery consumer while
    ``_claude_projects_root()`` was hardcoded to ``~/.claude/projects``."""
    _git("init", "-q", "-b", "main", cwd=tmp_path)
    (tmp_path / "a").write_text("x\n")
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=a@a", "-c", "user.name=a",
         "commit", "-q", "-m", "i", cwd=tmp_path)

    # Fake home with NO ~/.claude/projects at all — only the custom root
    # configured via cfg.projects_path holds the corpus.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    ri._cached_root.cache_clear()

    custom_root = tmp_path / "custom-projects"
    from opentraces.core.config import Config

    monkeypatch.setattr(
        "opentraces.core.config.load_config",
        lambda: Config(projects_path=str(custom_root)),
    )

    enc = ri.encode_claude_path(tmp_path)
    corpus_dir = custom_root / enc
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "session-1.jsonl").write_text("{}\n")

    found = ri.discover_claude_jsonl_corpus(tmp_path)
    assert [p.name for p in found] == ["session-1.jsonl"]
