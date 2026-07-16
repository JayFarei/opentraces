from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from opentraces.core.arena.capability_probe import (
    CapabilityProbeError,
    evaluate_capabilities,
    parse_capabilities_probe,
)
from opentraces.core.arena.box import BoxCommandResult
from opentraces.core.arena.engine import Bench
from opentraces.core.arena.run_store import RunStore
from tests.core.arena.test_engine import FakeBoxRuntime, _scenario


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "opentraces.capabilities.v0",
        "app": {
            "name": "opentraces",
            "version": "test",
            "trace_schema_version": "test",
            "security_version": "test",
        },
        "interfaces": [
            {
                "id": "cli",
                "kind": "cli",
                "drive": "cli",
                "maturity": "stable",
                "entrypoint": "opentraces",
            },
            {
                "id": "agent",
                "kind": "agent",
                "drive": "agent",
                "maturity": "seed-plays",
                "composite_over": ["cli"],
                "harnesses": ["claude-code"],
                "skill": "skill/SKILL.md",
                "lifecycle": {"start": "setup.claude-code"},
            },
        ],
        "cli": {
            "entrypoint": "opentraces",
            "json_flag": "--json",
            "pure_json_under_flag": True,
            "verbs": [
                {"path": "dataset.publish", "hidden": False},
                {"path": "setup.claude-code", "hidden": False},
            ],
        },
        "integration_seams": [
            {
                "id": "claude-code-hooks",
                "kind": "agent-hook",
                "direction": "inbound",
                "installed_by": "setup.claude-code",
            }
        ],
        "emulation_seams": [
            {
                "dependency": "huggingface",
                "kind": "redirect",
                "env": ["HF_ENDPOINT"],
                "auth_env": ["HF_TOKEN", "HUGGINGFACE_TOKEN"],
                "honored_by": "huggingface_hub constants.ENDPOINT",
                "declared_in": "opentraces.core.capabilities:EMULATION_SEAMS",
            },
            {
                "dependency": "pypi-version-check",
                "kind": "disable",
                "env": ["OPENTRACES_DISABLE_VERSION_CHECK"],
                "auth_env": [],
                "honored_by": "opentraces.core.integration_versions:version_status",
                "declared_in": "opentraces.core.capabilities:EMULATION_SEAMS",
            },
            {
                "dependency": "llm-openai-compat",
                "kind": "config",
                "env": [],
                "auth_env": ["ANTHROPIC_API_KEY"],
                "honored_by": "opentraces.security.llm_provider:OpenAICompatProvider",
                "declared_in": "opentraces.core.capabilities:EMULATION_SEAMS",
                "config_key": "review_llm.base_url",
            },
        ],
        "introspection": {"command": "opentraces introspect", "provides": []},
    }


def test_bad_probe_or_schema_is_a_named_machinery_error() -> None:
    with pytest.raises(CapabilityProbeError, match="exit 9") as nonzero:
        parse_capabilities_probe(returncode=9, stdout="{}", stderr="broken")
    assert nonzero.value.code == "capability_probe_failed"

    with pytest.raises(CapabilityProbeError, match="schema_version") as wrong:
        parse_capabilities_probe(
            returncode=0,
            stdout='{"schema_version":"opentraces.capabilities.v9"}',
            stderr="",
        )
    assert wrong.value.code == "capability_probe_schema"

    with pytest.raises(CapabilityProbeError, match="interfaces") as malformed:
        parse_capabilities_probe(
            returncode=0,
            stdout=(
                '{"schema_version":"opentraces.capabilities.v0",'
                '"interfaces":"not-an-array","cli":{},"emulation_seams":[]}'
            ),
            stderr="",
        )
    assert malformed.value.code == "capability_probe_invalid"


def test_malformed_integration_seam_is_machinery_error_not_capability_skip() -> None:
    manifest = _manifest()
    manifest["integration_seams"] = [{}]

    with pytest.raises(CapabilityProbeError, match="integration seam id") as malformed:
        evaluate_capabilities(
            manifest,
            requirements=["mcp"],
            runner_drives={"cli", "agent"},
            runner_harnesses={"claude-code"},
            runner_emulators={"huggingface"},
            seam_values={},
        )

    assert malformed.value.code == "capability_probe_invalid"


@pytest.mark.parametrize(
    ("path", "invalid", "message"),
    [
        (("interfaces", 0, "kind"), "wormhole", "interface kind"),
        (("integration_seams", 0, "kind"), "wormhole", "integration seam kind"),
        (("integration_seams", 0, "direction"), "sideways", "integration seam direction"),
        (
            ("interfaces", 1, "lifecycle", "start"),
            "setup.missing",
            "lifecycle.*CLI verb",
        ),
        (
            ("integration_seams", 0, "installed_by"),
            "setup.missing",
            "installed_by.*CLI verb",
        ),
    ],
)
def test_closed_enums_and_cli_cross_references_fail_the_manifest_probe(
    path: tuple[str | int, ...], invalid: str, message: str
) -> None:
    manifest: Any = _manifest()
    target = manifest
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = invalid

    with pytest.raises(CapabilityProbeError, match=message) as error:
        parse_capabilities_probe(
            returncode=0,
            stdout=json.dumps(manifest),
            stderr="",
        )

    assert error.value.code == "capability_probe_invalid"


@pytest.mark.parametrize(
    ("path", "malformed", "message"),
    [
        (("interfaces", 0, "entrypoint"), 7, "interface entrypoint"),
        (("interfaces", 1, "composite_over"), "cli", "interface composite_over"),
        (("interfaces", 1, "skill"), 7, "interface skill"),
        (("interfaces", 1, "lifecycle"), [], "interface lifecycle"),
        (("cli", "entrypoint"), 7, "cli.entrypoint"),
        (("cli", "json_flag"), 7, "cli.json_flag"),
        (("cli", "pure_json_under_flag"), "yes", "cli.pure_json_under_flag"),
        (("emulation_seams", 0, "honored_by"), 7, "emulation seam honored_by"),
        (("emulation_seams", 0, "declared_in"), 7, "emulation seam declared_in"),
        (("emulation_seams", 2, "config_key"), 7, "emulation seam config_key"),
        (("introspection", "command"), "", "introspection command"),
    ],
)
def test_every_emitted_capability_field_rejects_malformed_shapes(
    path: tuple[str | int, ...], malformed: object, message: str
) -> None:
    manifest: Any = _manifest()
    target = manifest
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = malformed

    with pytest.raises(CapabilityProbeError, match=message) as error:
        parse_capabilities_probe(
            returncode=0,
            stdout=json.dumps(manifest),
            stderr="",
        )

    assert error.value.code == "capability_probe_invalid"


def test_absent_valid_capability_is_a_named_skip_not_pass_or_error() -> None:
    outcome = evaluate_capabilities(
        _manifest(),
        requirements=["mcp"],
        runner_drives={"cli", "agent"},
        runner_harnesses={"claude-code"},
        runner_emulators={"huggingface"},
        seam_values={},
    )

    assert outcome.status == "skip"
    assert outcome.reason == {
        "code": "capability_unsatisfied",
        "message": "manifest: no interface kind=mcp",
        "capability": "mcp",
    }
    assert outcome.environment == {}


def test_probe_checks_both_installed_surface_and_runner_drive() -> None:
    manifest_missing = evaluate_capabilities(
        _manifest(),
        requirements=["cli:dataset.run"],
        runner_drives={"cli", "agent"},
        runner_harnesses={"claude-code"},
        runner_emulators=set(),
        seam_values={},
    )
    runner_missing = evaluate_capabilities(
        _manifest(),
        requirements=["agent:claude-code"],
        runner_drives={"cli"},
        runner_harnesses=set(),
        runner_emulators=set(),
        seam_values={},
    )

    assert manifest_missing.reason["message"] == "manifest: no CLI verb dataset.run"
    assert runner_missing.reason["message"] == "runner: no drive for agent"


def test_satisfied_emulator_exports_only_declared_product_vars_and_disables() -> None:
    outcome = evaluate_capabilities(
        _manifest(),
        requirements=["cli:dataset.publish", "emulator:huggingface"],
        runner_drives={"cli"},
        runner_harnesses=set(),
        runner_emulators={"huggingface"},
        seam_values={
            "HF_ENDPOINT": "http://127.0.0.1:14318",
            "HF_TOKEN": "hf_product",
            "OPENTRACES_HF_CONTROL_TOKEN": "must-not-escape",
        },
    )

    assert outcome.status == "satisfied"
    assert outcome.reason is None
    assert outcome.environment == {
        "HF_ENDPOINT": "http://127.0.0.1:14318",
        "HF_TOKEN": "hf_product",
        "OPENTRACES_DISABLE_VERSION_CHECK": "1",
    }
    assert "OPENTRACES_HF_CONTROL_TOKEN" not in outcome.environment


def test_emulator_requires_one_declared_product_auth_value() -> None:
    outcome = evaluate_capabilities(
        _manifest(),
        requirements=["emulator:huggingface"],
        runner_drives={"cli"},
        runner_harnesses=set(),
        runner_emulators={"huggingface"},
        seam_values={"HF_ENDPOINT": "http://127.0.0.1:14318"},
    )

    assert outcome.status == "skip"
    assert outcome.reason == {
        "code": "capability_unsatisfied",
        "message": (
            "runner: no value for any declared product-auth seam "
            "HF_TOKEN, HUGGINGFACE_TOKEN"
        ),
        "capability": "emulator:huggingface",
    }
    assert outcome.environment == {}


def test_runner_harness_is_checked_separately_from_installed_agent_support() -> None:
    outcome = evaluate_capabilities(
        _manifest(),
        requirements=["agent:claude-code"],
        runner_drives={"agent"},
        runner_harnesses=set(),
        runner_emulators=set(),
        seam_values={},
    )

    assert outcome.status == "skip"
    assert outcome.reason["message"] == "runner: no harness for claude-code"


class CapabilityRuntime(FakeBoxRuntime):
    def __init__(self, *, returncode: int = 0, manifest: dict | None = None) -> None:
        super().__init__()
        self.returncode = returncode
        self.manifest = manifest or _manifest()
        self.probe_count = 0
        self.product_envs: list[dict[str, str]] = []

    def exec_product(self, box, argv, *, cwd=None, env=None, timeout=60, timing_path):
        if list(argv) == ["opentraces", "capabilities", "--json"]:
            self.probe_count += 1
            return BoxCommandResult(
                argv=list(argv),
                returncode=self.returncode,
                stdout=json.dumps(self.manifest),
                stderr="probe failed" if self.returncode else "",
                timing={},
            )
        self.product_envs.append(dict(env or {}))
        return super().exec_product(
            box,
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            timing_path=timing_path,
        )


def test_run_capability_absence_is_a_stored_named_skip_before_actions(tmp_path: Path) -> None:
    runtime = CapabilityRuntime()
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "runs" / "v1"),
        box_runtime=runtime,
        repository_path=tmp_path,
    )

    with bench.run(app_state="install-only") as run:
        run.require_capabilities("mcp")
        pytest.fail("an unsatisfied capability must stop before scenario actions")

    assert runtime.probe_count == 1
    assert not list((run.final_path / "actions").iterdir())
    result = json.loads((run.final_path / "result.json").read_text())
    assert result["execution_status"] == "complete"
    assert result["verdict"] == "skip"
    assert result["reason"]["code"] == "capability_unsatisfied"
    assert result["pins"]["capabilities"]["digest"].startswith("sha256:")


def test_broken_capability_probe_is_a_stored_run_error(tmp_path: Path) -> None:
    runtime = CapabilityRuntime(returncode=9)
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "runs" / "v1"),
        box_runtime=runtime,
        repository_path=tmp_path,
    )

    with pytest.raises(CapabilityProbeError, match="exit 9"):
        with bench.run(app_state="install-only") as run:
            run.require_capabilities("cli")

    result = json.loads((run.final_path / "result.json").read_text())
    assert result["execution_status"] == "error"
    assert result["verdict"] is None
    assert result["reason"]["code"] == "capability_probe_failed"


def test_malformed_capability_envelope_is_a_stored_run_error(tmp_path: Path) -> None:
    runtime = CapabilityRuntime(
        manifest={
            "schema_version": "opentraces.capabilities.v0",
            "interfaces": "not-an-array",
            "cli": {},
            "emulation_seams": [],
        }
    )
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "runs" / "v1"),
        box_runtime=runtime,
        repository_path=tmp_path,
    )

    with pytest.raises(CapabilityProbeError, match="interfaces"):
        with bench.run(app_state="install-only") as run:
            run.require_capabilities("cli")

    result = json.loads((run.final_path / "result.json").read_text())
    assert result["execution_status"] == "error"
    assert result["verdict"] is None
    assert result["reason"]["code"] == "capability_probe_invalid"


def test_declared_seam_environment_is_exported_to_later_product_actions(
    tmp_path: Path,
) -> None:
    runtime = CapabilityRuntime()
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "runs" / "v1"),
        box_runtime=runtime,
        repository_path=tmp_path,
    )

    with bench.run(app_state="install-only") as run:
        run.require_capabilities("cli:dataset.publish")
        run.terminal.exec("opentraces", "dataset", "list")
        run.verify(lambda _run: {"evidence_refs": []})

    assert runtime.product_envs[-1] == {"OPENTRACES_DISABLE_VERSION_CHECK": "1"}
