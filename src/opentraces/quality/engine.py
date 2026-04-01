"""Scoring engine that integrates schema audit, persona rubrics, and preservation.

Runs all assessment layers against a batch of traces and produces
a combined report with per-persona breakdowns, schema completeness
audit, and preservation analysis.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opentraces_schema import TraceRecord

from .types import CheckDef, CheckResult, PersonaDef, RubricItem, RubricReport
from .schema_audit import (
    SchemaAuditReport,
    audit_schema_completeness,
    format_audit_report,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Assessment result types
# ---------------------------------------------------------------------------

@dataclass
class PersonaScore:
    """Score for one persona on one trace."""
    persona_name: str
    total_score: float  # 0-100 (deterministic only, or hybrid when judge ran)
    pass_rate: float  # 0-100
    items: list[RubricItem] = field(default_factory=list)
    category_scores: dict[str, float] = field(default_factory=dict)
    skipped: bool = False  # True when all checks were N/A for this trace
    # LLM judge fields (populated when enable_judge=True)
    deterministic_score: float | None = None  # original deterministic score before blending
    judge_score: float | None = None  # 0-100, from LLM judge
    judge_result: Any = None  # JudgeResult | None


@dataclass
class TraceAssessment:
    """Full assessment of one trace across all personas."""
    trace_id: str
    session_id: str
    task_description: str
    persona_scores: dict[str, PersonaScore] = field(default_factory=dict)
    preservation: Any = None  # PreservationReport | None
    overall_utility: float = 0.0


@dataclass
class BatchAssessment:
    """Aggregated assessment across a batch of traces."""
    assessments: list[TraceAssessment] = field(default_factory=list)
    schema_audit: SchemaAuditReport | None = None
    persona_averages: dict[str, float] = field(default_factory=dict)
    preservation_average: float | None = None


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

def _run_persona(
    persona: PersonaDef,
    record: TraceRecord,
    raw_data: dict | None,
) -> PersonaScore:
    """Run all checks for one persona against one trace."""
    items: list[RubricItem] = []

    for check_def in persona.checks:
        try:
            result = check_def.check(record, raw_data)
            items.append(RubricItem(
                name=check_def.name,
                category=check_def.category,
                weight=check_def.weight,
                passed=result.passed,
                score=result.score,
                evidence=result.evidence,
                note=result.note,
                skipped=result.skipped,
            ))
        except Exception as e:
            logger.warning(
                "Check %s failed for trace %s: %s",
                check_def.name, record.trace_id[:8], e,
            )
            items.append(RubricItem(
                name=check_def.name,
                category=check_def.category,
                weight=check_def.weight,
                passed=False,
                score=0.0,
                evidence=f"ERROR: {e}",
                note="Check raised an exception",
            ))

    # Compute scores — skipped items are excluded from the weighted average
    active = [i for i in items if not i.skipped]
    total_weight = sum(i.weight for i in active)
    if total_weight == 0:
        # All checks were N/A for this trace — mark persona as skipped so
        # the upload gate does not treat 0.0 as a failing score.
        return PersonaScore(
            persona_name=persona.name,
            total_score=0.0,
            pass_rate=0.0,
            items=items,
            category_scores={},
            skipped=True,
        )

    total_score = round(
        sum(i.score * i.weight for i in active) / total_weight * 100, 1
    )

    pass_count = sum(1 for i in active if i.passed)
    pass_rate = round(pass_count / max(len(active), 1) * 100, 1)

    # Category scores (skipped excluded)
    categories: dict[str, list[RubricItem]] = {}
    for item in active:
        categories.setdefault(item.category, []).append(item)
    category_scores = {
        cat: round(
            sum(i.score * i.weight for i in cat_items)
            / max(sum(i.weight for i in cat_items), 0.001) * 100, 1
        )
        for cat, cat_items in categories.items()
    }

    return PersonaScore(
        persona_name=persona.name,
        total_score=total_score,
        pass_rate=pass_rate,
        items=items,
        category_scores=category_scores,
    )


def assess_trace(
    record: TraceRecord,
    raw_session_path: Path | str | None = None,
    personas: list[PersonaDef] | None = None,
    enable_judge: bool = False,
    judge_model: str = "haiku",
    deterministic_weight: float = 0.6,
) -> TraceAssessment:
    """Assess a single trace against all personas.

    Args:
        record: Parsed TraceRecord to assess.
        raw_session_path: Path to raw session JSONL for preservation comparison.
        personas: Custom persona list. If None, uses all default personas.
        enable_judge: If True, run LLM judge for qualitative scoring.
        judge_model: Model for the judge ("haiku", "sonnet", "opus").
        deterministic_weight: Weight for deterministic score in hybrid blend
            (0.0-1.0, default 0.6). Judge weight is 1 - deterministic_weight.

    Returns:
        TraceAssessment with per-persona scores and optional preservation.
    """
    if personas is None:
        personas = _get_default_personas()

    # Serialize for checks that need the raw dict
    try:
        raw_data = json.loads(record.to_jsonl_line())
    except Exception:
        raw_data = None

    assessment = TraceAssessment(
        trace_id=record.trace_id,
        session_id=record.session_id,
        task_description=(record.task.description or "")[:100],
    )

    # Run each persona (deterministic checks)
    for persona in personas:
        score = _run_persona(persona, record, raw_data)
        assessment.persona_scores[persona.name] = score

    # LLM judge pass (optional)
    if enable_judge:
        try:
            from .judge import run_judge, summarize_for_judge

            # Collect deterministic issues for judge context
            det_issues = []
            for name, ps in assessment.persona_scores.items():
                if name == "conformance":
                    continue
                for item in ps.items:
                    if not item.passed:
                        det_issues.append(f"{item.name}: {item.evidence}")

            trace_summary = summarize_for_judge(record, deterministic_issues=det_issues)

            for name, ps in assessment.persona_scores.items():
                if name == "conformance":
                    continue  # no brief for conformance
                judge_result = run_judge(name, trace_summary, model=judge_model)
                ps.judge_result = judge_result

                if not judge_result.skipped:
                    ps.deterministic_score = ps.total_score
                    ps.judge_score = judge_result.overall_score
                    # Hybrid blend
                    ps.total_score = round(
                        deterministic_weight * ps.deterministic_score
                        + (1 - deterministic_weight) * ps.judge_score,
                        1,
                    )
        except Exception as e:
            logger.warning("Judge pass failed: %s", e)

    # Preservation comparison
    if raw_session_path is not None:
        try:
            from .raw_reader import read_raw_session
            from .preservation import compare_preservation

            raw_summary = read_raw_session(Path(raw_session_path))
            assessment.preservation = compare_preservation(record, raw_summary)
        except Exception as e:
            logger.warning("Preservation comparison failed: %s", e)

    # Overall utility: weighted average of non-skipped persona scores
    if assessment.persona_scores:
        active_scores = [ps.total_score for ps in assessment.persona_scores.values() if not ps.skipped]
        if active_scores:
            assessment.overall_utility = round(sum(active_scores) / len(active_scores), 1)

    return assessment


def assess_batch(
    traces: list[TraceRecord],
    raw_session_dir: Path | str | None = None,
    personas: list[PersonaDef] | None = None,
    enable_judge: bool = False,
    judge_model: str = "haiku",
) -> BatchAssessment:
    """Assess a batch of traces with all layers.

    Args:
        traces: List of parsed TraceRecords.
        raw_session_dir: Directory containing raw session JSONL files.
            Files are matched to traces via session_id.
        personas: Custom persona list. If None, uses all defaults.
        enable_judge: If True, run LLM judge for qualitative scoring.
        judge_model: Model for the judge ("haiku", "sonnet", "opus").

    Returns:
        BatchAssessment with per-trace assessments, schema audit,
        and aggregated statistics.
    """
    if personas is None:
        personas = _get_default_personas()

    batch = BatchAssessment()

    # Build session_id -> raw file path mapping
    raw_path_map: dict[str, Path] = {}
    if raw_session_dir is not None:
        raw_dir = Path(raw_session_dir)
        if raw_dir.exists():
            for jsonl_file in raw_dir.glob("*.jsonl"):
                # Session ID is the file stem
                raw_path_map[jsonl_file.stem] = jsonl_file

    # Assess each trace
    for record in traces:
        raw_path = raw_path_map.get(record.session_id)
        assessment = assess_trace(
            record, raw_path, personas,
            enable_judge=enable_judge, judge_model=judge_model,
        )
        batch.assessments.append(assessment)

    # Schema completeness audit across the full batch
    batch.schema_audit = audit_schema_completeness(traces)

    # Compute averages
    if batch.assessments:
        # Per-persona averages — exclude skipped scores so N/A traces
        # don't drag a persona's average to 0 in the upload gate.
        persona_totals: dict[str, list[float]] = {}
        for a in batch.assessments:
            for name, ps in a.persona_scores.items():
                if not ps.skipped:
                    persona_totals.setdefault(name, []).append(ps.total_score)
        batch.persona_averages = {
            name: round(sum(scores) / len(scores), 1)
            for name, scores in persona_totals.items()
        }

        # Preservation average
        pres_scores = [
            a.preservation.overall
            for a in batch.assessments
            if a.preservation is not None
        ]
        if pres_scores:
            batch.preservation_average = round(
                sum(pres_scores) / len(pres_scores), 2
            )

    return batch


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(batch: BatchAssessment) -> str:
    """Generate a comprehensive markdown report from a batch assessment.

    Includes:
    - Summary table with per-trace persona scores
    - Schema completeness audit section
    - Per-persona breakdowns with failing items
    - Preservation analysis
    - Recommendations
    """
    lines: list[str] = []
    lines.append("# Trace Quality Assessment Report")
    lines.append("")
    lines.append(f"Traces analyzed: {len(batch.assessments)}")
    lines.append("")

    # ===== Summary table =====
    lines.append("## Summary")
    lines.append("")

    if batch.assessments:
        # Header
        persona_names = list(batch.persona_averages.keys()) if batch.persona_averages else []
        header = "| Trace | Task |"
        separator = "|-------|------|"
        for name in persona_names:
            header += f" {name} |"
            separator += "------|"
        if batch.preservation_average is not None:
            header += " Preservation |"
            separator += "------------|"
        lines.append(header)
        lines.append(separator)

        # Rows
        for a in batch.assessments:
            row = f"| {a.trace_id[:8]}... | {a.task_description[:40]} |"
            for name in persona_names:
                ps = a.persona_scores.get(name)
                score = f"{ps.total_score:.0f}%" if ps else "N/A"
                row += f" {score} |"
            if batch.preservation_average is not None:
                pres = a.preservation
                pres_str = f"{pres.overall:.0%}" if pres else "N/A"
                row += f" {pres_str} |"
            lines.append(row)

        lines.append("")

        # Averages
        lines.append("### Averages")
        lines.append("")
        for name, avg in batch.persona_averages.items():
            lines.append(f"- **{name}**: {avg:.1f}%")
        if batch.preservation_average is not None:
            lines.append(f"- **Preservation**: {batch.preservation_average:.0%}")
        lines.append("")

    # ===== Schema completeness audit =====
    if batch.schema_audit:
        lines.append(format_audit_report(batch.schema_audit))
        lines.append("")

    # ===== Per-persona detail =====
    lines.append("## Persona Detail")
    lines.append("")

    for a in batch.assessments:
        lines.append(f"### Trace: {a.trace_id[:12]}...")
        lines.append(f"Task: {a.task_description}")
        lines.append("")

        for name, ps in a.persona_scores.items():
            score_parts = f"**{name}**: {ps.total_score:.0f}%"
            if ps.deterministic_score is not None and ps.judge_score is not None:
                score_parts += (
                    f" (deterministic: {ps.deterministic_score:.0f}%, "
                    f"judge: {ps.judge_score:.0f}%)"
                )
            else:
                score_parts += f" (pass rate: {ps.pass_rate:.0f}%)"
            lines.append(score_parts)

            # Show failing items
            failures = [i for i in ps.items if not i.passed]
            if failures:
                for item in failures:
                    lines.append(f"  - FAIL: {item.name} ({item.evidence})")
                    if item.note:
                        lines.append(f"    Note: {item.note}")

            # Show judge dimensions when available
            if ps.judge_result is not None and not ps.judge_result.skipped:
                lines.append(f"  Judge ({ps.judge_result.model_used}):")
                for dim in ps.judge_result.dimensions:
                    lines.append(
                        f"    {dim.name}: {dim.score:.0f}/5 - {dim.rationale}"
                    )

            lines.append("")

    # ===== Preservation analysis =====
    pres_traces = [
        a for a in batch.assessments if a.preservation is not None
    ]
    if pres_traces:
        lines.append("## Preservation Analysis")
        lines.append("")
        for a in pres_traces:
            pres = a.preservation
            lines.append(f"### Trace: {a.trace_id[:12]}...")
            lines.append(f"Overall: {pres.overall:.0%}")
            lines.append("")

            # Ratios
            for cat, ratio in sorted(pres.ratios.items()):
                status = "ok" if ratio >= 0.9 else "LOW" if ratio < 0.7 else "warn"
                lines.append(f"  {cat}: {ratio:.0%} [{status}]")
            lines.append("")

            # Signal losses
            if pres.signal_losses:
                lines.append("**Signal losses:**")
                for loss in pres.signal_losses:
                    lines.append(
                        f"  - {loss.category}: {loss.description} "
                        f"(raw={loss.raw_count}, parsed={loss.parsed_count})"
                    )
                lines.append("")

            # Impossible signals
            if pres.impossible_signals:
                lines.append("**Impossible signals (schema claims raw can't support):**")
                for sig in pres.impossible_signals:
                    lines.append(f"  - {sig}")
                lines.append("")

    # ===== Recommendations =====
    lines.append("## Recommendations")
    lines.append("")

    if batch.schema_audit:
        by_class = batch.schema_audit.by_classification
        for cls in ["parser_bug", "enrichment_gap", "not_yet_implemented"]:
            items = by_class.get(cls, [])
            if items:
                lines.append(f"### {cls.replace('_', ' ').title()}")
                for item in items:
                    if item.recommendation:
                        lines.append(f"- `{item.path}`: {item.recommendation}")
                lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Multi-project discovery and evaluation
# ---------------------------------------------------------------------------

@dataclass
class ProjectInfo:
    """Info about one Claude Code project directory."""
    name: str
    path: Path
    session_count: int


@dataclass
class ProjectCohort:
    """Assessment results for one project."""
    project: ProjectInfo
    batch: BatchAssessment
    traces_parsed: int
    traces_failed: int


@dataclass
class MultiProjectAssessment:
    """Aggregated assessment across multiple projects."""
    cohorts: list[ProjectCohort] = field(default_factory=list)
    cross_project_audit: SchemaAuditReport | None = None
    total_sessions_scanned: int = 0
    total_traces_parsed: int = 0
    total_traces_failed: int = 0

    @property
    def cross_project_averages(self) -> dict[str, float]:
        """Average persona scores across all projects."""
        totals: dict[str, list[float]] = {}
        for cohort in self.cohorts:
            for name, avg in cohort.batch.persona_averages.items():
                totals.setdefault(name, []).append(avg)
        return {
            name: round(sum(scores) / len(scores), 1)
            for name, scores in totals.items()
        }


def discover_projects(
    base_dir: Path | str | None = None,
) -> list[ProjectInfo]:
    """Discover Claude Code projects with session JSONL files.

    Args:
        base_dir: Directory to scan. Defaults to ~/.claude/projects/.

    Returns:
        List of ProjectInfo sorted by session count (descending).
    """
    if base_dir is None:
        base_dir = Path.home() / ".claude" / "projects"
    else:
        base_dir = Path(base_dir)

    if not base_dir.exists():
        return []

    projects: list[ProjectInfo] = []
    for entry in base_dir.iterdir():
        if not entry.is_dir():
            continue
        sessions = list(entry.glob("*.jsonl"))
        if sessions:
            projects.append(ProjectInfo(
                name=entry.name,
                path=entry,
                session_count=len(sessions),
            ))

    return sorted(projects, key=lambda p: p.session_count, reverse=True)


def assess_multi_project(
    projects: list[ProjectInfo],
    max_per_project: int = 5,
    max_total: int = 100,
    personas: list[PersonaDef] | None = None,
) -> MultiProjectAssessment:
    """Assess traces from multiple projects.

    Samples sessions from each project, parses them (parser-only,
    no git enrichment since we lack project directories), and runs
    the full quality harness.

    Args:
        projects: List of projects to assess.
        max_per_project: Max sessions to sample per project.
        max_total: Total cap across all projects.
        personas: Custom persona list. If None, uses defaults.
    """
    from ..parsers.claude_code import ClaudeCodeParser
    from ..enrichment.metrics import compute_metrics
    from ..enrichment.attribution import build_attribution
    from ..enrichment.git_signals import detect_commits_from_steps
    from ..enrichment.dependencies import (
        infer_language_ecosystem,
        extract_dependencies_from_imports,
        extract_dependencies_from_steps,
    )

    if personas is None:
        personas = _get_default_personas()

    parser = ClaudeCodeParser()
    result = MultiProjectAssessment()
    all_traces: list[TraceRecord] = []
    total_sampled = 0

    for project in projects:
        if total_sampled >= max_total:
            break

        # Sample sessions (most recent first by mtime)
        sessions = sorted(
            project.path.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        budget = min(max_per_project, max_total - total_sampled)
        sampled = sessions[:budget]
        total_sampled += len(sampled)
        result.total_sessions_scanned += len(sampled)

        # Parse
        traces: list[TraceRecord] = []
        failed = 0
        for session_path in sampled:
            try:
                record = parser.parse_session(session_path)
                if record is None:
                    failed += 1
                    continue
                # Parser-only enrichment (no project directory needed)
                record.metrics = compute_metrics(record.steps)
                record.environment.language_ecosystem = infer_language_ecosystem(record.steps)
                # Infer project name from cwd for internal package filtering
                cwd = record.metadata.get("cwd", "")
                proj_basename = cwd.rstrip("/").rsplit("/", 1)[-1] if cwd else None
                step_deps = extract_dependencies_from_steps(record.steps)
                import_deps = extract_dependencies_from_imports(
                    record.steps, project_name=proj_basename,
                )
                record.dependencies = sorted(set(step_deps + import_deps))
                record.attribution = build_attribution(record.steps)
                # Detect commits from Bash tool calls in the session itself
                step_outcome = detect_commits_from_steps(record.steps)
                if step_outcome.committed:
                    record.outcome = step_outcome
                record.content_hash = record.compute_content_hash()
                traces.append(record)
            except Exception as e:
                logger.warning(
                    "Failed to parse %s/%s: %s",
                    project.name[:30], session_path.name, e,
                )
                failed += 1

        if not traces:
            result.total_traces_failed += failed
            continue

        # Assess batch for this project
        batch = BatchAssessment()
        for record in traces:
            raw_path = project.path / f"{record.session_id}.jsonl"
            assessment = assess_trace(
                record,
                raw_session_path=raw_path if raw_path.exists() else None,
                personas=personas,
            )
            batch.assessments.append(assessment)

        # Schema audit per project
        batch.schema_audit = audit_schema_completeness(traces)

        # Compute per-project averages
        if batch.assessments:
            persona_totals: dict[str, list[float]] = {}
            for a in batch.assessments:
                for name, ps in a.persona_scores.items():
                    persona_totals.setdefault(name, []).append(ps.total_score)
            batch.persona_averages = {
                name: round(sum(scores) / len(scores), 1)
                for name, scores in persona_totals.items()
            }
            pres_scores = [
                a.preservation.overall
                for a in batch.assessments
                if a.preservation is not None
            ]
            if pres_scores:
                batch.preservation_average = round(
                    sum(pres_scores) / len(pres_scores), 2
                )

        cohort = ProjectCohort(
            project=project,
            batch=batch,
            traces_parsed=len(traces),
            traces_failed=failed,
        )
        result.cohorts.append(cohort)
        result.total_traces_parsed += len(traces)
        result.total_traces_failed += failed
        all_traces.extend(traces)

    # Cross-project schema audit
    if all_traces:
        result.cross_project_audit = audit_schema_completeness(all_traces)

    return result


def generate_multi_project_report(
    assessment: MultiProjectAssessment,
    failure_analysis: str | None = None,
    persona_analyses: dict[str, str] | None = None,
) -> str:
    """Generate a markdown report for multi-project evaluation.

    Args:
        assessment: The multi-project assessment results.
        failure_analysis: Optional markdown section analyzing parse failures.
        persona_analyses: Optional dict of persona_name -> markdown analysis.
    """
    lines: list[str] = []
    lines.append("# Multi-Project Trace Quality Evaluation")
    lines.append("")
    lines.append(f"Projects evaluated: {len(assessment.cohorts)}")
    lines.append(f"Sessions scanned: {assessment.total_sessions_scanned}")
    lines.append(f"Traces parsed: {assessment.total_traces_parsed}")
    lines.append(f"Traces failed: {assessment.total_traces_failed}")
    parse_rate = (
        assessment.total_traces_parsed
        / max(assessment.total_sessions_scanned, 1)
    )
    lines.append(f"Parse success rate: {parse_rate:.0%}")
    lines.append("")

    # Cross-project averages
    cross_avg = assessment.cross_project_averages
    if cross_avg:
        lines.append("## Cross-Project Averages")
        lines.append("")
        for name, avg in cross_avg.items():
            lines.append(f"- **{name}**: {avg:.1f}%")
        lines.append("")

    # Per-project cohort table
    lines.append("## Per-Project Results")
    lines.append("")
    persona_names = list(cross_avg.keys()) if cross_avg else []
    header = "| Project | Sessions | Parsed |"
    separator = "|---------|----------|--------|"
    for name in persona_names:
        short = name[:6]
        header += f" {short} |"
        separator += "------|"
    header += " Pres |"
    separator += "------|"
    lines.append(header)
    lines.append(separator)

    for cohort in assessment.cohorts:
        proj_name = cohort.project.name
        # Trim the hyphen-encoded home prefix for readability
        home_prefix = f"-Users-{Path.home().name}-"
        if proj_name.startswith(home_prefix):
            proj_name = proj_name[len(home_prefix):]
        if len(proj_name) > 40:
            proj_name = proj_name[:37] + "..."

        row = (
            f"| {proj_name} "
            f"| {cohort.project.session_count} "
            f"| {cohort.traces_parsed} |"
        )
        for name in persona_names:
            avg = cohort.batch.persona_averages.get(name, 0)
            row += f" {avg:.0f}% |"
        pres = cohort.batch.preservation_average
        row += f" {pres:.0%} |" if pres is not None else " N/A |"
        lines.append(row)

    lines.append("")

    # ===== Failure analysis =====
    if failure_analysis:
        lines.append(failure_analysis)
        lines.append("")

    # ===== Per-check breakdown across all traces =====
    lines.append("## Per-Check Breakdown")
    lines.append("")
    check_scores: dict[tuple[str, str], list[float]] = {}
    check_pass: dict[tuple[str, str], list[bool]] = {}
    check_weights: dict[tuple[str, str], float] = {}
    for cohort in assessment.cohorts:
        for a in cohort.batch.assessments:
            for pname, ps in a.persona_scores.items():
                if pname == "conformance":
                    continue
                for item in ps.items:
                    key = (pname, item.name)
                    check_scores.setdefault(key, []).append(item.score)
                    check_pass.setdefault(key, []).append(item.passed)
                    check_weights[key] = item.weight

    for persona in ["training", "rl", "analytics", "domain"]:
        lines.append(f"### {persona.title()}")
        lines.append("")
        lines.append("| Check | Avg Score | Pass Rate | Weight | Status |")
        lines.append("|-------|-----------|-----------|--------|--------|")
        checks = [
            (name, scores)
            for (p, name), scores in check_scores.items()
            if p == persona
        ]
        checks.sort(key=lambda x: sum(x[1]) / len(x[1]))
        for name, scores in checks:
            avg = sum(scores) / len(scores) * 100
            passes = check_pass.get((persona, name), [])
            pass_rate = sum(passes) / max(len(passes), 1) * 100
            weight = check_weights.get((persona, name), 0)
            if avg >= 90:
                status = "ok"
            elif avg >= 50:
                status = "WARN"
            else:
                status = "**CRITICAL**"
            lines.append(
                f"| {name} | {avg:.0f}% | {pass_rate:.0f}% "
                f"| {weight} | {status} |"
            )
        lines.append("")

    # ===== Persona improvement analyses =====
    if persona_analyses:
        lines.append("## Persona Improvement Analysis")
        lines.append("")
        lines.append(
            "Each persona below was analyzed by a dedicated agent that reviewed "
            "the check definitions, parser code, and enrichment pipeline to identify "
            "concrete improvements that would push scores toward the high 90s."
        )
        lines.append("")
        for persona_name, analysis in persona_analyses.items():
            lines.append(f"### {persona_name.title()} Persona")
            lines.append("")
            lines.append(analysis)
            lines.append("")

    # Cross-project schema audit
    if assessment.cross_project_audit:
        lines.append(format_audit_report(assessment.cross_project_audit))
        lines.append("")

    # Recommendations
    lines.append("## Cross-Project Recommendations")
    lines.append("")
    if assessment.cross_project_audit:
        by_class = assessment.cross_project_audit.by_classification
        for cls in ["parser_bug", "enrichment_gap", "not_yet_implemented"]:
            items = by_class.get(cls, [])
            if items:
                lines.append(f"### {cls.replace('_', ' ').title()}")
                for item in items:
                    if item.recommendation:
                        lines.append(
                            f"- `{item.path}` ({item.population_rate:.0%}): "
                            f"{item.recommendation}"
                        )
                lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Default personas loader
# ---------------------------------------------------------------------------

def _get_default_personas() -> list[PersonaDef]:
    """Load all default personas. Lazy import to avoid circular deps."""
    from .conformance import CONFORMANCE_PERSONA

    result = [CONFORMANCE_PERSONA]

    try:
        from .personas import ALL_PERSONAS
        result.extend(ALL_PERSONAS)
    except ImportError:
        logger.debug("Persona modules not yet available, using conformance only")

    return result
