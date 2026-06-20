"""Plan 087 — CLI core-surface simplification contract.

Substrate / maintenance / niche plumbing is HIDDEN from ``--help`` (to shrink the
surface to its core) but stays REGISTERED + callable (journeys + scripts invoke
it by argv). This test is the guard: a dropped verb (not merely hidden) fails, an
accidentally un-hidden plumbing verb fails, and an accidentally hidden core verb
fails.
"""

from __future__ import annotations

import click
import pytest

from opentraces.cli import main

HIDDEN_PLUMBING = [
    "ctx diff",
    "ctx prune",
    "ctx resume",
    "ctx resolve",
    "ctx compactions",
    "ctx anchor-for-step",
    "bucket rebuild",
    "bucket replay",
    "bucket prune",
    "bucket prefetch",
    "bucket security",
    "trace teleport",
    "git-backfill",
    # Tier B — experimental / operator / internal feature groups + subcommands.
    "capture-otlp",
    "skill-verifier",
    "setup runtime",
    "setup watcher sweep",
    "setup watcher tick",
    "workflow optimize",
    "workflow skill-intelligence",
    "workflow verifier-factory",
]

CORE_VISIBLE = [
    "bucket status",
    "bucket manifest",
    "bucket verify",
    "bucket repair",
    "ctx tree",
    "ctx list",
    "ctx show",
    "trace query",
    "trace map",
    "trail blame",
    "trail track",
    "dataset run",
]


def _resolve(path: str) -> click.Command:
    cmd: click.Command = main
    ctx = click.Context(main, info_name="opentraces")
    for part in path.split():
        assert isinstance(cmd, click.Group), f"{path}: {part} parent is not a group"
        child = cmd.get_command(ctx, part)
        assert child is not None, f"{path!r} is NOT registered (dropped, not hidden!)"
        ctx = click.Context(child, info_name=part, parent=ctx)
        cmd = child
    return cmd


@pytest.mark.parametrize("path", HIDDEN_PLUMBING)
def test_plumbing_is_hidden_but_callable(path: str) -> None:
    cmd = _resolve(path)  # registered (callable) or the assert in _resolve fires
    assert cmd.hidden is True, f"{path!r} must be hidden from --help"


@pytest.mark.parametrize("path", CORE_VISIBLE)
def test_core_verbs_stay_visible(path: str) -> None:
    cmd = _resolve(path)
    assert cmd.hidden is False, f"{path!r} is a core verb and must stay on --help"
