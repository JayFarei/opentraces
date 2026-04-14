"""LLMProvider protocol and reference implementations.

The LLM layer is an optional interface used by Tier 1.8 (per-field PII
detection) and Tier 2 LLM (session-level semantic review). Nothing in
the default pipeline calls an LLM; contributors have to configure a
provider and opt in explicitly.

Shipped providers:
  - :class:`FakeProvider` — deterministic for tests.
  - :class:`OpenAICompatProvider` — any OpenAI-compatible endpoint
    (Ollama at ``/v1``, LM Studio, vLLM, OpenAI, Groq, OpenRouter,
    Together, …). Preferred entry point for new setups.
  - :class:`OllamaProvider` — native Ollama ``/api/generate`` path,
    kept for backwards compatibility.
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
# OpenAICompatProvider — one provider covers most of the ecosystem
# ---------------------------------------------------------------------------


class OpenAICompatProvider:
    """Call any OpenAI-compatible ``/chat/completions`` endpoint.

    One shape handles Ollama (``http://localhost:11434/v1``), LM Studio,
    vLLM, OpenAI, Groq, OpenRouter, Together, Fireworks, Anyscale, etc.
    ``base_url`` must include the ``/v1`` suffix so the same string works
    across all of these.

    Authentication is opt-in: when ``api_key_env`` is set we read the env
    var and send ``Authorization: Bearer <token>``. Local servers that
    don't require auth just leave it empty.
    """

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434/v1",
        api_key_env: str = "",
        timeout: float = 120.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.extra_headers = dict(extra_headers or {})

    def _resolve_api_key(self) -> str | None:
        if not self.api_key_env:
            return None
        value = os.environ.get(self.api_key_env)
        if not value:
            raise RuntimeError(
                f"OpenAICompatProvider: env var {self.api_key_env!r} is not set"
            )
        return value

    def complete_json(
        self,
        prompt: str,
        schema_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import urllib.error
        import urllib.request

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", **self.extra_headers}
        api_key = self._resolve_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        url = f"{self.base_url}/chat/completions"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"OpenAI-compat request failed ({url}): {exc}") from exc

        try:
            envelope = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenAI-compat non-JSON envelope: {exc}") from exc

        choices = envelope.get("choices") or []
        if not choices:
            raise RuntimeError(f"OpenAI-compat: no choices in response: {envelope!r}")
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            # Some providers return a list of content blocks; concatenate text parts.
            content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        if not isinstance(content, str):
            raise RuntimeError(
                f"OpenAI-compat: unexpected content type {type(content)!r}"
            )
        return _extract_first_json_object(content)

    def ping(self) -> dict[str, Any]:
        """Lightweight reachability check used by ``opentraces doctor``.

        Hits ``{base_url}/models`` — supported by Ollama, OpenAI, LM
        Studio, vLLM, Groq, and most other OpenAI-compat servers. Returns
        ``{"ok": True, "models": [...]}`` on success; raises RuntimeError
        on failure (caller renders as unreachable).
        """
        import urllib.error
        import urllib.request

        headers = {"Accept": "application/json", **self.extra_headers}
        api_key = self._resolve_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        url = f"{self.base_url}/models"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=min(10.0, self.timeout)) as resp:
                body = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"ping {url} failed: {exc}") from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ping {url} non-JSON: {exc}") from exc
        models = payload.get("data") or payload.get("models") or []
        names = [m.get("id") or m.get("name") for m in models if isinstance(m, dict)]
        return {"ok": True, "models": [n for n in names if n]}


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
    """Build a provider implementation by api-format name.

    Known names: ``fake``, ``openai-compat``, ``ollama``, ``anthropic``.

    ``openai-compat`` is the preferred entry point for both hosted APIs
    (OpenAI, Groq, OpenRouter, Together) and local servers exposing an
    OpenAI-compatible endpoint (Ollama at ``/v1``, LM Studio, vLLM).
    ``ollama`` is kept for the native ``/api/generate`` path.
    """
    normalized = name.strip().lower()
    if normalized == "fake":
        return FakeProvider(
            responses=kwargs.get("responses", [{}]),
            model=model,
        )
    if normalized == "openai-compat":
        return OpenAICompatProvider(
            model=model,
            base_url=kwargs.get("base_url", "http://localhost:11434/v1"),
            api_key_env=kwargs.get("api_key_env", ""),
            timeout=kwargs.get("timeout", 120.0),
            extra_headers=kwargs.get("extra_headers"),
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
