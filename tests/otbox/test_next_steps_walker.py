"""B3b — deterministic ``next_steps`` graph walker (the "agents drive
agents" affordance test, no LLM).

Starting at ``opentraces status --json`` on a real captured world, the
walker mechanically follows every command referenced by ``next_steps`` /
``next_command`` strings and asserts:

1. **Closure** — every referenced ``opentraces <cmd>`` resolves in the
   Click registry (no suggested command can be a dead affordance).
2. **Execution** — every referenced command that needs no required
   arguments actually runs in the box under ``--json`` without leaking a
   traceback; its own ``next_steps`` extend the walk.
3. **Goal reachability** — within ``PAR_HOPS`` the affordance graph
   references the documented goals: finding past work (``trace query``),
   explaining a commit (``trail blame commit``), and shipping a dataset
   (``dataset publish``). The entry point must put an agent on a path to
   each documented job without external knowledge.

Deterministic and CI-safe: forks the ``c-captured-real-session``
checkpoint, executes only zero-required-arg references, bounded hops.
"""

from __future__ import annotations

import json
import re

import click
import pytest

from .checkpoints import resolve_checkpoint
from .drivers import get_driver

PAR_HOPS = 4

# Documented goals the affordance graph must reference within PAR_HOPS.
GOALS = {
    "trace query",
    "trail blame commit",
    "dataset publish",
}

_REF_RE = re.compile(r"opentraces ((?:[a-z][a-z0-9-]*)(?: [a-z][a-z0-9-]*)*)")

# Referenced commands we never EXECUTE during the walk (still closure-checked).
_EXEC_DENY = {
    "auth login",          # real device-flow network call
    "setup upgrade",       # package manager
    "capture-otlp start",  # long-lived daemon
    "capture-otlp restart",
    "init",                # interactive in a non-initialized cwd
    "remove",              # destructive confirmation flow
}


def _registry_root():
    from opentraces.cli import main as click_root

    return click_root


def _resolve_ref(words: list[str]):
    """Longest-prefix resolve of a referenced command in the Click registry.

    Returns ``(path, command)`` or ``(None, None)`` when even the first
    word does not resolve.
    """
    node = _registry_root()
    resolved: list[str] = []
    cmd = None
    for word in words:
        nxt = getattr(node, "commands", {}).get(word)
        if nxt is None:
            break
        resolved.append(word)
        cmd = nxt
        node = nxt
    if not resolved:
        return None, None
    return " ".join(resolved), cmd


def _required_args(cmd) -> bool:
    return any(
        (isinstance(p, click.Argument) and p.required)
        or getattr(p, "required", False)
        for p in cmd.params
    )


def _harvest_refs(payload) -> set[str]:
    """Collect ``opentraces ...`` references from next_steps/next_command
    fields anywhere in the envelope."""
    texts: list[str] = []

    def _walk(value):
        if isinstance(value, dict):
            for key, sub in value.items():
                if key in ("next_steps", "next_command"):
                    if isinstance(sub, str):
                        texts.append(sub)
                    elif isinstance(sub, list):
                        texts.extend(str(item) for item in sub)
                else:
                    _walk(sub)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(payload)
    refs: set[str] = set()
    for text in texts:
        for match in _REF_RE.finditer(text):
            refs.add(match.group(1))
    return refs


def _json_or_none(stdout: str):
    idx = stdout.find("{")
    if idx < 0:
        return None
    try:
        return json.loads(stdout[idx:])
    except ValueError:
        return None


@pytest.fixture(scope="module")
def walk_result():
    """Run the walk once; the tests below assert different properties."""
    driver = get_driver("local")
    cp = resolve_checkpoint(driver, "c-captured-real-session")
    box = cp.box
    base = driver.cli_argv(box)
    try:
        res = driver.exec(box, [*base, "--json", "status"])
        assert res.returncode == 0, f"entry point failed: {res.stderr[-300:]}"
        entry = _json_or_none(res.stdout)
        assert entry is not None, "status --json did not emit JSON"

        seen_refs: dict[str, int] = {}
        unresolved: list[str] = []
        traceback_leaks: list[str] = []
        executed: list[str] = []
        frontier = _harvest_refs(entry)
        for hop in range(1, PAR_HOPS + 1):
            next_frontier: set[str] = set()
            for ref in sorted(frontier):
                path, cmd = _resolve_ref(ref.split())
                if path is None:
                    unresolved.append(ref)
                    continue
                if path in seen_refs:
                    continue
                seen_refs[path] = hop
                if cmd is None or isinstance(cmd, click.Group):
                    continue
                if path in _EXEC_DENY or _required_args(cmd):
                    continue
                run = driver.exec(box, [*base, "--json", *path.split()])
                executed.append(path)
                combined = (run.stdout or "") + (run.stderr or "")
                if "Traceback (most recent call last)" in combined:
                    traceback_leaks.append(path)
                    continue
                doc = _json_or_none(run.stdout)
                if doc is not None:
                    next_frontier |= _harvest_refs(doc)
            frontier = next_frontier - set(seen_refs)
            if not frontier:
                break
        return {
            "seen": seen_refs,
            "unresolved": unresolved,
            "executed": executed,
            "traceback_leaks": traceback_leaks,
        }
    finally:
        if box.root.exists():
            driver.teardown(box)


def test_affordance_graph_is_closed(walk_result):
    """Every command the product suggests must exist in the registry."""
    assert not walk_result["unresolved"], (
        "next_steps reference commands that do not resolve in the Click "
        f"registry: {walk_result['unresolved']}"
    )


def test_walked_commands_run_clean(walk_result):
    assert walk_result["executed"], "the walker should execute at least one affordance"
    assert not walk_result["traceback_leaks"], (
        f"affordances leaked tracebacks: {walk_result['traceback_leaks']}"
    )


def test_documented_goals_referenced_within_par(walk_result):
    """The entry point must put an agent on a path to the documented jobs."""
    seen = set(walk_result["seen"])
    missing = {
        goal
        for goal in GOALS
        if not any(path == goal or path.startswith(goal) for path in seen)
    }
    assert not missing, (
        f"affordance graph never references {sorted(missing)} within "
        f"{PAR_HOPS} hops of `opentraces status --json` "
        f"(referenced: {sorted(seen)})"
    )
