"""Tier 1.8: per-field LLM PII detection.

Optional layer that sits between Tier 1.5 (TruffleHog) and Tier 2 LLM
semantic review. Uses the :class:`LLMProvider` interface from
:mod:`llm_provider` so contributors can plug in any backend.

Detects entity types that regex/entropy cannot catch semantically:
PERSON, EMAIL, API_KEY (semantic), INTERNAL_URL, IP_ADDR, USER_PATH,
CREDENTIAL, ORG_NAME, EMPLOYEE_ID, PHONE, SENSITIVE_DATA.

Validates every returned ``entity_text`` against the source — a
hallucinated entity that does not appear verbatim is dropped.
Results register into an :class:`EntityMap` from Part D so Named
Placeholders like ``[PERSON_1]`` can be produced downstream.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from .anonymizer import EntityMap
from .llm_provider import LLMProvider
from .scanner import is_base64_blob, is_safe_field_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template (v1)
# ---------------------------------------------------------------------------

PROMPT_VERSION = "1"

_PROMPT_TEMPLATE = """You are a PII detection service for an AI coding agent trace.

Identify personally identifiable information and credentials in the TEXT below.
Respond with ONLY a JSON object of the form:

  {{"entities": [{{"type": "<TYPE>", "text": "<verbatim substring>"}}, ...]}}

Allowed TYPE values:
  PERSON, EMAIL, API_KEY, INTERNAL_URL, IP_ADDR, USER_PATH,
  CREDENTIAL, ORG_NAME, EMPLOYEE_ID, PHONE, SENSITIVE_DATA

Rules:
  - "text" MUST appear verbatim in the TEXT. No paraphrasing.
  - Return an empty entities array when nothing is sensitive.
  - FIELD_TYPE is hint-only: {field_type}

TEXT:
{content}
"""


@dataclass
class PIIFinding:
    entity_type: str
    entity_text: str
    field_type: str


class LLMPIIDetector:
    """Tier 1.8 per-field PII detector.

    Caches identical content within a session by SHA-256 so repeated
    strings never hit the provider twice. Optionally threads results
    into an :class:`EntityMap` so downstream redaction renders named
    placeholders.
    """

    def __init__(
        self,
        provider: LLMProvider,
        entity_map: EntityMap | None = None,
    ) -> None:
        self.provider = provider
        self.entity_map = entity_map
        self._cache: dict[str, list[tuple[str, str]]] = {}

    # ------------------------------------------------------------------
    # Core detection
    # ------------------------------------------------------------------

    def detect(
        self,
        text: str,
        field_type: str,
    ) -> list[tuple[str, str]]:
        """Return ``(entity_type, entity_text)`` pairs found in ``text``."""
        if not text:
            return []

        key = self._cache_key(text, field_type)
        cached = self._cache.get(key)
        if cached is not None:
            # Re-register into entity_map so placeholders accumulate
            # correctly across repeated calls on the same text.
            self._register_all(cached)
            return list(cached)

        prompt = _PROMPT_TEMPLATE.format(
            field_type=field_type,
            content=text,
        )

        try:
            response = self.provider.complete_json(
                prompt,
                schema_hint={"entities": "list"},
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("LLM PII detection call failed: %s", exc)
            self._cache[key] = []
            return []

        pairs = self._parse_entities(response, source=text)
        self._cache[key] = pairs
        self._register_all(pairs)
        return pairs

    def detect_for_path(
        self,
        path: str,
        value: Any,
        field_type: str,
        siblings: dict[str, Any],
    ) -> list[tuple[str, str]]:
        """Same as :meth:`detect` but honors Part E field filters.

        Skips the LLM call entirely for safe metadata paths
        (``.type``, ``.id``, …) and for inline base64 blobs (sibling
        ``mimeType`` + length ≥ 256).
        """
        if is_safe_field_path(path):
            return []
        if is_base64_blob(value, siblings):
            return []
        if not isinstance(value, str):
            return []
        return self.detect(value, field_type)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cache_key(text: str, field_type: str) -> str:
        h = hashlib.sha256()
        h.update(field_type.encode("utf-8"))
        h.update(b"\0")
        h.update(text.encode("utf-8"))
        return h.hexdigest()

    @staticmethod
    def _parse_entities(
        response: dict[str, Any],
        source: str,
    ) -> list[tuple[str, str]]:
        """Validate and filter the provider's entity list.

        Every ``entity_text`` must appear verbatim in ``source``; any
        hallucinated entity is dropped silently.
        """
        entities = response.get("entities")
        if not isinstance(entities, list):
            return []

        out: list[tuple[str, str]] = []
        for item in entities:
            if not isinstance(item, dict):
                continue
            etype = item.get("type")
            etext = item.get("text")
            if not isinstance(etype, str) or not isinstance(etext, str):
                continue
            if not etext:
                continue
            if etext not in source:
                # Hallucinated entity — drop.
                continue
            out.append((etype, etext))
        return out

    def _register_all(self, pairs: list[tuple[str, str]]) -> None:
        """Register every pair into the entity map, if one is attached."""
        if self.entity_map is None:
            return
        for etype, etext in pairs:
            self.entity_map.get_placeholder(etype, etext)
