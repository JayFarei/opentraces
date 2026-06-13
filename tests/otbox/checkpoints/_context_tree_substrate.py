"""``c-context-tree-substrate`` — issue #42, plan 077 phase-4 deferral.

The Context Tree substrate checkpoint: a real captured-Claude world whose
``refs/opentraces/local/events/v1`` log carries the third event taxonomy
(``context_layer_captured`` / ``context_node_observed`` /
``context_compaction_observed`` / ``context_tree_reconciled``) alongside
Trace + Trail. It is what the 8 ``context-tree-*`` substrate journeys
(capture-fidelity, ctx-output-parity, ctx-prune-no-clobber,
ctx-resolve-empty-state, demo-acceptance, determinism, fork-fidelity,
temporal-anchor-precision) plus ``context-tree-branching-fidelity`` fork
from.

DESIGN — built on the c-captured-real-session JSONL CHAIN, not the OTel
builders. These journeys assert ``capture_method =
transcript_reconstruction`` fidelity (the JSONL parser path), so the
substrate must be produced by the same fake-harness + ``_ingest-session``
chain ``c-captured-real-session`` uses, NOT the OTel emitter (which stamps
``capture_method = otel``).

The committed Phase-2 fixtures under ``tests/otbox/fixtures/sessions/``
are purpose-built for this:

  * ``context-tree-multi-turn`` — 3 user turns, one Read + one Edit each
    across 3 distinct files, plus a closing Bash. This is the PRIMARY
    trace ``{trace_id}`` addresses: it has Read steps, ≥3 Edit steps
    (``requires_edit_commits_min = 3``), and a strictly-linear active
    path for the prune / fork-fidelity contracts.
  * ``context-tree-subagent`` — a Task tool_use that spawns a child
    JSONL; the parser observes a ``subagent_fork`` branch and a
    ``subagent_session_id``.
  * ``context-tree-rewound`` — an Esc-Esc rewind orphan branch; the
    parser observes a ``rewind_branch``.

All three are ingested into ONE project so the substrate's aggregated
``branch_types`` = {root, linear, subagent_fork, rewind_branch}. The
multi-turn trace's ids are pinned as the canonical ``{trace_id}`` /
``{step_index}`` / node-id template vars; the subagent / rewind traces
serve ``context-tree-fork-fidelity`` / ``context-tree-branching-fidelity``.

Node-id template vars (``{first_node_id}``, ``{first_read_node_id}``,
``{target_node_id}``, the active-path slices, ...) are resolved at
TEMPLATE-EXPANSION time from the project event log
(``journey.py::_resolve_context_tree_node_template_vars``), NOT baked into
the snapshot — so snapshot/restore across box ids never surfaces stale
content-addressed ids.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..drivers.base import Driver
from ..env import REPO_ROOT, Box, resolve_cli_argv
from . import Checkpoint, CheckpointError, register
from ._captured_helpers import (
    check as _check_helper,
    git as _git_helper,
    run_corpus_and_ingest,
    stage_harness_with_sessions,
)

_CHECKPOINT_NAME = "c-context-tree-substrate"

# tests/otbox/fake_harnesses/claude + fixtures/sessions/
_HARNESS_SRC = Path(__file__).resolve().parents[1] / "fake_harnesses" / "claude"
_SESSIONS_SRC = Path(__file__).resolve().parents[1] / "fixtures" / "sessions"

# Primary trace = multi-turn (3 edits + Read steps + linear active path).
_PRIMARY_SESSION = "context-tree-multi-turn"
_PRIMARY_SESSION_ID = "sess-otbox-context-tree-multi-turn"
# Branch-shape traces (subagent fork + rewind orphan).
_SUBAGENT_SESSION = "context-tree-subagent"
_SUBAGENT_SESSION_ID = "sess-otbox-context-tree-subagent"
_REWOUND_SESSION = "context-tree-rewound"
_REWOUND_SESSION_ID = "sess-otbox-context-tree-rewound"

# The multi-turn corpus's first Edit lands at step_index 3 (turn 1:
# user=1, Read=2, Edit=3). Journeys use {step_index} as the resume/anchor
# target.
_PRIMARY_EDIT_STEP_INDEX = 3


def _check(result, label: str) -> None:
    _check_helper(result, checkpoint=_CHECKPOINT_NAME, label=label)


def _git(driver: Driver, box: Box, *args: str):
    return _git_helper(driver, box, *args, checkpoint=_CHECKPOINT_NAME)


def _stage_harness(driver: Driver, box: Box, home: str) -> tuple[str, str]:
    """Place the fake-claude harness + the three context-tree corpora on
    the box. Returns (harness_dst, sessions_dir). Delegates to the
    hoisted ``_captured_helpers.stage_harness_with_sessions`` (issue #42)
    so composed checkpoints (c-compacted-session) reuse the same chain."""
    return stage_harness_with_sessions(
        driver, box, home,
        harness_src=_HARNESS_SRC,
        sessions_src=_SESSIONS_SRC,
        session_names=(_PRIMARY_SESSION, _SUBAGENT_SESSION, _REWOUND_SESSION),
        checkpoint=_CHECKPOINT_NAME,
    )


def _run_and_ingest(
    driver: Driver,
    box: Box,
    *,
    project: str,
    home: str,
    harness_dst: str,
    sessions_dir: str,
    session_name: str,
    session_id: str,
    cli: list[str],
) -> None:
    """Drive one corpus through the harness + _ingest-session chain."""
    run_corpus_and_ingest(
        driver, box,
        project=project, home=home,
        harness_dst=harness_dst, sessions_dir=sessions_dir,
        session_name=session_name, session_id=session_id,
        cli=cli, checkpoint=_CHECKPOINT_NAME,
    )


def _context_tree_substrate_delta(driver: Driver, box: Box) -> None:
    paths = driver.paths(box)
    project = paths["project"]
    home = paths["home"]

    # 1. Real git project + opentraces init + git hook (mirrors
    #    c-captured-real-session, but with a context-tree corpus seed).
    driver.put_text(
        box, f"{project}/README.md",
        "# otbox context-tree substrate checkpoint (issue #42 / plan 077)\n",
    )
    # Seed a project-scope CLAUDE.md BEFORE any corpus is ingested: the
    # claude_code system-layer builder reads the memory chain from disk
    # at ingest time, so this file is what makes the capture-fidelity
    # journey's `claude_md_set` containment assertion meaningful.
    driver.put_text(
        box, f"{project}/CLAUDE.md",
        "# context-tree substrate project\n\n"
        "Deterministic project memory seeded by the c-context-tree-substrate "
        "otbox checkpoint (issue #42).\n",
    )
    driver.mkdir(box, f"{project}/src")
    # Seed the files the corpora Read/Edit so the first Read returns
    # something definite. multi-turn seeds its own mod_*.py; the rewound
    # corpus edits src/normalize.py, the subagent reads src/lib.py.
    driver.put_text(box, f"{project}/src/normalize.py", "")
    driver.put_text(
        box, f"{project}/src/lib.py",
        "def one():\n    return 1\n\n\ndef two():\n    return 2\n",
    )
    _git(driver, box, "init", "-q")
    _git(driver, box, "add", "-A")
    _git(driver, box, "commit", "-q", "-m", "seed: context-tree substrate project")

    cli = resolve_cli_argv()
    _check(
        driver.exec(box, [*cli, "init", "--start-fresh", "--agent", "claude-code"]),
        "opentraces init",
    )
    _check(driver.exec(box, [*cli, "setup", "git"]), "opentraces setup git")

    harness_dst, sessions_dir = _stage_harness(driver, box, home)

    # 2. Ingest the PRIMARY (multi-turn) corpus, then commit so the
    #    three Edits land as real git history (edit_commits accrue).
    _run_and_ingest(
        driver, box, project=project, home=home, harness_dst=harness_dst,
        sessions_dir=sessions_dir, session_name=_PRIMARY_SESSION,
        session_id=_PRIMARY_SESSION_ID, cli=cli,
    )
    _git(driver, box, "add", "-A")
    _git(driver, box, "commit", "-q", "-m", "ctx: multi-turn alpha/beta/gamma helpers")

    # 3. Ingest the subagent + rewound corpora for branch-type diversity.
    _run_and_ingest(
        driver, box, project=project, home=home, harness_dst=harness_dst,
        sessions_dir=sessions_dir, session_name=_SUBAGENT_SESSION,
        session_id=_SUBAGENT_SESSION_ID, cli=cli,
    )
    _run_and_ingest(
        driver, box, project=project, home=home, harness_dst=harness_dst,
        sessions_dir=sessions_dir, session_name=_REWOUND_SESSION,
        session_id=_REWOUND_SESSION_ID, cli=cli,
    )
    _git(driver, box, "add", "-A")
    _git(driver, box, "commit", "-q", "-m", "ctx: subagent + rewound corpora")

    # 4. Rebuild the trace index so trace-side reads work too.
    _check(driver.exec(box, [*cli, "trace", "index"]), "trace index")

    # 5. VERIFY the world before it can be cached (a lying checkpoint must
    #    never enter the content-addressed cache). Read the event log and
    #    assert the substrate is genuinely populated.
    audit = _verify_and_audit(driver, box, project, home)
    box.notes["c_context_tree_substrate_audit"] = audit
    box.save()


def _verify_and_audit(
    driver: Driver, box: Box, project: str, home: str,
) -> dict:
    """Re-derive the substrate audit from on-disk evidence and assert the
    world is genuinely a context-tree substrate."""
    # Resolve the primary trace_id from project state.
    paths = driver.paths(box)
    state_dirs = driver.glob(box, f"{paths['opentraces_dir']}/projects/*")
    if not state_dirs:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: no opted-in project under "
            f"{paths['opentraces_dir']}/projects"
        )
    state_raw = driver.exec(box, ["cat", f"{state_dirs[0]}/state.json"])
    if not state_raw.ok:
        raise CheckpointError(f"{_CHECKPOINT_NAME}: cannot read project state.json")
    state = json.loads(state_raw.stdout)

    def _trace_for(session_id: str) -> tuple[str, int]:
        for tr in (state.get("traces") or {}).values():
            if tr.get("session_id") == session_id:
                return tr.get("trace_id") or "", int(tr.get("step_count") or 0)
        return "", 0

    primary_trace_id, primary_steps = _trace_for(_PRIMARY_SESSION_ID)
    subagent_trace_id, _ = _trace_for(_SUBAGENT_SESSION_ID)
    rewound_trace_id, _ = _trace_for(_REWOUND_SESSION_ID)
    if not primary_trace_id:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: primary session {_PRIMARY_SESSION_ID!r} "
            "produced no trace"
        )

    # Read the canonical event log to confirm context events landed and to
    # count branch types / edit commits.
    try:
        from opentraces.core.trails.event_log import read_events
        events = read_events(Path(project), verify=False)
    except Exception as exc:  # noqa: BLE001
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: could not read event log: {exc}"
        ) from exc

    ctx_event_types = {
        "context_layer_captured",
        "context_node_observed",
        "context_tree_reconciled",
    }
    have_event_types = {e.event_type for e in events}
    missing = ctx_event_types - have_event_types
    if missing:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: event log missing context event types: "
            f"{sorted(missing)} (have {sorted(have_event_types)})"
        )

    branch_types: set[str] = set()
    subagent_ids: set[str] = set()
    for e in events:
        if e.event_type == "context_node_observed":
            p = e.payload or {}
            if p.get("branch_type"):
                branch_types.add(p["branch_type"])
            if p.get("subagent_session_id"):
                subagent_ids.add(p["subagent_session_id"])

    # The substrate's headline contract: the rich branch shapes are all
    # present. If a fixture regresses, refuse to cache.
    required_branches = {"root", "linear", "subagent_fork", "rewind_branch"}
    if not required_branches <= branch_types:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: branch_types {sorted(branch_types)} do not "
            f"cover the required {sorted(required_branches)}"
        )

    # edit_commits: the number of distinct Edit operations the PRIMARY
    # (multi-turn) trace landed (one helper per turn -> 3). This is the
    # honest meaning the `requires_edit_commits_min` precondition wants —
    # "this world has >=N edit-bearing steps" — not git-commit count. We
    # read it off the primary trace's patch_created events.
    edit_commits = sum(
        1
        for e in events
        if e.event_type == "trace_patch_created" and e.trace_id == primary_trace_id
    )

    return {
        "trace_id": primary_trace_id,
        "session_id": _PRIMARY_SESSION_ID,
        "step_count": primary_steps,
        "step_index": _PRIMARY_EDIT_STEP_INDEX,
        "edit_step_index": _PRIMARY_EDIT_STEP_INDEX,
        "subagent_trace_id": subagent_trace_id,
        "subagent_session_id": _SUBAGENT_SESSION_ID,
        "rewound_trace_id": rewound_trace_id,
        "branch_types": sorted(branch_types),
        "subagent_session_ids": sorted(subagent_ids),
        "edit_commits": edit_commits,
        # Frozen-feature flag the resolver / preconditions read.
        "context_tree_built": True,
    }


register(
    Checkpoint(
        name=_CHECKPOINT_NAME,
        composed_from="c-installed-source",
        delta=_context_tree_substrate_delta,
        cache=True,
        description=(
            "c-installed-source + three context-tree corpora "
            "(multi-turn primary, subagent fork, rewound orphan) ingested "
            "through the real JSONL capture chain: the third "
            "(context_*) event taxonomy in refs/opentraces/local/events/v1, "
            "branch_types {root,linear,subagent_fork,rewind_branch}, and "
            "≥3 edit commits. The substrate every context-tree-* journey "
            "forks from (plan 077 phase-4 deferral, issue #42)."
        ),
        requires=("cli", "git"),
        provides={
            "captured_traces": 3,
            "survival_states": ["alive_on_path"],
            "branch_commits": 3,
            "context_tree_built": True,
            "git_repo_present": True,
            "post_commit_hook_installed": True,
            # Issue #42 context-tree world flags.
            "branch_types": ["root", "linear", "subagent_fork", "rewind_branch"],
            "edit_commits": 3,
        },
    )
)
