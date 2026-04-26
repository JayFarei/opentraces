"""Trace Snapshot capture and diffing for Trace Trails."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...enrichment.attribution import _parse_diff_hunks_with_content
from .event_log import append_event_batch
from .models import GitObjectID, TrailEventDraft


@dataclass(frozen=True)
class SnapshotResult:
    snapshot_id: str
    tree_id: dict[str, str]
    ref: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git(
    repo: Path,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout.strip()


def _id(prefix: str, material: dict[str, Any]) -> str:
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _head_id(repo: Path) -> dict[str, str] | None:
    out = _git(repo, ["rev-parse", "--verify", "HEAD"], check=False)
    if not out:
        return None
    return GitObjectID(hex=out).model_dump(mode="json")


def write_worktree_tree(repo: Path) -> dict[str, str]:
    """Compute a Git tree from the current worktree without touching the index."""
    repo = repo.resolve()
    with tempfile.TemporaryDirectory(prefix="opentraces-worktree-index-") as td:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(Path(td) / "index")
        if _head_id(repo):
            _git(repo, ["read-tree", "HEAD"], env=env)
        else:
            _git(repo, ["read-tree", "--empty"], env=env)
        _git(repo, ["add", "-A", "--", "."], env=env)
        tree = _git(repo, ["write-tree"], env=env)
    return GitObjectID(hex=tree).model_dump(mode="json")


def _create_snapshot_ref(repo: Path, ref: str, tree_hex: str) -> None:
    exists = _git(repo, ["show-ref", "--verify", ref], check=False)
    if exists:
        return
    _git(repo, ["update-ref", ref, tree_hex])


def append_step_snapshot(
    repo: Path,
    *,
    trace_id: str,
    step_index: int,
    agent_step_id: str,
    tool_call_id: str,
    capture_method: list[str],
    writer: str = "capture-claude-code",
    generation_index: int = 0,
    capture_status: str = "captured",
    limitations: list[str] | None = None,
    boundary_firmness: str = "firm",
    claimed_tree_id: dict[str, str] | None = None,
) -> SnapshotResult:
    """Capture one post-step Trace Snapshot plus its bracketing window events."""
    repo = repo.resolve()
    event_time = _utc_now()
    tree_id = write_worktree_tree(repo)
    git_head = _head_id(repo)
    limitations = list(limitations or [])
    if claimed_tree_id and claimed_tree_id != tree_id:
        limitations.append("hook_payload_state_mismatch")
    snapshot_id = _id(
        "snapshot",
        {
            "trace_id": trace_id,
            "generation_index": generation_index,
            "step_index": step_index,
            "tree_id": tree_id,
        },
    )
    ref = (
        f"refs/opentraces/local/traces/{trace_id}/{generation_index}"
        f"/snapshots/step_{step_index}"
    )

    window_payload = {
        "trace_id": trace_id,
        "generation_index": generation_index,
        "step_index": step_index,
        "agent_step_id": agent_step_id,
        "tool_call_id": tool_call_id,
        "worktree_root": str(repo),
        "git_head": git_head,
        "event_time": event_time,
        "capture_method": capture_method,
        "boundary_firmness": boundary_firmness,
        "capture_limitations": limitations,
    }
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_step_window_opened",
                trace_id=trace_id,
                generation_index=generation_index,
                step_index=step_index,
                event_time=event_time,
                capture_method=capture_method,
                payload={**window_payload, "boundary": "opened"},
            ),
            TrailEventDraft(
                event_type="trace_snapshot_created",
                trace_id=trace_id,
                generation_index=generation_index,
                step_index=step_index,
                event_time=event_time,
                capture_method=capture_method,
                payload={
                    "snapshot_id": snapshot_id,
                    "snapshot_role": "after",
                    "tree_id": tree_id,
                    "git_head": git_head,
                    "base_commit": git_head,
                    "capture_status": capture_status,
                    "limitations": limitations,
                },
            ),
            TrailEventDraft(
                event_type="trace_step_window_closed",
                trace_id=trace_id,
                generation_index=generation_index,
                step_index=step_index,
                event_time=event_time,
                capture_method=capture_method,
                payload={**window_payload, "boundary": "closed"},
            ),
        ],
        writer=writer,
    )
    _create_snapshot_ref(repo, ref, tree_id["hex"])
    return SnapshotResult(snapshot_id=snapshot_id, tree_id=tree_id, ref=ref)


def _snapshot_for_step(events: list, trace_id: str, step_index: int) -> tuple[dict, Any] | None:
    candidates = [
        (event.payload, event)
        for event in events
        if event.event_type == "trace_snapshot_created"
        and event.trace_id == trace_id
        and event.step_index == step_index
        and event.payload.get("snapshot_role") == "after"
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[1].event_sequence)


def diff_step_snapshots(repo: Path, trace_id: str, from_step: int, to_step: int) -> dict[str, Any]:
    """Return the Trace Patch between two captured step snapshots."""
    from .event_log import EVENT_LOG_REF, read_events

    events = read_events(repo)
    from_pair = _snapshot_for_step(events, trace_id, from_step)
    to_pair = _snapshot_for_step(events, trace_id, to_step)
    if from_pair is None or to_pair is None:
        missing = []
        if from_pair is None:
            missing.append(f"from_step:{from_step}")
        if to_pair is None:
            missing.append(f"to_step:{to_step}")
        return {
            "trace_id": trace_id,
            "from_step": from_step,
            "to_step": to_step,
            "relation": "unknown",
            "limitations": [f"missing_snapshot:{item}" for item in missing],
            "event_log_ref": EVENT_LOG_REF,
        }

    from_snapshot, from_event = from_pair
    to_snapshot, to_event = to_pair
    from_tree = from_snapshot["tree_id"]["hex"]
    to_tree = to_snapshot["tree_id"]["hex"]
    patch = _git(repo, ["diff", "--no-color", from_tree, to_tree])
    hunks = _parse_diff_hunks_with_content(patch)
    files = []
    for path, file_hunks in hunks.items():
        for hunk in file_hunks:
            files.append(
                {
                    "path": path,
                    "added_range": {
                        "start_line": hunk.get("added_start"),
                        "end_line": hunk.get("added_end"),
                    },
                    "added_text": hunk.get("added_text") or "",
                }
            )
    trace_patch_id = _id(
        "tracepatch",
        {
            "trace_id": trace_id,
            "from_snapshot_id": from_snapshot["snapshot_id"],
            "to_snapshot_id": to_snapshot["snapshot_id"],
            "patch": patch,
        },
    )
    return {
        "trace_id": trace_id,
        "from_step": from_step,
        "to_step": to_step,
        "from_snapshot_id": from_snapshot["snapshot_id"],
        "to_snapshot_id": to_snapshot["snapshot_id"],
        "from_tree_id": from_snapshot["tree_id"],
        "to_tree_id": to_snapshot["tree_id"],
        "relation": "snapshot_diff",
        "trace_patch": {
            "trace_patch_id": trace_patch_id,
            "files": files,
            "patch": patch,
            "limitations": sorted(
                set((from_snapshot.get("limitations") or []) + (to_snapshot.get("limitations") or []))
            ),
        },
        "event_log_ref": EVENT_LOG_REF,
        "source_events": [
            {
                "event_id": from_event.event_id,
                "event_sequence": from_event.event_sequence,
                "event_type": from_event.event_type,
            },
            {
                "event_id": to_event.event_id,
                "event_sequence": to_event.event_sequence,
                "event_type": to_event.event_type,
            },
        ],
    }
