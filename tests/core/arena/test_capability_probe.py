from __future__ import annotations

import pytest

from opentraces.core.arena.capability_probe import (
    CapabilityProbeError,
    evaluate_capabilities,
    parse_capabilities_probe,
)


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "opentraces.capabilities.v0",
        "interfaces": [
            {"id": "cli", "kind": "cli", "drive": "cli"},
            {
                "id": "agent",
                "kind": "agent",
                "drive": "agent",
                "harnesses": ["claude-code"],
            },
        ],
        "cli": {"verbs": [{"path": "dataset.publish", "hidden": False}]},
        "emulation_seams": [
            {
                "dependency": "huggingface",
                "kind": "redirect",
                "env": ["HF_ENDPOINT"],
                "auth_env": ["HF_TOKEN", "HUGGINGFACE_TOKEN"],
            },
            {
                "dependency": "pypi-version-check",
                "kind": "disable",
                "env": ["OPENTRACES_DISABLE_VERSION_CHECK"],
                "auth_env": [],
            },
        ],
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


def test_absent_valid_capability_is_a_named_skip_not_pass_or_error() -> None:
    outcome = evaluate_capabilities(
        _manifest(),
        requirements=["mcp"],
        runner_drives={"cli", "agent"},
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
        runner_emulators=set(),
        seam_values={},
    )
    runner_missing = evaluate_capabilities(
        _manifest(),
        requirements=["agent:claude-code"],
        runner_drives={"cli"},
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
