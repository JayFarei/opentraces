"""LLMProvider protocol and reference implementations.

The LLM layer is an optional interface used by Tier 1.8 (per-field PII
detection) and Tier 2 LLM (session-level semantic review). Nothing in
the default pipeline calls an LLM; contributors have to configure a
provider and opt in explicitly.

Shipped providers:
  - :class:`FakeProvider` — deterministic for tests.
  - :class:`OllamaProvider` — local inference via http://localhost:11434.
  - :class:`AnthropicProvider` — hosted API via the ``anthropic`` SDK.

All implementations satisfy the :class:`LLMProvider` runtime protocol —
new providers need only implement ``complete_json()`` and a ``model``
attribute.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class LLMProvider(Protocol):
    """Structural contract for LLM-backed tiers."""

    model: str

    def complete_json(
        self,
        prompt: str,
        schema_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a JSON-parseable dict from the provider.

        Implementations must:
          - Return a dict (not a JSON string).
          - Tolerate a surrounding prose prefix/suffix if the provider
            cannot be coerced into strict JSON mode — extract the first
            JSON object found.
          - Raise ``RuntimeError`` on unrecoverable failure.
        """
        ...


# ---------------------------------------------------------------------------
# FakeProvider
# ---------------------------------------------------------------------------


class FakeProvider:
    """In-memory deterministic provider used by the test suite.

    Returns each response in ``responses`` in order, raising when the
    list is exhausted. Records every prompt in :attr:`prompts_seen` so
    tests can assert on what the caller sent.
    """

    def __init__(self, responses: list[dict[str, Any]], model: str = "fake") -> None:
        self.model = model
        self._responses = list(responses)
        self._cursor = 0
        self.prompts_seen: list[str] = []

    def complete_json(
        self,
        prompt: str,
        schema_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.prompts_seen.append(prompt)
        if self._cursor >= len(self._responses):
            raise RuntimeError("FakeProvider exhausted: no responses left")
        out = self._responses[self._cursor]
        self._cursor += 1
        return out


# ---------------------------------------------------------------------------
# OllamaProvider
# ---------------------------------------------------------------------------


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_first_json_object(text: str) -> dict[str, Any]:
    """Best-effort: extract the first JSON object from a free-form string."""
    match = _JSON_OBJ_RE.search(text or "")
    if match is None:
        raise RuntimeError(f"No JSON object found in LLM response: {text!r}")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Malformed JSON in LLM response: {exc}") from exc


class OllamaProvider:
    """Local inference via the Ollama HTTP API."""

    DEFAULT_ENDPOINT = "http://localhost:11434/api/generate"

    def __init__(
        self,
        model: str = "gemma4:e4b",
        endpoint: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.endpoint = endpoint or self.DEFAULT_ENDPOINT
        self.timeout = timeout

    def complete_json(
        self,
        prompt: str,
        schema_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Imported lazily so the provider module stays importable without
        # ``requests`` / ``httpx`` installed (only needed at call time).
        import urllib.error
        import urllib.request

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc

        try:
            wrapper = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Ollama returned non-JSON envelope: {exc}") from exc

        inner = wrapper.get("response", "")
        if isinstance(inner, dict):
            return inner
        return _extract_first_json_object(str(inner))


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------


class AnthropicProvider:
    """Hosted Claude inference via the ``anthropic`` SDK.

    Imports the SDK lazily so the rest of the module stays importable
    without ``anthropic`` installed.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        api_key: str | None = None,
        max_tokens: int = 2048,
    ) -> None:
        self.model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.max_tokens = max_tokens

    def complete_json(
        self,
        prompt: str,
        schema_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._api_key:
            raise RuntimeError(
                "AnthropicProvider: ANTHROPIC_API_KEY is not set"
            )
        try:
            import anthropic  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "AnthropicProvider requires the 'anthropic' package"
            ) from exc

        client = anthropic.Anthropic(api_key=self._api_key)
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        # Messages API returns a list of content blocks; first text block wins.
        text = ""
        for block in message.content:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", "")
                break
        return _extract_first_json_object(text)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_provider(
    name: str,
    model: str,
    **kwargs: Any,
) -> LLMProvider:
    """Build a provider by string name (``fake``, ``ollama``, ``anthropic``)."""
    normalized = name.strip().lower()
    if normalized == "fake":
        return FakeProvider(
            responses=kwargs.get("responses", [{}]),
            model=model,
        )
    if normalized == "ollama":
        return OllamaProvider(
            model=model,
            endpoint=kwargs.get("endpoint"),
            timeout=kwargs.get("timeout", 120.0),
        )
    if normalized == "anthropic":
        return AnthropicProvider(
            model=model,
            api_key=kwargs.get("api_key"),
            max_tokens=kwargs.get("max_tokens", 2048),
        )
    raise ValueError(f"Unknown LLM provider: {name!r}")
