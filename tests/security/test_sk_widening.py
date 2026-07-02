"""Table tests for the ``sk-`` hyphen blind-spot fix (#143 move 3).

The generic OpenAI pattern ``sk-[A-Za-z0-9]{20,}`` forbade a hyphen after
``sk-``, so ``sk-live-…`` / ``sk-<env>-…`` tokens walked the floor. Widened to
``sk-[A-Za-z0-9-]{20,}`` while preserving ``_ALLOWLIST_DUMMY_TOKENS`` so the
synthetic ``sk-test``/``sk-example`` placeholders are still excluded.
"""

from __future__ import annotations

import pytest

from opentraces.security.secrets import scan_text


@pytest.mark.parametrize(
    "token",
    [
        "sk-live-SENTINEL3aaaaaaaaaaaaaaaaaaaaaaaa",   # the S3 leak
        "sk-prod-abcdefghijklmnopqrstuvwxyz012345",   # sk-<env>- shape
        "sk-staging-0123456789abcdefghijABCDEF",       # another env shape
        "sk-abcdefghijklmnopqrstuvwxyz012345",          # bare sk- (no hyphen) still matches
    ],
)
def test_hyphenated_and_bare_sk_tokens_are_detected(token):
    text = f"here is a key {token} in prose"
    matches = scan_text(text, include_entropy=False)
    matched = {m.matched_text for m in matches}
    assert any(token in m or m in token for m in matched), f"{token} not detected: {matched}"


@pytest.mark.parametrize(
    "placeholder",
    [
        "sk-test-aaaaaaaaaaaaaaaaaaaaaaaa",
        "sk-example-aaaaaaaaaaaaaaaaaaaaaa",
        "sk-dummy-aaaaaaaaaaaaaaaaaaaaaaaa",
        "sk-placeholder-aaaaaaaaaaaaaaaaaa",
    ],
)
def test_dummy_placeholders_are_not_flagged_after_widening(placeholder):
    """Negative control: the allowlist exclusion still holds post-widening."""
    text = f"example config: {placeholder}"
    matches = scan_text(text, include_entropy=False)
    for m in matches:
        assert placeholder not in m.matched_text, f"placeholder wrongly flagged: {m.matched_text}"
