"""Live end-to-end test of Tier 2 LLM review against real Ollama.

Skipped automatically unless the Ollama binary and a gemma model are
both present. Proves the full pipeline — transcript serializer, chunk,
provider call, JSON parse, verdict — against an actual LLM.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from opentraces.security.llm_provider import OllamaProvider
from opentraces.security.llm_review import review_trace


def _ollama_has_model(base: str) -> bool:
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
    return base in result.stdout


@pytest.mark.skipif(
    not _ollama_has_model("gemma4"),
    reason="Ollama or gemma4 model not available",
)
def test_live_review_clean_session_returns_verdict() -> None:
    provider = OllamaProvider(model="gemma4:e4b", timeout=300.0)
    verdict = review_trace(
        steps=[
            "user: please list files in the current directory",
            "assistant: here are the files: README.md, src/, tests/",
        ],
        provider=provider,
    )
    # Live model: we can't assert specific content, but the shape is fixed.
    assert verdict.shareable in {"yes", "no", "manual_review"}
    assert verdict.missed_sensitive_data in {"yes", "no", "maybe"}
    assert isinstance(verdict.flagged_parts, list)
    assert isinstance(verdict.summary, str)


@pytest.mark.skipif(
    not _ollama_has_model("gemma4"),
    reason="Ollama or gemma4 model not available",
)
def test_live_review_leaked_secret_flagged() -> None:
    provider = OllamaProvider(model="gemma4:e4b", timeout=300.0)
    verdict = review_trace(
        steps=[
            "user: run the deploy script",
            "assistant: setting AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/"
            "K7MDENG/bPxRfiCYEXAMPLEKEY in the config",
        ],
        provider=provider,
    )
    # A reasonable model should at least not return shareable=yes confidently;
    # we tolerate manual_review so a local small model isn't penalized for
    # being conservative.
    assert verdict.shareable in {"no", "manual_review"} or \
        verdict.missed_sensitive_data in {"yes", "maybe"}
