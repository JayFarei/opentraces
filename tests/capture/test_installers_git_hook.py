"""Tests for the minimal git hook installer (plan 041 phase 3 R21)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from opentraces.capture.git import install as git_hook


def _sh(cmd: list[str], cwd: Path) -> None:
    subprocess.check_call(
        cmd, cwd=str(cwd),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@pytest.fixture
def repo(tmp_path):
    _sh(["git", "init", "-q", "-b", "main"], tmp_path)
    _sh(["git", "config", "user.email", "t@t.t"], tmp_path)
    _sh(["git", "config", "user.name", "t"], tmp_path)
    return tmp_path


class TestInstaller:
    def test_install_creates_owned_hook_and_chain(self, repo):
        assert git_hook.install(repo)
        st = git_hook.status(repo)
        assert st["installed"] is True
        assert st["owned_hook_present"] is True
        assert st["chain_present"] is True

    def test_install_is_idempotent(self, repo):
        git_hook.install(repo)
        git_hook.install(repo)
        pc = repo / ".git" / "hooks" / "post-commit"
        text = pc.read_text()
        # Only one chain block.
        assert text.count(git_hook.CHAIN_BEGIN) == 1

    def test_install_preserves_existing_hook_body(self, repo):
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        pc = hooks / "post-commit"
        pc.write_text("#!/usr/bin/env sh\n# my husky code\necho hi\n")
        pc.chmod(0o755)

        git_hook.install(repo)
        text = pc.read_text()
        assert "my husky code" in text
        assert git_hook.CHAIN_BEGIN in text

    def test_remove_clears_chain_and_hook(self, repo):
        git_hook.install(repo)
        git_hook.remove(repo)
        st = git_hook.status(repo)
        assert st["installed"] is False
        # owned hook gone
        assert not (repo / ".git" / "hooks" / git_hook.HOOK_FILENAME).exists()

    def test_remove_preserves_sibling_hook_body(self, repo):
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        pc = hooks / "post-commit"
        pc.write_text("#!/usr/bin/env sh\n# my husky code\necho hi\n")
        git_hook.install(repo)
        git_hook.remove(repo)
        text = pc.read_text()
        assert "my husky code" in text
        assert git_hook.CHAIN_BEGIN not in text

    def test_owned_hook_is_executable(self, repo):
        git_hook.install(repo)
        owned = repo / ".git" / "hooks" / git_hook.HOOK_FILENAME
        import os
        assert os.access(owned, os.X_OK)

    def test_not_a_git_repo_returns_false(self, tmp_path):
        assert git_hook.install(tmp_path) is False
        assert git_hook.status(tmp_path)["installed"] is False
