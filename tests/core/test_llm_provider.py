"""Tests for ``core.llm_provider`` — provider detection + claude-cli adapter.

The Anthropic API adapter is exercised by its existing integration tests
indirectly; here we focus on:

* detection priority (claude-cli > codex-cli > anthropic-api > None)
* OT_LLM_PROVIDER override (including "none" sentinel and unknown values)
* ClaudeCLIProvider behavior with mocked subprocess: success, fence-stripped
  JSON, malformed envelope, model error, missing binary
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from opentraces.core import llm_provider


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ─── _strip_code_fences ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("```json\n{\"a\": 1}\n```", "{\"a\": 1}"),
        ("```JSON\n{\"a\": 1}\n```", "{\"a\": 1}"),
        ("```\n{\"a\": 1}\n```", "{\"a\": 1}"),
        ("  ```json\n{\"a\": 1}\n```  ", "{\"a\": 1}"),
        ("{\"a\": 1}", "{\"a\": 1}"),
        ("plain text no fences", "plain text no fences"),
    ],
)
def test_strip_code_fences(raw: str, expected: str):
    assert llm_provider._strip_code_fences(raw) == expected


# ─── detect_provider ──────────────────────────────────────────────────────


def test_detect_provider_picks_claude_cli_when_available(monkeypatch):
    monkeypatch.setattr(
        llm_provider.ClaudeCLIProvider, "is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        llm_provider.AnthropicAPIProvider, "is_available",
        lambda self: True,
    )
    detected = llm_provider.detect_provider()
    assert isinstance(detected, llm_provider.ClaudeCLIProvider)


def test_detect_provider_falls_back_to_anthropic_when_claude_missing(monkeypatch):
    monkeypatch.setattr(
        llm_provider.ClaudeCLIProvider, "is_available",
        lambda self: False,
    )
    monkeypatch.setattr(
        llm_provider.CodexCLIProvider, "is_available",
        lambda self: False,
    )
    monkeypatch.setattr(
        llm_provider.AnthropicAPIProvider, "is_available",
        lambda self: True,
    )
    detected = llm_provider.detect_provider()
    assert isinstance(detected, llm_provider.AnthropicAPIProvider)


def test_detect_provider_returns_none_when_nothing_available(monkeypatch):
    monkeypatch.setattr(
        llm_provider.ClaudeCLIProvider, "is_available",
        lambda self: False,
    )
    monkeypatch.setattr(
        llm_provider.CodexCLIProvider, "is_available",
        lambda self: False,
    )
    monkeypatch.setattr(
        llm_provider.AnthropicAPIProvider, "is_available",
        lambda self: False,
    )
    assert llm_provider.detect_provider() is None


def test_detect_provider_respects_override_argument(monkeypatch):
    monkeypatch.setattr(
        llm_provider.AnthropicAPIProvider, "is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        llm_provider.ClaudeCLIProvider, "is_available",
        lambda self: True,
    )
    detected = llm_provider.detect_provider(override="anthropic-api")
    assert isinstance(detected, llm_provider.AnthropicAPIProvider)


def test_detect_provider_respects_env_override(monkeypatch):
    monkeypatch.setenv("OT_LLM_PROVIDER", "anthropic-api")
    monkeypatch.setattr(
        llm_provider.AnthropicAPIProvider, "is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        llm_provider.ClaudeCLIProvider, "is_available",
        lambda self: True,
    )
    detected = llm_provider.detect_provider()
    assert isinstance(detected, llm_provider.AnthropicAPIProvider)


def test_detect_provider_none_sentinel_returns_none(monkeypatch):
    monkeypatch.setattr(
        llm_provider.ClaudeCLIProvider, "is_available", lambda self: True,
    )
    assert llm_provider.detect_provider(override="none") is None


def test_detect_provider_unknown_override_returns_none():
    assert llm_provider.detect_provider(override="not-a-provider") is None


def test_detect_provider_overridden_provider_must_be_available(monkeypatch):
    """If override names a provider that isn't available, return None."""

    monkeypatch.setattr(
        llm_provider.AnthropicAPIProvider, "is_available",
        lambda self: False,
    )
    assert llm_provider.detect_provider(override="anthropic-api") is None


# ─── CodexCLIProvider — reserved, never available ──────────────────────────


def test_codex_cli_provider_is_never_available_in_v1():
    """Even with `codex` on PATH, the v1 codex provider reports unavailable."""

    provider = llm_provider.CodexCLIProvider()
    assert provider.is_available() is False


def test_codex_cli_provider_call_raises_not_implemented():
    provider = llm_provider.CodexCLIProvider()
    with pytest.raises(NotImplementedError):
        provider.call_structured(
            system_prompt="x", user_message="y", json_schema={},
        )


# ─── ClaudeCLIProvider ─────────────────────────────────────────────────────


def _envelope(result: str, *, error: bool = False, subtype: str = "success") -> str:
    return json.dumps({
        "type": "result",
        "subtype": subtype,
        "is_error": error,
        "duration_ms": 1234,
        "result": result,
        "session_id": "test",
        "total_cost_usd": 0,
        "usage": {"input_tokens": 50, "output_tokens": 30},
    })


def test_claude_cli_call_structured_happy_path():
    provider = llm_provider.ClaudeCLIProvider()
    payload = {"hello": "world"}
    with (
        patch.object(llm_provider.shutil, "which", return_value="/usr/bin/claude"),
        patch.object(
            llm_provider.subprocess, "run",
            return_value=_Completed(stdout=_envelope(json.dumps(payload))),
        ),
    ):
        result = provider.call_structured(
            system_prompt="be precise", user_message="say hi",
            json_schema={"type": "object"},
        )
    assert result.payload == payload
    assert result.provider_name == "claude-cli"
    assert result.input_tokens == 50
    assert result.output_tokens == 30
    assert "Claude Code" in result.notes


def test_claude_cli_strips_code_fences_around_result():
    """Common case: model wraps JSON in triple-backtick fences."""

    provider = llm_provider.ClaudeCLIProvider()
    fenced_json = "```json\n{\"a\": 1}\n```"
    with (
        patch.object(llm_provider.shutil, "which", return_value="/usr/bin/claude"),
        patch.object(
            llm_provider.subprocess, "run",
            return_value=_Completed(stdout=_envelope(fenced_json)),
        ),
    ):
        result = provider.call_structured(
            system_prompt="x", user_message="y", json_schema={"type": "object"},
        )
    assert result.payload == {"a": 1}


def test_claude_cli_raises_when_binary_missing():
    provider = llm_provider.ClaudeCLIProvider()
    with patch.object(llm_provider.shutil, "which", return_value=None):
        with pytest.raises(llm_provider.ProviderError, match="not found on PATH"):
            provider.call_structured(
                system_prompt="x", user_message="y", json_schema={},
            )


def test_claude_cli_raises_on_nonzero_exit():
    provider = llm_provider.ClaudeCLIProvider()
    with (
        patch.object(llm_provider.shutil, "which", return_value="/usr/bin/claude"),
        patch.object(
            llm_provider.subprocess, "run",
            return_value=_Completed(returncode=1, stderr="auth failed"),
        ),
    ):
        with pytest.raises(llm_provider.ProviderError, match="exited 1"):
            provider.call_structured(
                system_prompt="x", user_message="y", json_schema={},
            )


def test_claude_cli_raises_on_malformed_envelope():
    provider = llm_provider.ClaudeCLIProvider()
    with (
        patch.object(llm_provider.shutil, "which", return_value="/usr/bin/claude"),
        patch.object(
            llm_provider.subprocess, "run",
            return_value=_Completed(stdout="not json at all"),
        ),
    ):
        with pytest.raises(llm_provider.ProviderError, match="not JSON"):
            provider.call_structured(
                system_prompt="x", user_message="y", json_schema={},
            )


def test_claude_cli_raises_on_envelope_error_field():
    provider = llm_provider.ClaudeCLIProvider()
    with (
        patch.object(llm_provider.shutil, "which", return_value="/usr/bin/claude"),
        patch.object(
            llm_provider.subprocess, "run",
            return_value=_Completed(stdout=_envelope("", error=True, subtype="failure")),
        ),
    ):
        with pytest.raises(llm_provider.ProviderError, match="reported error"):
            provider.call_structured(
                system_prompt="x", user_message="y", json_schema={},
            )


def test_claude_cli_raises_on_invalid_inner_json():
    provider = llm_provider.ClaudeCLIProvider()
    with (
        patch.object(llm_provider.shutil, "which", return_value="/usr/bin/claude"),
        patch.object(
            llm_provider.subprocess, "run",
            return_value=_Completed(stdout=_envelope("not actually json")),
        ),
    ):
        with pytest.raises(llm_provider.ProviderError, match="not valid JSON"):
            provider.call_structured(
                system_prompt="x", user_message="y", json_schema={},
            )


def test_claude_cli_argv_includes_model_and_no_tools():
    """The subprocess argv should disable tools and pin the model."""

    provider = llm_provider.ClaudeCLIProvider()
    captured: dict[str, Any] = {}

    def _spy(args: list[str], **kwargs: Any) -> _Completed:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _Completed(stdout=_envelope("{\"x\": 1}"))

    with (
        patch.object(llm_provider.shutil, "which", return_value="/usr/bin/claude"),
        patch.object(llm_provider.subprocess, "run", side_effect=_spy),
    ):
        provider.call_structured(
            system_prompt="rules", user_message="prompt body",
            json_schema={"type": "object"},
            model="claude-sonnet-4-6",
        )
    args = captured["args"]
    assert args[0:2] == ["claude", "-p"]
    assert "--model" in args and "claude-sonnet-4-6" in args
    assert "--allowed-tools" in args
    assert "--output-format" in args
    fmt_idx = args.index("--output-format")
    assert args[fmt_idx + 1] == "json"
    # The user message goes via stdin, not positional.
    assert captured["kwargs"]["input"] == "prompt body"
