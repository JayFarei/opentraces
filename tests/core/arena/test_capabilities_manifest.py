from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core import integration_versions
from opentraces.security.llm_provider import OpenAICompatProvider


def _manifest() -> tuple[str, dict[str, object]]:
    result = CliRunner().invoke(main, ["capabilities", "--json"])

    assert result.exit_code == 0, result.output
    return result.output, json.loads(result.output)


def test_capabilities_is_the_frozen_code_derived_v0_envelope() -> None:
    first_bytes, first = _manifest()
    second_bytes, second = _manifest()

    assert first_bytes == second_bytes
    assert first == second
    assert set(first) == {
        "schema_version",
        "app",
        "interfaces",
        "cli",
        "integration_seams",
        "emulation_seams",
        "introspection",
    }
    assert first["schema_version"] == "opentraces.capabilities.v0"
    assert set(first["app"]) == {
        "name",
        "version",
        "trace_schema_version",
        "security_version",
    }


def test_capabilities_derives_hidden_and_visible_leaf_verbs_from_click() -> None:
    _, payload = _manifest()

    verbs = payload["cli"]["verbs"]
    assert verbs == sorted(verbs, key=lambda row: row["path"])
    by_path = {row["path"]: row for row in verbs}
    assert by_path["auth.whoami"] == {"path": "auth.whoami", "hidden": False}
    assert by_path["capabilities"] == {"path": "capabilities", "hidden": True}
    assert by_path["trace.get"] == {"path": "trace.get", "hidden": False}
    assert all("children" not in row for row in verbs)


def test_capabilities_declares_probeable_interfaces_and_seams() -> None:
    _, payload = _manifest()

    interfaces = {row["id"]: row for row in payload["interfaces"]}
    assert interfaces["cli"]["drive"] == "cli"
    assert interfaces["agent"]["harnesses"] == ["claude-code", "codex-cli", "pi"]
    assert interfaces["agent"]["composite_over"] == ["cli"]
    assert interfaces["otlp-ingest"]["lifecycle"] == {
        "start": "capture-otlp.start",
        "status": "capture-otlp.status",
        "stop": "capture-otlp.stop",
    }

    verb_paths = {row["path"] for row in payload["cli"]["verbs"]}
    for seam in payload["integration_seams"]:
        assert seam["installed_by"] in verb_paths
    for interface in payload["interfaces"]:
        for command in interface.get("lifecycle", {}).values():
            assert command in verb_paths

    emulation = {row["dependency"]: row for row in payload["emulation_seams"]}
    assert emulation["huggingface"]["kind"] == "redirect"
    assert emulation["huggingface"]["env"] == ["HF_ENDPOINT"]
    assert emulation["huggingface"]["auth_env"] == ["HF_TOKEN", "HUGGINGFACE_TOKEN"]
    assert emulation["pypi-version-check"]["kind"] == "disable"
    assert emulation["pypi-version-check"]["env"] == [
        "OPENTRACES_DISABLE_VERSION_CHECK"
    ]
    assert emulation["llm-openai-compat"]["kind"] == "config"
    assert emulation["llm-openai-compat"]["config_key"] == "review_llm.base_url"


def test_huggingface_redirect_seam_is_honored_before_process_start() -> None:
    endpoint = "http://127.0.0.1:43181"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from huggingface_hub import HfApi; print(HfApi().endpoint)",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "HF_ENDPOINT": endpoint},
    )

    assert completed.stdout.strip() == endpoint


def test_version_check_disable_seam_short_circuits_network(monkeypatch) -> None:
    monkeypatch.setenv("OPENTRACES_DISABLE_VERSION_CHECK", "1")
    monkeypatch.setattr(
        integration_versions,
        "_fetch_latest_pypi_version",
        lambda: (_ for _ in ()).throw(AssertionError("network must not run")),
    )

    assert integration_versions.version_status()["check_state"] == "disabled"


def test_openai_compat_config_seam_targets_injected_base(monkeypatch) -> None:
    observed: list[str] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'

    def open_url(request: urllib.request.Request, **_kwargs: object) -> Response:
        observed.append(request.full_url)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", open_url)
    provider = OpenAICompatProvider(
        model="test-model",
        base_url="http://127.0.0.1:43182/v1/",
    )

    assert provider.complete_json("probe") == {"ok": True}
    assert observed == ["http://127.0.0.1:43182/v1/chat/completions"]
