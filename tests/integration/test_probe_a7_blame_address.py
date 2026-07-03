#!/usr/bin/env python3
"""PROBE A7 — trail blame commit address set == git_anchor_created oracle (epic #169).

Committed, grader-owned, location-relative RE-SCOPE of the loopcraft forge draft
`check_a3.py` (hardened against `check_a3_hardened.py` + `ground_oracle.py`).
Drives the SHIPPED CLI ``opentraces trail blame commit <sha> --json`` under a hard
90s interactivity budget and asserts the distinct ``(trace_id, step_index)``
ADDRESS set carried in ``trailEvidence`` EQUALS the address set derived
INDEPENDENTLY from the append-only ``git_anchor_created`` event log
(``anchors_for_commit``) — never trusting blame's own narration.

Done-claim under test: ``trail blame commit 884a1a6b... --json`` RETURNS within a
bounded interactive budget (NOT the ~6h per-anchor survival recompute hang) AND
its git-anchor attribution (``trailEvidence``) addresses EXACTLY the same
``(trace_id, step_index)`` pairs the canonical event log records for that commit.

Independent oracle (T2 provenance, stable across RED/GREEN because both the buggy
and the fixed code share ``anchors_for_commit`` — the bug is only that blame
routes attribution through ``anchors_for_commit_with_survival``, blame.py:586):
commit 884a1a6b carries >=951 ``git_anchor_created`` rows over EXACTLY the 10
pinned traces, collapsing to 457 distinct ``(trace_id, step_index)`` addresses;
trace 66733cf8-...-30188 appears with step_index=1085. The oracle is recomputed
from REPO's own .git via ``build_trail_query_projection_for_commits`` ->
``anchors_for_commit`` (the same API the forge draft uses), not hard-coded.

Defeaters killed (every forge-draft assertion preserved, never weakened; A7
set-equality hardening added):
  (1) HANG / non-interactive          -> the 90s budget trips (rc=124) -> RED.
  (2) fast-but-empty (dropped rows)    -> trailEvidence empty/missing -> RED.
  (3) fast-but-wrong (different trace) -> WANT id absent / a pinned trace
                                          missing / a stepless row -> RED.
  (4) set-divergence (A7 core)         -> blame address set != event-log oracle
                                          set -> RED (a "fast but incomplete/
                                          mangled" fix cannot pass).
  (5) malformed address                -> any non-UUID trace_id or non-negative
                                          -int step_index -> RED.

RED on current main : blame routes through the per-anchor survival recompute over
                      951 anchors and HANGS -> rc=124 -> RED.
GREEN only on the fix: blame returns within budget and its address set equals the
                      event-log oracle exactly, every address well-formed.

False-RED guards: assertions are over set-membership + non-neg-int steps, which
are order/timestamp/warm-vs-cold independent; ``trace_id`` & ``step_index`` are
immutable facts of append-only ``git_anchor_created`` events. The oracle build
(which dominates the fixed blame's cost) runs first and warms the scoped
event-log read, so a cold index cannot push a fixed blame past the generous 90s
budget (fixed blame ~12s even cold; the hang is hours).

Location-relative: imports the code-under-test from the SAME repo this file lives
in (``Path(__file__).resolve().parents[2]``) and drives that repo's
``.venv/bin/opentraces`` (editable to that ``src/``), so a grader worktree
exercises that worktree's code. The CLI is run from an opted-in project dir
resolved from this repo's own git (the worktree itself, else its git-common-dir
parent) so the shipped ``opentraces init`` gate is satisfied WITHOUT mutating any
state; ``trailEvidence`` is read off the shared canonical event log regardless of
which opted-in dir is used, so the address set is identical either way.

exit 0 GREEN / 1 RED / 2 SETUP-INVALID (CLI or opted-in project dir absent) /
3 ORACLE-INVALID (the independent measuring stick failed its own pins). A
non-GREEN verdict makes the pytest FAIL; it never silently passes.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

# --- CLI under test + the exact id/oracle pins (kept identical to the forge draft).
OT = REPO / ".venv" / "bin" / "opentraces"
COMMIT = "884a1a6bc1d598f13351140d2da03e64d2062b90"
WANT = "66733cf8-9003-4f09-bca8-e4cda7030188"   # the anchoring trace (T2 oracle)
WANT_STEP = 1085                                 # grounded step_index from event log
# T2 oracle (from the append-only git_anchor_created events, independent of CLI):
ORACLE_TRACES = {
    "0119a3e8", "1760881a", "2d3c813a", "42192f76", "66733cf8",
    "6dc2371f", "993b9584", "b26e1d03", "cbcc3bcc", "df5e8614",
}
ORACLE_MIN_ROWS = 951
# Interactivity bar: the fixed attribution-only blame returns in ~12s even cold;
# the buggy per-anchor survival recompute hangs for HOURS. 90s cleanly separates
# them — it trips the hang as rc=124 yet never false-REDs a cold-index fixed blame.
BUDGET = 90

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _is_uuid(v) -> bool:
    return isinstance(v, str) and bool(_UUID_RE.match(v))


def _is_nonneg_int(v) -> bool:
    # bool is an int subclass; an immutable step_index must be a real int >= 0.
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _git_common_parent() -> Path | None:
    """The canonical checkout that owns this repo's (possibly shared) git dir."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(REPO), capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return None
    return Path(out).resolve().parent if out else None


def _opted_in_project_dir() -> Path | None:
    """Location-relative opted-in project dir for the CLI's shipped init gate.

    Precedence: this worktree itself (most location-relative), else the canonical
    checkout at the git-common-dir parent (a linked worktree shares that
    checkout's event log + bucket). The opt-in marker (``.opentraces.json``) is
    the SHIPPED ground truth, checked independently of any code under test. No
    state is mutated. ``trailEvidence`` is event-log-derived, so either dir yields
    the identical address set.
    """
    for cand in (REPO, _git_common_parent()):
        if cand is not None and (cand / ".opentraces.json").is_file():
            return cand.resolve()
    return None


def _build_oracle() -> tuple[set[tuple[str, int]], list[str]]:
    """Independent ``(trace_id, step_index)`` address oracle from the canonical
    ``git_anchor_created`` event log, via the same API the forge draft uses
    (``build_trail_query_projection_for_commits`` -> ``anchors_for_commit``).

    Returns ``(address_set, problems)``; a non-empty ``problems`` list means the
    measuring stick itself failed its pins -> ORACLE-INVALID, never RED. Building
    this first also warms the scoped event-log read the fixed blame relies on."""
    problems: list[str] = []
    from opentraces.core.trails import build_trail_query_projection_for_commits

    proj = build_trail_query_projection_for_commits(REPO, {COMMIT})
    rows = proj.anchors_for_commit(COMMIT)
    if not rows:
        problems.append(
            "oracle anchors_for_commit returned ZERO rows (event log unavailable)"
        )
        return set(), problems
    if len(rows) < ORACLE_MIN_ROWS:
        problems.append(
            f"oracle rows {len(rows)} < pinned floor {ORACLE_MIN_ROWS} (event log changed)"
        )
    prefixes = {(r.get("trace_id") or "")[:8] for r in rows}
    missing = ORACLE_TRACES - prefixes
    if missing:
        problems.append(f"oracle missing pinned traces {sorted(missing)}")
    addrs: set[tuple[str, int]] = set()
    for r in rows:
        tid, sidx = r.get("trace_id"), r.get("step_index")
        if not _is_uuid(tid):
            problems.append(f"oracle row trace_id not a UUID: {tid!r}")
            continue
        if not _is_nonneg_int(sidx):
            problems.append(
                f"oracle row step_index not a non-neg int: {sidx!r} "
                f"(trace {str(tid)[:8]})"
            )
            continue
        addrs.add((tid, sidx))
    return addrs, problems


def _run_blame(project_dir: Path) -> tuple[bool, int, str]:
    """Re-observe the WORLD: run the real CLI under the 90s budget from an
    opted-in project dir. Returns ``(timed_out, rc, stdout)``. Prefers the
    ``timeout`` binary (literal rc=124 on the hang, exactly like the forge draft)
    and falls back to a subprocess timeout where the binary is absent."""
    cmd = [str(OT), "trail", "blame", "commit", COMMIT, "--json"]
    tbin = shutil.which("timeout") or shutil.which("gtimeout")
    try:
        if tbin:
            proc = subprocess.run(
                [tbin, str(BUDGET), *cmd],
                cwd=str(project_dir), capture_output=True, text=True,
                timeout=BUDGET + 30,
            )
            return (proc.returncode == 124), proc.returncode, proc.stdout or ""
        proc = subprocess.run(
            cmd, cwd=str(project_dir), capture_output=True, text=True, timeout=BUDGET,
        )
        return False, proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired as exc:  # subprocess-timeout fallback path
        out = exc.stdout if isinstance(exc.stdout, str) else (
            exc.stdout.decode(errors="replace") if exc.stdout else ""
        )
        return True, 124, out


def _run() -> int:
    # SETUP VALIDITY: the CLI under test + an opted-in project dir must exist.
    if not OT.is_file():
        print(f"[SETUP-INVALID] opentraces CLI not found at {OT}")
        return 2
    project_dir = _opted_in_project_dir()
    if project_dir is None:
        print(
            "[SETUP-INVALID] no opted-in project dir found (neither this worktree "
            "nor its git-common-dir parent has a .opentraces.json marker); "
            "`trail blame commit` cannot pass the shipped init gate"
        )
        return 2

    # ORACLE: build the independent address set from the canonical event log FIRST
    # (also warms the scoped event-log read the fixed blame relies on).
    try:
        oracle_addrs, oracle_problems = _build_oracle()
    except Exception as e:  # noqa: BLE001
        print(f"[ORACLE-INVALID] failed to build event-log oracle: {type(e).__name__}: {e}")
        return 3
    if oracle_problems:
        print("[ORACLE-INVALID] independent measuring stick failed its own pins:")
        for p in oracle_problems:
            print("  -", p)
        return 3

    # MEASURE: the real CLI call under the interactivity budget. A hang -> RED.
    timed_out, rc, stdout = _run_blame(project_dir)

    fail: list[str] = []
    if timed_out:
        fail.append(
            f"HANG: blame did not return within {BUDGET}s (rc=124) -> per-anchor "
            "survival recompute still in the blame path (non-interactive)"
        )
    elif rc != 0:
        fail.append(f"NONZERO_RC: blame exited rc={rc} (not interactive/usable)")

    rows = None
    if (not timed_out) and rc == 0:
        try:
            payload = json.loads(stdout)
        except Exception as e:  # noqa: BLE001
            fail.append(f"BAD_JSON: CLI stdout is not valid JSON ({e}); first 200B={stdout[:200]!r}")
            payload = None
        if payload is not None:
            rows = payload.get("trailEvidence")
            if not isinstance(rows, list) or len(rows) == 0:
                fail.append(
                    "EMPTY: trailEvidence empty/missing -> attribution dropped (fast-but-empty)"
                )
                rows = None

    if rows is not None:
        # --- forge-draft assertions (check_a3.py / check_a3_hardened.py), preserved ---
        # (a) row-count floor: an anchored commit yields >= the oracle's row count.
        if len(rows) < ORACLE_MIN_ROWS:
            fail.append(
                f"ROWS_DROPPED: trailEvidence {len(rows)} rows < oracle floor {ORACLE_MIN_ROWS}"
            )
        # (b) universal step-bearing: EVERY row carries a non-neg int trace:step.
        stepless = [r for r in rows if not _is_nonneg_int(r.get("step_index"))]
        if stepless:
            ex = stepless[0]
            fail.append(
                f"STEPLESS: {len(stepless)} trailEvidence row(s) carry no non-neg int "
                f"step_index (e.g. {ex.get('step_index')!r} for trace "
                f"{str(ex.get('trace_id'))[:8]})"
            )
        # (c) the anchoring trace WANT must be present with a non-null int step.
        want_rows = [r for r in rows if r.get("trace_id") == WANT]
        if not want_rows:
            present = sorted({(r.get("trace_id") or "?")[:8] for r in rows})
            fail.append(
                f"WRONG: anchoring trace {WANT[:8]} ABSENT from trailEvidence; present={present}"
            )
        elif not any(_is_nonneg_int(r.get("step_index")) for r in want_rows):
            fail.append(
                f"WANT_STEPLESS: trace {WANT[:8]} present but carries no non-neg int step_index"
            )
        # (d) all 10 pinned oracle traces present in blame's attribution.
        present_prefixes = {(r.get("trace_id") or "")[:8] for r in rows}
        missing = ORACLE_TRACES - present_prefixes
        if missing:
            fail.append(f"INCOMPLETE: oracle traces missing from blame {sorted(missing)}")

        # --- A7 core hardening: every address well-formed + SET EQUALITY ---
        blame_addrs: set[tuple[str, int]] = set()
        malformed: list[str] = []
        for r in rows:
            tid, sidx = r.get("trace_id"), r.get("step_index")
            if not _is_uuid(tid):
                malformed.append(f"trace_id={tid!r}")
                continue
            if not _is_nonneg_int(sidx):
                malformed.append(f"step_index={sidx!r}(trace {str(tid)[:8]})")
                continue
            blame_addrs.add((tid, sidx))
        if malformed:
            fail.append(
                f"MALFORMED_ADDR: {len(malformed)} malformed address(es), e.g. {malformed[:3]}"
            )
        # THE load-bearing assertion: blame's distinct (trace_id, step_index)
        # address set EQUALS the independent git_anchor_created event-log oracle's.
        if blame_addrs != oracle_addrs:
            only_blame = sorted(f"{t[:8]}:{s}" for t, s in (blame_addrs - oracle_addrs))[:5]
            only_oracle = sorted(f"{t[:8]}:{s}" for t, s in (oracle_addrs - blame_addrs))[:5]
            fail.append(
                "SET_DIVERGENCE: blame address set != event-log oracle set "
                f"(blame={len(blame_addrs)} oracle={len(oracle_addrs)}; "
                f"only_in_blame={only_blame}; only_in_oracle={only_oracle})"
            )
        # LOAD-BEARING (codex review #2): the pinned raw-log address fact
        # (WANT, WANT_STEP=1085) must be present on BOTH sides, grounding the
        # probe on an immutable git_anchor_created fact, not just set-equality.
        pinned = (WANT, WANT_STEP)
        if pinned not in oracle_addrs:
            fail.append(f"PIN_MISSING_ORACLE: ({WANT[:8]}, step {WANT_STEP}) not in event-log oracle")
        if pinned not in blame_addrs:
            fail.append(f"PIN_MISSING_BLAME: ({WANT[:8]}, step {WANT_STEP}) absent from blame address set")

    if fail:
        print("VERIFIER_RESULT=RED")
        for f in fail:
            print("  -", f)
        return 1

    # GREEN: surface the load-bearing facts.
    matched_step = any(
        r.get("trace_id") == WANT and r.get("step_index") == WANT_STEP for r in rows
    )
    print("VERIFIER_RESULT=GREEN")
    print(
        f"  blame interactive from {project_dir.name}; trailEvidence rows={len(rows)} "
        f"collapse to {len(oracle_addrs)} (trace_id,step_index) addresses == event-log "
        f"oracle; all UUID+non-neg-int; {len(ORACLE_TRACES)} pinned traces present; "
        f"WANT {WANT[:8]} present (oracle step {WANT_STEP} matched={matched_step})"
    )
    return 0


def test_probe_a7_blame_address():
    rc = _run()
    if rc == 2:
        pytest.skip("A7 SETUP-INVALID (opentraces CLI binary or opted-in project dir absent in this worktree)")
    if rc == 3:
        pytest.skip("A7 ORACLE-INVALID (independent git_anchor_created event-log oracle failed its own pins)")
    assert rc == 0, (
        "A7 RED: `trail blame commit` is non-interactive (hangs the per-anchor "
        "survival recompute over 951 anchors) and/or its trace:step address set "
        "diverges from the canonical git_anchor_created event-log oracle"
    )


if __name__ == "__main__":
    sys.exit(_run())
