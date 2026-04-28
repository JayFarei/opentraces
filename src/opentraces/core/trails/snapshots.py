"""Trace Snapshot capture and diffing for Trace Trails."""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opentraces_schema.models import TraceRecord

from ...enrichment.attribution import _norm, _parse_diff_hunks_with_content
from .event_log import append_event_batch
from .ids import (
    SNAPSHOT_CANONICALIZATION,
    TRACE_PATCH_CANONICALIZATION,
    content_ref,
    trace_patch_ref,
    trace_snapshot_ref,
)
from .models import GitObjectID, TrailEvent, TrailEventDraft, sha256_text


@dataclass(frozen=True)
class SnapshotResult:
    snapshot_id: str
    tree_id: dict[str, str]
    ref: str


@dataclass(frozen=True)
class StepWindowOpenResult:
    event_time: str
    tree_id: dict[str, str]
    git_head: dict[str, str] | None
    event_id: str


@dataclass(frozen=True)
class StepTrailEmissionResult:
    emitted_events: list[TrailEvent]
    skipped_tool_calls: int = 0


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
    kind = {
        "snapshot": "trace_snapshot",
        "tracepatch": "trace_patch",
    }.get(prefix, prefix)
    canonicalization = (
        SNAPSHOT_CANONICALIZATION
        if kind == "trace_snapshot"
        else TRACE_PATCH_CANONICALIZATION
    )
    return content_ref(
        kind=kind,
        canonicalization=canonicalization,
        material=material,
    )["id"]


def _head_id(repo: Path) -> dict[str, str] | None:
    out = _git(repo, ["rev-parse", "--verify", "HEAD"], check=False)
    if not out:
        return None
    return GitObjectID(hex=out).model_dump(mode="json")


def _is_git_worktree(repo: Path) -> bool:
    return _git(repo, ["rev-parse", "--is-inside-work-tree"], check=False) == "true"


def _object_type(repo: Path, oid_hex: str) -> str | None:
    out = _git(repo, ["cat-file", "-t", oid_hex], check=False)
    return out or None


def _git_toplevel(path: Path) -> Path | None:
    out = _git(path, ["rev-parse", "--show-toplevel"], check=False)
    if not out:
        return None
    try:
        return Path(out).resolve()
    except Exception:
        return None


def _trail_tree_id(repo: Path, hook_entry: dict[str, Any]) -> dict[str, str] | None:
    trail = hook_entry.get("trail") or {}
    tree_id = trail.get("tree_id")
    if not tree_id:
        return None
    try:
        typed = GitObjectID.model_validate(tree_id).model_dump(mode="json")
    except Exception:
        return None
    if _object_type(repo, typed["hex"]) != "tree":
        return None
    return typed


def _trail_git_head(hook_entry: dict[str, Any]) -> dict[str, str] | None:
    trail = hook_entry.get("trail") or {}
    git_head = trail.get("git_head")
    if not git_head:
        return None
    try:
        return GitObjectID.model_validate(git_head).model_dump(mode="json")
    except Exception:
        return None


def _trail_matches_repo(repo: Path, hook_entry: dict[str, Any]) -> bool:
    worktree_root = ((hook_entry.get("trail") or {}).get("worktree_root") or "").strip()
    if not worktree_root:
        return True
    try:
        hook_root = Path(worktree_root).resolve()
        repo_root = repo.resolve()
    except Exception:
        return False
    if hook_root == repo_root:
        return True
    repo_top = _git_toplevel(repo_root)
    hook_top = _git_toplevel(hook_root)
    return repo_top is not None and repo_top == hook_top


def _snapshot_id(
    *,
    trace_id: str,
    generation_index: int,
    step_index: int,
    tree_id: dict[str, str],
    role: str | None = None,
) -> str:
    material: dict[str, Any] = {
        "trace_id": trace_id,
        "generation_index": generation_index,
        "step_index": step_index,
        "tree_id": tree_id,
    }
    if role:
        material["role"] = role
    return _id("snapshot", material)


def _tree_blob_id(repo: Path, tree_id: dict[str, str], path: str) -> dict[str, str] | None:
    oid = _git(repo, ["rev-parse", f"{tree_id['hex']}:{path}"], check=False)
    if not oid:
        return None
    try:
        typed = GitObjectID(hex=oid).model_dump(mode="json")
    except Exception:
        return None
    if _object_type(repo, typed["hex"]) != "blob":
        return None
    return typed


def _patch_drafts_for_step(
    repo: Path,
    *,
    trace_id: str,
    generation_index: int,
    step_index: int,
    agent_step_id: str,
    tool_call_id: str,
    before_snapshot_id: str,
    after_snapshot_id: str,
    before_tree_id: dict[str, str],
    after_tree_id: dict[str, str],
    capture_method: list[str],
    limitations: list[str],
) -> list[TrailEventDraft]:
    patch = _git(repo, ["diff", "--no-color", before_tree_id["hex"], after_tree_id["hex"]])
    if not patch:
        return []

    hunks = _parse_diff_hunks_with_content(patch)
    drafts: list[TrailEventDraft] = []
    for file_path, file_hunks in hunks.items():
        before_blob_id = _tree_blob_id(repo, before_tree_id, file_path)
        after_blob_id = _tree_blob_id(repo, after_tree_id, file_path)
        for hunk_index, hunk in enumerate(file_hunks):
            authored_text = hunk.get("added_text") or ""
            affected_range = {
                "start_line": hunk.get("added_start"),
                "end_line": hunk.get("added_end"),
            }
            patch_limitations = list(limitations)
            if not authored_text:
                patch_limitations.append("no_added_text")
            raw_authored_hash = sha256_text(authored_text)
            git_clean_hash = sha256_text(_norm(authored_text))
            trace_patch_object_ref = content_ref(
                kind="trace_patch",
                canonicalization=TRACE_PATCH_CANONICALIZATION,
                material={
                    "trace_id": trace_id,
                    "generation_index": generation_index,
                    "step_index": step_index,
                    "snapshot_before_id": before_snapshot_id,
                    "snapshot_after_id": after_snapshot_id,
                    "file_path": file_path,
                    "hunk_index": hunk_index,
                    "affected_range": affected_range,
                    "raw_authored_hash": raw_authored_hash,
                    "before_blob_id": before_blob_id,
                    "after_blob_id": after_blob_id,
                },
            )
            trace_patch_id = trace_patch_object_ref["id"]
            drafts.append(
                TrailEventDraft(
                    event_type="trace_patch_created",
                    trace_id=trace_id,
                    generation_index=generation_index,
                    step_index=step_index,
                    capture_method=capture_method,
                    payload={
                        "trace_patch_id": trace_patch_id,
                        "trace_patch_ref": trace_patch_object_ref,
                        "snapshot_before_id": before_snapshot_id,
                        "snapshot_before_ref": trace_snapshot_ref(before_snapshot_id),
                        "snapshot_after_id": after_snapshot_id,
                        "snapshot_after_ref": trace_snapshot_ref(after_snapshot_id),
                        "agent_step_id": agent_step_id,
                        "tool_call_id": tool_call_id,
                        "file_path": file_path,
                        "affected_range": affected_range,
                        "authored_text": authored_text,
                        "raw_authored_hash": raw_authored_hash,
                        "git_clean_hash": git_clean_hash,
                        "before_blob_id": before_blob_id,
                        "after_blob_id": after_blob_id,
                        "limitations": sorted(set(patch_limitations)),
                    },
                )
            )
    return drafts


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


def _normalized_limitations(
    capture_status: str = "captured",
    limitations: list[str] | None = None,
) -> list[str]:
    out = list(limitations or [])
    if capture_status != "captured":
        out.append(capture_status)
    return sorted(set(out))


def _repo_relative_path(repo: Path, raw_path: Any) -> str | None:
    if not isinstance(raw_path, str) or not raw_path:
        return None
    try:
        path = Path(raw_path)
        if path.is_absolute():
            rel = path.resolve().relative_to(repo.resolve())
        else:
            rel = path
    except Exception:
        return None
    return str(rel).replace("\\", "/")


def _declared_write_paths(repo: Path, tool_name: str | None, tool_input: Any) -> list[str]:
    if not isinstance(tool_input, dict):
        return []
    name = (tool_name or "").lower()
    if name not in {"edit", "write", "multiedit", "notebookedit"}:
        return []
    keys = ["file_path", "path"]
    if name == "notebookedit":
        keys.append("notebook_path")
    out: list[str] = []
    for key in keys:
        rel = _repo_relative_path(repo, tool_input.get(key))
        if rel:
            out.append(rel)
    return sorted(set(out))


def _declared_command(tool_name: str | None, tool_input: Any) -> str | None:
    if (tool_name or "").lower() != "bash" or not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command") or tool_input.get("cmd")
    return command if isinstance(command, str) and command else None


def _boundary_state(
    repo: Path,
    *,
    event_time: str | None = None,
    tree_id: dict[str, str] | None = None,
    git_head: dict[str, str] | None = None,
    claimed_tree_id: dict[str, str] | None = None,
    limitations: list[str] | None = None,
) -> tuple[str, dict[str, str], dict[str, str] | None, list[str]]:
    verified_tree_id = tree_id or write_worktree_tree(repo)
    verified_git_head = git_head if git_head is not None else _head_id(repo)
    normalized = list(limitations or [])
    if claimed_tree_id and claimed_tree_id != verified_tree_id:
        normalized.append("hook_payload_state_mismatch")
    return event_time or _utc_now(), verified_tree_id, verified_git_head, sorted(set(normalized))


def _window_payload(
    repo: Path,
    *,
    trace_id: str,
    generation_index: int,
    step_index: int,
    agent_step_id: str,
    tool_call_id: str,
    tree_id: dict[str, str],
    git_head: dict[str, str] | None,
    event_time: str,
    capture_method: list[str],
    boundary_firmness: str,
    limitations: list[str],
    boundary: str,
    session_id: str | None = None,
    tool_name: str | None = None,
    declared_write_paths: list[str] | None = None,
    declared_command: str | None = None,
) -> dict[str, Any]:
    payload = {
        "trace_id": trace_id,
        "generation_index": generation_index,
        "step_index": step_index,
        "agent_step_id": agent_step_id,
        "tool_call_id": tool_call_id,
        "worktree_root": str(repo),
        "tree_id": tree_id,
        "git_head": git_head,
        "event_time": event_time,
        "capture_method": capture_method,
        "boundary_firmness": boundary_firmness,
        "capture_limitations": limitations,
        "boundary": boundary,
    }
    if tool_name:
        payload["tool_name"] = tool_name
    if session_id:
        payload["session_id"] = session_id
    if declared_write_paths:
        payload["declared_write_paths"] = list(declared_write_paths)
    if declared_command:
        payload["declared_command"] = declared_command
    return payload


def open_step_window(
    repo: Path,
    *,
    trace_id: str,
    step_index: int,
    agent_step_id: str,
    tool_call_id: str,
    capture_method: list[str],
    writer: str = "capture-claude-code",
    generation_index: int = 0,
    event_time: str | None = None,
    tree_id: dict[str, str] | None = None,
    git_head: dict[str, str] | None = None,
    limitations: list[str] | None = None,
    boundary_firmness: str = "firm",
    claimed_tree_id: dict[str, str] | None = None,
    tool_name: str | None = None,
    declared_write_paths: list[str] | None = None,
    declared_command: str | None = None,
    session_id: str | None = None,
) -> StepWindowOpenResult:
    """Append a pre-tool step-window event with verified boundary state."""
    repo = repo.resolve()
    event_time, tree_id, git_head, limitations = _boundary_state(
        repo,
        event_time=event_time,
        tree_id=tree_id,
        git_head=git_head,
        claimed_tree_id=claimed_tree_id,
        limitations=limitations,
    )
    events = append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_step_window_opened",
                trace_id=trace_id,
                generation_index=generation_index,
                step_index=step_index,
                event_time=event_time,
                capture_method=capture_method,
                payload=_window_payload(
                    repo,
                    trace_id=trace_id,
                    generation_index=generation_index,
                    step_index=step_index,
                    agent_step_id=agent_step_id,
                    tool_call_id=tool_call_id,
                    tree_id=tree_id,
                    git_head=git_head,
                    event_time=event_time,
                    capture_method=capture_method,
                    boundary_firmness=boundary_firmness,
                    limitations=limitations,
                    boundary="opened",
                    session_id=session_id,
                    tool_name=tool_name,
                    declared_write_paths=declared_write_paths,
                    declared_command=declared_command,
                ),
            )
        ],
        writer=writer,
    )
    return StepWindowOpenResult(
        event_time=event_time,
        tree_id=tree_id,
        git_head=git_head,
        event_id=events[0].event_id,
    )


def close_step_window_with_snapshot(
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
    event_time: str | None = None,
    tree_id: dict[str, str] | None = None,
    git_head: dict[str, str] | None = None,
    limitations: list[str] | None = None,
    boundary_firmness: str = "firm",
    claimed_tree_id: dict[str, str] | None = None,
    tool_name: str | None = None,
    declared_write_paths: list[str] | None = None,
    declared_command: str | None = None,
    session_id: str | None = None,
) -> SnapshotResult:
    """Append a post-tool snapshot plus the matching close-window event."""
    repo = repo.resolve()
    event_time, tree_id, git_head, boundary_limitations = _boundary_state(
        repo,
        event_time=event_time,
        tree_id=tree_id,
        git_head=git_head,
        claimed_tree_id=claimed_tree_id,
        limitations=limitations,
    )
    limitations = _normalized_limitations(capture_status, boundary_limitations)
    snapshot_id = _id(
        "snapshot",
        {
            "trace_id": trace_id,
            "generation_index": generation_index,
            "step_index": step_index,
            "tree_id": tree_id,
        },
    )
    ref = f"refs/opentraces/local/traces/{trace_id}/{generation_index}/snapshots/step_{step_index}"

    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_snapshot_created",
                trace_id=trace_id,
                generation_index=generation_index,
                step_index=step_index,
                event_time=event_time,
                capture_method=capture_method,
                payload={
                    "snapshot_id": snapshot_id,
                    "snapshot_ref": trace_snapshot_ref(snapshot_id),
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
                payload=_window_payload(
                    repo,
                    trace_id=trace_id,
                    generation_index=generation_index,
                    step_index=step_index,
                    agent_step_id=agent_step_id,
                    tool_call_id=tool_call_id,
                    tree_id=tree_id,
                    git_head=git_head,
                    event_time=event_time,
                    capture_method=capture_method,
                    boundary_firmness=boundary_firmness,
                    limitations=limitations,
                    boundary="closed",
                    session_id=session_id,
                    tool_name=tool_name,
                    declared_write_paths=declared_write_paths,
                    declared_command=declared_command,
                ),
            ),
        ],
        writer=writer,
    )
    _create_snapshot_ref(repo, ref, tree_id["hex"])
    return SnapshotResult(snapshot_id=snapshot_id, tree_id=tree_id, ref=ref)


def emit_step_window_events_from_record(
    repo: Path,
    record: TraceRecord,
    *,
    writer: str = "capture-claude-code",
) -> StepTrailEmissionResult:
    """Emit Trace Trail step-window events from parsed Claude Code hook metadata.

    The parser preserves PreToolUse/PostToolUse hook boundary facts before the
    final ``trace_id`` is known. Ingest calls this after assigning the canonical
    trace identity and generation index, turning those local facts into the
    append-only TrailEvent log.
    """
    repo = repo.resolve()
    if not _is_git_worktree(repo):
        return StepTrailEmissionResult(emitted_events=[])

    metadata = record.metadata or {}
    pre_hooks_raw = metadata.get("hook_pre_tool_use") or {}
    post_hooks_raw = metadata.get("hook_post_tool_use") or {}
    hook_stop_raw = metadata.get("hook_stop") or []
    pre_hooks = pre_hooks_raw if isinstance(pre_hooks_raw, dict) else {}
    post_hooks = post_hooks_raw if isinstance(post_hooks_raw, dict) else {}
    hook_stops = hook_stop_raw if isinstance(hook_stop_raw, list) else []
    if not pre_hooks and not post_hooks and not hook_stops:
        return StepTrailEmissionResult(emitted_events=[])

    from .event_log import read_events

    existing = read_events(repo, verify=False)
    existing_windows = {
        (
            event.event_type,
            event.trace_id,
            event.generation_index,
            event.step_index,
            event.payload.get("tool_call_id"),
        )
        for event in existing
        if event.event_type in {"trace_step_window_opened", "trace_step_window_closed"}
    }
    existing_snapshots = {
        event.payload.get("snapshot_id")
        for event in existing
        if event.event_type == "trace_snapshot_created"
    }
    existing_patches = {
        event.payload.get("trace_patch_id")
        for event in existing
        if event.event_type == "trace_patch_created"
    }
    existing_trace_events = {
        (
            event.event_type,
            event.trace_id,
            event.generation_index,
        )
        for event in existing
        if event.event_type in {"trace_session_closed", "trace_step_capture_incomplete"}
    }

    drafts: list[TrailEventDraft] = []
    snapshot_refs: list[tuple[str, str]] = []
    skipped = 0
    skipped_reasons: dict[str, int] = {}

    def mark_skipped(reason: str) -> None:
        nonlocal skipped
        skipped += 1
        skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1

    for step in record.steps:
        for tool_call in step.tool_calls:
            tool_call_id = tool_call.tool_call_id
            pre = pre_hooks.get(tool_call_id)
            post = post_hooks.get(tool_call_id)
            if not isinstance(pre, dict) or not isinstance(post, dict):
                mark_skipped("missing_pre_or_post_hook")
                continue
            if not _trail_matches_repo(repo, pre) or not _trail_matches_repo(repo, post):
                mark_skipped("worktree_root_mismatch")
                continue

            pre_tree_id = _trail_tree_id(repo, pre)
            post_tree_id = _trail_tree_id(repo, post)
            if not pre_tree_id or not post_tree_id:
                mark_skipped("missing_valid_tree_id")
                continue

            pre_git_head = _trail_git_head(pre)
            post_git_head = _trail_git_head(post)
            tool_name = tool_call.tool_name or pre.get("tool") or post.get("tool")
            tool_input = (
                pre.get("tool_input")
                if isinstance(pre.get("tool_input"), dict)
                else tool_call.input
            )
            declared_paths = _declared_write_paths(repo, tool_name, tool_input)
            declared_command = _declared_command(tool_name, tool_input)
            generation_index = record.generation_index
            agent_step_id = f"step_{step.step_index}"
            before_snapshot_id = _snapshot_id(
                trace_id=record.trace_id,
                generation_index=generation_index,
                step_index=step.step_index,
                tree_id=pre_tree_id,
                role="before",
            )
            after_snapshot_id = _snapshot_id(
                trace_id=record.trace_id,
                generation_index=generation_index,
                step_index=step.step_index,
                tree_id=post_tree_id,
            )

            open_key = (
                "trace_step_window_opened",
                record.trace_id,
                generation_index,
                step.step_index,
                tool_call_id,
            )
            if open_key not in existing_windows:
                existing_windows.add(open_key)
                drafts.append(
                    TrailEventDraft(
                        event_type="trace_step_window_opened",
                        trace_id=record.trace_id,
                        generation_index=generation_index,
                        step_index=step.step_index,
                        event_time=pre.get("timestamp"),
                        capture_method=["hook_pretooluse"],
                        payload=_window_payload(
                            repo,
                            trace_id=record.trace_id,
                            generation_index=generation_index,
                            step_index=step.step_index,
                            agent_step_id=agent_step_id,
                            tool_call_id=tool_call_id,
                            tree_id=pre_tree_id,
                            git_head=pre_git_head,
                            event_time=pre.get("timestamp") or _utc_now(),
                            capture_method=["hook_pretooluse"],
                            boundary_firmness="firm",
                            limitations=[],
                            boundary="opened",
                            session_id=record.session_id,
                            tool_name=tool_name,
                            declared_write_paths=declared_paths,
                            declared_command=declared_command,
                        ),
                    )
                )

            capture_status = post.get("capture_status") or "captured"
            limitations = _normalized_limitations(
                capture_status,
                post.get("limitations") if isinstance(post.get("limitations"), list) else None,
            )
            if before_snapshot_id not in existing_snapshots:
                existing_snapshots.add(before_snapshot_id)
                drafts.append(
                    TrailEventDraft(
                        event_type="trace_snapshot_created",
                        trace_id=record.trace_id,
                        generation_index=generation_index,
                        step_index=step.step_index,
                        event_time=pre.get("timestamp"),
                        capture_method=["hook_pretooluse"],
                        payload={
                            "snapshot_id": before_snapshot_id,
                            "snapshot_ref": trace_snapshot_ref(before_snapshot_id),
                            "snapshot_role": "before",
                            "agent_step_id": agent_step_id,
                            "tool_call_id": tool_call_id,
                            "tree_id": pre_tree_id,
                            "git_head": pre_git_head,
                            "base_commit": pre_git_head,
                            "capture_status": "captured",
                            "limitations": [],
                        },
                    )
                )
            ref = (
                f"refs/opentraces/local/traces/{record.trace_id}/{generation_index}"
                f"/snapshots/step_{step.step_index}"
            )
            if after_snapshot_id not in existing_snapshots:
                existing_snapshots.add(after_snapshot_id)
                drafts.append(
                    TrailEventDraft(
                        event_type="trace_snapshot_created",
                        trace_id=record.trace_id,
                        generation_index=generation_index,
                        step_index=step.step_index,
                        event_time=post.get("timestamp"),
                        capture_method=["hook_posttooluse"],
                        payload={
                            "snapshot_id": after_snapshot_id,
                            "snapshot_ref": trace_snapshot_ref(after_snapshot_id),
                            "snapshot_role": "after",
                            "agent_step_id": agent_step_id,
                            "tool_call_id": tool_call_id,
                            "tree_id": post_tree_id,
                            "git_head": post_git_head,
                            "base_commit": post_git_head,
                            "capture_status": capture_status,
                            "limitations": limitations,
                        },
                    )
                )
                snapshot_refs.append((ref, post_tree_id["hex"]))

            close_key = (
                "trace_step_window_closed",
                record.trace_id,
                generation_index,
                step.step_index,
                tool_call_id,
            )
            if close_key not in existing_windows:
                existing_windows.add(close_key)
                drafts.append(
                    TrailEventDraft(
                        event_type="trace_step_window_closed",
                        trace_id=record.trace_id,
                        generation_index=generation_index,
                        step_index=step.step_index,
                        event_time=post.get("timestamp"),
                        capture_method=["hook_posttooluse"],
                        payload=_window_payload(
                            repo,
                            trace_id=record.trace_id,
                            generation_index=generation_index,
                            step_index=step.step_index,
                            agent_step_id=agent_step_id,
                            tool_call_id=tool_call_id,
                            tree_id=post_tree_id,
                            git_head=post_git_head,
                            event_time=post.get("timestamp") or _utc_now(),
                            capture_method=["hook_posttooluse"],
                            boundary_firmness="firm",
                            limitations=limitations,
                            boundary="closed",
                            session_id=record.session_id,
                            tool_name=tool_name,
                            declared_write_paths=declared_paths,
                            declared_command=declared_command,
                        ),
                    )
                )

            patch_drafts = _patch_drafts_for_step(
                repo,
                trace_id=record.trace_id,
                generation_index=generation_index,
                step_index=step.step_index,
                agent_step_id=agent_step_id,
                tool_call_id=tool_call_id,
                before_snapshot_id=before_snapshot_id,
                after_snapshot_id=after_snapshot_id,
                before_tree_id=pre_tree_id,
                after_tree_id=post_tree_id,
                capture_method=["hook_pretooluse", "hook_posttooluse"],
                limitations=limitations,
            )
            for draft in patch_drafts:
                trace_patch_id = draft.payload.get("trace_patch_id")
                if trace_patch_id in existing_patches:
                    continue
                existing_patches.add(trace_patch_id)
                drafts.append(draft)

    generation_index = record.generation_index
    stop_event_key = ("trace_session_closed", record.trace_id, generation_index)
    stop_event = next((item for item in hook_stops if isinstance(item, dict)), None)
    if stop_event and stop_event_key not in existing_trace_events:
        trail = stop_event.get("trail") if isinstance(stop_event.get("trail"), dict) else {}
        stop_tree_id = _trail_tree_id(repo, stop_event)
        stop_git_head = _trail_git_head(stop_event)
        payload: dict[str, Any] = {
            "trace_id": record.trace_id,
            "generation_index": generation_index,
            "session_id": record.session_id,
            "event_time": stop_event.get("timestamp"),
            "worktree_root": trail.get("worktree_root") or str(repo),
            "tree_id": stop_tree_id,
            "git_head": stop_git_head,
            "git": stop_event.get("git") or {},
            "agent_type": stop_event.get("agent_type"),
            "permission_mode": stop_event.get("permission_mode"),
            "stop_hook_active": stop_event.get("stop_hook_active"),
            "capture_limitations": [],
        }
        drafts.append(
            TrailEventDraft(
                event_type="trace_session_closed",
                trace_id=record.trace_id,
                generation_index=generation_index,
                step_index=None,
                event_time=stop_event.get("timestamp"),
                capture_method=["hook_stop"],
                payload={key: value for key, value in payload.items() if value is not None},
            )
        )
        existing_trace_events.add(stop_event_key)

    incomplete_key = ("trace_step_capture_incomplete", record.trace_id, generation_index)
    if skipped and incomplete_key not in existing_trace_events:
        drafts.append(
            TrailEventDraft(
                event_type="trace_step_capture_incomplete",
                trace_id=record.trace_id,
                generation_index=generation_index,
                step_index=None,
                capture_method=["hook_stop"] if hook_stops else ["hook_pretooluse"],
                payload={
                    "trace_id": record.trace_id,
                    "generation_index": generation_index,
                    "session_id": record.session_id,
                    "total_tool_calls": sum(len(step.tool_calls) for step in record.steps),
                    "skipped_tool_calls": skipped,
                    "reasons": skipped_reasons,
                    "capture_limitations": ["incomplete_step_window_capture"],
                },
            )
        )
        existing_trace_events.add(incomplete_key)

    emitted = append_event_batch(repo, drafts, writer=writer)
    for ref, tree_hex in snapshot_refs:
        _create_snapshot_ref(repo, ref, tree_hex)
    return StepTrailEmissionResult(emitted_events=emitted, skipped_tool_calls=skipped)


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
    opened_event_time: str | None = None,
    opened_tree_id: dict[str, str] | None = None,
    opened_git_head: dict[str, str] | None = None,
    opened_capture_method: list[str] | None = None,
    opened_limitations: list[str] | None = None,
) -> SnapshotResult:
    """Compatibility helper for callers that still close from one boundary.

    New hook integrations should call ``open_step_window`` at PreToolUse and
    ``close_step_window_with_snapshot`` at PostToolUse so the wall-clock
    interval and pre/post tree IDs reflect the real tool execution.
    """
    open_step_window(
        repo,
        trace_id=trace_id,
        step_index=step_index,
        agent_step_id=agent_step_id,
        tool_call_id=tool_call_id,
        capture_method=opened_capture_method or capture_method,
        writer=writer,
        generation_index=generation_index,
        event_time=opened_event_time,
        tree_id=opened_tree_id,
        git_head=opened_git_head,
        limitations=opened_limitations,
        boundary_firmness=boundary_firmness,
    )
    return close_step_window_with_snapshot(
        repo,
        trace_id=trace_id,
        step_index=step_index,
        agent_step_id=agent_step_id,
        tool_call_id=tool_call_id,
        capture_method=capture_method,
        writer=writer,
        generation_index=generation_index,
        capture_status=capture_status,
        limitations=limitations,
        boundary_firmness=boundary_firmness,
        claimed_tree_id=claimed_tree_id,
    )


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
    trace_patch_object_ref = content_ref(
        kind="trace_patch",
        canonicalization=TRACE_PATCH_CANONICALIZATION,
        material={
            "trace_id": trace_id,
            "from_snapshot_id": from_snapshot["snapshot_id"],
            "to_snapshot_id": to_snapshot["snapshot_id"],
            "patch": patch,
        },
    )
    trace_patch_id = trace_patch_object_ref["id"]
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
            "trace_patch_ref": trace_patch_ref(trace_patch_id),
            "files": files,
            "patch": patch,
            "limitations": sorted(
                set(
                    (from_snapshot.get("limitations") or [])
                    + (to_snapshot.get("limitations") or [])
                )
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
