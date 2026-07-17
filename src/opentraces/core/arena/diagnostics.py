"""Shared sanitization for lifecycle reasons and diagnostic artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from ...security.secrets import scan_text


# Reuse the known credential/token shapes from ``security/secrets`` rather than
# inventing a second divergent regex vocabulary (issue #302 F4). Restricted to
# credential-shaped patterns plus the conservative single-token high-entropy
# catch — PII/network shapes (email, ipv4/ipv6, phone, ssn, credit_card,
# database_url) are deliberately excluded so ordinary diagnostic prose (region
# ids, timestamps, hostnames) is never mangled inside a reason string.
_REASON_SECRET_PATTERNS = frozenset(
    {
        "jwt_token",
        "anthropic_api_key",
        "openai_project_key",
        "openai_api_key",
        "huggingface_token",
        "github_token",
        "github_pat",
        "pypi_token",
        "npm_token",
        "aws_access_key",
        "aws_sts_key",
        "groq_api_key",
        "xai_api_key",
        "google_ai_key",
        "cerebras_api_key",
        "openrouter_api_key",
        "vercel_token",
        "slack_token",
        "stripe_secret_key",
        "stripe_restricted_key",
        "private_key",
        "high_entropy_string",
    }
)


_SENSITIVE_KEY_SEGMENTS = {
    "apikey",
    "authorization",
    "credential",
    "password",
    "privatekey",
    "secret",
    "sshkey",
    "token",
}
_SENSITIVE_KEY_PAIRS = {("api", "key"), ("private", "key"), ("ssh", "key")}
_HOST_PATH_RE = re.compile(r"(?:(?:/Users|/home|/private|/tmp)/[^\s\"']+)")
_ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])"
    r"(?P<key>[A-Za-z][A-Za-z0-9_.-]*)(?P<separator>\s*[:=]\s*)"
    r"(?P<value>bearer\s+[^\s,;}\]]+|\"[^\"\n]*\"|'[^'\n]*'|[^\s,;}\]]+)"
)
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)(?!\[redacted\])[^\s,;}\]]+")


def _key_segments(key: str) -> list[str]:
    return [segment for segment in re.split(r"[_.-]+", key.lower()) if segment]


def _is_sensitive_key(key: str) -> bool:
    segments = _key_segments(key)
    if any(segment in _SENSITIVE_KEY_SEGMENTS for segment in segments):
        return True
    pairs = set(zip(segments, segments[1:]))
    return bool(pairs & _SENSITIVE_KEY_PAIRS)


def _redact_assignment(match: re.Match[str]) -> str:
    if not _is_sensitive_key(match.group("key")):
        return match.group(0)
    value = match.group("value")
    replacement = (
        f"{value.split(None, 1)[0]} [redacted]"
        if value.lower().startswith("bearer ")
        else "[redacted]"
    )
    return f'{match.group("key")}{match.group("separator")}{replacement}'


def _redact_assignments(text: str) -> str:
    """Redact sensitive assignments even when nested after an ordinary label."""

    cursor = 0
    while match := _ASSIGNMENT_RE.search(text, cursor):
        if not _is_sensitive_key(match.group("key")):
            cursor = match.start() + 1
            continue
        replacement = _redact_assignment(match)
        text = f"{text[: match.start()]}{replacement}{text[match.end() :]}"
        cursor = match.start() + len(replacement)
    return text


# Bounds on the high-entropy catch (#302 review repair A1): operators need
# digests and paths intact in a reason, so an entropy-only match (no known
# credential shape) is redacted ONLY outside digest/path contexts AND with a
# credential-like word on its line. Known credential shapes redact regardless.
_DIGEST_CONTEXT_RE = re.compile(r"(?i)(?:sha1|sha256|sha384|sha512|md5|blake2[bs]?|digest)\s*[:=]\s*$")
_HEX_TOKEN_RE = re.compile(r"^[0-9a-fA-F]+$")
_CREDENTIAL_CONTEXT_RE = re.compile(
    r"(?i)\b(?:token|secret|key|credential|password|passwd|auth|authorization|bearer|api)\b"
)


def _entropy_match_is_operator_evidence(text: str, start: int, end: int) -> bool:
    """True when a high-entropy match is a digest or path, never a credential."""

    token = text[start:end]
    if "/" in token:
        return True
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if before == "/" or after == "/":
        return True
    if _HEX_TOKEN_RE.match(token):
        return True
    return bool(_DIGEST_CONTEXT_RE.search(text[:start]))


def _containing_line(text: str, start: int, end: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    return text[line_start : line_end if line_end != -1 else len(text)]


def _reason_secret_spans(text: str) -> list[tuple[int, int]]:
    spans: set[tuple[int, int]] = set()

    def collect(segment: str, base: int) -> None:
        for match in scan_text(segment, include_entropy=True):
            if match.pattern_name not in _REASON_SECRET_PATTERNS:
                continue
            start, end = base + match.start, base + match.end
            if match.pattern_name == "high_entropy_string":
                if _entropy_match_is_operator_evidence(text, start, end):
                    continue
                if not _CREDENTIAL_CONTEXT_RE.search(_containing_line(text, start, end)):
                    continue
            spans.add((start, end))

    collect(text, 0)
    stripped = text.lstrip()
    if stripped.startswith("@"):
        # The shared scanner returns early when the whole text STARTS with a
        # recognized decorator, which would mask a bare credential later in the
        # string (#302 review repair A2). Do not change the shared scanner
        # (SECURITY_VERSION policy); rescan wrapper-side past the leading
        # decorator token so the rest of the text is still covered.
        lead = len(text) - len(stripped)
        whitespace = re.search(r"\s", stripped)
        if whitespace is not None:
            collect(text[lead + whitespace.start() :], lead + whitespace.start())

    # Merge overlaps so the double-scan can never mangle a replacement.
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _redact_secret_tokens(text: str) -> str:
    """Redact bare secret-shaped tokens that are not ``key=value`` assignments.

    Assignments and bearer tokens are handled upstream; this closes the gap
    where a lone credential in an assertion reason (``assert resp ==
    "sk-live-…"``) survives into ``result.json`` and the rendered outcome
    reason (issue #302 F4). Shapes and the high-entropy heuristic are reused
    verbatim from ``security/secrets`` — no new regex vocabulary — with the
    entropy catch bounded to spare digests and paths (review repair A1) and a
    wrapper-side rescan past a leading decorator (review repair A2).
    """

    spans = _reason_secret_spans(text)
    for start, end in reversed(spans):
        text = f"{text[:start]}[redacted]{text[end:]}"
    return text


def sanitize_diagnostic_text(text: str) -> str:
    """Remove credential values and host paths while keeping diagnostic shape."""

    if not text:
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        sanitized = _redact_assignments(text)
        sanitized = _BEARER_RE.sub(r"\1[redacted]", sanitized)
        sanitized = _HOST_PATH_RE.sub("[host-path]", sanitized)
        return _redact_secret_tokens(sanitized)
    return json.dumps(sanitize_diagnostic_value(payload), sort_keys=True)


def sanitize_diagnostic_value(value: Any) -> Any:
    """Recursively sanitize structured lifecycle material without flattening it."""

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            compact = key.lower().replace("_", "").replace("-", "")
            if _is_sensitive_key(key) or compact in {
                "cwd",
                "home",
                "hostpath",
            }:
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = sanitize_diagnostic_value(child)
        return sanitized
    if isinstance(value, list):
        return [sanitize_diagnostic_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_diagnostic_value(item) for item in value]
    if isinstance(value, str):
        return sanitize_diagnostic_text(value)
    return value


def sanitize_reason(code: object, message: object) -> dict[str, str]:
    """Build one safe named reason for the result contract."""

    return {
        "code": str(sanitize_diagnostic_text(str(code))),
        "message": sanitize_diagnostic_text(str(message)),
    }
