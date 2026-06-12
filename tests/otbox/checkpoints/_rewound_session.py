"""``c-rewound-session`` — issue #42, plan 077 phase-4 deferral.

Thin audit-re-pinning checkpoint: the ``c-context-tree-substrate`` world
ALREADY contains the rewound corpus (``context-tree-rewound``, one
Esc-Esc orphan branch, zero subagents) — no new ingest is needed. The
substrate audit pins the journey-facing ``{trace_id}`` to the multi-turn
PRIMARY trace, which has no orphan branches, so the
``context-tree-branching-fidelity`` journey needs a checkpoint whose
audit flips ``{trace_id}`` to the REWOUND trace instead.

The delta re-derives the rewound trace id from the parent audit,
re-verifies the orphan-branch shape against the live event log (a
lying checkpoint must never enter the cache), and writes
``c_rewound_session_audit``. ``provides`` is the parent's verbatim.
"""

from __future__ import annotations

from pathlib import Path

from ..drivers.base import Driver
from ..env import Box
from . import Checkpoint, CheckpointError, register

_CHECKPOINT_NAME = "c-rewound-session"


def _rewound_session_delta(driver: Driver, box: Box) -> None:
    parent_audit = box.notes.get("c_context_tree_substrate_audit") or {}
    rewound_trace_id = str(parent_audit.get("rewound_trace_id") or "")
    subagent_trace_id = str(parent_audit.get("subagent_trace_id") or "")
    if not rewound_trace_id:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: parent substrate audit carries no "
            "rewound_trace_id"
        )

    paths = driver.paths(box)
    project = Path(paths["project"])
    try:
        from opentraces.core.trails.event_log import read_events
        events = read_events(project, verify=False)
    except Exception as exc:  # noqa: BLE001
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: could not read event log: {exc}"
        ) from exc

    # Exactly one orphan (rewind) branch on the rewound trace: count the
    # orphan ROOTS (rewind_branch nodes whose parent is not itself a
    # rewind_branch node), mirroring the query projection's
    # orphan_branch_roots semantics... the projection counts every
    # rewind_branch node as a root candidate, so verify via the
    # reconciled sentinel which carries the deduped orphan_branch_roots.
    orphan_roots: list[str] = []
    rewind_nodes = 0
    subagent_ids: list[str] = []
    for e in events:
        if e.event_type == "context_node_observed":
            p = e.payload or {}
            if e.trace_id == rewound_trace_id and p.get("branch_type") == "rewind_branch":
                rewind_nodes += 1
            if subagent_trace_id and e.trace_id == subagent_trace_id:
                sid = p.get("subagent_session_id")
                if sid and sid not in subagent_ids:
                    subagent_ids.append(sid)
        elif e.event_type == "context_tree_reconciled" and e.trace_id == rewound_trace_id:
            roots = (e.payload or {}).get("orphan_branch_roots") or []
            if roots:
                orphan_roots = [str(r) for r in roots]

    if len(orphan_roots) != 1:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: expected exactly 1 orphan branch root on the "
            f"rewound trace, got {len(orphan_roots)}"
        )
    if rewind_nodes < 1:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: rewound trace has no rewind_branch nodes"
        )
    if subagent_trace_id and not subagent_ids:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: subagent trace {subagent_trace_id!r} carries "
            "no subagent_session_id (branching journey's linkage half would lie)"
        )

    box.notes["c_rewound_session_audit"] = {
        "trace_id": rewound_trace_id,
        "session_id": str(parent_audit.get("rewound_session_id") or "sess-otbox-context-tree-rewound"),
        "rewound_trace_id": rewound_trace_id,
        "subagent_trace_id": subagent_trace_id,
        "subagent_session_ids_expected": subagent_ids,
        "orphan_branch_count": len(orphan_roots),
        "rewind_node_count": rewind_nodes,
        "context_tree_built": True,
    }
    box.save()


register(
    Checkpoint(
        name=_CHECKPOINT_NAME,
        composed_from="c-context-tree-substrate",
        delta=_rewound_session_delta,
        cache=True,
        description=(
            "c-context-tree-substrate with the journey-facing {trace_id} "
            "re-pinned to the REWOUND trace (one Esc-Esc orphan branch, "
            "zero subagents in that corpus; subagent linkage is asserted "
            "against the sibling subagent trace). Audit-only delta — the "
            "substrate world already contains every corpus (issue #42)."
        ),
        requires=("cli", "git"),
        provides={
            "captured_traces": 3,
            "survival_states": ["alive_on_path"],
            "branch_commits": 3,
            "context_tree_built": True,
            "git_repo_present": True,
            "post_commit_hook_installed": True,
            "branch_types": ["root", "linear", "subagent_fork", "rewind_branch"],
            "edit_commits": 3,
        },
    )
)
