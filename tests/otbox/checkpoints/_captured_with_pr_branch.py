"""`c-captured-with-pr-branch` — plan 068 M68-4.

Extends ``c-captured-real-session`` (which left the box with one
captured-session commit on ``main``) by branching off into
``feat/pr-branch-test`` and adding two more captured-session commits.
The resulting ``main..feat/pr-branch-test`` range has 2 commits, each
linked to its own trace via ``refs/notes/opentraces`` — the minimal
shape any PR-blame happy-path journey (plan 070) needs.

Why this exists
---------------
The parent checkpoint proves the single-commit capture chain works
end-to-end. PR-blame works at the *range* level: it walks every commit
between two refs and joins them back to traces. A checkpoint that only
provides one commit can't exercise that range walker, can't show
multi-commit attribution, and can't catch ordering/identity bugs in
the projection. This checkpoint provides the smallest 3-commit
``base..head`` shape (1 base + 2 branch commits) so journeys can assert
on a real branch history.

Strategy (Option C from the spec)
---------------------------------
The bundled ``simple-refactor`` corpus is hardcoded to a single
``session_id`` (``sess-otbox-simple-refactor``). Naively re-running it
twice would either (a) collide on session_id in the project state, or
(b) fail because ``src/app.py`` no longer matches the Edit's
``old_string``. Rather than introduce a new corpus, this checkpoint:

  1. Resets ``src/app.py`` to its canonical pre-state before each run.
  2. Removes any leftover harness transcript at the fixed path.
  3. Runs the harness — same corpus, same TrailEvents emitter, same
     hook chain — into the fixed transcript path.
  4. Renames the transcript and rewrites every ``sess-otbox-simple-refactor``
     occurrence inside it to a per-iteration ``sess-otbox-pr-branch-{i}``
     before calling ``_ingest-session``. Only ``session_id`` is
     rewritten; ``tool_use_id`` values keep their original strings so
     the post-commit hook can still join the freshly-written
     TrailEvents to the ingested trace via tool_use_id + content hash.

After both iterations:

  * ``main`` has the parent's single captured-session commit.
  * ``feat/pr-branch-test`` has that base commit plus two new
    captured-session commits, each with its own trace + commit↔trace
    note + matured Git Anchor.

The audit recorded in ``box.notes['c_captured_with_pr_branch_audit']``
is the contract journeys read out of: ``base_commit_sha``,
``base_branch``, ``branch_name``, ``branch_commit_count``,
``branch_commit_shas``, ``branch_trace_ids``, ``head_commit_sha``.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..drivers.base import Driver
from ..env import Box, resolve_cli_argv
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
    traces_by_created_at,
)

_CHECKPOINT_NAME = "c-captured-with-pr-branch"
# Plan B0 flip: the real-agent artifact produced by
# `make capture-refresh SCENARIO=claude-pr-branch` (scenario-named dir).
_CAPTURE_NAME = "claude-pr-branch"

# The corpus we reuse from c-captured-real-session.
_SESSION_NAME = "simple-refactor"
_FIXED_SESSION_ID = "sess-otbox-simple-refactor"

# Per-iteration session_ids the transcript is rewritten to before ingest.
# Each one becomes a distinct trace in the project state.
_BRANCH_NAME = "feat/pr-branch-test"
_BRANCH_COMMITS = 2

# Canonical pre-state for src/app.py — must match
# ``simple-refactor/session.py::_SOURCE_BEFORE``. We reset to this
# between iterations so the Edit's ``old_string`` still matches.
_SOURCE_BEFORE = 'def greet(name: str) -> str:\n    return f"hello, {name}"\n'

# tests/otbox/fake_harnesses/claude + tests/otbox/fixtures/sessions/ —
# staged by the synthetic chain when the parent world (possibly
# artifact-restored) doesn't already carry them.
_HARNESS_SRC = Path(__file__).resolve().parents[1] / "fake_harnesses" / "claude"
_SESSIONS_SRC = Path(__file__).resolve().parents[1] / "fixtures" / "sessions"


def _check(result, label: str) -> None:
    _check_helper(result, checkpoint=_CHECKPOINT_NAME, label=label)


def _clear_trace_index(driver: Driver, box: Box) -> None:
    """Drop the restored Trace Index before the final full rebuild.

    The parent checkpoint can carry SQLite WAL sidecars from the archived box.
    A restored derived index can occasionally report "database disk image is
    malformed" before ``trace index rebuild`` gets far enough to replace it.
    Clearing only the derived projection preserves the trace source data while
    keeping this PR-blame checkpoint deterministic.
    """
    code = (
        "from pathlib import Path; import sys; "
        "p=Path(sys.argv[1]); p.mkdir(parents=True, exist_ok=True); "
        "[child.unlink() for child in p.glob('index.db*') if child.is_file()]"
    )
    _check(
        driver.exec(box, ["python3", "-c", code, str(Path(box.opentraces_dir) / "index")]),
        "clear trace index before rebuild",
    )


def _git(driver: Driver, box: Box, *args: str):
    return _git_helper(driver, box, *args, checkpoint=_CHECKPOINT_NAME)


def _trace_id_for_session(state: dict, session_id: str) -> str | None:
    trace_id, _ = trace_for_session(state, session_id)
    return trace_id


def _derive_pr_branch_audit_from_restored_box(
    driver: Driver, box: Box, cap_meta: dict,
) -> dict:
    """Re-derive the PR-branch audit after an artifact restore.

    Plan 072 R2 — the artifact captures the post-branch state: a base
    branch holds the merge base, HEAD sits on the feature branch tip.
    Branch identity, base, and the per-commit shas are re-derived from
    the live git topology (real-agent artifacts choose their own
    branch names); trace ids are enumerated from state.json in capture
    order rather than assumed from the synthetic session naming.
    """
    paths = driver.paths(box)
    project = paths["project"]
    home = paths["home"]

    state_dir, state = read_state_json(driver, box)
    ordered = traces_by_created_at(state)
    if not ordered:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME} artifact restore produced no traces; "
            f"state at {state_dir}"
        )

    branch = driver.exec(box, [
        "git", "-C", project, "rev-parse", "--abbrev-ref", "HEAD",
    ])
    branch_name = branch.stdout.strip() if branch.ok else ""

    base_branch = ""
    for candidate in ("main", "master"):
        probe = driver.exec(box, [
            "git", "-C", project, "rev-parse", "--verify", "--quiet", candidate,
        ])
        if probe.ok:
            base_branch = candidate
            break
    base_commit_sha = ""
    if base_branch:
        merge_base = driver.exec(box, [
            "git", "-C", project, "merge-base", base_branch, "HEAD",
        ])
        base_commit_sha = merge_base.stdout.strip() if merge_base.ok else ""

    head_proc = driver.exec(box, [
        "git", "-C", project, "rev-parse", "HEAD",
    ])
    head_commit_sha = head_proc.stdout.strip() if head_proc.ok else ""

    # base..HEAD listed oldest-first so commit i pairs with branch run i.
    branch_commit_shas: list[str] = []
    if base_commit_sha:
        log = driver.exec(box, [
            "git", "-C", project, "log", "--reverse", "--format=%H",
            f"{base_commit_sha}..HEAD",
        ])
        if log.ok and log.stdout.strip():
            branch_commit_shas = log.stdout.strip().splitlines()

    # Subjects pair 1:1 with the shas. Journeys must assert on these via
    # {branch_commit_subject_N} template vars, never literal subjects —
    # real-agent artifacts word their own commits (#49 regen finding).
    branch_commit_subjects: list[str] = []
    for sha in branch_commit_shas:
        sub = driver.exec(box, [
            "git", "-C", project, "log", "-1", "--format=%s", sha,
        ])
        branch_commit_subjects.append(sub.stdout.strip() if sub.ok else "")

    # Synthetic-shaped artifacts carry the parent corpus session; the
    # remaining traces (capture order) are the branch sessions. Real
    # artifacts have no parent session — every trace is branch work.
    parent_trace_id = _trace_id_for_session(state, _FIXED_SESSION_ID)
    parent_session_id = _FIXED_SESSION_ID if parent_trace_id else ""
    branch_trace_ids = [
        t.get("trace_id") for t in ordered
        if t.get("trace_id") and t.get("trace_id") != parent_trace_id
    ]

    transcript_dirs = driver.glob(box, f"{home}/.claude/projects/*")
    transcript_dir = transcript_dirs[0] if len(transcript_dirs) == 1 else ""

    return {
        "base_commit_sha": base_commit_sha,
        "base_branch": base_branch,
        "branch_name": branch_name,
        "branch_commit_count": len(branch_commit_shas),
        "branch_commit_shas": branch_commit_shas,
        "branch_commit_subjects": branch_commit_subjects,
        "branch_trace_ids": branch_trace_ids,
        "head_commit_sha": head_commit_sha,
        "parent_trace_id": parent_trace_id,
        "parent_session_id": parent_session_id,
        "state_dir": state_dir,
        "transcript_dir": transcript_dir,
        "capture_metadata": capture_metadata_from_artifact(cap_meta),
    }


def _captured_with_pr_branch_delta(driver: Driver, box: Box) -> None:
    # Plan 072 R3 — artifact-preferred, synthetic-fallback. Contract
    # gate: this checkpoint advertises 3 captured traces (parent + 2
    # branch sessions); an artifact below that floor (e.g. one real
    # claude session spanning the whole branch) falls back to the
    # synthetic chain instead of building a world verify_provides
    # would refuse.
    cap_meta = restore_from_capture(
        driver, box, _CAPTURE_NAME, min_traces=_BRANCH_COMMITS + 1,
    )
    if cap_meta is not None:
        box.notes["c_captured_with_pr_branch_audit"] = (
            _derive_pr_branch_audit_from_restored_box(driver, box, cap_meta)
        )
        return

    parent_audit = box.notes.get("c_captured_session_audit") or {}
    base_commit_sha = parent_audit.get("commit_sha")
    if not base_commit_sha:
        raise CheckpointError(
            "c-captured-with-pr-branch requires the parent checkpoint's "
            "audit (commit_sha); got "
            f"{parent_audit!r}"
        )
    # Re-resolve state_dir against the CURRENT box. The parent's
    # ``state_dir`` audit field is recorded as an absolute path during
    # the parent's cold build and points at the original box id; after
    # snapshot restore the actual state lives in this (forked) box's
    # opentraces_dir under a fresh box id, so we must glob it live.
    paths = driver.paths(box)
    state_dirs = driver.glob(box, f"{paths['opentraces_dir']}/projects/*")
    if len(state_dirs) != 1:
        raise CheckpointError(
            "expected exactly one opted-in project under "
            f"{paths['opentraces_dir']}/projects, got {len(state_dirs)}"
        )
    state_dir = state_dirs[0]
    state_path = f"{state_dir}/state.json"

    project = paths["project"]
    home = paths["home"]
    cli = resolve_cli_argv()

    # The parent's SYNTHETIC chain puts the fake harness on PATH at
    # $HOME/bin/claude with the corpus mirrored under
    # $HOME/share/otbox/sessions/. An ARTIFACT-restored parent (real
    # claude session) carries neither, so stage them here when absent —
    # same staging contract as c-captured-real-session.
    harness_dst = f"{home}/bin/claude"
    sessions_dir = f"{home}/share/otbox/sessions"
    if not driver.path_exists(box, harness_dst):
        driver.mkdir(box, f"{home}/bin")
        driver.put_text(
            box, harness_dst,
            harness_source_with_shebang(_HARNESS_SRC),
        )
        _check(
            driver.exec(box, ["chmod", "+x", harness_dst]),
            "chmod +x harness (staged for artifact-restored parent)",
        )
    session_dst = f"{sessions_dir}/{_SESSION_NAME}"
    if not driver.path_exists(box, session_dst):
        driver.mkdir(box, session_dst)
        for child in (_SESSIONS_SRC / _SESSION_NAME).iterdir():
            if child.is_file():
                driver.put_text(box, f"{session_dst}/{child.name}", child.read_text())

    encoded_project = encode_claude_path(project)
    transcript_dir = f"{home}/.claude/projects/{encoded_project}"
    fixed_transcript = f"{transcript_dir}/{_FIXED_SESSION_ID}.jsonl"
    testvenv_python = f"{project}/.testvenv/bin/python"

    # 1. Branch off from main onto the feature branch.
    _git(driver, box, "switch", "-c", _BRANCH_NAME)

    branch_commit_shas: list[str] = []
    branch_trace_ids: list[str] = []

    for i in range(1, _BRANCH_COMMITS + 1):
        iter_label = f"iteration {i}"
        new_session_id = f"sess-otbox-pr-branch-{i}"
        new_transcript = f"{transcript_dir}/{new_session_id}.jsonl"

        # 2. Reset src/app.py to the canonical pre-state so the Edit's
        #    ``old_string`` (== _SOURCE_BEFORE) matches on disk.
        driver.put_text(box, f"{project}/src/app.py", _SOURCE_BEFORE)
        # Make sure the working tree is clean before the harness runs.
        # We `git checkout` after the put_text so the index matches HEAD
        # for the unchanged path (no-op if put_text already matched).
        _check(
            driver.exec(box, ["git", "checkout", "--", "src/app.py"]),
            f"{iter_label}: git checkout src/app.py",
        )

        # 3. Remove any leftover transcript at the fixed path so the
        #    harness writes a fresh one.
        driver.exec(box, ["rm", "-f", fixed_transcript])

        # 4. Run the harness. Same env shape as the parent checkpoint —
        #    the harness is a Python entrypoint that imports session.py
        #    and fires the live opentraces hooks. Edits are applied
        #    against src/app.py inside the project.
        _check(
            driver.exec(
                box,
                [harness_interpreter(), harness_dst],
                env_extra={
                    "OTBOX_FAKE_SESSION": _SESSION_NAME,
                    "OTBOX_FAKE_SESSIONS_DIR": sessions_dir,
                    "OTBOX_PROJECT_DIR": project,
                    "OTBOX_TRANSCRIPT_PATH": fixed_transcript,
                },
            ),
            f"{iter_label}: fake harness invocation",
        )

        # 5. Read the transcript, rewrite session_id occurrences to the
        #    per-iteration id, write to the new transcript path, and
        #    remove the original. We deliberately do NOT rewrite the
        #    tool_use_id strings — the post-commit hook joins newly
        #    written TrailEvents to the ingested trace via tool_use_id +
        #    content hash, so they must stay as the harness emitted them.
        read = driver.exec(box, ["cat", fixed_transcript])
        if not read.ok:
            raise CheckpointError(
                f"{iter_label}: could not read fixed transcript "
                f"at {fixed_transcript}: "
                f"{read.stderr.strip() or '<no stderr>'}"
            )
        rewritten = read.stdout.replace(_FIXED_SESSION_ID, new_session_id)
        driver.put_text(box, new_transcript, rewritten)
        driver.exec(box, ["rm", "-f", fixed_transcript])

        # 6. _ingest-session creates the Trace Patch events for this
        #    iteration's session before the commit lands, so the
        #    post-commit hook can write the commit↔trace note.
        _check(
            driver.exec(
                box,
                [
                    *cli,
                    "_ingest-session",
                    new_transcript,
                    "--project",
                    project,
                ],
            ),
            f"{iter_label}: _ingest-session",
        )

        # 7. Commit the harness's edit on this branch. The post-commit
        #    hook fires here and writes refs/notes/opentraces +
        #    Git Anchor events for the just-ingested trace.
        _git(driver, box, "add", "-A")
        _git(driver, box, "commit", "-q", "-m", f"PR branch commit {i}")
        commit_sha = _git(driver, box, "rev-parse", "HEAD").stdout.strip()
        if not commit_sha:
            raise CheckpointError(
                f"{iter_label}: could not resolve HEAD after commit"
            )
        branch_commit_shas.append(commit_sha)

        # 8. Watcher tick + explicit `trail mature --commit <sha>` —
        #    same belt-and-suspenders maturation the parent does for
        #    its single commit.
        _check(
            driver.exec(
                box,
                [
                    *cli,
                    "setup",
                    "watcher",
                    "tick",
                    "--project",
                    project,
                    "--json",
                ],
            ),
            f"{iter_label}: setup watcher tick",
        )
        _check(
            driver.exec(
                box,
                [
                    *cli,
                    "trail",
                    "mature",
                    "--commit",
                    commit_sha,
                    "--project",
                    project,
                    "--json",
                ],
            ),
            f"{iter_label}: trail mature --commit",
        )

        # 9. Resolve this iteration's trace_id from the project state.
        _state_dir, state = read_state_json(driver, box)
        trace_id = _trace_id_for_session(state, new_session_id)
        if not trace_id:
            raise CheckpointError(
                f"{iter_label}: ingest produced no trace for "
                f"session_id={new_session_id!r}; state at {state_path} "
                f"contains {len(state.get('traces') or {})} entries"
            )
        branch_trace_ids.append(trace_id)

    # 10. Trace Index rebuild — same final step the parent does, so
    #     `trace query` returns the new branch traces too.
    _clear_trace_index(driver, box)
    _check(
        driver.exec(box, [*cli, "trace", "index", "rebuild"]),
        "trace index rebuild",
    )

    head_commit_sha = _git(driver, box, "rev-parse", "HEAD").stdout.strip()

    branch_commit_subjects = [
        _git(driver, box, "log", "-1", "--format=%s", sha).stdout.strip()
        for sha in branch_commit_shas
    ]

    box.notes["c_captured_with_pr_branch_audit"] = {
        "base_commit_sha": base_commit_sha,
        "base_branch": "main",
        "branch_name": _BRANCH_NAME,
        "branch_commit_count": len(branch_commit_shas),
        "branch_commit_shas": branch_commit_shas,
        "branch_commit_subjects": branch_commit_subjects,
        "branch_trace_ids": branch_trace_ids,
        "head_commit_sha": head_commit_sha,
        "parent_trace_id": parent_audit.get("trace_id"),
        "parent_session_id": parent_audit.get("session_id"),
        "state_dir": state_dir,
        "transcript_dir": transcript_dir,
        "capture_metadata": synthetic_capture_metadata(),
    }


register(
    Checkpoint(
        name="c-captured-with-pr-branch",
        composed_from="c-captured-real-session",
        delta=_captured_with_pr_branch_delta,
        # cache=False: artifact presence/freshness live outside the
        # checkpoint source hash (same rationale as the codex/pi lanes).
        cache=False,
        description=(
            "c-captured-real-session + a feature branch ("
            f"{_BRANCH_NAME}) with {_BRANCH_COMMITS} additional "
            "captured-session commits. Each branch commit has its own "
            "trace, commit↔trace note in refs/notes/opentraces, and "
            "matured Git Anchor — the minimal shape PR-blame "
            "happy-path journeys (plan 070) need to walk a real "
            "base..head range."
        ),
        # Plan 069 R3: parent's 1 captured trace + 2 branch traces = 3
        # total; branch_commits counts the parent + the 2 branch
        # commits (the post-condition contract PR-blame journeys
        # depend on).
        provides={
            "captured_traces": 3,
            "survival_states": ["alive_on_path"],
            "branch_commits": _BRANCH_COMMITS + 1,
        },
    )
)
