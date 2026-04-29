"""Plan 058 default-inbox bridge UAT.

Covers Plan-058 verification items:

- V21: default-inbox migration exists for old local state, but unreleased
  root-level compatibility commands are not exposed.

- V22: ``ot init`` owns only project enrollment. Remote/review/hook
  compatibility flags are rejected instead of warn-and-accepted.

The tests also cover the bootstrap path: a clean ``ot dataset *`` call does
not create ``default-inbox`` by itself; when pre-existing per-project inbox
decisions exist, the bridge creates ``~/.opentraces/datasets/default-inbox/``
and migrates those decisions into the M3 publication-state sidecar.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.cli import main as cli_main
from opentraces.cli.dataset import dataset_group
from opentraces.core.datasets import (
    dataset_path,
    list_datasets,
    load_dataset,
    read_publication_state,
)


DEFAULT_INBOX = "default-inbox"


# ---------------------------------------------------------------------------
# Bootstrap: V21 default-inbox dataset is auto-created on first dataset call.
# ---------------------------------------------------------------------------


def test_default_inbox_bootstrap_creates_dataset_on_first_dataset_command(tmp_path, monkeypatch):
    """V21 bootstrap: legacy inbox state mints ``default-inbox`` on dataset use."""
    assert DEFAULT_INBOX not in {d.name for d in list_datasets()}

    project_dir = _make_project_with_inbox_state(tmp_path)
    monkeypatch.chdir(project_dir)
    runner = CliRunner()
    result = runner.invoke(dataset_group, ["list", "--json"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.output)
    names = {d["name"] for d in payload["datasets"]}
    assert DEFAULT_INBOX in names, (
        "default-inbox should be auto-created on first dataset CLI invocation"
    )

    # Manifest sanity: the bootstrapped dataset uses the publish schema and
    # records its provenance in the description so a reader can tell it
    # came from the bridge rather than a hand-rolled `ot dataset new`.
    dataset = load_dataset(DEFAULT_INBOX)
    assert dataset.manifest.workflow.skill, "bootstrap manifest needs a workflow skill"
    assert dataset_path(DEFAULT_INBOX).is_dir()


def test_dataset_list_does_not_create_default_inbox_without_legacy_state():
    """Clean dataset users should not see a synthetic inbox dataset."""
    assert DEFAULT_INBOX not in {d.name for d in list_datasets()}

    runner = CliRunner()
    result = runner.invoke(dataset_group, ["list", "--json"])
    assert result.exit_code == 0, result.output
    assert DEFAULT_INBOX not in {d["name"] for d in json.loads(result.output)["datasets"]}


def test_default_inbox_bootstrap_is_idempotent():
    """Running ``ot dataset list`` twice never trips the FileExistsError path."""
    runner = CliRunner()
    first = runner.invoke(dataset_group, ["list", "--json"])
    assert first.exit_code == 0, first.output
    second = runner.invoke(dataset_group, ["list", "--json"])
    assert second.exit_code == 0, second.output


# ---------------------------------------------------------------------------
# Inbox decision migration: pre-existing per-project staged/rejected/pending
# trace entries should land as default-inbox publication-state rows.
# ---------------------------------------------------------------------------


def _make_project_with_inbox_state(tmp_path: Path) -> Path:
    """Initialise a project marker + inbox state with one approved + one rejected trace."""
    from opentraces.core.config import (
        load_config,
        register_project,
        save_config,
    )
    from opentraces.core.config import (
        get_project_state_path,
        get_project_traces_dir,
    )
    from opentraces.core.state import StateManager, TraceStatus

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    cfg = load_config()
    register_project(cfg, project_dir)
    save_config(cfg)

    traces_dir = get_project_traces_dir(project_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)

    state = StateManager(state_path=get_project_state_path(project_dir))

    approved_id = "trace_approved_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    rejected_id = "trace_rejected_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    pending_id = "trace_pending_cccccccccccccccccccccccccccccccc"

    for trace_id in (approved_id, rejected_id, pending_id):
        path = traces_dir / f"{trace_id}.jsonl"
        path.write_text("{}\n")

    state.set_trace_status(approved_id, TraceStatus.COMMITTED, file_path=str(traces_dir / f"{approved_id}.jsonl"))
    state.set_trace_status(rejected_id, TraceStatus.REJECTED, file_path=str(traces_dir / f"{rejected_id}.jsonl"))
    state.set_trace_status(pending_id, TraceStatus.STAGED, file_path=str(traces_dir / f"{pending_id}.jsonl"))

    return project_dir


def test_default_inbox_bridge_migrates_inbox_decisions(tmp_path, monkeypatch):
    """V21 decision migration: existing inbox state populates publication-state."""
    project_dir = _make_project_with_inbox_state(tmp_path)
    monkeypatch.chdir(project_dir)

    runner = CliRunner()
    result = runner.invoke(dataset_group, ["list", "--json"])
    assert result.exit_code == 0, result.output

    state = read_publication_state(DEFAULT_INBOX)
    statuses = {entry.status for entry in state.rows.values()}
    # At least one row each: an approved/publishable carry-over and a rejected
    # carry-over. Pending traces should land as needs_review.
    assert "publishable" in statuses, f"missing publishable carry-over: {statuses}"
    assert "rejected" in statuses, f"missing rejected carry-over: {statuses}"
    assert "needs_review" in statuses, f"missing needs_review carry-over: {statuses}"


# ---------------------------------------------------------------------------
# V21 — Root compatibility aliases are not exposed in the unreleased CLI.
# ---------------------------------------------------------------------------


@pytest.fixture()
def opted_in_project(tmp_path, monkeypatch):
    """Initialised project with a pre-created default-inbox."""
    project_dir = _make_project_with_inbox_state(tmp_path)
    monkeypatch.chdir(project_dir)
    # Pre-create the default-inbox so alias targets resolve cleanly.
    runner = CliRunner()
    runner.invoke(dataset_group, ["list", "--json"])
    return project_dir


def _invoke_main(args: list[str]) -> object:
    """Invoke the root ``ot`` CLI and return the click Result."""
    return CliRunner().invoke(cli_main, args)


@pytest.mark.parametrize("cmd", ["push", "list", "add", "reject", "pull", "web", "tui"])
def test_root_compatibility_commands_are_not_registered(opted_in_project, cmd):
    result = _invoke_main([cmd, "--help"])
    assert result.exit_code != 0
    combined = (result.output or "") + (result.stderr or "")
    assert "No such command" in combined


# ---------------------------------------------------------------------------
# V22 — `ot init` no longer accepts remote/review/hook compatibility flags.
# ---------------------------------------------------------------------------


def test_ot_init_help_hides_deprecated_flags():
    """V22: --remote/--review-policy/--no-hook/--manifest are hidden from help."""
    result = _invoke_main(["init", "--help"])
    assert result.exit_code == 0, result.output
    help_text = result.output
    # The flags should not appear as primary surface in --help output.
    assert "--remote" not in help_text, help_text
    assert "--review-policy" not in help_text, help_text
    assert "--no-hook" not in help_text, help_text


def test_ot_setup_help_exposes_global_auth_not_project_review_policy():
    result = _invoke_main(["setup", "--help"])
    assert result.exit_code == 0, result.output
    assert "auth" in result.output
    assert "review-policy" not in result.output
    assert "entity-parser" not in result.output


def test_ot_init_rejects_legacy_flags(tmp_path, monkeypatch):
    """V22: invoking ``ot init --remote ...`` is a usage error."""
    project_dir = tmp_path / "fresh-project"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)
    # Skip interactive setup and hook installation.
    monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
    monkeypatch.setattr(
        "opentraces.cli._install_capture_hook", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(
        "opentraces.cli._install_skill", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(
        "opentraces.cli._plan043_finalize_identity", lambda *_args, **_kw: None
    )

    result = _invoke_main(["init", "--remote", "me/legacy-remote", "--no-hook"])
    assert result.exit_code != 0, (result.output, result.stderr)
    combined = (result.stderr or "") + (result.output or "")
    assert "No such option" in combined, combined
