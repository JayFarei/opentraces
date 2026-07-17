"""Deterministic installed-product capability contract.

The registries in this module are the one declaration site for interfaces and
external seams.  World-state probes belong to doctor; this manifest is a pure
function of installed code and the live CLI registry.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final, Iterable, Mapping


CAPABILITIES_SCHEMA_VERSION: Final = "opentraces.capabilities.v0"


@dataclass(frozen=True)
class Interface:
    id: str
    kind: str
    drive: str
    maturity: str
    entrypoint: str | None = None
    composite_over: tuple[str, ...] = ()
    harnesses: tuple[str, ...] = ()
    skill: str | None = None
    lifecycle: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, object]:
        row: dict[str, object] = {
            "id": self.id,
            "kind": self.kind,
            "drive": self.drive,
            "maturity": self.maturity,
        }
        if self.entrypoint is not None:
            row["entrypoint"] = self.entrypoint
        if self.composite_over:
            row["composite_over"] = list(self.composite_over)
        if self.harnesses:
            row["harnesses"] = list(self.harnesses)
        if self.skill is not None:
            row["skill"] = self.skill
        if self.lifecycle:
            row["lifecycle"] = dict(self.lifecycle)
        return row


@dataclass(frozen=True)
class IntegrationSeam:
    id: str
    kind: str
    direction: str
    installed_by: str


@dataclass(frozen=True)
class EmulationSeam:
    dependency: str
    kind: str
    env: tuple[str, ...]
    auth_env: tuple[str, ...]
    honored_by: str
    declared_in: str = "opentraces.core.capabilities:EMULATION_SEAMS"
    config_key: str | None = None

    def to_dict(self) -> dict[str, object]:
        row: dict[str, object] = {
            "dependency": self.dependency,
            "kind": self.kind,
            "env": list(self.env),
            "auth_env": list(self.auth_env),
            "honored_by": self.honored_by,
            "declared_in": self.declared_in,
        }
        if self.config_key is not None:
            row["config_key"] = self.config_key
        return row


INTERFACES: Final = (
    Interface(
        id="cli",
        kind="cli",
        drive="cli",
        maturity="stable",
        entrypoint="opentraces",
    ),
    Interface(
        id="agent",
        kind="agent",
        drive="agent",
        maturity="seed-plays",
        composite_over=("cli",),
        harnesses=("claude-code", "codex-cli", "pi"),
        skill="skill/SKILL.md",
    ),
    Interface(
        id="otlp-ingest",
        kind="http",
        drive="http",
        maturity="v0-minimal",
        entrypoint="http://127.0.0.1:4318",
        lifecycle=(
            ("start", "capture-otlp.start"),
            ("status", "capture-otlp.status"),
            ("stop", "capture-otlp.stop"),
        ),
    ),
)

INTEGRATION_SEAMS: Final = (
    IntegrationSeam("claude-code-hooks", "agent-hook", "inbound", "setup.claude-code"),
    IntegrationSeam("codex-cli-hooks", "agent-hook", "inbound", "setup.codex-cli"),
    IntegrationSeam("pi-bridge", "agent-hook", "inbound", "setup.pi"),
    IntegrationSeam("git-post-commit", "vcs-hook", "inbound", "setup.git"),
    IntegrationSeam("watcher", "daemon", "inbound", "setup.watcher.install"),
    IntegrationSeam(
        "otel-settings-patch", "settings-patch", "inbound", "setup.capture-otlp"
    ),
)

EMULATION_SEAMS: Final = (
    EmulationSeam(
        dependency="huggingface",
        kind="redirect",
        env=("HF_ENDPOINT",),
        auth_env=("HF_TOKEN", "HUGGINGFACE_TOKEN"),
        honored_by=(
            "huggingface_hub constants.ENDPOINT (read at import; set before process start)"
        ),
    ),
    EmulationSeam(
        dependency="pypi-version-check",
        kind="disable",
        env=("OPENTRACES_DISABLE_VERSION_CHECK",),
        auth_env=(),
        honored_by="opentraces.core.integration_versions:version_status",
    ),
    EmulationSeam(
        dependency="llm-openai-compat",
        kind="config",
        env=(),
        auth_env=("ANTHROPIC_API_KEY",),
        config_key="review_llm.base_url",
        honored_by="opentraces.security.llm_provider:OpenAICompatProvider",
    ),
)


def build_capabilities_manifest(
    *,
    verbs: Iterable[Mapping[str, object]],
    app_version: str,
    trace_schema_version: str,
    security_version: str,
) -> dict[str, object]:
    """Build and cross-check the frozen manifest from installed code facts."""

    verb_rows = sorted(
        ({"path": str(row["path"]), "hidden": bool(row["hidden"])} for row in verbs),
        key=lambda row: row["path"],
    )
    verb_paths = {str(row["path"]) for row in verb_rows}
    referenced = {seam.installed_by for seam in INTEGRATION_SEAMS}
    referenced.update(command for row in INTERFACES for _, command in row.lifecycle)
    missing = sorted(referenced - verb_paths)
    if missing:
        raise ValueError(f"capabilities registry references missing CLI verbs: {missing}")

    return {
        "schema_version": CAPABILITIES_SCHEMA_VERSION,
        "app": {
            "name": "opentraces",
            "version": app_version,
            "trace_schema_version": trace_schema_version,
            "security_version": security_version,
        },
        "interfaces": [row.to_dict() for row in INTERFACES],
        "cli": {
            "entrypoint": "opentraces",
            "json_flag": "--json",
            "pure_json_under_flag": True,
            "verbs": verb_rows,
        },
        "integration_seams": [asdict(row) for row in INTEGRATION_SEAMS],
        "emulation_seams": [row.to_dict() for row in EMULATION_SEAMS],
        "introspection": {
            "command": "opentraces introspect",
            "provides": ["options", "arguments", "exit_codes", "trace_record_schema"],
        },
    }
