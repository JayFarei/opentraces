#!/usr/bin/env python3
"""PROBE A3 — blame attribution anchored + interactive (epic #169).

Committed, grader-owned, location-relative version of the loopcraft forge drafts
`check_a3.py` + `check_a3_hardened.py`. Re-observes the REAL world: runs the
SHIPPED CLI ``trail blame commit <COMMIT> --json`` under a hard time budget and
asserts the git-anchor attribution (`trailEvidence`) is complete, anchored, and
step-bearing — measured against an oracle derived INDEPENDENTLY of the buggy CLI
from the append-only ``git_anchor_created`` event log.

Done-claim (one sentence): ``trail blame commit 884a1a6b... --json`` RETURNS
within a bounded interactive budget (not the ~6h per-anchor survival hang) AND
its ``trailEvidence`` includes the anchoring trace 66733cf8-...-30188 carrying an
integer trace:step, is a SUPERSET of the 10 oracle traces, has >=951 rows, and
every row is integer step-bearing.

RED on main : ``anchors_for_commit_with_survival`` recomputes live reachability
              per anchor -> the CLI hangs -> ``timeout`` -> rc=124 => RED.
GREEN on fix: blame returns within budget with the complete, anchored,
              step-bearing attribution that the independent oracle proves exists.

Defeaters killed:
  - HANG (not interactive):            rc != 0 / timed out (timeout -> 124)  => RED
  - fast-but-empty (dropped traces):   trailEvidence empty / missing          => RED
  - fast-but-wrong (different trace):   oracle trace set NOT a subset of rows  => RED
  - fast-but-truncated (rows dropped):  row count < oracle floor (951)         => RED
  - fast-but-stepless (no trace:step):  any row carries no integer step_index  => RED

Oracle (T2 provenance, derived here from ``build_trail_query_projection_for_commits``
-> ``anchors_for_commit`` — the attribution-only path, NO survival, fast on main
AND fix alike, independent of the hanging CLI survival path): commit 884a1a6b has
>=951 anchor rows over 10 distinct traces, all integer step-bearing, including the
anchoring trace 66733cf8-...-30188. The derived oracle is cross-checked against the
forge's pinned facts; a derivation that no longer reproduces them is SETUP-INVALID
(the world/derivation broke), never a silent weakening.

False-RED guards: every CLI assertion is set-membership + lower-bound + non-null
integer step — order/timestamp/warm-vs-cold independent; trace_id & step_index are
immutable facts of append-only events. BUDGET=45s sits far above the measured fixed
end-to-end build (~12s, even cold-scoped) and far below the ~6h hang.

Location-relative: imports the code-under-test from the ``src/`` next to this file
(a grader worktree exercises that worktree's code) and resolves the initialized
project (and thus the canonical event ref) from this checkout's COMMON git dir, so
it works from any worktree of the repo without hardcoding a path.

exit 0 GREEN / 1 RED / 2 SETUP-INVALID.

Usage:
  test_probe_a3_blame_anchored.py                 # run the live CLI and assert (the gate)
  test_probe_a3_blame_anchored.py --from-json P   # assert against a captured payload (proof-of-green)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

CLI = REPO / ".venv" / "bin" / "opentraces"
COMMIT = "884a1a6bc1d598f13351140d2da03e64d2062b90"
WANT = "66733cf8-9003-4f09-bca8-e4cda7030188"  # the anchoring trace (T2 oracle)
WANT_STEP = 1085  # grounded step_index from the event log (non-load-bearing corroboration)
BUDGET = 45  # seconds; hang(~6h) >> 45 >> fixed(~12s)

# Forge pins folded in from check_a3_hardened.py — the independently-derived
# oracle MUST reproduce (be a superset of) these, else the derivation/world drifted.
PINNED_ORACLE_TRACES = {
    "0119a3e8", "1760881a", "2d3c813a", "42192f76", "66733cf8",
    "6dc2371f", "993b9584", "b26e1d03", "cbcc3bcc", "df5e8614",
}
PINNED_MIN_ROWS = 951


def _is_step(row: dict) -> bool:
    # bool is a subclass of int; a True/False masquerading as step 1/0 is NOT a step.
    s = row.get("step_index")
    return isinstance(s, int) and not isinstance(s, bool)


def _git_common_dir(cwd: Path) -> Path | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=str(cwd), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip()).resolve()


def _resolve_project() -> tuple[Path | None, str]:
    """The initialized opentraces project that owns this checkout's shared object
    store: the parent of the COMMON git dir. Location-relative; no hardcoded path.
    Works whether this file lives in the main checkout or a linked worktree.
    """
    common = _git_common_dir(REPO)
    if common is None:
        return None, "git rev-parse --git-common-dir failed (not a git checkout?)"
    project = common.parent
    if not (project / ".opentraces.json").is_file():
        return None, f"owning project {project} has no .opentraces.json (not opted in)"
    return project, ""


def _derive_oracle(project: Path) -> tuple[set[str] | None, int, str]:
    """Independent T2 oracle from the append-only ``git_anchor_created`` log via the
    attribution-only projection path (NO survival recompute), so it is fast on main
    and fix alike and never touches the hanging CLI survival path.

    Returns (oracle_traces8, oracle_floor, error). On any drift from the forge pins
    the error is set -> the caller treats it as SETUP-INVALID, never a weakening.
    """
    try:
        from opentraces.core.trails import build_trail_query_projection_for_commits

        proj = build_trail_query_projection_for_commits(project, {COMMIT})
        rows = proj.anchors_for_commit(COMMIT)
    except Exception as e:  # noqa: BLE001
        return None, 0, f"oracle derivation raised: {type(e).__name__}: {e}"

    if not rows:
        return None, 0, "oracle derivation produced 0 anchor rows for the commit"
    stepless = [r for r in rows if not _is_step(r)]
    if stepless:
        return None, 0, f"oracle has {len(stepless)} non-integer-step row(s) (derivation broken)"
    traces8 = {(r.get("trace_id") or "")[:8] for r in rows if r.get("trace_id")}
    # Cross-check the derivation against the forge pins (append-only => superset OK).
    missing_pin = PINNED_ORACLE_TRACES - traces8
    if missing_pin:
        return None, 0, f"derived oracle missing pinned traces {sorted(missing_pin)} (drift)"
    if WANT[:8] not in traces8:
        return None, 0, f"derived oracle missing the anchoring trace {WANT[:8]} (drift)"
    if len(rows) < PINNED_MIN_ROWS:
        return None, 0, f"derived oracle has {len(rows)} rows < pinned floor {PINNED_MIN_ROWS} (drift)"
    return traces8, len(rows), ""


def _invoke_cli(project: Path) -> tuple[int, str, str, bool]:
    """Live re-observation of the SHIPPED CLI under a hard time budget.

    Returns (rc, stdout, stderr, timed_out). Prefer the GNU ``timeout`` binary
    (the hang -> rc=124, exactly as the forge); fall back to a Python-level
    timeout (TimeoutExpired -> timed_out) when the binary is absent so the hang
    still produces RED on any host.
    """
    base = [str(CLI), "trail", "blame", "commit", COMMIT, "--json", "--project", str(project)]
    timeout_bin = shutil.which("timeout") or shutil.which("gtimeout")
    if timeout_bin:
        try:
            proc = subprocess.run(
                [timeout_bin, str(BUDGET), *base],
                cwd=str(project), capture_output=True, text=True, timeout=BUDGET + 30,
            )
        except subprocess.TimeoutExpired as e:
            return -1, (e.stdout or "") if isinstance(e.stdout, str) else "", \
                   (e.stderr or "") if isinstance(e.stderr, str) else "", True
        # GNU timeout exits 124 when it had to kill the over-budget child.
        return proc.returncode, proc.stdout, proc.stderr, proc.returncode == 124
    # No timeout binary: bound it in-process and kill the whole group on expiry.
    try:
        proc = subprocess.run(
            base, cwd=str(project), capture_output=True, text=True,
            timeout=BUDGET, start_new_session=True,
        )
    except subprocess.TimeoutExpired as e:
        return -1, (e.stdout or "") if isinstance(e.stdout, str) else "", \
               (e.stderr or "") if isinstance(e.stderr, str) else "", True
    return proc.returncode, proc.stdout, proc.stderr, False


def _assert_payload(payload: dict, oracle_traces8: set[str], oracle_floor: int) -> list[str]:
    """Load-bearing assertions against the (independently derived) oracle. Returns
    the list of RED reasons (empty == GREEN)."""
    failures: list[str] = []

    rows = payload.get("trailEvidence")
    if not isinstance(rows, list) or len(rows) == 0:
        # In the fixed payload `traces`/`hookLinked` are empty by construction;
        # trailEvidence is the git_anchor_created-sourced surface.
        return ["trailEvidence empty/missing -> attribution dropped (fast-but-empty)"]

    # (i) universal integer step-bearing (oracle: all rows carry a step).
    stepless = [r for r in rows if not _is_step(r)]
    if stepless:
        failures.append(
            f"{len(stepless)}/{len(rows)} row(s) carry NO integer trace:step "
            "(attribution not preserved / fast-but-stepless)"
        )

    # (ii) completeness: the CLI present-set must be a SUPERSET of the oracle set.
    present8 = {(r.get("trace_id") or "")[:8] for r in rows if r.get("trace_id")}
    missing = oracle_traces8 - present8
    if missing:
        failures.append(
            f"incomplete attribution: oracle traces missing from CLI={sorted(missing)} "
            f"(truncation/forge); present={sorted(present8)}"
        )

    # (iii) row-count lower bound (robust to append-only growth).
    if len(rows) < oracle_floor:
        failures.append(
            f"row count {len(rows)} < oracle floor {oracle_floor} (rows dropped)"
        )

    # (iv) the load-bearing claim: WANT present AND carries an integer step.
    want_rows = [r for r in rows if r.get("trace_id") == WANT]
    if not want_rows:
        failures.append(
            f"anchoring trace {WANT[:8]} ABSENT from trailEvidence (fast-but-wrong)"
        )
    elif not any(_is_step(r) for r in want_rows):
        failures.append(
            f"trace {WANT[:8]} present but carries NO integer trace:step "
            "(step_index null) -> attribution not preserved"
        )
    return failures


def _run(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    if not CLI.is_file():
        print(f"[SETUP-INVALID] opentraces CLI not found at {CLI}")
        return 2

    project, perr = _resolve_project()
    if project is None:
        print(f"[SETUP-INVALID] cannot resolve initialized project: {perr}")
        return 2

    oracle_traces8, oracle_floor, oerr = _derive_oracle(project)
    if oracle_traces8 is None:
        print(f"[SETUP-INVALID] oracle integrity: {oerr}")
        return 2

    # Acquire the payload to assert: captured (proof-of-green) or live re-observation.
    from_json = None
    if len(argv) >= 3 and argv[1] == "--from-json":
        from_json = Path(argv[2])

    if from_json is not None:
        try:
            payload = json.loads(from_json.read_text())
        except Exception as e:  # noqa: BLE001
            print(f"[SETUP-INVALID] --from-json payload unreadable: {e}")
            return 2
    else:
        rc, stdout, stderr, timed_out = _invoke_cli(project)
        if timed_out:
            print(
                f"RED: blame NOT interactive — over budget {BUDGET}s "
                "(the ~6h per-anchor survival hang). stderr_tail="
                f"{stderr.strip()[-200:]!r}"
            )
            return 1
        if rc != 0:
            print(
                f"RED: blame NOT interactive/usable — CLI rc={rc} "
                f"(124=timeout/hang). stderr_tail={stderr.strip()[-200:]!r}"
            )
            return 1
        try:
            payload = json.loads(stdout)
        except Exception as e:  # noqa: BLE001
            print(f"RED: CLI stdout is not valid JSON ({e}); first 200B={stdout[:200]!r}")
            return 1

    failures = _assert_payload(payload, oracle_traces8, oracle_floor)
    if failures:
        print("RED: blame attribution not anchored/complete:")
        for f in failures:
            print("  -", f)
        return 1

    rows = payload["trailEvidence"]
    want_steps = sorted({r.get("step_index") for r in rows if r.get("trace_id") == WANT})
    matched_step = WANT_STEP in want_steps
    # LOAD-BEARING (codex review #2): the pinned raw-log fact (WANT, WANT_STEP=1085)
    # must actually be present in the blame address set, not merely corroborated.
    # This grounds the probe on an immutable git_anchor_created fact independent of
    # whether the shared anchors_for_commit projection is later refactored.
    if not matched_step:
        print(
            f"RED: pinned address ({WANT[:8]}, step {WANT_STEP}) ABSENT from blame "
            f"trailEvidence (got steps={want_steps} for {WANT[:8]})"
        )
        return 1
    n_traces = len({r.get("trace_id") for r in rows if r.get("trace_id")})
    print(
        f"GREEN: blame interactive; trailEvidence rows={len(rows)} over {n_traces} traces, "
        f"all integer step-bearing; superset of {len(oracle_traces8)} oracle traces "
        f"(floor {oracle_floor}); anchoring trace {WANT[:8]} present with step(s)={want_steps} "
        f"(oracle corroboration step {WANT_STEP} matched={matched_step})"
    )
    return 0


def test_probe_a3_blame_anchored():
    rc = _run([sys.argv[0]])  # never consume pytest's argv; always run the live gate
    assert rc != 2, (
        "A3 SETUP-INVALID: oracle derivation / initialized-project resolution failed "
        "(see stdout)"
    )
    assert rc == 0, (
        "A3 RED: blame attribution not interactive/anchored/complete "
        "(hang rc=124, dropped traces, truncation, or stepless rows — see stdout)"
    )


if __name__ == "__main__":
    sys.exit(_run())
