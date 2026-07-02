#!/usr/bin/env python3
"""PROBE A1 — track complete (epic #169, grounded verifier, T3 replay).

Committed, grader-owned, location-relative version of the loopcraft forge draft
`probe_a1_track_complete.py`.

Done-claim under test: per-trace `trail track <id> --json` is INTERACTIVE *and*
returns the FULL trace (event_count >= 10148, len(timeline) == event_count, same
trace_id), not a truncated stale-index read and not a multi-second batch walk.

Re-observes the WORLD: real CLI exit code + real JSON payload + wall-clock; never
reads any agent narration. The code under test is the `src/` that lives next to
this file (location-relative), so a grader worktree exercises that worktree's
code via both the in-process oracle and the `.venv/bin/opentraces` CLI (which is
an editable install of the same `src/`).

Hardened floor (kills the stale-companion 10042): the expected count is derived
INDEPENDENTLY, head-pinned, from the canonical Git event log via the production
`read_events_for_trace(REPO, TRACE)` API, and `event_count` must equal it — in
addition to the absolute >= 10148 floor.

RED on main : the per-trace track is over budget (~5.9s warm >= 4.0s) and/or a
              truncated/stale count -> non-GREEN.
GREEN on fix: interactive (< 4.0s warm) AND the full head-pinned trace.

exit 0 GREEN / 1 RED / 2 SETUP-INVALID (oracle floor not met) / 3 HARNESS-INVALID
(oracle could not be established). A non-GREEN verdict FAILS the pytest.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

OT = REPO / ".venv" / "bin" / "opentraces"
TRACE = "111c5a6a-744b-403f-963d-29b1b166350f"
EXPECT_MIN = 10148          # full trace; stale companion = 10042, empty = 0
THRESH = 4.0                # s; sits in the [fixed ~1.8s, broken ~5.9s] gap
HARD_TIMEOUT = 20           # s; a true hang -> rc=124 -> RED


def _run_cli(silent: bool):
    args = [str(OT), "trail", "track", TRACE, "--json"] + (["--silent"] if silent else [])
    try:
        p = subprocess.run(
            args, capture_output=True, text=True, timeout=HARD_TIMEOUT, cwd=str(REPO)
        )
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return 124, ""


def _run() -> int:
    # ----- head-pinned oracle: the independent ground truth ------------------
    # Derive the expected event count from the canonical Git event log via the
    # SAME production reader the CLI walks (read_events_for_trace), pinned to the
    # current ref HEAD. This kills the stale-companion 10042: if the CLI ever
    # reads a stale projection while the log carries the full trace, ec !=
    # expected catches it independent of the absolute floor.
    try:
        from opentraces.core.trails.event_log import read_events_for_trace

        expected = len(read_events_for_trace(REPO, TRACE))
    except Exception as exc:  # noqa: BLE001
        print(
            "PROBE_HARNESS-INVALID A1-track-complete: oracle read_events_for_trace "
            f"crashed: {exc!r}"
        )
        return 3
    if expected < EXPECT_MIN:
        print(
            f"PROBE_SETUP-INVALID A1-track-complete: head-pinned oracle {expected} "
            f"< {EXPECT_MIN} (witness trace {TRACE} not present in REPO event log)"
        )
        return 2

    # Warm the on-disk event_index accelerator so a cold-cache penalty cannot
    # false-RED the FIXED (fast) path. The BROKEN path stays ~5.9s warm -> still
    # RED. This warm-up immediately precedes the timed run (the forge contract).
    _run_cli(silent=True)

    # Timed grounded run (single-process timing; no shell/date portability risk).
    t0 = time.perf_counter()
    rc, out = _run_cli(silent=False)
    elapsed = time.perf_counter() - t0

    fails: list[str] = []
    if rc != 0:
        fails.append(f"rc={rc} (124=timeout/hang)")
    d = None
    if rc == 0:
        try:
            d = json.loads(out)
        except Exception as e:  # noqa: BLE001
            fails.append(f"unparseable JSON: {e}")
    ec = tid = None
    n = -1
    if d is not None:
        ec = d.get("event_count")
        tid = d.get("trace_id")
        tl = d.get("timeline")
        n = len(tl) if isinstance(tl, list) else -1
        if tid != TRACE:
            fails.append(f"trace_id={tid!r} != {TRACE!r}")
        if not isinstance(ec, int) or ec < EXPECT_MIN:
            fails.append(f"event_count={ec} < {EXPECT_MIN} (stale-index=10042 / empty=0)")
        if isinstance(ec, int) and ec != expected:
            fails.append(
                f"event_count={ec} != head-pinned oracle {expected} "
                "(truncated / stale-companion 10042)"
            )
        if n != ec:
            fails.append(f"len(timeline)={n} != event_count={ec} (faked count)")
    if elapsed >= THRESH:
        fails.append(f"elapsed={elapsed:.2f}s >= {THRESH}s (not interactive)")

    obs = (
        f"rc={rc} elapsed={elapsed:.2f}s event_count={ec} len(timeline)={n} "
        f"trace_id={tid} oracle={expected}"
    )
    if fails:
        print("PROBE_RED A1-track-complete:", "; ".join(fails))
        print("  observed:", obs)
        return 1
    print("PROBE_GREEN A1-track-complete:", obs)
    return 0


def test_probe_a1_track_complete():
    rc = _run()
    if rc == 3:
        pytest.skip(
            "A1 HARNESS-INVALID: could not establish the head-pinned oracle "
            "(read_events_for_trace crashed)"
        )
    if rc == 2:
        pytest.skip(
            "A1 SETUP-INVALID: head-pinned oracle below the 10148 floor "
            "(witness trace not present in the REPO event log)"
        )
    assert rc == 0, (
        "A1 RED: per-trace `trail track` is not interactive and/or not the full "
        "head-pinned trace (over budget >= 4.0s or truncated/stale count)"
    )


if __name__ == "__main__":
    sys.exit(_run())
