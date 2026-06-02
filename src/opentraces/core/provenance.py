"""Deterministic provenance color for discovery results."""

from __future__ import annotations

from typing import Any


COLOR_COMMITTED = "committed"
COLOR_UNCOMMITTED = "uncommitted"
COLOR_REVERTED = "reverted"
COLOR_UNKNOWN = "unknown"


def provenance_from_record(record: Any) -> dict[str, Any]:
    outcome = getattr(record, "outcome", None)
    committed = getattr(outcome, "committed", None)
    commit_sha = getattr(outcome, "commit_sha", None)
    survival_states = _survival_states_from_record(record)
    if not commit_sha:
        for link in getattr(record, "git_links", []) or []:
            if getattr(link, "tier", None) != "orphan" and getattr(link, "revision", None):
                commit_sha = str(link.revision)
                break
    return _provenance(
        committed=committed if isinstance(committed, bool) else None,
        commit_sha=commit_sha,
        survival_states=survival_states,
        commit_subject=None,
    )


def provenance_from_unit(unit: Any) -> dict[str, Any]:
    committed = _bool_facet(unit, "outcome.committed")
    commit_sha = (
        _first_facet(unit, "git.commit_sha")
        or _first_metadata(unit, "commit_sha")
        or _first_metadata(unit, "outcome_commit_sha")
    )
    states = [
        value
        for value in (
            _facet_values(unit, "survival.state")
            + _facet_values(unit, "trail.survival_state")
        )
        if value
    ]
    return _provenance(
        committed=committed,
        commit_sha=commit_sha,
        survival_states=states,
        commit_subject=_first_metadata(unit, "commit_subject"),
    )


def _provenance(
    *,
    committed: bool | None,
    commit_sha: str | None,
    survival_states: list[str],
    commit_subject: str | None,
) -> dict[str, Any]:
    states = {str(state) for state in survival_states if state}
    if "reverted" in states:
        color = COLOR_REVERTED
    elif committed is True or commit_sha:
        color = COLOR_COMMITTED
    elif committed is False:
        color = COLOR_UNCOMMITTED
    else:
        color = COLOR_UNKNOWN
    return {
        "provenance_color": color,
        "committed": committed,
        "commit_sha": commit_sha,
        "commit_subject": commit_subject,
        "survival_states": sorted(states),
    }


def _survival_states_from_record(record: Any) -> list[str]:
    metadata = getattr(record, "metadata", {}) or {}
    raw = metadata.get("survival_state") or metadata.get("survival_states")
    if raw:
        if isinstance(raw, list):
            return sorted({str(item) for item in raw if item})
        return [str(raw)]

    states: set[str] = set()
    for link in getattr(record, "git_links", []) or []:
        alive = getattr(link, "content_alive", None)
        reachable = getattr(link, "commit_reachable", None)
        if alive is True:
            states.add("alive_on_path")
        elif alive is False:
            states.add("reverted" if reachable is False else "lost")
        elif reachable is False:
            states.add("reverted")
    if not states and getattr(record, "git_links", None):
        states.add("unknown")
    return sorted(states)


def _facet_values(unit: Any, name: str) -> list[str]:
    out: list[str] = []
    for facet in getattr(unit, "facets", []) or []:
        if getattr(facet, "name", None) == name and getattr(facet, "value", None) is not None:
            out.append(str(facet.value))
    return out


def _first_facet(unit: Any, name: str) -> str | None:
    values = _facet_values(unit, name)
    return values[0] if values else None


def _first_metadata(unit: Any, name: str) -> str | None:
    value = (getattr(unit, "metadata", {}) or {}).get(name)
    return str(value) if value else None


def _bool_facet(unit: Any, name: str) -> bool | None:
    value = _first_facet(unit, name)
    if value is None:
        return None
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return None
