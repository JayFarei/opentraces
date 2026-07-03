#!/usr/bin/env python3
"""PROBE B3 — per-patch attribution correctness (epic #169).

Committed, grader-owned, location-relative version of the loopcraft forge draft
`b3_per_patch_attribution.py`. Done-claim under test: a patch is anchored to a
commit ONLY if its own file is genuinely changed by that commit AND that anchor
is backed by a canonical ``git_anchor_created`` event.

Witness (default ``4ee84d36``): the shipped bucket trace.json stamps 3 patches
``anchor.found=True commit=e774c788`` whose file
(``tests/otbox/simulated_users/_prompt_install_harness.py``) commit e774c788
NEVER touched -> over-attribution. On main the canonical event log ALSO carries
ZERO ``git_anchor_created`` events for those patches, so both grounding legs are
independently RED. Must go GREEN only when the fix de-attributes the bogus
anchors OR re-anchors them to a genuinely-touching commit backed by a real
``git_anchor_created`` event.

Grounding (T2/T3): re-observes the WORLD on both sides of the join, never agent
narration:
  - LEG 1 (git membership): the SHIPPED bucket trace.json on disk vs. real git
    ``git diff-tree --no-commit-id --name-only -r <commit>``. Full-relative-path
    membership (never basename-only) with a witness-file integrity guard.
  - LEG 2 (agreement): every anchored patch's (patch_id, commit_sha) must equal
    a canonical ``git_anchor_created`` anchor read from REPO's .git via
    ``read_events_for_trace`` — so re-pointing to a different genuinely-touching
    commit with NO backing event still fails.

Location-relative: REPO is the worktree this test lives in, git runs against it
(its shared object store has the witness commit), and the canonical oracle is
read from its .git. A grader worktree therefore exercises ITS own src + log.

Exit codes (standalone) / pytest verdict:
  0 = GREEN  (every anchored patch is in its anchor commit AND backed by a
              canonical event, or there are no anchored patches)
  1 = RED    (>=1 anchored patch whose file the commit never touched, OR whose
              (patch, commit) has no backing git_anchor_created event)
  2 = BLOCK  (precondition missing: trace gone/empty/corrupt, witness absent,
              commit unresolvable/merge-ambiguous, or oracle unavailable)
A non-GREEN verdict (RED or BLOCK) FAILS the pytest; it is NEVER read as a pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

# Bucket root: env-overridable so the probe works against a re-derived COPY of
# the bucket. The witness trace is located generically by globbing the
# project-slug dirs (the slug is NEVER hardcoded).
BUCKET_ROOT = Path(
    os.environ.get("OT_BUCKET_ROOT", str(Path.home() / ".opentraces" / "bucket"))
)
# Witness trace id (prefix); default per the forge draft.
WITNESS_TRACE = os.environ.get("OT_B3_WITNESS", "4ee84d36")
# Witness file that must be present for the trace to be considered intact.
WITNESS_FILE = "tests/otbox/simulated_users/_prompt_install_harness.py"

GIT = ["git", "-C", str(REPO)]


class _Block(Exception):
    """Precondition failure -> BLOCK(2), never a pass."""


def _resolve_trace() -> Path:
    """Resolve the witness trace.json path, slug-agnostic.

    OT_B3_TRACE wins if set; otherwise glob the project-slug dirs under the
    bucket root for the witness trace id. Ambiguity or absence -> BLOCK.
    """
    override = os.environ.get("OT_B3_TRACE")
    if override:
        return Path(override)
    matches = sorted(
        BUCKET_ROOT.glob(f"traces/v1/*/{WITNESS_TRACE}*/trace.json")
    )
    if not matches:
        raise _Block(
            f"witness trace {WITNESS_TRACE}* not found under {BUCKET_ROOT}/traces/v1"
        )
    if len(matches) > 1:
        raise _Block(
            f"witness {WITNESS_TRACE}* is ambiguous ({len(matches)} matches): "
            + ", ".join(str(m) for m in matches)
        )
    return matches[0]


def _git_changed_files(sha: str) -> set[str] | None:
    """Real changed-file set for a commit, or None if unresolvable/ambiguous.

    Merge commits give an empty default diff-tree (combined diff) -> ambiguous;
    a root commit with no single-parent diff is likewise ambiguous. Either ->
    None so the caller BLOCKs rather than silently passing.
    """
    rp = subprocess.run(
        GIT + ["rev-parse", "--verify", "-q", f"{sha}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    if rp.returncode != 0:
        return None  # commit not in repo -> caller blocks
    parents = subprocess.run(
        GIT + ["rev-list", "--parents", "-n", "1", sha],
        capture_output=True,
        text=True,
    ).stdout.split()
    n_parents = max(0, len(parents) - 1)
    out = subprocess.run(
        GIT + ["diff-tree", "--no-commit-id", "--name-only", "-r", sha],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return None
    files = [line for line in out.stdout.splitlines() if line.strip()]
    if not files and n_parents != 1:
        return None  # merge / root with no single-parent diff -> ambiguous
    return set(files)


def _file_in_commit(fp: str, commit_files: set[str]) -> bool:
    """Full-relative-path membership with abs/foreign-prefix tolerance.

    NEVER a basename-only match (that would mask divergence -> defeat the probe).
    """
    fp = fp.lstrip("/")
    if fp in commit_files:
        return True
    for cf in commit_files:
        if fp.endswith("/" + cf) or cf.endswith("/" + fp):
            return True
    return False


def _commit_matches(claimed: str, canonical: str) -> bool:
    """Tolerant sha equality: exact, or one a prefix of the other (>=7 hex)."""
    if claimed == canonical:
        return True
    return (
        len(claimed) >= 7
        and len(canonical) >= 7
        and (claimed.startswith(canonical) or canonical.startswith(claimed))
    )


def _canonical_anchor_pairs(trace_id: str) -> tuple[set[tuple[str, str]], int]:
    """(trace_patch_id, commit_hex) pairs from canonical git_anchor_created
    events for ``trace_id``, read from REPO's .git via the shipped APIs.

    Returns the pair set plus the total event count for the trace (so the caller
    can distinguish "oracle present, no created events" (genuine RED) from
    "oracle absent for this trace" (BLOCK)).
    """
    from opentraces.core.trails.event_log import read_events_for_trace
    from opentraces.core.trails.ids import id_from_payload

    events = read_events_for_trace(REPO, trace_id)
    pairs: set[tuple[str, str]] = set()
    for ev in events:
        if ev.event_type != "git_anchor_created":
            continue
        pl = ev.payload or {}
        tp = id_from_payload(pl, "trace_patch")
        hexv = (pl.get("commit_id") or {}).get("hex")
        if tp and hexv:
            pairs.add((tp, hexv))
    return pairs, len(events)


def _pair_backed(pid: str, sha: str, canonical: set[tuple[str, str]]) -> bool:
    for tp, hexv in canonical:
        if tp == pid and _commit_matches(sha, hexv):
            return True
    return False


def _run() -> int:
    try:
        trace_path = _resolve_trace()
        if not trace_path.is_file():
            raise _Block(f"trace.json missing: {trace_path}")
        try:
            d = json.loads(trace_path.read_text())
        except Exception as exc:  # noqa: BLE001
            raise _Block(f"trace.json unreadable: {exc}") from exc

        trace_id = d.get("trace_id")
        patches = d.get("patches") or []

        # Integrity / anti-fake-green guard: an emptied/truncated trace must NOT
        # pass as 0 violations.
        if not patches:
            raise _Block("trace has zero patches (corrupt/empty) -> cannot certify")
        if not any(
            (p.get("file_path") or "").endswith(WITNESS_FILE) for p in patches
        ):
            raise _Block(
                f"witness file {WITNESS_FILE} absent from trace patches "
                "-> trace integrity guard tripped"
            )

        anchored = [
            p
            for p in patches
            if (p.get("anchor") or {}).get("found")
            and (p.get("anchor") or {}).get("commit_sha")
        ]
        print(
            f"trace_id={trace_id}  trace={trace_path}\n"
            f"patches={len(patches)}  anchored={len(anchored)}"
        )

        # LEG 1 — git membership: every anchored patch's file must be genuinely
        # in its claimed commit.
        git_violations: list[tuple] = []
        cache: dict[str, set[str] | None] = {}
        for p in anchored:
            sha = p["anchor"]["commit_sha"]
            fp = p.get("file_path") or ""
            if sha not in cache:
                cache[sha] = _git_changed_files(sha)
            cf = cache[sha]
            if cf is None:
                raise _Block(
                    f"commit {sha[:12]} unresolvable/merge-ambiguous in repo "
                    "-> cannot verify membership"
                )
            if not _file_in_commit(fp, cf):
                git_violations.append((p.get("patch_id", "?"), fp, sha, sorted(cf)))

        # LEG 2 — agreement: every anchored patch's (patch_id, commit_sha) must
        # equal a canonical git_anchor_created anchor. Re-pointing to a genuinely
        # touching commit with no backing event still fails here.
        agreement_violations: list[tuple] = []
        if anchored:
            canonical, n_events = _canonical_anchor_pairs(trace_id)
            if n_events == 0:
                raise _Block(
                    f"canonical event log has no events for trace {trace_id} "
                    "-> agreement oracle unavailable (cannot certify)"
                )
            print(
                f"canonical git_anchor_created pairs={len(canonical)} "
                f"(from {n_events} events for trace)"
            )
            for p in anchored:
                pid = p.get("patch_id", "?")
                sha = p["anchor"]["commit_sha"]
                if not _pair_backed(pid, sha, canonical):
                    agreement_violations.append((pid, p.get("file_path") or "", sha))

        if git_violations or agreement_violations:
            print("RED: per-patch attribution is unsound.")
            if git_violations:
                print(
                    "  [over-attribution] anchored patch(es) whose file the "
                    "commit never touched:"
                )
                for pid, fp, sha, cf in git_violations:
                    print(f"    patch={pid} file={fp}")
                    print(
                        f"      claimed anchor commit={sha[:12]} "
                        f"actually changed: {cf}"
                    )
            if agreement_violations:
                print(
                    "  [no-backing-event] anchored patch(es) with no canonical "
                    "git_anchor_created event for the claimed commit:"
                )
                for pid, fp, sha in agreement_violations:
                    print(f"    patch={pid} file={fp} claimed commit={sha[:12]}")
            print(
                f"GIT_VIOLATIONS={len(git_violations)} "
                f"AGREEMENT_VIOLATIONS={len(agreement_violations)}"
            )
            return 1

        print(
            "GREEN: every anchored patch's file is genuinely present in its "
            "anchor commit AND backed by a canonical git_anchor_created event "
            "(or patches de-attributed). VIOLATIONS=0"
        )
        return 0
    except _Block as blk:
        print(f"BLOCK: {blk}", file=sys.stderr)
        return 2


def test_probe_b3_per_patch_attribution():
    rc = _run()
    if rc == 2:
        pytest.skip(
            "B3 BLOCK: precondition unmet (trace missing/empty/corrupt, witness "
            "absent, commit unresolvable/merge, or oracle unavailable)"
        )
    assert rc == 0, (
        "B3 RED: per-patch attribution unsound -- an anchored patch's file is "
        "not in its claimed commit, or its (patch, commit) has no backing "
        "git_anchor_created event"
    )


if __name__ == "__main__":
    sys.exit(_run())
