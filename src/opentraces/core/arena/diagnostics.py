"""Shared sanitization for lifecycle reasons and diagnostic artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any


_SENSITIVE_KEY_PARTS = (
    "apikey",
    "authorization",
    "credential",
    "password",
    "privatekey",
    "secret",
    "sshkey",
    "token",
)
_HOST_PATH_RE = re.compile(r"(?:(?:/Users|/home|/private|/tmp)/[^\s\"']+)")
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|authorization|credential|password|secret|token)\b\s*[:=]\s*)"
    r"(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s,;}\]]+)"
)
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)[^\s,;}\]]+")


def sanitize_diagnostic_text(text: str) -> str:
    """Remove credential values and host paths while keeping diagnostic shape."""

    if not text:
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        sanitized = _SENSITIVE_ASSIGNMENT_RE.sub(r"\1[redacted]", text)
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
            if any(part in compact for part in _SENSITIVE_KEY_PARTS) or compact in {
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
