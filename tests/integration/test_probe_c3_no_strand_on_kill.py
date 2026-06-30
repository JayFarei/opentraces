#!/usr/bin/env python3
"""PROBE C3 — no strand on kill (epic #169, hygiene gate).

Committed, grader-owned, location-relative version of forge `c3_no_strand_on_kill.py`.
Drives the SHIPPED `_save_event_snapshot` (`core/trails/event_log.py`) in an
ISOLATED scratch git repo and SIGKILLs at the os.replace boundary (after the tmp
is fully written, before the `finally` unlink), twice, then runs one clean save.

Orphans are counted as ANY non-allowlisted regular file in the cache dir (a
`.tmp`-suffix-only count is gameable). GREEN: two killed saves leave <=1 orphan
(fixed reusable tmp name, no accumulation) AND the clean save reclaims to zero
AND the snapshot actually landed.

RED on main: each killed save mkstemps a NEW random tmpXXXX.tmp -> two kills
leave TWO distinct orphans (2 > 1).

exit 0 GREEN / 1 RED / 3 HARNESS-INVALID (a kill that did not die by SIGKILL).
"""
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

CHILD = "child-kill-at-replace"
# The live snapshot + its watermark are legitimate residents; everything else
# in the cache dir is a reclaimable strand.
ALLOWLIST = {"event_log_snapshot.pkl", "event_log_verified.json"}


def _cache_dir(repo: Path) -> Path:
    return repo / ".git" / "opentraces"


def _count_orphans(repo: Path) -> int:
    d = _cache_dir(repo)
    if not d.is_dir():
        return 0
    return len([p for p in d.iterdir() if p.is_file() and p.name not in ALLOWLIST])


def _orphan_names(repo: Path):
    d = _cache_dir(repo)
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file() and p.name not in ALLOWLIST)


def run_child(repo: Path) -> int:
    from opentraces.core.trails import event_log as el

    def _kill_replace(src, dst):  # noqa: ANN001
        os.replace = _orig_replace
        os.kill(os.getpid(), signal.SIGKILL)  # hard kill: finally is NOT run

    _orig_replace = os.replace
    os.replace = _kill_replace
    el._save_event_snapshot(repo, "deadbeefcafefeed" * 2, list(range(64)))
    return 0  # reaching here means the kill did NOT fire -> harness FAIL


def run_clean(repo: Path) -> int:
    from opentraces.core.trails import event_log as el
    el._save_event_snapshot(repo, "deadbeefcafefeed" * 2, list(range(64)))
    return 0


def _run() -> int:
    import tempfile
    base = Path(tempfile.mkdtemp(prefix="c3-strand-")) / "repo"
    base.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(base)], check=True)

    me = [sys.executable, __file__]
    for i in (1, 2):
        proc = subprocess.run(me + [CHILD, str(base)], capture_output=True)
        if proc.returncode != -signal.SIGKILL:
            print(json.dumps({
                "verdict": "HARNESS-INVALID",
                "reason": f"killed-save #{i} did not die by SIGKILL (rc={proc.returncode})",
                "stderr": proc.stderr.decode(errors="replace")[-400:],
            }))
            shutil.rmtree(base.parent, ignore_errors=True)
            return 3

    leftover_after_kills = _count_orphans(base)
    names_after_kills = _orphan_names(base)

    clean = subprocess.run(me + ["child-clean", str(base)], capture_output=True)
    if clean.returncode != 0:
        print(json.dumps({"verdict": "HARNESS-INVALID", "reason": f"clean save rc={clean.returncode}",
                          "stderr": clean.stderr.decode(errors="replace")[-400:]}))
        shutil.rmtree(base.parent, ignore_errors=True)
        return 3
    leftover_after_clean = _count_orphans(base)
    snapshot_written = (base / ".git" / "opentraces" / "event_log_snapshot.pkl").is_file()

    is_green = (leftover_after_kills <= 1) and (leftover_after_clean == 0) and snapshot_written
    print(json.dumps({
        "verdict": "GREEN" if is_green else "RED",
        "orphans_after_two_killed_saves": leftover_after_kills,
        "orphan_names_after_kills": names_after_kills,
        "orphans_after_clean_save": leftover_after_clean,
        "snapshot_written": snapshot_written,
        "green_requires": "orphans_after_kills<=1 AND orphans_after_clean==0 AND snapshot_written",
    }, indent=2))
    shutil.rmtree(base.parent, ignore_errors=True)
    return 0 if is_green else 1


def test_probe_c3_no_strand_on_kill():
    rc = _run()
    assert rc != 3, "C3 HARNESS-INVALID (kill point not exercised)"
    assert rc == 0, "C3 RED: killed saves accumulate distinct orphan strands (random mkstemp name)"


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == CHILD:
        sys.exit(run_child(Path(sys.argv[2])))
    if len(sys.argv) >= 3 and sys.argv[1] == "child-clean":
        sys.exit(run_clean(Path(sys.argv[2])))
    sys.exit(_run())
