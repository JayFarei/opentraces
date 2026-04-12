"""Part F1 tests: LLMProvider protocol + Fake/Ollama/Anthropic providers.

Nothing in the default pipeline calls an LLM. Providers are an optional
interface. Tests exercise the protocol and each provider's contract.
Ollama and Anthropic live tests skip when the prerequisites are absent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from opentraces.security.llm_provider import (
    FakeProvider,
    LLMProvider,
    OllamaProvider,
    build_provider,
)


class TestFakeProvider:
    def test_returns_preset_response(self) -> None:
        p = FakeProvider(responses=[{"shareable": "yes"}])
        result = p.complete_json("ignored", schema_hint={"shareable": "str"})
        assert result == {"shareable": "yes"}

    def test_cycles_through_responses(self) -> None:
        p = FakeProvider(responses=[{"n": 1}, {"n": 2}])
        assert p.complete_json("a")["n"] == 1
        assert p.complete_json("b")["n"] == 2

    def test_raises_when_exhausted(self) -> None:
        p = FakeProvider(responses=[{"n": 1}])
        p.complete_json("a")
        with pytest.raises(RuntimeError):
            p.complete_json("b")

    def test_records_prompts(self) -> None:
        p = FakeProvider(responses=[{"ok": True}, {"ok": True}])
        p.complete_json("first prompt")
        p.complete_json("second prompt")
        assert p.prompts_seen == ["first prompt", "second prompt"]

    def test_implements_protocol(self) -> None:
        p = FakeProvider(responses=[{}])
        assert isinstance(p, LLMProvider)


class TestBuildProvider:
    def test_build_fake(self) -> None:
        p = build_provider("fake", model="whatever")
        assert isinstance(p, FakeProvider)

    def test_build_ollama(self) -> None:
        p = build_provider("ollama", model="gemma4:e4b")
        assert isinstance(p, OllamaProvider)
        assert p.model == "gemma4:e4b"

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError):
            build_provider("not-a-provider", model="x")


# ---------------------------------------------------------------------------
# Live Ollama smoke test — gated on ollama binary + model presence.
# ---------------------------------------------------------------------------


def _ollama_has_model(model: str) -> bool:
    if shutil.which("ollama") is None:
        return False
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return model.split(":")[0] in result.stdout


@pytest.mark.skipif(
    not _ollama_has_model("gemma4"),
    reason="Ollama or gemma4 model not available for live smoke test",
)
def test_ollama_live_returns_json_dict() -> None:
    """E2E: real Ollama provider returns a JSON-parseable dict."""
    p = OllamaProvider(model="gemma4:e4b")
    result = p.complete_json(
        'Respond ONLY with this exact JSON and nothing else: {"ok": "yes"}',
        schema_hint={"ok": "str"},
    )
    assert isinstance(result, dict)


@pytest.mark.skipif(
    os.environ.get("ANTHROPIC_API_KEY") is None,
    reason="ANTHROPIC_API_KEY not set; skipping live Anthropic test",
)
def test_anthropic_import_does_not_error() -> None:
    """Smoke: Anthropic provider builds without the SDK exploding."""
    from opentraces.security.llm_provider import AnthropicProvider

    p = AnthropicProvider(model="claude-haiku-4-5-20251001")
    assert p.model == "claude-haiku-4-5-20251001"
