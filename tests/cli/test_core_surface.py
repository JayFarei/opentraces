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
    # v7 #164 — superseded by the bare-noun `ctx <trace>[:step|:last]` read;
    # callable, off --help.
    "ctx tree",
    "ctx show",
    "ctx step",
    "ctx reads",
    "ctx writes",
    # v7 #164 — "no ctx list": discovery is the trace spine, `bucket list` is
    # the row surface; `ctx info` is superseded by the bare-noun overview.
    "ctx list",
    "ctx info",
    "bucket rebuild",
    "bucket replay",
    "bucket prune",
    "bucket prefetch",
    "bucket security",
    # v7 #162 — the fleet dashboard moved to the top-level `status` and the
    # per-trace read side to `bucket list`; `--heal` folded into `bucket
    # repair`, and `bucket sync` supersedes `bucket remote`. All stay callable.
    "bucket status",
    "bucket manifest",
    "bucket remote",
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
    # Redundant alias of the canonical `auth login`.
    "setup auth",
    # Folded into the idempotent `dataset remote create` (create-or-bind).
    "dataset remote add",
    # Niche query variant (grouped topic capsule); core search is `trace query`.
    "trace discover",
]

CORE_VISIBLE = [
    "bucket list",
    "bucket connect",
    "bucket sync",
    "bucket verify",
    "bucket repair",
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


def test_trace_group_surface_pin() -> None:
    """v7 7->4 collapse pin: the trace group's VISIBLE surface is exactly
    the four job verbs. A future re-exposure of compare/partition/skills/
    index/discover/teleport (or a new visible verb) fails here rather than
    silently passing the lint; a dropped hidden verb fails too (hidden !=
    removed)."""
    trace = _resolve("trace")
    assert isinstance(trace, click.Group)
    ctx = click.Context(trace, info_name="trace")
    all_cmds = {
        name: trace.get_command(ctx, name) for name in trace.list_commands(ctx)
    }
    visible = {name for name, cmd in all_cmds.items() if not cmd.hidden}
    assert visible == {"get", "map", "query", "slice"}
    for name in ("compare", "partition", "skills", "index", "discover", "teleport"):
        cmd = all_cmds.get(name)
        assert cmd is not None, f"trace {name} was dropped (must stay hidden-but-callable)"
        assert cmd.hidden is True, f"trace {name} must stay hidden from --help"
