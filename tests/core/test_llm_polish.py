"""Tests for ``core.llm_polish`` — provider-agnostic LLM enrichment.

These tests mock the provider abstraction (``llm_provider.LLMProvider``)
so they don't depend on which backend ships. The provider's individual
adapters (claude-cli, codex-cli, anthropic-api) are tested separately
in ``test_llm_provider.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from opentraces.core import llm_polish
from opentraces.core.llm_provider import (
    LLMProvider,
    ProviderError,
    ProviderResult,
)


def _commit(sha: str, subject: str = "x", is_merge: bool = False) -> dict:
    return {
        "sha": sha, "short_sha": sha[:7], "subject": subject,
        "body": "", "is_merge": is_merge,
    }


def _row(sha: str, *, lineage: list[dict] | None = None, is_merge: bool = False) -> dict:
    return {
        "commit_sha": sha,
        "short_sha": sha[:7],
        "subject": "x",
        "is_merge": is_merge,
        "lineage": lineage or [],
        "no_lineage_reason": None if lineage else "no_burst",
    }


def _lineage(*, trace_id: str = "t1", spec: str = "do the thing", line_count: int = 100) -> dict:
    return {
        "trace_id": trace_id,
        "credit": {"line_count": line_count, "line_ratio": 0.9, "overlap_case": "has_entities"},
        "intent": {
            "most_substantive_spec": {"step": 1, "text": spec},
            "spec_chain_count": 1,
        },
        "did_what_bullets": ["+ Added foo in bar.py"],
        "files_touched": ["bar.py"],
    }


def _fake_provider(
    *,
    payload: dict[str, Any] | None = None,
    error: Exception | None = None,
    payloads: list[dict[str, Any]] | None = None,
    name: str = "test-provider",
    notes: str = "fake provider for tests",
) -> MagicMock:
    """Build a MagicMock that satisfies the LLMProvider protocol."""

    provider = MagicMock(spec=LLMProvider)
    provider.name = name
    provider.is_available.return_value = True
    if error is not None:
        provider.call_structured.side_effect = error
        return provider
    if payloads is not None:
        provider.call_structured.side_effect = [
            ProviderResult(
                payload=p, provider_name=name, model="test-model",
                input_tokens=10, output_tokens=20, notes=notes,
            )
            for p in payloads
        ]
        return provider
    provider.call_structured.return_value = ProviderResult(
        payload=payload or {},
        provider_name=name, model="test-model",
        input_tokens=10, output_tokens=20, notes=notes,
    )
    return provider


# ─── input shaping ─────────────────────────────────────────────────────────


def test_input_payload_drops_merge_commits():
    payload = llm_polish._build_input_payload(
        commits=[
            _commit("a"),
            _commit("b", is_merge=True),
            _commit("c"),
        ],
        rows=[_row("a", lineage=[_lineage()]), _row("c")],
    )
    shas = [c["commit_sha"] for c in payload["commits"]]
    assert shas == ["a", "c"]


def test_input_payload_dedupes_specs():
    payload = llm_polish._build_input_payload(
        commits=[_commit("a"), _commit("b")],
        rows=[
            _row("a", lineage=[_lineage(trace_id="t1", spec="refactor X")]),
            _row("b", lineage=[_lineage(trace_id="t2", spec="refactor X")]),
        ],
    )
    spec_texts = [s["text"] for s in payload["specs"]]
    assert spec_texts.count("refactor X") == 1


def test_input_payload_caps_specs_at_max():
    rows = [
        _row(f"sha{i}", lineage=[_lineage(trace_id=f"t{i}", spec=f"unique spec {i}")])
        for i in range(50)
    ]
    commits = [_commit(f"sha{i}") for i in range(50)]
    payload = llm_polish._build_input_payload(commits=commits, rows=rows)
    assert len(payload["specs"]) <= llm_polish._MAX_SPECS_TO_INCLUDE


# ─── graceful fallback ─────────────────────────────────────────────────────


def test_polish_returns_none_when_no_provider_available(monkeypatch):
    """detect_provider returns None → polish_branch returns None."""

    monkeypatch.setattr(llm_polish, "detect_provider", lambda: None)
    result = llm_polish.polish_branch(
        commits=[_commit("a")], rows=[_row("a", lineage=[_lineage()])],
    )
    assert result is None


def test_polish_returns_none_when_no_non_merge_commits():
    """No commits to summarize → return None even with a working provider."""

    provider = _fake_provider(payload={
        "headline_what": "x", "headline_why": "y",
        "commit_summaries": [], "confidence_signals": [],
    })
    result = llm_polish.polish_branch(
        commits=[_commit("a", is_merge=True)],
        rows=[_row("a", is_merge=True)],
        provider=provider,
    )
    assert result is None
    provider.call_structured.assert_not_called()


def test_polish_returns_none_when_provider_raises():
    """Provider error → graceful None, no exception escapes."""

    provider = _fake_provider(error=ProviderError("network down"))
    result = llm_polish.polish_branch(
        commits=[_commit("a")], rows=[_row("a", lineage=[_lineage()])],
        provider=provider,
    )
    assert result is None


# ─── happy-path ────────────────────────────────────────────────────────────


def test_polish_happy_path_returns_validated_payload():
    payload = {
        "headline_what": "Adds the X feature with deterministic Y.",
        "headline_why": "Users needed Z; the existing flow couldn't express it.",
        "commit_summaries": [
            {"commit_sha": "a", "summary": "Implements X core."},
            {"commit_sha": "c", "summary": "Adds tests for X."},
        ],
        "confidence_signals": [
            {"kind": "ok", "claim": "All commits traced", "evidence_command": "ot trail blame commit a"},
        ],
    }
    provider = _fake_provider(payload=payload, name="claude-cli", notes="via Claude Code")
    result = llm_polish.polish_branch(
        commits=[_commit("a"), _commit("c")],
        rows=[_row("a", lineage=[_lineage()]), _row("c", lineage=[_lineage()])],
        provider=provider,
    )

    assert result is not None
    assert result.headline_what.startswith("Adds the X feature")
    assert result.headline_why.startswith("Users needed Z")
    assert len(result.commit_summaries) == 2
    assert result.commit_summaries[0].commit_sha == "a"
    assert len(result.confidence_signals) == 1
    assert result.confidence_signals[0].kind == "ok"
    assert result.provider == "claude-cli"
    assert result.provider_notes == "via Claude Code"
    assert result.input_tokens == 10
    assert result.output_tokens == 20


# ─── validation: rejects hallucinated SHAs ─────────────────────────────────


def test_polish_rejects_invented_commit_sha_then_retries_then_fails():
    bad_payload = {
        "headline_what": "x", "headline_why": "y",
        "commit_summaries": [
            {"commit_sha": "INVENTED", "summary": "this sha is not in the input"},
        ],
        "confidence_signals": [],
    }
    # Provider returns the same bad payload on both calls.
    provider = _fake_provider(payloads=[bad_payload, bad_payload])
    result = llm_polish.polish_branch(
        commits=[_commit("a")], rows=[_row("a", lineage=[_lineage()])],
        provider=provider,
    )
    assert result is None
    # Confirms retry happened (called twice: original + 1 retry).
    assert provider.call_structured.call_count == 2


def test_polish_validation_recovers_on_retry():
    bad_payload = {
        "headline_what": "x", "headline_why": "y",
        "commit_summaries": [{"commit_sha": "BOGUS", "summary": "x"}],
        "confidence_signals": [],
    }
    good_payload = {
        "headline_what": "ok", "headline_why": "ok",
        "commit_summaries": [{"commit_sha": "a", "summary": "ok"}],
        "confidence_signals": [],
    }
    provider = _fake_provider(payloads=[bad_payload, good_payload])
    result = llm_polish.polish_branch(
        commits=[_commit("a")], rows=[_row("a", lineage=[_lineage()])],
        provider=provider,
    )
    assert result is not None
    assert result.commit_summaries[0].commit_sha == "a"
    assert provider.call_structured.call_count == 2


# ─── EnrichmentPayload roundtrip ───────────────────────────────────────────


def test_enrichment_payload_json_roundtrip():
    p = llm_polish.EnrichmentPayload(
        headline_what="W",
        headline_why="Y",
        commit_summaries=[llm_polish.CommitSummary(commit_sha="a", summary="s")],
        confidence_signals=[
            llm_polish.ConfidenceSignal(kind="ok", claim="c", evidence_command="cmd"),
        ],
        model="m",
        provider="claude-cli",
        provider_notes="via Claude Code",
        input_tokens=10,
        output_tokens=20,
    )
    roundtripped = llm_polish.EnrichmentPayload.from_json(p.to_json())
    assert roundtripped == p
