"""``c-compacted-session`` — issue #42, plan 077 phase-4 deferral.

The compaction-boundary world: ``c-context-tree-substrate`` plus the
committed ``context-tree-compacted`` corpus (5 pre-compaction turns, a
``system.compact_boundary`` with ``trigger=manual`` and a pinned
``isCompactSummary`` record, then 2 post-compaction turns) ingested
through the SAME fake-harness + ``_ingest-session`` chain the substrate
uses. The ``context-tree-compaction-fidelity`` and
``context-tree-ctx-show-bounded-default`` journeys fork from here.

Determinism note: the corpus pins every uuid (``*-otbox-ctx-compacted-*``)
so the compacted span is byte-deterministic; timestamps are the only
nondeterminism, and the journey-facing node-id template vars are resolved
at template-expansion time from the live event log
(``journey.py::_resolve_compaction_template_vars``) — never baked into
the snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..drivers.base import Driver
from ..env import Box, resolve_cli_argv
from . import Checkpoint, CheckpointError, register
from ._captured_helpers import (
    check as _check_helper,
    git as _git_helper,
    run_corpus_and_ingest,
    stage_harness_with_sessions,
)

_CHECKPOINT_NAME = "c-compacted-session"

_HARNESS_SRC = Path(__file__).resolve().parents[1] / "fake_harnesses" / "claude"
_SESSIONS_SRC = Path(__file__).resolve().parents[1] / "fixtures" / "sessions"

_COMPACTED_SESSION = "context-tree-compacted"
_COMPACTED_SESSION_ID = "sess-otbox-context-tree-compacted"

# Mirrors the corpus's _PRE_COMPACT_TURNS (session.py): the compacted
# span is every user/assistant uuid emitted before the boundary, in
# transcript order. The checkpoint refuses to cache unless the event
# log's lossy_diff_removed_uuids equals exactly this list.
_PRE_COMPACT_SLUGS = ("a", "b", "c", "d", "e")
_TURN_UUID_PARTS = (
    ("user", "prompt"),
    ("asst", "read"),
    ("user", "read-result"),
    ("asst", "edit"),
    ("user", "edit-result"),
    ("asst", "close"),
)


def expected_removed_uuids() -> list[str]:
    """The deterministic pre-compaction span the fixture pins."""
    out: list[str] = []
    for turn_index, slug in enumerate(_PRE_COMPACT_SLUGS, start=1):
        suffix = f"pre-{turn_index}-{slug}"
        for prefix, part in _TURN_UUID_PARTS:
            out.append(f"{prefix}-otbox-ctx-compacted-{suffix}-{part}")
    return out


def _check(result, label: str) -> None:
    _check_helper(result, checkpoint=_CHECKPOINT_NAME, label=label)


def _compacted_session_delta(driver: Driver, box: Box) -> None:
    paths = driver.paths(box)
    project = paths["project"]
    home = paths["home"]
    cli = resolve_cli_argv()

    # Stage the compacted corpus next to the parent's corpora (the
    # helper re-stages the harness byte-identically; idempotent).
    harness_dst, sessions_dir = stage_harness_with_sessions(
        driver, box, home,
        harness_src=_HARNESS_SRC,
        sessions_src=_SESSIONS_SRC,
        session_names=(_COMPACTED_SESSION,),
        checkpoint=_CHECKPOINT_NAME,
    )

    run_corpus_and_ingest(
        driver, box,
        project=project, home=home,
        harness_dst=harness_dst, sessions_dir=sessions_dir,
        session_name=_COMPACTED_SESSION, session_id=_COMPACTED_SESSION_ID,
        cli=cli, checkpoint=_CHECKPOINT_NAME,
    )
    _git_helper(driver, box, "add", "-A", checkpoint=_CHECKPOINT_NAME)
    _git_helper(
        driver, box, "commit", "-q", "-m",
        "ctx: compacted corpus (rolling-statistic helpers)",
        checkpoint=_CHECKPOINT_NAME,
    )

    # Keep the trace-side projection warm (parity with the substrate).
    _check(driver.exec(box, [*cli, "trace", "index"]), "trace index")

    audit = _verify_and_audit(driver, box, project)
    box.notes["c_compacted_session_audit"] = audit
    box.save()


def _verify_and_audit(driver: Driver, box: Box, project: str) -> dict:
    """Refuse to cache unless the compaction world is genuinely built."""
    # Resolve the compacted trace_id from project state.
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

    trace_id = ""
    for tr in (state.get("traces") or {}).values():
        if tr.get("session_id") == _COMPACTED_SESSION_ID:
            trace_id = tr.get("trace_id") or ""
            break
    if not trace_id:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: compacted session {_COMPACTED_SESSION_ID!r} "
            "produced no trace"
        )

    try:
        from opentraces.core.trails.event_log import read_events
        events = read_events(Path(project), verify=False)
    except Exception as exc:  # noqa: BLE001
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: could not read event log: {exc}"
        ) from exc

    compaction_events = [
        e for e in events
        if e.event_type == "context_compaction_observed" and e.trace_id == trace_id
    ]
    if len(compaction_events) != 1:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: expected exactly 1 context_compaction_observed "
            f"event for the compacted trace, got {len(compaction_events)}"
        )
    payload = compaction_events[0].payload or {}
    pre_node_id = payload.get("pre_node_id")
    post_node_id = payload.get("post_node_id")
    if not pre_node_id or not post_node_id:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: compaction event has null pre/post node ids "
            f"(pre={pre_node_id!r}, post={post_node_id!r})"
        )
    removed = list(payload.get("lossy_diff_removed_uuids") or [])
    expected = expected_removed_uuids()
    if removed != expected:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: lossy_diff_removed_uuids does not equal the "
            f"deterministic fixture span (got {len(removed)} uuids, expected "
            f"{len(expected)}; first divergence at "
            f"{next((i for i, (a, b) in enumerate(zip(removed, expected)) if a != b), 'length')})"
        )

    # Messages-layer size floor: the ctx-show-bounded-default journey's
    # `requires_messages_layer_bytes_min` precondition leans on this.
    messages_layer_bytes = 0
    for e in events:
        if e.event_type != "context_layer_captured" or e.trace_id != trace_id:
            continue
        p = e.payload or {}
        if p.get("layer_type") != "messages":
            continue
        content = p.get("content")
        size = len(json.dumps(content, separators=(",", ":")))
        messages_layer_bytes = max(messages_layer_bytes, size)
    if messages_layer_bytes < 8192:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: largest messages layer is "
            f"{messages_layer_bytes}B < the 8192B floor the bounded-default "
            "journey's precondition declares"
        )

    # Branch-shape sanity: the post node observed a compaction_fork.
    post_branch_type = ""
    for e in events:
        if e.event_type == "context_node_observed" and e.trace_id == trace_id:
            p = e.payload or {}
            if p.get("node_id") == post_node_id:
                post_branch_type = p.get("branch_type") or ""
                break
    if post_branch_type != "compaction_fork":
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: post-compaction node branch_type is "
            f"{post_branch_type!r}, expected 'compaction_fork'"
        )

    return {
        "trace_id": trace_id,
        "session_id": _COMPACTED_SESSION_ID,
        "compaction_count": len(compaction_events),
        "removed_uuids": removed,
        "messages_layer_byte_count": messages_layer_bytes,
        "context_tree_built": True,
    }


register(
    Checkpoint(
        name=_CHECKPOINT_NAME,
        composed_from="c-context-tree-substrate",
        delta=_compacted_session_delta,
        cache=True,
        description=(
            "c-context-tree-substrate + the context-tree-compacted corpus "
            "(5 pre-compaction turns, one manual /compact boundary with a "
            "pinned summary uuid, 2 post-compaction turns) ingested through "
            "the real JSONL capture chain. Exactly one "
            "context_compaction_observed event whose removed-uuid span "
            "equals the deterministic fixture span, plus a >=8KB messages "
            "layer (plan 077 phase-4 deferral, issue #42)."
        ),
        requires=("cli", "git"),
        provides={
            "captured_traces": 4,
            "survival_states": ["alive_on_path"],
            "branch_commits": 4,
            "context_tree_built": True,
            "git_repo_present": True,
            "post_commit_hook_installed": True,
            "branch_types": [
                "root", "linear", "subagent_fork", "rewind_branch",
                "compaction_fork",
            ],
            "edit_commits": 3,
            "compactions": 1,
            "messages_layer_bytes_max": 8192,
        },
    )
)
