"""Part F2 tests: Tier 1.8 per-field LLM PII detection.

Detects entity types that regex/entropy cannot catch semantically:
PERSON, EMAIL, API_KEY (semantic), INTERNAL_URL, IP_ADDR, USER_PATH,
CREDENTIAL, ORG_NAME, EMPLOYEE_ID, PHONE, SENSITIVE_DATA.

Detected entities register in an EntityMap so Part D's named
placeholders apply. Hallucinations are rejected — an entity text that
does not appear verbatim in the source is dropped.
"""

from __future__ import annotations

import pytest

from opentraces.security.anonymizer import EntityMap
from opentraces.security.llm_provider import FakeProvider
from opentraces.security.pii_detector import LLMPIIDetector


def _provider(*responses: dict) -> FakeProvider:
    return FakeProvider(responses=list(responses))


class TestDetect:
    def test_returns_entity_tuples(self) -> None:
        p = _provider(
            {"entities": [
                {"type": "PERSON", "text": "Alice"},
                {"type": "EMAIL", "text": "alice@corp.com"},
            ]}
        )
        det = LLMPIIDetector(provider=p)
        got = det.detect("Alice sent mail to alice@corp.com", "TOOL_INPUT")
        assert ("PERSON", "Alice") in got
        assert ("EMAIL", "alice@corp.com") in got

    def test_hallucinated_entity_rejected(self) -> None:
        p = _provider(
            {"entities": [
                {"type": "PERSON", "text": "Alice"},
                {"type": "PERSON", "text": "Zelda"},  # never appears
            ]}
        )
        det = LLMPIIDetector(provider=p)
        got = det.detect("Alice is here", "TOOL_INPUT")
        names = [t for _, t in got]
        assert "Alice" in names
        assert "Zelda" not in names

    def test_empty_text_no_call(self) -> None:
        p = _provider({"entities": []})
        det = LLMPIIDetector(provider=p)
        got = det.detect("", "TOOL_INPUT")
        assert got == []
        assert p.prompts_seen == []

    def test_malformed_response_returns_empty(self) -> None:
        p = _provider({"not_entities": "oops"})
        det = LLMPIIDetector(provider=p)
        got = det.detect("some content", "TOOL_INPUT")
        assert got == []


class TestCache:
    def test_identical_text_cached(self) -> None:
        p = _provider(
            {"entities": [{"type": "PERSON", "text": "Alice"}]},
            # A second response would be used only if cache missed.
            {"entities": [{"type": "PERSON", "text": "ShouldNotShow"}]},
        )
        det = LLMPIIDetector(provider=p)
        first = det.detect("Alice", "TOOL_INPUT")
        second = det.detect("Alice", "TOOL_INPUT")
        assert first == second
        # Only one prompt actually sent.
        assert len(p.prompts_seen) == 1

    def test_different_texts_not_shared(self) -> None:
        p = _provider(
            {"entities": [{"type": "PERSON", "text": "Alice"}]},
            {"entities": [{"type": "PERSON", "text": "Bob"}]},
        )
        det = LLMPIIDetector(provider=p)
        det.detect("Alice", "TOOL_INPUT")
        det.detect("Bob", "TOOL_INPUT")
        assert len(p.prompts_seen) == 2


class TestEntityMapIntegration:
    def test_registers_into_entity_map(self) -> None:
        p = _provider(
            {"entities": [
                {"type": "PERSON", "text": "Alice"},
                {"type": "EMAIL", "text": "a@b.com"},
            ]}
        )
        em = EntityMap()
        det = LLMPIIDetector(provider=p, entity_map=em)
        det.detect("Alice mailed a@b.com", "TOOL_INPUT")
        assert em.get_placeholder("PERSON", "Alice") == "[PERSON_1]"
        assert em.get_placeholder("EMAIL", "a@b.com") == "[EMAIL_1]"


class TestFieldFiltering:
    def test_safe_path_skipped(self) -> None:
        p = _provider({"entities": [{"type": "PERSON", "text": "X"}]})
        det = LLMPIIDetector(provider=p)
        got = det.detect_for_path(
            path="steps.0.type",
            value="Alice",
            field_type="TOOL_INPUT",
            siblings={},
        )
        assert got == []
        assert p.prompts_seen == []

    def test_base64_blob_skipped(self) -> None:
        p = _provider({"entities": [{"type": "PERSON", "text": "X"}]})
        det = LLMPIIDetector(provider=p)
        blob = "A" * 300
        got = det.detect_for_path(
            path="attachments.0.data",
            value=blob,
            field_type="TOOL_INPUT",
            siblings={"mimeType": "image/png"},
        )
        assert got == []
        assert p.prompts_seen == []

    def test_normal_field_is_scanned(self) -> None:
        p = _provider(
            {"entities": [{"type": "PERSON", "text": "Alice"}]}
        )
        det = LLMPIIDetector(provider=p)
        got = det.detect_for_path(
            path="steps.0.content",
            value="Alice wrote the note",
            field_type="GENERAL",
            siblings={},
        )
        assert ("PERSON", "Alice") in got
