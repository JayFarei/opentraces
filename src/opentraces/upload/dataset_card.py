"""Auto-generated dataset card for HuggingFace Hub.

Generates a README.md with YAML frontmatter and machine-managed stats.
Preserves user-edited sections on update.
"""

from __future__ import annotations

import json
from collections import Counter

from opentraces_schema.models import TraceRecord
from opentraces_schema.version import SCHEMA_VERSION

AUTO_START = "<!-- opentraces:auto-stats-start -->"
AUTO_END = "<!-- opentraces:auto-stats-end -->"
BADGE_START = "<!-- opentraces:auto-badges-start -->"
BADGE_END = "<!-- opentraces:auto-badges-end -->"
STATS_SENTINEL_START = "<!-- opentraces:stats"
STATS_SENTINEL_END = "<!-- opentraces:stats-end -->"


def _size_category(count: int) -> str:
    """Map trace count to HF size category."""
    if count < 100:
        return "n<1K"
    elif count < 1000:
        return "1K<n<10K"
    elif count < 10000:
        return "10K<n<100K"
    else:
        return "100K<n<1M"


def _compute_stats(traces: list[TraceRecord]) -> dict:
    """Compute aggregate statistics from traces."""
    total_steps = 0
    total_tokens = 0
    model_counts: Counter[str] = Counter()
    agent_counts: Counter[str] = Counter()
    dep_counts: Counter[str] = Counter()
    timestamps: list[str] = []
    costs: list[float] = []
    success_signals: list[bool] = []

    for t in traces:
        total_steps += t.metrics.total_steps or len(t.steps)
        total_tokens += (t.metrics.total_input_tokens or 0) + (t.metrics.total_output_tokens or 0)

        if t.agent.model:
            model_counts[t.agent.model] += 1
        agent_counts[t.agent.name] += 1

        if t.timestamp_start:
            timestamps.append(t.timestamp_start)
        if t.timestamp_end:
            timestamps.append(t.timestamp_end)

        if t.metrics.estimated_cost_usd is not None:
            costs.append(t.metrics.estimated_cost_usd)

        if t.outcome.success is not None:
            success_signals.append(t.outcome.success)

        for dep in (t.dependencies or []):
            dep_counts[dep] += 1

    date_range = "N/A"
    if timestamps:
        sorted_ts = sorted(timestamps)
        date_range = f"{sorted_ts[0][:10]} to {sorted_ts[-1][:10]}"

    n = len(traces)
    avg_steps = round(total_steps / n) if n else 0
    avg_cost = round(sum(costs) / len(costs), 2) if costs else None
    total_cost = round(sum(costs), 2) if costs else None
    success_rate = round(100 * sum(success_signals) / len(success_signals), 1) if success_signals else None
    top_deps = [[name, count] for name, count in dep_counts.most_common(10)]

    return {
        "total_traces": n,
        "total_steps": total_steps,
        "total_tokens": total_tokens,
        "avg_steps_per_session": avg_steps,
        "avg_cost_usd": avg_cost,
        "total_cost_usd": total_cost,
        "success_rate": success_rate,
        "top_dependencies": top_deps,
        "model_counts": dict(model_counts),
        "agent_counts": dict(agent_counts),
        "date_range": date_range,
    }


def _render_badge_row(quality_summary: dict | None) -> str:
    """Render the auto-managed badge row that sits just below the H1 title.

    Returns an empty sentinel block when quality_summary is None so the
    markers are present for future updates even if there are no scores yet.
    """
    if not quality_summary:
        return f"{BADGE_START}\n{BADGE_END}"

    overall = quality_summary.get("overall_utility", 0.0)
    gate = quality_summary.get("gate_status", "unknown")
    gate_color = "28a745" if gate.lower() == "passing" else "dc3545"

    badges = [
        f"[![Overall Quality]({_badge_url('Overall_Quality', overall)})](https://opentraces.ai)",
        f"[![Gate: {gate.upper()}](https://img.shields.io/badge/Gate-{gate.upper()}-{gate_color})](https://opentraces.ai)",
    ]
    for name, ps in quality_summary.get("persona_scores", {}).items():
        avg = ps.get("average", 0.0)
        label = name.replace("_", " ").title()
        badges.append(f"![{label}]({_badge_url(label, avg)})")

    inner = " ".join(badges)
    return f"{BADGE_START}\n{inner}\n{BADGE_END}"


def _score_color(score: float) -> str:
    """Map a 0-100 score to a shields.io hex color."""
    if score >= 80:
        return "28a745"
    elif score >= 60:
        return "ffc107"
    elif score >= 40:
        return "fd7e14"
    else:
        return "dc3545"


def _badge_url(label: str, score: float) -> str:
    """Build a shields.io static badge URL for a score."""
    label_enc = label.replace(" ", "_").replace("-", "--")
    score_enc = f"{score:.1f}%25"  # %25 is URL-encoded %
    color = _score_color(score)
    return f"https://img.shields.io/badge/{label_enc}-{score_enc}-{color}"


def _render_quality_table(quality_summary: dict) -> str:
    """Render the quality scorecard detail table for the stats block.

    The badge row itself lives at the top of the card (BADGE_START/BADGE_END).
    This section shows the per-persona breakdown table.
    """
    persona_scores = quality_summary.get("persona_scores", {})
    overall = quality_summary.get("overall_utility", 0.0)
    gate = quality_summary.get("gate_status", "unknown")

    lines = [
        "### opentraces Scorecard",
        "",
        f"Assessed: {quality_summary.get('assessed_at', 'N/A')} | "
        f"Mode: {quality_summary.get('scoring_mode', 'deterministic')} | "
        f"Scorer: v{quality_summary.get('scorer_version', 'unknown')}",
        "",
        "| Persona | Score | Min | Max | Status |",
        "|---------|-------|-----|-----|--------|",
    ]
    for name, ps in persona_scores.items():
        avg = ps.get("average", 0.0)
        mn = ps.get("min", 0.0)
        mx = ps.get("max", 0.0)
        status = "PASS" if avg >= 80 else ("WARN" if avg >= 60 else "FAIL")
        lines.append(f"| {name} | {avg:.1f}% | {mn:.1f}% | {mx:.1f}% | {status} |")

    lines.append("")
    lines.append(f"**Overall utility: {overall:.1f}%** | Gate: {gate.upper()}")
    lines.append("")
    return "\n".join(lines)


def _render_machine_json(stats: dict) -> str:
    """Render the single-line machine-readable JSON sentinel block.

    Placed just before AUTO_START so the existing marker replacement logic
    is unaffected. Parseable with a simple regex in browser JS:
        /<!-- opentraces:stats\\s*(\\{[\\s\\S]*?\\})\\s*(?:-->|<!--\\s*opentraces:stats-end)/
    """
    payload = {
        "total_traces": stats["total_traces"],
        "total_tokens": stats["total_tokens"],
        "avg_steps_per_session": stats["avg_steps_per_session"],
        "avg_cost_usd": stats["avg_cost_usd"],
        "total_cost_usd": stats["total_cost_usd"],
        "success_rate": stats["success_rate"],
        "top_dependencies": stats["top_dependencies"],
        "agent_counts": stats["agent_counts"],
        "model_counts": stats["model_counts"],
        "date_range": stats["date_range"],
    }
    return f"{STATS_SENTINEL_START}\n{json.dumps(payload, separators=(',', ':'))}\n{STATS_SENTINEL_END}\n"


def _render_stats_section(stats: dict, quality_summary: dict | None = None) -> str:
    """Render the machine-managed statistics section."""
    lines = [
        _render_machine_json(stats),
        AUTO_START,
        "## Dataset Statistics",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total traces | {stats['total_traces']:,} |",
        f"| Total steps | {stats['total_steps']:,} |",
        f"| Total tokens | {stats['total_tokens']:,} |",
        f"| Date range | {stats['date_range']} |",
        f"| Schema version | {SCHEMA_VERSION} |",
        "",
    ]

    if quality_summary:
        lines.append(_render_quality_table(quality_summary))

    if stats["model_counts"]:
        lines.append("### Model Distribution")
        lines.append("")
        lines.append("| Model | Count |")
        lines.append("|-------|-------|")
        for model, count in sorted(stats["model_counts"].items(), key=lambda x: -x[1]):
            lines.append(f"| {model} | {count:,} |")
        lines.append("")

    if stats["agent_counts"]:
        lines.append("### Agent Distribution")
        lines.append("")
        lines.append("| Agent | Count |")
        lines.append("|-------|-------|")
        for agent, count in sorted(stats["agent_counts"].items(), key=lambda x: -x[1]):
            lines.append(f"| {agent} | {count:,} |")
        lines.append("")

    lines.append(AUTO_END)
    return "\n".join(lines)


def _render_frontmatter(
    repo_id: str,
    traces: list[TraceRecord],
    quality_summary: dict | None = None,
) -> str:
    """Render YAML frontmatter.

    Includes a configs block with a glob pattern so HF datasets-server
    discovers all current and future shards without per-file gitattributes
    entries. If quality_summary is provided, adds flat score keys (HF-searchable).
    """
    size_cat = _size_category(len(traces))
    lines = [
        "---",
        "license: cc-by-4.0",
        "tags:",
        "  - opentraces",
        "  - agent-traces",
        "task_categories:",
        "  - text-generation",
        "language:",
        "  - en",
        "size_categories:",
        f"  - {size_cat}",
        # configs glob: tells HF datasets-server where to find all shards.
        # New pushes add shards matching data/traces_*.jsonl automatically.
        "configs:",
        "- config_name: default",
        "  data_files:",
        "  - split: train",
        "    path: data/traces_*.jsonl",
    ]

    if quality_summary:
        # Flat top-level keys for HF search
        persona_scores = quality_summary.get("persona_scores", {})
        for name, ps in persona_scores.items():
            avg = ps.get("average", 0.0)
            lines.append(f"{name}_score: {avg}")
        overall = quality_summary.get("overall_utility", 0.0)
        lines.append(f"overall_quality: {overall}")

    lines.append("---")
    return "\n".join(lines)


def _render_default_body(repo_id: str) -> str:
    """Render the default (non-stats) body sections."""
    return f"""
# {repo_id.split("/")[-1]}

Community-contributed agent traces in opentraces JSONL format.

## Usage

```python
from datasets import load_dataset

ds = load_dataset("{repo_id}")
```

## Schema

Each JSONL line is a `TraceRecord` containing:

- **trace_id**: Unique identifier for the trace
- **session_id**: Source session identifier
- **agent**: Agent identity (name, version, model)
- **task**: Structured task metadata
- **steps**: List of LLM API calls (thought-action-observation cycles)
- **outcome**: Session outcome signals
- **metrics**: Aggregated token usage and cost estimates
- **environment**: Runtime environment metadata
- **attribution**: Code attribution data (experimental)

Schema version: `{SCHEMA_VERSION}`

Full schema docs: [opentraces.ai/schema](https://opentraces.ai/schema)

## License

This dataset is licensed under [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/).

Contributors retain copyright over their individual traces. By uploading, you agree
to share under CC-BY-4.0 for research and training purposes.
"""


def generate_dataset_card(
    repo_id: str,
    traces: list[TraceRecord],
    existing_card: str | None = None,
    quality_summary: dict | None = None,
) -> str:
    """Generate or update a dataset card README.md.

    If existing_card is provided, only the machine-managed stats section
    (between auto-stats markers) is replaced. All other content is preserved.

    Args:
        repo_id: HF dataset repo ID (e.g. "user/my-traces")
        traces: list of TraceRecord objects
        existing_card: existing README content to update, or None for fresh card
        quality_summary: dict from QualitySummary.to_dict(), or None to omit quality section
    """
    stats = _compute_stats(traces)
    stats_section = _render_stats_section(stats, quality_summary=quality_summary)

    badge_row = _render_badge_row(quality_summary)

    if existing_card and AUTO_START in existing_card and AUTO_END in existing_card:
        # Replace only the machine-managed section
        # Also strip any existing sentinel block that sits just before AUTO_START
        card_body = existing_card
        if STATS_SENTINEL_START in card_body and STATS_SENTINEL_END in card_body:
            s_start = card_body.index(STATS_SENTINEL_START)
            s_end = card_body.index(STATS_SENTINEL_END, s_start) + len(STATS_SENTINEL_END)
            card_body = card_body[:s_start] + card_body[s_end:]

        before = card_body[: card_body.index(AUTO_START)]
        after = card_body[card_body.index(AUTO_END) + len(AUTO_END) :]

        # Also update frontmatter if present
        if card_body.startswith("---"):
            end_idx = card_body.index("---", 3) + 3
            frontmatter = _render_frontmatter(repo_id, traces, quality_summary=quality_summary)
            before = frontmatter + card_body[end_idx : card_body.index(AUTO_START)]

        # Update or inject badge row right after the first H1 heading
        if BADGE_START in before and BADGE_END in before:
            b_start = before.index(BADGE_START)
            b_end = before.index(BADGE_END, b_start) + len(BADGE_END)
            before = before[:b_start] + badge_row + before[b_end:]
        else:
            # Legacy card without badge sentinel: inject after first H1
            h1_idx = before.find("\n# ")
            if h1_idx != -1:
                eol = before.find("\n", h1_idx + 1)
                if eol != -1:
                    before = before[: eol + 1] + "\n" + badge_row + "\n" + before[eol + 1 :]

        return before + stats_section + after

    # Generate fresh card: badge row goes right after the H1 title line
    frontmatter = _render_frontmatter(repo_id, traces, quality_summary=quality_summary)
    body = _render_default_body(repo_id)

    # Insert badge row after the first H1 in body
    h1_idx = body.find("\n# ")
    if h1_idx != -1:
        eol = body.find("\n", h1_idx + 1)
        if eol != -1:
            body = body[: eol + 1] + "\n" + badge_row + "\n" + body[eol + 1 :]

    return f"{frontmatter}\n{body}\n{stats_section}\n"
