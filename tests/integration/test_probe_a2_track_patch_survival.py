#!/usr/bin/env python3
"""PROBE A2 — trail track --patch survival is interactive AND non-empty (epic #169).

Committed, grader-owned, location-relative port of the loopcraft forge draft
`check_a2_track_patch_survival.sh`. Drives the SHIPPED CLI
``opentraces trail track --patch <real-pid> --json`` under a hard 10s
interactivity budget and asserts it returns NON-EMPTY, non-stale, correctly
routed survival data — not merely rc=0.

Done-claim under test: `trail track --patch <pid> --json` returns fast (does NOT
hang the ~6h full-event-log read) AND yields a real per-anchor survival
observation (input patch id echoed back + ``patch_survival_observed`` records
carrying a real ``git_anchor_id`` and a ``survival_state`` in the survival
vocabulary).

Defeaters killed (ported verbatim from the forge draft, never weakened):
  (1) HANG / non-interactive             -> the 10s budget trips, RED.
  (2) fast-but-empty (only search events -> zero survival observations, or a
      ``no_trace_patch_event`` / ``no_git_anchor_event`` empty sentinel) -> RED.
  (3) wrong-route (some other patch returned / not the survival route)   -> RED.

RED on current main : the ``--patch`` route does the full ``read_events()`` read
                      and hangs -> the 10s budget trips (rc=124) -> RED.
GREEN only on the fix: warm scoped survival returns in ~seconds with real,
                      non-empty, correctly-routed observations.

Rung: T3 (grounded re-observation of real CLI JSON over the real bucket event
log). Imports the code-under-test from the SAME repo this test file lives in
(location-relative), so a grader worktree exercises that worktree's ``src/`` and
drives that worktree's ``.venv/bin/opentraces``.

exit 0 GREEN / 1 RED / 2 SETUP-INVALID (CLI binary absent). A non-GREEN verdict
makes the pytest FAIL; it never silently passes.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

# --- CLI under test + the exact id/oracle pins from the forge draft (do NOT relax).
OT = REPO / ".venv" / "bin" / "opentraces"
PID = "85779b9573738e4ca5ec17703a0438db0a968d6b2aefc88ed400c4827f719f7f"
# Interactivity bar: warm scoped survival returns ~2s; the ~6h full-read hang
# trips this budget on main (rc=124). Kept identical to the forge draft.
BUDGET = 10

# Survival vocabulary — kept byte-identical to the forge draft. Never widen.
VOCAB = {
    "alive_on_path",
    "alive_transformed",
    "lost",
    "reverted",
    "unknown",
    "alive_moved",
    "partially_preserved",
    "repaired",
    "never_committed",
    "orphan_branch",
}
EMPTY_SENTINELS = {"no_trace_patch_event", "no_git_anchor_event"}


def _warm_up() -> None:
    """False-RED guard: warm the SCOPED accelerator the fix relies on, reading
    the canonical ``trace_patch_created`` / ``git_anchor_created`` slice from the
    repo's Git event log via the same API the forge draft uses
    (``read_events_scoped``). Best-effort and non-rescuing: on main the CLI still
    does the full ``read_events()`` hang regardless, so this can only remove a
    spurious cold-vs-warm slow first run on the GREEN side, never flip RED."""
    try:
        from opentraces.core.trails.event_log import read_events_scoped

        read_events_scoped(
            REPO,
            event_types={"trace_patch_created", "git_anchor_created"},
        )
    except Exception:
        # The warm-up is advisory only; never let it fail the probe.
        pass


def _evaluate(timed_out: bool, rc: int, stdout: str) -> list[str]:
    """Hard, tamper-resistant predicate over the re-observed world. Ported
    verbatim from the forge draft's PYASSERT block — every assertion preserved."""
    pid = PID.lower()
    fail: list[str] = []

    # (1) interactivity: a hang trips the budget -> rc=124; any non-zero rc fails.
    if timed_out:
        fail.append("HANG: command did not return within budget (rc=124) -> non-interactive")
    elif rc != 0:
        fail.append(f"NONZERO_RC: rc={rc}")

    d = None
    if (not timed_out) and rc == 0:
        try:
            d = json.loads(stdout)
        except Exception as e:  # noqa: BLE001
            fail.append(f"BAD_JSON: {e}")
        if d is not None and not isinstance(d, dict):
            fail.append("PAYLOAD_NOT_OBJECT")
            d = None

    if d is not None:
        # (3) wrong-route guard: the input patch id must be echoed back verbatim.
        got = str(d.get("trace_patch_id") or "").lower()
        if got != pid:
            fail.append(f"WRONG_ROUTE: trace_patch_id={got!r} != input {pid!r}")

        # (2) fast-but-empty guard: an anchored patch MUST yield real survival
        # observations.
        tl = d.get("trail_limitations") or []
        if isinstance(tl, list) and set(tl) & EMPTY_SENTINELS:
            fail.append(
                "EMPTY_SURVIVAL: trail_limitations carries empty sentinel "
                f"{sorted(set(tl) & EMPTY_SENTINELS)}"
            )
        obs = d.get("observations")
        if not isinstance(obs, list) or len(obs) == 0:
            fail.append(f"EMPTY_SURVIVAL: observations is empty/missing ({type(obs).__name__})")
        else:
            # each observation must be a real survival record (anti-tamper: [{}] fails).
            for i, o in enumerate(obs):
                if not isinstance(o, dict):
                    fail.append(f"OBS_NOT_OBJECT[{i}]")
                    continue
                if o.get("observation_type") != "patch_survival_observed":
                    fail.append(f"OBS_BAD_TYPE[{i}]={o.get('observation_type')!r}")
                if not o.get("git_anchor_id"):
                    fail.append(f"OBS_NO_ANCHOR[{i}]")
                st = o.get("survival_state")
                if st not in VOCAB:
                    fail.append(f"OBS_BAD_STATE[{i}]={st!r}")
            # the observed patch id on each observation must also match (route integrity).
            for i, o in enumerate(obs):
                opid = str((o or {}).get("trace_patch_id") or "").lower() if isinstance(o, dict) else ""
                if opid and opid != pid:
                    fail.append(f"OBS_WRONG_PATCH[{i}]={opid!r}")

        # aggregate current survival must be a real state (not a fabricated placeholder).
        cs = d.get("current_survival") or {}
        cstate = cs.get("survival_state") if isinstance(cs, dict) else None
        if cstate not in VOCAB:
            fail.append(f"CURRENT_STATE_NOT_IN_VOCAB={cstate!r}")

    return fail


def _run() -> int:
    # SETUP VALIDITY: the CLI under test must exist in this (grader) worktree.
    if not OT.is_file():
        print(f"[SETUP-INVALID] opentraces CLI not found at {OT}")
        return 2

    # Warm the scoped accelerator (best-effort, bounded by the scoped reader).
    _warm_up()

    # MEASURE: the real CLI call under the interactivity budget. A hang -> RED.
    timed_out = False
    rc = -1
    stdout = ""
    try:
        proc = subprocess.run(
            [str(OT), "trail", "track", "--patch", PID, "--json"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=BUDGET,
        )
        rc = proc.returncode
        stdout = proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        # Equivalent to the shell `timeout` rc=124 hang signal.
        timed_out = True
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""

    fail = _evaluate(timed_out, rc, stdout)

    if fail:
        print("VERIFIER_RESULT=RED")
        for f in fail:
            print("  -", f)
        return 1

    # On GREEN, surface the load-bearing facts.
    d = json.loads(stdout)
    print("VERIFIER_RESULT=GREEN")
    print(
        f"  patch={PID[:12]}.. observations={len(d.get('observations'))} "
        f"current_survival={(d.get('current_survival') or {}).get('survival_state')}"
    )
    return 0


def test_probe_a2_track_patch_survival():
    rc = _run()
    assert rc != 2, "A2 SETUP-INVALID (opentraces CLI binary absent in this worktree)"
    assert rc == 0, (
        "A2 RED: `trail track --patch` is non-interactive (hangs the full "
        "read_events() read) and/or returns empty/wrong-route survival data"
    )


if __name__ == "__main__":
    sys.exit(_run())
