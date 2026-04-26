"""Patch Trail survival observations for Trace Trails."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .event_log import EVENT_LOG_REF, read_events
from .models import GitObjectID

PHASE4_SURVIVAL_STATES = [
    "alive_on_path",
    "alive_transformed",
    "reverted",
    "lost",
    "unknown",
]
RESERVED_PHASE5_SURVIVAL_STATES = [
    "alive_moved",
    "partially_preserved",
    "repaired",
    "orphaned",
]
REVERT_SEARCH_LIMIT = 2000
PATCH_TRAIL_COMMIT_LIMIT = 500
OBSERVATION_SCOPE = "anchor_to_head"
SURVIVAL_PRECEDENCE = {
    "alive_on_path": 50,
    "alive_transformed": 40,
    "reverted": 30,
    "lost": 20,
    "unknown": 10,
}


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _git_ok(repo: Path, *args: str) -> bool:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def _oid(hex_value: str | None) -> dict[str, str] | None:
    if not hex_value:
        return None
    try:
        return GitObjectID(hex=hex_value.strip()).model_dump(mode="json")
    except Exception:
        return None


def _source_event(event) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_sequence": event.event_sequence,
        "event_type": event.event_type,
        "capture_method": event.capture_method,
    }


def _head_id(repo: Path) -> dict[str, str] | None:
    return _oid(_git(repo, "rev-parse", "HEAD", check=False).strip())


def _commit_id(repo: Path, ref: str) -> dict[str, str] | None:
    return _oid(_git(repo, "rev-parse", ref, check=False).strip())


def _show_file(repo: Path, ref: str, path: str) -> str | None:
    out = _git(repo, "show", f"{ref}:{path}", check=False)
    return out if out else None


def _find_revert_commit(
    repo: Path,
    *,
    anchor_commit: str,
    observed_ref: str,
) -> tuple[str | None, bool]:
    out = _git(
        repo,
        "log",
        f"--max-count={REVERT_SEARCH_LIMIT + 1}",
        "--format=%H%x00%B%x00%x00",
        f"{anchor_commit}..{observed_ref}",
        check=False,
    )
    if not out:
        return None, False
    records = [record for record in out.split("\x00\x00") if record.strip()]
    truncated = len(records) > REVERT_SEARCH_LIMIT
    for record in records[:REVERT_SEARCH_LIMIT]:
        commit, _, body = record.partition("\x00")
        if f"This reverts commit {anchor_commit}" in body:
            return commit.strip(), truncated
    return None, truncated


def _authored_lines(patch: dict[str, Any]) -> list[str]:
    authored = patch.get("authored_text")
    if not isinstance(authored, str) or not authored:
        return []
    return authored.splitlines()


def _compute_survival(
    repo: Path,
    *,
    patch: dict[str, Any],
    anchor: dict[str, Any],
    observed_ref: str = "HEAD",
) -> dict[str, Any]:
    observed_commit_id = _commit_id(repo, observed_ref)
    head_id = _head_id(repo)
    commit_id = anchor.get("commit_id") or {}
    anchor_commit = commit_id.get("hex")
    path = anchor.get("path") or patch.get("file_path")
    anchor_range = anchor.get("range") or {}
    limitations: list[str] = []

    base = {
        "observation_type": "patch_survival_observed",
        "git_anchor_id": anchor.get("git_anchor_id"),
        "trace_patch_id": anchor.get("trace_patch_id") or patch.get("trace_patch_id"),
        "anchor_commit_id": commit_id or None,
        "observed_ref": observed_ref,
        "observed_commit_id": observed_commit_id,
        "path": path,
        "range": anchor_range or None,
        "evidence_tier": anchor.get("evidence_tier") or "unknown",
        "evidence_firmness": anchor.get("evidence_firmness") or "unknown",
        "limitations": limitations,
    }
    if head_id is not None and observed_commit_id == head_id:
        base["observed_head_id"] = head_id

    if not anchor_commit or not path or observed_commit_id is None:
        limitations.append("missing_git_survival_inputs")
        return {**base, "survival_state": "unknown"}
    if not _git_ok(repo, "merge-base", "--is-ancestor", anchor_commit, observed_ref):
        limitations.append(
            "anchor_commit_not_reachable_from_head"
            if observed_ref == "HEAD"
            else "anchor_commit_not_reachable_from_observed_ref"
        )
        return {**base, "survival_state": "unknown"}

    revert_commit, revert_search_truncated = _find_revert_commit(
        repo,
        anchor_commit=anchor_commit,
        observed_ref=observed_ref,
    )
    if revert_commit:
        return {
            **base,
            "survival_state": "reverted",
            "revert_commit_id": _oid(revert_commit),
        }
    if revert_search_truncated:
        limitations.append("revert_search_truncated")

    authored_lines = _authored_lines(patch)
    if not authored_lines:
        limitations.append("missing_authored_text")
        return {**base, "survival_state": "unknown"}

    current_text = _show_file(repo, observed_ref, path)
    if current_text is None:
        return {**base, "survival_state": "lost"}

    current_lines = current_text.splitlines()
    start = anchor_range.get("start_line")
    end = anchor_range.get("end_line")
    if isinstance(start, int) and isinstance(end, int) and start >= 1 and end >= start:
        current_range = current_lines[start - 1:end]
        if current_range == authored_lines:
            return {**base, "survival_state": "alive_on_path"}
        if len(current_lines) >= start:
            return {**base, "survival_state": "alive_transformed"}

    return {**base, "survival_state": "lost"}


def _aggregate_current_survival(
    indexed_observations: list[tuple[int, dict[str, Any]]],
) -> dict[str, Any]:
    if not indexed_observations:
        return {"survival_state": "unknown"}
    best_index, best = max(
        indexed_observations,
        key=lambda item: (
            SURVIVAL_PRECEDENCE.get(item[1].get("survival_state"), 0),
            item[0],
        ),
    )
    current = dict(best)
    current["aggregation"] = "any_alive_anchor_wins"
    current["selected_observation_index"] = best_index
    return current


def _commits_from_anchor_to_head(
    repo: Path,
    anchor_commit: str,
) -> tuple[list[str], list[str]]:
    """Return a bounded chronological ancestry path including anchor and HEAD."""
    if not _git_ok(repo, "merge-base", "--is-ancestor", anchor_commit, "HEAD"):
        return ["HEAD"], []

    total_out = _git(
        repo,
        "rev-list",
        "--count",
        "--ancestry-path",
        f"{anchor_commit}..HEAD",
        check=False,
    ).strip()
    try:
        descendant_count = int(total_out or "0")
    except ValueError:
        descendant_count = 0

    if descendant_count == 0:
        return [anchor_commit], []

    head = _git(repo, "rev-parse", "HEAD", check=False).strip()
    limit = max(PATCH_TRAIL_COMMIT_LIMIT, 2)
    if descendant_count <= limit - 1:
        descendants = _git(
            repo,
            "rev-list",
            "--reverse",
            "--ancestry-path",
            f"{anchor_commit}..HEAD",
            check=False,
        ).splitlines()
        return [anchor_commit] + descendants, []

    keep_oldest = max(limit - 2, 0)
    oldest_descendants: list[str] = []
    if keep_oldest:
        newest_order_oldest_subset = _git(
            repo,
            "rev-list",
            "--ancestry-path",
            f"--skip={descendant_count - keep_oldest}",
            f"--max-count={keep_oldest}",
            f"{anchor_commit}..HEAD",
            check=False,
        ).splitlines()
        oldest_descendants = list(reversed(newest_order_oldest_subset))
    return [anchor_commit] + oldest_descendants + [head], ["patch_trail_history_truncated"]


def _anchor_observations(
    repo: Path,
    *,
    patch: dict[str, Any],
    anchor: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    commit_id = anchor.get("commit_id") or {}
    anchor_commit = commit_id.get("hex")
    if not anchor_commit:
        return [_compute_survival(repo, patch=patch, anchor=anchor)], []

    commits, limitations = _commits_from_anchor_to_head(repo, anchor_commit)
    observations = [
        _compute_survival(repo, patch=patch, anchor=anchor, observed_ref=commit)
        for commit in commits
    ]
    for index, observation in enumerate(observations):
        observation["trail_index"] = index
    return observations, limitations


def _follow(
    repo: Path,
    *,
    trace_patch_id: str | None = None,
    git_anchor_id: str | None = None,
) -> dict[str, Any]:
    events = read_events(repo)
    patches: dict[str, tuple[dict[str, Any], Any]] = {}
    anchors: list[tuple[dict[str, Any], Any]] = []
    for event in events:
        if event.event_type == "trace_patch_created":
            patch_id = event.payload.get("trace_patch_id")
            if patch_id:
                patches[patch_id] = (event.payload, event)
        elif event.event_type == "git_anchor_created":
            anchors.append((event.payload, event))

    if git_anchor_id:
        anchors = [
            (anchor, event)
            for anchor, event in anchors
            if anchor.get("git_anchor_id") == git_anchor_id
        ]
        if anchors and trace_patch_id is None:
            trace_patch_id = anchors[0][0].get("trace_patch_id")
    elif trace_patch_id:
        anchors = [
            (anchor, event)
            for anchor, event in anchors
            if anchor.get("trace_patch_id") == trace_patch_id
        ]

    patch_pair = patches.get(trace_patch_id or "")
    if patch_pair is None:
        return {
            "trace_patch_id": trace_patch_id,
            "git_anchor_id": git_anchor_id,
            "relation": "unknown",
            "current_survival": {"survival_state": "unknown"},
            "current_observations": [],
            "observations": [],
            "observation_scope": OBSERVATION_SCOPE,
            "history_limit": PATCH_TRAIL_COMMIT_LIMIT,
            "limitations": ["no_trace_patch_event"],
            "event_log_ref": EVENT_LOG_REF,
            "phase4_survival_states": PHASE4_SURVIVAL_STATES,
            "reserved_survival_states": RESERVED_PHASE5_SURVIVAL_STATES,
            "source_events": [],
        }

    patch, patch_event = patch_pair
    if not anchors:
        return {
            "trace_patch_id": patch.get("trace_patch_id"),
            "git_anchor_id": git_anchor_id,
            "relation": "unknown",
            "current_survival": {"survival_state": "unknown"},
            "current_observations": [],
            "observations": [],
            "observation_scope": OBSERVATION_SCOPE,
            "history_limit": PATCH_TRAIL_COMMIT_LIMIT,
            "limitations": ["no_git_anchor_event"],
            "event_log_ref": EVENT_LOG_REF,
            "phase4_survival_states": PHASE4_SURVIVAL_STATES,
            "reserved_survival_states": RESERVED_PHASE5_SURVIVAL_STATES,
            "source_events": [_source_event(patch_event)],
        }

    observations: list[dict[str, Any]] = []
    current_observations: list[tuple[int, dict[str, Any]]] = []
    limitations: list[str] = []
    sorted_anchors = sorted(anchors, key=lambda item: item[1].event_sequence)
    for anchor, event in sorted_anchors:
        anchor_observations, anchor_limitations = _anchor_observations(
            repo,
            patch=patch,
            anchor=anchor,
        )
        limitations.extend(anchor_limitations)
        for observation in anchor_observations:
            observation["anchor_event_sequence"] = event.event_sequence
        start_index = len(observations)
        observations.extend(anchor_observations)
        if anchor_observations:
            current_observations.append(
                (start_index + len(anchor_observations) - 1, anchor_observations[-1])
            )

    unique_limitations = list(dict.fromkeys(limitations))
    current_observation_values = [observation for _index, observation in current_observations]
    return {
        "trace_patch_id": patch.get("trace_patch_id"),
        "git_anchor_id": git_anchor_id,
        "relation": "patch_trail_observed",
        "current_survival": (
            current_observation_values[-1]
            if git_anchor_id and current_observation_values
            else _aggregate_current_survival(current_observations)
        ),
        "current_observations": current_observation_values,
        "observations": observations,
        "observation_scope": OBSERVATION_SCOPE,
        "history_limit": PATCH_TRAIL_COMMIT_LIMIT,
        "limitations": unique_limitations,
        "event_log_ref": EVENT_LOG_REF,
        "phase4_survival_states": PHASE4_SURVIVAL_STATES,
        "reserved_survival_states": RESERVED_PHASE5_SURVIVAL_STATES,
        "source_events": [_source_event(patch_event)]
        + [_source_event(event) for _anchor, event in sorted_anchors],
    }


def follow_patch(repo: Path, trace_patch_id: str) -> dict[str, Any]:
    """Follow survival for all Git Anchors attached to a Trace Patch."""
    return _follow(repo, trace_patch_id=trace_patch_id)


def follow_anchor(repo: Path, git_anchor_id: str) -> dict[str, Any]:
    """Follow survival for one Git Anchor."""
    return _follow(repo, git_anchor_id=git_anchor_id)
