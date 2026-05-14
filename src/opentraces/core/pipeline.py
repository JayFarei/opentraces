"""Shared enrichment + security pipeline for trace processing.

Encapsulates the multi-step pipeline used by both ``capture`` and ``parse``
commands:

  1. Git signals (VCS detection, commit check from project dir)
  2. Step-derived enrichment (attribution, deps, ecosystem, commits)
  3. Filesystem dependencies (from project directory)
  4. Metrics
  5. Security/privacy tools — delegated to ``opentraces.security.sanitize_record``

The security pass is intentionally thin: it resolves the enabled tool list
from ``cfg``, hands the record to ``sanitize_record``, then lifts a handful
of summary fields (``record.security``) out of the resulting
:class:`PipelineReport`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from opentraces_schema.models import TraceRecord

from .config import Config
from ..enrichment.attribution import build_attribution
from ..enrichment.dependencies import (
    extract_dependencies,
    extract_dependencies_from_imports,
    extract_dependencies_from_steps,
    infer_language_ecosystem,
)
from ..enrichment.git_signals import check_committed, detect_commits_from_steps, detect_vcs
from ..enrichment.metrics import compute_metrics
from ..security import SECURITY_VERSION, sanitize_record
from ..security.privacy import mark_record_tools_applied
from ..security.tools._registry import iter_enabled
from ..security.trufflehog import TruffleHogReport


@dataclass
class ProcessedTrace:
    """Result of running the shared pipeline on a single trace."""

    record: TraceRecord
    needs_review: bool
    redaction_count: int
    trufflehog_report: TruffleHogReport | None = None

    @property
    def trufflehog_blocked(self) -> bool:
        """True iff the TruffleHog tool ran and flagged any secret."""
        return self.trufflehog_report is not None and self.trufflehog_report.blocked


def _enrich_from_steps(
    record: TraceRecord, project_name: str | None = None,
) -> None:
    """Step-derived enrichment shared by process_trace and process_imported_trace.

    Covers: language ecosystem, dependencies (from install commands + imports),
    attribution (from Edit/Write tool calls), and commit detection (from Bash
    tool calls containing git commit).

    No project directory needed, works from step data alone.
    """
    if not record.environment.language_ecosystem:
        record.environment.language_ecosystem = infer_language_ecosystem(record.steps)
    if not record.dependencies:
        step_deps = extract_dependencies_from_steps(record.steps)
        import_deps = extract_dependencies_from_imports(
            record.steps, project_name=project_name,
        )
        record.dependencies = sorted(set(step_deps + import_deps))
    if not record.attribution:
        patch = record.outcome.patch if record.outcome else None
        meta = record.metadata or {}
        hook_git = meta.get("hook_git_final") or {}
        end_state_changed = hook_git.get("changed_paths") or None
        hook_tool_use = meta.get("hook_post_tool_use") or None
        record.attribution = build_attribution(
            record.steps,
            patch,
            trace_id=record.trace_id,
            end_state_changed_files=end_state_changed,
            hook_post_tool_use=hook_tool_use,
        )
    if not record.outcome.committed:
        step_outcome = detect_commits_from_steps(record.steps)
        if step_outcome.committed:
            # Merge commit signals into the existing outcome rather than replacing it.
            # Replacing would lose RL/runtime signals (terminal_state, reward, etc.)
            # already set by the parser for runtime traces.
            record.outcome.committed = step_outcome.committed
            record.outcome.commit_sha = step_outcome.commit_sha
            if record.outcome.success is None and step_outcome.success is not None:
                record.outcome.success = step_outcome.success
            if not record.outcome.signal_source or record.outcome.signal_source == "deterministic":
                record.outcome.signal_source = step_outcome.signal_source
                record.outcome.signal_confidence = step_outcome.signal_confidence


def _resolved_tool_names(cfg: Config, skip_trufflehog: bool) -> list[str]:
    """Resolve the tool list for one pipeline run.

    Honors per-tool ``enabled(cfg)`` plus a per-invocation
    ``skip_trufflehog`` override so callers like ``push --no-trufflehog``
    can suppress that one tool without mutating config.
    """
    names = [t.name for t in iter_enabled(cfg)]
    if skip_trufflehog and "trufflehog" in names:
        names = [n for n in names if n != "trufflehog"]
    return names


def _classifier_flag_count(report) -> int:
    for verdict in report.verdicts:
        if verdict.name == "classifier":
            flags = verdict.payload.get("flags") if verdict.payload else None
            return len(flags) if isinstance(flags, list) else 0
    return 0


def _run_privacy_pipeline(
    record: TraceRecord,
    cfg: Config,
    *,
    skip_trufflehog: bool,
    review_on_redaction: bool,
) -> ProcessedTrace:
    """Run the security tool pipeline against ``record`` in place."""

    tool_names = _resolved_tool_names(cfg, skip_trufflehog)

    if not tool_names:
        record.security.scanned = False
        record.security.redactions_applied = 0
        record.security.flags_reviewed = 0
        record.security.classifier_version = None
        mark_record_tools_applied(record, [])
        return ProcessedTrace(
            record=record,
            needs_review=False,
            redaction_count=0,
            trufflehog_report=None,
        )

    record, report = sanitize_record(record, tools=tool_names, cfg=cfg)

    record.security.scanned = True
    record.security.redactions_applied = report.redactions_applied
    record.security.flags_reviewed = _classifier_flag_count(report)
    record.security.classifier_version = SECURITY_VERSION

    th_result = report.tool_results.get("trufflehog")
    th_report: TruffleHogReport | None = th_result.payload if th_result else None

    mark_record_tools_applied(record, report.tools_applied)

    needs_review = bool(report.findings) or _classifier_flag_count(report) > 0
    if review_on_redaction and report.redactions_applied:
        needs_review = True
    if th_report and th_report.blocked:
        needs_review = True

    return ProcessedTrace(
        record=record,
        needs_review=needs_review,
        redaction_count=report.redactions_applied,
        trufflehog_report=th_report,
    )


def process_trace(
    record: TraceRecord,
    project_dir: Path,
    cfg: Config,
    skip_trufflehog: bool = False,
) -> ProcessedTrace:
    """Run the full enrichment + security pipeline on a parsed trace.

    Steps:
        1. Git signals (VCS detection, commit check from project dir)
        2. Step-derived enrichment (attribution, deps, ecosystem, commits)
        3. Filesystem dependencies (from project directory)
        4. Metrics (from step data)
        5. Security tools (delegated to ``security.sanitize_record``)
    """
    vcs = detect_vcs(project_dir)
    record.environment.vcs = vcs
    if vcs.type == "git" and record.timestamp_start:
        ts_end = record.timestamp_end or record.timestamp_start
        outcome = check_committed(project_dir, record.timestamp_start, ts_end)
        if outcome.committed:
            record.outcome = outcome

    _enrich_from_steps(record)

    fs_deps = extract_dependencies(str(project_dir))
    if fs_deps:
        merged = sorted(set(record.dependencies + fs_deps))
        record.dependencies = merged

    record.metrics = compute_metrics(record.steps)

    return _run_privacy_pipeline(
        record,
        cfg,
        skip_trufflehog=skip_trufflehog,
        review_on_redaction=True,
    )


def process_imported_trace(
    record: TraceRecord,
    cfg: Config,
    skip_trufflehog: bool = False,
) -> ProcessedTrace:
    """Enrichment + security pipeline for imported traces (no project dir).

    Same security pipeline as ``process_trace``. No VCS detection or filesystem
    dependency extraction (no local project directory for imported data).
    """
    _enrich_from_steps(record)

    if record.metrics.total_steps == 0 and record.metrics.total_input_tokens == 0:
        record.metrics = compute_metrics(record.steps)

    return _run_privacy_pipeline(
        record,
        cfg,
        skip_trufflehog=skip_trufflehog,
        review_on_redaction=False,
    )
