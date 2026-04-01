"""Regex-based secret detection.

Pattern library vendored from DataClaw (MIT license), extended with
provider-specific prefixes and heuristic detectors (entropy, Luhn).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

Severity = Literal["critical", "high", "medium"]


@dataclass
class SecretMatch:
    """A single secret detection match."""

    pattern_name: str
    matched_text: str
    start: int
    end: int
    severity: Severity


# ---------------------------------------------------------------------------
# Allowlist -- patterns that look like secrets but are benign
# ---------------------------------------------------------------------------

_ALLOWLIST_EMAILS = re.compile(
    r"noreply@|no-reply@|@example\.com|@example\.org|@test\.com|@localhost"
)

_ALLOWLIST_DECORATORS = re.compile(
    r"@(?:property|staticmethod|classmethod|abstractmethod|override|dataclass|"
    r"cached_property|contextmanager|pytest\.mark|app\.route|router\.|click\.)"
)

_PRIVATE_IP_V4 = re.compile(
    r"(?:^|(?<=\s))(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|127\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|0\.0\.0\.0)(?:\s|$|[:/])"
)

_ALLOWLIST_URLS = re.compile(
    r"example\.com|example\.org|localhost|127\.0\.0\.1|0\.0\.0\.0"
)

_ALLOWLIST_DUMMY_TOKENS = re.compile(
    r"sk-(?:test|dummy|fake|example|xxx|your|placeholder)|Bearer\s+(?:\$|<|{|\[|test|dummy|fake|example|xxx|your)"
)


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

_PATTERNS: list[dict] = [
    # JWT
    {
        "name": "jwt_token",
        "pattern": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        "description": "JSON Web Token",
        "severity": "critical",
    },
    # Anthropic
    {
        "name": "anthropic_api_key",
        "pattern": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
        "description": "Anthropic API key",
        "severity": "critical",
    },
    # OpenAI project key
    {
        "name": "openai_project_key",
        "pattern": re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),
        "description": "OpenAI project API key",
        "severity": "critical",
    },
    # OpenAI generic key
    {
        "name": "openai_api_key",
        "pattern": re.compile(r"sk-[A-Za-z0-9]{20,}"),
        "description": "OpenAI API key (generic sk- prefix)",
        "severity": "critical",
    },
    # HuggingFace
    {
        "name": "huggingface_token",
        "pattern": re.compile(r"hf_[A-Za-z0-9]{20,}"),
        "description": "HuggingFace token",
        "severity": "critical",
    },
    # GitHub tokens
    {
        "name": "github_token",
        "pattern": re.compile(r"(?:ghp_|gho_|ghs_|ghu_)[A-Za-z0-9]{20,}"),
        "description": "GitHub personal/OAuth/app/user token",
        "severity": "critical",
    },
    {
        "name": "github_pat",
        "pattern": re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
        "description": "GitHub fine-grained PAT",
        "severity": "critical",
    },
    # PyPI
    {
        "name": "pypi_token",
        "pattern": re.compile(r"pypi-[A-Za-z0-9_-]{20,}"),
        "description": "PyPI API token",
        "severity": "critical",
    },
    # NPM
    {
        "name": "npm_token",
        "pattern": re.compile(r"npm_[A-Za-z0-9]{20,}"),
        "description": "NPM access token",
        "severity": "critical",
    },
    # AWS access key
    {
        "name": "aws_access_key",
        "pattern": re.compile(r"AKIA[0-9A-Z]{16}"),
        "description": "AWS access key ID",
        "severity": "critical",
    },
    # Slack tokens
    {
        "name": "slack_token",
        "pattern": re.compile(r"xox[bpse]-[A-Za-z0-9-]{10,}"),
        "description": "Slack bot/user/enterprise token",
        "severity": "critical",
    },
    # Discord webhook
    {
        "name": "discord_webhook",
        "pattern": re.compile(
            r"https://(?:discord|discordapp)\.com/api/webhooks/\d+/[A-Za-z0-9_-]+"
        ),
        "description": "Discord webhook URL",
        "severity": "high",
    },
    # Private keys
    {
        "name": "private_key",
        "pattern": re.compile(r"-----BEGIN\s+(?:RSA|DSA|EC|OPENSSH|PGP)?\s*PRIVATE\s+KEY-----"),
        "description": "Private key header",
        "severity": "critical",
    },
    # Database URLs
    {
        "name": "database_url",
        "pattern": re.compile(
            r"(?:postgresql|postgres|mysql|mongodb|redis)://[^\s\"'`<>]{8,}"
        ),
        "description": "Database connection string",
        "severity": "high",
    },
    # Bearer tokens
    {
        "name": "bearer_token",
        "pattern": re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}"),
        "description": "Bearer authentication token",
        "severity": "high",
    },
    # IPv4 address
    {
        "name": "ipv4_address",
        "pattern": re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
        ),
        "description": "IPv4 address",
        "severity": "medium",
    },
    # IPv6 address (simplified, catches common formats)
    {
        "name": "ipv6_address",
        "pattern": re.compile(
            r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
            r"|\b(?:[0-9a-fA-F]{1,4}:){1,7}:\b"
            r"|\b::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}\b"
        ),
        "description": "IPv6 address",
        "severity": "medium",
    },
    # Email address
    {
        "name": "email_address",
        "pattern": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        "description": "Email address",
        "severity": "medium",
    },
    # Credit card numbers (13-19 digits, optionally separated by spaces/dashes)
    {
        "name": "credit_card",
        "pattern": re.compile(
            r"\b(?:\d[ -]?){13,19}\b"
        ),
        "description": "Possible credit card number",
        "severity": "critical",
    },
    # SSN
    {
        "name": "ssn",
        "pattern": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "description": "US Social Security Number",
        "severity": "critical",
    },
    # Phone numbers
    {
        "name": "phone_number",
        "pattern": re.compile(
            r"(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"
        ),
        "description": "Phone number (US formats)",
        "severity": "medium",
    },
]


# ---------------------------------------------------------------------------
# Entropy detection
# ---------------------------------------------------------------------------

def shannon_entropy(text: str) -> float:
    """Calculate Shannon entropy of a string."""
    if not text:
        return 0.0
    freq: dict[str, int] = {}
    for ch in text:
        freq[ch] = freq.get(ch, 0) + 1
    length = len(text)
    return -sum((c / length) * math.log2(c / length) for c in freq.values())


_HIGH_ENTROPY_RE = re.compile(r"[A-Za-z0-9+/=_-]{20,}")

DEFAULT_ENTROPY_THRESHOLD = 4.5


def _find_high_entropy(text: str, threshold: float = DEFAULT_ENTROPY_THRESHOLD) -> list[SecretMatch]:
    """Find high-entropy strings that may be secrets."""
    matches: list[SecretMatch] = []
    for m in _HIGH_ENTROPY_RE.finditer(text):
        candidate = m.group()
        if shannon_entropy(candidate) >= threshold:
            # Skip if it looks like a common word or path segment
            if candidate.isalpha() and candidate.islower():
                continue
            matches.append(
                SecretMatch(
                    pattern_name="high_entropy_string",
                    matched_text=candidate,
                    start=m.start(),
                    end=m.end(),
                    severity="medium",
                )
            )
    return matches


# ---------------------------------------------------------------------------
# Luhn validation for credit cards
# ---------------------------------------------------------------------------

def _luhn_check(number_str: str) -> bool:
    """Validate a number string using the Luhn algorithm."""
    digits = [int(d) for d in number_str if d.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    reverse = digits[::-1]
    for i, d in enumerate(reverse):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ---------------------------------------------------------------------------
# Allowlist filtering
# ---------------------------------------------------------------------------

def _is_allowlisted(pattern_name: str, matched_text: str, full_text: str, start: int) -> bool:
    """Check if a match is a known false positive."""
    if pattern_name == "email_address":
        return bool(_ALLOWLIST_EMAILS.search(matched_text))

    if pattern_name == "ipv4_address":
        # Check surrounding context for private IPs
        ctx_start = max(0, start - 1)
        ctx_end = min(len(full_text), start + len(matched_text) + 1)
        ctx = full_text[ctx_start:ctx_end]
        return bool(_PRIVATE_IP_V4.search(ctx))

    if pattern_name in ("bearer_token", "openai_api_key", "anthropic_api_key"):
        return bool(_ALLOWLIST_DUMMY_TOKENS.search(matched_text))

    if pattern_name == "database_url":
        return bool(_ALLOWLIST_URLS.search(matched_text))

    if pattern_name == "credit_card":
        digits_only = re.sub(r"[ -]", "", matched_text)
        return not _luhn_check(digits_only)

    if pattern_name == "ssn":
        # Filter out obviously fake SSNs
        digits = matched_text.replace("-", "")
        if digits[:3] in ("000", "666") or digits[:3] >= "900":
            return True
        if digits[3:5] == "00" or digits[5:] == "0000":
            return True
        return False

    if pattern_name == "phone_number":
        # Filter out numbers that are actually part of other patterns (timestamps, etc.)
        digits = re.sub(r"\D", "", matched_text)
        if len(digits) < 10:
            return True
        return False

    if pattern_name == "high_entropy_string" and "/" in matched_text:
        # Filesystem paths score artificially high because mixing uppercase, lowercase,
        # digits, slashes, and underscores across multiple path segments inflates entropy.
        # Check whether any individual path component is high-entropy on its own.
        # If none are, the match is driven by path structure, not a secret.
        threshold = DEFAULT_ENTROPY_THRESHOLD
        components = [c for c in matched_text.split("/") if len(c) >= 8]
        if not any(shannon_entropy(c) >= threshold for c in components):
            return True

    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_text(
    text: str,
    *,
    include_entropy: bool = True,
    entropy_threshold: float = DEFAULT_ENTROPY_THRESHOLD,
) -> list[SecretMatch]:
    """Scan text for secrets using regex patterns and optional entropy analysis.

    Args:
        text: The text to scan.
        include_entropy: Whether to include high-entropy string detection.
        entropy_threshold: Shannon entropy threshold for flagging strings.

    Returns:
        List of SecretMatch objects for all detected secrets.
    """
    if not text:
        return []

    # Check if the text starts with a decorator (quick escape for Python code)
    stripped = text.strip()
    if _ALLOWLIST_DECORATORS.match(stripped):
        return []

    matches: list[SecretMatch] = []
    seen_spans: set[tuple[int, int]] = set()

    for pat in _PATTERNS:
        for m in pat["pattern"].finditer(text):
            matched_text = m.group()
            start, end = m.start(), m.end()

            if _is_allowlisted(pat["name"], matched_text, text, start):
                continue

            span = (start, end)
            if span not in seen_spans:
                seen_spans.add(span)
                matches.append(
                    SecretMatch(
                        pattern_name=pat["name"],
                        matched_text=matched_text,
                        start=start,
                        end=end,
                        severity=pat["severity"],
                    )
                )

    if include_entropy:
        for em in _find_high_entropy(text, threshold=entropy_threshold):
            if _is_allowlisted("high_entropy_string", em.matched_text, text, em.start):
                continue
            span = (em.start, em.end)
            # Avoid duplicates with regex matches
            overlaps = any(
                not (span[1] <= s[0] or span[0] >= s[1]) for s in seen_spans
            )
            if not overlaps:
                seen_spans.add(span)
                matches.append(em)

    matches.sort(key=lambda m: m.start)
    return matches


def redact_text(text: str, matches: list[SecretMatch]) -> str:
    """Replace matched secrets with [REDACTED] placeholders.

    Args:
        text: The original text.
        matches: List of SecretMatch objects to redact.

    Returns:
        Text with secrets replaced by [REDACTED].
    """
    if not matches:
        return text

    # Sort by start position descending so we can replace from the end
    sorted_matches = sorted(matches, key=lambda m: m.start, reverse=True)
    result = text
    for match in sorted_matches:
        result = result[:match.start] + "[REDACTED]" + result[match.end:]
    return result
