"""Shared CLI helpers for security-tool enable flags."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..security.config import (
    enabled_security_tool_names,
    set_security_tools_exact,
)
from ..security.tools._registry import describe_all


BUCKET_SECURITY_POLICIES: dict[str, tuple[str, ...]] = {
    "off": (),
    "basic": ("regex", "entropy"),
    "recommended": (
        "regex",
        "entropy",
        "business_logic",
        "path_anonymizer",
        "classifier",
    ),
    "strict": (
        "regex",
        "entropy",
        "trufflehog",
        "privacy_filter",
        "business_logic",
        "path_anonymizer",
        "classifier",
    ),
}
RECOMMENDED_BUCKET_SECURITY_TOOLS: tuple[str, ...] = (
    *BUCKET_SECURITY_POLICIES["recommended"],
)


def apply_bucket_security_policy(cfg: Any, policy: str) -> dict[str, list[str]]:
    """Apply one named bucket security policy to the shared tool flags."""

    try:
        tools = BUCKET_SECURITY_POLICIES[policy]
    except KeyError:
        choices = ", ".join(BUCKET_SECURITY_POLICIES)
        raise ValueError(f"unknown bucket security policy {policy!r}; choose one of: {choices}") from None
    return set_security_tools_exact(cfg, tools)


def active_bucket_security_policy(cfg: Any) -> str:
    enabled = tuple(enabled_security_tool_names(cfg))
    enabled_set = set(enabled)
    for name, tools in BUCKET_SECURITY_POLICIES.items():
        if enabled_set == set(tools):
            return name
    return "custom"


def security_tool_state_payload(cfg: Any, *, scope: str | None = None) -> dict[str, Any]:
    payload = {
        "enabled": enabled_security_tool_names(cfg),
        "tools": [asdict(info) for info in describe_all(cfg)],
    }
    if scope:
        payload["scope"] = scope
    if scope == "bucket":
        payload["policy"] = active_bucket_security_policy(cfg)
        payload["available_policies"] = {
            name: list(tools) for name, tools in BUCKET_SECURITY_POLICIES.items()
        }
    return {
        **payload,
    }


def security_tool_change_payload(
    cfg: Any,
    *,
    scope: str,
    changes: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    return {
        "status": "ok",
        "security": security_tool_state_payload(cfg, scope=scope),
        "changes": changes or {"enabled": [], "disabled": []},
    }
