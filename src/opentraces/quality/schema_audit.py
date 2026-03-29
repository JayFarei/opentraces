"""Schema completeness audit for opentraces TraceRecords.

Walks every field in the TraceRecord model, computes population rates
across a batch of traces, and classifies gaps as:
- parser_bug: raw session has the data, parsed doesn't
- enrichment_gap: no enrichment module writes to this field
- schema_unrealistic: raw sessions fundamentally can't provide this
- session_dependent: varies by session characteristics
- not_yet_implemented: code path exists but isn't wired up
- needs_review: ambiguous, requires human judgment
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from opentraces_schema import TraceRecord


# ---------------------------------------------------------------------------
# Field metadata: what each field should contain and where it comes from
# ---------------------------------------------------------------------------

@dataclass
class FieldSpec:
    """Metadata about one schema field."""
    path: str
    description: str
    source: str  # "parser", "enrichment", "enrichment:git", "enrichment:metrics",
                 # "enrichment:attribution", "enrichment:dependencies", "security",
                 # "generated", "raw_session"
    expected_when: str  # "always", "git_repo", "has_edits", "has_commits",
                        # "has_subagents", "has_tools", "has_manifests", "optional"
    persona_impact: list[str]  # which personas care: training, rl, analytics, domain


# Complete field inventory with metadata
FIELD_SPECS: list[FieldSpec] = [
    # --- TraceRecord top-level ---
    FieldSpec("schema_version", "Schema version string", "generated", "always", []),
    FieldSpec("trace_id", "Random UUID", "generated", "always", []),
    FieldSpec("session_id", "Claude Code session ID", "parser", "always", []),
    FieldSpec("content_hash", "SHA-256 of record content", "generated", "always", []),
    FieldSpec("timestamp_start", "ISO 8601 start time", "parser", "always", ["analytics"]),
    FieldSpec("timestamp_end", "ISO 8601 end time", "parser", "always", ["analytics"]),

    # --- Task ---
    FieldSpec("task.description", "First user message (<=500 chars)", "parser", "always", ["training", "domain"]),
    FieldSpec("task.source", "How the task was initiated", "parser", "optional", ["domain"]),
    FieldSpec("task.repository", "owner/repo format", "enrichment:git", "git_repo", ["domain"]),
    FieldSpec("task.base_commit", "Git SHA at task start", "enrichment:git", "git_repo", ["rl"]),

    # --- Agent ---
    FieldSpec("agent.name", "Agent identifier", "parser", "always", ["domain"]),
    FieldSpec("agent.version", "CLI version string", "parser", "always", ["domain"]),
    FieldSpec("agent.model", "provider/model format", "parser", "always", ["analytics", "rl"]),

    # --- Environment ---
    FieldSpec("environment.os", "Operating system", "parser", "always", ["domain", "analytics"]),
    FieldSpec("environment.shell", "Shell type", "parser", "optional", ["domain"]),
    FieldSpec("environment.vcs.type", "git or none", "enrichment:git", "always", ["domain"]),
    FieldSpec("environment.vcs.base_commit", "HEAD SHA", "enrichment:git", "git_repo", ["rl"]),
    FieldSpec("environment.vcs.branch", "Branch name", "enrichment:git", "git_repo", ["domain"]),
    FieldSpec("environment.vcs.diff", "Unified diff", "enrichment:git", "git_repo", ["rl"]),
    FieldSpec("environment.language_ecosystem", "Detected languages", "enrichment:dependencies", "has_manifests", ["domain"]),

    # --- System prompts ---
    FieldSpec("system_prompts", "Deduplicated system prompts by hash", "parser", "optional", ["training"]),

    # --- Tool definitions ---
    FieldSpec("tool_definitions", "Tool schemas from session", "parser", "optional", ["training"]),

    # --- Steps (sampled) ---
    FieldSpec("steps", "TAO loop steps", "parser", "always", ["training", "rl", "analytics"]),
    FieldSpec("steps[].content", "Text content", "parser", "optional", ["training"]),
    FieldSpec("steps[].reasoning_content", "Chain-of-thought", "parser", "optional", ["training", "rl"]),
    FieldSpec("steps[].model", "Per-step model ID", "parser", "optional", ["analytics", "rl"]),
    FieldSpec("steps[].system_prompt_hash", "Ref into system_prompts map", "parser", "optional", ["training"]),
    FieldSpec("steps[].agent_role", "main/explore/plan", "parser", "has_subagents", ["rl"]),
    FieldSpec("steps[].parent_step", "Sub-agent hierarchy link", "parser", "has_subagents", ["rl"]),
    FieldSpec("steps[].call_type", "main/subagent/warmup", "parser", "always", ["training", "rl", "analytics"]),
    FieldSpec("steps[].subagent_trajectory_ref", "Sub-agent session ID", "parser", "has_subagents", []),
    FieldSpec("steps[].tools_available", "Tool names in this step", "parser", "has_tools", []),
    FieldSpec("steps[].timestamp", "Per-step timestamp", "parser", "always", ["analytics"]),

    # --- ToolCall (sampled) ---
    FieldSpec("steps[].tool_calls[].tool_call_id", "Unique tool call ID", "parser", "has_tools", ["training"]),
    FieldSpec("steps[].tool_calls[].tool_name", "Tool identifier", "parser", "has_tools", ["training"]),
    FieldSpec("steps[].tool_calls[].input", "Tool input dict", "parser", "has_tools", ["training"]),
    FieldSpec("steps[].tool_calls[].duration_ms", "Execution duration", "parser", "has_tools", ["analytics"]),

    # --- Observation (sampled) ---
    FieldSpec("steps[].observations[].source_call_id", "Link to tool call", "parser", "has_tools", ["training"]),
    FieldSpec("steps[].observations[].content", "Result content (<=10K)", "parser", "has_tools", ["training"]),
    FieldSpec("steps[].observations[].output_summary", "Preview (<=200 chars)", "parser", "has_tools", []),
    FieldSpec("steps[].observations[].error", "Error info", "parser", "optional", []),

    # --- Snippet (sampled) ---
    FieldSpec("steps[].snippets[].file_path", "File path", "parser", "has_tools", ["domain"]),
    FieldSpec("steps[].snippets[].language", "Detected language", "parser", "has_tools", ["domain"]),
    FieldSpec("steps[].snippets[].text", "Code content (<=5K)", "parser", "has_tools", ["training", "domain"]),
    FieldSpec("steps[].snippets[].start_line", "Start line", "parser", "has_tools", []),
    FieldSpec("steps[].snippets[].end_line", "End line", "parser", "has_tools", []),
    FieldSpec("steps[].snippets[].source_step", "Step reference", "parser", "has_tools", []),

    # --- TokenUsage (sampled across agent steps) ---
    FieldSpec("steps[].token_usage.input_tokens", "Input tokens", "parser", "always", ["analytics", "rl"]),
    FieldSpec("steps[].token_usage.output_tokens", "Output tokens", "parser", "always", ["analytics", "rl"]),
    FieldSpec("steps[].token_usage.cache_read_tokens", "Cache read tokens", "parser", "always", ["analytics"]),
    FieldSpec("steps[].token_usage.cache_write_tokens", "Cache write tokens", "parser", "optional", ["analytics"]),
    FieldSpec("steps[].token_usage.prefix_reuse_tokens", "Prefix reuse tokens", "parser", "optional", []),

    # --- Outcome ---
    FieldSpec("outcome.success", "Success boolean", "enrichment:git", "optional", ["rl"]),
    FieldSpec("outcome.signal_source", "Signal source type", "enrichment:git", "always", ["rl"]),
    FieldSpec("outcome.signal_confidence", "derived/inferred/annotated", "enrichment:git", "always", ["rl"]),
    FieldSpec("outcome.description", "Outcome description", "enrichment:git", "optional", []),
    FieldSpec("outcome.patch", "Unified diff from session", "enrichment:git", "has_commits", ["rl"]),
    FieldSpec("outcome.committed", "Whether session produced a commit", "enrichment:git", "git_repo", ["rl"]),
    FieldSpec("outcome.commit_sha", "Commit SHA", "enrichment:git", "has_commits", ["rl"]),

    # --- Attribution ---
    FieldSpec("attribution", "Agent Trace attribution block", "enrichment:attribution", "has_edits", ["domain", "rl"]),
    FieldSpec("attribution.files", "Attributed files list", "enrichment:attribution", "has_edits", ["domain"]),

    # --- Metrics ---
    FieldSpec("metrics.total_steps", "Total step count", "enrichment:metrics", "always", ["analytics"]),
    FieldSpec("metrics.total_input_tokens", "Sum of input tokens", "enrichment:metrics", "always", ["analytics"]),
    FieldSpec("metrics.total_output_tokens", "Sum of output tokens", "enrichment:metrics", "always", ["analytics"]),
    FieldSpec("metrics.total_duration_s", "Wall clock seconds", "enrichment:metrics", "always", ["analytics"]),
    FieldSpec("metrics.cache_hit_rate", "Cache hit rate [0.0, 1.0]", "enrichment:metrics", "always", ["analytics"]),
    FieldSpec("metrics.estimated_cost_usd", "Estimated cost in USD", "enrichment:metrics", "always", ["analytics", "rl"]),

    # --- SecurityMetadata ---
    FieldSpec("security.scanned", "Whether security scan was applied", "security", "always", []),
    FieldSpec("security.flags_reviewed", "Flags reviewed count", "security", "always", []),
    FieldSpec("security.redactions_applied", "Redactions applied count", "security", "always", []),
    FieldSpec("security.classifier_version", "Classifier version", "security", "optional", []),

    # --- Dependencies ---
    FieldSpec("dependencies", "Package names from manifests", "enrichment:dependencies", "has_manifests", ["domain"]),

    # --- Metadata ---
    FieldSpec("metadata", "Catch-all metadata dict", "parser", "optional", []),
]


# ---------------------------------------------------------------------------
# Field value extraction
# ---------------------------------------------------------------------------

def _is_populated(value: Any) -> bool:
    """Check if a value is meaningfully populated (not None, empty, or default-only)."""
    if value is None:
        return False
    if isinstance(value, str):
        return len(value.strip()) > 0
    if isinstance(value, (list, dict)):
        return len(value) > 0
    if isinstance(value, bool):
        return True  # bools are always "populated" (even False is meaningful)
    if isinstance(value, (int, float)):
        return True  # 0 is a valid value for counts
    return True


def _get_nested_value(obj: Any, path: str) -> Any:
    """Extract a value from a nested object using dot-notation path.

    Handles paths like "task.description", "environment.vcs.type".
    Does NOT handle array indexing (steps[] paths are handled separately).
    """
    parts = path.split(".")
    current = obj
    for part in parts:
        if current is None:
            return None
        if hasattr(current, part):
            current = getattr(current, part)
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _sample_list_field(record: TraceRecord, path: str, max_samples: int = 10) -> tuple[int, int]:
    """For array fields like steps[].content, sample items and return (populated, total).

    Returns (count_populated, count_total) across sampled items.
    Only counts items where the parent list is non-empty for nested fields.
    """
    # Parse the path: e.g. "steps[].content" -> list_attr="steps", sub_path="content"
    # Or "steps[].tool_calls[].tool_name" -> nested lists
    parts = path.split("[].")
    if len(parts) < 2:
        return 0, 0

    # Get the top-level list
    list_path = parts[0]
    remaining = "[]." .join(parts[1:])
    items = _get_nested_value(record, list_path)

    if not items or not isinstance(items, list):
        return 0, 0

    # Sample items
    sampled = items[:max_samples]
    populated = 0
    total = 0

    for item in sampled:
        # Handle nested array fields: "tool_calls[].tool_name"
        if "[]." in remaining:
            nested_parts = remaining.split("[].")
            sub_list_attr = nested_parts[0]
            leaf_path = "[]." .join(nested_parts[1:])
            sub_items = _get_nested_value(item, sub_list_attr)
            if not sub_items or not isinstance(sub_items, list) or len(sub_items) == 0:
                # Skip items with empty inner lists (don't count toward total)
                continue
            for sub_item in sub_items[:5]:
                # Handle further nesting if needed
                if "[]." in leaf_path:
                    p, t = _sample_list_field_from_item(sub_item, leaf_path)
                    populated += p
                    total += t
                else:
                    val = _get_nested_value(sub_item, leaf_path)
                    total += 1
                    if _is_populated(val):
                        populated += 1
        else:
            val = _get_nested_value(item, remaining)
            total += 1
            if _is_populated(val):
                populated += 1

    return populated, total


def _sample_list_field_from_item(item: Any, path: str) -> tuple[int, int]:
    """Helper for deeply nested list fields."""
    parts = path.split("[].")
    if len(parts) < 2:
        val = _get_nested_value(item, path)
        return (1 if _is_populated(val) else 0, 1)

    list_attr = parts[0]
    remaining = "[]." .join(parts[1:])
    sub_items = _get_nested_value(item, list_attr)
    if not sub_items or not isinstance(sub_items, list) or len(sub_items) == 0:
        return 0, 0

    populated = 0
    total = 0
    for sub in sub_items[:5]:
        val = _get_nested_value(sub, remaining)
        total += 1
        if _is_populated(val):
            populated += 1
    return populated, total


# ---------------------------------------------------------------------------
# Field check result
# ---------------------------------------------------------------------------

@dataclass
class FieldCheckResult:
    """Result of checking one field across a batch of traces."""
    path: str
    description: str
    source: str
    expected_when: str
    persona_impact: list[str]
    population_rate: float  # 0.0 - 1.0
    populated_count: int
    total_count: int
    classification: str  # parser_bug, enrichment_gap, schema_unrealistic,
                         # session_dependent, not_yet_implemented, needs_review, ok
    evidence: str
    recommendation: str


# ---------------------------------------------------------------------------
# Gap classification logic
# ---------------------------------------------------------------------------

# Fields known to be not yet implemented in the parser/enrichment pipeline
_NOT_YET_IMPLEMENTED = {
    "task.repository",      # Needs git remote URL extraction in enrichment
    "task.base_commit",     # Needs wiring from vcs.base_commit in enrichment
    "security.classifier_version",  # Set when classifier runs
}

# Fields that are inherently session-dependent (empty is valid for some sessions)
_SESSION_DEPENDENT = {
    "outcome.success",
    "outcome.description",
    "outcome.patch",
    "outcome.committed",
    "outcome.commit_sha",
    "attribution",
    "attribution.files",
    "steps[].reasoning_content",
    "steps[].agent_role",
    "steps[].parent_step",
    "steps[].subagent_trajectory_ref",
    "steps[].observations[].error",
    "steps[].snippets[].file_path",
    "steps[].snippets[].language",
    "steps[].snippets[].text",
    "steps[].snippets[].start_line",
    "steps[].snippets[].end_line",
    "steps[].snippets[].source_step",
    "steps[].tool_calls[].duration_ms",
    "steps[].observations[].output_summary",
}

# Fields populated by enrichment (not parser)
_ENRICHMENT_SOURCES = {
    "enrichment:git",
    "enrichment:metrics",
    "enrichment:attribution",
    "enrichment:dependencies",
}


def _classify_gap(
    spec: FieldSpec,
    population_rate: float,
    has_raw_signal: bool | None = None,
) -> tuple[str, str, str]:
    """Classify why a field is unpopulated.

    Returns (classification, evidence, recommendation).
    """
    path = spec.path

    # High population rate = no gap
    if population_rate >= 0.8:
        return "ok", f"Populated in {population_rate:.0%} of traces", ""

    # Known not-yet-implemented
    if path in _NOT_YET_IMPLEMENTED:
        return (
            "not_yet_implemented",
            f"Field {path} exists in schema but parser/enrichment does not populate it yet",
            f"Add {path} extraction to the {spec.source} pipeline",
        )

    # Session-dependent fields with partial population are expected
    if path in _SESSION_DEPENDENT:
        if spec.expected_when in ("optional", "has_edits", "has_commits", "has_subagents"):
            return (
                "session_dependent",
                f"Field {path} is populated when {spec.expected_when} "
                f"({population_rate:.0%} of sessions)",
                "No action needed, population varies by session characteristics",
            )

    # Raw session has the signal but parsed doesn't -> parser bug
    if has_raw_signal is True and population_rate < 0.2:
        return (
            "parser_bug",
            f"Raw session contains {path} data but parsed trace has it in only "
            f"{population_rate:.0%} of cases",
            f"Fix the parser to extract {path} from raw session data",
        )

    # Enrichment field that's not populated
    if spec.source in _ENRICHMENT_SOURCES and population_rate < 0.5:
        # Check if it's expected_when=always but still empty
        if spec.expected_when == "always":
            return (
                "enrichment_gap",
                f"Enrichment module {spec.source} should populate {path} "
                f"but it's only present in {population_rate:.0%} of traces",
                f"Check {spec.source} enrichment pipeline for {path}",
            )
        else:
            return (
                "session_dependent",
                f"Field {path} depends on {spec.expected_when} "
                f"({population_rate:.0%} of sessions have it)",
                "No action needed",
            )

    # Parser field that should always be populated but isn't
    if spec.source == "parser" and spec.expected_when == "always" and population_rate < 0.5:
        if has_raw_signal is None:
            # Can't cross-reference without raw data
            return (
                "needs_review",
                f"Parser field {path} expected always but only {population_rate:.0%} populated. "
                f"Cannot determine if raw data has the signal without raw session comparison",
                f"Run with raw session data to determine if this is a parser bug or schema issue",
            )
        elif has_raw_signal is False:
            return (
                "schema_unrealistic",
                f"Field {path} expected always but raw sessions don't contain this data. "
                f"Population rate: {population_rate:.0%}",
                f"Consider making {path} optional or removing from schema",
            )
        else:
            return (
                "parser_bug",
                f"Raw sessions contain {path} data but parser only extracts it "
                f"in {population_rate:.0%} of cases",
                f"Fix parser extraction for {path}",
            )

    # Conditional fields with low population
    if spec.expected_when != "always" and population_rate < 0.5:
        return (
            "session_dependent",
            f"Field {path} depends on {spec.expected_when} "
            f"({population_rate:.0%} of sessions)",
            "No action needed" if population_rate > 0 else
            f"Verify that {spec.expected_when} conditions occur in the test sessions",
        )

    # Fallback
    return (
        "needs_review",
        f"Field {path} has {population_rate:.0%} population rate, "
        f"source={spec.source}, expected_when={spec.expected_when}",
        "Manual review needed to classify this gap",
    )


# ---------------------------------------------------------------------------
# Batch audit
# ---------------------------------------------------------------------------

@dataclass
class SchemaAuditReport:
    """Complete schema audit across a batch of traces."""
    total_traces: int
    total_fields: int
    fields: list[FieldCheckResult]

    @property
    def ok_count(self) -> int:
        return sum(1 for f in self.fields if f.classification == "ok")

    @property
    def gap_count(self) -> int:
        return self.total_fields - self.ok_count

    @property
    def by_classification(self) -> dict[str, list[FieldCheckResult]]:
        result: dict[str, list[FieldCheckResult]] = {}
        for f in self.fields:
            result.setdefault(f.classification, []).append(f)
        return result

    @property
    def by_persona_impact(self) -> dict[str, list[FieldCheckResult]]:
        """Group gaps by which persona they impact."""
        result: dict[str, list[FieldCheckResult]] = {}
        for f in self.fields:
            if f.classification == "ok":
                continue
            for persona in f.persona_impact:
                result.setdefault(persona, []).append(f)
        return result


def audit_schema_completeness(
    traces: list[TraceRecord],
    raw_signal_map: dict[str, bool] | None = None,
) -> SchemaAuditReport:
    """Audit every schema field across a batch of traces.

    Args:
        traces: List of parsed TraceRecords to audit.
        raw_signal_map: Optional dict mapping field paths to whether
            the raw session data contains that signal. Built by the
            preservation comparator.

    Returns:
        SchemaAuditReport with per-field population rates and gap classifications.
    """
    if not traces:
        return SchemaAuditReport(total_traces=0, total_fields=len(FIELD_SPECS), fields=[])

    results: list[FieldCheckResult] = []

    for spec in FIELD_SPECS:
        path = spec.path

        # Determine if this is an array-sampled field
        is_array_field = "[]." in path

        if is_array_field:
            # Aggregate across all traces
            total_populated = 0
            total_checked = 0
            for record in traces:
                p, t = _sample_list_field(record, path)
                total_populated += p
                total_checked += t
            pop_rate = total_populated / max(total_checked, 1)
        else:
            # Simple field: check across all traces
            populated_count = 0
            for record in traces:
                val = _get_nested_value(record, path)
                if _is_populated(val):
                    populated_count += 1
            pop_rate = populated_count / len(traces)
            total_populated = populated_count
            total_checked = len(traces)

        # Get raw signal info if available
        has_raw = raw_signal_map.get(path) if raw_signal_map else None

        # Classify
        classification, evidence, recommendation = _classify_gap(
            spec, pop_rate, has_raw
        )

        results.append(FieldCheckResult(
            path=path,
            description=spec.description,
            source=spec.source,
            expected_when=spec.expected_when,
            persona_impact=spec.persona_impact,
            population_rate=pop_rate,
            populated_count=total_populated,
            total_count=total_checked,
            classification=classification,
            evidence=evidence,
            recommendation=recommendation,
        ))

    return SchemaAuditReport(
        total_traces=len(traces),
        total_fields=len(results),
        fields=results,
    )


def format_audit_report(report: SchemaAuditReport) -> str:
    """Format SchemaAuditReport as a markdown report section."""
    lines: list[str] = []
    lines.append("## Schema Completeness Audit")
    lines.append("")
    lines.append(f"Traces analyzed: {report.total_traces}")
    lines.append(f"Fields checked: {report.total_fields}")
    lines.append(f"Fields OK: {report.ok_count}")
    lines.append(f"Fields with gaps: {report.gap_count}")
    lines.append("")

    # Summary by classification
    by_class = report.by_classification
    for cls in ["parser_bug", "enrichment_gap", "not_yet_implemented",
                "schema_unrealistic", "session_dependent", "needs_review"]:
        items = by_class.get(cls, [])
        if items:
            lines.append(f"### {cls.replace('_', ' ').title()} ({len(items)})")
            lines.append("")
            for item in items:
                lines.append(f"**`{item.path}`** -- {item.description}")
                lines.append(f"  Population rate: {item.population_rate:.0%} "
                             f"({item.populated_count}/{item.total_count})")
                lines.append(f"  Evidence: {item.evidence}")
                if item.recommendation:
                    lines.append(f"  Recommendation: {item.recommendation}")
                if item.persona_impact:
                    lines.append(f"  Affects: {', '.join(item.persona_impact)}")
                lines.append("")

    # Impact by persona
    by_persona = report.by_persona_impact
    if by_persona:
        lines.append("### Impact by Persona")
        lines.append("")
        for persona, items in sorted(by_persona.items()):
            non_session = [i for i in items if i.classification != "session_dependent"]
            lines.append(f"- **{persona}**: {len(items)} gaps "
                         f"({len(non_session)} actionable)")
        lines.append("")

    return "\n".join(lines)
