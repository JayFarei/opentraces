"""F4 (#302): bare secret-shaped reason tokens must be sanitized.

`diagnostics.py` handled assignments, bearer tokens, and host paths, but a bare
credential token in an assertion reason (`assert resp == "sk-live-…"`) is not an
assignment and reached `result.json` + the rendered outcome reason. These
controls reuse the known secret shapes from ``security/tools`` and a conservative
high-entropy single-token catch; benign diagnostic prose must survive untouched.
"""

from __future__ import annotations

import pytest

from opentraces.core.arena.diagnostics import (
    sanitize_diagnostic_text,
    sanitize_reason,
)


# Every secret is assembled at runtime from a (prefix, body) pair so the source
# file never carries a contiguous credential literal (which would trip GitHub
# push-protection / secret scanning). The reassembled value still matches the
# corresponding ``security/secrets`` shape.
@pytest.mark.parametrize(
    ("prefix", "body"),
    [
        ("sk-live-", "abcdefghij0123456789ABCD"),  # OpenAI generic (hyphenated env)
        ("sk-proj-", "abcdefghij0123456789ABCD"),  # OpenAI project
        ("sk-ant-", "abcdefghij0123456789ABCD"),  # Anthropic
        ("ghp_", "ABCDEFGHIJ0123456789abcdWXYZ"),  # GitHub PAT
        ("xoxb-", "123456789012-abcdefghijklmnop"),  # Slack bot token
        ("AKIA", "ROTATEDKEY123456"),  # AWS access key id (AKIA[0-9A-Z]{16})
    ],
)
def test_bare_secret_tokens_are_redacted_from_reason(prefix: str, body: str) -> None:
    secret = prefix + body
    reason = sanitize_reason(
        "assertion_failed",
        f"assert resp == {secret}; tokenization completed",
    )
    assert secret not in reason["message"]
    assert "[redacted]" in reason["message"]
    # Benign diagnostic prose is never erased.
    assert "tokenization completed" in reason["message"]


def test_benign_reason_prose_survives_untouched() -> None:
    benign = "tokenization completed; secret scanner healthy; credential policy loaded"
    assert sanitize_diagnostic_text(benign) == benign


def test_short_and_low_entropy_tokens_are_not_over_redacted() -> None:
    # A git short sha and a normal word are not secret-shaped and must survive.
    text = "commit a1b2c3d completed the run in region us-east-1 successfully"
    sanitized = sanitize_diagnostic_text(text)
    assert "a1b2c3d" in sanitized
    assert "us-east-1" in sanitized
    assert "successfully" in sanitized


def test_bare_secret_inside_structured_json_reason_is_redacted() -> None:
    secret = "sk-live-" + "abcdefghij0123456789ABCD"
    payload = f'{{"detail":"observed {secret} in response"}}'
    sanitized = sanitize_diagnostic_text(payload)
    assert secret not in sanitized
    assert "[redacted]" in sanitized
