"""Shared enrichment + security pipeline for trace processing.

Encapsulates the 7-step pipeline used by both ``capture`` and ``parse``
commands: git signals, attribution, dependencies, metrics, security
scan/redact, classification, and path anonymization.
"""

from __future__ import annotations

import os
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
from ..security import SECURITY_VERSION
from ..security.anonymizer import anonymize_paths
from ..security.classifier import classify_trace_record
from ..security.privacy import (
    DEFAULT_PRIVACY_TIER,
    mark_record_privacy,
    privacy_policy_for_tier,
)
from ..security.scanner import apply_redactions, two_pass_scan
from ..security.scanner_trufflehog import maybe_run_trufflehog
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
        """True iff Tier 1.5 ran and flagged any secret (Plan 032 Part A)."""
        return self.trufflehog_report is not None and self.trufflehog_report.blocked


def _run_trufflehog_on_record(
    record: TraceRecord,
    cfg: Config,
    skip: bool,
) -> TruffleHogReport | None:
    """Tier 1.5 pass: scan the serialized record via TruffleHog.

    Honors both the persistent ``security.trufflehog.enabled`` config
    flag and a per-invocation ``skip`` override so callers like
    ``push --no-trufflehog`` can suppress the tier without mutating
    config.
    """
    if skip:
        return None
    if not cfg.security.trufflehog.enabled:
        return None

    # trufflehog needs a file path; serialize the record to a temp file.
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", encoding="utf-8", delete=False,
    ) as fh:
        fh.write(record.to_jsonl_line() + "\n")
        tmp_path = Path(fh.name)
    try:
        return maybe_run_trufflehog(tmp_path, cfg.security.trufflehog)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _persist_trufflehog_result(
    record: TraceRecord,
    report: TruffleHogReport | None,
) -> int:
    """Record the Tier 1.5 outcome on ``record.metadata.security``.

    Always writes a status marker when ``report`` is not ``None`` — even
    on clean scans — so the TUI can distinguish "scanned, no findings"
    from "never scanned." Returns the redaction count applied when
    findings were present (zero otherwise) so callers can fold it into
    the running total.
    """
    if report is None:
        return 0

    sec_meta = record.metadata.setdefault("security", {})
    th_redacted = 0
    if report.findings:
        from ..security.scanner import apply_trufflehog_redactions
        th_redacted = apply_trufflehog_redactions(record, report.findings)
        sec_meta["trufflehog_findings"] = [
            {
                "detector": f.detector_name,
                "verified": bool(f.verified),
                "line": f.line_number,
                "source_file": f.source_file,
            }
            for f in report.findings
        ]
        sec_meta["trufflehog_redactions_applied"] = th_redacted

    sec_meta["trufflehog"] = {
        "status": "findings" if report.findings else "clean",
        "version": report.trufflehog_version,
        "scanned_at": report.scanned_at,
        "findings_count": len(report.findings),
    }
    return th_redacted


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


def _privacy_tier_from_config(cfg: Config) -> str:
    return getattr(cfg.security, "privacy_tier", DEFAULT_PRIVACY_TIER)


def _run_privacy_pipeline(
    record: TraceRecord,
    cfg: Config,
    *,
    skip_trufflehog: bool,
    privacy_tier: str | None,
    review_on_redaction: bool,
) -> ProcessedTrace:
    """Apply the configured privacy/security policy to an enriched record."""

    policy = privacy_policy_for_tier(
        privacy_tier or _privacy_tier_from_config(cfg),
        classifier_sensitivity=cfg.classifier_sensitivity,
    )
    if not policy.filters_enabled:
        record.security.scanned = False
        record.security.redactions_applied = 0
        record.security.flags_reviewed = 0
        record.security.classifier_version = None
        mark_record_privacy(record, policy.tier, redactions_applied=0)
        return ProcessedTrace(
            record=record,
            needs_review=False,
            redaction_count=0,
            trufflehog_report=None,
        )

    pass1, pass2 = two_pass_scan(record, include_entropy=policy.include_entropy)
    redaction_count = apply_redactions(record, include_entropy=policy.include_entropy)
    record.security.scanned = True
    record.security.redactions_applied = redaction_count
    needs_review = bool(pass1.matches or pass2.matches)
    if review_on_redaction and redaction_count:
        needs_review = True

    trufflehog_report = _run_trufflehog_on_record(
        record,
        cfg,
        skip_trufflehog or not policy.run_trufflehog,
    )
    th_redacted = _persist_trufflehog_result(record, trufflehog_report)
    if th_redacted:
        record.security.redactions_applied = (
            (record.security.redactions_applied or 0) + th_redacted
        )
        redaction_count += th_redacted
        needs_review = True

    classifier_result = classify_trace_record(record, policy.classifier_sensitivity)
    record.security.flags_reviewed = len(classifier_result.flags)
    record.security.classifier_version = SECURITY_VERSION
    if classifier_result.flags:
        needs_review = True

    if policy.anonymize_sources:
        anonymize_record(record, cfg)
    mark_record_privacy(record, policy.tier, redactions_applied=redaction_count)

    return ProcessedTrace(
        record=record,
        needs_review=needs_review,
        redaction_count=redaction_count,
        trufflehog_report=trufflehog_report,
    )


def process_trace(
    record: TraceRecord,
    project_dir: Path,
    cfg: Config,
    skip_trufflehog: bool = False,
    privacy_tier: str | None = None,
) -> ProcessedTrace:
    """Run the full enrichment + security pipeline on a parsed trace.

    Steps:
        1. Git signals (VCS detection, commit check from project dir)
        2. Step-derived enrichment (attribution, deps, ecosystem, commits)
        3. Filesystem dependencies (from project directory)
        4. Metrics (from step data)
        5. Security scan + redact
        6. Classifier
        7. Path anonymization

    Returns a ProcessedTrace with the enriched record, a needs_review flag,
    and the count of redactions applied.
    """
    # 1. Git signals (project-dir-based VCS detection)
    vcs = detect_vcs(project_dir)
    record.environment.vcs = vcs
    if vcs.type == "git" and record.timestamp_start:
        ts_end = record.timestamp_end or record.timestamp_start
        outcome = check_committed(project_dir, record.timestamp_start, ts_end)
        if outcome.committed:
            record.outcome = outcome

    # 2. Step-derived enrichment (shared with imported traces)
    _enrich_from_steps(record)

    # 3. Filesystem dependencies (project-dir-based, not available for imports)
    fs_deps = extract_dependencies(str(project_dir))
    if fs_deps:
        merged = sorted(set(record.dependencies + fs_deps))
        record.dependencies = merged

    # 4. Metrics
    record.metrics = compute_metrics(record.steps)

    return _run_privacy_pipeline(
        record,
        cfg,
        skip_trufflehog=skip_trufflehog,
        privacy_tier=privacy_tier,
        review_on_redaction=True,
    )


def process_imported_trace(
    record: TraceRecord,
    cfg: Config,
    skip_trufflehog: bool = False,
    privacy_tier: str | None = None,
) -> ProcessedTrace:
    """Enrichment + security pipeline for imported traces (no project dir).

    Same security pipeline as process_trace. No VCS detection or filesystem
    dependency extraction (no local project directory for imported data).
    """
    # 1. Step-derived enrichment
    _enrich_from_steps(record)

    # 2. Metrics: only compute if parser didn't populate them (FIX-3)
    if record.metrics.total_steps == 0 and record.metrics.total_input_tokens == 0:
        record.metrics = compute_metrics(record.steps)

    return _run_privacy_pipeline(
        record,
        cfg,
        skip_trufflehog=skip_trufflehog,
        privacy_tier=privacy_tier,
        review_on_redaction=False,
    )


def anonymize_record(record: TraceRecord, cfg: Config) -> None:
    """Walk all text fields of a TraceRecord and anonymize paths in-place."""
    username = os.environ.get("USER") or os.environ.get("USERNAME") or None
    extra_usernames = cfg.custom_redact_strings or None

    def _anon(text: str | None) -> str | None:
        if not text:
            return text
        return anonymize_paths(text, username=username, extra_usernames=extra_usernames)

    # -- metadata (e.g. hyphen-encoded project path from Claude Code) --
    for k, v in list(record.metadata.items()):
        if isinstance(v, str):
            record.metadata[k] = _anon(v) or v

    # -- system_prompts (often contain cwd / absolute paths) --
    for k, v in list(record.system_prompts.items()):
        if isinstance(v, str):
            record.system_prompts[k] = _anon(v) or v

    if record.task.description:
        record.task.description = _anon(record.task.description)

    for step in record.steps:
        step.content = _anon(step.content)
        if step.reasoning_content:
            step.reasoning_content = _anon(step.reasoning_content)
        for tc in step.tool_calls:
            for k, v in list(tc.input.items()):
                if isinstance(v, str):
                    tc.input[k] = _anon(v)
        for obs in step.observations:
            obs.content = _anon(obs.content)
            obs.output_summary = _anon(obs.output_summary)
            obs.error = _anon(obs.error)
        for snip in step.snippets:
            snip.file_path = _anon(snip.file_path) or snip.file_path
            snip.text = _anon(snip.text)

    if record.outcome and record.outcome.patch:
        record.outcome.patch = _anon(record.outcome.patch)

    if record.attribution:
        for attr_file in record.attribution.files:
            attr_file.path = _anon(attr_file.path) or attr_file.path
