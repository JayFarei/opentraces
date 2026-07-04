#!/usr/bin/env python3
"""PROBE A9 — track survival speed (epic #169, restore `trail track`).

Committed, grader-owned, location-relative version of the loopcraft forge draft
`a9_track_survival_speed.py`.

Re-observes the WORLD: drives the EXACT production survival-recompute code path
that `opentraces trail track --patch` runs on a cache miss
(`core.trails.sync.sync_patch`, the same `_sync` the CLI calls on a miss),
against the real ~1900-trace bucket event log + real Git, on the multi-anchor
patch pinned below (anchored at commit 884a1a6b, 951 anchor events,
path runs/progressive-discovery/log.md).

Done-claim under test: track is interactive at its OWN survival job on a
multi-anchor case — the dominant survival cost that blame offloaded is bounded
on the track side; the seam did not just move the 6h.

Defeater killed: track inherits the O(anchors x descendant-commits) survival
recompute (~21.9s/anchor) and busts the interactive budget.

Cache-masking guard (assertion-tamper): the survival cache is keyed by
(trace_patch_id, observed_head_sha). A warm cache, or a hand-pre-warmed cache
for just this patch, would let the user-facing CLI return fast WITHOUT the cold
recompute being bounded -> false GREEN. So this probe forces the cold path with
``use_cache=False, write_cache=False``: it measures the genuine recompute and is
immune to a pre-warmed cache.

GREEN requires correct, non-empty, NON-STALE survival data, not just speed:
  * the result is for the requested patch (trace_patch_id matches),
  * survival_state is a real verdict in the known vocab (ALL_SURVIVAL_STATES),
  * observed_head_id.hex == the CURRENT git HEAD (keyed to head, not stale),
  * the cold recompute finished within the interactive budget (< BUDGET_S).

Hang guard: the timed measurement runs in a CHILD subprocess under an outer
~25s timeout. A hang -> the child is SIGKILLed -> RED (rc=124 equivalent), so a
busted budget FAILS the test instead of hanging pytest. RED on current main =
the cold recompute over 951 anchors never finishes inside the outer guard.

Location-relative: imports the code-under-test from the SAME repo this test file
lives in, so a grader worktree exercises that worktree's ``src/``.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

# The patch anchored at commit 884a1a6b (951 anchor events) -> the multi-anchor
# survival case. Pinned (not sampled/ordered) for determinism, exactly as the
# forge draft.
PATCH = "0282232c60569db68c8a256566d7995a2edf5f75"
BUDGET_S = 5.0          # interactive budget (matches the spec's `timeout 5`)
OUTER_TIMEOUT_S = 25.0  # outer hang guard (matches the forge's `timeout 25`)
CHILD = "child-a9-measure"


def _child() -> int:
    """The load-bearing measurement (the forge's main(), as a subprocess).

    Returns 0 GREEN / 1 RED / 2 SETUP-INVALID / 3 INCONCLUSIVE. Prints a JSON
    evidence line plus a RESULT verdict.
    """
    try:
        from opentraces.core.trails.sync import ALL_SURVIVAL_STATES, sync_patch

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
        ).stdout.strip()
        if not head:
            print(json.dumps({
                "verdict": "SETUP-INVALID",
                "reason": "could not resolve current git HEAD",
            }))
            return 2

        # False-RED guard: warm the scoped-read OID accelerator so the timed
        # call pays the survival RECOMPUTE cost, not a cold-index rebuild. A
        # cheap scoped read of one event type primes the event_index base.pkl
        # path. Best-effort; never the thing under test.
        try:
            from opentraces.core.trails.event_log import read_events_scoped

            read_events_scoped(REPO, event_types={"git_anchor_search_completed"})
        except Exception:
            pass

        # Cold survival recompute (cache bypassed) -- the load-bearing
        # measurement. This is the SAME `_sync` the CLI calls on a miss.
        t0 = time.time()
        result = sync_patch(REPO, PATCH, use_cache=False, write_cache=False)
        elapsed = time.time() - t0

        cs = (result or {}).get("current_survival") or {}
        state = cs.get("survival_state")
        observed_head = ((cs.get("observed_head_id") or {}) or {}).get("hex")
        rpid = (result or {}).get("trace_patch_id")

        print(json.dumps({
            "elapsed_s": round(elapsed, 3),
            "budget_s": BUDGET_S,
            "head": head,
            "requested_patch": PATCH,
            "result_trace_patch_id": rpid,
            "survival_state": state,
            "observed_head_id_hex": observed_head,
            "observation_scope": (result or {}).get("observation_scope"),
            "n_observations": len((result or {}).get("observations") or []),
            "cache_bypassed": True,
        }, default=str))

        # --- GREEN assertions (each ANDs into pass; any failure -> RED) ---
        reasons: list[str] = []
        # 1. Correct, non-empty patch identity.
        if not rpid or PATCH[:20] not in str(rpid):
            reasons.append(
                f"survival is not for the requested patch (got trace_patch_id={rpid!r})"
            )
        # 2. Real survival verdict (non-empty, in the known vocab).
        if state not in ALL_SURVIVAL_STATES:
            reasons.append(
                f"survival_state {state!r} is not a real verdict in {ALL_SURVIVAL_STATES}"
            )
        # 3. NON-STALE: keyed to the CURRENT HEAD, not a cached/old ref.
        if observed_head != head:
            reasons.append(
                f"survival is stale: observed_head_id {observed_head!r} != current HEAD {head!r}"
            )
        # 4. Interactive: the cold recompute finished within budget.
        if elapsed >= BUDGET_S:
            reasons.append(
                f"cold survival recompute took {elapsed:.2f}s >= {BUDGET_S}s budget "
                f"(track still inherits the O(anchors x descendant) cost)"
            )

        if reasons:
            print("RESULT: RED")
            for r in reasons:
                print("  -", r)
            return 1
        print(
            f"RESULT: GREEN (cold survival recompute bounded {elapsed:.2f}s < {BUDGET_S}s, "
            f"state={state}, head-keyed to {head[:12]})"
        )
        return 0
    except Exception as exc:  # noqa: BLE001 — surface as a clean INCONCLUSIVE verdict
        print(json.dumps({
            "verdict": "INCONCLUSIVE",
            "reason": f"{type(exc).__name__}: {exc}",
        }))
        return 3


def _run() -> int:
    """Run the measurement child under an outer hang guard.

    0 GREEN / 1 RED / 2 SETUP-INVALID / 3 INCONCLUSIVE / 124 RED-by-hang.
    """
    proc = subprocess.Popen(
        [sys.executable, __file__, CHILD],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # own process group so we can kill grandchildren (git)
        cwd=str(REPO),
    )
    try:
        out, err = proc.communicate(timeout=OUTER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        # Over-budget HANG: kill the whole group, then report RED (rc=124).
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except Exception:
                pass
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = ("", "")
        if out:
            sys.stdout.write(out if out.endswith("\n") else out + "\n")
        if err:
            sys.stderr.write(err)
        print(
            f"RESULT: RED (hang) — cold survival recompute did NOT finish within the "
            f"{OUTER_TIMEOUT_S}s outer guard (over budget / rc=124 equivalent): "
            f"track still inherits the O(anchors x descendant) survival cost."
        )
        return 124

    if out:
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
    if err:
        sys.stderr.write(err)
    rc = proc.returncode
    if rc in (0, 1, 2, 3):
        return rc
    print(f"RESULT: INCONCLUSIVE — measurement child exited with unexpected rc={rc}")
    return 3


def test_probe_a9_track_survival_speed():
    rc = _run()
    if rc == 2:
        pytest.skip("A9 SETUP-INVALID: could not resolve git HEAD / build the measurement scenario")
    if rc == 3:
        pytest.skip("A9 INCONCLUSIVE: the measurement child errored before producing a verdict")
    assert rc == 0, (
        "A9 RED: track's cold survival recompute on the multi-anchor patch "
        f"{PATCH[:12]} busted the interactive budget (>= {BUDGET_S}s, or did not finish "
        f"within the {OUTER_TIMEOUT_S}s outer guard), or returned stale/wrong survival data "
        "(track still inherits the O(anchors x descendant-commits) survival recompute)."
    )


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == CHILD:
        sys.exit(_child())
    sys.exit(_run())
