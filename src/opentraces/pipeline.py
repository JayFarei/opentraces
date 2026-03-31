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
from .enrichment.attribution import build_attribution
from .enrichment.dependencies import (
    extract_dependencies,
    extract_dependencies_from_imports,
    extract_dependencies_from_steps,
    infer_language_ecosystem,
)
from .enrichment.git_signals import check_committed, detect_commits_from_steps, detect_vcs
from .enrichment.metrics import compute_metrics
from .security import SECURITY_VERSION
from .security.anonymizer import anonymize_paths
from .security.classifier import classify_trace_record
from .security.scanner import apply_redactions, two_pass_scan


@dataclass
class ProcessedTrace:
    """Result of running the shared pipeline on a single trace."""

    record: TraceRecord
    needs_review: bool
    redaction_count: int


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
        record.attribution = build_attribution(record.steps, patch)
    if not record.outcome.committed:
        step_outcome = detect_commits_from_steps(record.steps)
        if step_outcome.committed:
            record.outcome = step_outcome


def process_trace(
    record: TraceRecord,
    project_dir: Path,
    cfg: Config,
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

    # 5. Security scan + redact
    pass1, pass2 = two_pass_scan(record)
    redaction_count = apply_redactions(record)
    record.security.scanned = True
    record.security.redactions_applied = redaction_count
    needs_review = bool(pass1.matches or pass2.matches or redaction_count)

    # 6. Classifier
    classifier_result = classify_trace_record(record, cfg.classifier_sensitivity)
    record.security.flags_reviewed = len(classifier_result.flags)
    record.security.classifier_version = SECURITY_VERSION

    # 7. Path anonymization
    anonymize_record(record, cfg)

    return ProcessedTrace(
        record=record,
        needs_review=needs_review,
        redaction_count=redaction_count,
    )


def process_imported_trace(
    record: TraceRecord,
    cfg: Config,
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

    # 3. Security scan + redact
    pass1, pass2 = two_pass_scan(record)
    redaction_count = apply_redactions(record)
    record.security.scanned = True
    record.security.redactions_applied = redaction_count
    needs_review = bool(pass1.matches or pass2.matches or redaction_count)

    # 4. Classifier (FIX-10: set flags_reviewed)
    classifier_result = classify_trace_record(record, cfg.classifier_sensitivity)
    record.security.flags_reviewed = len(classifier_result.flags)
    record.security.classifier_version = SECURITY_VERSION

    # 5. Path anonymization
    anonymize_record(record, cfg)

    return ProcessedTrace(
        record=record,
        needs_review=needs_review,
        redaction_count=redaction_count,
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
