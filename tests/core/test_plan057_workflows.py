from __future__ import annotations

import json
import subprocess
import sys


def test_workflow_scaffold_creates_skill_package_and_stable_digest():
    from opentraces.core.workflows import (
        compute_workflow_digest,
        create_workflow,
        list_workflows,
        load_workflow,
        workflows_dir,
    )

    created = create_workflow(
        "grill-me-intent-curator",
        description="Build rows for the grill-me-intents dataset",
    )

    assert created.path == workflows_dir() / "grill-me-intent-curator"
    assert (created.path / "SKILL.md").exists()
    assert (created.path / "examples" / "input-candidate-packet.json").exists()
    assert (created.path / "examples" / "expected-row.json").exists()
    assert (created.path / "scripts").is_dir()
    assert (created.path / "tests").is_dir()

    loaded = load_workflow("grill-me-intent-curator")
    assert loaded.name == "grill-me-intent-curator"
    assert loaded.description == "Build rows for the grill-me-intents dataset"
    assert loaded.digest.startswith("sha256:")
    assert [item.name for item in list_workflows()] == ["grill-me-intent-curator"]

    first_digest = compute_workflow_digest(created.path)
    (created.path / "zz-extra.txt").write_text("extra\n")
    changed_digest = compute_workflow_digest(created.path)

    mirrored = workflows_dir() / "mirrored"
    (mirrored / "examples").mkdir(parents=True)
    (mirrored / "zz-extra.txt").write_text("extra\n")
    (mirrored / "SKILL.md").write_text((created.path / "SKILL.md").read_text())
    (mirrored / "tests").mkdir()
    (mirrored / "tests" / "README.md").write_text(
        (created.path / "tests" / "README.md").read_text()
    )
    (mirrored / "scripts").mkdir()
    (mirrored / "examples" / "expected-row.json").write_text(
        (created.path / "examples" / "expected-row.json").read_text()
    )
    (mirrored / "examples" / "input-candidate-packet.json").write_text(
        (created.path / "examples" / "input-candidate-packet.json").read_text()
    )
    mirrored_digest = compute_workflow_digest(mirrored)

    assert first_digest != changed_digest
    assert changed_digest == mirrored_digest


def test_workflow_scaffold_can_use_packaged_command_eval_template(tmp_path):
    from opentraces.core.workflows import create_workflow, list_workflow_templates

    assert "skill-command-trajectory-eval-v1" in list_workflow_templates()

    created = create_workflow(
        "custom-command-eval",
        description="Custom command trajectory eval workflow",
        template="skill-command-trajectory-eval-v1",
    )

    assert created.name == "custom-command-eval"
    assert created.description == "Custom command trajectory eval workflow"
    assert (created.path / "schemas" / "row.schema.json").exists()
    assert (created.path / "scripts" / "build_rows.py").exists()
    assert "custom-command-eval" in (created.path / "tests" / "README.md").read_text()

    source = tmp_path / "raw-command-trajectories.jsonl"
    example_input = json.loads(
        (created.path / "examples" / "input-candidate-packet.json").read_text(
            encoding="utf-8"
        )
    )
    source.write_text(
        json.dumps(example_input, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "rows.jsonl"
    subprocess.run(
        [
            sys.executable,
            str(created.path / "scripts" / "build_rows.py"),
            "--source",
            str(source),
            "--output",
            str(output),
        ],
        check=True,
    )

    expected = json.loads((created.path / "examples" / "expected-row.json").read_text())
    actual = json.loads(output.read_text(encoding="utf-8"))
    assert actual == expected


def test_install_workflow_copies_skill_package_without_source_path_dependency(tmp_path):
    from opentraces.core.workflows import install_workflow, remove_workflow

    source = tmp_path / "source-skill"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\n"
        "name: installed-curator\n"
        "description: Installed workflow\n"
        "mode: agent-skill\n"
        "---\n"
        "# Installed workflow\n"
    )
    (source / "examples").mkdir()
    (source / "examples" / "expected-row.json").write_text("{}\n")

    installed = install_workflow(source)
    assert installed.name == "installed-curator"
    assert installed.path != source
    assert (installed.path / "examples" / "expected-row.json").read_text() == "{}\n"

    (source / "examples" / "expected-row.json").write_text('{"changed": true}\n')
    assert (installed.path / "examples" / "expected-row.json").read_text() == "{}\n"

    removed_path = remove_workflow("installed-curator")
    assert removed_path.name == "installed-curator"
    assert not removed_path.exists()


def test_workflow_name_validation_rejects_paths():
    import pytest

    from opentraces.core.workflows import create_workflow

    with pytest.raises(ValueError):
        create_workflow("../bad")

    with pytest.raises(ValueError):
        create_workflow("bad/name")

    with pytest.raises(ValueError):
        create_workflow("")


# --- Plan 092 Track 2 U6: nested YAML front matter + security contract -------


def _write_workflow_md(tmp_path, body: str):
    md = tmp_path / "wf.md"
    md.write_text(body, encoding="utf-8")
    return md


def test_frontmatter_parses_nested_security_contract(tmp_path):
    from opentraces.core.workflows import resolve_workflow_reference

    md = _write_workflow_md(
        tmp_path,
        "---\n"
        "name: secure-curator\n"
        "description: Curate rows with a security contract\n"
        "security:\n"
        "  required_tools: [regex, entropy]\n"
        "  optional_tools: [business_logic, path_anonymizer, classifier]\n"
        "  default_enabled_tools: [business_logic]\n"
        "  disallowed_tools: []\n"
        "  allow_disable_required: false\n"
        "---\n\n"
        "# secure-curator\n\nEmit rows.\n",
    )

    pkg = resolve_workflow_reference(md)
    assert pkg.name == "secure-curator"
    assert pkg.description == "Curate rows with a security contract"
    assert pkg.security is not None
    assert pkg.security.required_tools == ["regex", "entropy"]
    assert pkg.security.default_enabled_tools == ["business_logic"]
    # The contract resolves required + default-enabled in canonical order.
    assert pkg.security.resolved_enabled_tools() == ["regex", "entropy", "business_logic"]


def test_frontmatter_without_security_block_has_no_contract(tmp_path):
    from opentraces.core.workflows import resolve_workflow_reference

    md = _write_workflow_md(
        tmp_path,
        "---\nname: plain-curator\ndescription: No security block\n---\n\n# plain-curator\n",
    )
    pkg = resolve_workflow_reference(md)
    assert pkg.security is None


def test_frontmatter_legacy_flat_fields_still_parse(tmp_path):
    """Back-compat: flat key: value front matter still yields name/description."""
    from opentraces.core.workflows import resolve_workflow_reference

    md = _write_workflow_md(
        tmp_path,
        "---\nname: legacy-curator\ndescription: Legacy flat front matter\n---\n\n# legacy-curator\n",
    )
    pkg = resolve_workflow_reference(md)
    assert pkg.name == "legacy-curator"
    assert pkg.description == "Legacy flat front matter"


def test_frontmatter_invalid_security_contract_degrades_to_none(tmp_path):
    """An invalid `security:` contract (unknown tool) must not crash workflow
    loading — load_workflow runs for every package in list_workflows(). It
    degrades to no contract with a warning so one bad workflow cannot break the
    whole listing; dataset creation surfaces the missing policy separately."""
    from opentraces.core.workflows import resolve_workflow_reference

    md = _write_workflow_md(
        tmp_path,
        "---\n"
        "name: bad-curator\n"
        "security:\n"
        "  required_tools: [not_a_tool]\n"
        "---\n\n# bad-curator\n",
    )
    pkg = resolve_workflow_reference(md)
    assert pkg.security is None


def test_frontmatter_non_dict_security_block_degrades_to_none(tmp_path):
    """A scalar `security:` typo degrades to no contract (with a warning),
    rather than raising and crashing `workflow list`."""
    from opentraces.core.workflows import resolve_workflow_reference

    md = _write_workflow_md(
        tmp_path,
        "---\nname: typo-curator\nsecurity: recommended\n---\n\n# typo-curator\n",
    )
    pkg = resolve_workflow_reference(md)
    assert pkg.security is None
