"""Plan 032 end-to-end integration smoke.

Threads the new pieces together against a synthesized trace-like
content:
  - Part E filters skip metadata paths and base64 blobs.
  - Part D EntityMap renders named placeholders.
  - Part C parse-error state blocks upload.
  - Part B token resolver finds the configured source.
  - Part A TruffleHog gate runs only when config enables it.
  - Part F1 FakeProvider drives F2 and F3.
  - Part F2 detects PII and registers into EntityMap.
  - Part F3 aggregates per-chunk verdicts pessimistically.
"""

from __future__ import annotations

import pytest

from opentraces.core.config import Config, TruffleHogConfig
from opentraces.capture._base import ParseOutcome
from opentraces.security.anonymizer import EntityMap, normalize_user_paths
from opentraces.security.llm_provider import FakeProvider
from opentraces.security.llm_review import review_trace
from opentraces.security.pii_detector import LLMPIIDetector
from opentraces.security.scanner import is_base64_blob, is_safe_field_path
from opentraces.security.tools.trufflehog_tool import TruffleHogDetector
from opentraces.security.version import SECURITY_VERSION
from opentraces.core.state import StateManager, TraceStatus
from opentraces.publish.huggingface.upload import verify_hf_token


def test_security_version_is_0_6_0() -> None:
    assert SECURITY_VERSION == "0.6.0"


def test_full_stack_integration(monkeypatch, tmp_path) -> None:
    # ---- Part E: filters skip metadata, scan content.
    assert is_safe_field_path("steps.0.type")
    assert not is_safe_field_path("steps.0.content")
    assert is_base64_blob("A" * 300, {"mimeType": "image/png"})

    # ---- Part D: EntityMap produces stable placeholders.
    em = EntityMap()
    em.get_placeholder("PERSON", "Sarah")
    assert em.apply_all("Sarah wrote") == "[PERSON_1] wrote"
    assert normalize_user_paths("/Users/sarah.chen/x") == "/Users/user/x"

    # ---- Part C: parse outcome with errors is blocked through state.
    outcome = ParseOutcome(errors=["bad step at 42"])
    assert outcome.is_blocked()

    sm = StateManager(tmp_path / "state.json")
    sm.block_trace("t1", reason=outcome.block_reason())
    assert sm.get_trace("t1").status == TraceStatus.BLOCKED
    # And that trace can't be picked up for upload.
    assert "t1" not in {e.trace_id for e in sm.get_pending_upload_traces()}

    # ---- Part B: token resolver finds HF_TOKEN.
    monkeypatch.setenv("HF_TOKEN", "hf_integration_test")
    monkeypatch.setattr(
        "opentraces.publish.huggingface.upload._HF_CACHE_TOKEN_PATH",
        tmp_path / "nope",
    )
    token, source = verify_hf_token()
    assert token == "hf_integration_test"
    assert "HF_TOKEN" in source

    # ---- Part A: disabled config means the TruffleHog tool is inert.
    cfg = Config()
    assert cfg.security.trufflehog.enabled is False
    assert TruffleHogDetector().enabled(cfg) is False

    # ---- Part F2: PII detector threads into the same EntityMap and
    # respects Part E filters when routed through detect_for_path.
    provider = FakeProvider(responses=[
        {"entities": [{"type": "PERSON", "text": "Sarah"}]},
    ])
    detector = LLMPIIDetector(provider=provider, entity_map=em)
    found = detector.detect_for_path(
        path="steps.1.content",
        value="Sarah also emailed later",
        field_type="GENERAL",
        siblings={},
    )
    assert ("PERSON", "Sarah") in found

    # ---- Part F3: review aggregates chunks pessimistically.
    review_provider = FakeProvider(responses=[
        {
            "shareable": "yes",
            "missed_sensitive_data": "no",
            "flagged_parts": [],
            "summary": "ok",
        },
        {
            "shareable": "manual_review",
            "missed_sensitive_data": "maybe",
            "flagged_parts": [{"reason": "unclear", "evidence": "x"}],
            "summary": "uncertain",
        },
    ])
    verdict = review_trace(
        steps=["a" * 300_000, "b" * 300_000],
        provider=review_provider,
        max_chars_per_chunk=400_000,
    )
    assert verdict.shareable == "manual_review"
    assert verdict.missed_sensitive_data == "maybe"
    assert len(verdict.flagged_parts) == 1
