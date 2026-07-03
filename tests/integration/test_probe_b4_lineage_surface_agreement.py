#!/usr/bin/env python3
"""PROBE B4 — lineage/surface anchor-set agreement (epic #169, done-claim gate).

Committed, grader-owned, location-relative version of the loopcraft forge draft
`b4_lineage_surface_agreement.py`.

Done-claim gated: the per-trace anchor SURFACE (the bucket `trace.json`
projection, which the manifest + lineage derive from) must surface the SAME
(patch, commit) anchor set the canonical events store holds
(`git_anchor_created`), and must be non-empty whenever the events store's
anchored_count > 0.

Re-observes the WORLD, never narration:
  - events store : git_anchor_created events read from the canonical Git event
                   log via `read_events_scoped` (fresh OID index, ~0.3s) — the
                   SAME oracle API the forge used.
  - surface      : the bucket per-trace `trace.json` patches[].anchor projection.
  - git          : `git show --name-only <commit>` to falsify phantom anchors.

GREEN only if every pinned fixture's surface set EXACTLY equals the events set
AND no bucket-claimed anchor is refuted by git AND the reader cleared a sanity
floor. RED on any divergence, printing the exact symptom. Pinned fixtures + set
comparison => immune to ordering / warm-cold index / sampling / timestamps. The
git cross-check makes the headline phantom (#139) immune to the stale-snapshot
defeater: a stale projection can LAG the live log but can never invent an anchor
to a commit that never touched the file.

RED on main : the surface DISAGREES with the events store (phantom over-
              attribution, gross over-projection, and an empty surface while
              anchored>0) — see the three pinned fixtures.
GREEN on fix: every pinned fixture's surface set == events set, no phantoms.

Location-relative: imports the code-under-test and reads the canonical event log
from the SAME repo this test file lives in (a grader worktree exercises that
worktree's `src/` and shares its common git dir), and resolves the bucket under
`OT_BUCKET_ROOT` so it also works against a re-derived COPY of the bucket.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

# Bucket root is env-overridable so the probe works against a re-derived COPY of
# the bucket; the per-trace trace.json lives under traces/v1/<slug>/<trace_id>/.
BUCKET_ROOT = Path(os.environ.get("OT_BUCKET_ROOT", str(Path.home() / ".opentraces" / "bucket")))
BUCKET_TRACES = BUCKET_ROOT / "traces" / "v1"

# Pinned real fixtures (verified divergent on main today). Each is a STABLE
# trace id; we compare SETS so order / warmth / sampling cannot move the result.
FIX_PHANTOM = "4ee84d36-0007-43bb-b85a-028070f8b731"   # #139: 3 phantom anchors @ e774c788 (git-refutable)
FIX_OVER = "d24e8e96-4c02-46a3-b47c-55b6bca846c1"      # 190 bucket anchors @ one outcome commit, events=28 across many
FIX_UNDER = "cbcc3bcc-7abb-40e9-b81a-a4d2dc364428"     # events=122, bucket=0 (empty surface when anchored>0)
FIXTURES = [FIX_PHANTOM, FIX_OVER, FIX_UNDER]
SANITY_FLOOR = 1000  # total git_anchor_created events; a broken/empty read must not read as "agreement"


def events_anchor_sets():
    """trace_id -> set((trace_patch_id, commit12)) from canonical git_anchor_created.

    Reads through the SAME oracle API the forge used (`read_events_scoped`
    scoped to {git_anchor_created}) against this repo's canonical Git event log.
    """
    from opentraces.core.trails.event_log import read_events_scoped

    evs = read_events_scoped(REPO, event_types={"git_anchor_created"})
    by_trace = defaultdict(set)
    for e in evs:
        p = e.payload or {}
        tp = p.get("trace_patch_id")
        cm = (p.get("commit_id") or {}).get("hex")
        if e.trace_id and tp and cm:
            by_trace[e.trace_id].add((tp, cm[:12]))
    return len(evs), by_trace


def bucket_anchor_set(tid):
    """set((patch_id, commit12)) and patch_id->file_path from the bucket trace.json projection.

    Returns (set, files, present_bool). The project-slug directory is globbed
    generically (never hardcoded), so a re-derived bucket copy resolves too.
    """
    if not BUCKET_TRACES.is_dir():
        return set(), {}, False
    for proj in sorted(BUCKET_TRACES.iterdir()):
        if not proj.is_dir():
            continue
        tj = proj / tid / "trace.json"
        if tj.exists():
            with open(tj) as fh:
                t = json.load(fh)
            s = set()
            files = {}
            for pp in t.get("patches", []):
                a = pp.get("anchor") or {}
                if a.get("found"):
                    c = (a.get("commit") or a.get("commit_sha") or "")[:12]
                    pid = pp.get("patch_id") or pp.get("trace_patch_id") or pp.get("id")
                    if c and pid:
                        s.add((pid, c))
                        files[pid] = pp.get("file_path") or pp.get("path")
            return s, files, True
    return set(), {}, False


def commit_touches(commit, path):
    """True iff git records `commit` as modifying `path` (re-observed from git)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "show", "--name-only", "--format=", commit],
            capture_output=True, text=True, timeout=20,
        )
    except subprocess.TimeoutExpired:
        return None  # cannot decide -> do not silently pass
    touched = {l.strip() for l in out.stdout.splitlines() if l.strip()}
    return path in touched


def _run() -> int:
    """0 GREEN / 2 RED / 3 SETUP-INVALID. Mirrors the forge return scheme (0/2)."""
    if not BUCKET_TRACES.is_dir():
        print(f"[SETUP-INVALID] bucket traces dir not found under OT_BUCKET_ROOT: {BUCKET_TRACES}")
        return 3

    failures = []

    total_events, ev = events_anchor_sets()
    if total_events < SANITY_FLOOR:
        # A broken reader must FAIL, never masquerade as vacuous agreement.
        print(f"RED[sanity]: git_anchor_created reader returned {total_events} (< floor {SANITY_FLOOR}); "
              f"agreement would be vacuous")
        return 2

    for tid in FIXTURES:
        eset = ev.get(tid, set())
        bset, files, present = bucket_anchor_set(tid)

        # --- Half 2 of the done-claim: non-empty surface whenever anchored>0 ---
        if eset and (not present or not bset):
            failures.append(
                f"RED[empty-surface] {tid[:12]}: events anchored_count={len(eset)}>0 but "
                f"surface bucket anchors={len(bset)} (present={present}) -> empty/missing when anchored"
            )

        # --- Half 1 of the done-claim: surface set EXACTLY equals events set ---
        if eset != bset:
            over = bset - eset    # bucket claims, events store does NOT hold (phantom / over-attribution)
            under = eset - bset   # events store holds, surface omits (under-projection)
            failures.append(
                f"RED[set-disagree] {tid[:12]}: |events|={len(eset)} |surface|={len(bset)} "
                f"overlap={len(eset & bset)} phantom(bucket-only)={len(over)} missing(events-only)={len(under)}"
            )

            # --- staleness-immune git falsification of phantom anchors ---
            # Check up to 5 phantom anchors against real git: a claimed commit
            # that NEVER touched the patch file is a proven phantom (a stale
            # snapshot can lag but can never invent such an anchor).
            refuted = []
            for pid, c12 in sorted(over)[:5]:
                fp = files.get(pid)
                if not fp:
                    continue
                t = commit_touches(c12, fp)
                if t is False:
                    refuted.append((pid[:12], c12, fp))
            for pid12, c12, fp in refuted:
                failures.append(
                    f"  GIT-REFUTED phantom {tid[:12]}: bucket claims patch {pid12} anchored @ {c12} "
                    f"but `git show {c12}` did NOT touch {fp}"
                )

    if failures:
        print("\n".join(failures))
        print(f"\nVERDICT: RED ({len(failures)} symptom(s); reader saw {total_events} git_anchor_created events)")
        return 2

    print(f"VERDICT: GREEN (all {len(FIXTURES)} fixtures: surface anchor set == events store; "
          f"no git-refuted phantoms; reader saw {total_events} events)")
    return 0


def test_probe_b4_lineage_surface_agreement():
    rc = _run()
    if rc == 3:
        pytest.skip(
            "B4 SETUP-INVALID: bucket traces dir not found "
            f"(set OT_BUCKET_ROOT; looked under {BUCKET_TRACES})"
        )
    assert rc == 0, (
        "B4 RED: bucket anchor SURFACE disagrees with the canonical git_anchor_created "
        "events store (phantom over-attribution / over-projection / empty surface when anchored>0)"
    )


if __name__ == "__main__":
    sys.exit(_run())
