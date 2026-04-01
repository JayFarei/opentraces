"""Domain Sourcing persona rubric for trace quality assessment.

Evaluates traces through the lens of a Domain Sourcing consumer.
Checks focus on language ecosystem, dependencies, task descriptions,
VCS info, code snippets, attribution, and agent identity, which
enable HF dataset discovery queries like 'all Django traces with success'.
"""

from __future__ import annotations

from opentraces_schema import TraceRecord

from ..types import CheckDef, CheckResult, PersonaDef


def _d1_language_ecosystem(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """D1: language_ecosystem populated (weight 1.0).

    HF dataset filtering: 'all Python traces'.
    Skip for runtime traces with no code-writing tool calls: language ecosystem
    is inferred from file edits and imports, which are not present in action-trajectory agents.
    """
    if record.execution_context == "runtime":
        edit_tool_names = {"Edit", "Write", "edit", "write", "patch", "write_file"}
        has_code_writing = any(
            tc.tool_name in edit_tool_names
            for step in record.steps
            for tc in step.tool_calls
        )
        if not has_code_writing:
            return CheckResult(
                passed=False, score=0.0,
                evidence="N/A: runtime trace with no code-writing tool calls, language ecosystem not inferrable",
                skipped=True,
            )
    langs = record.environment.language_ecosystem
    if langs and len(langs) > 0:
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"language_ecosystem={langs}",
        )
    return CheckResult(
        passed=False, score=0.0,
        evidence="language_ecosystem is empty",
    )


def _d2_dependencies_extracted(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """D2: dependencies extracted (weight 0.9).

    At least 1 dependency when language_ecosystem is non-empty.
    """
    has_langs = bool(record.environment.language_ecosystem)
    has_deps = len(record.dependencies) > 0

    if has_deps:
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"{len(record.dependencies)} dependencies extracted",
        )

    if not has_langs:
        # No languages detected, so no deps expected
        return CheckResult(
            passed=True, score=1.0,
            evidence="No language_ecosystem, dependencies not expected",
            note="N/A when no language ecosystem detected",
        )

    return CheckResult(
        passed=False, score=0.0,
        evidence=f"language_ecosystem={record.environment.language_ecosystem} but 0 dependencies",
    )


def _d3_task_description_meaningful(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """D3: task.description meaningful > 10 chars (weight 0.8).

    HF search and human browsing require descriptive task summaries.
    """
    desc = record.task.description
    if desc and len(desc.strip()) > 10:
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"Task description ({len(desc)} chars): {desc[:80]!r}",
        )
    if desc and desc.strip():
        return CheckResult(
            passed=False, score=0.5,
            evidence=f"Task description too short ({len(desc.strip())} chars): {desc!r}",
        )
    return CheckResult(
        passed=False, score=0.0,
        evidence="task.description is empty or None",
    )


def _d4_vcs_info(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """D4: VCS info populated (weight 0.7).

    vcs.type and vcs.branch should be set.
    Skip for runtime traces: VCS enrichment requires a local project directory,
    which is not available for imported action-trajectory traces.
    """
    if record.execution_context == "runtime":
        return CheckResult(
            passed=False, score=0.0,
            evidence="N/A: runtime traces have no VCS enrichment path",
            skipped=True,
        )
    vcs = record.environment.vcs
    has_type = vcs.type and vcs.type != "none"
    has_branch = vcs.branch and vcs.branch.strip()

    if has_type and has_branch:
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"vcs.type={vcs.type!r}, vcs.branch={vcs.branch!r}",
        )

    parts = []
    score = 0.0
    if has_type:
        parts.append(f"type={vcs.type!r}")
        score += 0.5
    if has_branch:
        parts.append(f"branch={vcs.branch!r}")
        score += 0.5

    if parts:
        return CheckResult(
            passed=False, score=score,
            evidence=f"Partial VCS: {', '.join(parts)}",
        )
    return CheckResult(
        passed=False, score=0.0,
        evidence="VCS info not populated (type='none', no branch)",
    )


def _d5_snippets_with_language(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """D5: Snippets with language tags (weight 0.6).

    At least one snippet has a language tag for code-specific dataset curation.
    """
    all_snippets = []
    for step in record.steps:
        all_snippets.extend(step.snippets)

    if not all_snippets:
        return CheckResult(
            passed=False, score=0.0,
            evidence="No snippets in trace",
            skipped=True,
        )

    with_lang = sum(1 for s in all_snippets if s.language and s.language.strip())
    ratio = with_lang / len(all_snippets)
    passed = ratio >= 0.5
    return CheckResult(
        passed=passed,
        score=round(ratio, 3),
        evidence=f"{with_lang}/{len(all_snippets)} snippets have language tags",
    )


def _d6_attribution_when_edits(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """D6: Attribution present when Edit/Write tool calls exist (weight 0.5).

    Agent Trace spec bridge. Experimental in v0.1.
    """
    edit_tool_names = {"Edit", "Write", "edit", "write", "patch", "write_file"}
    has_edits = any(
        tc.tool_name in edit_tool_names
        for step in record.steps
        for tc in step.tool_calls
    )

    if not has_edits:
        return CheckResult(
            passed=True, score=1.0,
            evidence="No Edit/Write tool calls in trace",
            note="N/A when no file edits",
        )

    if record.attribution and record.attribution.files:
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"Attribution present with {len(record.attribution.files)} files",
        )

    if record.attribution:
        return CheckResult(
            passed=False, score=0.3,
            evidence="Attribution block present but no files attributed",
        )

    return CheckResult(
        passed=False, score=0.0,
        evidence="Edit/Write tool calls found but no attribution block",
    )


def _d7_agent_name_version(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """D7: Agent name + version (weight 0.5).

    Both populated for queries like 'all Claude Code v1.x traces'.
    For runtime traces: version is often unavailable from source metadata,
    so name-only passes (score=0.7) rather than failing for missing version.
    """
    has_name = bool(record.agent.name and record.agent.name.strip())
    has_version = bool(record.agent.version and record.agent.version.strip())

    if has_name and has_version:
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"agent={record.agent.name!r} v{record.agent.version!r}",
        )

    if record.execution_context == "runtime":
        if has_name:
            return CheckResult(
                passed=True, score=0.7,
                evidence=f"agent name={record.agent.name!r} (version not available from runtime source metadata)",
            )
        return CheckResult(
            passed=False, score=0.0,
            evidence="No agent name (required even for runtime traces)",
        )

    score = 0.0
    parts = []
    if has_name:
        parts.append(f"name={record.agent.name!r}")
        score += 0.5
    if has_version:
        parts.append(f"version={record.agent.version!r}")
        score += 0.5

    return CheckResult(
        passed=False,
        score=score,
        evidence=f"Partial agent identity: {', '.join(parts)}" if parts else "No agent name or version",
    )


def _d8_environment_os(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """D8: Environment OS populated (weight 0.3).

    Cross-platform analysis.
    """
    os_val = record.environment.os
    if os_val and os_val.strip() and os_val.strip() != "_NOT_YET_IMPLEMENTED":
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"environment.os={os_val!r}",
        )
    if not os_val or not os_val.strip() or os_val.strip() == "_NOT_YET_IMPLEMENTED":
        return CheckResult(
            passed=True, score=1.0,
            evidence="environment.os not yet implemented, skipped",
            note="N/A when OS detection is not yet implemented",
        )
    return CheckResult(
        passed=False, score=0.0,
        evidence="environment.os is empty or None",
    )


DOMAIN_PERSONA = PersonaDef(
    name="domain",
    description="Domain Sourcing consumer: evaluates trace utility for HF dataset discovery and domain-specific queries",
    checks=[
        CheckDef(name="D1: Language ecosystem populated", category="domain", weight=1.0, check=_d1_language_ecosystem),
        CheckDef(name="D2: Dependencies extracted", category="domain", weight=0.9, check=_d2_dependencies_extracted),
        CheckDef(name="D3: Task description meaningful", category="domain", weight=0.8, check=_d3_task_description_meaningful),
        CheckDef(name="D4: VCS info populated", category="domain", weight=0.7, check=_d4_vcs_info),
        CheckDef(name="D5: Snippets with language tags", category="domain", weight=0.6, check=_d5_snippets_with_language),
        CheckDef(name="D6: Attribution when edits exist", category="domain", weight=0.5, check=_d6_attribution_when_edits),
        CheckDef(name="D7: Agent name + version", category="domain", weight=0.5, check=_d7_agent_name_version),
        CheckDef(name="D8: Environment OS populated", category="domain", weight=0.3, check=_d8_environment_os),
    ],
)
