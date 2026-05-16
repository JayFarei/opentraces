"""`c-captured-multi-skill` — plan 068 M68-3.

This checkpoint sits beside ``c-captured-real-session`` (plan 064) and
fills the "history depth" gap that single-session corpus left open:
plan 070's dataset-sync-with-history journeys need a substrate that
already contains multiple captured traces, across multiple commits,
exercising more than one slash-command skill.

The delta composes onto ``c-installed-source`` (an independent capture
chain — it does NOT compose onto ``c-captured-real-session``) and runs
the full capture loop **three times** against the same project:

    For index in [0, 1, 2]:
      1. Run the fake-claude harness with OTBOX_FAKE_SESSION=
         multi-skill-session and OTBOX_FAKE_SESSION_INDEX=<index>.
         The harness produces a distinct session_id
         ``sess-otbox-multi-skill-<index>`` and creates / edits
         ``src/feature_<index>.py``. The skill invoked alternates
         between ``skill-alpha`` (even index) and ``skill-beta``.
      2. ``opentraces _ingest-session`` against that transcript —
         mints a TraceRecord and writes TrailEvents into
         ``refs/opentraces/local/events/v1``.
      3. ``git add`` + ``git commit`` — the post-commit hook fires and
         writes the commit↔trace note for this iteration.
      4. ``setup watcher tick`` reconciles, then ``trail mature``
         against HEAD matures the patches into a Git Anchor.

After the loop: ``trace index rebuild`` populates the local BM25 +
semantic projection so consumer commands see the full set of three
captured traces.

The audit recorded in ``box.notes["c_captured_multi_skill_audit"]`` is
the canonical handle journeys forked from this checkpoint use:

    {
        "captured_session_count": 3,
        "trace_ids": [...3 distinct ids...],
        "session_ids": [...3 distinct ids...],
        "commit_shas": [...3 distinct shas...],
        "skills_invoked": ["skill-alpha", "skill-beta", "skill-alpha"],
        "head_commit_sha": <last commit>,
        ...
    }
"""

from __future__ import annotations

import json
from pathlib import Path

from ..drivers.base import Driver
from ..env import Box, resolve_cli_argv
from . import Checkpoint, CheckpointError, register
from ._captured_helpers import (
    capture_metadata_from_artifact,
    check as _check_helper,
    encode_claude_path,
    git as _git_helper,
    read_state_json,
    restore_from_capture,
    synthetic_capture_metadata,
    trace_for_session as _trace_id_for_session,
)

_CAPTURE_NAME = "c-captured-multi-skill"
_SESSION_NAME = "multi-skill-session"
# Must match _SKILLS in fixtures/sessions/multi-skill-session/session.py.
_SKILLS = ("skill-alpha", "skill-beta")
_ITERATIONS = 3

# tests/otbox/fake_harnesses/claude
_HARNESS_SRC = Path(__file__).resolve().parents[1] / "fake_harnesses" / "claude"
# tests/otbox/fixtures/sessions/
_SESSIONS_SRC = Path(__file__).resolve().parents[1] / "fixtures" / "sessions"


def _check(result, label: str) -> None:
    _check_helper(result, checkpoint=_CAPTURE_NAME, label=label)


def _git(driver: Driver, box: Box, *args: str):
    return _git_helper(driver, box, *args, checkpoint=_CAPTURE_NAME)


def _session_id_for(index: int) -> str:
    return f"sess-otbox-multi-skill-{index}"


def _skill_for(index: int) -> str:
    return _SKILLS[index % len(_SKILLS)]


def _derive_multi_skill_audit_from_restored_box(
    driver: Driver, box: Box, cap_meta: dict,
) -> dict:
    """Re-derive the multi-skill audit after an artifact restore.

    Plan 072 R2 — the artifact captures 3 sessions × 3 commits. We
    re-derive trace_ids from state.json (keyed off the session_ids
    the multi-skill harness mints) and walk ``git log`` for the
    matching commit shas. Skills are derived from the iteration
    rotation since the harness alternates deterministically.
    """
    paths = driver.paths(box)
    project = paths["project"]

    state_dir, state = read_state_json(driver, box)

    trace_ids: list[str] = []
    session_ids: list[str] = []
    skills_invoked: list[str] = []
    iteration_audits: list[dict] = []
    for index in range(_ITERATIONS):
        session_id = _session_id_for(index)
        skill_name = _skill_for(index)
        trace_id, step_count = _trace_id_for_session(state, session_id)
        if not trace_id:
            raise CheckpointError(
                f"multi-skill artifact restore missing trace for "
                f"session_id={session_id!r}; state at {state_dir}"
            )
        trace_ids.append(trace_id)
        session_ids.append(session_id)
        skills_invoked.append(skill_name)
        iteration_audits.append({
            "index": index,
            "session_id": session_id,
            "skill": skill_name,
            "trace_id": trace_id,
            "step_count": step_count,
        })

    # Commit shas — the 3 latest commits, oldest first (the order the
    # synthetic loop produced them in).
    log = driver.exec(box, [
        "git", "-C", project, "log",
        f"-n{_ITERATIONS}", "--format=%H",
    ])
    commit_shas: list[str] = []
    if log.ok and log.stdout.strip():
        commit_shas = list(reversed(log.stdout.strip().splitlines()))
    for index, sha in enumerate(commit_shas):
        if index < len(iteration_audits):
            iteration_audits[index]["commit_sha"] = sha

    return {
        "session_corpus": _SESSION_NAME,
        "captured_session_count": len(trace_ids),
        "trace_ids": trace_ids,
        "session_ids": session_ids,
        "commit_shas": commit_shas,
        "skills_invoked": skills_invoked,
        "head_commit_sha": commit_shas[-1] if commit_shas else None,
        "state_dir": state_dir,
        "iterations": iteration_audits,
        "capture_metadata": capture_metadata_from_artifact(cap_meta),
    }


def _captured_multi_skill_delta(driver: Driver, box: Box) -> None:
    # Plan 072 R3 — artifact-preferred, synthetic-fallback.
    cap_meta = restore_from_capture(driver, box, _CAPTURE_NAME)
    if cap_meta is not None:
        box.notes["c_captured_multi_skill_audit"] = (
            _derive_multi_skill_audit_from_restored_box(driver, box, cap_meta)
        )
        return

    paths = driver.paths(box)
    project = paths["project"]
    home = paths["home"]

    # 1. Real git project with a seed commit so the post-commit hook
    #    has history to work with on the first iteration's commit.
    driver.mkdir(box, project)
    driver.put_text(
        box, f"{project}/README.md",
        "# otbox captured-multi-skill checkpoint\n\n"
        "Driver-mediated fixture for plan 068 M68-3 — "
        "multi-session, multi-skill substrate.\n",
    )
    driver.mkdir(box, f"{project}/src")
    _git(driver, box, "init", "-q")
    _git(driver, box, "add", "-A")
    _git(driver, box, "commit", "-q", "-m", "seed: initial project")

    # 2. opentraces init.
    cli = resolve_cli_argv()
    _check(
        driver.exec(box, [*cli, "init", "--start-fresh", "--agent", "claude-code"]),
        "opentraces init",
    )

    # 3. opentraces setup git — install the post-commit hook.
    _check(
        driver.exec(box, [*cli, "setup", "git"]),
        "opentraces setup git",
    )

    # 4. Place the fake harness on PATH and mirror the corpus alongside
    #    it. Same staging contract as c-captured-real-session.
    bin_dir = f"{home}/bin"
    sessions_dir = f"{home}/share/otbox/sessions"
    driver.mkdir(box, bin_dir)
    driver.mkdir(box, sessions_dir)
    harness_dst = f"{bin_dir}/claude"
    driver.put_text(box, harness_dst, _HARNESS_SRC.read_text())
    _check(
        driver.exec(box, ["chmod", "+x", harness_dst]),
        "chmod +x harness",
    )

    session_dst = f"{sessions_dir}/{_SESSION_NAME}"
    driver.mkdir(box, session_dst)
    corpus_src = _SESSIONS_SRC / _SESSION_NAME
    for child in corpus_src.iterdir():
        target = f"{session_dst}/{child.name}"
        if child.is_file():
            driver.put_text(box, target, child.read_text())

    encoded_project = encode_claude_path(project)
    testvenv_python = f"{project}/.testvenv/bin/python"

    # State dir resolution — same single-project assumption the
    # captured-session checkpoint makes (opt-in by `init` creates
    # exactly one project under ~/.opentraces/projects/).
    state_dirs = driver.glob(box, f"{paths['opentraces_dir']}/projects/*")
    if len(state_dirs) != 1:
        raise CheckpointError(
            f"expected exactly one opted-in project after init, "
            f"got {len(state_dirs)} under {paths['opentraces_dir']}/projects"
        )
    state_dir = state_dirs[0]
    state_path = f"{state_dir}/state.json"

    trace_ids: list[str] = []
    session_ids: list[str] = []
    commit_shas: list[str] = []
    skills_invoked: list[str] = []
    iteration_audits: list[dict] = []

    # 5-9. Run the capture loop _ITERATIONS times.
    for index in range(_ITERATIONS):
        session_id = _session_id_for(index)
        skill_name = _skill_for(index)

        transcript_path = (
            f"{home}/.claude/projects/{encoded_project}/{session_id}.jsonl"
        )

        # 5. Fake-claude harness — produces transcript + applies the
        #    file mutation + fires the live hooks.
        _check(
            driver.exec(box, [testvenv_python, harness_dst], env_extra={
                "OTBOX_FAKE_SESSION": _SESSION_NAME,
                "OTBOX_FAKE_SESSION_INDEX": str(index),
                "OTBOX_FAKE_SESSIONS_DIR": sessions_dir,
                "OTBOX_PROJECT_DIR": project,
                "OTBOX_TRANSCRIPT_PATH": transcript_path,
            }),
            f"fake harness invocation (iteration {index})",
        )

        # 6. _ingest-session — mints the TraceRecord + Trace Patch events.
        _check(
            driver.exec(box, [
                *cli, "_ingest-session", transcript_path,
                "--project", project,
            ]),
            f"_ingest-session (iteration {index})",
        )

        # 7. Commit the harness's edit. The post-commit hook fires.
        _git(driver, box, "add", "-A")
        _git(
            driver, box, "commit", "-q", "-m",
            f"Session {index}: {skill_name} adds feature_{index}",
        )
        commit_sha = _git(driver, box, "rev-parse", "HEAD").stdout.strip()

        # 8. Watcher tick — same backstop the captured-session checkpoint
        #    uses; informational only when the activity probe says quiet.
        tick = driver.exec(box, [
            *cli, "setup", "watcher", "tick", "--project", project, "--json",
        ])
        _check(tick, f"setup watcher tick (iteration {index})")
        try:
            tick_report = json.loads(tick.stdout)
            if isinstance(tick_report, list):
                tick_report = tick_report[0] if tick_report else {}
        except json.JSONDecodeError:
            tick_report = {"raw": tick.stdout}

        # 9. Explicit trail mature against HEAD — the load-bearing
        #    maturation path for the per-iteration commit.
        mature = driver.exec(box, [
            *cli, "trail", "mature", "--commit", commit_sha,
            "--project", project, "--json",
        ])
        _check(mature, f"trail mature --commit HEAD (iteration {index})")
        try:
            mature_payload = json.loads(mature.stdout) if mature.stdout else {}
        except json.JSONDecodeError:
            mature_payload = {}

        # Resolve the trace_id minted this iteration by re-reading
        # state.json and looking for this iteration's session_id.
        _state_dir, state = read_state_json(driver, box)
        trace_id, step_count = _trace_id_for_session(state, session_id)
        if not trace_id:
            raise CheckpointError(
                f"iteration {index} ingested no trace matching "
                f"session_id={session_id!r}; state at {state_path} held "
                f"{len(state.get('traces') or {})} entries"
            )

        trace_ids.append(trace_id)
        session_ids.append(session_id)
        commit_shas.append(commit_sha)
        skills_invoked.append(skill_name)
        iteration_audits.append({
            "index": index,
            "session_id": session_id,
            "skill": skill_name,
            "trace_id": trace_id,
            "step_count": step_count,
            "commit_sha": commit_sha,
            "transcript_path": transcript_path,
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
        })

    # Trace Index rebuild — one pass after all three sessions land.
    _check(
        driver.exec(box, [*cli, "trace", "index", "rebuild"]),
        "trace index rebuild",
    )

    # Defensive: trace_ids and commit_shas must be distinct. If they
    # collapsed it means ingest re-used a record or the harness wrote
    # the same session_id twice; either way the substrate is broken
    # for the dataset-sync-with-history journeys this checkpoint feeds.
    if len(set(trace_ids)) != len(trace_ids):
        raise CheckpointError(
            f"c-captured-multi-skill produced duplicate trace_ids: {trace_ids}"
        )
    if len(set(commit_shas)) != len(commit_shas):
        raise CheckpointError(
            f"c-captured-multi-skill produced duplicate commit_shas: {commit_shas}"
        )
    if len(set(session_ids)) != len(session_ids):
        raise CheckpointError(
            f"c-captured-multi-skill produced duplicate session_ids: {session_ids}"
        )

    box.notes["c_captured_multi_skill_audit"] = {
        "session_corpus": _SESSION_NAME,
        "captured_session_count": len(trace_ids),
        "trace_ids": trace_ids,
        "session_ids": session_ids,
        "commit_shas": commit_shas,
        "skills_invoked": skills_invoked,
        "head_commit_sha": commit_shas[-1] if commit_shas else None,
        "state_dir": state_dir,
        "iterations": iteration_audits,
        "capture_metadata": synthetic_capture_metadata(),
    }


register(
    Checkpoint(
        name="c-captured-multi-skill",
        composed_from="c-installed-source",
        delta=_captured_multi_skill_delta,
        cache=True,
        description=(
            "c-installed-source + 3 captured Claude Code sessions across "
            "3 commits via the multi-skill-session corpus. Alternates two "
            "synthetic slash-command skills (skill-alpha, skill-beta) so "
            "the substrate contains multi-trace, multi-skill history — "
            "the seed dataset-sync-with-history journeys (plan 070) fork "
            "from for replay/incremental scenarios."
        ),
        # Plan 069 R3: three captured sessions across three commits, with
        # alternating skill identifiers (skill-alpha, skill-beta).
        provides={
            "captured_traces": 3,
            "survival_states": ["alive_on_path"],
            "skills": ["skill-alpha", "skill-beta"],
            "branch_commits": 3,
        },
    )
)
