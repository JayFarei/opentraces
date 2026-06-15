"""Plan 058 default-inbox compatibility bridge.

Bootstraps a ``default-inbox`` local dataset on first interaction with the
``ot dataset *`` CLI surface, and migrates pre-existing per-project inbox
decisions (staged / approved / rejected / pending) into the M3
``publication_state.json`` sidecar so reviewers don't lose their work
during the inbox-first to dataset-first migration described in
``kb/plans/058-dataset-remotes-and-cli-lifecycle.md``.

The bridge is intentionally additive:

- It never deletes per-project inbox state; the legacy commands keep
  working as deprecated aliases for one minor version.
- It refuses to overwrite a hand-rolled ``default-inbox`` dataset if one
  already exists — this is a best-effort migration, not a destructive
  rebuild. Subsequent calls are idempotent no-ops.
- It only writes publication-state rows for trace ids the migrator has
  not already mirrored, so calling the bridge after a partial run picks
  up where the previous run left off.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import datasets as _datasets

logger = logging.getLogger(__name__)


DEFAULT_INBOX_NAME = "default-inbox"

DEFAULT_INBOX_DESCRIPTION = (
    "Compatibility bridge for the legacy inbox-first workflow. "
    "Auto-created by `ot dataset *` while old `ot push`/`ot add`/`ot reject` "
    "aliases redirect here for one minor version (Plan 058)."
)

DEFAULT_INBOX_WORKFLOW_SKILL = "default-inbox-bridge"
DEFAULT_INBOX_WORKFLOW_DIGEST = "sha256:default-inbox-bridge-v1"
DEFAULT_INBOX_BRIDGE_SCHEMA = "opentraces.default_inbox_bridge.v1"


# Map TraceStatus → publication-state status. Anything outside this map is
# treated as ``needs_review`` so that human eyes catch unrecognised states
# rather than silently auto-publishing them.
_TRACE_STATUS_TO_PUBLICATION = {
    "uploaded": "publishable",
    "approved": "publishable",
    "committed": "publishable",
    "rejected": "rejected",
    "blocked": "rejected",
    "staged": "needs_review",
    "reviewing": "needs_review",
    "discovered": "needs_review",
    "parsed": "needs_review",
    "failed": "needs_review",
}


@dataclass
class BridgeResult:
    """Outcome of a single bridge run, useful for diagnostics + tests."""

    created: bool
    migrated_row_count: int
    skipped_existing: bool


def ensure_default_inbox() -> _datasets.LocalDataset:
    """Return the ``default-inbox`` dataset, creating it when missing.

    Idempotent: if the dataset already exists we just hand back the
    existing :class:`LocalDataset`. Tests rely on this being safe to call
    on every invocation of every ``ot dataset *`` command.
    """

    try:
        return _datasets.load_dataset(DEFAULT_INBOX_NAME)
    except FileNotFoundError:
        pass

    return _datasets.create_dataset(
        DEFAULT_INBOX_NAME,
        description=DEFAULT_INBOX_DESCRIPTION,
        workflow_skill=DEFAULT_INBOX_WORKFLOW_SKILL,
        workflow_digest=DEFAULT_INBOX_WORKFLOW_DIGEST,
    )


def migrate_project_inbox_state(project_dir: Path | None = None) -> BridgeResult:
    """Mirror the current project's inbox state into ``default-inbox``.

    The migration is best-effort: if the project hasn't been opted in, or
    has no legacy inbox state, we do nothing. When the project has staged /
    approved / rejected / pending traces we create ``default-inbox`` on
    demand and synthesise placeholder rows in ``default-inbox/data/train.jsonl``
    (one row per trace) so the M3 publication-state sidecar can carry the
    human review decision forward.

    The placeholder rows use the same shape as ``opentraces dataset run``
    output for the bridge workflow: a ``source_trace_id`` /
    ``source_unit_id`` / ``summary`` triple. Callers can later replace
    them with the rendered trace summary once the trace-to-row exporter
    lands.
    """

    project_dir = Path(project_dir) if project_dir else Path.cwd()

    # Lazy import: importing config at module load creates a circular
    # dependency through paths.py during test isolation.
    from . import config as _config

    if not _config.project_is_opted_in(project_dir):
        return BridgeResult(
            created=_datasets.dataset_path(DEFAULT_INBOX_NAME).exists(),
            migrated_row_count=0,
            skipped_existing=False,
        )

    project_id = _config._project_id_for(project_dir)
    run_id = f"default-inbox-bridge:{project_id}"
    if _bridge_already_migrated(project_dir, run_id):
        return BridgeResult(
            created=_datasets.dataset_path(DEFAULT_INBOX_NAME).exists(),
            migrated_row_count=0,
            skipped_existing=True,
        )

    state_path = _config.get_project_state_path(project_dir)
    if not state_path.exists():
        return BridgeResult(
            created=_datasets.dataset_path(DEFAULT_INBOX_NAME).exists(),
            migrated_row_count=0,
            skipped_existing=False,
        )

    from .state import StateManager

    state = StateManager(state_path=state_path)

    candidate_rows: list[tuple[str, str]] = []
    for trace_id, raw in (state._state.get("traces") or {}).items():  # type: ignore[attr-defined]
        status_value = raw.get("status")
        target = _TRACE_STATUS_TO_PUBLICATION.get(status_value, "needs_review")
        candidate_rows.append((trace_id, target))

    if not candidate_rows:
        return BridgeResult(
            created=_datasets.dataset_path(DEFAULT_INBOX_NAME).exists(),
            migrated_row_count=0,
            skipped_existing=False,
        )

    dataset = ensure_default_inbox()

    # Build placeholder rows — the schema is the default (source_trace_id,
    # source_unit_id, summary). We use a stable run_id keyed off the
    # project so re-runs dedupe instead of appending duplicates.
    rows = [
        {
            "source_trace_id": trace_id,
            "source_unit_id": f"tu:{trace_id}:trace",
            "summary": f"Migrated inbox decision: {target}",
        }
        for trace_id, target in candidate_rows
    ]
    summary = _datasets.append_rows(DEFAULT_INBOX_NAME, rows, run_id=run_id)

    # Map the bridge's appended row_ids back to publication-state targets.
    appended_targets = dict(zip(summary.row_ids, [t for _tid, t in candidate_rows]))

    if appended_targets:
        # Group by status so we make one set_publication_review_status call
        # per target (the helper already validates row ids).
        publishable = [rid for rid, t in appended_targets.items() if t == "publishable"]
        rejected = [rid for rid, t in appended_targets.items() if t == "rejected"]
        if publishable:
            _datasets.set_publication_review_status(
                DEFAULT_INBOX_NAME,
                publishable,
                "publishable",
                reviewer="default-inbox-bridge",
            )
        if rejected:
            _datasets.set_publication_review_status(
                DEFAULT_INBOX_NAME,
                rejected,
                "rejected",
                reviewer="default-inbox-bridge",
            )

    # Even rows that map to ``needs_review`` need a publication-state
    # entry so the reviewer surfaces them; ``append_rows`` already calls
    # ``evaluate_publication_state`` for the appended ids, which puts
    # them in ``needs_review`` under the default ``review: required``
    # policy.
    _write_bridge_marker(project_dir, run_id, migrated_row_count=summary.appended_count)

    return BridgeResult(
        created=dataset.path.exists(),
        migrated_row_count=summary.appended_count,
        skipped_existing=False,
    )


def _bridge_marker_path(project_dir: Path) -> Path:
    from . import config as _config

    return _config.get_project_dir(project_dir) / "default_inbox_bridge_v1.json"


def _bridge_already_migrated(project_dir: Path, run_id: str) -> bool:
    marker_path = _bridge_marker_path(project_dir)
    if marker_path.exists():
        return True
    if _legacy_row_provenance_has_run(run_id):
        _write_bridge_marker(
            project_dir,
            run_id,
            migrated_row_count=0,
            source="row_provenance",
        )
        return True
    return False


def _legacy_row_provenance_has_run(run_id: str) -> bool:
    path = _datasets.row_provenance_path(DEFAULT_INBOX_NAME)
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if run_id not in line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and payload.get("run_id") == run_id:
                    return True
    except OSError:
        return False
    return False


def _write_bridge_marker(
    project_dir: Path,
    run_id: str,
    *,
    migrated_row_count: int,
    source: str = "migration",
) -> None:
    marker_path = _bridge_marker_path(project_dir)
    payload = {
        "schema_version": DEFAULT_INBOX_BRIDGE_SCHEMA,
        "run_id": run_id,
        "source": source,
        "migrated_row_count": migrated_row_count,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except OSError:
        logger.debug("default-inbox bridge marker write failed: %s", marker_path)


def run_bridge_once(project_dir: Path | None = None) -> BridgeResult:
    """Best-effort bridge run for use from CLI entry points.

    Wraps :func:`migrate_project_inbox_state` so a single failure mode
    (e.g. a malformed state file) doesn't blow up the whole CLI command.
    """

    try:
        return migrate_project_inbox_state(project_dir)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("default-inbox bridge skipped: %s", exc)
        return BridgeResult(
            created=_datasets.dataset_path(DEFAULT_INBOX_NAME).exists(),
            migrated_row_count=0,
            skipped_existing=True,
        )
