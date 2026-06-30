#!/usr/bin/env python3
"""PROBE C1 — contiguity guard rejects corrupt (epic #169, hygiene gate).

Committed, grader-owned, location-relative version of the loopcraft forge draft
`check_c1_contiguity.py`. Asserts the SHIPPED `_events_contiguous`
(`core/trails/event_log.py`) equals an independent gap-free 1..N verdict over a
seeded battery of dup+gap defeaters engineered so the old endpoint check
(`first==1 and last==len`) is fooled, plus a clean rebuilt 1..50 it must accept,
and a T3 leg over the real on-disk accelerator pkl shaped into a [1,1,3]
defeater of genuine TrailEvent objects.

RED on main : the endpoint check ACCEPTS the dup+gap class -> disagrees.
GREEN on fix: the production guard == true gap-free verdict on every case.

Pure-integer, no git, instant, deterministic. Imports the code-under-test from
the SAME repo this test file lives in (location-relative), so a grader worktree
exercises that worktree's `src/`.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))


def _run() -> list[str]:
    from opentraces.core.trails.event_log import _events_contiguous

    def mk(seqs):
        # Sorted-by-design (like every real snapshot); exposes only event_sequence.
        return [SimpleNamespace(event_sequence=s) for s in sorted(seqs)]

    def old_endpoint(L):
        if not L:
            return True
        return L[0].event_sequence == 1 and L[-1].event_sequence == len(L)

    def true_gapfree(L):
        s = sorted(e.event_sequence for e in L)
        return s == list(range(1, len(s) + 1))

    # dup+gap snapshots where max==len (#dups == #gaps): the silent-wrong class
    # the endpoint (last==len) check cannot catch.
    defeaters = {
        "D1[1,1,3]": mk([1, 1, 3]),
        "D2[1,2,2,4]": mk([1, 2, 2, 4]),
        "D3(dup30,gap31,n=60)": mk([*range(1, 30), 30, 30, *range(32, 61)]),
    }
    valid = {"V[1..50]": mk(range(1, 51))}

    failures: list[str] = []
    for name, L in defeaters.items():
        # Prove each defeater genuinely fools the old check and is genuinely corrupt.
        assert old_endpoint(L), f"TEST BUG: {name} does not fool the old endpoint check"
        assert not true_gapfree(L), f"TEST BUG: {name} is actually contiguous"
        if _events_contiguous(L) is not False:
            failures.append(f"{name}: production guard ACCEPTED a dup+gap snapshot")
    for name, L in valid.items():
        assert true_gapfree(L)
        if _events_contiguous(L) is not True:
            failures.append(f"{name}: production guard REJECTED a clean 1..N rebuild")

    # T3 grounding: real accelerator pkl (resolved via the COMMON git dir so it
    # works from a linked worktree). Informational — never flips a clean pkl RED.
    import subprocess

    proc = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=str(REPO), capture_output=True, text=True,
    )
    pkl = None
    if proc.returncode == 0:
        pkl = Path(proc.stdout.strip()) / "opentraces" / "event_log_snapshot.pkl"
    if pkl is not None and pkl.is_file():
        import pickle

        blob = pickle.loads(pkl.read_bytes())
        by = {}
        for e in blob.get("events", []):
            if getattr(e, "event_sequence", None) in (1, 3):
                by.setdefault(e.event_sequence, e)
            if 1 in by and 3 in by:
                break
        if 1 in by and 3 in by:
            real_def = [by[1], by[1], by[3]]  # [1,1,3] of REAL TrailEvent objects
            if _events_contiguous(real_def) is not False:
                failures.append("real-object [1,1,3] defeater ACCEPTED by production guard")
    return failures


def test_probe_c1_contiguity():
    failures = _run()
    assert not failures, "C1 RED: " + "; ".join(failures)


if __name__ == "__main__":
    f = _run()
    if f:
        print("RESULT: RED")
        for x in f:
            print("  -", x)
        sys.exit(1)
    print("RESULT: GREEN (production guard == true gap-free verdict on every case)")
    sys.exit(0)
