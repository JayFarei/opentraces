"""LLM-based qualitative judge for trace quality assessment.

Complements deterministic persona checks with an LLM evaluator that reads
persona briefs (markdown rubric definitions) and scores traces on subjective
quality dimensions that code cannot measure.

Gracefully degrades to skipped results when no API key is available.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from opentraces_schema import TraceRecord

logger = logging.getLogger(__name__)

BRIEFS_DIR = Path(__file__).parent / "personas" / "briefs"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class JudgeDimension:
    """Score for one rubric dimension from the LLM judge."""
    name: str
    score: float  # 1-5
    rationale: str = ""


@dataclass
class JudgeResult:
    """Complete LLM judge result for one persona on one trace."""
    persona_name: str
    dimensions: list[JudgeDimension] = field(default_factory=list)
    overall_score: float = 0.0  # 0-100 (weighted, scaled from 1-5 to 0-100)
    model_used: str = ""
    skipped: bool = False
    skip_reason: str = ""


# ---------------------------------------------------------------------------
# Brief loader
# ---------------------------------------------------------------------------

@dataclass
class BriefDimension:
    """Parsed dimension from a persona brief's YAML frontmatter."""
    name: str
    weight: float
    description: str
    scoring: str


@dataclass
class PersonaBrief:
    """Parsed persona brief with dimensions and prose."""
    persona: str
    description: str
    dimensions: list[BriefDimension]
    prose: str  # markdown body after frontmatter


def load_brief(persona_name: str) -> PersonaBrief | None:
    """Load and parse a persona brief from the briefs directory."""
    brief_path = BRIEFS_DIR / f"{persona_name}.md"
    if not brief_path.exists():
        logger.warning("Brief not found: %s", brief_path)
        return None

    text = brief_path.read_text()
    parts = text.split("---", 2)
    if len(parts) < 3:
        logger.warning("Brief %s has no YAML frontmatter", persona_name)
        return None

    meta = yaml.safe_load(parts[1])
    prose = parts[2].strip()

    dims = []
    for d in meta.get("dimensions", []):
        dims.append(BriefDimension(
            name=d["name"],
            weight=d["weight"],
            description=d.get("description", ""),
            scoring=d.get("scoring", ""),
        ))

    return PersonaBrief(
        persona=meta.get("persona", persona_name),
        description=meta.get("description", ""),
        dimensions=dims,
        prose=prose,
    )


# ---------------------------------------------------------------------------
# Trace summarizer
# ---------------------------------------------------------------------------

def _truncate(text: str | None, max_len: int) -> str:
    """Truncate text, returning empty string for None."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _summarize_step(step: Any) -> dict:
    """Summarize a single step for the judge."""
    summary: dict[str, Any] = {
        "step_index": step.step_index,
        "role": step.role,
    }

    if step.call_type:
        summary["call_type"] = step.call_type
    if step.agent_role:
        summary["agent_role"] = step.agent_role

    content = _truncate(step.content, 500)
    if content:
        summary["content"] = content

    reasoning = step.reasoning_content
    if reasoning and reasoning.strip():
        if reasoning.startswith("[encrypted"):
            summary["reasoning"] = "[encrypted thinking block]"
        else:
            summary["reasoning"] = _truncate(reasoning, 300)

    if step.tool_calls:
        summary["tool_calls"] = [
            {
                "tool": tc.tool_name,
                "input_preview": _truncate(
                    json.dumps(tc.input, default=str), 200
                ),
            }
            for tc in step.tool_calls[:5]  # cap at 5 tool calls per step
        ]

    if step.observations:
        obs_summaries = []
        for obs in step.observations[:5]:
            o: dict[str, str] = {}
            if obs.error:
                o["error"] = obs.error
            if obs.output_summary:
                o["summary"] = _truncate(obs.output_summary, 200)
            elif obs.content:
                o["content_preview"] = _truncate(obs.content, 200)
            if o:
                obs_summaries.append(o)
        if obs_summaries:
            summary["observations"] = obs_summaries

    tokens = step.token_usage
    if tokens.input_tokens > 0 or tokens.output_tokens > 0:
        summary["tokens"] = {
            "input": tokens.input_tokens,
            "output": tokens.output_tokens,
            "cache_read": tokens.cache_read_tokens,
        }

    return summary


def _select_representative_steps(steps: list) -> list:
    """Select 3-5 representative agent steps for the summary."""
    agent_steps = [s for s in steps if s.role == "agent"]
    if not agent_steps:
        return []

    selected: list = []
    seen_indices: set[int] = set()

    def _add(step: Any) -> None:
        if step.step_index not in seen_indices:
            selected.append(step)
            seen_indices.add(step.step_index)

    # First agent step
    _add(agent_steps[0])

    # Step with most tool calls + reasoning
    steps_with_tools_and_reasoning = [
        s for s in agent_steps
        if s.tool_calls and s.reasoning_content and s.reasoning_content.strip()
    ]
    if steps_with_tools_and_reasoning:
        best = max(steps_with_tools_and_reasoning, key=lambda s: len(s.tool_calls))
        _add(best)

    # Step with highest tool call count
    if agent_steps:
        most_tools = max(agent_steps, key=lambda s: len(s.tool_calls))
        if most_tools.tool_calls:
            _add(most_tools)

    # Step with an error observation
    for s in agent_steps:
        if any(obs.error for obs in s.observations):
            _add(s)
            break

    # Last agent step
    _add(agent_steps[-1])

    return selected[:5]


def summarize_for_judge(
    record: TraceRecord,
    deterministic_issues: list[str] | None = None,
) -> dict[str, Any]:
    """Compress a TraceRecord into a structured summary for LLM evaluation.

    Returns a dict with consistent keys suitable for serialization into the
    judge prompt. Target size: ~2-4K tokens when serialized.

    Args:
        record: The parsed TraceRecord to summarize.
        deterministic_issues: Optional list of issues flagged by deterministic
            checks (e.g. "T6 reasoning coverage at 65%").
    """
    summary: dict[str, Any] = {}

    # Task
    summary["task_description"] = _truncate(record.task.description, 300)

    # Agent
    summary["agent"] = {
        "name": record.agent.name,
        "version": record.agent.version,
        "model": record.agent.model,
    }

    # Environment
    env: dict[str, Any] = {}
    if record.environment.language_ecosystem:
        env["language_ecosystem"] = record.environment.language_ecosystem
    if record.environment.vcs.type != "none":
        env["vcs"] = {
            "type": record.environment.vcs.type,
            "branch": record.environment.vcs.branch,
        }
    if record.dependencies:
        env["dependencies"] = record.dependencies[:20]  # cap
    if env:
        summary["environment"] = env

    # Step overview
    all_steps = record.steps
    agent_steps = [s for s in all_steps if s.role == "agent"]
    user_steps = [s for s in all_steps if s.role == "user"]
    summary["step_overview"] = {
        "total": len(all_steps),
        "agent": len(agent_steps),
        "user": len(user_steps),
        "with_tool_calls": sum(1 for s in all_steps if s.tool_calls),
        "with_reasoning": sum(
            1 for s in agent_steps
            if s.reasoning_content and s.reasoning_content.strip()
            and not s.reasoning_content.startswith("[encrypted")
        ),
        "with_encrypted_reasoning": sum(
            1 for s in agent_steps
            if s.reasoning_content and s.reasoning_content.startswith("[encrypted")
        ),
        "subagent_steps": sum(1 for s in all_steps if s.call_type == "subagent"),
        "warmup_steps": sum(1 for s in all_steps if s.call_type == "warmup"),
    }

    # First user message
    if user_steps:
        summary["first_user_message"] = _truncate(user_steps[0].content, 500)

    # Representative agent steps
    rep_steps = _select_representative_steps(all_steps)
    if rep_steps:
        summary["representative_steps"] = [_summarize_step(s) for s in rep_steps]

    # Outcome
    outcome = record.outcome
    summary["outcome"] = {
        "success": outcome.success,
        "committed": outcome.committed,
        "signal_confidence": outcome.signal_confidence,
        "signal_source": outcome.signal_source,
    }
    if outcome.commit_sha:
        summary["outcome"]["commit_sha"] = outcome.commit_sha
    if outcome.description:
        summary["outcome"]["description"] = _truncate(outcome.description, 200)
    if outcome.patch:
        summary["outcome"]["patch_preview"] = _truncate(outcome.patch, 500)

    # Metrics
    metrics = record.metrics
    summary["metrics"] = {
        "total_steps": metrics.total_steps,
        "total_input_tokens": metrics.total_input_tokens,
        "total_output_tokens": metrics.total_output_tokens,
        "total_duration_s": metrics.total_duration_s,
        "cache_hit_rate": metrics.cache_hit_rate,
        "estimated_cost_usd": metrics.estimated_cost_usd,
    }

    # Security
    summary["security_scanned"] = record.security.scanned

    # Attribution
    if record.attribution and record.attribution.files:
        summary["attribution"] = {
            "files_count": len(record.attribution.files),
            "files": [f.path for f in record.attribution.files[:10]],
        }

    # Deterministic check issues
    if deterministic_issues:
        summary["deterministic_issues"] = deterministic_issues

    return summary


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

def _build_judge_prompt(brief: PersonaBrief, trace_summary: dict) -> tuple[str, str]:
    """Build system and user prompts for the judge.

    Returns (system_prompt, user_prompt).
    """
    # System prompt: persona brief + scoring instructions
    dim_instructions = []
    for d in brief.dimensions:
        dim_instructions.append(
            f"- **{d.name}** (weight {d.weight}): {d.description}\n"
            f"  Scoring guide: {d.scoring}"
        )

    system_prompt = f"""You are a quality evaluator for agent traces, assessing from the perspective described below.

## Your Persona

{brief.description}

{brief.prose}

## Scoring Dimensions

For each dimension, assign a score from 1 to 5 based on the scoring guide:

{"".join(dim_instructions)}

## Response Format

Respond with ONLY a JSON object, no other text. The JSON must have this exact structure:

{{
  "dimensions": [
    {{"name": "<dimension_name>", "score": <1-5>, "rationale": "<1-2 sentence explanation>"}}
  ]
}}

Include ALL {len(brief.dimensions)} dimensions in the order listed above. Scores must be integers 1-5."""

    # User prompt: trace summary
    user_prompt = (
        "Evaluate the following agent trace:\n\n"
        f"```json\n{json.dumps(trace_summary, indent=2, default=str)}\n```"
    )

    return system_prompt, user_prompt


def _parse_judge_response(
    response_text: str,
    brief: PersonaBrief,
) -> list[JudgeDimension]:
    """Parse the LLM judge's JSON response into JudgeDimension list."""
    # Extract JSON from response (handle markdown code blocks)
    text = response_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (```json and ```)
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    data = json.loads(text)

    dims_data = data.get("dimensions", [])
    expected_names = {d.name for d in brief.dimensions}

    dimensions = []
    for d in dims_data:
        name = d.get("name", "")
        if name not in expected_names:
            continue
        score = d.get("score", 3)
        # Clamp to 1-5
        score = max(1, min(5, int(score)))
        dimensions.append(JudgeDimension(
            name=name,
            score=float(score),
            rationale=d.get("rationale", ""),
        ))

    # Fill missing dimensions with score 3 (neutral)
    found_names = {d.name for d in dimensions}
    for bd in brief.dimensions:
        if bd.name not in found_names:
            dimensions.append(JudgeDimension(
                name=bd.name,
                score=3.0,
                rationale="Dimension not scored by judge, defaulting to neutral.",
            ))

    return dimensions


def _compute_judge_overall(
    dimensions: list[JudgeDimension],
    brief: PersonaBrief,
) -> float:
    """Compute weighted overall score from judge dimensions (0-100 scale)."""
    weight_map = {d.name: d.weight for d in brief.dimensions}
    total_weight = sum(weight_map.values())
    if total_weight == 0:
        return 0.0

    weighted_sum = 0.0
    for dim in dimensions:
        w = weight_map.get(dim.name, 0.0)
        # Scale 1-5 to 0-100: (score - 1) / 4 * 100
        scaled = (dim.score - 1.0) / 4.0 * 100.0
        weighted_sum += scaled * w

    return round(weighted_sum / total_weight, 1)


def run_judge(
    persona_name: str,
    trace_summary: dict[str, Any],
    model: str = "haiku",
) -> JudgeResult:
    """Run the LLM judge for one persona on one trace summary.

    Args:
        persona_name: Name of the persona (training, rl, analytics, domain).
        trace_summary: Output of summarize_for_judge().
        model: Model to use. "haiku" for fast/cheap, "sonnet" for detailed.

    Returns:
        JudgeResult with per-dimension scores or skipped=True on failure.
    """
    # Load brief
    brief = load_brief(persona_name)
    if brief is None:
        return JudgeResult(
            persona_name=persona_name,
            skipped=True,
            skip_reason=f"Brief not found for persona: {persona_name}",
        )

    # Try to import and call the Anthropic SDK
    try:
        import anthropic
    except ImportError:
        return JudgeResult(
            persona_name=persona_name,
            skipped=True,
            skip_reason="anthropic SDK not installed",
        )

    # Check for API key
    try:
        client = anthropic.Anthropic()
    except anthropic.AuthenticationError:
        return JudgeResult(
            persona_name=persona_name,
            skipped=True,
            skip_reason="ANTHROPIC_API_KEY not set or invalid",
        )

    # Map friendly model names to model IDs
    model_map = {
        "haiku": "claude-haiku-4-5-20251001",
        "sonnet": "claude-sonnet-4-6",
        "opus": "claude-opus-4-6",
    }
    model_id = model_map.get(model, model)

    # Build prompts
    system_prompt, user_prompt = _build_judge_prompt(brief, trace_summary)

    # Call the API
    try:
        response = client.messages.create(
            model=model_id,
            max_tokens=1024,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        response_text = response.content[0].text
    except Exception as e:
        logger.warning("Judge API call failed for %s: %s", persona_name, e)
        return JudgeResult(
            persona_name=persona_name,
            skipped=True,
            skip_reason=f"API call failed: {e}",
        )

    # Parse response
    try:
        dimensions = _parse_judge_response(response_text, brief)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("Judge response parse failed for %s: %s", persona_name, e)
        return JudgeResult(
            persona_name=persona_name,
            skipped=True,
            skip_reason=f"Response parse failed: {e}",
            model_used=model_id,
        )

    overall = _compute_judge_overall(dimensions, brief)

    return JudgeResult(
        persona_name=persona_name,
        dimensions=dimensions,
        overall_score=overall,
        model_used=model_id,
        skipped=False,
    )
