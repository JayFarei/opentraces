"""Shared security-tool configuration helpers.

Security is configured as explicit per-tool enable flags.  This module is the
non-presentation owner used by capture, datasets, and CLI adapters alike.
"""

from __future__ import annotations

from typing import Any, Iterable

from .tools._registry import describe_all, get as get_tool, iter_tools


SECURITY_TOOL_NAMES: tuple[str, ...] = tuple(tool.name for tool in iter_tools())


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
    """Flip ``cfg.security.<tool>.enabled`` after registry validation."""

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


def enabled_security_tool_names(cfg: Any) -> list[str]:
    """Return enabled registry tools in canonical execution order."""

    return [info.name for info in describe_all(cfg) if info.enabled]
