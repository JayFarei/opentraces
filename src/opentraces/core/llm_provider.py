"""LLM provider abstraction for the polish step.

Three backends, in priority order:

1. **claude-cli** — wraps ``claude -p --output-format json`` (Claude Code
   headless). Uses the user's Claude Code OAuth (free with Pro/Max
   subscription) or falls through to ``ANTHROPIC_API_KEY`` if no OAuth is
   configured. Default when ``claude`` is on PATH.
2. **codex-cli** — placeholder for ``codex exec`` (ChatGPT Plus/Pro OAuth).
   Not implemented in v1; reserved so the protocol can grow without
   churn when Codex's headless+JSON path stabilizes.
3. **anthropic-api** — direct ``anthropic.Anthropic`` SDK call with
   tool-use for guaranteed schema-valid output. Requires
   ``ANTHROPIC_API_KEY`` and the optional ``[llm-api]`` install extra.

Detection picks the highest-priority *available* provider. Override via
``OT_LLM_PROVIDER`` env var (``claude-cli``, ``codex-cli``,
``anthropic-api``, or ``none``).

Why CLI providers are preferred
-------------------------------

Most users have ``claude`` (or will install it) and pay $0 for these
calls under their subscription. Forcing them to add an API key for what
amounts to a few hundred output tokens per render would be a worse UX
than just shelling out to a tool they already trust.

Schema enforcement on CLI paths
-------------------------------

The Anthropic SDK guarantees JSON-schema-valid output via tool-use. The
CLI paths can't — they're general-purpose chat surfaces. So on CLI paths
we embed the schema in the prompt, demand JSON-only output, parse the
response, and let ``llm_polish`` validate + retry. In practice Haiku
follows JSON-only instructions reliably with a strong system prompt.

Not the same as ``opentraces.security.llm_provider``
----------------------------------------------------

That module's ``LLMProvider`` is a ``complete_json(prompt)`` contract for
the security pipeline's Tier 1.8 / Tier 2 review. This one is a
``call_structured()`` + ``is_available()`` + ``name`` contract built for
*provider selection* across CLI-subprocess and API backends. Different
shape, different consumers — deliberately separate, not duplicated.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Protocol


DEFAULT_MODEL = "claude-haiku-4-5-20251001"
"""Model id used by every provider unless ``model_hint`` overrides it."""


# ---------------------------------------------------------------------------
# Provider protocol + result shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderResult:
    """Outcome of one ``call_structured`` invocation."""

    payload: dict[str, Any]
    provider_name: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    notes: str = ""  # human-readable provenance ("Claude Code session", etc.)


class ProviderError(Exception):
    """Raised by a provider when the call fails after its own retries."""


class LLMProvider(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def call_structured(
        self,
        *,
        system_prompt: str,
        user_message: str,
        json_schema: dict[str, Any],
        model: str = DEFAULT_MODEL,
    ) -> ProviderResult: ...


# ---------------------------------------------------------------------------
# claude-cli provider (preferred default)
# ---------------------------------------------------------------------------


class ClaudeCLIProvider:
    """Wraps ``claude -p --output-format json``.

    Uses the user's Claude Code OAuth if configured (free with Pro/Max);
    falls through to ``ANTHROPIC_API_KEY`` per Claude Code's normal auth
    chain. We never set ``--bare`` because that explicitly disables OAuth
    and forces the API key.
    """

    name = "claude-cli"

    def is_available(self) -> bool:
        return shutil.which("claude") is not None

    def call_structured(
        self,
        *,
        system_prompt: str,
        user_message: str,
        json_schema: dict[str, Any],
        model: str = DEFAULT_MODEL,
    ) -> ProviderResult:
        if not self.is_available():
            raise ProviderError("`claude` CLI not found on PATH")

        composed_system = (
            f"{system_prompt}\n\n"
            "OUTPUT CONSTRAINT (CRITICAL):\n"
            "Respond with ONLY a valid JSON object matching this schema. "
            "No prose, no commentary, no markdown code fences. The first "
            "character of your response must be `{` and the last must be `}`.\n\n"
            f"Schema:\n```json\n{json.dumps(json_schema, indent=2)}\n```"
        )
        completed = subprocess.run(
            [
                "claude", "-p",
                "--output-format", "json",
                "--model", model,
                "--allowed-tools", "",
                "--append-system-prompt", composed_system,
            ],
            input=user_message,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise ProviderError(
                f"claude -p exited {completed.returncode}: "
                f"{(completed.stderr or '').strip()[:300]}"
            )
        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"claude envelope was not JSON: {exc}") from exc

        if envelope.get("is_error") or envelope.get("subtype") != "success":
            raise ProviderError(
                f"claude reported error: {envelope.get('subtype')!r}"
            )
        result_text = str(envelope.get("result") or "").strip()
        result_text = _strip_code_fences(result_text)
        try:
            payload = json.loads(result_text)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"claude result was not valid JSON after fence-stripping: "
                f"{exc}; first 200 chars: {result_text[:200]!r}"
            ) from exc

        usage = envelope.get("usage") or {}
        # total_cost_usd in the envelope reports the *equivalent* API cost
        # even for OAuth subscribers, so we don't surface it as "you paid X".
        # Just note that we ran through the local Claude Code session.
        return ProviderResult(
            payload=payload,
            provider_name=self.name,
            model=model,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            notes="via Claude Code (OAuth subscription if configured, else API key)",
        )


# ---------------------------------------------------------------------------
# codex-cli provider — reserved, not yet implemented
# ---------------------------------------------------------------------------


class CodexCLIProvider:
    """Reserved for ``codex exec`` integration.

    Codex's headless+JSON output path is less stable than Claude's across
    versions; we ship the abstraction without the adapter and add it when
    a user asks or when Codex's CLI flags settle. Calling this provider
    raises ``NotImplementedError`` immediately so detection skips it.
    """

    name = "codex-cli"

    def is_available(self) -> bool:
        # Even if `codex` is on PATH we report unavailable until the
        # adapter is implemented — preserves the priority chain without
        # silently routing calls to a non-functional backend.
        return False

    def call_structured(
        self, **kwargs: Any,
    ) -> ProviderResult:
        raise NotImplementedError(
            "codex-cli provider is reserved for a follow-up; not active in v1"
        )


# ---------------------------------------------------------------------------
# anthropic-api provider (API key, optional install extra)
# ---------------------------------------------------------------------------


_TOOL_NAME = "submit_pr_summary"


class AnthropicAPIProvider:
    """Direct Anthropic SDK call with tool-use for schema enforcement.

    Requires the ``[llm-api]`` install extra (``pip install
    opentraces[llm-api]``) and ``ANTHROPIC_API_KEY``. Returns the
    tool_use block's input verbatim — no fence-stripping or
    re-parsing needed.
    """

    name = "anthropic-api"

    def is_available(self) -> bool:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return False
        try:
            import anthropic  # noqa: F401
            return True
        except ImportError:
            return False

    def call_structured(
        self,
        *,
        system_prompt: str,
        user_message: str,
        json_schema: dict[str, Any],
        model: str = DEFAULT_MODEL,
    ) -> ProviderResult:
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError(
                "anthropic SDK not installed; "
                "run `pip install opentraces[llm-api]`"
            ) from exc

        tool = {
            "name": _TOOL_NAME,
            "description": "Submit the structured response for the requested task.",
            "input_schema": json_schema,
        }
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=1500,
            system=system_prompt,
            tools=[tool],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": user_message}],
        )
        tool_block = next(
            (b for b in response.content if getattr(b, "type", None) == "tool_use"),
            None,
        )
        if tool_block is None:
            raise ProviderError("anthropic API returned no tool_use block")
        payload = getattr(tool_block, "input", None)
        if not isinstance(payload, dict):
            raise ProviderError("anthropic tool_use block input was not a dict")

        usage = getattr(response, "usage", None)
        return ProviderResult(
            payload=payload,
            provider_name=self.name,
            model=model,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            notes="via Anthropic API (charged to ANTHROPIC_API_KEY)",
        )


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


_PROVIDER_PRIORITY: list[type] = [
    ClaudeCLIProvider,
    CodexCLIProvider,
    AnthropicAPIProvider,
]

_PROVIDER_BY_NAME: dict[str, type] = {
    "claude-cli": ClaudeCLIProvider,
    "codex-cli": CodexCLIProvider,
    "anthropic-api": AnthropicAPIProvider,
}


def detect_provider(*, override: str | None = None) -> LLMProvider | None:
    """Pick the best available provider, or ``None`` if nothing's available.

    Resolution order:
      1. ``override`` argument (or ``OT_LLM_PROVIDER`` env var) if non-empty:
         use the named provider, or return ``None`` if it's unavailable
         or the value is ``"none"``.
      2. Otherwise iterate ``_PROVIDER_PRIORITY`` and return the first
         provider whose ``is_available()`` is True.
    """

    requested = override or os.environ.get("OT_LLM_PROVIDER")
    if requested:
        requested = requested.strip().lower()
        if requested in {"none", ""}:
            return None
        cls = _PROVIDER_BY_NAME.get(requested)
        if cls is None:
            return None
        instance = cls()
        return instance if instance.is_available() else None

    for cls in _PROVIDER_PRIORITY:
        instance = cls()
        if instance.is_available():
            return instance
    return None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


_FENCE_RE = re.compile(
    r"^```(?:json|JSON)?\s*\n?(.*?)\n?```\s*$",
    re.DOTALL,
)


def _strip_code_fences(text: str) -> str:
    """Strip a single wrapping ```json … ``` fence if present."""

    text = text.strip()
    match = _FENCE_RE.match(text)
    if match:
        return match.group(1).strip()
    return text


__all__ = [
    "AnthropicAPIProvider",
    "ClaudeCLIProvider",
    "CodexCLIProvider",
    "DEFAULT_MODEL",
    "LLMProvider",
    "ProviderError",
    "ProviderResult",
    "detect_provider",
]
