"""Append-only Git event log for Trace Trails."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    GitObjectID,
    TrailEvent,
    TrailEventDraft,
    expected_event_id,
    finalize_event,
    payload_content_hash,
)

EVENT_LOG_REF = "refs/opentraces/local/events/v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git(
    cwd: Path,
    args: list[str],
    *,
    input: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def _ref_head(cwd: Path) -> str | None:
    proc = _git(cwd, ["rev-parse", "--verify", EVENT_LOG_REF], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _hash_blob(cwd: Path, text: str) -> str:
    return _git(cwd, ["hash-object", "-w", "--stdin"], input=text).stdout.strip()


def _object_type(cwd: Path, oid: GitObjectID) -> str | None:
    proc = _git(cwd, ["cat-file", "-t", oid.hex], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _collect_object_ids(value: Any) -> list[GitObjectID]:
    found: list[GitObjectID] = []
    if isinstance(value, dict):
        if set(value.keys()) >= {"algo", "hex"}:
            try:
                found.append(GitObjectID.model_validate(value))
            except Exception:
                pass
        for child in value.values():
            found.extend(_collect_object_ids(child))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_object_ids(item))
    return found


def _safe_tree_entry_name(value: str) -> str:
    return value.replace(":", "-").replace("/", "-")


def _tree_oid_from_payload(cwd: Path, event: TrailEvent) -> GitObjectID | None:
    tree_id = event.payload.get("tree_id")
    if not tree_id:
        return None
    try:
        oid = GitObjectID.model_validate(tree_id)
    except Exception:
        return None
    if _object_type(cwd, oid) != "tree":
        return None
    return oid


def _write_batch_tree(cwd: Path, events: list[TrailEvent], batch: dict[str, Any]) -> str:
    snapshot_tree_entries: list[tuple[str, str]] = []
    boundary_tree_entries: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="opentraces-trails-index-") as td:
        index_path = str(Path(td) / "index")
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = index_path
        _git(cwd, ["read-tree", "--empty"], env=env)

        batch_blob = _hash_blob(cwd, json.dumps(batch, sort_keys=True, indent=2) + "\n")
        _git(
            cwd,
            ["update-index", "--add", "--cacheinfo", f"100644,{batch_blob},batch.json"],
            env=env,
        )

        retained_blobs: set[str] = set()
        retained_trees: set[str] = set()
        for event in events:
            payload = event.model_dump(mode="json")
            event_blob = _hash_blob(cwd, json.dumps(payload, sort_keys=True, indent=2) + "\n")
            _git(
                cwd,
                [
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"100644,{event_blob},events/{event.event_sequence:012d}.json",
                ],
                env=env,
            )
            for oid in _collect_object_ids(event.payload):
                if oid.hex in retained_blobs:
                    continue
                if _object_type(cwd, oid) != "blob":
                    continue
                retained_blobs.add(oid.hex)
                _git(
                    cwd,
                    [
                        "update-index",
                        "--add",
                        "--cacheinfo",
                        f"100644,{oid.hex},objects/blobs/{oid.hex}",
                    ],
                    env=env,
                )
            snapshot_id = event.payload.get("snapshot_id")
            oid = _tree_oid_from_payload(cwd, event)
            if oid:
                safe_event_id = _safe_tree_entry_name(event.event_id)
                boundary_tree_entries.append(
                    (f"{event.event_sequence:012d}-{safe_event_id}", oid.hex)
                )
            if event.event_type == "trace_snapshot_created" and oid and snapshot_id:
                if oid.hex not in retained_trees:
                    retained_trees.add(oid.hex)
                    safe_snapshot_id = _safe_tree_entry_name(str(snapshot_id))
                    snapshot_tree_entries.append((safe_snapshot_id, oid.hex))

        base_tree = _git(cwd, ["write-tree"], env=env).stdout.strip()

    if not snapshot_tree_entries and not boundary_tree_entries:
        return base_tree

    root_entries = _git(cwd, ["ls-tree", base_tree]).stdout
    if snapshot_tree_entries:
        snapshots_tree_input = "".join(
            f"040000 tree {tree_hex}\t{name}\n"
            for name, tree_hex in sorted(snapshot_tree_entries)
        )
        snapshots_tree = _git(cwd, ["mktree"], input=snapshots_tree_input).stdout.strip()
        root_entries += f"040000 tree {snapshots_tree}\tsnapshots\n"
    if boundary_tree_entries:
        trees_tree_input = "".join(
            f"040000 tree {tree_hex}\t{name}\n"
            for name, tree_hex in sorted(boundary_tree_entries)
        )
        trees_tree = _git(cwd, ["mktree"], input=trees_tree_input).stdout.strip()
        root_entries += f"040000 tree {trees_tree}\ttrees\n"
    return _git(cwd, ["mktree"], input=root_entries).stdout.strip()


def _commit_batch(cwd: Path, tree_sha: str, head: str | None, batch_id: str) -> str:
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "opentraces")
    env.setdefault("GIT_AUTHOR_EMAIL", "opentraces@local")
    env.setdefault("GIT_COMMITTER_NAME", "opentraces")
    env.setdefault("GIT_COMMITTER_EMAIL", "opentraces@local")
    args = ["commit-tree", tree_sha, "-m", f"opentraces trail event batch {batch_id}"]
    if head:
        args[2:2] = ["-p", head]
    return _git(cwd, args, env=env).stdout.strip()


def append_event_batch(
    cwd: Path,
    drafts: list[TrailEventDraft | dict[str, Any]],
    *,
    writer: str,
) -> list[TrailEvent]:
    """Append a linear batch of TrailEvents to the local Git event log."""
    cwd = cwd.resolve()
    if not drafts:
        return []
    head = _ref_head(cwd)
    existing = read_events(cwd, verify=False)
    next_sequence = max((event.event_sequence for event in existing), default=0) + 1
    previous_event_id = existing[-1].event_id if existing else None
    batch_id = f"batch-{uuid.uuid4().hex}"

    events: list[TrailEvent] = []
    for raw in drafts:
        draft = raw if isinstance(raw, TrailEventDraft) else TrailEventDraft.model_validate(raw)
        data = {
            "event_sequence": next_sequence,
            "event_time": draft.event_time or _utc_now(),
            "previous_event_id": previous_event_id,
            "trace_id": draft.trace_id,
            "generation_index": draft.generation_index,
            "step_index": draft.step_index,
            "batch_id": batch_id,
            "writer": writer,
            "capture_method": draft.capture_method,
            "event_type": draft.event_type,
            "payload": draft.payload,
            "SCHEMA_VERSION": None,
            "SECURITY_VERSION": None,
            "ATTRIBUTION_VERSION": None,
        }
        # Let the model defaults set version fields by omitting explicit None.
        data = {k: v for k, v in data.items() if v is not None}
        event = finalize_event(data)
        events.append(event)
        next_sequence += 1
        previous_event_id = event.event_id

    batch = {
        "batch_id": batch_id,
        "writer": writer,
        "previous_event_log_head": head,
        "event_count": len(events),
    }
    tree_sha = _write_batch_tree(cwd, events, batch)
    commit_sha = _commit_batch(cwd, tree_sha, head, batch_id)

    if head:
        _git(cwd, ["update-ref", EVENT_LOG_REF, commit_sha, head])
    else:
        _git(cwd, ["update-ref", EVENT_LOG_REF, commit_sha])
    return events


def read_events(cwd: Path, *, verify: bool = True) -> list[TrailEvent]:
    head = _ref_head(cwd)
    if head is None:
        return []
    commits = _git(cwd, ["rev-list", "--reverse", EVENT_LOG_REF]).stdout.splitlines()
    events: list[TrailEvent] = []
    for commit in commits:
        names = _git(cwd, ["ls-tree", "-r", "--name-only", commit, "events"]).stdout.splitlines()
        for name in sorted(n for n in names if n.startswith("events/") and n.endswith(".json")):
            raw = _git(cwd, ["show", f"{commit}:{name}"]).stdout
            events.append(TrailEvent.model_validate_json(raw))
    events.sort(key=lambda event: event.event_sequence)
    if verify:
        errors = verify_event_log(cwd, events=events)["errors"]
        if errors:
            raise ValueError("; ".join(errors))
    return events


def _parents_are_linear(cwd: Path) -> tuple[bool, list[str], int]:
    if _ref_head(cwd) is None:
        return False, [], 0
    lines = _git(cwd, ["rev-list", "--reverse", "--parents", EVENT_LOG_REF]).stdout.splitlines()
    previous: str | None = None
    errors: list[str] = []
    for index, line in enumerate(lines):
        parts = line.split()
        commit, parents = parts[0], parts[1:]
        if index == 0:
            if len(parents) > 1:
                errors.append(f"{commit[:12]} has multiple parents")
        elif parents != [previous]:
            errors.append(f"{commit[:12]} does not parent previous batch {previous[:12] if previous else '?'}")
        previous = commit
    return not errors, errors, len(lines)


def verify_event_log(
    cwd: Path,
    *,
    events: list[TrailEvent] | None = None,
) -> dict[str, Any]:
    """Verify parent linearity and TrailEvent content addresses."""
    errors: list[str] = []
    head = _ref_head(cwd)
    if head is None:
        return {
            "ref": EVENT_LOG_REF,
            "exists": False,
            "head": None,
            "batch_count": 0,
            "event_count": 0,
            "batch_parents_linear": False,
            "content_hashes_valid": False,
            "event_chain_valid": False,
            "errors": [],
        }

    linear, parent_errors, batch_count = _parents_are_linear(cwd)
    errors.extend(parent_errors)

    if events is None:
        events = []
        commits = _git(cwd, ["rev-list", "--reverse", EVENT_LOG_REF]).stdout.splitlines()
        for commit in commits:
            names = _git(cwd, ["ls-tree", "-r", "--name-only", commit, "events"]).stdout.splitlines()
            for name in sorted(n for n in names if n.startswith("events/") and n.endswith(".json")):
                raw = _git(cwd, ["show", f"{commit}:{name}"]).stdout
                try:
                    events.append(TrailEvent.model_validate_json(raw))
                except Exception as exc:
                    errors.append(f"{name}: invalid event JSON: {exc}")

    content_ok = True
    chain_ok = True
    previous_event_id: str | None = None
    for expected_sequence, event in enumerate(sorted(events, key=lambda e: e.event_sequence), start=1):
        expected_hash = payload_content_hash(event.payload)
        if event.content_hash != expected_hash:
            content_ok = False
            errors.append(f"event {event.event_sequence}: content_hash mismatch")
        if event.event_id != expected_event_id(event):
            content_ok = False
            errors.append(f"event {event.event_sequence}: event_id mismatch")
        if event.event_sequence != expected_sequence:
            chain_ok = False
            errors.append(f"event {event.event_sequence}: non-contiguous event_sequence")
        if event.previous_event_id != previous_event_id:
            chain_ok = False
            errors.append(f"event {event.event_sequence}: previous_event_id mismatch")
        previous_event_id = event.event_id

    return {
        "ref": EVENT_LOG_REF,
        "exists": True,
        "head": head,
        "batch_count": batch_count,
        "event_count": len(events),
        "batch_parents_linear": linear,
        "content_hashes_valid": content_ok,
        "event_chain_valid": chain_ok,
        "errors": errors,
    }


def event_log_status(cwd: Path) -> dict[str, Any]:
    status = verify_event_log(cwd)
    if not status["exists"]:
        status["state"] = "missing"
    elif status["errors"]:
        status["state"] = "invalid"
    else:
        status["state"] = "ok"
    return status
