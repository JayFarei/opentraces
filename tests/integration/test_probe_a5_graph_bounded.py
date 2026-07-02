#!/usr/bin/env python3
"""PROBE A5 — graph page-bounded interactivity (epic #169, restore trail-track).

Committed, grader-owned, location-relative merge of the loopcraft forge draft
`a5_check.sh` + `a5_assert.py` into ONE self-contained python file.

Re-observes the WORLD, never the agent's narration: the real CLI exit code (a
124 hang = RED), the real CLI JSON, and a real `git cat-file -t` per node.

`opentraces trail graph --limit 3` must be INTERACTIVE and page-bounded: it
emits exactly 3 nodes that are each a REAL in-repo commit, and it returns inside
the spec's hard 3s SLA. GREEN means page-bounded attribution restored (work
bounded by the page, not run over the whole canonical event log).

RED on main : the per-page graph build deserializes the whole on-disk event-log
              snapshot regardless of `--limit`, so a cold/representative run
              blows the 3s budget -> rc=124 (the timeout maps to RED).
GREEN on fix: `--limit 3` is index/page-bounded and returns fast with exactly 3
              real commit nodes.

Budgets and oracle pins are kept EXACTLY as the forge draft: an 8s warm-up whose
result and timing are ignored (it only removes cold-vs-warm-index nondeterminism
so the measured run reads steady-state interactivity), then the measured run
under a HARD 3s SLA. The commit oracle is a real `git cat-file -t`, exactly as
the forge — no node may be fabricated or a placeholder.

Location-relative: the CLI under test is the `.venv/bin/opentraces` that lives
next to this file, so a grader worktree exercises that worktree's `src/`.

Exit 0 GREEN / 1 RED / 2 SETUP-INVALID.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

# The CLI under test is the editable install that lives next to this probe, so a
# grader worktree drives its own src/ (location-relative, per the universal
# probe contract). Plain python, when needed, would be sys.executable.
OT = REPO / ".venv" / "bin" / "opentraces"

# Budgets pinned EXACTLY to the forge draft. Do not relax: RED on main is the
# over-budget timeout (rc=124) under these very numbers.
WARMUP_TIMEOUT = 8  # seconds; result + timing IGNORED (page-cache / index warm)
SLA_TIMEOUT = 3     # seconds; HARD interactivity budget for the measured run


def _assert_json_and_git(raw: str) -> int:
    """Re-observe the captured stdout JSON + git. Port of a5_assert.py verbatim.

    Asserts (none weakened): valid JSON with a `commits` list; exactly 3 nodes
    (non-empty AND page-bounded -> kills empty/stale-passes and
    limit-bounds-output-not-work); every node sha is a 40-hex oid that
    `git cat-file -t` resolves to a real in-repo `commit` (kills
    fabricated/placeholder nodes). Returns 0 GREEN / 1 RED.
    """
    try:
        data = json.loads(raw)
    except Exception:
        print("RED: stdout was not valid JSON: %r" % (raw[:160],))
        return 1

    commits = data.get("commits")
    if not isinstance(commits, list):
        print("RED: JSON has no `commits` list")
        return 1

    n = len(commits)
    if n == 0:
        print("RED: zero commits (empty/stale output cannot pass)")
        return 1
    if n != 3:
        print("RED: --limit 3 returned %d commits (limit not page-bounding output)" % n)
        return 1

    shorts = []
    for c in commits:
        sha = c.get("sha") if isinstance(c, dict) else None
        if not (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha)):
            print("RED: node sha is not a 40-hex git oid: %r" % (sha,))
            return 1
        # Oracle: re-observe the world. The sha MUST be a real in-repo commit.
        # cwd=REPO resolves against the shared object store from a linked worktree.
        t = subprocess.run(
            ["git", "cat-file", "-t", sha],
            cwd=str(REPO), capture_output=True, text=True,
        )
        if t.returncode != 0 or t.stdout.strip() != "commit":
            print("RED: node %s is not a real in-repo commit "
                  "(cat-file=%r rc=%d) -> fabricated/placeholder node"
                  % (sha, t.stdout.strip(), t.returncode))
            return 1
        shorts.append((c.get("short_sha") if isinstance(c, dict) else None) or sha[:8])

    print("GREEN: graph --limit 3 interactive; exactly 3 real in-repo commits [%s]"
          % ", ".join(shorts))
    return 0


def _run() -> int:
    # SETUP VALIDITY: the location-relative CLI must exist and be runnable, and
    # the repo it drives must be a real git repo.
    if not OT.is_file():
        print(f"[SETUP-INVALID] opentraces CLI missing at {OT}")
        return 2
    if subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=str(REPO), capture_output=True, text=True,
    ).returncode != 0:
        print(f"[SETUP-INVALID] {REPO} is not a git repo")
        return 2

    # false-RED guard: warm OS page cache + scoped-read accelerator once. Result
    # and timing are IGNORED; this removes cold-vs-warm-index nondeterminism so
    # the SLA below measures steady-state interactivity. On main this warm hangs
    # and is killed (still RED on the measured run); on the fix it returns fast.
    try:
        subprocess.run(
            [str(OT), "trail", "graph", "--limit", "3", "--json"],
            cwd=str(REPO),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=WARMUP_TIMEOUT,
        )
    except Exception:
        # `|| true`: any warm-up outcome (timeout, error, non-zero) is ignored.
        pass

    # Measured interactivity run: hard 3s SLA (the spec's budget). Ordering
    # pinned: --page 1 --limit 3 is deterministic on a >=3-commit repo (no
    # sampling). A TimeoutExpired here is the rc=124 hang -> RED.
    try:
        proc = subprocess.run(
            [str(OT), "trail", "graph", "--page", "1", "--limit", "3", "--json"],
            cwd=str(REPO), capture_output=True, text=True, timeout=SLA_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print("RED: graph --limit 3 did not return within the 3s interactivity "
              "SLA (timeout, rc=124)")
        return 1

    rc = proc.returncode
    if rc != 0:
        print("RED: graph --limit 3 exited rc=%d within budget "
              "(not a clean interactive return)" % rc)
        tail = (proc.stderr or "")[-400:]
        if tail.strip():
            print("  stderr:", tail)
        return 1

    return _assert_json_and_git(proc.stdout)


def test_probe_a5_graph_bounded():
    rc = _run()
    if rc == 2:
        pytest.skip(
            "A5 SETUP-INVALID (location-relative opentraces CLI or git "
            "repo not present in the worktree)"
        )
    assert rc == 0, ("A5 RED: trail graph --limit 3 was not page-bounded/interactive "
                     "within the 3s SLA (timeout=rc124, or wrong/empty/fabricated nodes)")


if __name__ == "__main__":
    sys.exit(_run())
