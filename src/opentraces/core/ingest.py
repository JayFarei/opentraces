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


class _LockHeld(Exception):
    """Raised when a non-blocking ``_FileLock`` can't be acquired."""


class _FileLock:
    """``with _FileLock(path): ...`` — blocking exclusive flock by default.

    A blocking flock keeps things correct under contention: the second
    caller waits for the first to finish. That relies on short hold times.
    Pass ``blocking=False`` for the fire-and-forget path so a stuck or
    slow holder can't make followers pile up — a non-acquired follower
    raises ``_LockHeld`` and the caller skips (ingest is idempotent and the
    watcher sweep is the safety net).
    """

    def __init__(self, lock_path: Path, *, blocking: bool = True) -> None:
        self._lock_path = lock_path
        self._blocking = blocking
        self._fd: int | None = None

    def __enter__(self) -> "_FileLock":
        import os
        self._fd = os.open(str(self._lock_path),
                           os.O_CREAT | os.O_RDWR, 0o644)
        flags = fcntl.LOCK_EX if self._blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(self._fd, flags)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            raise _LockHeld(str(self._lock_path)) from exc
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
    wait_for_lock: bool = True,
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
        with _FileLock(_lock_path_for(project_dir, session_id),
                       blocking=wait_for_lock):
            return _ingest_locked(jsonl_path, project_dir, session_id,
                                  reparse=reparse, cfg=cfg)
    except _LockHeld:
        # Another ingest already owns this session; it (or the watcher
        # sweep) will cover the work. Skip rather than stack behind it.
        return IngestResult(
            session_id=session_id, action="skipped",
            error="another ingest holds this session lock",
        )
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

    # Plan 080 §3: backfill ``TraceRecord.patches[]`` from the canonical trail
    # event log. ``trace_patch_created`` events are the spine of truth for
    # patches; this projection lifts a curated Patch row per event onto the
    # record so consumers (datasets, viewer, blame) get the cross-substrate
    # join keys (``patch_id``, ``step_index``, ``tool_call_id``,
    # ``capture_method``) without reading the event log themselves. Full diff
    # content stays in trail.jsonl.gz. Defensive: failure here must not block
    # ingest. The post-commit hook (Track 2) sets ``Patch.anchor`` later; the
    # derive helper then projects to ``outcome`` + ``git_links``.
    if not final_record.patches:
        try:
            final_record.patches = _backfill_patches_from_trail_events(
                project_dir, final_record.trace_id, final_record.generation_index,
            )
        except Exception:
            logger.warning(
                "patches backfill from trail events failed for %s", trace_id,
                exc_info=True,
            )

    # Plan 080 Resolution C: with patches[] populated, refresh derived fields.
    # At ingest time no patch has an anchor yet (post-commit has not fired),
    # so the helper would clobber the legacy step-derived Bash-commit signal;
    # only invoke when at least one patch is anchored.
    if any(p.anchor is not None and p.anchor.found for p in final_record.patches):
        try:
            from .trace_derived import derive_outcome_and_git_links_from_patches

            derive_outcome_and_git_links_from_patches(final_record)
        except Exception:
            logger.warning(
                "derive_outcome_and_git_links_from_patches failed for %s", trace_id,
                exc_info=True,
            )

    # Context Tree substrate (plan 077): capture what the LLM saw at each
    # active-path record. Independent try block so a Context Tree failure
    # never blocks Trail event emission or normal ingest.
    try:
        from ..capture.claude_code.context_tree_capture import (
            emit_context_tree_events_from_record,
        )

        ct_summary = emit_context_tree_events_from_record(
            project_dir=project_dir,
            final_record=final_record,
            transcript_path=jsonl_path,
        )
        # R10 cross-substrate join: populate Step.context_node_id from the
        # active-path step_index -> node_id map the orchestrator returned.
        # Mutating final_record.steps in place before the staging JSONL is
        # written downstream makes the link visible to every consumer
        # without a re-parse pass.
        step_map = ct_summary.get("step_node_id_map") or {}
        if step_map:
            for step in final_record.steps:
                node_id = step_map.get(step.step_index)
                if node_id is not None:
                    step.context_node_id = node_id
        # Surface the projection summary onto the trace record so doctor
        # and the bucket manifest can report it without reading the event
        # log again. Strip the (internal) step_node_id_map first.
        public_summary = {
            k: v for k, v in ct_summary.items() if k != "step_node_id_map"
        }
        final_record.context_tree_summary = public_summary
    except Exception:
        logger.warning(
            "context tree event emission failed for %s", trace_id,
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
    # Plan 079: first-class Context Tree bucket projection. Stage 2 is
    # additive; the trail-piggyback above is intentionally not removed.
    try:
        from .bucket_store import project_context_tree_to_bucket

        project_context_tree_to_bucket(
            project_dir, project_slug=project_slug,
            trace_id=final_record.trace_id,
        )
    except Exception:
        logger.warning(
            "context tree bucket projection failed for %s", trace_id, exc_info=True
        )

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


# --------------------------------------------------------------------------- #
# Plan 080 §3 — patches[] backfill from canonical trail event log
# --------------------------------------------------------------------------- #


def _backfill_patches_from_trail_events(
    project_dir: Path,
    trace_id: str,
    generation_index: int,
) -> list:
    """Project ``trace_patch_created`` events onto a ``list[Patch]``.

    Reads the canonical TrailEvent log (plan 080's spine of truth) and lifts
    one ``Patch`` per matching event. Only the curated cross-substrate join
    keys are projected — full diff content stays in ``trail.jsonl.gz``. The
    post-commit hook (Track 2) sets ``Patch.anchor`` after Git correlation;
    at ingest time anchors are always ``None``.

    Returns an empty list when the project is not a Git worktree, when the
    event log has not been initialized yet, or when no patches belong to
    this trace generation.

    Patch ordering: by ``step_index`` ascending, then by event_sequence so
    multiple patches at the same step preserve their emission order.
    """
    from opentraces_schema import Patch

    from .trails.event_log import read_events

    try:
        events = read_events(project_dir, verify=False)
    except Exception:  # noqa: BLE001 — backfill is non-fatal at ingest time.
        logger.warning(
            "read_events failed during patches backfill for %s", trace_id,
            exc_info=True,
        )
        return []

    matched = [
        event
        for event in events
        if event.event_type == "trace_patch_created"
        and event.trace_id == trace_id
        and (event.generation_index or 0) == generation_index
    ]
    if not matched:
        return []

    # Stable ordering: step_index first (None last), then by event_sequence.
    matched.sort(key=lambda e: (
        e.step_index if e.step_index is not None else 1 << 31,
        e.event_sequence,
    ))

    patches: list = []
    seen_patch_ids: set[str] = set()
    for event in matched:
        payload = event.payload or {}
        patch_id = payload.get("trace_patch_id")
        file_path = payload.get("file_path")
        if not isinstance(patch_id, str) or not isinstance(file_path, str):
            continue
        # Dedupe by patch_id: the reconciler may re-emit a ``trace_patch_created``
        # event with extended ``capture_method`` (watcher_backstop corroboration).
        # Latest event wins — we keep the second occurrence which has the
        # superset capture_method.
        if patch_id in seen_patch_ids:
            # Replace the prior Patch with one carrying the merged capture_method.
            for i, existing in enumerate(patches):
                if existing.patch_id == patch_id:
                    patches[i] = _patch_from_event(event)
                    break
            continue
        seen_patch_ids.add(patch_id)
        patches.append(_patch_from_event(event))

    return patches


def _patch_from_event(event) -> "Patch":  # noqa: F821 — schema type imported in caller
    """Build a ``Patch`` row from a ``trace_patch_created`` event."""
    from opentraces_schema import Patch

    payload = event.payload or {}
    tool_call_id = payload.get("tool_call_id")
    snapshot_before_id = payload.get("snapshot_before_id")
    snapshot_after_id = payload.get("snapshot_after_id")
    limitations_raw = payload.get("limitations") or []
    limitations = [str(item) for item in limitations_raw if isinstance(item, str)]
    return Patch(
        patch_id=str(payload["trace_patch_id"]),
        file_path=str(payload["file_path"]),
        step_index=event.step_index,
        tool_call_id=str(tool_call_id) if isinstance(tool_call_id, (str, int)) else None,
        capture_method=list(event.capture_method or []),
        snapshot_before_id=str(snapshot_before_id) if isinstance(snapshot_before_id, str) else None,
        snapshot_after_id=str(snapshot_after_id) if isinstance(snapshot_after_id, str) else None,
        anchor=None,
        superseded_by=[],
        limitations=limitations,
    )
