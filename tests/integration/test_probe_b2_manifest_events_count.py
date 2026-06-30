#!/usr/bin/env python3
"""PROBE B2 — manifest anchored_count never over-counts (epic #169, blast: high, rung: T3).

Committed, grader-owned, location-relative version of the loopcraft forge draft
`check_b2_manifest_events_count.py`.

Done-claim gated:
    For every in-repo, nonzero-anchored manifest row, the published
    summary.anchored_count NEVER EXCEEDS the number of distinct trace_patch_ids
    that carry a real `git_anchor_created` event in the CANONICAL append-only
    event log (refs/opentraces/local/events/v1).

Defeater killed:
    anchored_count over-counts by counting correlator anchor.found=True STAMPS on
    patch records that have NO git_anchor_created event (the #139 creation-gap /
    over-attribution state). Manifest LEFT is sourced from the published row
    summary.anchored_count; RIGHT is re-derived from the canonical event log via
    the SAME API the forge uses (read_events_for_trace), reading the oracle from
    this worktree's REPO/.git (its common dir is the main repo, where the
    canonical ref lives).

Why the gate is "<=" not "==" (staleness-proof, kills false-RED):
    The event log is append-only, so a CORRECTLY-built manifest's anchor count
    (count of git_anchor_created at build time) can only be <= the log's CURRENT
    count. Therefore LEFT > RIGHT is impossible for a correct manifest at ANY
    later read time and is UNAMBIGUOUSLY the over-count bug, never staleness.
    Under-count (LEFT < RIGHT) CAN be benign manifest lag, so it is reported but
    NOT a hard failure. Over-count is the RED.

False-RED guards:
    * RIGHT read from the canonical Git ref via read_events_for_trace, which uses
      the fresh head==ref event_index (warm accelerator; rebuilds if stale) — so
      RIGHT is always against the CURRENT canonical head, never the lagging bucket
      companion (trail.jsonl.gz) that the ticket flags.
    * counts are SET cardinalities of distinct patch_ids -> order/timestamp/
      warm-vs-cold independent.
    * cross-repo rows (events_seen==0 here) are SKIPPED, never failed, so a trace
      that genuinely lives in another clone cannot cause a spurious RED.

Assertion-tamper / vacuous-pass guards (a guard trip is a hard FAIL, never a pass):
    * manifest must have rows and must contain the witness trace.
    * the witness trace MUST be re-observed with events_seen>0 (proves the read
      path worked) or the run hard-fails — a fix that only deletes/empties the
      witness cannot fake a green.
    * at least one nonzero-anchored row must be re-observed.
    * both sides computed by this script from raw world-state (published manifest
      int vs structurally-parsed git_anchor_created events); no agent self-report
      or "valid:true" flag is trusted.

RED on main : 19/21 re-observed nonzero-anchored rows OVER-COUNT (incl. the
              witness: anchored_count=3 over 0 canonical git_anchor_created).
GREEN on fix: every re-observed nonzero-anchored row satisfies anchored_count <=
              canonical git_anchor_created patch count.

Hardening over the forge draft (no assertion weakened):
    * LOCATION-RELATIVE: REPO = parents[2]; src/ inserted on sys.path before
      importing opentraces, so a grader worktree exercises its OWN src and reads
      the canonical oracle from its OWN .git common dir.
    * BUCKET_ROOT honors OT_BUCKET_ROOT; MANIFEST = BUCKET_ROOT/manifest.json, so
      it also runs against a re-derived COPY of the bucket.
    * the witness project_slug is DERIVED from the witness manifest row, not
      hardcoded, so the in-repo target filter is correct against a re-derived copy
      whose slug differs.
    * runnable as pytest AND as a standalone script.

exit 0 GREEN / 1 RED / 2 SETUP-INVALID-or-vacuous-pass-guard-trip.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

BUCKET_ROOT = Path(os.environ.get("OT_BUCKET_ROOT", str(Path.home() / ".opentraces" / "bucket")))
MANIFEST = BUCKET_ROOT / "manifest.json"
WITNESS = "4ee84d36-0007-43bb-b85a-028070f8b731"


class _Guard(Exception):
    """A vacuous-pass / setup-validity guard tripped; never a silent pass."""


def _guard(msg: str) -> None:
    raise _Guard(msg)


def _anchor_patch_count(trace_id: str) -> tuple[int, int]:
    """(events_seen, distinct git_anchor_created patch_ids) from the canonical log.

    Reads the oracle the same way the forge does: read_events_for_trace over this
    REPO's canonical event ref, counting distinct trace_patch_ids structurally
    parsed from real git_anchor_created events.
    """
    from opentraces.core.trails.event_log import read_events_for_trace
    from opentraces.core.trails.ids import id_from_payload

    evs = read_events_for_trace(REPO, trace_id)
    pids = set()
    for e in evs:
        if e.event_type == "git_anchor_created":
            pid = id_from_payload(e.payload, "trace_patch")
            if pid:
                pids.add(pid)
    return len(evs), len(pids)


def _sweep() -> int:
    from opentraces.core.trails.event_log import _ref_head

    head = _ref_head(REPO)
    if head is None:
        _guard("canonical event ref refs/opentraces/local/events/v1 missing in repo")

    if not MANIFEST.exists():
        _guard(f"manifest absent: {MANIFEST}")
    m = json.loads(MANIFEST.read_text())
    rows = {r["trace_id"]: r for r in (m.get("traces") or [])}
    if not rows:
        _guard("manifest has no trace rows (empty/stale read-model)")
    if WITNESS not in rows:
        _guard(f"witness {WITNESS} absent from manifest")

    # Derive the in-repo slug from the witness row (do NOT hardcode it), so the
    # target filter is correct against a re-derived COPY of the bucket.
    witness_slug = rows[WITNESS].get("project_slug")
    if not witness_slug:
        _guard(f"witness {WITNESS} row has no project_slug")

    targets = sorted(
        tid
        for tid, r in rows.items()
        if r.get("project_slug") == witness_slug
        and (r.get("summary") or {}).get("anchored_count", 0) > 0
    )
    if WITNESS not in targets:
        _guard("witness is not among nonzero-anchored in-repo rows")

    overcount = []   # LEFT > RIGHT  -> hard RED (the defeater)
    undercount = []  # LEFT < RIGHT  -> reported only (possible benign lag)
    checked = 0
    witness_checked = False

    for tid in targets:
        left = (rows[tid].get("summary") or {}).get("anchored_count", 0)
        seen, right = _anchor_patch_count(tid)
        if seen == 0:
            print(f"skip {tid}: not re-observable in this repo's canonical log")
            continue
        checked += 1
        if tid == WITNESS:
            witness_checked = True
        marker = "==" if left == right else (">" if left > right else "<")
        print(
            f"  {tid}: manifest.anchored_count={left} {marker} "
            f"event_log.git_anchor_created_patches={right}  (events_seen={seen})"
        )
        if left > right:
            overcount.append((tid, left, right))
        elif left < right:
            undercount.append((tid, left, right))

    if not witness_checked:
        _guard(
            "witness trace not re-observable (events_seen==0); result would be "
            "vacuous — read path failed"
        )
    if checked == 0:
        _guard("no nonzero-anchored rows re-observable (vacuous-pass guard)")

    if undercount:
        print(
            f"INFO: {len(undercount)} under-count rows (possible benign manifest "
            "lag; not gated)"
        )

    if overcount:
        print(
            f"\nRED: {len(overcount)}/{checked} re-observed rows OVER-COUNT "
            "anchored_count vs canonical git_anchor_created events:"
        )
        for tid, left, right in overcount:
            print(
                f"  RED {tid}: anchored_count={left} EXCEEDS "
                f"git_anchor_created_patches={right}  "
                f"(over-attribution / creation-gap; {left - right} phantom anchors)"
            )
        return 1

    print(
        f"\nGREEN: all {checked} re-observed nonzero-anchored rows satisfy "
        "anchored_count <= canonical git_anchor_created patch count "
        "(no over-count)."
    )
    return 0


def _run() -> int:
    try:
        return _sweep()
    except _Guard as e:
        print(f"FAIL(guard): {e}")
        return 2


def test_probe_b2_manifest_events_count():
    rc = _run()
    assert rc != 2, (
        "B2 SETUP-INVALID / vacuous-pass guard trip "
        "(manifest, witness, or canonical read-path invalid)"
    )
    assert rc == 0, (
        "B2 RED: manifest summary.anchored_count over-counts vs canonical "
        "git_anchor_created events (#139 creation-gap / over-attribution)"
    )


if __name__ == "__main__":
    sys.exit(_run())
