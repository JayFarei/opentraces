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

from opentraces_schema.models import Metrics, TraceRecord

from .config import Config
from .trace_derived import derive_outcome_and_git_links_from_patches
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
        # Plan 080 Resolution C: ``Outcome.patch`` was removed in schema 0.6.0;
        # full diff content now lives in ``trail.jsonl.gz`` (per-trace, sibling
        # of trace.json in the bucket). Phase C wires up that assembly; for now
        # ``build_attribution`` runs without an inline unified diff hint and
        # falls back to its in-memory cumulative reconstruction.
        # TODO(plan 080 Phase C): pass an assembled diff loaded from trail.jsonl.gz
        # when available so attribution ranges can hash-match against post-commit
        # blobs.
        meta = record.metadata or {}
        hook_git = meta.get("hook_git_final") or {}
        end_state_changed = hook_git.get("changed_paths") or None
        hook_tool_use = meta.get("hook_post_tool_use") or None
        record.attribution = build_attribution(
            record.steps,
            None,
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


_USAGE_METRIC_FIELDS = (
    "total_input_tokens",
    "total_output_tokens",
    "total_cache_read_tokens",
    "total_cache_creation_tokens",
)


def _has_usage_metrics(metrics: Metrics) -> bool:
    return any(getattr(metrics, field, 0) > 0 for field in _USAGE_METRIC_FIELDS)


def _fill_cache_hit_rate(metrics: Metrics) -> None:
    if metrics.cache_hit_rate is not None:
        return
    denominator = metrics.total_input_tokens + metrics.total_cache_read_tokens
    if denominator > 0:
        metrics.cache_hit_rate = round(metrics.total_cache_read_tokens / denominator, 4)


def _merge_parser_and_step_metrics(parser_metrics: Metrics, step_metrics: Metrics) -> Metrics:
    """Merge harness-level aggregate metrics with step-derived metrics.

    Claude can attach token usage to individual assistant steps, so step-derived
    totals are the source of truth there. Codex CLI and Pi currently expose
    reliable usage at the session/message layer but do not place it on Step,
    so a blind recompute from Step zeros out the parser's aggregate totals.
    """
    parser_has_usage = _has_usage_metrics(parser_metrics)
    step_has_usage = _has_usage_metrics(step_metrics)

    if step_has_usage:
        merged = step_metrics.model_copy(deep=True)
    elif parser_has_usage:
        merged = parser_metrics.model_copy(deep=True)
        merged.total_steps = step_metrics.total_steps or parser_metrics.total_steps
    else:
        merged = step_metrics.model_copy(deep=True)
        merged.total_steps = step_metrics.total_steps or parser_metrics.total_steps

    # Parser-level duration/cost often comes from the runtime's final accounting
    # event, while step-derived duration/cost can only be reconstructed.
    if parser_metrics.total_duration_s is not None:
        merged.total_duration_s = parser_metrics.total_duration_s
    elif merged.total_duration_s is None:
        merged.total_duration_s = step_metrics.total_duration_s

    if parser_metrics.estimated_cost_usd is not None:
        merged.estimated_cost_usd = parser_metrics.estimated_cost_usd
    elif merged.estimated_cost_usd is None:
        merged.estimated_cost_usd = step_metrics.estimated_cost_usd

    if merged.cache_hit_rate is None and parser_metrics.cache_hit_rate is not None:
        merged.cache_hit_rate = parser_metrics.cache_hit_rate
    _fill_cache_hit_rate(merged)
    return merged


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

    parser_metrics = record.metrics
    step_metrics = compute_metrics(record.steps, model_fallback=record.agent.model)
    record.metrics = _merge_parser_and_step_metrics(parser_metrics, step_metrics)

    # Plan 080 Resolution C: once patches[] has been populated (by the ingest
    # backfill from trail events, or by the post-commit hook on a re-process),
    # outcome.committed/commit_sha and git_links MUST track patches[].anchor.
    # We only invoke the derivation when at least one patch is anchored: at
    # ingest time anchors are None (post-commit has not fired yet) and the
    # legacy step-derived Bash-commit signal must survive. The post-commit
    # hook calls this same helper after setting anchors, so the fields
    # converge on the spine of truth once Git evidence arrives.
    if any(p.anchor is not None and p.anchor.found for p in record.patches):
        derive_outcome_and_git_links_from_patches(record)

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
        record.metrics = compute_metrics(record.steps, model_fallback=record.agent.model)

    # Plan 080 Resolution C: keep derived fields aligned with patches[].anchor
    # once any patch is anchored. Same conditional as ``process_trace``; see
    # that helper for the rationale.
    if any(p.anchor is not None and p.anchor.found for p in record.patches):
        derive_outcome_and_git_links_from_patches(record)

    return _run_privacy_pipeline(
        record,
        cfg,
        skip_trufflehog=skip_trufflehog,
        review_on_redaction=False,
    )
