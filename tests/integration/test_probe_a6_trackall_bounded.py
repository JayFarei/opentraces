#!/usr/bin/env python3
"""PROBE A6 — trail track --all is bounded by --limit (epic #169).

Committed, grader-owned, location-relative version of the loopcraft forge draft
`a6_check.py`.

Done-claim under test: `trail track --all --limit 5 --json` is INTERACTIVE
(completes within a tight budget on the real ~1900-trace bucket) AND enumerates
exactly 5 REAL trace patches (real distinct patch_ids, real UUID trace_ids). The
budget proves the WORK is bounded by --limit, not a post-walk slice of a full
trail walk.

Re-observes the WORLD only: the CLI's raw exit code (124 == hang, unfakeable),
the CLI's own stdout JSONL, and an INDEPENDENT direct read of the project event
log's `trace_patch_created` registry (via `read_events_scoped`, the same API the
forge uses, against the SAME git event ref this worktree shares). Reads no agent
narration / status.

Kills:
  * limit-bounds-output-not-work (full walk then [:5]) -> command hangs past the
    budget on this corpus -> rc 124 -> RED. (RED on main today.)
  * fast-but-empty -> requires exactly 5 records, each with a real, well-formed,
    non-null UUID trace_id and a real distinct patch_id drawn from the registry.

LOCATION-RELATIVE: imports the code-under-test and runs the CLI from the SAME
repo this test file lives in, so a grader worktree exercises that worktree's
`src/` and `.venv`.

exit 0 GREEN / 1 RED / 2 SETUP-INVALID (registry precondition) /
3 HARNESS-INVALID (missing `timeout` or CLI binary). A non-GREEN verdict fails
the pytest case; it never silently passes.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

OT = REPO / ".venv" / "bin" / "opentraces"
BUDGET = "15"  # seconds; RED on main (survival storm >> 15s), generous for a bounded fix
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _run() -> int:
    # 1) Build the REAL registry from an INDEPENDENT direct read of the project
    #    event log (trace_patch_created) via the same API the forge uses. This
    #    also WARMS the event_index scoped accelerator so a bounded fix is not
    #    flaky on a cold cache (false-RED guard). It does NOT warm main's
    #    survival/git_anchor_created path, so RED on main holds.
    from opentraces.core.trails import read_events_scoped

    evs = read_events_scoped(REPO, event_types={"trace_patch_created"})
    real_pids, real_tids = set(), set()
    for e in evs:
        if e.event_type != "trace_patch_created":
            continue
        pid = e.payload.get("trace_patch_id")
        tid = e.trace_id or e.payload.get("trace_id")
        if isinstance(pid, str) and pid:
            real_pids.add(pid)
        if isinstance(tid, str) and tid:
            real_tids.add(tid)
    if len(real_pids) < 100:
        # Sanity: a near-empty registry would make membership trivially pass;
        # the ticket corpus has ~21k trace_patch_created events.
        print(
            "[SETUP-INVALID] registry too small "
            f"({len(real_pids)} patches < 100) - bad grounding for the ticket corpus"
        )
        return 2

    # 2) Re-observe the WORLD: run the exact target command under a hard budget.
    #    rc 124 == timeout fired == hang == the bounded-work symptom (unfakeable).
    timeout_bin = shutil.which("timeout") or shutil.which("gtimeout")
    if timeout_bin is None:
        print("[HARNESS-INVALID] no `timeout`/`gtimeout` on PATH; cannot bound the run")
        return 3
    if not OT.is_file():
        print(f"[HARNESS-INVALID] opentraces CLI not found at {OT}")
        return 3

    proc = subprocess.run(
        [timeout_bin, BUDGET, str(OT), "trail", "track", "--all", "--limit", "5", "--json"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    rc = proc.returncode
    out = proc.stdout

    fails: list[str] = []
    if rc == 124:
        fails.append(f"HANG: timed out at {BUDGET}s (rc=124) - work NOT bounded by --limit")
    elif rc != 0:
        fails.append(f"nonzero exit rc={rc}; stderr_tail={proc.stderr.strip()[-300:]!r}")

    # 3) Parse the command's own stdout as JSONL and assert exactly 5 REAL records.
    recs = []
    if rc == 0:
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                fails.append(f"non-JSON line in output: {line[:80]!r}")
        if len(recs) != 5:
            fails.append(f"expected exactly 5 records, got {len(recs)}")
        seen_pids = []
        for i, r in enumerate(recs):
            pid = r.get("trace_patch_id")
            tid = r.get("trace_id")
            if not (isinstance(pid, str) and pid):
                fails.append(f"rec[{i}] missing/empty trace_patch_id")
                continue
            seen_pids.append(pid)
            if pid not in real_pids:
                fails.append(f"rec[{i}] trace_patch_id not in real registry: {pid[:40]}")
            # REAL trace_id grounding: non-null, UUID-shaped, present in registry.
            if not (isinstance(tid, str) and tid):
                fails.append(f"rec[{i}] empty/null trace_id (fast-but-empty)")
            elif not UUID_RE.match(tid):
                fails.append(f"rec[{i}] trace_id not UUID-shaped: {tid!r}")
            elif tid not in real_tids:
                fails.append(f"rec[{i}] trace_id not a REAL trail trace: {tid}")
        # distinct patch ids (no degenerate repeated row masquerading as 5)
        if len(seen_pids) == 5 and len(set(seen_pids)) != 5:
            fails.append("the 5 records are not distinct patches (repeated rows)")

    if fails:
        print("A6_FAIL: " + " | ".join(fails))
        return 1
    print(
        f"A6_PASS: GREEN — 5 distinct real patches enumerated within {BUDGET}s "
        f"(registry={len(real_pids)} patches / {len(real_tids)} traces)"
    )
    return 0


def test_probe_a6_trackall_bounded():
    rc = _run()
    if rc == 2:
        pytest.skip("A6 SETUP-INVALID: trace_patch_created registry too small (<100) for grounding")
    if rc == 3:
        pytest.skip("A6 HARNESS-INVALID: missing `timeout`/`gtimeout` or opentraces CLI binary")
    assert rc == 0, (
        "A6 RED: `trail track --all --limit 5 --json` is not bounded by --limit "
        "(hang past budget) or did not enumerate exactly 5 real distinct patches"
    )


if __name__ == "__main__":
    sys.exit(_run())
