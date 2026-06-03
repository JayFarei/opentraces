"""Checkpoint catalog — resumable starting states for journeys (plan 062).

A *checkpoint* is a named, resumable opentraces-world state. Journeys
declare ``from_checkpoints = [...]`` to say where they want to start
from; the matrix runner builds each checkpoint once per run and
snapshot-forks it for every dependent journey (the crabbox ergonomic).

Two ways to define a checkpoint:

    register(Checkpoint(name="c-empty"))                         # bare box
    register(Checkpoint(name="c-foo",
                        composed_from="c-empty",
                        delta=_foo_delta_fn))                    # parent + delta
    register(Checkpoint(name="c-bar", builder=_bar_builder_fn))  # cold builder

Resolution is content-addressed: each checkpoint's snapshot lives at
``.otbox/snapshots/_checkpoint-<name>-<hash>.tar.gz``. The hash is
recursive over the builder's source + the parent's hash, so editing any
ancestor invalidates the cache transparently.

Driver-mediated end to end (plan 061 contract), so the same registry
works on Tier 0 (local) and Tier 1 (remote over SSH/Tailscale).
"""

from __future__ import annotations

import hashlib
import inspect
import time
from dataclasses import dataclass
from typing import Any, Callable

from ..drivers.base import Driver
from ..env import Box, ensure_state_root, new_box_id
from ..snapshot import snapshot_exists


class CheckpointError(Exception):
    pass


Builder = Callable[[Driver, Box], None]


@dataclass
class Checkpoint:
    """A resumable starting state for journeys.

    ``provides`` (plan 069 R3) declares what world-state dimensions this
    checkpoint produces, so a journey's declarative ``[preconditions]``
    block can be resolved against the catalog without naming a specific
    checkpoint. Supported keys (plan 069 R1 vocabulary):

      * ``captured_traces: int`` — how many distinct captured trace
        records the checkpoint leaves in the project state.
      * ``survival_states: list[str]`` — which survival-state strings
        the captured Trace Patches may resolve to after maturation.
      * ``skills: list[str]`` — which skill identifiers were invoked
        across the captured sessions.
      * ``branch_commits: int`` — how many commits the checkpoint
        produced across all branches (base + any feature branches).
      * ``has_security_findings: bool`` — whether the captured sessions
        carry security-pipeline findings (secret tools fingerprinted
        the trace).

    Default is ``None`` (no declared dimensions); checkpoints without
    captured state simply leave it unset.
    """

    name: str
    builder: Builder | None = None
    composed_from: str | None = None
    delta: Builder | None = None
    cache: bool = True
    description: str = ""
    requires: tuple[str, ...] = ()
    provides: dict[str, Any] | None = None


@dataclass
class CheckpointResult:
    name: str
    box: Box
    cache_hit: bool
    cold_build_seconds: float
    parent: str | None
    snapshot_name: str


REGISTRY: dict[str, Checkpoint] = {}


def register(cp: Checkpoint) -> None:
    REGISTRY[cp.name] = cp


def available_checkpoints() -> list[dict]:
    return [
        {
            "name": cp.name,
            "composed_from": cp.composed_from,
            "cache": cp.cache,
            "description": cp.description.strip(),
            "requires": list(cp.requires),
            "provides": dict(cp.provides) if cp.provides else {},
        }
        for cp in sorted(REGISTRY.values(), key=lambda c: c.name)
    ]


# ---------------------------------------------------------------------------
# content-addressed cache key
# ---------------------------------------------------------------------------
def _source_bytes(fn: Builder | None) -> bytes:
    if fn is None:
        return b""
    try:
        return inspect.getsource(fn).encode("utf-8")
    except (OSError, TypeError):  # builtins or lambdas without source
        return repr(fn).encode("utf-8")


def _source_hash(cp: Checkpoint) -> str:
    h = hashlib.sha256()
    h.update(cp.name.encode("utf-8"))
    h.update((cp.composed_from or "").encode("utf-8"))
    h.update(_source_bytes(cp.builder))
    h.update(_source_bytes(cp.delta))
    if cp.composed_from:
        try:
            parent = REGISTRY[cp.composed_from]
        except KeyError:
            raise CheckpointError(
                f"checkpoint {cp.name!r} composed_from unknown {cp.composed_from!r}"
            ) from None
        h.update(_source_hash(parent).encode("ascii"))
    return h.hexdigest()[:12]


def snapshot_name(cp: Checkpoint) -> str:
    """The cache filename for this checkpoint's snapshot."""
    return f"_checkpoint-{cp.name}-{_source_hash(cp)}"


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------
def resolve_checkpoint(driver: Driver, name: str) -> CheckpointResult:
    """Apply checkpoint ``name``, returning a ready-to-run box.

    On a cache hit, ``driver.restore`` forks a fresh box from the cached
    snapshot. On a miss, the parent is resolved recursively (or a bare
    box is provisioned for a leaf), then this checkpoint's
    ``builder`` / ``delta`` runs, and the result is snapshotted for
    next time (unless ``cache=False``).
    """
    ensure_state_root()
    try:
        cp = REGISTRY[name]
    except KeyError:
        raise CheckpointError(
            f"unknown checkpoint {name!r}; available: "
            f"{[c['name'] for c in available_checkpoints()]}"
        ) from None

    snap_name = snapshot_name(cp)

    # cache hit → fork from the snapshot
    if cp.cache and snapshot_exists(snap_name):
        box, _meta = driver.restore(snap_name)
        return CheckpointResult(
            name=cp.name, box=box, cache_hit=True,
            cold_build_seconds=0.0,
            parent=cp.composed_from, snapshot_name=snap_name,
        )

    # cold build
    start = time.monotonic()
    if cp.composed_from:
        parent_result = resolve_checkpoint(driver, cp.composed_from)
        box = parent_result.box
    else:
        box = Box(box_id=new_box_id(), driver=driver.name)
        driver.provision(box)
        box.save()

    if cp.builder is not None:
        cp.builder(driver, box)
    if cp.delta is not None:
        cp.delta(driver, box)

    box.notes["checkpoint"] = cp.name
    box.save()

    if cp.cache:
        driver.snapshot(box, snap_name, overwrite=True)

    return CheckpointResult(
        name=cp.name, box=box, cache_hit=False,
        cold_build_seconds=round(time.monotonic() - start, 3),
        parent=cp.composed_from, snapshot_name=snap_name,
    )


# ---------------------------------------------------------------------------
# bundled checkpoints — register on import
# ---------------------------------------------------------------------------
from . import _empty  # noqa: E402,F401  (registers c-empty)
from . import _prereqs  # noqa: E402,F401  (registers c-prereqs-present)
from . import _installed_source  # noqa: E402,F401  (registers c-installed-source)
from . import _captured_session  # noqa: E402,F401  (registers c-captured-real-session)
# Plan 068 substrate — credible-state variants composing on the captures above.
from . import _captured_with_revert  # noqa: E402,F401  (registers c-captured-with-revert)
from . import _captured_with_secrets  # noqa: E402,F401  (registers c-captured-with-secrets)
from . import _captured_multi_skill  # noqa: E402,F401  (registers c-captured-multi-skill)
from . import _captured_with_pr_branch  # noqa: E402,F401  (registers c-captured-with-pr-branch)
# Plan 083 Codex lane scaffold — artifact-preferred, inert without captures.
from . import _captured_codex_real_session  # noqa: E402,F401  (registers c-captured-codex-real-session)
from . import _captured_codex_bash_session  # noqa: E402,F401  (registers c-captured-codex-bash-session)
from . import _captured_codex_pending  # noqa: E402,F401  (registers pending plan 083 Codex checkpoints)
# Plan 091 Pi lane scaffold — artifact-preferred with deterministic synthetic fallback.
from . import _captured_pi_real_session  # noqa: E402,F401  (registers c-captured-pi-real-session)
from . import _captured_pi_pending  # noqa: E402,F401  (registers pending plan 091 Pi checkpoints)
# Plan 078 OTLP receiver capture-source checkpoints.
from . import _context_tree_otel_linear  # noqa: E402,F401  (registers c-context-tree-otel-linear)
from . import _context_tree_otel_with_mcp  # noqa: E402,F401  (registers c-context-tree-otel-with-mcp)
# Plan 085 legacy-world (otbox's first previous-version world).
from . import _legacy_v033  # noqa: E402,F401  (registers c-legacy-v033 + c-legacy-v033-upgraded)
