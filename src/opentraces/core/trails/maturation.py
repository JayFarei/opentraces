"""Runtime maturation for Trace Patches into Git Anchors."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .anchors import reconcile_commit_anchors
from .event_log import read_events
from .ids import id_from_payload
from .models import ATTRIBUTION_VERSION
from .search_records import iter_search_records

DEFAULT_RECENT_COMMITS = 50
MATURATION_CAPTURE_METHOD = ["trail_maturation"]
MATURATION_WRITER = "trail-maturation"


@dataclass(frozen=True)
class MaturationSummary:
    commits_considered: int = 0
    searches_completed: int = 0
    anchors_created: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "commits_considered": self.commits_considered,
            "searches_completed": self.searches_completed,
            "anchors_created": self.anchors_created,
            "errors": list(self.errors),
        }


def mature_trails(
    repo: Path,
    *,
    commit_refs: Iterable[str] | None = None,
    max_commits: int = DEFAULT_RECENT_COMMITS,
    attribution_version: str | None = None,
    writer: str = MATURATION_WRITER,
) -> MaturationSummary:
    """Search recent commits for Trace Patches that are not yet anchored.

    The underlying anchor reconciler is append-only and idempotent per
    ``(trace_patch_id, commit, attribution_version)`` because it records
    ``git_anchor_search_completed`` events, including ``unknown`` results.
    """
    repo = Path(repo).resolve()
    commit_refs = tuple(commit_refs) if commit_refs is not None else None
    effective_version = attribution_version or ATTRIBUTION_VERSION
    commits = _candidate_commits(repo, commit_refs=commit_refs, max_commits=max_commits)
    errors = _candidate_errors(
        repo,
        commit_refs=commit_refs,
        max_commits=max_commits,
        commits=commits,
    )
    if not commits:
        return MaturationSummary(errors=errors)

    before = _event_counts(repo)
    anchors_created = 0
    for commit in commits:
        try:
            anchors_created += len(
                reconcile_commit_anchors(
                    repo,
                    commit,
                    writer=writer,
                    capture_method=MATURATION_CAPTURE_METHOD,
                    attribution_version=effective_version,
                )
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{commit}: {type(exc).__name__}: {exc}")

    after = _event_counts(repo)
    return MaturationSummary(
        commits_considered=len(commits),
        searches_completed=max(
            0,
            after["git_anchor_search_completed"] - before["git_anchor_search_completed"],
        ),
        anchors_created=anchors_created,
        errors=errors,
    )


def has_unsearched_recent_patches(
    repo: Path,
    *,
    max_commits: int = DEFAULT_RECENT_COMMITS,
    attribution_version: str | None = None,
) -> bool:
    """Return true if any recent commit lacks a search for any Trace Patch."""
    repo = Path(repo).resolve()
    commits = _candidate_commits(repo, commit_refs=None, max_commits=max_commits)
    if not commits:
        return False
    effective_version = attribution_version or ATTRIBUTION_VERSION
    try:
        events = read_events(repo, verify=False)
    except Exception:
        return False
    patch_ids = {
        trace_patch_id
        for event in events
        if event.event_type == "trace_patch_created"
        for trace_patch_id in [id_from_payload(event.payload, "trace_patch")]
        if trace_patch_id
    }
    if not patch_ids:
        return False
    searched = {
        (
            record["trace_patch_id"],
            record["search_head_sha"],
            record["attribution_version"],
        )
        for event in events
        if event.event_type == "git_anchor_search_completed"
        for record in iter_search_records(event)
    }
    return any(
        (trace_patch_id, commit, effective_version) not in searched
        for trace_patch_id in patch_ids
        for commit in commits
    )


def _candidate_commits(
    repo: Path,
    *,
    commit_refs: Iterable[str] | None,
    max_commits: int,
) -> list[str]:
    if commit_refs is not None:
        out: list[str] = []
        seen: set[str] = set()
        for ref in commit_refs:
            resolved = _rev_parse(repo, ref)
            if resolved and resolved not in seen:
                out.append(resolved)
                seen.add(resolved)
        return out

    limit = max(0, int(max_commits))
    if limit == 0:
        return []
    proc = subprocess.run(
        ["git", "rev-list", f"--max-count={limit}", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _candidate_errors(
    repo: Path,
    *,
    commit_refs: Iterable[str] | None,
    max_commits: int,
    commits: list[str],
) -> list[str]:
    if commit_refs is not None:
        errors: list[str] = []
        for ref in commit_refs:
            if _rev_parse(repo, ref) is None:
                errors.append(f"unresolved commit ref: {ref}")
        return errors
    if max_commits > 0 and not commits and not _rev_parse(repo, "HEAD"):
        return ["not a Git repository or HEAD is unavailable"]
    return []


def _rev_parse(repo: Path, ref: str) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", ref],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _event_counts(repo: Path) -> dict[str, int]:
    # plan 090: count per-patch search RECORDS, not raw events. One v2 summary
    # event covers N patch-searches, so counting events would collapse
    # ``searches_completed`` to ~run-count. Expanding through iter_search_records
    # keeps the metric meaning "number of patch-searches" across both the legacy
    # per-patch shape and the new summary shape.
    counts = {
        "git_anchor_search_completed": 0,
    }
    try:
        events = read_events(repo, verify=False)
    except Exception:
        return counts
    for event in events:
        if event.event_type == "git_anchor_search_completed":
            counts["git_anchor_search_completed"] += sum(
                1 for _ in iter_search_records(event)
            )
    return counts
