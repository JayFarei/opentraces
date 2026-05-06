"""Deterministic semantic concepts for local trace search.

This is the lightweight layer before embeddings: it turns concrete trace
evidence such as dependencies, package names, service names, commands, and
prompt text into stable service/library concepts that can be indexed locally.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from opentraces_schema import TraceFacet


SEMANTIC_SCHEMA_VERSION = "opentraces.semantic.v1"


@dataclass(frozen=True)
class SemanticConcept:
    concept_id: str
    kind: str
    label: str
    aliases: tuple[str, ...]
    categories: tuple[str, ...] = ()


CONCEPTS: tuple[SemanticConcept, ...] = (
    SemanticConcept(
        concept_id="service:mongodb",
        kind="service",
        label="MongoDB",
        aliases=(
            "mongodb",
            "mongo db",
            "mongo",
            "pymongo",
            "motor",
            "mongoose",
            "mongosh",
            "mongodb atlas",
            "mongo atlas",
            "atlas search",
            "mongo client",
            "mongoclient",
        ),
        categories=("database", "document database", "cloud database"),
    ),
    SemanticConcept(
        concept_id="service:huggingface",
        kind="service",
        label="Hugging Face",
        aliases=(
            "hugging face",
            "huggingface",
            "huggingface_hub",
            "hf hub",
            "transformers",
            "diffusers",
            "dependency datasets",
            "import datasets",
            "from datasets",
            "dependency accelerate",
            "import accelerate",
            "hub dataset",
        ),
        categories=("machine learning", "model hub", "dataset hub"),
    ),
    SemanticConcept(
        concept_id="library:clack",
        kind="library",
        label="Clack",
        aliases=(
            "clack",
            "@clack/prompts",
            "clack prompts",
            "interactive prompts",
            "typescript prompts",
        ),
        categories=("typescript", "cli prompts", "interactive cli"),
    ),
    SemanticConcept(
        concept_id="service:openai",
        kind="service",
        label="OpenAI",
        aliases=("openai", "openai api", "responses api", "chatgpt", "codex"),
        categories=("llm", "ai platform"),
    ),
    SemanticConcept(
        concept_id="service:anthropic",
        kind="service",
        label="Anthropic",
        aliases=("anthropic", "claude", "claude code", "claude-code"),
        categories=("llm", "ai platform", "coding agent"),
    ),
)


def semantic_facets_for_trace(
    record: Any,
    *,
    files: Iterable[str] = (),
    skills: Iterable[str] = (),
) -> list[TraceFacet]:
    """Infer semantic facets from a TraceRecord-like object."""

    material = _trace_material(record, files=files, skills=skills)
    matches = match_semantic_concepts(material)
    facets: list[TraceFacet] = []
    for concept, aliases in matches:
        evidence_ref = f"semantic:{concept.concept_id}"
        facets.extend(
            [
                TraceFacet(
                    name="semantic.concept_id",
                    value=concept.concept_id,
                    source="heuristic",
                    confidence="medium",
                    evidence_ref=evidence_ref,
                ),
                TraceFacet(
                    name="semantic.name",
                    value=_canonical_name(concept.label),
                    source="heuristic",
                    confidence="medium",
                    evidence_ref=evidence_ref,
                ),
                TraceFacet(
                    name="semantic.kind",
                    value=concept.kind,
                    source="heuristic",
                    confidence="medium",
                    evidence_ref=evidence_ref,
                ),
            ]
        )
        for alias in aliases:
            facets.append(
                TraceFacet(
                    name="semantic.alias",
                    value=alias,
                    source="heuristic",
                    confidence="medium",
                    evidence_ref=evidence_ref,
                )
            )
        for category in concept.categories:
            facets.append(
                TraceFacet(
                    name="semantic.category",
                    value=category,
                    source="heuristic",
                    confidence="medium",
                    evidence_ref=evidence_ref,
                )
            )
    return _dedupe_facets(facets)


def semantic_profile_from_facets(facets: Iterable[TraceFacet]) -> dict[str, Any]:
    """Return a projection-friendly semantic profile from TraceFacet rows."""

    by_id: dict[str, dict[str, Any]] = {}
    for facet in facets:
        if not facet.name.startswith("semantic."):
            continue
        concept_id = _concept_id_from_ref(facet.evidence_ref) or (
            str(facet.value) if facet.name == "semantic.concept_id" else None
        )
        if not concept_id:
            continue
        entry = by_id.setdefault(
            concept_id,
            {
                "concept_id": concept_id,
                "name": None,
                "kind": None,
                "aliases": set(),
                "categories": set(),
            },
        )
        if facet.name == "semantic.name":
            entry["name"] = str(facet.value)
        elif facet.name == "semantic.kind":
            entry["kind"] = str(facet.value)
        elif facet.name == "semantic.alias":
            entry["aliases"].add(str(facet.value))
        elif facet.name == "semantic.category":
            entry["categories"].add(str(facet.value))

    concepts = []
    for concept_id, entry in sorted(by_id.items()):
        concepts.append(
            {
                "concept_id": concept_id,
                "name": entry["name"],
                "kind": entry["kind"],
                "aliases": sorted(entry["aliases"]),
                "categories": sorted(entry["categories"]),
            }
        )
    return {
        "schema_version": SEMANTIC_SCHEMA_VERSION,
        "concept_ids": [concept["concept_id"] for concept in concepts],
        "concepts": concepts,
    }


def expand_semantic_query(query: str) -> dict[str, Any]:
    """Expand a semantic query to deterministic concept ids and aliases."""

    matches = match_semantic_concepts([query])
    concept_ids = [concept.concept_id for concept, _aliases in matches]
    terms: set[str] = {_normalise_query_text(query)}
    concepts: list[dict[str, Any]] = []
    for concept, aliases in matches:
        terms.add(concept.label)
        terms.update(concept.aliases)
        terms.update(concept.categories)
        concepts.append(
            {
                "concept_id": concept.concept_id,
                "name": _canonical_name(concept.label),
                "kind": concept.kind,
                "matched_aliases": sorted(aliases),
                "expanded_aliases": sorted(concept.aliases),
                "categories": sorted(concept.categories),
            }
        )
    return {
        "schema_version": SEMANTIC_SCHEMA_VERSION,
        "query": query,
        "concept_ids": concept_ids,
        "terms": sorted(term for term in terms if term),
        "concepts": concepts,
    }


def match_semantic_concepts(
    material: Iterable[str],
) -> list[tuple[SemanticConcept, tuple[str, ...]]]:
    text = _normalise_search_blob(material)
    matches: list[tuple[SemanticConcept, tuple[str, ...]]] = []
    for concept in CONCEPTS:
        matched_aliases = tuple(
            alias for alias in concept.aliases if _contains_alias(text, alias)
        )
        if matched_aliases:
            matches.append((concept, matched_aliases))
    return matches


def _trace_material(
    record: Any,
    *,
    files: Iterable[str],
    skills: Iterable[str],
) -> list[str]:
    material: list[str] = []
    task = getattr(record, "task", None)
    description = getattr(task, "description", None)
    if description:
        material.append(str(description))
    for dependency in getattr(record, "dependencies", []) or []:
        material.append(str(dependency))
        material.append(f"dependency {dependency}")
    material.extend(str(item) for item in files)
    material.extend(str(item) for item in skills)
    for step in getattr(record, "steps", []) or []:
        content = getattr(step, "content", None)
        if content:
            material.append(str(content))
        for call in getattr(step, "tool_calls", []) or []:
            material.append(str(getattr(call, "tool_name", "")))
            input_payload = getattr(call, "input", {}) or {}
            if isinstance(input_payload, dict):
                material.extend(_flatten_values(input_payload))
            else:
                material.append(str(input_payload))
        for observation in getattr(step, "observations", []) or []:
            for attr in ("content", "output_summary", "error"):
                value = getattr(observation, attr, None)
                if value:
                    material.append(str(value))
    return material


def _flatten_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_flatten_values(item))
        return out
    if isinstance(value, dict):
        out: list[str] = []
        for key, item in value.items():
            out.append(str(key))
            out.extend(_flatten_values(item))
        return out
    try:
        return [json.dumps(value, sort_keys=True)]
    except TypeError:
        return [str(value)]


def _normalise_search_blob(material: Iterable[str]) -> str:
    return f" {_normalise_query_text(' '.join(str(item) for item in material))} "


def _normalise_query_text(text: str) -> str:
    lowered = text.lower()
    lowered = lowered.replace("_", " ").replace("-", " ")
    lowered = re.sub(r"[^a-z0-9@./+]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _contains_alias(text: str, alias: str) -> bool:
    normalised_alias = _normalise_query_text(alias)
    if not normalised_alias:
        return False
    # Single short aliases are noisy; only accept them when surrounded by
    # token boundaries.
    if len(normalised_alias) <= 3 and normalised_alias.isalnum():
        return f" {normalised_alias} " in text
    return normalised_alias in text


def _canonical_name(label: str) -> str:
    return _normalise_query_text(label).replace(" ", "_")


def _concept_id_from_ref(ref: str | None) -> str | None:
    if not ref or not ref.startswith("semantic:"):
        return None
    return ref.removeprefix("semantic:")


def _dedupe_facets(facets: list[TraceFacet]) -> list[TraceFacet]:
    seen: set[tuple[str, str]] = set()
    out: list[TraceFacet] = []
    for facet in facets:
        key = (facet.name, str(facet.value))
        if key in seen:
            continue
        seen.add(key)
        out.append(facet)
    return out
