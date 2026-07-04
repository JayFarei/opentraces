from __future__ import annotations

import json

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.cli.dataset import dataset_group
from opentraces.core.datasets import dataset_path
from opentraces.core.workflows import create_workflow


def test_dataset_cli_new_list_remove_round_trip(tmp_path):
    runner = CliRunner()
    # #190: bind-time validation resolves the workflow BEFORE creating anything,
    # so the bind must name a real installed workflow (the pre-#190 opaque
    # bare-name bind is dead — see test_dataset_new_bogus_workflow_exits_2).
    pkg = create_workflow("grill-me-intent-curator")

    created = runner.invoke(
        dataset_group,
        [
            "new",
            "grill-me-intents",
            "--description",
            "Intent summaries",
            "--workflow",
            "grill-me-intent-curator",
            "--json",
        ],
    )
    assert created.exit_code == 0, created.output
    created_payload = json.loads(created.output)
    assert created_payload["dataset"]["name"] == "grill-me-intents"
    assert created_payload["dataset"]["path"].endswith("grill-me-intents")
    # The real resolved digest is pinned at bind time (never sha256:unconfigured).
    workflow = created_payload["dataset"]["manifest"]["workflow"]
    assert workflow["digest"] == pkg.digest
    assert workflow["digest"].startswith("sha256:")
    assert workflow["digest"] != "sha256:unconfigured"

    listed = runner.invoke(dataset_group, ["list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert [item["name"] for item in json.loads(listed.output)["datasets"]] == [
        "grill-me-intents"
    ]

    removed = runner.invoke(dataset_group, ["remove", "grill-me-intents", "--yes", "--json"])
    assert removed.exit_code == 0, removed.output
    assert not dataset_path("grill-me-intents").exists()


def test_dataset_new_bogus_workflow_exits_2_and_creates_nothing():
    """#190: an unresolvable --workflow ref exits rc=2 (invalid usage) and binds
    nothing — no dataset directory is created."""
    runner = CliRunner()
    created = runner.invoke(
        dataset_group,
        ["new", "doomed", "--workflow", "totally-bogus-workflow-nope", "--json"],
    )
    assert created.exit_code == 2, created.output
    payload = json.loads(created.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "workflow_unresolved"
    assert not dataset_path("doomed").exists()


def test_dataset_new_bogus_workflow_digest_override_still_exits_2():
    """An explicit --workflow-digest no longer resurrects the opaque bare-name
    bind #190 kills: an unresolvable name is rc=2 regardless of the digest flag."""
    runner = CliRunner()
    created = runner.invoke(
        dataset_group,
        [
            "new",
            "doomed2",
            "--workflow",
            "still-bogus",
            "--workflow-digest",
            "sha256:whatever",
            "--json",
        ],
    )
    assert created.exit_code == 2, created.output
    assert not dataset_path("doomed2").exists()


def test_dataset_new_without_workflow_requires_one():
    """#190: the automated path requires a workflow; a bare `dataset new <name>`
    with no --workflow / --rows-file / --from-skill exits rc=2, nothing created."""
    runner = CliRunner()
    created = runner.invoke(dataset_group, ["new", "no-workflow", "--json"])
    assert created.exit_code == 2, created.output
    payload = json.loads(created.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "workflow_required"
    assert not dataset_path("no-workflow").exists()


def test_dataset_new_accepts_markdown_workflow_path(tmp_path):
    workflow_file = tmp_path / "classic-intent-labels.md"
    workflow_file.write_text(
        "---\n"
        "name: classic-intent-labels\n"
        "description: Classic intent trajectory labels\n"
        "---\n\n"
        "# Classic Intent Labels\n\n"
        "Use `opentraces trace slice --template bursts` as the source packet.\n",
        encoding="utf-8",
    )
    runner = CliRunner()

    created = runner.invoke(
        dataset_group,
        [
            "new",
            "classic-intents",
            "--workflow",
            str(workflow_file),
            "--json",
        ],
    )
    assert created.exit_code == 0, created.output
    payload = json.loads(created.output)
    workflow = payload["dataset"]["manifest"]["workflow"]
    assert workflow["skill"] == "classic-intent-labels"
    assert workflow["digest"].startswith("sha256:")
    assert workflow["config"]["source"] == str(workflow_file.resolve())
    assert workflow["config"]["source_type"] == "file"
    assert workflow["config"]["entrypoint"] == str(workflow_file.resolve())


def test_dataset_new_accepts_workflow_schema_without_seed_rows(tmp_path):
    schema_file = tmp_path / "intent-trajectory.schema.json"
    schema_file.write_text(
        json.dumps(
            {
                "type": "object",
                "required": ["source_trace_id", "source_unit_id", "intent"],
                "properties": {
                    "source_trace_id": {"type": "string"},
                    "source_unit_id": {"type": "string"},
                    "intent": {"type": "string"},
                },
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    # #190: the bound workflow must resolve at bind time.
    create_workflow("hf-intent-trajectory-outcome")

    created = runner.invoke(
        dataset_group,
        [
            "new",
            "hf-intents",
            "--workflow",
            "hf-intent-trajectory-outcome",
            "--schema",
            str(schema_file),
            "--query-semantic",
            "hugging face",
            "--query-source",
            "projection",
            "--json",
        ],
    )

    assert created.exit_code == 0, created.output
    payload = json.loads(created.output)
    assert payload["dataset"]["manifest"]["workflow"]["skill"] == (
        "hf-intent-trajectory-outcome"
    )
    schema_payload = json.loads(
        (dataset_path("hf-intents") / "schemas" / "row.schema.json").read_text()
    )
    assert schema_payload["required"] == ["source_trace_id", "source_unit_id", "intent"]
    candidate_query = payload["dataset"]["manifest"]["candidate_query"]
    assert candidate_query["scope"] == "all-projects"
    assert candidate_query["args"]["semantic"] == "hugging face"
    assert candidate_query["args"]["source"] == "projection"


def _write_secure_workflow(tmp_path, *, allow_disable_required=False):
    """A workflow whose front matter declares a security contract."""
    md = tmp_path / "secure-workflow.md"
    md.write_text(
        "---\n"
        "name: secure-workflow\n"
        "description: Curate with a security contract\n"
        "security:\n"
        "  required_tools: [regex, entropy]\n"
        "  optional_tools: [business_logic, path_anonymizer]\n"
        "  default_enabled_tools: [business_logic]\n"
        f"  allow_disable_required: {'true' if allow_disable_required else 'false'}\n"
        "---\n\n# secure-workflow\nEmit rows.\n",
        encoding="utf-8",
    )
    return md


def test_dataset_new_seeds_manifest_security_from_workflow_contract(tmp_path):
    """Plan 092 Track 2: `dataset new --workflow` seeds the dataset manifest
    security policy from the workflow contract and pins the workflow digest.
    Dataset security is NOT a global config toggle."""
    md = _write_secure_workflow(tmp_path)
    runner = CliRunner()
    created = runner.invoke(
        dataset_group,
        ["new", "security-visible", "--workflow", str(md), "--json"],
    )

    assert created.exit_code == 0, created.output
    security = json.loads(created.output)["dataset"]["security"]
    assert security["scope"] == "dataset"
    assert security["source"] == "workflow"
    assert security["source_workflow_digest"].startswith("sha256:")
    # required + default_enabled, in canonical order.
    assert security["enabled_tools"] == ["regex", "entropy", "business_logic"]
    assert security["required_tools"] == ["regex", "entropy"]
    assert security["required_satisfied"] is True


def test_dataset_new_named_installed_workflow_seeds_security():
    """Plan 092 (Codex #1): a bare installed-workflow name (not a path) still
    seeds the dataset security policy from that workflow's contract."""
    from opentraces.core.workflows import create_workflow

    pkg = create_workflow("secure-installed")
    (pkg.path / "SKILL.md").write_text(
        "---\n"
        "name: secure-installed\n"
        "security:\n"
        "  required_tools: [regex, entropy]\n"
        "  optional_tools: [business_logic]\n"
        "  default_enabled_tools: [business_logic]\n"
        "---\n\n# secure-installed\nEmit rows.\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    created = runner.invoke(
        dataset_group, ["new", "named-ds", "--workflow", "secure-installed", "--json"]
    )
    assert created.exit_code == 0, created.output
    sec = json.loads(created.output)["dataset"]["security"]
    assert sec["source"] == "workflow"
    assert sec["source_workflow_digest"].startswith("sha256:")
    assert sec["required_tools"] == ["regex", "entropy"]
    assert sec["enabled_tools"] == ["regex", "entropy", "business_logic"]


def test_dataset_security_edits_only_that_dataset_manifest(tmp_path):
    """`dataset security <name>` edits one dataset's manifest policy, with
    per-dataset isolation; it never reads or writes global config."""
    runner = CliRunner()
    md = _write_secure_workflow(tmp_path)
    assert runner.invoke(
        dataset_group, ["new", "ds-a", "--workflow", str(md), "--json"]
    ).exit_code == 0
    assert runner.invoke(
        dataset_group, ["new", "ds-b", "--workflow", str(md), "--json"]
    ).exit_code == 0

    # Enable an optional tool on ds-a only.
    enabled = runner.invoke(
        main,
        ["dataset", "security", "ds-a", "--tool", "path_anonymizer", "--enable", "--json"],
    )
    assert enabled.exit_code == 0, enabled.output
    payload = json.loads(enabled.output)
    assert payload["dataset"] == "ds-a"
    assert payload["security"]["scope"] == "dataset"
    assert "path_anonymizer" in payload["security"]["enabled_tools"]
    assert payload["changes"]["enabled"] == ["path_anonymizer"]
    # A human edit marks the policy manually managed (was seeded "workflow").
    assert payload["security"]["source"] == "manual"

    # ds-b is unchanged (per-dataset isolation).
    other = runner.invoke(main, ["dataset", "security", "ds-b", "--json"])
    assert other.exit_code == 0, other.output
    assert "path_anonymizer" not in json.loads(other.output)["security"]["enabled_tools"]


def test_dataset_security_missing_dataset_exits_3():
    runner = CliRunner()
    result = runner.invoke(main, ["dataset", "security", "does-not-exist", "--json"])
    assert result.exit_code == 3
    assert "does-not-exist" in result.output


def test_dataset_security_required_tool_disable_is_gated(tmp_path):
    """A required tool cannot be disabled unless the contract allows it AND the
    caller passes --unsafe-override; the override is recorded in the manifest."""
    runner = CliRunner()

    # Contract forbids disabling required tools.
    md = _write_secure_workflow(tmp_path, allow_disable_required=False)
    assert runner.invoke(
        dataset_group, ["new", "ds-locked", "--workflow", str(md), "--json"]
    ).exit_code == 0
    forbidden = runner.invoke(
        main, ["dataset", "security", "ds-locked", "--tool", "regex", "--disable", "--json"]
    )
    assert forbidden.exit_code == 2
    assert "forbids" in forbidden.output

    # Contract allows it, but only with --unsafe-override.
    md2 = _write_secure_workflow(tmp_path, allow_disable_required=True)
    assert runner.invoke(
        dataset_group, ["new", "ds-open", "--workflow", str(md2), "--json"]
    ).exit_code == 0
    needs_override = runner.invoke(
        main, ["dataset", "security", "ds-open", "--tool", "regex", "--disable", "--json"]
    )
    assert needs_override.exit_code == 2
    assert "--unsafe-override" in needs_override.output

    overridden = runner.invoke(
        main,
        [
            "dataset", "security", "ds-open",
            "--tool", "regex", "--disable",
            "--unsafe-override", "--reason", "legacy import", "--json",
        ],
    )
    assert overridden.exit_code == 0, overridden.output
    payload = json.loads(overridden.output)
    assert "regex" not in payload["security"]["enabled_tools"]
    assert payload["security"]["overrides"] == [{"tool": "regex", "reason": "legacy import"}]
    assert payload["security"]["required_satisfied"] is False
    assert payload["security"]["missing_required_tools"] == ["regex"]


def test_dataset_and_workflow_groups_are_registered_on_root_cli():
    runner = CliRunner()

    result = runner.invoke(main, ["dataset", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["datasets"] == []

    result = runner.invoke(main, ["workflow", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["workflows"] == []


def test_dataset_review_interactive_clients_are_decommissioned():
    runner = CliRunner()

    result = runner.invoke(dataset_group, ["review", "example-dataset", "--web"])
    assert result.exit_code == 2
    assert "Interactive TUI/web review clients are decommissioned" in result.output

    result = runner.invoke(dataset_group, ["review", "example-dataset", "--tui", "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["error"] == "interactive_review_decommissioned"
