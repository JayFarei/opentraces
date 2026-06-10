"""Progressive-discovery read models.

This module is intentionally read-only. It composes bounded discovery packets
from the read-only trace search snapshot plus top-N trace hydration.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from opentraces_schema import TraceRecord, load_record_json

from .boilerplate import headline_from_summary, summary_for_record
from .bucket_store import read_trace_record_object
from .provenance import provenance_from_record
from .trace_map import build_trace_map


class CandidateCard(BaseModel):
    """Bounded per-trace card for progressive discovery."""

    trace_id: str
    headline: str
    summary: str
    agent: dict[str, Any] = Field(default_factory=dict)
    day: str | None = None
    key_files: list[str] = Field(default_factory=list)
    key_steps: list[dict[str, Any]] = Field(default_factory=list)
    provenance_color: str | None = None
    committed: bool | None = None
    commit_sha: str | None = None
    commit_subject: str | None = None
    outcome: dict[str, Any] = Field(default_factory=dict)
    refs: dict[str, str] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class DiscoveryGroup(BaseModel):
    """One bounded group inside a progressive discovery packet."""

    key: str
    label: str
    count: int
    narrative: str
    cards: list[CandidateCard] = Field(default_factory=list)
    refs: dict[str, str] = Field(default_factory=dict)


class DiscoveryPacket(BaseModel):
    """Deterministic topic capsule built from bounded search + cards."""

    topic: str
    by: str = "day"
    total_candidates: int
    total_cards: int
    search_diagnostics: dict[str, Any] = Field(default_factory=dict)
    groups: list[DiscoveryGroup] = Field(default_factory=list)
    refs: dict[str, str] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


def discover(
    topic: str,
    *,
    by: str = "day",
    limit: int = 50,
    per_group: int = 5,
    latest_generation: bool = True,
    project: str | None = None,
    index_path: Path | None = None,
) -> DiscoveryPacket:
    """Build a deterministic progressive-discovery packet for ``topic``.

    The function deliberately avoids LLMs and embeddings. It asks the
    read-only search snapshot for a bounded candidate page, dedupes by trace id,
    then expands each selected trace into a bounded :class:`CandidateCard`.
    """

    if by != "day":
        raise ValueError("Only --by day is supported in this release.")
    topic = " ".join(topic.split())
    if not topic:
        raise ValueError("topic must not be empty")
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if per_group < 1:
        raise ValueError("per_group must be >= 1")

    page = _topic_page(
        topic,
        limit=limit,
        latest_generation=latest_generation,
        project=project,
        index_path=index_path,
    )
    candidates = page.hits
    cards: list[CandidateCard] = []
    seen: set[str] = set()
    hydrated_count = 0
    for candidate in candidates:
        trace_id = getattr(candidate, "trace_id", None)
        if not trace_id or trace_id in seen:
            continue
        seen.add(trace_id)
        hydrated_count += 1
        try:
            cards.append(candidate_card(trace_id, index_path=index_path))
        except FileNotFoundError:
            continue
        if len(cards) >= limit:
            break

    groups = _group_cards_by_day(topic, cards, per_group=per_group)
    diagnostics = page.diagnostics.as_dict()
    diagnostics["hydrated_count"] = hydrated_count
    return DiscoveryPacket(
        topic=topic,
        by=by,
        total_candidates=len(candidates),
        total_cards=len(cards),
        search_diagnostics=diagnostics,
        groups=groups,
        refs={
            "query": f"ot://trace-search/lex/{_ref_escape(topic)}",
            "command": f"opentraces trace query --lex {json.dumps(topic)}",
        },
        limitations=["lexical_topic_discovery", "bounded_cards", "no_llm"],
    )


def candidate_card(
    ref: str,
    *,
    max_files: int = 8,
    max_steps: int = 8,
    index_path: Path | None = None,
) -> CandidateCard:
    """Build a bounded discovery card for a trace-like ref."""

    trace_id = _trace_id_from_ref(ref, index_path=index_path)
    record = _load_trace_record(trace_id, index_path=index_path)
    summary = summary_for_record(record)
    headline = headline_from_summary(summary or record.task.description or trace_id)
    trace_map = build_trace_map(record)
    provenance = provenance_from_record(record)
    return CandidateCard(
        trace_id=record.trace_id,
        headline=headline or record.trace_id,
        summary=summary or headline or record.trace_id,
        agent={
            "name": record.agent.name,
            "version": record.agent.version,
            "model": record.agent.model,
        },
        day=_trace_day(record),
        key_files=_key_files(trace_map.nodes, max_files=max_files),
        key_steps=_key_steps(trace_map.nodes, max_steps=max_steps),
        provenance_color=provenance.get("provenance_color"),
        committed=provenance.get("committed"),
        commit_sha=provenance.get("commit_sha"),
        commit_subject=provenance.get("commit_subject"),
        outcome=_outcome(record),
        refs={
            "trace": record.trace_id,
            "map": f"ot://trace/{record.trace_id}/map",
            "card": f"ot://trace/{record.trace_id}/card",
        },
        limitations=["candidate_card_bounded"],
    )


def _trace_id_from_ref(ref: str, *, index_path: Path | None = None) -> str:
    if ref.startswith("ot://trace/"):
        path = ref.removeprefix("ot://trace/")
        if path.endswith("/card"):
            return path.removesuffix("/card")
        if path.endswith("/map"):
            return path.removesuffix("/map")
        return path.split("/", 1)[0]
    if ref.startswith("tmn:"):
        from .trace_index import get_map_node

        node = get_map_node(ref, index_path=index_path)
        if node is not None:
            return node.trace_id
    if ref.startswith("tu:"):
        from .trace_index import get_unit

        unit = get_unit(ref, index_path=index_path)
        if unit is not None:
            return unit.trace_id
        parts = ref.split(":")
        if len(parts) >= 3:
            return parts[1]
    if ref.startswith("t:"):
        return ref[2:]
    return ref


def _topic_page(
    topic: str,
    *,
    limit: int,
    latest_generation: bool,
    project: str | None,
    index_path: Path | None,
):
    from .trace_search_snapshot import SearchFilters, search_traces

    return search_traces(
        topic,
        SearchFilters(
            project=project,
            latest_generation=latest_generation,
        ),
        limit=limit,
    )


def _group_cards_by_day(
    topic: str, cards: list[CandidateCard], *, per_group: int
) -> list[DiscoveryGroup]:
    buckets: dict[str, list[CandidateCard]] = defaultdict(list)
    for card in cards:
        key = card.day or "unknown-day"
        if len(buckets[key]) < per_group:
            buckets[key].append(card)

    groups: list[DiscoveryGroup] = []
    for key in sorted(buckets, reverse=True):
        group_cards = buckets[key]
        groups.append(
            DiscoveryGroup(
                key=key,
                label=_day_label(key),
                count=len(group_cards),
                narrative=_group_narrative(topic, key, group_cards),
                cards=group_cards,
                refs={
                    "query": f"ot://trace-search/lex/{_ref_escape(topic)}?day={_ref_escape(key)}",
                    "first_card": group_cards[0].refs.get("card", "") if group_cards else "",
                },
            )
        )
    return groups


def _day_label(key: str) -> str:
    if key == "unknown-day":
        return "Unknown day"
    return key


def _group_narrative(topic: str, key: str, cards: list[CandidateCard]) -> str:
    files: list[str] = []
    for card in cards:
        for path in card.key_files:
            if path not in files:
                files.append(path)
            if len(files) >= 3:
                break
        if len(files) >= 3:
            break
    subject = f"{len(cards)} trace" + ("" if len(cards) == 1 else "s")
    detail = f" touching {', '.join(files)}" if files else ""
    return f"{subject} for {json.dumps(topic)} on {_day_label(key)}{detail}."


def _ref_escape(value: str) -> str:
    return value.replace(" ", "+").replace("/", "%2F").replace("?", "%3F")


def _load_trace_record(trace_id: str, *, index_path: Path | None = None) -> TraceRecord:
    from .trace_index import get_trace_path
    from .trace_search_snapshot import get_trace_source_path

    trace_path = get_trace_path(trace_id, index_path=index_path)
    if trace_path is None:
        trace_path = get_trace_source_path(trace_id)
    if trace_path is None or not trace_path.exists():
        raise FileNotFoundError(trace_id)
    bucket_obj = read_trace_record_object(trace_path)
    if bucket_obj is not None:
        return bucket_obj.record
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = load_record_json(line)
        if record.trace_id == trace_id:
            return record
    raise FileNotFoundError(trace_id)


def _trace_day(record: TraceRecord) -> str | None:
    ts = record.timestamp_start or record.timestamp_end
    if not ts:
        return None
    return ts[:10]


def _key_files(nodes: list[Any], *, max_files: int) -> list[str]:
    counts: Counter[str] = Counter()
    for node in nodes:
        for path in [*getattr(node, "files_modified", []), *getattr(node, "files_read", [])]:
            if path:
                counts[str(path)] += 1
    return [path for path, _count in counts.most_common(max_files)]


def _key_steps(nodes: list[Any], *, max_steps: int) -> list[dict[str, Any]]:
    wanted = {
        "user_instruction",
        "agent_plan",
        "file_edit",
        "test_run",
        "final_response",
    }
    out: list[dict[str, Any]] = []
    for node in sorted(nodes, key=lambda n: getattr(n, "step_index", 0) or 0):
        if getattr(node, "action_type", None) not in wanted:
            continue
        out.append(
            {
                "step_index": node.step_index,
                "action_type": node.action_type,
                "text": node.text_preview,
                "files": list(dict.fromkeys([*node.files_modified, *node.files_read])),
            }
        )
        if len(out) >= max_steps:
            break
    return out


def _outcome(record: TraceRecord) -> dict[str, Any]:
    payload = record.outcome.model_dump(mode="json")
    return {
        key: value
        for key, value in payload.items()
        if value not in (None, [], {}, "")
    }


def card_json(card: CandidateCard) -> str:
    return json.dumps(card.model_dump(mode="json"), indent=2, sort_keys=True)
