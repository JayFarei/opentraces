#!/usr/bin/env python3
"""PROBE A4 — blame on a 0-anchor commit is FAST + well-formed + correctly-empty
(epic #169, design-gate latency/correctness).

Committed, grader-owned, location-relative port of the loopcraft forge draft
``A4_blame_empty_fast.sh``. Drives the SHIPPED CLI
``opentraces trail blame commit <sha> --project <repo> --json`` on a real,
0-anchor commit and asserts it returns a well-formed, correctly-empty blame
envelope FAST — no hang, no glob-fallthrough whole-log survival walk.

Done-claim under test: ``trail blame commit <0-anchor-sha> --json`` finishes in
seconds (NOT the ~6h glob-fallthrough whole-log survival walk) AND emits the
stable commit-mode envelope (``commit`` / ``coverage`` / ``traces`` / ``files``)
whose ``trailEvidence`` is exactly empty, matching the canonical anchor oracle of
ZERO ``git_anchor_created`` rows for this commit.

Two latency gates, ported verbatim from the forge draft (never relaxed):
  * Phase 1 — HANG gate (30s). RED on main: the glob-fallthrough whole-log
    survival walk does not finish in 30s -> timeout (rc=124-equivalent) -> RED.
  * Phase 2 — warm FAST gate (3s). The accelerator is on-disk/persistent so the
    second run is warm; the tight 3s budget is the FAST half of the claim.
Both phases must exit rc=0; a timeout in either is the hang symptom (unfakeable).

Phase 3 — grounded envelope assertions (every forge assertion preserved, plus
hardening):
  * stdout parses as a JSON object;
  * stable commit-mode keys ``commit`` / ``coverage`` / ``traces`` / ``files``
    are present;
  * ``commit.sha`` echoes the requested sha (forge: ``startswith(sha[:12])``;
    hardening: also ``== git rev-parse <sha>`` — an INDEPENDENT cross-check that
    the resolved commit is the one we asked for, not a glob fallthrough onto some
    other commit);
  * ``coverage`` carries ``attributed`` / ``total`` / ``ratio``;
  * ``traces`` is a list;
  * ``trailEvidence`` is a list whose length is exactly 0 AND equals the
    canonical anchor oracle (a 0-anchor commit yields zero anchor-survival rows).

CANONICAL ORACLE (read from REPO ``.git`` via the SAME APIs the forge uses):
``build_trail_query_projection_for_commits(REPO, {sha}).anchors_for_commit(sha)``
(the attribution-only fix path, no survival recompute) MUST be 0 rows, AND an
independent ``read_events_scoped(event_types={"git_anchor_created"})`` pass MUST
find ZERO events referencing this sha. The probe asserts the CLI's empty
``trailEvidence`` equals this independently-computed oracle, so an empty answer
can only be GREEN when the canonical truth is also empty.

LOCATION-RELATIVE: imports the code-under-test and drives ``REPO/.venv`` from the
SAME repo this test file lives in (``parents[2]``), so a grader worktree
exercises that worktree's ``src/`` and its ``.venv/bin/opentraces``.

exit 0 GREEN / 1 RED / 2 SETUP-INVALID (this REPO carries no attribution for the
pinned commit, or the commit is not 0-anchor here, so the blame path cannot be
exercised) / 3 HARNESS-INVALID (CLI binary absent). A non-GREEN verdict makes the
pytest FAIL; it never silently passes.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

# --- CLI under test + the exact id/oracle pins from the forge draft (do NOT relax).
OT = REPO / ".venv" / "bin" / "opentraces"
SHA = "d8d2b5d6efb8fa8f784eb73215a7b200ddba776d"  # real 0-anchor docs commit
# Latency gates, byte-identical to the forge draft. << ~6h glob-fallthrough worst
# case; a timeout here is the hang (rc=124-equivalent), unfakeable.
HANG_GUARD = 30  # Phase 1 hang gate
FAST_GUARD = 3   # Phase 2 warm fast gate

_HEX8 = re.compile(r"^[0-9a-f]{8}$")


def _project_dir() -> Path | None:
    """The opted-in opentraces project that owns this checkout's shared event
    log: the parent of the COMMON git dir (works from the main checkout or a
    linked worktree, regardless of the worktree's basename). Returns None when
    that project is not opted in. Location-relative; aligns A4 with A3/A7/A8 so
    blame runs against the provisioned project that carries the commit's
    attribution — never the un-provisioned grader worktree itself."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(REPO), capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return None
    if not out:
        return None
    proj = Path(out).resolve().parent
    if (proj / ".opentraces.json").is_file() or (proj / ".opentraces").is_dir():
        return proj
    return None


def _git_full_sha() -> str | None:
    """Independent cross-check anchor: resolve the pinned sha through Git in
    THIS repo. Returns the full 40-char sha, or None if the commit is absent."""
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{SHA}^{{commit}}"],
        cwd=str(REPO), capture_output=True, text=True,
    )
    out = (proc.stdout or "").strip()
    return out if proc.returncode == 0 and out else None


def _canonical_anchor_oracle() -> int | None:
    """Canonical ``git_anchor_created`` oracle for the pinned commit, read from
    REPO ``.git`` via the SAME APIs the forge uses. Returns the anchor-row count
    for this commit (the attribution-only fix path, NO survival recompute), or
    None on a read error. Cross-checked against an independent whole-log
    ``read_events_scoped`` pass; a disagreement returns None (untrustworthy
    oracle -> SETUP-INVALID upstream)."""
    try:
        from opentraces.core.trails import (
            build_trail_query_projection_for_commits,
            read_events_scoped,
        )

        proj = build_trail_query_projection_for_commits(REPO, {SHA})
        if proj is None:
            return None
        rows = proj.anchors_for_commit(SHA)  # attribution-only, no survival
        scoped_n = rows if isinstance(rows, list) else list(rows)
        primary = len(scoped_n)

        # Independent cross-check: count git_anchor_created events that name this
        # sha anywhere in their payload. Must agree with the projection.
        evs = read_events_scoped(REPO, event_types={"git_anchor_created"})
        cross = 0
        for e in evs:
            if getattr(e, "event_type", None) != "git_anchor_created":
                continue
            blob = str(getattr(e, "payload", None) or {})
            if SHA in blob or SHA[:12] in blob:
                cross += 1
        if primary != cross:
            return None
        return primary
    except Exception:  # noqa: BLE001
        return None


def _ensure_resolvable() -> bool:
    """Make ``--project REPO`` resolve to the project state that carries this
    commit's attribution, so the blame trail-evidence path is actually exercised
    (the empty per-slug attribution cache otherwise bails rc=1 BEFORE the buggy
    walk). Idempotent; touches only REPO's gitignored ``.opentraces.json`` marker
    and never anything tracked.

    Resolution order:
      1. Already opted-in AND resolving to a slug that carries the commit's
         attribution -> done.
      2. Otherwise, discover an existing ``~/.opentraces/projects/<base>-<pid8>``
         dir (matching THIS worktree's basename) that holds
         ``attributions/<sha>.json``, and point REPO's marker at that identity.
      3. Re-check; True iff the resolved project carries the attribution.
    """
    try:
        from opentraces.core.cache import AttributionCache
        from opentraces.core.config import (
            PROJECTS_DIR,
            _make_slug,
            _write_marker,
            project_is_opted_in,
            set_first_run_backfill_decision,
        )
    except Exception:  # noqa: BLE001
        return False

    def _resolves() -> bool:
        if not project_is_opted_in(REPO):
            return False
        try:
            return AttributionCache(REPO).has_attribution(SHA)
        except Exception:  # noqa: BLE001
            return False

    if _resolves():
        return True

    # Discover a populated project dir for this worktree's basename.
    base_slug = _make_slug(REPO.name, "0" * 8).rsplit("-", 1)[0]
    try:
        candidates = sorted(p for p in PROJECTS_DIR.iterdir() if p.is_dir())
    except Exception:  # noqa: BLE001
        candidates = []
    for d in candidates:
        if not d.name.startswith(base_slug + "-"):
            continue
        pid8 = d.name.rsplit("-", 1)[1]
        if d.name != f"{base_slug}-{pid8}" or not _HEX8.match(pid8):
            continue
        if not (d / "attributions" / f"{SHA}.json").is_file():
            continue
        # project_id[:8] keys the slug -> any 32-hex id starting with pid8 maps
        # _make_slug(REPO.name, id) back onto this exact populated dir.
        project_id = pid8 + "0" * 24
        try:
            _write_marker(
                REPO,
                project_id,
                {
                    "review_policy": "auto",
                    "push_policy": "manual",
                    "agents": [],
                    "default_visibility": "private",
                },
            )
            # Belt-and-suspenders: never let a first-run backfill prompt block.
            set_first_run_backfill_decision(REPO, "never")
        except Exception:  # noqa: BLE001
            return False
        break

    return _resolves()


def _evaluate(stdout: str, oracle: int, full_sha: str) -> list[str]:
    """Hard, tamper-resistant predicate over the re-observed world. Every forge
    assertion preserved verbatim, plus the rev-parse and oracle-equality
    hardening."""
    fail: list[str] = []
    raw = stdout or ""
    try:
        p = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        return [f"malformed: stdout is not valid JSON ({e}); first120B={raw[:120]!r}"]
    if not isinstance(p, dict):
        return [f"malformed: top-level JSON is {type(p).__name__}, expected object"]

    # stable documented commit-mode keys (forge: commit/coverage/traces/files).
    for k in ("commit", "coverage", "traces", "files"):
        if k not in p:
            fail.append(f"malformed: missing stable commit-mode key {k!r}; keys={sorted(p)}")

    # commit.sha echoes the requested sha (forge prefix + rev-parse cross-check).
    c = p.get("commit")
    got = str(c.get("sha", "")) if isinstance(c, dict) else ""
    if not isinstance(c, dict) or not got.startswith(SHA[:12]):
        fail.append(f"wrong-commit: commit.sha={got!r} does not echo requested {SHA[:12]}")
    elif got != full_sha:
        fail.append(
            f"wrong-commit: commit.sha={got!r} != git rev-parse {full_sha!r} "
            "(resolved a different commit / glob fallthrough)"
        )

    # coverage block shape.
    cov = p.get("coverage")
    if not isinstance(cov, dict) or not all(x in cov for x in ("attributed", "total", "ratio")):
        fail.append(f"malformed: coverage block shape wrong: {cov!r}")

    # traces is a list.
    if not isinstance(p.get("traces"), list):
        fail.append(f"malformed: traces is {type(p.get('traces')).__name__}, expected list")

    # trailEvidence is a list, exactly empty, AND equal to the canonical oracle.
    te = p.get("trailEvidence", [])  # additive; absent treated as empty (rename-robust).
    if not isinstance(te, list):
        fail.append(f"malformed: trailEvidence is {type(te).__name__}, expected list")
    elif len(te) != 0:
        fail.append(f"wrong-data: 0-anchor commit returned {len(te)} anchor evidence rows (expected 0)")
    elif len(te) != oracle:
        fail.append(
            f"oracle-mismatch: trailEvidence has {len(te)} rows but the canonical "
            f"git_anchor_created oracle is {oracle}"
        )
    return fail


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGKILL the child's whole process group, then reap it. The survival walk
    spawns git grandchildren; killing only the parent would leak them AND (under
    pipe capture) block on the held fd, so the run must redirect to a file and
    tear down the group — exactly what the forge's shell ``timeout`` + file
    redirect achieved cleanly (rc=124)."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass


def _blame(timeout: int, project: Path) -> tuple[bool, int, str]:
    """Run the shipped blame command under ``timeout`` seconds. Redirects stdout
    to a temp FILE (never a pipe) and launches in its own session/process group,
    so a hang is bounded the way the forge's ``timeout ... >file`` was: on
    timeout the whole group is killed and the call returns promptly.
    Returns (timed_out, rc, stdout)."""
    fd, out_path = tempfile.mkstemp(prefix="a4-blame-", suffix=".json")
    os.close(fd)
    try:
        with open(out_path, "wb") as out_f:
            proc = subprocess.Popen(
                [str(OT), "trail", "blame", "commit", SHA, "--project", str(project), "--json"],
                cwd=str(project),
                stdout=out_f,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                return True, -1, ""
        try:
            stdout = Path(out_path).read_text(errors="replace")
        except OSError:
            stdout = ""
        return False, rc, stdout
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def _run() -> int:
    # HARNESS VALIDITY: the CLI under test must exist in this (grader) worktree.
    if not OT.is_file():
        print(f"[HARNESS-INVALID] opentraces CLI not found at {OT}")
        return 3

    # SETUP VALIDITY: the pinned commit must exist in this repo.
    full_sha = _git_full_sha()
    if full_sha is None:
        print(f"[SETUP-INVALID] pinned commit {SHA[:12]} not found in {REPO}")
        return 2
    if full_sha != SHA:
        print(f"[SETUP-INVALID] git rev-parse {SHA[:12]} -> {full_sha} (sha pin drift)")
        return 2

    # CANONICAL ORACLE: this must be a genuinely 0-anchor commit, else the
    # premise (empty trailEvidence) is not a real test of the fast/empty path.
    oracle = _canonical_anchor_oracle()
    if oracle is None:
        print("[SETUP-INVALID] could not read a trustworthy git_anchor_created oracle from REPO .git")
        return 2
    if oracle != 0:
        print(f"[SETUP-INVALID] pinned commit is NOT 0-anchor here: oracle={oracle} git_anchor_created rows")
        return 2

    # SETUP VALIDITY: resolve the opted-in opentraces project that owns this
    # checkout's shared event log (the git-common-dir parent), and run blame
    # --project against it — exactly as A3/A7/A8 do. A linked grader worktree is
    # itself un-provisioned; the shared canonical event log + bucket live with
    # the owning project, so the address/empty answer is identical there.
    project = _project_dir()
    if project is None:
        print(
            "[SETUP-INVALID] no opted-in opentraces project owns this checkout's "
            "git-common-dir; blame cannot exercise the trail-evidence path here."
        )
        return 2

    # PHASE 1 — HANG gate (30s). A timeout is the glob-fallthrough whole-log
    # survival-walk hang (RED on main); any non-zero rc is also RED.
    t1_timed_out, rc1, _ = _blame(HANG_GUARD, project)
    if t1_timed_out:
        print(
            f"RED: hang-on-empty -- blame commit on 0-anchor {SHA[:12]} did not finish "
            f"in {HANG_GUARD}s (glob-fallthrough whole-log survival walk)"
        )
        return 1
    if rc1 != 0:
        print(f"RED: blame commit exited rc={rc1} (expected 0) on 0-anchor commit")
        return 1

    # PHASE 2 — warm measured FAST run under the tight probe budget.
    t2_timed_out, rc2, out2 = _blame(FAST_GUARD, project)
    if t2_timed_out:
        print(
            f"RED: not-fast -- warm blame commit on 0-anchor {SHA[:12]} exceeded "
            f"{FAST_GUARD}s (glob fallthrough not eliminated)"
        )
        return 1
    if rc2 != 0:
        print(f"RED: warm blame commit exited rc={rc2}")
        return 1

    # PHASE 3 — grounded envelope assertions (well-formed + correctly-empty).
    fail = _evaluate(out2, oracle, full_sha)
    if fail:
        print("VERIFIER_RESULT=RED")
        for f in fail:
            print("  -", f)
        return 1

    print(
        f"GREEN: blame commit on 0-anchor {SHA[:12]} fast (<= {FAST_GUARD}s warm) "
        f"+ well-formed + correctly-empty (trailEvidence == oracle == {oracle} rows)"
    )
    return 0


def test_probe_a4_blame_empty_fast():
    rc = _run()
    assert rc != 3, "A4 HARNESS-INVALID (opentraces CLI binary absent in this worktree)"
    assert rc != 2, (
        "A4 SETUP-INVALID (commit missing / not 0-anchor / REPO carries no "
        "attribution for the pinned commit)"
    )
    assert rc == 0, (
        "A4 RED: blame on a 0-anchor commit hangs (glob-fallthrough whole-log "
        "survival walk) and/or is not fast / not well-formed / not correctly-empty"
    )


if __name__ == "__main__":
    sys.exit(_run())
