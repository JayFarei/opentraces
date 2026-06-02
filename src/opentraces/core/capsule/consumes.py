"""Consumed dependencies — the first-class replay axis (plan 089).

A capsule's ``environment.consumes[]`` records what the client *consumes but does
not control*: a package pinned at a version, or a service at an endpoint. The
verdict is then ``oracle(story, consumed_dep@version)`` — hold the client source
constant, vary the consumed dep, watch reproduces→fixed.

Each entry is one of:

    {"kind": "package", "name": "humanduration",
     "pin": "git+https://github.com/o/r@v0.1.0"}        # or "name==0.1.0"

    {"kind": "service", "name": "convert-api", "env": "CONVERT_API_URL",
     "endpoint": "https://.../api/convert", "deploy": "dpl_v1"}

``capsule test --with name=VALUE`` / ``--matrix name=v1,v2`` override a consumed
dep at replay time. VALUE is either a full spec (kept verbatim) or a bare
ref/version token substituted into the entry's pin/endpoint.

This module is pure (no I/O); the venv/env wiring lives in ``run.py``.
"""

from __future__ import annotations

import re
from typing import Any

# A value is a "full spec" (used verbatim) if it already looks like a pip
# requirement, a VCS/URL spec, or an http(s) endpoint. Otherwise it's a bare
# token (a tag/version) substituted into the existing pin.
_FULL_SPEC = re.compile(r"(==|@|://|\bgit\+)")


class ConsumeError(ValueError):
    pass


def parse_with(items: list[str] | tuple[str, ...] | None) -> dict[str, str]:
    """``["name=value", ...]`` -> ``{name: value}``. Raises on malformed input."""

    out: dict[str, str] = {}
    for raw in items or ():
        if "=" not in raw:
            raise ConsumeError(f"--with expects name=value, got {raw!r}")
        name, value = raw.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name or not value:
            raise ConsumeError(f"--with expects a non-empty name and value, got {raw!r}")
        out[name] = value
    return out


def parse_matrix(spec: str | None) -> tuple[str, list[str]]:
    """``"name=v1,v2,v3"`` -> ``("name", ["v1","v2","v3"])``."""

    if not spec or "=" not in spec:
        raise ConsumeError(f"--matrix expects name=v1,v2,…, got {spec!r}")
    name, versions = spec.split("=", 1)
    tokens = [v.strip() for v in versions.split(",") if v.strip()]
    if not name.strip() or not tokens:
        raise ConsumeError(f"--matrix needs a name and at least one version, got {spec!r}")
    return name.strip(), tokens


def version_token(pin_or_endpoint: str) -> str:
    """Best-effort human label for what version a pin/endpoint points at."""

    if "@" in pin_or_endpoint:  # git+…@<ref>
        return pin_or_endpoint.rsplit("@", 1)[1]
    if "==" in pin_or_endpoint:  # name==<ver>
        return pin_or_endpoint.split("==", 1)[1]
    return pin_or_endpoint


def apply_override(base: str, value: str) -> str:
    """Substitute ``value`` into ``base``.

    Full spec (``==`` / ``@`` / ``://`` / ``git+``) -> used verbatim. Bare token
    -> replace the ref after ``@`` or the version after ``==`` in ``base``; if
    ``base`` has neither, append ``==value``.
    """

    if _FULL_SPEC.search(value):
        return value
    if "@" in base:
        return base.rsplit("@", 1)[0] + "@" + value
    if "==" in base:
        return base.split("==", 1)[0] + "==" + value
    return f"{base}=={value}"


def resolve_consumes(
    consumes: list[dict[str, Any]] | None,
    overrides: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """Apply overrides, returning resolved entries with a ``version`` label.

    Package entries gain ``spec`` (the pip install target) + ``version``; service
    entries gain ``endpoint`` + ``version`` (the deploy/url in effect).
    """

    overrides = overrides or {}
    resolved: list[dict[str, Any]] = []
    for entry in consumes or ():
        if not isinstance(entry, dict):
            raise ConsumeError(f"consumes entry must be an object, got {entry!r}")
        kind = entry.get("kind")
        name = entry.get("name")
        if not name:
            raise ConsumeError(f"consumes entry missing name: {entry!r}")
        ov = overrides.get(name)
        if kind == "package":
            base = entry.get("pin") or name
            spec = apply_override(base, ov) if ov else base
            resolved.append({"kind": "package", "name": name, "spec": spec,
                             "version": version_token(spec)})
        elif kind == "service":
            base = entry.get("endpoint") or ""
            endpoint = ov if (ov and _FULL_SPEC.search(ov)) else (ov or base)
            resolved.append({"kind": "service", "name": name,
                             "env": entry.get("env") or _default_env_name(name),
                             "endpoint": endpoint, "version": ov or entry.get("deploy") or endpoint})
        else:
            raise ConsumeError(f"unknown consumes kind {kind!r} for {name!r}")
    return resolved


def _default_env_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()) + "_URL"


def consumes_used(resolved: list[dict[str, Any]]) -> dict[str, str]:
    """``{name: version}`` label of what this run actually used (for resolved_in)."""

    return {e["name"]: e["version"] for e in resolved}


__all__ = [
    "ConsumeError", "parse_with", "parse_matrix", "version_token",
    "apply_override", "resolve_consumes", "consumes_used",
]
