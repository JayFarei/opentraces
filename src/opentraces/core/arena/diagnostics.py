"""Shared sanitization for lifecycle reasons and diagnostic artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any


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


def sanitize_diagnostic_text(text: str) -> str:
    """Remove credential values and host paths while keeping diagnostic shape."""

    if not text:
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        sanitized = _redact_assignments(text)
        sanitized = _BEARER_RE.sub(r"\1[redacted]", sanitized)
        return _HOST_PATH_RE.sub("[host-path]", sanitized)
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
