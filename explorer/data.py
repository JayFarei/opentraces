"""
Data loading utilities for the opentraces Explorer.

Discovers opentraces-tagged datasets on HuggingFace Hub, loads traces,
and computes analytics. Falls back to sample data when no real datasets
are available yet.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from explorer.sample_data import generate_sample_traces

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------

_CACHED_TRACES: list[dict] | None = None
_USE_SAMPLE = True


def _get_traces() -> list[dict]:
    """Return all traces, using cache when available."""
    global _CACHED_TRACES, _USE_SAMPLE
    if _CACHED_TRACES is not None:
        return _CACHED_TRACES

    # Try real datasets first
    try:
        datasets = discover_opentraces_datasets()
        if datasets:
            all_traces: list[dict] = []
            for ds in datasets:
                all_traces.extend(load_traces_from_dataset(ds["repo_id"]))
            if all_traces:
                _CACHED_TRACES = all_traces
                _USE_SAMPLE = False
                return _CACHED_TRACES
    except Exception:
        logger.info("Could not load real datasets, falling back to sample data.")

    # Fallback to sample data
    _CACHED_TRACES = generate_sample_traces(25)
    _USE_SAMPLE = True
    return _CACHED_TRACES


def is_using_sample_data() -> bool:
    """Return True if the explorer is running on generated sample data."""
    _get_traces()  # ensure initialized
    return _USE_SAMPLE


# ---------------------------------------------------------------------------
# Dataset discovery
# ---------------------------------------------------------------------------

def discover_opentraces_datasets() -> list[dict]:
    """Find all datasets tagged with 'opentraces' on HF Hub."""
    try:
        from huggingface_hub import HfApi
        api = HfApi()
        datasets = list(api.list_datasets(search="opentraces", limit=200))
        return [
            {
                "repo_id": ds.id,
                "author": ds.author or ds.id.split("/")[0] if "/" in ds.id else "unknown",
                "last_modified": str(ds.last_modified) if ds.last_modified else "",
                "downloads": getattr(ds, "downloads", 0),
            }
            for ds in datasets
        ]
    except Exception as exc:
        logger.warning("Failed to discover datasets: %s", exc)
        return []


def load_traces_from_dataset(repo_id: str, max_traces: int = 100) -> list[dict]:
    """Load trace records from an HF dataset repo.

    Attempts to load from parquet/jsonl files in the repo. Returns an empty
    list on failure.
    """
    try:
        from huggingface_hub import hf_hub_download
        import json

        path = hf_hub_download(repo_id=repo_id, filename="traces.jsonl", repo_type="dataset")
        traces: list[dict] = []
        with open(path) as f:
            for i, line in enumerate(f):
                if i >= max_traces:
                    break
                traces.append(json.loads(line))
        return traces
    except Exception as exc:
        logger.debug("Could not load traces from %s: %s", repo_id, exc)
        return []


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_traces(query: str, filters: dict | None = None) -> list[dict]:
    """Search across all opentraces datasets.

    *query*: free-text search against task descriptions, contributors, etc.
    *filters*: optional dict with keys ecosystem, model, agent, outcome.
    """
    traces = _get_traces()
    filters = filters or {}
    query_lower = query.strip().lower()

    results: list[dict] = []
    for t in traces:
        # Text search
        if query_lower:
            searchable = " ".join([
                t.get("task", ""),
                t.get("contributor", ""),
                t.get("model", ""),
                t.get("agent", ""),
                t.get("ecosystem", ""),
            ]).lower()
            if query_lower not in searchable:
                continue

        # Filters
        eco = filters.get("ecosystem")
        if eco and eco != "All" and t.get("ecosystem", "").lower() != eco.lower():
            continue

        mdl = filters.get("model")
        if mdl and mdl != "All" and mdl.lower() not in t.get("model", "").lower():
            continue

        agt = filters.get("agent")
        if agt and agt != "All" and t.get("agent", "").lower() != agt.lower():
            continue

        outcome = filters.get("outcome")
        if outcome and outcome != "All":
            committed = t.get("outcome", {}).get("committed", False)
            if outcome == "Committed" and not committed:
                continue
            if outcome == "Not committed" and committed:
                continue

        results.append(t)

    return results


# ---------------------------------------------------------------------------
# Contributor analytics
# ---------------------------------------------------------------------------

def get_contributor_stats(username: str) -> dict:
    """Compute analytics for a specific contributor."""
    traces = _get_traces()
    user_traces = [t for t in traces if t.get("contributor", "").lower() == username.lower()]

    if not user_traces:
        return {"found": False, "username": username}

    # Sessions over time (group by date)
    dates: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()
    tool_counts: Counter[str] = Counter()
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_creation = 0
    dep_counts: Counter[str] = Counter()
    committed_count = 0
    outcome_known = 0

    for t in user_traces:
        ts = t.get("timestamp", "")
        if ts:
            day = ts[:10]
            dates[day] += 1

        model_counts[_short_model(t.get("model", "unknown"))] += 1

        for tc in t.get("tool_calls", []):
            tool_counts[tc.get("tool", "unknown")] += 1

        tokens = t.get("tokens", {})
        total_input += tokens.get("input", 0)
        total_output += tokens.get("output", 0)
        total_cache_read += tokens.get("cache_read", 0)
        total_cache_creation += tokens.get("cache_creation", 0)

        for dep in t.get("dependencies", []):
            dep_counts[dep] += 1

        outcome = t.get("outcome", {})
        if "committed" in outcome:
            outcome_known += 1
            if outcome["committed"]:
                committed_count += 1

    cache_hit_rate = total_cache_read / max(total_input, 1)
    # Rough cost estimate: $3/M input, $15/M output for Sonnet
    estimated_cost = (total_input * 3 + total_output * 15) / 1_000_000

    return {
        "found": True,
        "username": username,
        "total_sessions": len(user_traces),
        "sessions_by_date": dict(sorted(dates.items())),
        "model_distribution": dict(model_counts.most_common()),
        "tokens": {
            "input": total_input,
            "output": total_output,
            "cache_read": total_cache_read,
            "cache_creation": total_cache_creation,
        },
        "tool_usage": dict(tool_counts.most_common()),
        "cache_hit_rate": round(cache_hit_rate, 3),
        "estimated_cost": round(estimated_cost, 2),
        "top_dependencies": dict(dep_counts.most_common(10)),
        "success_rate": round(committed_count / max(outcome_known, 1), 3),
        "outcome_known": outcome_known,
    }


# ---------------------------------------------------------------------------
# Community stats
# ---------------------------------------------------------------------------

def get_community_stats() -> dict:
    """Compute aggregate community statistics."""
    traces = _get_traces()

    contributors: set[str] = set()
    model_counts: Counter[str] = Counter()
    agent_counts: Counter[str] = Counter()
    ecosystem_counts: Counter[str] = Counter()
    has_attribution = 0
    has_outcome = 0
    recent: list[dict] = []

    for t in traces:
        contributors.add(t.get("contributor", "anonymous"))
        model_counts[_short_model(t.get("model", "unknown"))] += 1
        agent_counts[t.get("agent", "unknown")] += 1
        ecosystem_counts[t.get("ecosystem", "unknown")] += 1

        if t.get("attribution"):
            has_attribution += 1
        if t.get("outcome", {}).get("committed") is not None:
            has_outcome += 1

    # Sort by timestamp descending for recent activity
    sorted_traces = sorted(traces, key=lambda x: x.get("timestamp", ""), reverse=True)
    for t in sorted_traces[:10]:
        recent.append({
            "trace_id": t["trace_id"],
            "task": t["task"][:80],
            "contributor": t.get("contributor", "anonymous"),
            "date": t.get("timestamp", "")[:10],
            "model": _short_model(t.get("model", "")),
        })

    total = max(len(traces), 1)

    return {
        "total_traces": len(traces),
        "active_contributors": len(contributors),
        "model_distribution": dict(model_counts.most_common()),
        "agent_distribution": dict(agent_counts.most_common()),
        "ecosystem_distribution": dict(ecosystem_counts.most_common()),
        "attribution_pct": round(has_attribution / total * 100, 1),
        "outcome_pct": round(has_outcome / total * 100, 1),
        "recent_activity": recent,
        "is_sample_data": is_using_sample_data(),
    }


# ---------------------------------------------------------------------------
# Trace detail
# ---------------------------------------------------------------------------

def get_trace_by_id(trace_id: str) -> dict | None:
    """Find a single trace by its ID."""
    for t in _get_traces():
        if t.get("trace_id") == trace_id:
            return t
    return None


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _short_model(model: str) -> str:
    """Shorten model identifiers for display."""
    if "opus" in model.lower():
        return "claude-opus-4"
    if "sonnet" in model.lower():
        return "claude-sonnet-4"
    if "haiku" in model.lower():
        return "claude-haiku-3.5"
    return model


def get_filter_options() -> dict[str, list[str]]:
    """Return available filter values derived from loaded traces."""
    traces = _get_traces()
    ecosystems: set[str] = set()
    models: set[str] = set()
    agents: set[str] = set()
    for t in traces:
        ecosystems.add(t.get("ecosystem", ""))
        models.add(_short_model(t.get("model", "")))
        agents.add(t.get("agent", ""))
    return {
        "ecosystems": ["All"] + sorted(e for e in ecosystems if e),
        "models": ["All"] + sorted(m for m in models if m),
        "agents": ["All"] + sorted(a for a in agents if a),
        "outcomes": ["All", "Committed", "Not committed"],
    }
