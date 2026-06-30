#!/usr/bin/env python3
"""PROBE A8 — blame contract: attribution only, ZERO survival leak (epic #169, design gate).

Committed, grader-owned, location-relative port of the loopcraft forge draft
`a8_blame_contract.sh`. Drives the SHIPPED CLI
`opentraces trail blame commit 884a1a6b… --json` on the real, heavily-anchored
commit (951 git_anchor_created anchors) in the real ~1900-trace bucket and
re-observes the actual stdout JSON of the blame command — no agent narration.

DONE-CLAIM (#169): blame carries attribution and ZERO survival fields; survival
is reachable ONLY via `trail track` (the locked seam). The defeater killed is
survival leaking back into blame output (seam not carved) so a consumer reads a
survival field off blame and trusts it as live truth though blame never
recomputes survival — silent-wrong.

GREEN iff: rc==0 within the 25s budget AND the JSON parses AND blame resolved
THIS commit (`commit.sha` echoes) AND attribution is non-empty
(`trailEvidence` >= 1 or `traces` or `entity_contributions`) AND a recursive
scan finds (a) ZERO dict keys whose name contains ``survival`` anywhere in the
payload [the forge's substring gate, KEPT verbatim] and (b) ZERO dict keys
outside a POSITIVE attribution-only whitelist [the hardening — catches a
``surv_state`` rename a substring scan would miss].

RED on main: the command HANGS past budget (rc=124 / over-budget) because the
per-anchor survival recompute (`anchors_for_commit_with_survival` ->
`sync_patch` -> `_compute_survival`, cli/blame.py:586) is still in the blame
path; and were it to return, every `trailEvidence` row carries
`current_state.survival_state`.

DETERMINISM: asserts on KEY NAMES, never values -> survival-VALUE / timestamp /
observed_commit / ordering / warm-vs-cold nondeterminism cannot flip the verdict.

The whitelist is built from the REAL fixed-blame payload key universe (the
`_build_json_payload` shape with `anchors_for_commit_with_survival` swapped for
the attribution-only `anchors_for_commit`, exactly what the fix does): the
top-level `commit`/`coverage`/`traces`/`files`/`trailEvidence`/
`entity_contributions` keys plus every nested attribution field, minus the lone
survival-named key `survival_state` (which the substring gate owns). A correct
fix emits a survival-free SUBSET of this universe, so it passes; any unexpected
key (a rename, shape drift) goes RED.

Optional `--simulate-leak` red-capability self-test: the fix adds a hidden
test-only flag that injects a survival key, proving the survival-key branch is
RED-capable. If the flag is accepted, this probe asserts the run DOES contain a
detectable leak; if the flag is unsupported/errors (as on main), the leg is
SKIPPED gracefully — the no-leak run is the gate.

Imports nothing from the code-under-test for the gate (the verdict is the real
CLI's JSON). The trail API is used only for an informational oracle cross-check.
Location-relative: a grader worktree exercises that worktree's `src/` + CLI.

exit 0 GREEN / 1 RED / 2 SETUP-INVALID (CLI not found in the probe's worktree).
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

OT = REPO / ".venv" / "bin" / "opentraces"
# Ticket's canonical heavily-anchored commit: 951 git_anchor_created anchors.
COMMIT = "884a1a6bc1d598f13351140d2da03e64d2062b90"
# Documented trail-CLI guard; rc=124 / over-budget == survival recompute still
# in the blame path == RED (forge pin, unchanged).
BUDGET = 25

# Positive attribution-only whitelist of allowed dict KEY names. Derived from the
# real fixed-blame payload universe (`_build_json_payload` under the
# attribution-only `anchors_for_commit` swap) UNION the documented but
# situationally-empty entity/trace/file-line/disagreement schema keys, MINUS the
# single survival-named key (`survival_state`), which the substring gate owns.
# Any payload key outside this set goes RED — that is what catches a `surv_state`
# / `liveness` / `alive_state` rename a substring scan misses.
ATTRIBUTION_KEY_WHITELIST = frozenset({
    # ---- top-level payload (blame commit --json) ----
    "commit", "coverage", "traces", "files", "hookLinked", "trailEvidence",
    "projection_disagreements", "projection_limitations", "entity_contributions",
    # ---- commit / coverage ----
    "sha", "subject", "timestamp", "attributed", "total", "ratio",
    # ---- trailEvidence rows (flat anchor fields) ----
    "affected_range", "anchor_relation", "call_type", "capture_method",
    "commit_id", "commit_ref", "commit_sha", "containing_segment_id",
    "containing_segment_ref", "content_status", "current_state", "display_id",
    "event_id", "event_log_ref", "event_sequence", "event_time", "event_type",
    "evidence", "evidence_firmness", "evidence_refs", "evidence_tier",
    "file_path", "firmness", "generation_index", "git_anchor",
    "git_anchor_event_id", "git_anchor_event_sequence", "git_anchor_id",
    "git_anchor_ref", "landing", "limitation_details", "limitations",
    "line_count", "line_origin", "line_origin_refs", "lineage_key", "origin",
    "parent_step", "parent_step_id", "patch_status", "path", "range", "ranges",
    "relation", "resource_ref", "resource_refs", "source_events", "source_refs",
    "step", "step_id", "step_index", "step_metadata", "subagent_trajectory_ref",
    "trace", "trace_id", "trace_patch", "trace_patch_event_id",
    "trace_patch_event_sequence", "trace_patch_event_time", "trace_patch_id",
    "trace_patch_ref", "trace_patch_trail", "trace_slice",
    # ---- nested contract objects (enrich_trail_row) ----
    "affects", "algo", "algorithm", "canonicalization", "code", "end_line",
    "end_step_index", "file_line_origin", "hex", "id", "id_format", "index",
    "kind", "label", "line", "message", "observed_at_commit", "observed_at_ref",
    "parent_ref", "rank", "reason_codes", "ref", "repo_ref", "severity", "side",
    "source", "start_line", "start_step_index", "tier", "tool_call_id",
    "tool_name",
    # ---- entity_contributions / traces / file-line / disagreement schema
    #      (empty for this commit; whitelisted so a populated re-derived bucket
    #       never false-REDs) ----
    "short_name", "entities", "chunks", "change_type", "entity_type",
    "entity_name", "old_entity_name", "start", "end", "name", "model",
    "lines", "n", "consistency", "blob_sha256", "type", "trace_ids",
    "trace_patch_ids",
})

# Maps whose KEYS are data (e.g. file paths under `files`), not schema names.
DATA_KEYED_PARENTS = frozenset({"files"})

_UUIDISH = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-", re.IGNORECASE)
_HEXISH = re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE)
_FILE_EXT = (
    ".py", ".md", ".json", ".txt", ".sh", ".toml", ".js", ".ts", ".tsx",
    ".html", ".css", ".yml", ".yaml", ".cfg", ".ini", ".rs", ".go",
)


def _is_data_key(ks: str) -> bool:
    """A key that is DATA, not a schema name (path / uuid / sha / filename).

    Skipping these in the whitelist scan keeps a populated re-derived bucket
    (path-keyed `files`, uuid/sha-keyed maps) from false-RED, without weakening
    rename detection: a survival rename like `surv_state` matches none of these
    shapes and is still checked.
    """
    if "/" in ks or "\\" in ks:
        return True
    if _UUIDISH.match(ks) or _HEXISH.match(ks):
        return True
    return ks.endswith(_FILE_EXT)


def _survival_keys(o, path="$"):
    """The forge's substring scan, KEPT verbatim: every dict key whose name
    contains ``survival`` anywhere in the payload. Returns (json_path, value)."""
    f = []
    if isinstance(o, dict):
        for k, v in o.items():
            kp = f"{path}.{k}"
            if "survival" in str(k).lower():
                f.append((kp, repr(v)[:50]))
            f += _survival_keys(v, kp)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            f += _survival_keys(v, f"{path}[{i}]")
    return f


def _non_whitelisted_keys(o, parent_key=None, path="$"):
    """HARDENING: every SCHEMA dict key outside ATTRIBUTION_KEY_WHITELIST.

    Skips data-keyed map keys (file paths under `files`, plus any path/uuid/sha/
    filename-shaped key) so data never false-REDs; checks every remaining key
    name. Catches a `surv_state`/`liveness` rename the substring scan misses.
    Returns the json paths of offending keys.
    """
    bad = []
    if isinstance(o, dict):
        data_keyed = parent_key in DATA_KEYED_PARENTS
        for k, v in o.items():
            ks = str(k)
            kp = f"{path}.{ks}"
            if not (data_keyed or _is_data_key(ks)):
                if ks not in ATTRIBUTION_KEY_WHITELIST:
                    bad.append(kp)
            bad += _non_whitelisted_keys(v, parent_key=ks, path=kp)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            bad += _non_whitelisted_keys(v, parent_key=parent_key, path=f"{path}[{i}]")
    return bad


def _initialized_project():
    """Locate an opentraces-initialized project to blame, LOCATION-RELATIVELY.

    The src under test is the probes-worktree `src/` (the binary at
    REPO/.venv/bin/opentraces imports it), but the *project* being blamed must
    carry an `.opentraces.json` marker (or legacy `.opentraces/` dir) or blame
    refuses with rc=2 ("not opted in to review"). The canonical event log lives
    in the SHARED common git dir, so the initialized worktree (this worktree if
    it is initialized, else the primary worktree = parent of the common git dir)
    reads the very same 951 anchors. No path is hardcoded.
    """
    candidates = [REPO]
    try:
        cg = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(REPO), capture_output=True, text=True,
        )
        if cg.returncode == 0 and cg.stdout.strip():
            candidates.append(Path(cg.stdout.strip()).parent)
    except Exception:  # noqa: BLE001
        pass
    seen = set()
    for c in candidates:
        c = c.resolve()
        if c in seen:
            continue
        seen.add(c)
        if (c / ".opentraces.json").exists() or (c / ".opentraces").is_dir():
            return c
    return None


def _run_cli(project, extra_args, budget):
    """Run `opentraces trail blame commit <COMMIT> --project <project> --json
    [extra]`, killing the whole process group on over-budget so the verdict is
    rc=124 (== the forge's `timeout` semantics, portably, no coreutils dep)."""
    proc = subprocess.Popen(
        [str(OT), "trail", "blame", "commit", COMMIT,
         "--project", str(project), "--json", *extra_args],
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=budget)
        return proc.returncode, out, err
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        return 124, "", ""  # over-budget == RED


def _oracle_anchor_count(project):
    """Informational cross-check: canonical git_anchor_created anchor count for
    the commit, read from the shared common git dir via the SAME trail API the
    CLI uses (build_trail_query_projection_for_commits -> anchors_for_commit).
    Best-effort and NON-GATING — the gate is the real CLI JSON, per the forge
    draft (which pins no anchor-count oracle for A8)."""
    try:
        from opentraces.core.trails import build_trail_query_projection_for_commits

        proj = build_trail_query_projection_for_commits(project, {COMMIT})
        return len(proj.anchors_for_commit(COMMIT))
    except Exception as exc:  # noqa: BLE001
        return f"(oracle unavailable: {type(exc).__name__})"


def _simulate_leak_selftest(project):
    """Optional `--simulate-leak` red-capability leg. The FIX adds this hidden
    test-only flag to inject a survival key. If accepted, the injected leak MUST
    be detectable (proving the survival-key branch is RED-capable). If the flag
    is unsupported/errors (rc!=0, as on main) or its output does not parse, SKIP
    gracefully — the no-leak run is the gate."""
    rc, out, _err = _run_cli(project, ["--simulate-leak"], BUDGET)
    if rc != 0:
        return ("SKIP",
                f"simulate-leak: flag unsupported/errored (rc={rc}) -> leg skipped")
    try:
        payload = json.loads(out)
    except Exception:
        return ("SKIP", "simulate-leak: output did not parse -> leg skipped")
    leaks = _survival_keys(payload)
    unexpected = _non_whitelisted_keys(payload)
    if leaks or unexpected:
        ex = leaks[0][0] if leaks else unexpected[0]
        return ("PASS",
                f"simulate-leak: RED-capable -> injected leak detected (e.g. {ex})")
    return ("FAIL",
            "simulate-leak: flag accepted but NO leak detected "
            "(survival-key branch not proven red-capable)")


def _run() -> int:
    if not OT.exists():
        print(f"[SETUP-INVALID] opentraces CLI not found at {OT}")
        return 2

    project = _initialized_project()
    if project is None:
        print("[SETUP-INVALID] no opentraces-initialized project found "
              "(neither the probe worktree nor the primary worktree carries an "
              ".opentraces marker); cannot blame the canonical commit")
        return 2
    print(f"project: {project}")

    # false-RED guard (forge): warm the scoped event_index accelerator so a cold
    # index cannot push a fixed (interactive) blame just past budget and fake a
    # hang. One prior identical run, result ignored.
    _run_cli(project, [], BUDGET)

    rc, out, err = _run_cli(project, [], BUDGET)

    if rc == 124:
        print(f"RED: blame commit {COMMIT[:8]} HUNG past {BUDGET}s budget "
              "(rc=124 / over-budget) -> survival recompute still in the blame path")
        return 1
    if rc != 0:
        print(f"RED: blame commit {COMMIT[:8]} exited rc={rc}")
        if err:
            print("stderr tail:", err[-400:])
        return 1
    try:
        payload = json.loads(out)
    except Exception as e:  # noqa: BLE001
        print(f"RED: blame --json did not parse: {e}")
        return 1

    trail_ev = payload.get("trailEvidence") or []
    traces = payload.get("traces") or []
    ec = payload.get("entity_contributions") or []
    attribution_present = (len(trail_ev) >= 1) or (len(traces) >= 1) or (len(ec) >= 1)
    sha_ok = payload.get("commit", {}).get("sha", "")[:8] == COMMIT[:8]

    survival_leaks = _survival_keys(payload)          # forge substring gate (KEPT)
    unexpected = _non_whitelisted_keys(payload)       # positive-whitelist hardening
    oracle = _oracle_anchor_count(project)            # informational, non-gating

    print(f"observed: rc={rc} sha_ok={sha_ok} trailEvidence={len(trail_ev)} "
          f"traces={len(traces)} entity_contributions={len(ec)} "
          f"survival_keys={len(survival_leaks)} non_whitelisted_keys={len(unexpected)} "
          f"oracle_anchor_count={oracle}")

    if not sha_ok:
        print(f"RED: blame resolved wrong/empty commit -> {payload.get('commit')}")
        return 1
    if not attribution_present:
        print("RED: blame returned NO attribution for an anchored commit "
              "(empty-output dodge)")
        return 1
    if survival_leaks:
        print("RED: survival vocabulary LEAKED into blame output (seam not carved):")
        for kp, v in survival_leaks[:8]:
            print(f"   {kp} = {v}")
        return 1
    if unexpected:
        print("RED: non-whitelisted key(s) in blame payload "
              "(possible survival rename / shape drift):")
        for kp in unexpected[:8]:
            print(f"   {kp}")
        return 1

    # Hardening leg: prove the survival-key branch is red-capable when the fix's
    # test-only --simulate-leak flag exists; skip gracefully otherwise.
    status, note = _simulate_leak_selftest(project)
    print(note)
    if status == "FAIL":
        print("RED: --simulate-leak self-test ran clean but produced no detectable "
              "leak (survival-key branch not proven red-capable)")
        return 1

    print(f"GREEN: blame carries attribution and ZERO survival/unexpected keys "
          f"(positive whitelist of {len(ATTRIBUTION_KEY_WHITELIST)} attribution keys)")
    return 0


def test_probe_a8_blame_contract():
    rc = _run()
    assert rc != 2, "A8 SETUP-INVALID (opentraces CLI not found in the probe's worktree)"
    assert rc == 0, (
        "A8 RED: blame hangs past budget / leaks survival vocabulary / emits an "
        "unexpected key / is missing attribution (see printed observed line)"
    )


if __name__ == "__main__":
    sys.exit(_run())
