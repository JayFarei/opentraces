"""Auto-generated dataset card for HuggingFace Hub.

Generates a README.md with YAML frontmatter and machine-managed stats.
Preserves user-edited sections on update.
"""

from __future__ import annotations

from collections import Counter

from opentraces_schema.models import TraceRecord
from opentraces_schema.version import SCHEMA_VERSION

AUTO_START = "<!-- opentraces:auto-stats-start -->"
AUTO_END = "<!-- opentraces:auto-stats-end -->"


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
    timestamps: list[str] = []

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

    date_range = "N/A"
    if timestamps:
        sorted_ts = sorted(timestamps)
        date_range = f"{sorted_ts[0][:10]} to {sorted_ts[-1][:10]}"

    return {
        "total_traces": len(traces),
        "total_steps": total_steps,
        "total_tokens": total_tokens,
        "model_counts": dict(model_counts),
        "agent_counts": dict(agent_counts),
        "date_range": date_range,
    }


def _render_quality_table(quality_summary: dict) -> str:
    """Render the quality scores table for the stats section."""
    lines = [
        "### Quality Scores",
        "",
        f"Assessed: {quality_summary.get('assessed_at', 'N/A')} | "
        f"Mode: {quality_summary.get('scoring_mode', 'deterministic')} | "
        f"Scorer: v{quality_summary.get('scorer_version', 'unknown')}",
        "",
        "| Persona | Average | Min | Max |",
        "|---------|---------|-----|-----|",
    ]
    for name, ps in quality_summary.get("persona_scores", {}).items():
        avg = ps.get("average", 0.0)
        mn = ps.get("min", 0.0)
        mx = ps.get("max", 0.0)
        lines.append(f"| {name} | {avg:.1f}% | {mn:.1f}% | {mx:.1f}% |")

    overall = quality_summary.get("overall_utility", 0.0)
    gate = quality_summary.get("gate_status", "unknown")
    lines.append("")
    lines.append(f"**Overall utility: {overall:.1f}%** | Gate: {gate.upper()}")
    lines.append("")
    return "\n".join(lines)


def _render_stats_section(stats: dict, quality_summary: dict | None = None) -> str:
    """Render the machine-managed statistics section."""
    lines = [
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

    if existing_card and AUTO_START in existing_card and AUTO_END in existing_card:
        # Replace only the machine-managed section
        before = existing_card[: existing_card.index(AUTO_START)]
        after = existing_card[existing_card.index(AUTO_END) + len(AUTO_END) :]

        # Also update frontmatter if present
        if existing_card.startswith("---"):
            end_idx = existing_card.index("---", 3) + 3
            frontmatter = _render_frontmatter(repo_id, traces, quality_summary=quality_summary)
            before = frontmatter + existing_card[end_idx : existing_card.index(AUTO_START)]

        return before + stats_section + after

    # Generate fresh card
    frontmatter = _render_frontmatter(repo_id, traces, quality_summary=quality_summary)
    body = _render_default_body(repo_id)

    return f"{frontmatter}\n{body}\n{stats_section}\n"
