"""AGT-5 — the documented CLI exit-code contract, asserted explicitly.

The CLI documents three non-zero exit-code families for agent consumers:

* **exit 2** — caller/contract violation: usage errors, mutually exclusive
  flags, and security-policy edits the workflow contract forbids;
* **exit 3** — world-not-ready / environment: the remote dataset schema is
  AHEAD of the local one (fail-closed, never overwrite a newer remote);
* **exit 6** — unresolved trace/resource reference (lookup miss).

Each test pins one documented code at the CLI boundary so a refactor that
reshuffles exit codes becomes a named, reviewed contract change instead of a
silent drift. Sibling coverage (kept, not duplicated): schema-ahead plumbing
in tests/cli/test_plan058_dataset_remote_cli.py, the full security-gate
matrix in tests/cli/test_plan057_dataset_cli.py, and ``ot://`` resolution in
tests/cli/test_trace_query_plan056.py.

NOTE on the HFUploader twin: ``opentraces.publish.huggingface.upload``
defines ``RemoteSchemaAheadError`` with the same exit-3 intent, but the
``push``/HFUploader path is not registered on the live CLI (known product
gap) — the reachable exit-3 schema-ahead surface is ``dataset publish``,
asserted below against ``DatasetRemoteSchemaAheadError``.
"""

from __future__ import annotations

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.datasets import DatasetRemoteSchemaAheadError


# ---------------------------------------------------------------------------
# exit 6 — unresolved trace/resource reference
# ---------------------------------------------------------------------------


def test_trace_get_unresolved_trace_ref_exits_6():
    result = CliRunner().invoke(main, ["trace", "get", "nonexistent-trace", "--json"])

    assert result.exit_code == 6, result.output
    assert "Trace not found" in result.output
    assert "Traceback" not in result.output


def test_trace_get_unresolved_ot_resource_exits_6():
    result = CliRunner().invoke(
        main, ["trace", "get", "ot://trace/nonexistent", "--json"]
    )

    assert result.exit_code == 6, result.output
    assert "Trace resource not found" in result.output
    assert "Traceback" not in result.output


def test_trace_get_step_address_unresolved_trace_exits_6():
    """F1: a well-formed step address on an unknown trace is a lookup miss
    (rc=6), same family as the bare-ref miss above."""
    result = CliRunner().invoke(main, ["trace", "get", "nonexistent-trace:3", "--json"])

    assert result.exit_code == 6, result.output
    assert "Trace not found" in result.output
    assert "Traceback" not in result.output


def test_trace_get_malformed_step_selector_exits_2():
    """F1: a malformed selector is a caller/contract violation (rc=2) —
    the address parse runs BEFORE trace resolution, matching ctx/trail."""
    result = CliRunner().invoke(main, ["trace", "get", "nonexistent-trace:junk", "--json"])

    assert result.exit_code == 2, result.output
    assert "Unparseable step address" in result.output
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# exit 3 — remote schema ahead of local (fail-closed publish)
# ---------------------------------------------------------------------------


def test_dataset_publish_schema_ahead_exits_3(monkeypatch):
    monkeypatch.setattr("opentraces.cli.dataset._hf_auth", lambda: ("tok", "user"))

    def fake_publish_dataset(name: str, **kwargs):
        raise DatasetRemoteSchemaAheadError("9.0.0", "1.0.0")

    monkeypatch.setattr("opentraces.cli.dataset.publish_dataset", fake_publish_dataset)

    result = CliRunner().invoke(main, ["dataset", "publish", "schema-ahead-contract"])

    assert result.exit_code == 3, result.output
    assert "9.0.0" in result.output
    assert "1.0.0" in result.output
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# exit 2 — caller/contract violations
# ---------------------------------------------------------------------------


def _write_forbidding_workflow(tmp_path):
    """A workflow whose security contract forbids disabling required tools."""
    md = tmp_path / "forbidding-workflow.md"
    md.write_text(
        "---\n"
        "name: forbidding-workflow\n"
        "description: Contract forbids disabling required tools\n"
        "security:\n"
        "  required_tools: [regex, entropy]\n"
        "  optional_tools: [business_logic]\n"
        "  default_enabled_tools: [business_logic]\n"
        "  allow_disable_required: false\n"
        "---\n\n# forbidding-workflow\nEmit rows.\n",
        encoding="utf-8",
    )
    return md


def test_dataset_security_disable_against_forbidding_contract_exits_2(tmp_path):
    runner = CliRunner()
    md = _write_forbidding_workflow(tmp_path)
    created = runner.invoke(
        main, ["dataset", "new", "ds-exit-contract", "--workflow", str(md), "--json"]
    )
    assert created.exit_code == 0, created.output

    forbidden = runner.invoke(
        main,
        ["dataset", "security", "ds-exit-contract",
         "--tool", "regex", "--disable", "--json"],
    )

    assert forbidden.exit_code == 2, forbidden.output
    assert "forbids" in forbidden.output
    assert "Traceback" not in forbidden.output


def test_security_sanitize_tools_and_use_config_exits_2():
    result = CliRunner().invoke(
        main,
        ["security", "sanitize", "--tools", "regex", "--use-config"],
        input='{"text": "x"}',
    )

    assert result.exit_code == 2, result.output
    assert "not both" in result.output


def test_security_sanitize_without_resolution_flag_exits_2():
    result = CliRunner().invoke(main, ["security", "sanitize"], input='{"text": "x"}')

    assert result.exit_code == 2, result.output
    assert "--tools" in result.output or "--use-config" in result.output
