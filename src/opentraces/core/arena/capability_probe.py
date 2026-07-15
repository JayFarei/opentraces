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


def _invalid(message: str) -> CapabilityProbeError:
    return CapabilityProbeError("capability_probe_invalid", message)


def _string_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise _invalid(f"capabilities {field} must be an array of strings")
    return value


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid(f"capabilities {field} must be a string")
    return value


def _string_mapping(value: object, *, field: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) or not key or not isinstance(item, str) or not item
        for key, item in value.items()
    ):
        raise _invalid(f"capabilities {field} must be an object of strings")
    return value


def _validate_manifest(payload: Mapping[str, Any]) -> None:
    interfaces = payload.get("interfaces")
    if not isinstance(interfaces, list) or any(not isinstance(row, Mapping) for row in interfaces):
        raise _invalid("capabilities interfaces must be an array of objects")
    interface_ids: set[str] = set()
    interface_kinds: set[str] = set()
    for row in interfaces:
        for field in ("id", "kind", "drive", "maturity"):
            _string(row.get(field), field=f"interface {field}")
        interface_id = str(row["id"])
        kind = str(row["kind"])
        if interface_id in interface_ids:
            raise _invalid(f"capabilities interfaces has duplicate id {interface_id}")
        if kind in interface_kinds:
            raise _invalid(f"capabilities interfaces has duplicate kind {kind}")
        interface_ids.add(interface_id)
        interface_kinds.add(kind)
        if "entrypoint" in row:
            _string(row["entrypoint"], field="interface entrypoint")
        if "composite_over" in row:
            _string_list(row["composite_over"], field="interface composite_over")
        if "harnesses" in row:
            _string_list(row["harnesses"], field="interface harnesses")
        if "skill" in row:
            _string(row["skill"], field="interface skill")
        if "lifecycle" in row:
            _string_mapping(row["lifecycle"], field="interface lifecycle")

    cli = payload.get("cli")
    if not isinstance(cli, Mapping):
        raise _invalid("capabilities cli must be an object")
    _string(cli.get("entrypoint"), field="cli.entrypoint")
    _string(cli.get("json_flag"), field="cli.json_flag")
    if not isinstance(cli.get("pure_json_under_flag"), bool):
        raise _invalid("capabilities cli.pure_json_under_flag must be a boolean")
    verbs = cli.get("verbs")
    if not isinstance(verbs, list) or any(not isinstance(row, Mapping) for row in verbs):
        raise _invalid("capabilities cli.verbs must be an array of objects")
    verb_paths: set[str] = set()
    for row in verbs:
        path = row.get("path")
        if not isinstance(path, str) or not path or not isinstance(row.get("hidden"), bool):
            raise _invalid("capabilities cli verb requires path and hidden")
        if path in verb_paths:
            raise _invalid(f"capabilities cli.verbs has duplicate path {path}")
        verb_paths.add(path)

    seams = payload.get("emulation_seams")
    if not isinstance(seams, list) or any(not isinstance(row, Mapping) for row in seams):
        raise _invalid("capabilities emulation_seams must be an array of objects")
    dependencies: set[str] = set()
    for row in seams:
        dependency = row.get("dependency")
        kind = row.get("kind")
        if not isinstance(dependency, str) or not dependency:
            raise _invalid("capabilities emulation seam dependency must be a string")
        if kind not in {"redirect", "disable", "config"}:
            raise _invalid("capabilities emulation seam kind is invalid")
        if dependency in dependencies:
            raise _invalid(f"capabilities emulation_seams has duplicate dependency {dependency}")
        dependencies.add(dependency)
        _string_list(row.get("env"), field="emulation seam env")
        _string_list(row.get("auth_env"), field="emulation seam auth_env")
        _string(row.get("honored_by"), field="emulation seam honored_by")
        _string(row.get("declared_in"), field="emulation seam declared_in")
        if "config_key" in row:
            _string(row["config_key"], field="emulation seam config_key")

    app = payload.get("app")
    if not isinstance(app, Mapping) or any(
        not isinstance(app.get(field), str) or not app.get(field)
        for field in ("name", "version", "trace_schema_version", "security_version")
    ):
        raise _invalid("capabilities app must contain its four version strings")
    integration_seams = payload.get("integration_seams")
    if not isinstance(integration_seams, list) or any(
        not isinstance(row, Mapping) for row in integration_seams
    ):
        raise _invalid("capabilities integration_seams must be an array of objects")
    integration_ids: set[str] = set()
    for row in integration_seams:
        for field in ("id", "kind", "direction", "installed_by"):
            if not isinstance(row.get(field), str) or not row.get(field):
                raise _invalid(f"capabilities integration seam {field} must be a string")
        seam_id = str(row["id"])
        if seam_id in integration_ids:
            raise _invalid(f"capabilities integration_seams has duplicate id {seam_id}")
        integration_ids.add(seam_id)
    introspection = payload.get("introspection")
    if not isinstance(introspection, Mapping):
        raise _invalid("capabilities introspection must be an object with command")
    _string(introspection.get("command"), field="introspection command")
    _string_list(introspection.get("provides"), field="introspection provides")


def parse_capabilities_probe(*, returncode: int, stdout: str, stderr: str) -> Mapping[str, Any]:
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
    _validate_manifest(payload)
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
    _validate_manifest(manifest)
    interfaces = [row for row in manifest.get("interfaces") or [] if isinstance(row, Mapping)]
    interface_by_kind = {str(row.get("kind")): row for row in interfaces}
    cli = manifest.get("cli") if isinstance(manifest.get("cli"), Mapping) else {}
    cli_verbs = {
        str(row.get("path"))
        for row in cli.get("verbs") or []
        if isinstance(row, Mapping) and row.get("path")
    }
    seams = [row for row in manifest.get("emulation_seams") or [] if isinstance(row, Mapping)]
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
