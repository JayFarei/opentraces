"""LLM polish for the ``pr-intent-summary-v1`` workflow output.

This module is the *only* place an LLM is invoked anywhere in the PR-render
pipeline. The renderer never calls it directly; the workflow's
``build_rows.py`` script calls it as an enrichment pass and stores the
result in the row JSONL. Other consumers (Slack, dashboard, CI) get the
same enrichment for free.

Backend selection
-----------------

The actual LLM call is delegated to :mod:`opentraces.core.llm_provider`,
which picks the best available provider:

* ``claude-cli`` (default) — wraps ``claude -p``, uses the user's
  Claude Code subscription (free with Pro/Max).
* ``codex-cli`` — reserved, not yet active.
* ``anthropic-api`` — direct SDK call, requires API key + ``[llm-api]``
  install extra.

If no provider is available, ``polish_branch`` returns ``None`` and the
workflow falls back to deterministic-only render.

Safety boundary
---------------

The model only ever sees data that is *already destined for the PR body*:

* commit subjects + truncated bodies (already public in git)
* user-typed specs that have already passed through ``redact_intent``
* aggregated rule-based ``did_what_bullets`` (deterministic English)
* numeric aggregates (file counts, line counts, trace counts)

It never receives raw file contents, raw tool-call inputs, or any text
that has not already cleared the security-redaction pipeline.

Grounding contract
------------------

The system prompt forbids the model from inventing file paths, commit
SHAs, or trace IDs. Output is validated against the input grounding
(commit SHAs must be in the input set, etc.). On validation failure we
retry once; second failure returns ``None`` and the workflow falls back.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .llm_provider import (
    DEFAULT_MODEL as PROVIDER_DEFAULT_MODEL,
    LLMProvider,
    ProviderError,
    ProviderResult,
    detect_provider,
)


DEFAULT_MODEL = PROVIDER_DEFAULT_MODEL

_MAX_SPECS_TO_INCLUDE = 12
_MAX_SPEC_CHARS = 500
_MAX_BODY_CHARS = 400


@dataclass(frozen=True)
class ConfidenceSignal:
    kind: str           # "ok" | "warn" | "info"
    claim: str
    evidence_command: str | None = None


@dataclass(frozen=True)
class CommitSummary:
    commit_sha: str
    summary: str


@dataclass(frozen=True)
class EnrichmentPayload:
    headline_what: str
    headline_why: str
    commit_summaries: list[CommitSummary] = field(default_factory=list)
    confidence_signals: list[ConfidenceSignal] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    provider_notes: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "headline_what": self.headline_what,
            "headline_why": self.headline_why,
            "commit_summaries": [
                {"commit_sha": cs.commit_sha, "summary": cs.summary}
                for cs in self.commit_summaries
            ],
            "confidence_signals": [
                {
                    "kind": cs.kind,
                    "claim": cs.claim,
                    "evidence_command": cs.evidence_command,
                }
                for cs in self.confidence_signals
            ],
            "model": self.model,
            "provider": self.provider,
            "provider_notes": self.provider_notes,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "EnrichmentPayload":
        return cls(
            headline_what=str(data.get("headline_what") or ""),
            headline_why=str(data.get("headline_why") or ""),
            commit_summaries=[
                CommitSummary(
                    commit_sha=str(item.get("commit_sha") or ""),
                    summary=str(item.get("summary") or ""),
                )
                for item in (data.get("commit_summaries") or [])
            ],
            confidence_signals=[
                ConfidenceSignal(
                    kind=str(item.get("kind") or "info"),
                    claim=str(item.get("claim") or ""),
                    evidence_command=item.get("evidence_command") or None,
                )
                for item in (data.get("confidence_signals") or [])
            ],
            model=str(data.get("model") or ""),
            provider=str(data.get("provider") or ""),
            provider_notes=str(data.get("provider_notes") or ""),
            input_tokens=int(data.get("input_tokens") or 0),
            output_tokens=int(data.get("output_tokens") or 0),
        )


class LLMPolishError(Exception):
    """Raised internally on unrecoverable polish failures; never escapes."""


def polish_branch(
    *,
    commits: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    model: str = DEFAULT_MODEL,
    provider: LLMProvider | None = None,
) -> EnrichmentPayload | None:
    """Polish branch lineage rows into a context-rich PR summary.

    Returns ``None`` on any failure (no provider available, schema-invalid
    output after retry, network error). The caller is expected to fall
    back to the deterministic-only render when ``None`` is returned.

    Parameters
    ----------
    commits:
        ``scope.commits`` from the workflow's run packet.
    rows:
        The deterministic per-commit rows produced earlier in
        ``build_rows.py``.
    model:
        Model id passed to the provider. Defaults to Haiku 4.5.
    provider:
        Optional explicit provider. If ``None``, ``detect_provider()``
        picks the best available backend (claude-cli > codex-cli >
        anthropic-api > None).
    """

    selected = provider if provider is not None else detect_provider()
    if selected is None:
        return None

    payload_input = _build_input_payload(commits=commits, rows=rows)
    if not payload_input["commits"]:
        return None

    try:
        return _call_provider(selected, payload_input, model=model)
    except (ProviderError, LLMPolishError):
        return None
    except Exception:  # noqa: BLE001 — graceful fallback on any unexpected error
        return None


# ---------------------------------------------------------------------------
# Input shaping
# ---------------------------------------------------------------------------


def _build_input_payload(
    *, commits: list[dict[str, Any]], rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reduce the workflow output to the minimal grounded context for the LLM."""

    by_sha = {row.get("commit_sha"): row for row in rows if row.get("commit_sha")}
    commit_payloads: list[dict[str, Any]] = []
    aggregated_files: set[str] = set()
    test_files = src_files = docs_files = 0

    for commit in commits:
        if commit.get("is_merge"):
            continue
        sha = commit.get("sha") or ""
        row = by_sha.get(sha) or {}
        lineage = row.get("lineage") or []
        ranked = sorted(
            lineage,
            key=lambda lin: (lin.get("credit") or {}).get("line_count", 0),
            reverse=True,
        )[:3]
        lineage_summaries = []
        for lin in ranked:
            credit = lin.get("credit") or {}
            bullets = lin.get("did_what_bullets") or []
            lineage_summaries.append({
                "trace_id": lin.get("trace_id") or "",
                "line_count": int(credit.get("line_count") or 0),
                "line_ratio": float(credit.get("line_ratio") or 0.0),
                "did_what": bullets[:3],
            })
        for f in (lineage[0].get("files_touched") or []) if lineage else []:
            aggregated_files.add(f)
            if "/test" in f or f.startswith("tests/") or "/test_" in f:
                test_files += 1
            elif f.startswith("src/") or "/src/" in f:
                src_files += 1
            elif f.endswith((".md", ".rst", ".txt")):
                docs_files += 1
        commit_payloads.append({
            "commit_sha": sha,
            "short_sha": commit.get("short_sha") or sha[:7],
            "subject": (commit.get("subject") or "")[:200],
            "body_truncated": (commit.get("body") or "")[:_MAX_BODY_CHARS],
            "trace_count": len(lineage),
            "lineages": lineage_summaries,
        })

    specs_seen: set[str] = set()
    specs: list[dict[str, str]] = []
    for row in rows:
        for lin in (row.get("lineage") or []):
            spec = (lin.get("intent") or {}).get("most_substantive_spec") or {}
            text = (spec.get("text") or "").strip()
            if not text or text in specs_seen:
                continue
            specs_seen.add(text)
            specs.append({
                "trace_id": lin.get("trace_id") or "",
                "text": text[:_MAX_SPEC_CHARS],
            })
            if len(specs) >= _MAX_SPECS_TO_INCLUDE:
                break
        if len(specs) >= _MAX_SPECS_TO_INCLUDE:
            break

    return {
        "commits": commit_payloads,
        "specs": specs,
        "aggregate": {
            "total_files_touched": len(aggregated_files),
            "test_files_touched": test_files,
            "src_files_touched": src_files,
            "docs_files_touched": docs_files,
            "trace_count_total": sum(c["trace_count"] for c in commit_payloads),
        },
    }


# ---------------------------------------------------------------------------
# Provider call + schema validation + retry
# ---------------------------------------------------------------------------


_JSON_SCHEMA = {
    "type": "object",
    "required": ["headline_what", "headline_why", "commit_summaries", "confidence_signals"],
    "properties": {
        "headline_what": {
            "type": "string",
            "description": (
                "2 to 3 sentences asserting what this PR ships. Be specific about "
                "the substrate added, modules touched, and user-visible behavior. "
                "Do not invent file paths or APIs not mentioned in the input."
            ),
        },
        "headline_why": {
            "type": "string",
            "description": (
                "1 to 2 sentences motivating the change, grounded in the user-typed "
                "specs. Quote or paraphrase user intent only — do not invent rationale."
            ),
        },
        "commit_summaries": {
            "type": "array",
            "description": (
                "One entry per non-merge commit in the input, in the same order. "
                "Each summary is at most one sentence. Use the exact commit_sha from "
                "the input."
            ),
            "items": {
                "type": "object",
                "required": ["commit_sha", "summary"],
                "properties": {
                    "commit_sha": {"type": "string"},
                    "summary": {"type": "string"},
                },
            },
        },
        "confidence_signals": {
            "type": "array",
            "description": (
                "Factual signals about this PR's quality and scope, each paired "
                "with the opentraces command that would verify the claim."
            ),
            "items": {
                "type": "object",
                "required": ["kind", "claim"],
                "properties": {
                    "kind": {"type": "string", "enum": ["ok", "warn", "info"]},
                    "claim": {"type": "string"},
                    "evidence_command": {"type": ["string", "null"]},
                },
            },
        },
    },
}


_SYSTEM_PROMPT = """You are summarizing a software pull request for an AI code reviewer.

You receive structured JSON about commits on the branch and the agent traces \
that authored them. Your job is to produce a tight, factual PR summary that \
the reviewer can verify command-by-command.

GROUNDING RULES — these are absolute, no exceptions:
1. Do NOT invent file paths, function names, commit SHAs, or trace IDs that \
do not appear in the input.
2. Do NOT invent test counts, line counts, or trace counts. Use only the \
numbers in `aggregate` and per-commit `lineages`.
3. Do NOT speculate about behavior beyond what the commit subjects, bodies, \
and `did_what` bullets state.
4. If you are unsure about a fact, OMIT it. The deterministic render is \
shown below your output and will fill any gap. Empty is better than wrong.
5. The user-typed `specs` are the source of truth for "why". Paraphrase or \
quote, do not synthesize new motivations.

OUTPUT STYLE:
- `headline_what`: 2-3 sentences, present tense, names the substrate added \
and the user-visible surface. Avoid hedge words ("appears to", "seems to").
- `headline_why`: 1-2 sentences, grounded in the specs. Frame as user need or \
constraint, not generic technical motivation.
- `commit_summaries`: 1 sentence each, in the same order as the input commits.
- `confidence_signals`: factual, pairwise (claim + verifying command). Avoid \
generic statements like "code quality is good"; prefer concrete signals \
("23 tests added in tests/integration/", "schema field X added — additive \
under VERSION-POLICY.md")."""


def _call_provider(
    provider: LLMProvider,
    payload_input: dict[str, Any],
    *,
    model: str,
    _retries_left: int = 1,
) -> EnrichmentPayload:
    user_message = (
        "Here is the structured input for the PR you are summarizing. "
        "Use only the facts present here.\n\n"
        f"```json\n{json.dumps(payload_input, indent=2)}\n```\n\n"
        "Produce the PR summary now."
    )
    result = provider.call_structured(
        system_prompt=_SYSTEM_PROMPT,
        user_message=user_message,
        json_schema=_JSON_SCHEMA,
        model=model,
    )
    try:
        validated = _validate_payload(result.payload, payload_input)
    except LLMPolishError:
        if _retries_left > 0:
            return _call_provider(
                provider, payload_input, model=model,
                _retries_left=_retries_left - 1,
            )
        raise

    return EnrichmentPayload(
        headline_what=validated["headline_what"],
        headline_why=validated["headline_why"],
        commit_summaries=[
            CommitSummary(
                commit_sha=item["commit_sha"],
                summary=item["summary"],
            )
            for item in validated["commit_summaries"]
        ],
        confidence_signals=[
            ConfidenceSignal(
                kind=item["kind"],
                claim=item["claim"],
                evidence_command=item.get("evidence_command"),
            )
            for item in validated["confidence_signals"]
        ],
        model=result.model,
        provider=result.provider_name,
        provider_notes=result.notes,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


def _validate_payload(
    raw: dict[str, Any], grounding: dict[str, Any],
) -> dict[str, Any]:
    """Validate the LLM output against schema + grounding constraints."""

    for key in ("headline_what", "headline_why", "commit_summaries", "confidence_signals"):
        if key not in raw:
            raise LLMPolishError(f"missing required field: {key}")

    if not isinstance(raw["headline_what"], str) or not raw["headline_what"].strip():
        raise LLMPolishError("headline_what must be a non-empty string")
    if not isinstance(raw["headline_why"], str) or not raw["headline_why"].strip():
        raise LLMPolishError("headline_why must be a non-empty string")

    valid_shas = {c["commit_sha"] for c in grounding["commits"]}
    summaries = raw["commit_summaries"]
    if not isinstance(summaries, list):
        raise LLMPolishError("commit_summaries must be a list")
    for entry in summaries:
        if not isinstance(entry, dict):
            raise LLMPolishError("commit_summaries entry not a dict")
        sha = entry.get("commit_sha")
        if sha not in valid_shas:
            raise LLMPolishError(f"commit_sha not in grounding: {sha}")
        if not isinstance(entry.get("summary"), str):
            raise LLMPolishError(f"summary missing for {sha}")

    signals = raw["confidence_signals"]
    if not isinstance(signals, list):
        raise LLMPolishError("confidence_signals must be a list")
    for sig in signals:
        if not isinstance(sig, dict):
            raise LLMPolishError("signal not a dict")
        if sig.get("kind") not in {"ok", "warn", "info"}:
            raise LLMPolishError(f"invalid signal kind: {sig.get('kind')}")
        if not isinstance(sig.get("claim"), str) or not sig["claim"].strip():
            raise LLMPolishError("signal missing claim")

    return raw


__all__ = [
    "CommitSummary",
    "ConfidenceSignal",
    "DEFAULT_MODEL",
    "EnrichmentPayload",
    "LLMPolishError",
    "polish_branch",
]
