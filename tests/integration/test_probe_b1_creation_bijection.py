#!/usr/bin/env python3
"""PROBE B1 — creation bijection (epic #169, restore `trail track` lineage).

Committed, grader-owned, location-relative version of the loopcraft forge draft
`b1_record_events_bijection.py`.

DONE-CLAIM (one checkable sentence):
  For the #139 trace, every patch stamped ``anchor.found=True`` in the bucket
  trace.json has a backing ``git_anchor_created`` event (matching
  ``trace_patch_id``) in the canonical Git event log — i.e. ZERO found=True
  patches lack a creation event (the B1 creation gap is closed).

DEFEATER KILLED:
  "bucket/manifest claims anchored while lineage shows zero anchors" — a patch
  whose trace.json says ``anchor.found=True`` while the canonical append-only
  log holds no ``git_anchor_created`` for that patch_id (over-attribution).

WORLD RE-OBSERVED (never any agent narration):
  - the bucket trace.json on disk (the bucket/manifest-side claim), AND
  - the canonical Git event log via ``read_events_for_trace`` (the lineage), the
    head-pinned single source of truth — NOT the lagging trail.jsonl.gz
    companion.

RUNG: T3 (grounded replay/re-observation over a T2 append-only provenance log).
BLAST: high — the trail/track attribution interface ships on this bijection.

GUARDS:
  false-RED:
    * Reader-returns-empty -> false RED: a POSITIVE CONTROL trace whose
      bijection genuinely holds must pass; if the reader cannot surface even the
      known-good anchors the probe reports INCONCLUSIVE (rc=2), never a clean
      RED. The control requires found_true>0 AND backed>0 AND no missing.
    * Sets of patch_ids -> ordering/sampling independent; ALL found=True patches
      checked, no sampling. No timestamps in the assertion.
    * read_events_for_trace is head-pinned, so a stale warm index cannot leak the
      lagging companion's view.
  assertion-tamper:
    * verdict derived purely from on-disk world state; no env/sentinel can flip
      it.
    * fixture-present guard: target trace.json must exist with a non-empty
      ``patches`` array, else INCONCLUSIVE (a deleted fixture cannot fake GREEN).
    * the missing patch_ids are printed, so the exact symptom is visible.

GREEN after the real fix ("anchor.found derives from git_anchor_created events"):
  either the 3 unbacked patches gain creation events (found stays True, backed)
  OR they flip found=False (LHS empties) — both satisfy the bijection. Patches
  remain present, so the fixture-present guard still holds.

Location-relative: imports the code-under-test (read_events_for_trace) from the
SAME repo this test file lives in, so a grader worktree exercises that
worktree's ``src/`` and that worktree's canonical Git event log. The bucket
witness trace.json is resolved generically by globbing the project-slug dirs
under ``OT_BUCKET_ROOT`` for the pinned trace id, so it also works against a
re-derived COPY of the bucket.

Exit: 0 GREEN, 1 RED, 2 INCONCLUSIVE. A non-GREEN verdict FAILS the pytest.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

# Bucket root is overridable so the probe also runs against a re-derived COPY of
# the bucket; the project-slug dir is NOT hardcoded — it is globbed per trace id.
BUCKET_ROOT = Path(
    os.environ.get("OT_BUCKET_ROOT", str(Path.home() / ".opentraces" / "bucket"))
)

TARGET = "4ee84d36-0007-43bb-b85a-028070f8b731"    # #139 creation-gap trace
POSITIVE = "4f0e4e98-63af-44f7-bb46-6f43fc5856c1"  # bijection-holds control


def _find_trace_json(trace_id: str) -> Path | None:
    """Locate the bucket trace.json for a trace id by globbing the project-slug
    dirs under BUCKET_ROOT (slug NOT hardcoded), so a re-derived bucket COPY
    still resolves. Returns the first matching path, or None."""
    matches = sorted(
        (BUCKET_ROOT / "traces" / "v1").glob(f"*/{trace_id}/trace.json")
    )
    for m in matches:
        if m.is_file():
            return m
    return None


def found_true_patch_ids(trace_id: str):
    """Patch ids with anchor.found is True, read straight off bucket trace.json.
    Returns (set_of_found_pids, total_patches, trace_json_exists)."""
    tj = _find_trace_json(trace_id)
    if tj is None or not tj.exists():
        return set(), 0, False
    d = json.loads(tj.read_text())
    patches = d.get("patches") or []
    fp = {
        p.get("patch_id")
        for p in patches
        if isinstance(p.get("anchor"), dict) and p["anchor"].get("found") is True
    }
    fp.discard(None)
    return fp, len(patches), True


def created_anchor_patch_ids(trace_id: str):
    """Distinct trace_patch_ids carried by git_anchor_created events in the
    canonical log for this trace (read from REPO's .git). Returns
    (set, total_events_for_trace)."""
    from opentraces.core.trails.event_log import read_events_for_trace

    evs = read_events_for_trace(REPO, trace_id)
    ap = {
        (e.payload or {}).get("trace_patch_id")
        for e in evs
        if e.event_type == "git_anchor_created"
    }
    ap.discard(None)
    return ap, len(evs)


def _run() -> int:
    """0 GREEN / 1 RED / 2 INCONCLUSIVE."""
    # --- positive control: prove the reader surfaces creations when they exist -
    pc_found, pc_npatch, pc_exists = found_true_patch_ids(POSITIVE)
    pc_anchors, pc_nev = created_anchor_patch_ids(POSITIVE)
    pc_missing = pc_found - pc_anchors
    reader_ok = (
        pc_exists
        and len(pc_found) > 0
        and len(pc_anchors) > 0
        and not pc_missing
    )
    if not reader_ok:
        print(
            "PROBE_INCONCLUSIVE B1-creation-bijection: positive control failed "
            f"(trace {POSITIVE[:8]} exists={pc_exists} found={len(pc_found)} "
            f"backed={len(pc_anchors)} missing={len(pc_missing)} events={pc_nev}) — "
            "reader cannot confirm known-good anchors; not a clean RED."
        )
        return 2

    # --- fixture-present guard ------------------------------------------------
    tgt_found, tgt_npatch, tgt_exists = found_true_patch_ids(TARGET)
    if not tgt_exists or tgt_npatch == 0:
        print(
            "PROBE_INCONCLUSIVE B1-creation-bijection: target fixture missing "
            f"(trace {TARGET[:8]} exists={tgt_exists} patches={tgt_npatch}); a "
            "deleted fixture must not fake GREEN."
        )
        return 2

    # --- the gate: every found=True patch has a backing git_anchor_created -----
    tgt_anchors, tgt_nev = created_anchor_patch_ids(TARGET)
    missing = tgt_found - tgt_anchors

    obs = (
        f"target={TARGET[:8]} patches={tgt_npatch} found_true={len(tgt_found)} "
        f"created_events={len(tgt_anchors)} events_for_trace={tgt_nev} "
        f"missing={len(missing)} | posctrl={POSITIVE[:8]} found={len(pc_found)} "
        f"backed={len(pc_anchors)} ok={reader_ok}"
    )

    if missing:
        print(
            "PROBE_RED B1-creation-bijection: "
            f"{len(missing)} patch(es) anchor.found=True with NO backing "
            "git_anchor_created event in the canonical log (creation gap / "
            "over-attribution)."
        )
        for m in sorted(missing):
            print("   MISSING_CREATION_EVENT trace_patch_id:", m)
        print("  observed:", obs)
        return 1

    print("PROBE_GREEN B1-creation-bijection:", obs)
    return 0


def test_probe_b1_creation_bijection():
    rc = _run()
    assert rc != 2, (
        "B1 INCONCLUSIVE: positive control or target fixture could not be "
        "confirmed (reader returned empty / fixture missing); not a clean RED."
    )
    assert rc == 0, (
        "B1 RED: one or more patches stamped anchor.found=True in the bucket "
        "trace.json have NO backing git_anchor_created event in the canonical "
        "Git event log (creation gap / over-attribution; the trail/track "
        "bijection is broken)."
    )


if __name__ == "__main__":
    sys.exit(_run())
