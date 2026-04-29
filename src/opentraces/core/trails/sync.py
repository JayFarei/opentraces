"""Patch Trail survival observations for Trace Trails."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .event_log import EVENT_LOG_REF, read_events
from .ids import id_from_payload, normalize_id
from .models import GitObjectID

PHASE4_SURVIVAL_STATES = [
    "alive_on_path",
    "alive_transformed",
    "reverted",
    "lost",
    "unknown",
]
PHASE5_SURVIVAL_STATES = [
    "alive_moved",
    "partially_preserved",
    "repaired",
]
ALL_SURVIVAL_STATES = PHASE4_SURVIVAL_STATES + PHASE5_SURVIVAL_STATES + ["orphaned"]
# Phase 5 reserves "orphaned" for reference-transaction observation
# (deferred beyond Phase 5 — see plan §Phase 5 line 248).
RESERVED_PHASE5_SURVIVAL_STATES = ["orphaned"]
REVERT_SEARCH_LIMIT = 2000
PATCH_TRAIL_COMMIT_LIMIT = 500
OBSERVATION_SCOPE = "anchor_to_head"
SURVIVAL_PRECEDENCE = {
    "alive_on_path": 50,
    "alive_moved": 45,
    "alive_transformed": 40,
    "partially_preserved": 35,
    "repaired": 32,
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
    return (
        subprocess.run(
            ["git", *args],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


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


def _commit_time(repo: Path, ref: str) -> int | None:
    out = _git(repo, "log", "-1", "--format=%ct", ref, check=False).strip()
    if not out:
        return None
    try:
        return int(out)
    except ValueError:
        return None


def _show_file(repo: Path, ref: str, path: str) -> str | None:
    out = _git(repo, "show", f"{ref}:{path}", check=False)
    return out if out else None


def _parse_log_line(line: str) -> tuple[str, int | None]:
    parts = line.strip().split(maxsplit=1)
    if not parts or not parts[0]:
        return ("", None)
    sha = parts[0]
    if len(parts) == 2:
        try:
            return (sha, int(parts[1]))
        except ValueError:
            return (sha, None)
    return (sha, None)


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


def _track_rename_path(
    repo: Path, *, anchor_commit: str, observed_ref: str, original_path: str
) -> tuple[str, int]:
    """Walk forward through ``git log --name-status -M`` to track renames.

    Returns ``(current_path, rename_hops)``. When the file was never
    renamed in the range ``anchor_commit..observed_ref``, returns
    ``(original_path, 0)``.
    """
    out = _git(
        repo,
        "log",
        "-M",
        "--name-status",
        "--reverse",
        "--pretty=format:",
        f"{anchor_commit}..{observed_ref}",
        check=False,
    )
    current = original_path
    hops = 0
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].startswith("R") and parts[1] == current:
            current = parts[2]
            hops += 1
    return current, hops


def _committers_in_range(repo: Path, *, ref: str, path: str, start: int, end: int) -> set[str]:
    """Return the set of committer emails for lines [start..end] at ``ref``."""
    out = _git(
        repo,
        "blame",
        f"-L{start},{end}",
        "--line-porcelain",
        ref,
        "--",
        path,
        check=False,
    )
    emails: set[str] = set()
    for line in out.splitlines():
        if line.startswith("committer-mail "):
            email = line.split(" ", 1)[1].strip().strip("<>")
            if email:
                emails.add(email)
    return emails


def _commit_author_email(repo: Path, commit: str) -> str | None:
    out = _git(repo, "log", "-1", "--format=%ae", commit, check=False).strip()
    return out or None


def _count_preserved_lines(authored_lines: list[str], current_text: str) -> int:
    """Count authored lines that survive (whitespace-stripped) anywhere in
    the current file. Empty lines do not contribute to the count.

    The metric is intentionally line-level rather than character-level so
    a refactor that splits one authored line across two surviving lines
    is not double-counted. Phase 5 uses this for partially_preserved /
    alive_moved gating; future tiers may use AST-aware matching.
    """
    if not authored_lines:
        return 0
    current_lines_stripped = {line.strip() for line in current_text.splitlines() if line.strip()}
    return sum(
        1 for line in authored_lines if line.strip() and line.strip() in current_lines_stripped
    )


def _compute_survival(
    repo: Path,
    *,
    patch: dict[str, Any],
    anchor: dict[str, Any],
    observed_ref: str = "HEAD",
    head_id: dict[str, str] | None = None,
    observed_commit_time: int | None = None,
) -> dict[str, Any]:
    observed_commit_id = _commit_id(repo, observed_ref)
    if head_id is None:
        head_id = _head_id(repo)
    if observed_commit_time is None and observed_commit_id is not None:
        observed_commit_time = _commit_time(repo, observed_ref)
    commit_id = anchor.get("commit_id") or {}
    anchor_commit = commit_id.get("hex")
    path = anchor.get("path") or patch.get("file_path")
    anchor_range = anchor.get("range") or {}
    limitations: list[str] = []

    base = {
        "observation_type": "patch_survival_observed",
        "git_anchor_id": id_from_payload(anchor, "git_anchor"),
        "trace_patch_id": id_from_payload(anchor, "trace_patch")
        or id_from_payload(patch, "trace_patch"),
        "anchor_commit_id": commit_id or None,
        "observed_ref": observed_ref,
        "observed_commit_id": observed_commit_id,
        "observed_commit_time": observed_commit_time,
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
    if revert_search_truncated:
        limitations.append("revert_search_truncated")

    authored_lines = _authored_lines(patch)
    if not authored_lines:
        limitations.append("missing_authored_text")
        return {**base, "survival_state": "unknown"}

    current_text = _show_file(repo, observed_ref, path)
    if current_text is None:
        # File missing at observed_ref. Phase 5 tries rename detection
        # before declaring the patch lost.
        new_path, rename_hops = _track_rename_path(
            repo,
            anchor_commit=anchor_commit,
            observed_ref=observed_ref,
            original_path=path,
        )
        if new_path != path and rename_hops > 0:
            moved_text = _show_file(repo, observed_ref, new_path)
            if moved_text is not None:
                preserved = _count_preserved_lines(authored_lines, moved_text)
                if preserved > 0:
                    return {
                        **base,
                        "survival_state": "alive_moved",
                        "current_path": new_path,
                        "rename_hops": rename_hops,
                        "preserved_line_count": preserved,
                        "authored_line_count": len(authored_lines),
                    }
        if revert_commit:
            return {
                **base,
                "survival_state": "reverted",
                "revert_commit_id": _oid(revert_commit),
            }
        return {**base, "survival_state": "lost"}

    current_lines = current_text.splitlines()
    start = anchor_range.get("start_line")
    end = anchor_range.get("end_line")
    preserved = _count_preserved_lines(authored_lines, current_text)
    range_exists = False
    if isinstance(start, int) and isinstance(end, int) and start >= 1 and end >= start:
        current_range = current_lines[start - 1 : end]
        if current_range == authored_lines:
            return {**base, "survival_state": "alive_on_path"}
        if len(current_lines) >= start:
            range_exists = True
            anchor_email = _commit_author_email(repo, anchor_commit)
            range_committers = _committers_in_range(
                repo,
                ref=observed_ref,
                path=path,
                start=start,
                end=end,
            )
            non_anchor_committers = (
                range_committers - {anchor_email} if anchor_email else range_committers
            )
            if non_anchor_committers:
                return {
                    **base,
                    "survival_state": "repaired",
                    "repair_committer_email": sorted(non_anchor_committers)[0],
                    "repair_committers": sorted(non_anchor_committers),
                    "anchor_author_email": anchor_email,
                }

    if preserved == 0 and revert_commit:
        return {
            **base,
            "survival_state": "reverted",
            "revert_commit_id": _oid(revert_commit),
        }
    if 0 < preserved < len(authored_lines):
        return {
            **base,
            "survival_state": "partially_preserved",
            "preserved_line_count": preserved,
            "authored_line_count": len(authored_lines),
        }
    if range_exists:
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
    *,
    history_limit: int,
) -> tuple[list[tuple[str, int | None]], int | None, list[str]]:
    """Return ``(commits, descendant_count, limitations)`` for one anchor.

    ``commits`` is a chronological ``[(sha, commit_time_unix)]`` path including
    the anchor commit and HEAD. When the anchor is not reachable from HEAD,
    returns a single observation at HEAD and ``descendant_count=None`` so
    callers can distinguish "unreachable, count unknown" from "reachable, zero
    descendants".
    """
    if not _git_ok(repo, "merge-base", "--is-ancestor", anchor_commit, "HEAD"):
        head_sha = _git(repo, "rev-parse", "HEAD", check=False).strip()
        if not head_sha:
            return [], None, []
        return [("HEAD", _commit_time(repo, "HEAD"))], None, []

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

    anchor_time = _commit_time(repo, anchor_commit)
    if descendant_count == 0:
        return [(anchor_commit, anchor_time)], 0, []

    limit = max(history_limit, 2)
    if descendant_count <= limit - 1:
        out = _git(
            repo,
            "log",
            "--reverse",
            "--ancestry-path",
            "--format=%H %ct",
            f"{anchor_commit}..HEAD",
            check=False,
        )
        descendants = [_parse_log_line(line) for line in out.splitlines() if line.strip()]
        return [(anchor_commit, anchor_time)] + descendants, descendant_count, []

    keep_oldest = max(limit - 2, 0)
    oldest_descendants: list[tuple[str, int | None]] = []
    if keep_oldest:
        out = _git(
            repo,
            "log",
            "--ancestry-path",
            f"--skip={descendant_count - keep_oldest}",
            f"--max-count={keep_oldest}",
            "--format=%H %ct",
            f"{anchor_commit}..HEAD",
            check=False,
        )
        oldest_descendants = list(
            reversed([_parse_log_line(line) for line in out.splitlines() if line.strip()])
        )
    head_sha = _git(repo, "rev-parse", "HEAD", check=False).strip()
    head_time = _commit_time(repo, head_sha) if head_sha else None
    return (
        [(anchor_commit, anchor_time)] + oldest_descendants + [(head_sha, head_time)],
        descendant_count,
        ["patch_trail_history_truncated"],
    )


def _anchor_observations(
    repo: Path,
    *,
    patch: dict[str, Any],
    anchor: dict[str, Any],
    head_id: dict[str, str] | None,
    history_limit: int,
) -> tuple[list[dict[str, Any]], int | None, list[str]]:
    commit_id = anchor.get("commit_id") or {}
    anchor_commit = commit_id.get("hex")
    if not anchor_commit:
        observation = _compute_survival(repo, patch=patch, anchor=anchor, head_id=head_id)
        observation["anchor_trail_index"] = 0
        observation["anchor_descendant_count"] = None
        return [observation], None, []

    commits, descendant_count, limitations = _commits_from_anchor_to_head(
        repo, anchor_commit, history_limit=history_limit
    )
    observations: list[dict[str, Any]] = []
    for index, (commit_sha, commit_time) in enumerate(commits):
        observation = _compute_survival(
            repo,
            patch=patch,
            anchor=anchor,
            observed_ref=commit_sha,
            head_id=head_id,
            observed_commit_time=commit_time,
        )
        observation["anchor_trail_index"] = index
        observation["anchor_descendant_count"] = descendant_count
        observations.append(observation)
    return observations, descendant_count, limitations


def _sync(
    repo: Path,
    *,
    trace_patch_id: str | None = None,
    git_anchor_id: str | None = None,
    history_limit: int | None = None,
) -> dict[str, Any]:
    """Sync a Trace Patch against Git history and report survival.

    Limitation field scoping is deliberately three-level:

    * ``response["trail_limitations"]`` carries trail-construction limitations
      (e.g. ``patch_trail_history_truncated`` when an anchor's descendant count
      exceeds ``history_limit``).
    * ``response["observations"][i]["limitations"]`` carries per-observation
      limitations from a single commit lookup (e.g. ``revert_search_truncated``,
      ``missing_authored_text``, ``anchor_commit_not_reachable_from_*``).
    * ``response["current_survival"]["limitations"]`` mirrors the latest
      observation per anchor.

    These fields are deliberately distinct from the Phase 5 ``capture_limitations``
    vocabulary on TrailEvents. Capture limitations describe what the capture
    pipeline observed during a session; trail/observation limitations describe
    what the sync projection could compute at query time over current repo
    state.
    """
    effective_limit = history_limit if history_limit is not None else PATCH_TRAIL_COMMIT_LIMIT
    head_id = _head_id(repo)
    events = read_events(repo)
    patches: dict[str, tuple[dict[str, Any], Any]] = {}
    anchors: list[tuple[dict[str, Any], Any]] = []
    if trace_patch_id:
        trace_patch_id = normalize_id(trace_patch_id)
    if git_anchor_id:
        git_anchor_id = normalize_id(git_anchor_id)
    for event in events:
        if event.event_type == "trace_patch_created":
            patch_id = id_from_payload(event.payload, "trace_patch")
            if patch_id:
                patches[patch_id] = (event.payload, event)
        elif event.event_type == "git_anchor_created":
            anchors.append((event.payload, event))

    if git_anchor_id:
        anchors = [
            (anchor, event)
            for anchor, event in anchors
            if id_from_payload(anchor, "git_anchor") == git_anchor_id
        ]
        if anchors and trace_patch_id is None:
            trace_patch_id = id_from_payload(anchors[0][0], "trace_patch")
    elif trace_patch_id:
        anchors = [
            (anchor, event)
            for anchor, event in anchors
            if id_from_payload(anchor, "trace_patch") == trace_patch_id
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
            "history_limit": effective_limit,
            "trail_limitations": ["no_trace_patch_event"],
            "event_log_ref": EVENT_LOG_REF,
            "phase4_survival_states": PHASE4_SURVIVAL_STATES,
            "phase5_survival_states": PHASE5_SURVIVAL_STATES,
            "reserved_survival_states": RESERVED_PHASE5_SURVIVAL_STATES,
            "source_events": [],
        }

    patch, patch_event = patch_pair
    if not anchors:
        return {
            "trace_patch_id": id_from_payload(patch, "trace_patch"),
            "git_anchor_id": git_anchor_id,
            "relation": "unknown",
            "current_survival": {"survival_state": "unknown"},
            "current_observations": [],
            "observations": [],
            "observation_scope": OBSERVATION_SCOPE,
            "history_limit": effective_limit,
            "trail_limitations": ["no_git_anchor_event"],
            "event_log_ref": EVENT_LOG_REF,
            "phase4_survival_states": PHASE4_SURVIVAL_STATES,
            "phase5_survival_states": PHASE5_SURVIVAL_STATES,
            "reserved_survival_states": RESERVED_PHASE5_SURVIVAL_STATES,
            "source_events": [_source_event(patch_event)],
        }

    observations: list[dict[str, Any]] = []
    current_observations: list[tuple[int, dict[str, Any]]] = []
    trail_limitations: list[str] = []
    sorted_anchors = sorted(anchors, key=lambda item: item[1].event_sequence)
    for anchor, event in sorted_anchors:
        anchor_observations, _descendant_count, anchor_limitations = _anchor_observations(
            repo,
            patch=patch,
            anchor=anchor,
            head_id=head_id,
            history_limit=effective_limit,
        )
        trail_limitations.extend(anchor_limitations)
        for observation in anchor_observations:
            observation["anchor_event_sequence"] = event.event_sequence
        start_index = len(observations)
        observations.extend(anchor_observations)
        if anchor_observations:
            current_observations.append(
                (start_index + len(anchor_observations) - 1, anchor_observations[-1])
            )

    for sequence, observation in enumerate(observations):
        observation["observation_sequence"] = sequence

    unique_trail_limitations = list(dict.fromkeys(trail_limitations))
    current_observation_values = [observation for _index, observation in current_observations]
    return {
        "trace_patch_id": id_from_payload(patch, "trace_patch"),
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
        "history_limit": effective_limit,
        "trail_limitations": unique_trail_limitations,
        "event_log_ref": EVENT_LOG_REF,
        "phase4_survival_states": PHASE4_SURVIVAL_STATES,
        "reserved_survival_states": RESERVED_PHASE5_SURVIVAL_STATES,
        "source_events": [_source_event(patch_event)]
        + [_source_event(event) for _anchor, event in sorted_anchors],
    }


def sync_patch(
    repo: Path,
    trace_patch_id: str,
    *,
    history_limit: int | None = None,
) -> dict[str, Any]:
    """Sync survival for all Git Anchors attached to a Trace Patch."""
    return _sync(repo, trace_patch_id=trace_patch_id, history_limit=history_limit)


def sync_anchor(
    repo: Path,
    git_anchor_id: str,
    *,
    history_limit: int | None = None,
) -> dict[str, Any]:
    """Sync survival for one Git Anchor."""
    return _sync(repo, git_anchor_id=git_anchor_id, history_limit=history_limit)
