from __future__ import annotations

import json
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "packages" / "opentraces-pi"


def test_pi_package_manifest_declares_gallery_resources() -> None:
    payload = json.loads((PACKAGE / "package.json").read_text())

    assert payload["name"] == "opentraces-pi"
    assert "pi-package" in payload["keywords"]
    assert payload["repository"]["directory"] == "packages/opentraces-pi"
    assert payload["pi"]["extensions"] == ["./src/index.ts"]
    assert payload["pi"]["skills"] == ["./skills"]
    assert payload["pi"]["prompts"] == ["./prompts"]
    assert "postinstall" not in payload.get("scripts", {})


def test_pi_extension_registers_high_level_tools_and_commands() -> None:
    index = (PACKAGE / "src" / "index.ts").read_text()

    for tool in ["ot_search", "ot_trace", "ot_standup", "ot_capsule", "ot_dataset", "ot_capture_status"]:
        assert f'name: "{tool}"' in index
    for command in ["ot-search", "ot-trace", "ot-standup", "ot-capsule", "ot-dataset", "ot-capture-status", "ot-setup"]:
        assert f'registerCommand("{command}"' in index
    assert "OPENTRACES_PI_EXTENSION_INTERNAL" in (PACKAGE / "src" / "tools" / "opentraces.ts").read_text()
    assert "trace\", \"query\", \"--lex\", \"recent work" in index
    assert '"capsule", "preview"' in index
    assert '"capsule", "status"' not in index
    assert '"trace", "standup"' not in index
    assert "raw provider bodies" in (PACKAGE / "README.md").read_text().lower()


def test_pi_extension_resources_exist() -> None:
    assert (PACKAGE / "skills" / "opentraces" / "SKILL.md").exists()
    for prompt in ["ot-search.md", "ot-standup.md", "ot-capsule.md", "ot-dataset.md"]:
        assert (PACKAGE / "prompts" / prompt).exists()
