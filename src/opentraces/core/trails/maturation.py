"""Runtime maturation for Trace Patches into Git Anchors."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .anchors import reconcile_commit_anchors
from .event_log import read_events, read_events_scoped
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
    truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "commits_considered": self.commits_considered,
            "searches_completed": self.searches_completed,
            "anchors_created": self.anchors_created,
            "errors": list(self.errors),
            "truncated": self.truncated,
        }


def mature_trails(
    repo: Path,
    *,
    commit_refs: Iterable[str] | None = None,
    max_commits: int = DEFAULT_RECENT_COMMITS,
    attribution_version: str | None = None,
    writer: str = MATURATION_WRITER,
    deadline: float | None = None,
) -> MaturationSummary:
    """Search recent commits for Trace Patches that are not yet anchored.

    The underlying anchor reconciler is append-only and idempotent per
    ``(trace_patch_id, commit, attribution_version)`` because it records
    ``git_anchor_search_completed`` events, including ``unknown`` results.

    ``deadline`` (``time.monotonic`` absolute, #65) bounds one call's work:
    the per-patch loop inside ``reconcile_commit_anchors`` already supports it
    (#44 Phase 2, durable: searched subsets are recorded), and the commit loop
    here stops starting new commits once it expires. A truncated sweep does
    NOT stamp the maturation watermark, so the next tick resumes the backlog
    where this one stopped — a cold backlog (710,875 searches / 14.4GB peak in
    ONE tick observed live) amortises across ticks instead.
    """
    repo = Path(repo).resolve()
    commit_refs = tuple(commit_refs) if commit_refs is not None else None
    effective_version = attribution_version or ATTRIBUTION_VERSION
    # #65 (codex P2): bail BEFORE any log work when the budget is already
    # spent — a caller chaining budgeted phases must not pay the shared scan
    # below out of an exhausted budget.
    if deadline is not None and time.monotonic() >= deadline:
        return MaturationSummary(truncated=True)
    commits = _candidate_commits(repo, commit_refs=commit_refs, max_commits=max_commits)
    errors = _candidate_errors(
        repo,
        commit_refs=commit_refs,
        max_commits=max_commits,
        commits=commits,
    )
    if not commits:
        return MaturationSummary(errors=errors)

    # #23 step 1: read the anchor/search/patch slice for ALL candidate commits in
    # ONE whole-log pass (`commit_shas`), then hand each reconcile call the shared
    # slice. This replaces N per-commit scoped reads (each a `rev-list --objects`
    # over the whole log) with a single read — the quadratic git work that pinned
    # a CPU core on mature repos.
    #
    # #65: stream that pass through a sink that keeps ONLY patch events plus
    # the dedup KEY tuples. Anchor events and (especially) the plan-090 search
    # summary events are reduced to keys as they stream — a drained backlog's
    # summaries carry results[] arrays for ~710K searches, and re-materialising
    # them per tick was a multi-GB allocator in the live #65 capture.
    shared_patches: list | None
    shared_anchor_keys: set[tuple] | None
    shared_search_keys: set[tuple] | None
    try:
        patches: list = []
        anchor_keys: set[tuple] = set()
        search_keys: set[tuple] = set()

        def _maturation_sink(event) -> None:
            etype = event.event_type
            if etype == "trace_patch_created":
                patches.append(event)
            elif etype == "git_anchor_created":
                anchor_keys.add((
                    id_from_payload(event.payload, "trace_patch"),
                    (event.payload.get("commit_id") or {}).get("hex"),
                ))
            elif etype == "git_anchor_search_completed":
                for record in iter_search_records(event):
                    search_keys.add((
                        record["trace_patch_id"],
                        record["search_head_sha"],
                        record["attribution_version"],
                    ))

        read_events_scoped(
            repo,
            event_types={
                "trace_patch_created",
                "git_anchor_created",
                "git_anchor_search_completed",
            },
            commit_filter={
                "git_anchor_created": "commit_id",
                "git_anchor_search_completed": "search_head",
            },
            commit_shas=set(commits),
            sink=_maturation_sink,
        )
        shared_patches = patches
        shared_anchor_keys = anchor_keys
        shared_search_keys = search_keys
    except Exception:  # noqa: BLE001 — fall back to per-commit reads on error
        shared_patches = None
        shared_anchor_keys = None
        shared_search_keys = None

    # #23 step 2: sum each reconcile's reported search count via ``summary_out``
    # instead of reading the whole log twice (before/after) just to diff the
    # search-record delta.
    # #65 (codex P2): the shared scan above is atomic by design — aborting it
    # mid-stream would hand the commit loop PARTIAL dedup keys, and a missing
    # search key re-emits an already-recorded search (violating the plan-090
    # R5 invariant). So the budget brackets the scan instead of preempting it:
    # checked before (above) and after (here). A scan that itself overruns the
    # budget yields a truncated no-op tick — the watermark stays unstamped,
    # the next tick retries, and the child's wall-clock budget remains the
    # hard bound on total tick time.
    anchors_created = 0
    searches_completed = 0
    truncated = False
    for commit in commits:
        if deadline is not None and time.monotonic() >= deadline:
            truncated = True
            break
        try:
            per_commit_summary: dict[str, object] = {}
            anchors_created += len(
                reconcile_commit_anchors(
                    repo,
                    commit,
                    writer=writer,
                    capture_method=MATURATION_CAPTURE_METHOD,
                    attribution_version=effective_version,
                    patch_events=shared_patches,
                    anchor_keys=shared_anchor_keys,
                    search_keys=shared_search_keys,
                    summary_out=per_commit_summary,
                    deadline=deadline,
                )
            )
            searches_completed += int(
                per_commit_summary.get("searches_recorded", 0) or 0
            )
            if per_commit_summary.get("budget_exhausted"):
                truncated = True
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{commit}: {type(exc).__name__}: {exc}")

    # Only a full recent-commits sweep may stamp the watermark: an explicit
    # commit_refs subset has not matured the rest of the recent window, and
    # stamping would make the quiet-tick gate skip those commits until the
    # event-log head or repo HEAD next changes. A deadline-truncated sweep
    # must not stamp either (#65) — the unsearched remainder would hide
    # behind the quiet-tick gate until the next head change.
    if commit_refs is None and not truncated:
        _stamp_maturation_watermark(repo, effective_version)
    return MaturationSummary(
        commits_considered=len(commits),
        searches_completed=searches_completed,
        anchors_created=anchors_created,
        errors=errors,
        truncated=truncated,
    )


def has_unsearched_recent_patches(
    repo: Path,
    *,
    max_commits: int = DEFAULT_RECENT_COMMITS,
    attribution_version: str | None = None,
) -> bool:
    """Return true if any recent commit lacks a search for any Trace Patch."""
    repo = Path(repo).resolve()
    effective_version = attribution_version or ATTRIBUTION_VERSION

    # #23 step 3: watermark short-circuit. If the event-log head, repo HEAD, and
    # attribution version are all unchanged since the last time maturation ran to
    # completion (or the gate previously found nothing to do), there is provably
    # nothing new to search — return False WITHOUT reading the whole event log.
    # This is the quiet-tick fast path: zero git object enumeration on idle
    # ticks, which is the runaway-CPU symptom in #23.
    state = _maturation_state(repo, effective_version)
    watermark = _load_maturation_watermark(repo)
    if state is not None and watermark == state:
        return False

    commits = _candidate_commits(repo, commit_refs=None, max_commits=max_commits)
    if not commits:
        # Nothing to mature at this head; record the watermark so subsequent
        # quiet ticks short-circuit.
        if state is not None:
            _save_maturation_watermark(repo, state)
        return False
    # #45: scope the gate's whole-log read to exactly the slice mature_trails
    # reads (:72-84). The gate never inspects any other event type, and the
    # watcher daemon calls it on every quiet tick that misses the watermark, so
    # a full-log materialisation here is the same unbounded per-tick RSS cost
    # Bug B closed on the hook path. ``trace_patch_created`` carries no
    # commit_filter (kept in full for the patch_ids set); the two anchor/search
    # types are commit-keyed to the candidate commits, mirroring mature_trails.
    #
    # #65 (codex P1): stream the read down to the three KEY SETS the gate
    # actually consults — never retain the events. On a truncated-backlog repo
    # the maturation watermark is deliberately unstamped, so EVERY quiet tick
    # passes through this gate before the budgeted mature_trails call; a
    # materialised slice here (fat trace_patch_created authored_text + the
    # plan-090 summary results[] arrays) re-opens the multi-GB path the sink
    # closed in mature_trails, and the child gets RSS-killed before the
    # budgeted worker can make progress — defeating amortisation entirely.
    patch_ids: set[str] = set()
    searched: set[tuple] = set()
    anchored: set[tuple] = set()

    def _gate_sink(event) -> None:
        etype = event.event_type
        if etype == "trace_patch_created":
            trace_patch_id = id_from_payload(event.payload, "trace_patch")
            if trace_patch_id:
                patch_ids.add(trace_patch_id)
        elif etype == "git_anchor_search_completed":
            for record in iter_search_records(event):
                searched.add((
                    record["trace_patch_id"],
                    record["search_head_sha"],
                    record["attribution_version"],
                ))
        elif etype == "git_anchor_created":
            # #23 step 4: close the gate/worker asymmetry. The worker
            # (reconcile_commit_anchors) skips a (patch, commit) pair when
            # EITHER a search record OR an anchor already exists for it. The
            # gate must mirror that, else a pair with an
            # anchor-but-no-search-record (possible across attribution
            # versions / legacy logs) reads as "unsearched" forever -> the
            # worker is invoked every tick, does nothing, never records a new
            # search -> livelock.
            anchored.add((
                id_from_payload(event.payload, "trace_patch"),
                (event.payload.get("commit_id") or {}).get("hex"),
            ))

    try:
        read_events_scoped(
            repo,
            event_types={
                "trace_patch_created",
                "git_anchor_created",
                "git_anchor_search_completed",
            },
            commit_filter={
                "git_anchor_created": "commit_id",
                "git_anchor_search_completed": "search_head",
            },
            commit_shas=set(commits),
            sink=_gate_sink,
        )
    except Exception:
        return False
    if not patch_ids:
        if state is not None:
            _save_maturation_watermark(repo, state)
        return False
    has_unsearched = any(
        (trace_patch_id, commit, effective_version) not in searched
        and (trace_patch_id, commit) not in anchored
        for trace_patch_id in patch_ids
        for commit in commits
    )
    if not has_unsearched and state is not None:
        # Everything in range is satisfied — stamp so the next quiet tick is free.
        _save_maturation_watermark(repo, state)
    return has_unsearched


# --- maturation watermark (quiet-tick short-circuit, #23) -------------------

_MATURATION_WATERMARK_FORMAT = 1


def _maturation_watermark_path(repo: Path) -> Path | None:
    """Per-repo maturation watermark file inside the git dir.

    Separate file from the verify watermark (event_log_verified.json) so the two
    optimizations never collide.
    """
    proc = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    git_dir = Path(proc.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (repo / git_dir).resolve()
    return git_dir / "opentraces" / "maturation_watermark.json"


def _event_log_head(repo: Path) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/opentraces/local/events/v1"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _maturation_state(repo: Path, attribution_version: str) -> dict[str, object] | None:
    """The watermark identity for the current world: event-log head + repo HEAD
    + attribution version. None when either head is unresolvable (then the gate
    falls through to the full scan and never short-circuits)."""
    event_head = _event_log_head(repo)
    repo_head = _rev_parse(repo, "HEAD")
    if event_head is None or repo_head is None:
        return None
    return {
        "format": _MATURATION_WATERMARK_FORMAT,
        "event_log_head": event_head,
        "repo_head": repo_head,
        "attribution_version": attribution_version,
    }


def _load_maturation_watermark(repo: Path) -> dict[str, object] | None:
    path = _maturation_watermark_path(repo)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != _MATURATION_WATERMARK_FORMAT:
        return None
    return data


def _save_maturation_watermark(repo: Path, state: dict[str, object]) -> None:
    path = _maturation_watermark_path(repo)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 — watermark is an optimization, never fatal
        return


def _stamp_maturation_watermark(
    repo: Path, attribution_version: str | None = None
) -> None:
    """Stamp the watermark at the current world state (end of mature_trails).

    Called even when maturation hit errors: this trades infinite-retry livelock
    for at-most-one-retry-per-head-change. Once the event-log head or repo HEAD
    advances, the watermark mismatches and maturation runs again.
    """
    state = _maturation_state(repo, attribution_version or ATTRIBUTION_VERSION)
    if state is not None:
        _save_maturation_watermark(repo, state)


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
