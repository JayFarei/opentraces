"""Two-sided capability checks for a materialized bench product."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..capabilities import CAPABILITIES_SCHEMA_VERSION


@dataclass(frozen=True)
class CapabilityOutcome:
    status: str
    reason: dict[str, str] | None
    environment: dict[str, str]


class CapabilityProbeError(RuntimeError):
    """The installed product could not state a valid capability contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_capabilities_probe(
    *, returncode: int, stdout: str, stderr: str
) -> Mapping[str, Any]:
    """Parse the public in-box probe; failures are machinery errors, not skips."""

    if returncode != 0:
        raise CapabilityProbeError(
            "capability_probe_failed",
            f"capabilities probe exited with exit {returncode}",
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CapabilityProbeError(
            "capability_probe_invalid", "capabilities probe did not emit JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise CapabilityProbeError(
            "capability_probe_invalid", "capabilities probe did not emit an object"
        )
    if payload.get("schema_version") != CAPABILITIES_SCHEMA_VERSION:
        raise CapabilityProbeError(
            "capability_probe_schema",
            "capabilities probe has an unsupported schema_version",
        )
    return payload


def _skip(capability: str, message: str) -> CapabilityOutcome:
    return CapabilityOutcome(
        status="skip",
        reason={
            "code": "capability_unsatisfied",
            "message": message,
            "capability": capability,
        },
        environment={},
    )


def evaluate_capabilities(
    manifest: Mapping[str, Any],
    *,
    requirements: Iterable[str],
    runner_drives: set[str],
    runner_harnesses: set[str],
    runner_emulators: set[str],
    seam_values: Mapping[str, str],
) -> CapabilityOutcome:
    """Evaluate installed-app and runner sides and return safe step environment."""

    if manifest.get("schema_version") != CAPABILITIES_SCHEMA_VERSION:
        raise CapabilityProbeError(
            "capability_probe_schema",
            "capabilities manifest has an unsupported schema_version",
        )
    interfaces = [row for row in manifest.get("interfaces") or [] if isinstance(row, Mapping)]
    interface_by_kind = {str(row.get("kind")): row for row in interfaces}
    cli = manifest.get("cli") if isinstance(manifest.get("cli"), Mapping) else {}
    cli_verbs = {
        str(row.get("path"))
        for row in cli.get("verbs") or []
        if isinstance(row, Mapping) and row.get("path")
    }
    seams = [
        row for row in manifest.get("emulation_seams") or [] if isinstance(row, Mapping)
    ]
    seam_by_dependency = {str(row.get("dependency")): row for row in seams}
    selected_redirects: list[Mapping[str, Any]] = []

    for requirement in requirements:
        if not isinstance(requirement, str) or not requirement:
            raise CapabilityProbeError(
                "capability_requirement_invalid", "capability requirement must be a string"
            )
        if ":" not in requirement:
            if requirement not in {"cli", "agent", "http", "mcp", "web", "desktop"}:
                raise CapabilityProbeError(
                    "capability_requirement_invalid",
                    f"unknown capability requirement {requirement}",
                )
            interface = interface_by_kind.get(requirement)
            if interface is None:
                return _skip(requirement, f"manifest: no interface kind={requirement}")
            drive = str(interface.get("drive") or "")
            if drive not in runner_drives:
                return _skip(requirement, f"runner: no drive for {drive}")
            continue

        family, value = requirement.split(":", 1)
        if not value or family not in {"cli", "agent", "emulator"}:
            raise CapabilityProbeError(
                "capability_requirement_invalid",
                f"unknown capability requirement {requirement}",
            )
        if family == "cli":
            interface = interface_by_kind.get("cli")
            if interface is None:
                return _skip(requirement, "manifest: no interface kind=cli")
            if value not in cli_verbs:
                return _skip(requirement, f"manifest: no CLI verb {value}")
            drive = str(interface.get("drive") or "")
            if drive not in runner_drives:
                return _skip(requirement, f"runner: no drive for {drive}")
            continue
        if family == "agent":
            interface = interface_by_kind.get("agent")
            if interface is None:
                return _skip(requirement, "manifest: no interface kind=agent")
            if value not in set(map(str, interface.get("harnesses") or [])):
                return _skip(requirement, f"manifest: no agent harness {value}")
            drive = str(interface.get("drive") or "")
            if drive not in runner_drives:
                return _skip(requirement, f"runner: no drive for {drive}")
            if value not in runner_harnesses:
                return _skip(requirement, f"runner: no harness for {value}")
            continue

        seam = seam_by_dependency.get(value)
        if seam is None or seam.get("kind") != "redirect":
            return _skip(requirement, f"manifest: no redirect seam for {value}")
        if value not in runner_emulators:
            return _skip(requirement, f"runner: no emulator package {value}")
        selected_redirects.append(seam)

    environment: dict[str, str] = {}
    for seam in selected_redirects:
        declared = [*list(seam.get("env") or []), *list(seam.get("auth_env") or [])]
        for name in map(str, declared):
            if name in seam_values:
                environment[name] = str(seam_values[name])
        redirect_vars = list(map(str, seam.get("env") or []))
        missing_redirects = [name for name in redirect_vars if name not in environment]
        if missing_redirects:
            dependency = str(seam.get("dependency"))
            return _skip(
                f"emulator:{dependency}",
                f"runner: no value for declared seam {missing_redirects[0]}",
            )
    for seam in seams:
        if seam.get("kind") != "disable":
            continue
        for name in map(str, seam.get("env") or []):
            environment[name] = "1"
    return CapabilityOutcome(status="satisfied", reason=None, environment=environment)
