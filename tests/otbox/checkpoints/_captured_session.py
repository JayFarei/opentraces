"""`c-captured-real-session` — plan 064 R3.

This is the checkpoint that closes the gap between "consumer APIs return
the empty-state envelope" and "consumer APIs return real data on a real
captured session." It composes onto ``c-installed-source`` and runs the
full capture chain inside the box:

    1. Initialise a git project (a real repo with one seed commit).
    2. ``opentraces init`` the project (creates the per-project state
       store and ``.opentraces.json`` marker).
    3. ``opentraces setup git`` to install the post-commit hook that
       correlates each commit to the traces whose Edit/Write tool calls
       produced its content.
    4. Place the fake harness on the box's PATH at ``$HOME/bin/claude``
       and point its sessions corpus at the in-repo fixtures.
    5. Invoke the harness with ``OTBOX_FAKE_SESSION=simple-refactor``,
       writing the transcript JSONL directly into the canonical Claude
       Code transcript path
       (``$HOME/.claude/projects/<encoded>/<session>.jsonl``) and
       applying the deterministic file edit on disk.
    6. Run the real Click ``_ingest-session`` command on that transcript
       — this is what creates Trace Patch events in
       ``refs/opentraces/local/events/v1``.
    7. ``git add`` + ``git commit`` — the post-commit hook fires and
       writes the commit↔trace link into ``refs/notes/opentraces``.
    8. ``setup watcher tick`` reconciles + matures the patches into a
       Git Anchor over the new commit.
    9. ``trace index rebuild`` populates the local BM25 + semantic
       Trace Index so consumer commands like ``trace query`` work.

After this delta runs, the box has REAL TrailEvents, REAL Git Anchors,
REAL ``refs/notes/opentraces`` entries, and a populated Trace Index —
all keyed off the harness's deterministic ``session_id``. The
checkpoint records the minted ``trace_id`` + the commit sha in
``box.notes["c_captured_session_audit"]`` so journeys forked from this
checkpoint can address the captured trace via ``{trace_id}`` /
``{commit_sha}`` templating in their TOML.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..drivers.base import Driver
from ..env import REPO_ROOT, Box, resolve_cli_argv
from . import Checkpoint, CheckpointError, register
from ._captured_helpers import (
    harness_interpreter,
    harness_source_with_shebang,
    capture_metadata_from_artifact,
    check as _check_helper,
    encode_claude_path,
    git as _git_helper,
    read_state_json,
    restore_from_capture,
    synthetic_capture_metadata,
    trace_for_session,
)

_CHECKPOINT_NAME = "c-captured-real-session"


def _check(result, label: str) -> None:
    _check_helper(result, checkpoint=_CHECKPOINT_NAME, label=label)


def _git(driver: Driver, box: Box, *args: str):
    return _git_helper(driver, box, *args, checkpoint=_CHECKPOINT_NAME)

# Pinned at the harness corpus: every step in this checkpoint depends on
# the corpus matching this name.
_SESSION_NAME = "simple-refactor"
_SESSION_ID = "sess-otbox-simple-refactor"
_CAPTURE_NAME = "c-captured-real-session"

# The Edit lands on step 3 in the parsed trace (1=user-intro, 2=Read,
# 3=Edit). Journeys forked from this checkpoint use {step_index} to
# address the anchored step against `trail explain`.
EDIT_STEP_INDEX = 3

# tests/otbox/fake_harnesses/claude
_HARNESS_SRC = Path(__file__).resolve().parents[1] / "fake_harnesses" / "claude"
# tests/otbox/fixtures/sessions/
_SESSIONS_SRC = Path(__file__).resolve().parents[1] / "fixtures" / "sessions"


def _derive_audit_from_restored_box(
    driver: Driver, box: Box, cap_meta: dict,
) -> dict:
    """Re-derive the audit dict from a freshly-restored captured artifact.

    Plan 072 R2: the artifact-restored box has the same on-disk shape
    as a synthetic-derived one (state.json under
    ``~/.opentraces/projects/<slug>/``, refs/notes/opentraces in the
    project repo, etc.). The audit is re-derived from the same
    sources the synthetic path reads from at the END of its delta.

    Intermediate command outputs (watcher tick stdout, ``trail
    mature`` stdout) are NOT preserved in the artifact, so the
    artifact-restored audit omits the numeric ``tick_*`` /
    ``mature_*`` fields. Journeys that depend on those numbers must
    branch on ``capture_metadata.source``.
    """
    state_dir, state = read_state_json(driver, box)
    trace_id, step_count = trace_for_session(state, _SESSION_ID)
    if not trace_id:
        # Fallback for any captured-artifact whose session_id naming
        # diverges from the synthetic id: take the first trace.
        traces = list((state.get("traces") or {}).values())
        if traces:
            trace_id = traces[0].get("trace_id")
            step_count = int(traces[0].get("step_count") or 0)
    if not trace_id:
        raise CheckpointError(
            "c-captured-real-session artifact restore produced no "
            f"traces; state at {state_dir}/state.json contained "
            f"{len(state.get('traces') or {})} entries"
        )

    paths = driver.paths(box)
    project = paths["project"]
    home = paths["home"]
    head = driver.exec(box, ["git", "-C", project, "rev-parse", "HEAD"])
    commit_sha = head.stdout.strip() if head.ok else ""
    encoded_project = encode_claude_path(project)
    transcript_path = (
        f"{home}/.claude/projects/{encoded_project}/{_SESSION_ID}.jsonl"
    )

    return {
        "session_id": _SESSION_ID,
        "session_corpus": _SESSION_NAME,
        "trace_id": trace_id,
        "step_count": step_count,
        "edit_step_index": EDIT_STEP_INDEX,
        "commit_sha": commit_sha,
        "transcript_path": transcript_path,
        "state_dir": state_dir,
        "capture_metadata": capture_metadata_from_artifact(cap_meta),
    }


def _captured_session_delta(driver: Driver, box: Box) -> None:
    # Plan 072 R3 — artifact-preferred, synthetic-fallback.
    cap_meta = restore_from_capture(driver, box, _CAPTURE_NAME)
    if cap_meta is not None:
        box.notes["c_captured_session_audit"] = _derive_audit_from_restored_box(
            driver, box, cap_meta,
        )
        return

    paths = driver.paths(box)
    project = paths["project"]
    home = paths["home"]

    # 1. Real git project with a seed commit so the post-commit hook has
    #    history to work with on commit #2 (the harness-driven commit).
    driver.mkdir(box, project)
    driver.put_text(
        box, f"{project}/README.md",
        "# otbox captured-session checkpoint\n\n"
        "Driver-mediated fixture for plan 064 — agent-centric session UAT.\n",
    )
    driver.mkdir(box, f"{project}/src")
    driver.put_text(
        box, f"{project}/src/app.py",
        'def greet(name: str) -> str:\n    return f"hello, {name}"\n',
    )
    _git(driver, box, "init", "-q")
    _git(driver, box, "add", "-A")
    _git(driver, box, "commit", "-q", "-m", "seed: initial project")

    # 2. opentraces init — opt the project in. Use --start-fresh so a
    #    re-resolved checkpoint doesn't trip the existing-marker guard.
    cli = resolve_cli_argv()
    _check(
        driver.exec(box, [*cli, "init", "--start-fresh", "--agent", "claude-code"]),
        "opentraces init",
    )

    # 3. opentraces setup git — install the post-commit hook that
    #    correlates commits back to traces via refs/notes/opentraces.
    _check(
        driver.exec(box, [*cli, "setup", "git"]),
        "opentraces setup git",
    )

    # 4. Place the fake harness on PATH and stage the corpus alongside
    #    it. We do not depend on the harness's in-tree corpus fallback —
    #    Tier 1 boxes don't have access to REPO_ROOT, so the corpus
    #    travels with the harness via OTBOX_FAKE_SESSIONS_DIR.
    bin_dir = f"{home}/bin"
    sessions_dir = f"{home}/share/otbox/sessions"
    driver.mkdir(box, bin_dir)
    driver.mkdir(box, sessions_dir)
    harness_dst = f"{bin_dir}/claude"
    driver.put_text(box, harness_dst, harness_source_with_shebang(_HARNESS_SRC))
    # The shell driver doesn't expose chmod; do it via exec so the bit
    # is set inside the box (Tier 0 same fs, Tier 1 over SSH).
    _check(
        driver.exec(box, ["chmod", "+x", harness_dst]),
        "chmod +x harness",
    )

    # Mirror the corpus root. Only the named session we'll invoke is
    # required, but copying the whole directory keeps the checkpoint
    # extensible to future corpora without re-touching the delta.
    session_dst = f"{sessions_dir}/{_SESSION_NAME}"
    driver.mkdir(box, session_dst)
    corpus_src = _SESSIONS_SRC / _SESSION_NAME
    for child in corpus_src.iterdir():
        target = f"{session_dst}/{child.name}"
        if child.is_file():
            driver.put_text(box, target, child.read_text())

    # 5. Run the harness. The Stop-hook contract puts the transcript
    #    under ~/.claude/projects/<encoded>/<session>.jsonl — the
    #    canonical path `_capture` and `setup watcher tick` look at.
    #
    #    The harness is a Python executable that invokes the live
    #    opentraces PreToolUse/PostToolUse hooks. It must run under
    #    the box's testvenv (the c-installed-source-built one) so
    #    ``import opentraces`` resolves. The agent-facing OT_REAL_REPL
    #    swap (R6) replaces this invocation with a real ``claude``
    #    binary on PATH — same env, same artefacts.
    encoded_project = encode_claude_path(project)
    transcript_path = (
        f"{home}/.claude/projects/{encoded_project}/{_SESSION_ID}.jsonl"
    )
    testvenv_python = f"{project}/.testvenv/bin/python"
    _check(
        driver.exec(box, [harness_interpreter(), harness_dst], env_extra={
            "OTBOX_FAKE_SESSION": _SESSION_NAME,
            "OTBOX_FAKE_SESSIONS_DIR": sessions_dir,
            "OTBOX_PROJECT_DIR": project,
            "OTBOX_TRANSCRIPT_PATH": transcript_path,
        }),
        "fake harness invocation",
    )

    # 6. _ingest-session creates the Trace Patch events in
    #    refs/opentraces/local/events/v1 *before* the commit lands, so
    #    the post-commit hook has patches in the inbox window to write
    #    a commit↔trace note for.
    _check(
        driver.exec(box, [
            *cli, "_ingest-session", transcript_path,
            "--project", project,
        ]),
        "_ingest-session",
    )

    # 7. Commit the harness's edit. The post-commit hook fires here.
    #    With the patches already in the inbox the hook records the
    #    commit↔trace note under refs/notes/opentraces and (in the
    #    same pass) writes Git Anchor events linking the trace patches
    #    to this commit.
    _git(driver, box, "add", "-A")
    _git(driver, box, "commit", "-q", "-m", "Add farewell() helper")
    commit_sha = _git(driver, box, "rev-parse", "HEAD").stdout.strip()

    # 8. Watcher tick — the maturation backstop. The post-commit hook
    #    is the fast path, but the watcher tick is the consumer-API
    #    shape and the path the daemon would have driven in
    #    production. It reconciles any filesystem mutations the hook
    #    boundary missed and matures anchors that the hook deferred.
    tick = driver.exec(box, [
        *cli, "setup", "watcher", "tick", "--project", project, "--json",
    ])
    _check(tick, "setup watcher tick")

    # 9. Explicit ``trail mature`` against HEAD — a belt-and-suspenders
    #    backstop. The watcher tick only matures when its activity
    #    probe says the project is "active"; calling ``trail mature``
    #    directly is the documented user-facing recovery path and
    #    keeps the checkpoint deterministic regardless of how the
    #    tick's bookkeeping evolves.
    mature = driver.exec(box, [
        *cli, "trail", "mature", "--commit", commit_sha,
        "--project", project, "--json",
    ])
    _check(mature, "trail mature --commit HEAD")
    try:
        tick_report = json.loads(tick.stdout)
        if isinstance(tick_report, list):
            tick_report = tick_report[0] if tick_report else {}
    except json.JSONDecodeError:
        tick_report = {"raw": tick.stdout}

    # 10. Trace Index rebuild — populates the local BM25 + semantic
    #    projection so `trace query` returns the captured trace.
    _check(
        driver.exec(box, [*cli, "trace", "index", "rebuild"]),
        "trace index rebuild",
    )

    # Resolve the trace_id minted by ingest. The state file under
    # ~/.opentraces/projects/<slug>/state.json has the canonical mapping.
    state_dirs = driver.glob(box, f"{paths['opentraces_dir']}/projects/*")
    if len(state_dirs) != 1:
        raise CheckpointError(
            f"expected exactly one opted-in project after init, "
            f"got {len(state_dirs)} under {paths['opentraces_dir']}/projects"
        )
    state_dir = state_dirs[0]
    state_path = f"{state_dir}/state.json"
    state_raw = driver.exec(box, ["cat", state_path])
    if not state_raw.ok:
        raise CheckpointError(
            f"could not read project state at {state_path}: "
            f"{state_raw.stderr.strip() or '<no stderr>'}"
        )
    try:
        state = json.loads(state_raw.stdout)
    except json.JSONDecodeError as exc:
        raise CheckpointError(
            f"malformed state.json at {state_path}: {exc}"
        ) from None

    # Find the trace whose session_id matches the harness's session.
    trace_id: str | None = None
    step_count = 0
    for trace in (state.get("traces") or {}).values():
        if trace.get("session_id") == _SESSION_ID:
            trace_id = trace.get("trace_id")
            step_count = int(trace.get("step_count") or 0)
            break
    if not trace_id:
        # Fall back to the first trace — useful diagnostic when the
        # session_id-keyed lookup misses.
        traces = list((state.get("traces") or {}).values())
        if traces:
            trace_id = traces[0].get("trace_id")
            step_count = int(traces[0].get("step_count") or 0)
    if not trace_id:
        raise CheckpointError(
            "c-captured-real-session ingested no traces; "
            f"state at {state_path} contained "
            f"{len(state.get('traces') or {})} entries"
        )

    # Parse the explicit `trail mature` payload — its anchors_created /
    # searches_completed counts are the load-bearing maturation numbers
    # used by the journey assertions (the watcher tick's counts are
    # informational only and can be 0 on quiet ticks).
    try:
        mature_payload = json.loads(mature.stdout) if mature.stdout else {}
    except json.JSONDecodeError:
        mature_payload = {}

    box.notes["c_captured_session_audit"] = {
        "session_id": _SESSION_ID,
        "session_corpus": _SESSION_NAME,
        "trace_id": trace_id,
        "step_count": step_count,
        # The captured Edit lands on step 3 in the parsed trace
        # (1=user-intro, 2=Read, 3=Edit). Journeys forked from here use
        # this as the canonical "anchored step" they explain against.
        "edit_step_index": EDIT_STEP_INDEX,
        "commit_sha": commit_sha,
        "transcript_path": transcript_path,
        "state_dir": state_dir,
        "tick_trail_maturation_anchors": int(
            tick_report.get("trail_maturation_anchors", 0)
            if isinstance(tick_report, dict) else 0
        ),
        "tick_trail_maturation_searches": int(
            tick_report.get("trail_maturation_searches", 0)
            if isinstance(tick_report, dict) else 0
        ),
        "mature_anchors_created": int(
            mature_payload.get("anchors_created", 0)
            if isinstance(mature_payload, dict) else 0
        ),
        "mature_searches_completed": int(
            mature_payload.get("searches_completed", 0)
            if isinstance(mature_payload, dict) else 0
        ),
        # Plan 072 R4 — provenance marker so consumers can branch on
        # artifact-restored vs synthetic-derived audits.
        "capture_metadata": synthetic_capture_metadata(),
    }


register(
    Checkpoint(
        name="c-captured-real-session",
        composed_from="c-installed-source",
        delta=_captured_session_delta,
        cache=True,
        description=(
            "c-installed-source + a real captured Claude Code session "
            "(fake harness + simple-refactor corpus): TrailEvents in "
            "refs/opentraces/local/events/v1, commit↔trace note under "
            "refs/notes/opentraces, matured Git Anchor, and a populated "
            "Trace Index — the seed every consumer-API happy-path "
            "journey forks from in plan 064."
        ),
        # Plan 069 R3: the post-delta box has one captured trace whose
        # patch matures to ``alive_on_path`` (the file the Edit touched
        # is unchanged after the commit lands).
        provides={
            "captured_traces": 1,
            "survival_states": ["alive_on_path"],
            "branch_commits": 1,
        },
    )
)
