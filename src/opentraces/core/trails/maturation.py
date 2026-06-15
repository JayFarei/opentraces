"""Runtime maturation for Trace Patches into Git Anchors."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .anchors import ANCHOR_ALGORITHMS_PHASE5, reconcile_commit_anchors
from .contract import ANCHOR_SEARCH_SCHEMA_VERSION
from .event_log import append_event_batch, read_events_scoped
from .ids import id_from_payload
from .models import ATTRIBUTION_VERSION, TrailEventDraft
from .search_records import build_anchor_search_summary_payload, iter_search_records

DEFAULT_RECENT_COMMITS = 50
MATURATION_CAPTURE_METHOD = ["trail_maturation"]
MATURATION_WRITER = "trail-maturation"
DEFAULT_PATCH_CHUNK_MAX_EVENTS = 256
DEFAULT_PATCH_CHUNK_MAX_AUTHORED_BYTES = 64 * 1024 * 1024


class _MaturationBudgetExhausted(Exception):
    pass


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

    ``deadline`` (``time.monotonic`` absolute, #65) bounds one call's work. To
    preserve plan-090's one-summary-per-commit shape, maturation appends search
    summaries and anchors only after a complete non-truncated pass. A truncated
    sweep does NOT stamp the maturation watermark, so the next tick retries the
    backlog against the bounded working set rather than hiding unsearched pairs.
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

    anchors_created = 0
    searches_completed = 0
    pending_anchors_created = 0
    pending_searches_completed = 0
    truncated = False
    with tempfile.TemporaryDirectory(prefix="opentraces-maturation-") as scratch_dir:
        scratch = _open_maturation_scratch(Path(scratch_dir) / "maturation.sqlite3")
        try:
            # #65 cure: retain dedup keys in a disk-backed scratch index, not in
            # Python sets for the whole tick. Per-chunk queries below materialize
            # only O(chunk_size * candidate_commits) keys in memory.
            _build_dedup_index(repo, scratch, commits)

            # The shared scan is atomic by design. If it overruns the caller's
            # budget, do not use a partial dedup view; return a truncated no-op
            # tick and leave the watermark unstamped.
            if deadline is not None and time.monotonic() >= deadline:
                truncated = True
            else:
                patch_chunk: list = []
                patch_chunk_authored_bytes = 0
                max_patch_events, max_patch_bytes = _patch_chunk_limits()

                def _flush_patch_chunk() -> None:
                    nonlocal pending_anchors_created, pending_searches_completed
                    nonlocal truncated, patch_chunk, patch_chunk_authored_bytes
                    if not patch_chunk or truncated:
                        return
                    chunk_result = _mature_patch_chunk(
                        repo,
                        commits=commits,
                        patch_events=patch_chunk,
                        scratch=scratch,
                        attribution_version=effective_version,
                        writer=writer,
                        deadline=deadline,
                    )
                    pending_anchors_created += chunk_result.anchors_created
                    pending_searches_completed += chunk_result.searches_completed
                    errors.extend(chunk_result.errors)
                    truncated = truncated or chunk_result.truncated
                    patch_chunk = []
                    patch_chunk_authored_bytes = 0
                    if truncated:
                        raise _MaturationBudgetExhausted

                def _patch_sink(event) -> None:
                    nonlocal patch_chunk_authored_bytes
                    patch_chunk.append(event)
                    patch_chunk_authored_bytes += _patch_event_authored_bytes(event)
                    if (
                        len(patch_chunk) >= max_patch_events
                        or patch_chunk_authored_bytes >= max_patch_bytes
                    ):
                        _flush_patch_chunk()

                try:
                    read_events_scoped(
                        repo,
                        event_types={"trace_patch_created"},
                        sink=_patch_sink,
                    )
                    _flush_patch_chunk()
                except _MaturationBudgetExhausted:
                    pass
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"patch stream failed: {type(exc).__name__}: {exc}")
                    truncated = True

            # #65 cure: flush the work this tick COMPLETED even when it truncated,
            # so a cold backlog drains across ticks instead of recomputing and
            # discarding the same prefix forever (the livelock). The appended
            # per-(patch, commit) search/anchor events make those pairs dedup-skip
            # on the next tick, which is what makes progress monotonic. The
            # watermark is still only stamped on a full, untruncated sweep (below),
            # so the quiet-tick gate keeps re-entering until the remainder is done.
            try:
                _flush_maturation_scratch(
                    repo,
                    scratch,
                    commits=commits,
                    attribution_version=effective_version,
                    writer=writer,
                )
                anchors_created = pending_anchors_created
                searches_completed = pending_searches_completed
            except Exception as exc:  # noqa: BLE001
                errors.append(f"maturation flush failed: {type(exc).__name__}: {exc}")
                truncated = True
        except Exception as exc:  # noqa: BLE001
            errors.append(f"dedup scratch failed: {type(exc).__name__}: {exc}")
            truncated = True
        finally:
            scratch.close()

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


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _patch_chunk_limits() -> tuple[int, int]:
    return (
        _positive_int_env(
            "OT_MATURATION_PATCH_CHUNK_MAX_EVENTS",
            DEFAULT_PATCH_CHUNK_MAX_EVENTS,
        ),
        _positive_int_env(
            "OT_MATURATION_PATCH_CHUNK_MAX_BYTES",
            DEFAULT_PATCH_CHUNK_MAX_AUTHORED_BYTES,
        ),
    )


def _patch_event_authored_bytes(event) -> int:
    text = event.payload.get("authored_text") or ""
    if isinstance(text, str):
        return len(text.encode("utf-8", errors="ignore"))
    return 0


def _open_maturation_scratch(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=FILE")
    conn.executescript(
        """
        CREATE TABLE anchor_keys (
            patch_id TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            PRIMARY KEY (patch_id, commit_sha)
        );
        CREATE TABLE search_keys (
            patch_id TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            attribution_version TEXT NOT NULL,
            PRIMARY KEY (patch_id, commit_sha, attribution_version)
        );
        CREATE TABLE search_results (
            commit_sha TEXT NOT NULL,
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            result_json TEXT NOT NULL
        );
        CREATE INDEX search_results_commit_seq
            ON search_results(commit_sha, seq);
        CREATE TABLE anchor_drafts (
            commit_sha TEXT NOT NULL,
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_json TEXT NOT NULL
        );
        CREATE INDEX anchor_drafts_commit_seq
            ON anchor_drafts(commit_sha, seq);
        CREATE TABLE patch_ids (
            patch_id TEXT PRIMARY KEY
        );
        CREATE TEMP TABLE candidate_commits (
            commit_sha TEXT PRIMARY KEY
        );
        CREATE TEMP TABLE chunk_patch_ids (
            patch_id TEXT PRIMARY KEY
        );
        """
    )
    return conn


def _store_candidate_commits(
    scratch: sqlite3.Connection,
    commits: list[str],
) -> None:
    scratch.execute("DELETE FROM candidate_commits")
    scratch.executemany(
        "INSERT OR IGNORE INTO candidate_commits(commit_sha) VALUES (?)",
        [(commit,) for commit in commits],
    )


def _build_dedup_index(
    repo: Path,
    scratch: sqlite3.Connection,
    commits: list[str],
) -> None:
    commit_set = set(commits)
    anchor_rows: list[tuple[str, str]] = []
    search_rows: list[tuple[str, str, str]] = []

    def _flush() -> None:
        nonlocal anchor_rows, search_rows
        if anchor_rows:
            scratch.executemany(
                "INSERT OR IGNORE INTO anchor_keys(patch_id, commit_sha) "
                "VALUES (?, ?)",
                anchor_rows,
            )
            anchor_rows = []
        if search_rows:
            scratch.executemany(
                "INSERT OR IGNORE INTO search_keys"
                "(patch_id, commit_sha, attribution_version) VALUES (?, ?, ?)",
                search_rows,
            )
            search_rows = []

    def _key_sink(event) -> None:
        if event.event_type == "git_anchor_created":
            patch_id = id_from_payload(event.payload, "trace_patch")
            commit = (event.payload.get("commit_id") or {}).get("hex")
            if patch_id and commit in commit_set:
                anchor_rows.append((patch_id, commit))
        elif event.event_type == "git_anchor_search_completed":
            for record in iter_search_records(event):
                patch_id = record.get("trace_patch_id")
                commit = record.get("search_head_sha")
                version = record.get("attribution_version")
                if patch_id and commit in commit_set and version:
                    search_rows.append((patch_id, commit, version))
        if len(anchor_rows) + len(search_rows) >= 4096:
            _flush()

    read_events_scoped(
        repo,
        event_types={
            "git_anchor_created",
            "git_anchor_search_completed",
        },
        commit_filter={
            "git_anchor_created": "commit_id",
            "git_anchor_search_completed": "search_head",
        },
        commit_shas=commit_set,
        sink=_key_sink,
    )
    _flush()
    scratch.commit()


def _patch_ids_for_chunk(patch_events: list) -> list[str]:
    patch_ids: list[str] = []
    seen: set[str] = set()
    for event in patch_events:
        patch_id = id_from_payload(event.payload, "trace_patch")
        if patch_id and patch_id not in seen:
            patch_ids.append(patch_id)
            seen.add(patch_id)
    return patch_ids


def _dedup_keys_for_chunk(
    scratch: sqlite3.Connection,
    patch_ids: list[str],
) -> tuple[set[tuple], set[tuple]]:
    scratch.execute("DELETE FROM chunk_patch_ids")
    if not patch_ids:
        return set(), set()
    scratch.executemany(
        "INSERT OR IGNORE INTO chunk_patch_ids(patch_id) VALUES (?)",
        [(patch_id,) for patch_id in patch_ids],
    )
    anchor_keys = {
        (patch_id, commit_sha)
        for patch_id, commit_sha in scratch.execute(
            "SELECT a.patch_id, a.commit_sha "
            "FROM anchor_keys a "
            "JOIN chunk_patch_ids c ON c.patch_id = a.patch_id"
        )
    }
    search_keys = {
        (patch_id, commit_sha, attribution_version)
        for patch_id, commit_sha, attribution_version in scratch.execute(
            "SELECT s.patch_id, s.commit_sha, s.attribution_version "
            "FROM search_keys s "
            "JOIN chunk_patch_ids c ON c.patch_id = s.patch_id"
        )
    }
    return anchor_keys, search_keys


def _store_chunk_outputs(
    scratch: sqlite3.Connection,
    commit: str,
    *,
    search_results: list[dict],
    anchor_drafts: list[TrailEventDraft],
    attribution_version: str,
) -> None:
    if search_results:
        scratch.executemany(
            "INSERT INTO search_results(commit_sha, result_json) VALUES (?, ?)",
            [
                (commit, json.dumps(result, sort_keys=True, separators=(",", ":")))
                for result in search_results
            ],
        )
        scratch.executemany(
            "INSERT OR IGNORE INTO search_keys"
            "(patch_id, commit_sha, attribution_version) VALUES (?, ?, ?)",
            [
                (str(result["trace_patch_id"]), commit, attribution_version)
                for result in search_results
                if result.get("trace_patch_id")
            ],
        )
    if anchor_drafts:
        scratch.executemany(
            "INSERT INTO anchor_drafts(commit_sha, draft_json) VALUES (?, ?)",
            [
                (
                    commit,
                    json.dumps(
                        draft.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                for draft in anchor_drafts
            ],
        )
        scratch.executemany(
            "INSERT OR IGNORE INTO anchor_keys(patch_id, commit_sha) VALUES (?, ?)",
            [
                (str(draft.payload["trace_patch_id"]), commit)
                for draft in anchor_drafts
                if draft.payload.get("trace_patch_id")
            ],
        )
    scratch.commit()


def _flush_maturation_scratch(
    repo: Path,
    scratch: sqlite3.Connection,
    *,
    commits: list[str],
    attribution_version: str,
    writer: str,
) -> None:
    for commit in commits:
        results = [
            json.loads(row[0])
            for row in scratch.execute(
                "SELECT result_json FROM search_results "
                "WHERE commit_sha = ? ORDER BY seq",
                (commit,),
            )
        ]
        anchor_drafts = [
            TrailEventDraft.model_validate(json.loads(row[0]))
            for row in scratch.execute(
                "SELECT draft_json FROM anchor_drafts "
                "WHERE commit_sha = ? ORDER BY seq",
                (commit,),
            )
        ]
        if not results and not anchor_drafts:
            continue
        drafts: list[TrailEventDraft] = []
        if results:
            drafts.append(
                TrailEventDraft(
                    event_type="git_anchor_search_completed",
                    trace_id=None,
                    step_index=None,
                    capture_method=MATURATION_CAPTURE_METHOD,
                    ATTRIBUTION_VERSION=attribution_version,
                    payload=build_anchor_search_summary_payload(
                        schema_version=ANCHOR_SEARCH_SCHEMA_VERSION,
                        search_head={"algo": "sha1", "hex": commit},
                        algorithms_attempted=ANCHOR_ALGORITHMS_PHASE5,
                        results=results,
                    ),
                )
            )
        drafts.extend(anchor_drafts)
        append_event_batch(repo, drafts, writer=writer)


def _mature_patch_chunk(
    repo: Path,
    *,
    commits: list[str],
    patch_events: list,
    scratch: sqlite3.Connection,
    attribution_version: str,
    writer: str,
    deadline: float | None,
) -> MaturationSummary:
    anchors_created = 0
    searches_completed = 0
    errors: list[str] = []
    truncated = False
    chunk_patch_ids = _patch_ids_for_chunk(patch_events)
    anchor_keys, search_keys = _dedup_keys_for_chunk(scratch, chunk_patch_ids)
    # #65 cure: skip commits whose every chunk patch is ALREADY searched or
    # anchored, BEFORE the per-commit loop. reconcile_commit_anchors otherwise
    # re-pays a full `git show` + diff parse + per-patch dedup walk on every
    # already-covered commit each tick (NEW-DEDUPED-GITSHOW) — under a bounded
    # deadline that re-walk consumes the budget before any NEW commit is reached,
    # so a partially-drained backlog would still never advance. Filtering here
    # means each bounded tick spends its budget on OUTSTANDING work, which (with
    # the truncation flush above) makes the backlog drain monotonically.
    outstanding = [
        commit
        for commit in commits
        if not all(
            (patch_id, commit) in anchor_keys
            or (patch_id, commit, attribution_version) in search_keys
            for patch_id in chunk_patch_ids
        )
    ]
    for commit in outstanding:
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
                    attribution_version=attribution_version,
                    patch_events=patch_events,
                    anchor_keys=anchor_keys,
                    search_keys=search_keys,
                    summary_out=per_commit_summary,
                    deadline=deadline,
                    append_events=False,
                )
            )
            search_results = list(per_commit_summary.get("search_results") or [])
            anchor_drafts = list(per_commit_summary.get("anchor_drafts") or [])
            _store_chunk_outputs(
                scratch,
                commit,
                search_results=search_results,
                anchor_drafts=anchor_drafts,
                attribution_version=attribution_version,
            )
            searches_completed += int(
                per_commit_summary.get("searches_recorded", 0) or 0
            )
            if per_commit_summary.get("budget_exhausted"):
                truncated = True
                break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{commit}: {type(exc).__name__}: {exc}")
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
    # #65: the gate is on the quiet-tick path before mature_trails. It must not
    # retain whole-history patch/search/anchor key sets either, so it streams the
    # scoped slice into the same disk-backed scratch shape and asks SQLite for a
    # single missing (patch, commit) pair.
    with tempfile.TemporaryDirectory(prefix="opentraces-maturation-gate-") as scratch_dir:
        scratch = _open_maturation_scratch(Path(scratch_dir) / "gate.sqlite3")
        try:
            _store_candidate_commits(scratch, commits)
            patch_rows: list[tuple[str]] = []
            anchor_rows: list[tuple[str, str]] = []
            search_rows: list[tuple[str, str, str]] = []

            def _flush_gate_rows() -> None:
                nonlocal patch_rows, anchor_rows, search_rows
                if patch_rows:
                    scratch.executemany(
                        "INSERT OR IGNORE INTO patch_ids(patch_id) VALUES (?)",
                        patch_rows,
                    )
                    patch_rows = []
                if anchor_rows:
                    scratch.executemany(
                        "INSERT OR IGNORE INTO anchor_keys(patch_id, commit_sha) "
                        "VALUES (?, ?)",
                        anchor_rows,
                    )
                    anchor_rows = []
                if search_rows:
                    scratch.executemany(
                        "INSERT OR IGNORE INTO search_keys"
                        "(patch_id, commit_sha, attribution_version) "
                        "VALUES (?, ?, ?)",
                        search_rows,
                    )
                    search_rows = []

            def _gate_sink(event) -> None:
                etype = event.event_type
                if etype == "trace_patch_created":
                    trace_patch_id = id_from_payload(event.payload, "trace_patch")
                    if trace_patch_id:
                        patch_rows.append((trace_patch_id,))
                elif etype == "git_anchor_search_completed":
                    for record in iter_search_records(event):
                        patch_id = record.get("trace_patch_id")
                        commit = record.get("search_head_sha")
                        version = record.get("attribution_version")
                        if patch_id and commit and version:
                            search_rows.append((patch_id, commit, version))
                elif etype == "git_anchor_created":
                    # #23 step 4: close the gate/worker asymmetry. The worker
                    # skips a pair when EITHER a search record OR an anchor exists.
                    patch_id = id_from_payload(event.payload, "trace_patch")
                    commit = (event.payload.get("commit_id") or {}).get("hex")
                    if patch_id and commit:
                        anchor_rows.append((patch_id, commit))
                if len(patch_rows) + len(anchor_rows) + len(search_rows) >= 4096:
                    _flush_gate_rows()

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
            _flush_gate_rows()
            scratch.commit()

            if scratch.execute("SELECT 1 FROM patch_ids LIMIT 1").fetchone() is None:
                if state is not None:
                    _save_maturation_watermark(repo, state)
                return False
            has_unsearched = scratch.execute(
                """
                SELECT 1
                FROM patch_ids p
                CROSS JOIN candidate_commits c
                LEFT JOIN search_keys s
                  ON s.patch_id = p.patch_id
                 AND s.commit_sha = c.commit_sha
                 AND s.attribution_version = ?
                LEFT JOIN anchor_keys a
                  ON a.patch_id = p.patch_id
                 AND a.commit_sha = c.commit_sha
                WHERE s.patch_id IS NULL
                  AND a.patch_id IS NULL
                LIMIT 1
                """,
                (effective_version,),
            ).fetchone() is not None
        except Exception:
            return False
        finally:
            scratch.close()
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
