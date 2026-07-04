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
import os
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
def verify_provides(driver: Driver, box: Box, cp: "Checkpoint") -> None:
    """Re-verify probe-backed ``provides`` keys against the built box.

    Mapping from provides vocabulary to the runtime probe registry: only
    keys a probe can check are verified (captured_traces, survival_states,
    context_tree_built, branch_commits); unprobeable keys (skills,
    has_security_findings, migration flags) pass through unverified for now
    — widening this map is the cheap follow-up, never a blocker to honesty
    on the probeable ones.
    """
    provides = cp.provides or {}
    if not provides:
        return
    from ..probes import run_probes

    as_preconditions: dict = {}
    if provides.get("captured_traces"):
        as_preconditions["min_captured_traces"] = int(provides["captured_traces"])
    if provides.get("survival_states"):
        as_preconditions["requires_survival_states"] = list(provides["survival_states"])
    # context_tree_built is deliberately NOT verified at build time yet:
    # `ctx list` is manifest-only and restored worlds have the open
    # manifest-projection gap (issue #25), so the probe would conflate that
    # product bug with a lying checkpoint. Re-enable when the gap closes.
    if provides.get("branch_commits"):
        as_preconditions["requires_branch_commits_min"] = int(provides["branch_commits"])
    # Issue #213 — the mature scale-world honesty guard: refuse to cache a world
    # whose largest trail companion is below the declared floor (a dishonest
    # world that would let pre-fix code short-circuit the O(companion) path).
    if provides.get("outlier_trail_companion_min_bytes"):
        as_preconditions["outlier_trail_companion_min_bytes"] = int(
            provides["outlier_trail_companion_min_bytes"]
        )
    # Issue #213 external review CRITICAL 2 — refuse to cache a world whose REAL
    # plan-080 v2 object-store + per-trace envelope count is below the declared
    # floor (state.json rows are the builder's own bookkeeping, not the world).
    if provides.get("bucket_trace_envelopes_min"):
        as_preconditions["bucket_trace_envelopes_min"] = int(
            provides["bucket_trace_envelopes_min"]
        )
    # Issue #213 external review RECOMMENDATION B — refuse to cache a world whose
    # events-mirror scale silently shrank below the declared ~50K floor.
    if provides.get("trail_events_min"):
        as_preconditions["trail_events_min"] = int(provides["trail_events_min"])
    if not as_preconditions:
        return
    failures = [
        f"{key}: {message}"
        for key, ok, message in run_probes(driver, box, as_preconditions)
        if not ok
    ]
    if failures:
        raise CheckpointError(
            f"checkpoint {cp.name!r} advertises provides it did not build "
            f"(refusing to cache a lying world): " + "; ".join(failures)
        )


def _ensure_derived_search_snapshot(driver: Driver, box) -> None:
    """Re-derive the read-only trace search snapshot on a cache-hit fork.

    The snapshot cache key does not cover the PRODUCT code, so a cached
    world built by an older checkout can be forked under newer code whose
    derived-index expectations differ (surfaced live when PR #24's
    read-only ``~/.opentraces/index/search.sqlite`` landed: every cached
    world predating it forked with `trace query` rc=3 "missing", while CI
    — always cold — stayed green). The search snapshot is a fully
    rebuildable derived projection, so the honest cache-hit contract is:
    re-derive it when the restored world's copy is missing or stale.
    Best-effort by design — worlds without an initialized opentraces home
    (c-empty, c-prereqs-present) skip out on the status probe failing.
    """
    import json as _json

    try:
        status = driver.exec(
            box, [*driver.cli_argv(box), "--json", "trace", "index", "status"]
        )
        if status.returncode != 0:
            return
        state = (
            _json.loads(status.stdout).get("search_snapshot", {}).get("state")
        )
        if state in ("missing", "stale"):
            driver.exec(box, [*driver.cli_argv(box), "trace", "index", "rebuild"])
    except Exception:  # noqa: BLE001 - cache hygiene must never sink a fork
        return


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

    # Faultpoint hygiene (otbox 2.0 phase 6): while a product faultpoint is
    # armed, never read from NOR write to the snapshot cache — a faulted
    # world cached once would poison every future run silently.
    try:
        from opentraces.core.faultpoints import armed_site
        _fault_armed = armed_site() is not None
    except ImportError:  # pragma: no cover - older product checkouts
        _fault_armed = False

    # cache hit → fork from the snapshot
    if not _fault_armed and cp.cache and snapshot_exists(snap_name):
        box, _meta = driver.restore(snap_name)
        _ensure_derived_search_snapshot(driver, box)
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

    try:
        if cp.builder is not None:
            cp.builder(driver, box)
        if cp.delta is not None:
            cp.delta(driver, box)

        # otbox 2.0 phase 4: a lying checkpoint cannot enter the cache. Every
        # probe-backed key in the static ``provides`` is re-verified against the
        # box that was actually built; mismatch raises instead of caching a
        # world that doesn't contain what it advertises.
        verify_provides(driver, box, cp)

        box.notes["checkpoint"] = cp.name
        box.save()

        if cp.cache and not _fault_armed:
            driver.snapshot(box, snap_name, overwrite=True)
    except Exception:
        # Issue #53: a failed cold build must not leak its partial box.
        # The recursive chain threads ONE physical box, so the failure
        # frame tears down the shared root; teardown is idempotent.
        # Escape hatch for builder debugging: OTBOX_KEEP_FAILED_BOXES=1.
        if os.environ.get("OTBOX_KEEP_FAILED_BOXES") != "1":
            try:
                driver.teardown(box)
            except Exception:  # noqa: BLE001 - never mask the build error
                pass
        raise

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
# Issue #141 Trace Slicer Library — deterministic fixture for the conformance journey.
from . import _captured_slicer_fixture  # noqa: E402,F401  (registers c-captured-slicer-fixture)
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
# Plan 080 bucket-spine-v2 family (issue #42 — the formerly-phantom checkpoints).
from . import _bucket_spine_v2  # noqa: E402,F401  (registers c-bucket-spine-v2-* + c-bucket-spine-v1-legacy-fixture)
# Issue #42 — context-tree substrate (plan 077 phase-4 deferral).
from . import _context_tree_substrate  # noqa: E402,F401  (registers c-context-tree-substrate)
from . import _compacted_session  # noqa: E402,F401  (registers c-compacted-session)
from . import _rewound_session  # noqa: E402,F401  (registers c-rewound-session)
# Issue #93 — divergent install roots behind the integration runners.
from . import _multi_install_mixed_runtime  # noqa: E402,F401  (registers c-multi-install-mixed-runtime)
# Issue #213 (seal-family W5) — the mature scale-world perf recurrence guard.
from . import _mature_bucket  # noqa: E402,F401  (registers c-mature-bucket)
