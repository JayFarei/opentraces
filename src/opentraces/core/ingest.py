"""Session-level ingestion core.

Converts Claude Code JSONL session files into staged ``TraceRecord``
entries in the project's local inbox. This module is the single
authoritative entry point for:

  - Per-session ingest (called by the Stop/SessionEnd hooks, the watcher
    sweep, the hidden ``ot _scan`` CLI, and ``ot init`` autoscan).
  - Project-wide scan.

Design (see discussion 2026-04-15, Phase 1 of the live-session
ingestion plan):

  * One JSONL file == one logical session, forever. ``claude --resume``
    appends in place; compaction is additive (preserves original turns);
    no sibling JSONLs ever link here.

  * Every ingest is a FULL re-derivation from offset 0 to EOF. The
    ``supersedes`` semantics are *replace*, not *stitch*: generation N
    contains the complete history plus new turns. Dataset consumers
    filter superseded trace_ids. No byte-slice bookkeeping.

  * State per session: ``observed_size`` + ``observed_mtime`` drive the
    "has it grown since last ingest?" check; ``generations`` records
    what we've captured and what supersedes what.

  * Terminal-status policy on growth:
      - INBOX/PARSED/STAGED/REVIEWING/APPROVED → refresh in place.
      - UPLOADED/REJECTED/COMMITTED/DISCARDED → open new generation,
        ``supersedes = prev.trace_id``.
      - BLOCKED → no-op (a secret in the transcript is still there).
      - FAILED → treated as terminal for safety; avoids spamming retries.
"""

from __future__ import annotations

import fcntl
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from opentraces_schema import SCHEMA_VERSION

from ..capture.claude_code.parse import ClaudeCodeParser
from ..security import SECURITY_VERSION
from .config import (
    Config,
    get_project_dir,
    get_project_state_path,
    get_project_traces_dir,
    load_config,
    load_project_config,
)
from .pipeline import process_trace
from .repo_identity import discover_claude_jsonl_corpus
from .state import (
    GenerationRecord,
    StateManager,
    TraceStatus,
)
from .workflow import decide_post_parse_status

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Status taxonomy
# --------------------------------------------------------------------------- #

# These statuses lock the current generation — a new generation is opened
# for further content. Aligned with the policy in Phase 1 design.
_TERMINAL_STATUSES: frozenset[str] = frozenset({
    TraceStatus.UPLOADED.value,
    TraceStatus.REJECTED.value,
    TraceStatus.COMMITTED.value,
    TraceStatus.FAILED.value,
})

# A generation in BLOCKED stops ingestion entirely. The secret that blocked
# it is still present in the transcript; resuming cannot untaint it. The
# user must redact + reset manually.
_BLOCKED_STATUS: str = TraceStatus.BLOCKED.value


# --------------------------------------------------------------------------- #
# Result + report types
# --------------------------------------------------------------------------- #

@dataclass
class IngestResult:
    session_id: str
    action: str  # new | refreshed | new_generation | noop | skipped | error
    trace_id: str | None = None
    supersedes: str | None = None
    supersedes_reason: str | None = None
    error: str | None = None


@dataclass
class ScanReport:
    project_dir: Path
    results: list[IngestResult] = field(default_factory=list)

    @property
    def created(self) -> int:
        return sum(1 for r in self.results if r.action == "new")

    @property
    def refreshed(self) -> int:
        return sum(1 for r in self.results if r.action == "refreshed")

    @property
    def new_generations(self) -> int:
        return sum(1 for r in self.results if r.action == "new_generation")

    @property
    def noops(self) -> int:
        return sum(1 for r in self.results if r.action == "noop")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.action == "skipped")

    @property
    def errored(self) -> int:
        return sum(1 for r in self.results if r.action == "error")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _trace_id_for(session_id: str, generation: int) -> str:
    """Mint a fresh, opaque trace_id per the schema contract.

    ``trace_id`` is an opentraces-canonical random UUID (see
    ``field_intent.yaml``), deliberately independent of any upstream-agent
    identifier. The upstream ``session_id`` lives in its own field on the
    record; do not encode it in the trace_id.

    Idempotent re-ingest does not rely on a deterministic trace_id: the
    caller guards via ``state.latest_generation(session_id)`` before
    minting, so the same transcript re-parsed still no-ops. Generation
    numbers are tracked in the session record, not in the id string.
    """
    return str(uuid.uuid4())


def _lock_path_for(project_dir: Path, session_id: str) -> Path:
    """Per-session flock path — co-located with the project state dir.

    Short-held (only during parse + staging write). Prevents the Stop
    hook subprocess and a concurrent watcher sweep from racing on the
    same session.
    """
    project_global_dir = get_project_dir(project_dir)
    lock_dir = project_global_dir / "ingest-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{session_id}.lock"


class _FileLock:
    """``with _FileLock(path): ...`` — non-blocking is NOT what we want here.

    A blocking exclusive flock keeps things correct under contention:
    the second caller simply waits for the first to finish. Short hold
    times make this cheap.
    """

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._fd: int | None = None

    def __enter__(self) -> "_FileLock":
        import os
        self._fd = os.open(str(self._lock_path),
                           os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        import os
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def _session_id_from(jsonl_path: Path) -> str:
    """Claude Code encodes session_id as the basename without suffix."""
    return jsonl_path.stem


def _has_grown(jsonl_path: Path, observed_size: int,
               observed_mtime: float) -> bool:
    """True iff the file is bigger or mtime has advanced.

    A shrink (truncation) also counts as "grown" for our purposes — we
    still need to re-derive. Shrinks are handled the same way as grows
    downstream (full reparse from offset 0).
    """
    stat = jsonl_path.stat()
    if stat.st_size != observed_size:
        return True
    if stat.st_mtime > observed_mtime:
        return True
    return False


# --------------------------------------------------------------------------- #
# Core ingest
# --------------------------------------------------------------------------- #

def ingest_one_session(
    jsonl_path: Path,
    project_dir: Path,
    *,
    reparse: bool = False,
    cfg: Config | None = None,
) -> IngestResult:
    """Ingest a single Claude Code session JSONL into the project's inbox.

    Idempotent: safe to call repeatedly against a live session. Returns
    an ``IngestResult`` describing what changed (new / refreshed /
    new_generation / noop / skipped / error).

    ``reparse=True`` forces a re-derivation even if the file hasn't
    grown. Used by the ``_scan --reparse`` CLI path and by the schema-
    bump auto-upgrade (Phase 3) — not by the default watcher tick.
    """
    session_id = _session_id_from(jsonl_path)

    try:
        with _FileLock(_lock_path_for(project_dir, session_id)):
            return _ingest_locked(jsonl_path, project_dir, session_id,
                                  reparse=reparse, cfg=cfg)
    except Exception as e:  # noqa: BLE001
        logger.exception("ingest failed for %s", jsonl_path)
        return IngestResult(
            session_id=session_id,
            action="error",
            error=f"{type(e).__name__}: {e}",
        )


def _ingest_locked(
    jsonl_path: Path,
    project_dir: Path,
    session_id: str,
    *,
    reparse: bool,
    cfg: Config | None,
) -> IngestResult:
    """Inner, flock-held ingest. Must not raise; caller wraps."""

    state = StateManager(state_path=get_project_state_path(project_dir))

    # Don't touch the disk if the source is gone — a user could have
    # rotated ~/.claude/projects/.
    if not jsonl_path.exists():
        return IngestResult(session_id=session_id, action="skipped",
                            error="source JSONL missing")

    stat = jsonl_path.stat()

    # BLOCKED terminates ingest for this session. Check the most recent
    # generation; BLOCKED earns its keep by staying sticky.
    latest_gen = state.latest_generation(session_id)
    if latest_gen is not None:
        current_status = _current_trace_status(state, latest_gen.trace_id)
        if current_status == _BLOCKED_STATUS:
            return IngestResult(
                session_id=session_id, action="noop",
                trace_id=latest_gen.trace_id,
                error="trace is blocked",
            )

    # Has it grown? (Or are we forcing a reparse?)
    prior_sess = state.get_session(session_id)
    grew = True
    if prior_sess is not None and not reparse:
        grew = _has_grown(
            jsonl_path,
            observed_size=prior_sess.observed_size,
            observed_mtime=prior_sess.observed_mtime,
        )
    if not grew and latest_gen is not None:
        return IngestResult(
            session_id=session_id, action="noop",
            trace_id=latest_gen.trace_id,
        )

    # Decide: refresh the current gen in place, or open a new one?
    open_new_gen = False
    supersedes: str | None = None
    supersedes_reason: str | None = None

    if latest_gen is None:
        # Brand new session. Generation 1.
        next_generation = 1
        trace_id = _trace_id_for(session_id, next_generation)
    else:
        current_status = _current_trace_status(state, latest_gen.trace_id)
        if current_status in _TERMINAL_STATUSES:
            open_new_gen = True
            next_generation = latest_gen.generation + 1
            supersedes = latest_gen.trace_id
            supersedes_reason = "resume"
            trace_id = _trace_id_for(session_id, next_generation)
        else:
            # Refresh in place: keep the existing canonical trace_id so the
            # record's identity is stable across re-ingest.
            next_generation = latest_gen.generation
            trace_id = latest_gen.trace_id

    # Parse. The parser returns None if the quality gate filters out the
    # session (no meaningful content yet). We treat that as a clean skip
    # and still advance the observed_* bookmark so we don't reparse this
    # exact byte-state on every tick.
    parser = ClaudeCodeParser()
    record = parser.parse_session(jsonl_path)
    if record is None:
        state.upsert_session(
            session_id=session_id,
            source_path=str(jsonl_path),
            observed_size=stat.st_size,
            observed_mtime=stat.st_mtime,
        )
        return IngestResult(
            session_id=session_id, action="skipped",
            error="below parse quality gate",
        )

    # Stamp the trace with our deterministic ID before running the
    # enrichment pipeline so downstream (staging path, security logs,
    # etc.) all see the same ID.
    record.trace_id = trace_id

    # Persist the parser's step anchors to local state. Anchors power
    # `opentraces resume --at-step` and live outside the schema because
    # they point into ~/.claude/projects/ on the capture machine.
    state.set_step_anchors(trace_id, getattr(parser, "step_anchors", {}) or {})

    resolved_cfg = cfg or load_config()
    processed = process_trace(
        record,
        project_dir,
        resolved_cfg,
        privacy_tier=_resolve_privacy_tier(project_dir, resolved_cfg),
    )
    final_record = processed.record
    # process_trace may leave trace_id unchanged, but be defensive —
    # override after the fact so the staging filename matches the
    # generation ID we just picked.
    final_record.trace_id = trace_id
    # Project the session's monotonic generation counter onto the outgoing
    # record so downstream consumers can resolve latest-per-session with a
    # single ``max(generation_index)`` pass. Source of truth is the state
    # record we are about to write below (``next_generation``).
    final_record.generation_index = next_generation

    # Trace Trails Phase 2: hook metadata is parsed before the canonical
    # trace_id exists. Emit the local event-log projection after identity and
    # generation are known. This substrate must not make normal inbox capture
    # fragile, so TrailEvent write failures are logged but non-fatal.
    try:
        from .trails import (
            emit_step_window_events_from_record,
            reconcile_watcher_observations,
        )

        emit_step_window_events_from_record(project_dir, final_record)
        reconcile_watcher_observations(project_dir)
    except Exception:
        logger.warning(
            "trace trail event emission/reconciliation failed for %s", trace_id,
            exc_info=True,
        )

    # Write the staging JSONL (idempotent overwrite).
    staging_dir = get_project_traces_dir(project_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_file = staging_dir / f"{trace_id}.jsonl"
    staging_file.write_text(final_record.to_jsonl_line() + "\n")
    from .bucket_store import (
        sync_trail_events_from_repo,
        write_raw_source_artifact,
        write_trace_record,
    )

    project_slug = get_project_dir(project_dir).name
    write_trace_record(
        final_record,
        project_slug=project_slug,
        source_layer="canonical",
        legacy_mirror=True,
    )
    try:
        write_raw_source_artifact(
            jsonl_path,
            trace_id=final_record.trace_id,
            project_slug=project_slug,
            source_kind="claude-code-session-jsonl",
            parser="claude-code",
        )
    except Exception:
        logger.warning("raw source bucket write failed for %s", trace_id, exc_info=True)
    try:
        sync_trail_events_from_repo(project_dir, repo_id=project_slug)
    except Exception:
        logger.warning("trail event bucket export failed for %s", trace_id, exc_info=True)

    # Decide the status this generation enters.
    #
    # Delegating to ``decide_post_parse_status`` keeps the auto vs review
    # policy + trufflehog-blocks-everything rules in one place (shared
    # with the ``ot parse`` path). Phase 3 will layer a staleness gate
    # on top for auto mode so freshly-touched sessions don't churn
    # straight to COMMITTED; for Phase 1 the default review policy gives
    # STAGED → visible "inbox", which is what we want.
    review_policy = _resolve_review_policy(project_dir)
    decided_status, block_reason = decide_post_parse_status(
        processed, review_policy=review_policy
    )
    if decided_status == TraceStatus.BLOCKED:
        state.block_trace(
            trace_id, reason=block_reason or "blocked",
            session_id=session_id, file_path=str(staging_file),
        )
    else:
        state.set_trace_status(
            trace_id, decided_status,
            session_id=session_id, file_path=str(staging_file),
        )
    entry_status = decided_status

    # Update the session record + generation list.
    state.upsert_session(
        session_id=session_id,
        source_path=str(jsonl_path),
        observed_size=stat.st_size,
        observed_mtime=stat.st_mtime,
    )

    if open_new_gen or latest_gen is None:
        state.append_generation(session_id, GenerationRecord(
            trace_id=trace_id,
            generation=next_generation,
            captured_size=stat.st_size,
            captured_mtime=stat.st_mtime,
            schema_version=str(SCHEMA_VERSION),
            security_version=str(SECURITY_VERSION),
            status_at_capture=entry_status.value,
            supersedes=supersedes,
            supersedes_reason=supersedes_reason,
        ))
        action = "new" if latest_gen is None else "new_generation"
    else:
        # Refresh in place — update the captured_* fields on the existing
        # generation without appending.
        _update_latest_generation(
            state, session_id,
            captured_size=stat.st_size,
            captured_mtime=stat.st_mtime,
            schema_version=str(SCHEMA_VERSION),
            security_version=str(SECURITY_VERSION),
            status_at_capture=entry_status.value,
        )
        action = "refreshed"

    return IngestResult(
        session_id=session_id,
        action=action,
        trace_id=trace_id,
        supersedes=supersedes,
        supersedes_reason=supersedes_reason,
    )


# --------------------------------------------------------------------------- #
# Generation refresh helper (kept at module scope for clarity)
# --------------------------------------------------------------------------- #

def _update_latest_generation(
    state: StateManager, session_id: str, **fields: object
) -> None:
    """Mutate the latest generation's record in place, then persist."""
    raw = state._state["sessions"][session_id]  # noqa: SLF001 — internal
    gens = raw.get("generations") or []
    if not gens:
        raise RuntimeError(
            f"update_latest_generation on session with no gens: {session_id}"
        )
    gens[-1].update(fields)
    state.save()


def _current_trace_status(state: StateManager, trace_id: str) -> str | None:
    entry = state.get_trace(trace_id)
    return entry.status if entry is not None else None


def _resolve_review_policy(project_dir: Path) -> str:
    """Read the project's review_policy from its ``.opentraces.json``.

    Defaults to ``"review"`` on any error — the safer side (traces need
    explicit approval before push).
    """
    try:
        data = load_project_config(project_dir)
        return data.get("review_policy") or "review"
    except Exception:  # noqa: BLE001
        return "review"


def _resolve_privacy_tier(project_dir: Path, cfg: Config) -> str:
    """Read the project's privacy tier, falling back to global config."""

    try:
        data = load_project_config(project_dir)
        return data.get("privacy_tier") or cfg.security.privacy_tier
    except Exception:  # noqa: BLE001
        return cfg.security.privacy_tier


# --------------------------------------------------------------------------- #
# Project-wide scan
# --------------------------------------------------------------------------- #

def scan_project(
    project_dir: Path,
    *,
    reparse: bool = False,
    paths: list[Path] | None = None,
    cfg: Config | None = None,
    on_result: Callable[[IngestResult, int, int], None] | None = None,
) -> ScanReport:
    """Scan every Claude Code JSONL associated with ``project_dir``.

    Per-session failures are isolated — one broken JSONL never aborts
    the scan. The report carries one ``IngestResult`` per session
    attempted.
    """
    project_dir = Path(project_dir).resolve()

    if paths is None:
        candidates = discover_claude_jsonl_corpus(project_dir)
    else:
        candidates = [Path(p) for p in paths]

    resolved_cfg = cfg or load_config()
    report = ScanReport(project_dir=project_dir)

    total = len(candidates)
    for idx, jsonl in enumerate(candidates, start=1):
        try:
            result = ingest_one_session(
                jsonl, project_dir, reparse=reparse, cfg=resolved_cfg
            )
        except Exception as e:  # noqa: BLE001 — ingest_one_session already
            # wraps, but keep a belt here for the discovery path.
            logger.exception("scan_project: ingest crashed for %s", jsonl)
            result = IngestResult(
                session_id=_session_id_from(jsonl),
                action="error",
                error=f"{type(e).__name__}: {e}",
            )
        report.results.append(result)
        if on_result is not None:
            try:
                on_result(result, idx, total)
            except Exception:  # pragma: no cover - callback is best-effort.
                logger.exception("scan_project progress callback failed")

    return report
