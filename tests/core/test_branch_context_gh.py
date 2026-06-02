"""Tests for the ``gh`` subprocess wrappers in ``core.branch_context``.

These tests stub ``subprocess.run`` so they don't require a live GitHub
auth or network. They verify the argv shape, body-on-stdin convention,
and idempotency contract (``find_pr_for_branch`` returning ``None`` on
``gh pr view`` exit-1).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from opentraces.consumers import branch_pr as branch_context


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ─── find_pr_for_branch ────────────────────────────────────────────────────


def test_find_pr_for_branch_returns_none_when_gh_exits_one():
    """`gh pr view` exits 1 when there is no PR for the branch."""

    with (
        patch("opentraces.consumers.branch_pr.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "opentraces.consumers.branch_pr.subprocess.run",
            return_value=_FakeCompleted(returncode=1, stderr="no pull requests found"),
        ),
    ):
        assert branch_context.find_pr_for_branch(Path(".")) is None


def test_find_pr_for_branch_parses_gh_json_payload():
    payload = json.dumps({
        "number": 42, "url": "https://github.com/x/y/pull/42",
        "headRefName": "feat/x", "state": "OPEN",
    })
    with (
        patch("opentraces.consumers.branch_pr.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "opentraces.consumers.branch_pr.subprocess.run",
            return_value=_FakeCompleted(stdout=payload),
        ),
    ):
        info = branch_context.find_pr_for_branch(Path("."))
    assert info is not None
    assert info.number == 42
    assert info.url == "https://github.com/x/y/pull/42"
    assert info.head_ref == "feat/x"
    assert info.state == "OPEN"


def test_find_pr_for_branch_raises_when_gh_missing():
    with patch("opentraces.consumers.branch_pr.shutil.which", return_value=None):
        with pytest.raises(branch_context.GhUnavailableError):
            branch_context.find_pr_for_branch(Path("."))


# ─── create_pr ─────────────────────────────────────────────────────────────


def test_create_pr_passes_body_via_stdin_with_correct_argv():
    """create_pr issues `gh pr create` with body via stdin, then queries
    `gh pr view` for the canonical PRInfo."""

    captured_create: dict[str, Any] = {}
    view_payload = json.dumps({
        "number": 99, "url": "https://github.com/x/y/pull/99",
        "headRefName": "feat/x", "state": "OPEN",
    })

    def _spy(args: list[str], **kwargs: Any) -> _FakeCompleted:
        # Distinguish the two gh subcommands: pr create vs pr view.
        if "create" in args:
            captured_create["args"] = args
            captured_create["kwargs"] = kwargs
            return _FakeCompleted(stdout="https://github.com/x/y/pull/99\n")
        if "view" in args:
            return _FakeCompleted(stdout=view_payload)
        raise AssertionError(f"unexpected gh argv: {args}")

    with (
        patch("opentraces.consumers.branch_pr.shutil.which", return_value="/usr/bin/gh"),
        patch("opentraces.consumers.branch_pr.subprocess.run", side_effect=_spy),
    ):
        info = branch_context.create_pr(
            project_dir=Path("."),
            base="main",
            title="feat: x",
            body="body content",
            draft=True,
        )

    args = captured_create["args"]
    assert args[1:6] == ["pr", "create", "--base", "main", "--title"]
    assert "--body-file" in args
    assert "-" in args  # body via stdin
    assert "--draft" in args
    assert captured_create["kwargs"]["input"] == "body content"
    assert info.number == 99
    assert info.url == "https://github.com/x/y/pull/99"


def test_create_pr_raises_on_gh_failure():
    with (
        patch("opentraces.consumers.branch_pr.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "opentraces.consumers.branch_pr.subprocess.run",
            return_value=_FakeCompleted(returncode=1, stderr="rate limit exceeded"),
        ),
    ):
        with pytest.raises(branch_context.GhError) as exc_info:
            branch_context.create_pr(
                project_dir=Path("."),
                base="main",
                title="feat: x",
                body="b",
            )
    assert "rate limit" in str(exc_info.value)


# ─── update_pr ─────────────────────────────────────────────────────────────


def test_update_pr_passes_body_via_stdin_to_pr_edit():
    captured_edit: dict[str, Any] = {}
    view_payload = json.dumps({
        "number": 42, "url": "https://github.com/x/y/pull/42",
        "headRefName": "feat/x", "state": "OPEN",
    })

    def _spy(args: list[str], **kwargs: Any) -> _FakeCompleted:
        if "edit" in args:
            captured_edit["args"] = args
            captured_edit["kwargs"] = kwargs
            return _FakeCompleted(stdout="https://github.com/x/y/pull/42\n")
        if "view" in args:
            return _FakeCompleted(stdout=view_payload)
        raise AssertionError(f"unexpected gh argv: {args}")

    with (
        patch("opentraces.consumers.branch_pr.shutil.which", return_value="/usr/bin/gh"),
        patch("opentraces.consumers.branch_pr.subprocess.run", side_effect=_spy),
    ):
        info = branch_context.update_pr(
            project_dir=Path("."),
            number=42,
            body="updated body",
        )

    args = captured_edit["args"]
    assert args[1:5] == ["pr", "edit", "42", "--body-file"]
    assert args[-1] == "-"
    assert captured_edit["kwargs"]["input"] == "updated body"
    assert info.number == 42
