"""Small human/agent projections over the atlas's stored truth."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from .atlas import ATLAS_SCHEMA_VERSION, AtlasIntegrityError


AGENT_SUMMARY_SCHEMA_VERSION = "opentraces.arena.agent-summary.v0"


def _rows(atlas: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if atlas.get("schema_version") != ATLAS_SCHEMA_VERSION:
        raise AtlasIntegrityError("unsupported atlas schema_version")
    rows = atlas.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise AtlasIntegrityError("atlas rows must be an array of objects")
    return rows


def query_atlas(
    atlas: Mapping[str, Any],
    *,
    states: Iterable[str] | None = None,
    guarantee_ids: Iterable[str] | None = None,
) -> list[Mapping[str, Any]]:
    """Return rows matching simple machine-query filters without rewriting them."""

    wanted_states = set(states) if states is not None else None
    wanted_ids = set(guarantee_ids) if guarantee_ids is not None else None
    return [
        row
        for row in _rows(atlas)
        if (wanted_states is None or row.get("state") in wanted_states)
        and (wanted_ids is None or row.get("id") in wanted_ids)
    ]


def _signal_row(row: Mapping[str, Any], *, include_verdict: bool) -> dict[str, Any]:
    signal = {
        "id": row.get("id"),
        "claim": row.get("claim"),
        "state": row.get("state"),
        "run_id": row.get("latest_run_id"),
        "evidence_ref": row.get("evidence_ref"),
    }
    if include_verdict:
        signal = {
            "id": signal["id"],
            "claim": signal["claim"],
            "state": signal["state"],
            "verdict": row.get("verdict"),
            "run_id": signal["run_id"],
            "evidence_ref": signal["evidence_ref"],
        }
    return signal


def build_agent_summary(atlas: Mapping[str, Any]) -> dict[str, Any]:
    """Emit the minimum facts needed to locate current failures and holes."""

    rows = _rows(atlas)
    counts = Counter(str(row.get("state")) for row in rows)
    failures = [
        _signal_row(row, include_verdict=True)
        for row in rows
        if row.get("state") == "failing"
    ]
    holes = [
        _signal_row(row, include_verdict=False)
        for row in rows
        if row.get("state") not in {"proven", "failing"}
    ]
    return {
        "schema_version": AGENT_SUMMARY_SCHEMA_VERSION,
        "counts": dict(sorted(counts.items())),
        "failures": failures,
        "holes": holes,
    }


def format_pr_evidence_link(row: Mapping[str, Any], *, page_url: str) -> str:
    """Format a PR link without promoting it beyond the row's stored facts."""

    run_id = row.get("latest_run_id")
    evidence_ref = row.get("evidence_ref")
    if not isinstance(run_id, str) or not run_id:
        raise AtlasIntegrityError("an unbound atlas row has no PR evidence link")
    if not isinstance(evidence_ref, str) or not evidence_ref:
        raise AtlasIntegrityError("a bound atlas row has no evidence ref")
    if not isinstance(page_url, str) or not page_url.startswith(("https://", "http://")):
        raise AtlasIntegrityError("PR evidence page URL must be an HTTP(S) URL")
    return f"[bench evidence: {run_id}]({page_url}) (`{evidence_ref}`)"
