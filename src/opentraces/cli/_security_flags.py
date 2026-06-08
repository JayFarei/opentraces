"""Shared CLI helpers for security-tool enable flags."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable

from ..security.tools._registry import describe_all, get as get_tool, iter_tools


SECURITY_TOOL_NAMES: tuple[str, ...] = tuple(tool.name for tool in iter_tools())
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


def _dedupe(names: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        name = raw.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def set_security_tool_enabled(cfg: Any, name: str, enabled: bool) -> None:
    """Flip ``cfg.security.<tool>.enabled`` after validating the registry name."""

    try:
        get_tool(name)
    except KeyError as exc:
        raise ValueError(str(exc)) from None

    block = getattr(getattr(cfg, "security", None), name, None)
    if block is None or not hasattr(block, "enabled"):
        raise ValueError(f"security tool {name!r} has no config flag")
    setattr(block, "enabled", enabled)


def apply_security_tool_flag_changes(
    cfg: Any,
    *,
    enable: Iterable[str] = (),
    disable: Iterable[str] = (),
) -> dict[str, list[str]]:
    """Apply generic security-tool enable/disable flags to a loaded config."""

    enable_names = _dedupe(enable)
    disable_names = _dedupe(disable)
    overlap = sorted(set(enable_names) & set(disable_names))
    if overlap:
        joined = ", ".join(overlap)
        raise ValueError(f"security tool(s) both enabled and disabled: {joined}")

    for name in enable_names:
        set_security_tool_enabled(cfg, name, True)
    for name in disable_names:
        set_security_tool_enabled(cfg, name, False)
    return {"enabled": enable_names, "disabled": disable_names}


def set_security_tools_exact(cfg: Any, names: Iterable[str]) -> dict[str, list[str]]:
    """Set the enabled security tools to exactly ``names``."""

    enabled_names = _dedupe(names)
    unknown = sorted(set(enabled_names) - set(SECURITY_TOOL_NAMES))
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"unknown security tool(s): {joined}")

    enabled_set = set(enabled_names)
    before = set(enabled_security_tool_names(cfg))
    for name in SECURITY_TOOL_NAMES:
        set_security_tool_enabled(cfg, name, name in enabled_set)
    after = set(enabled_security_tool_names(cfg))
    return {
        "enabled": [name for name in SECURITY_TOOL_NAMES if name in after - before],
        "disabled": [name for name in SECURITY_TOOL_NAMES if name in before - after],
    }


def apply_bucket_security_policy(cfg: Any, policy: str) -> dict[str, list[str]]:
    """Apply one named bucket security policy to the shared tool flags."""

    try:
        tools = BUCKET_SECURITY_POLICIES[policy]
    except KeyError:
        choices = ", ".join(BUCKET_SECURITY_POLICIES)
        raise ValueError(f"unknown bucket security policy {policy!r}; choose one of: {choices}") from None
    return set_security_tools_exact(cfg, tools)


def enabled_security_tool_names(cfg: Any) -> list[str]:
    return [info.name for info in describe_all(cfg) if info.enabled]


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
