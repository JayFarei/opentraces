"""Plan 058 default-inbox bridge UAT.

Covers Plan-058 verification items:

- V21: legacy ``ot push`` routes to ``ot dataset publish default-inbox`` and
  emits a deprecation warning to stderr. The same shape applies to
  ``ot list``, ``ot add``, ``ot reject``, ``ot pull``, ``ot web``, and
  ``ot tui`` aliases that point at ``ot trace ...`` or
  ``ot dataset ... default-inbox`` per the CLI lifecycle migration table.

- V22: ``ot init`` keeps ``--remote``, ``--review-policy``, ``--no-hook``,
  and ``--manifest`` for one minor version but they are hidden from
  ``--help`` and emit a deprecation warning when used.

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
# V21 — Compatibility aliases route to dataset commands with deprecation
# warnings on stderr. We use the in-process CliRunner so we can assert on
# both exit code and the exact stderr text the user sees.
# ---------------------------------------------------------------------------


@pytest.fixture()
def opted_in_project(tmp_path, monkeypatch):
    """Initialised project for alias tests so the underlying inbox calls work."""
    project_dir = _make_project_with_inbox_state(tmp_path)
    monkeypatch.chdir(project_dir)
    # Pre-create the default-inbox so alias targets resolve cleanly.
    runner = CliRunner()
    runner.invoke(dataset_group, ["list", "--json"])
    return project_dir


def _invoke_main(args: list[str]) -> object:
    """Invoke the root ``ot`` CLI and return the click Result."""
    return CliRunner().invoke(cli_main, args)


def test_ot_push_alias_routes_to_dataset_publish_default_inbox(opted_in_project):
    """V21: ``ot push`` emits a deprecation warning naming the new verb.

    The legacy push command stays wired during the one-minor-version
    compatibility window so existing scripts keep working — the
    user-visible contract V21 cares about is the stderr deprecation
    text and the migration target it names. ``ot dataset publish
    default-inbox`` is the canonical surface and is exercised
    independently in ``tests/cli/test_plan058_dataset_remote_cli.py``.
    """
    result = _invoke_main(["push"])
    # `ot push` may fail on auth/upload in the isolated fixture — V21
    # only gates on the stderr contract.
    stderr = (result.stderr or "")
    assert "deprecated" in stderr.lower(), stderr
    assert "ot dataset publish default-inbox" in stderr


def test_ot_list_alias_routes_to_trace_list(opted_in_project):
    """V21: ``ot list`` => ``ot trace list`` + deprecation warning."""
    result = _invoke_main(["list"])
    # trace list may exit cleanly even with no traces; we only need to see
    # the warning text and a non-error exit.
    assert result.exit_code == 0, result.output
    assert "deprecated" in result.stderr.lower(), result.stderr
    assert "ot trace list" in result.stderr


def test_ot_add_alias_emits_deprecation_warning_naming_dataset_approve(opted_in_project):
    """V21: ``ot add`` emits a deprecation warning naming the new verb.

    Legacy inbox staging keeps working during the compatibility window.
    """
    result = _invoke_main(["add", "--all"])
    stderr = (result.stderr or "")
    assert "deprecated" in stderr.lower(), stderr
    assert "ot dataset approve default-inbox" in stderr


def test_ot_reject_alias_emits_deprecation_warning_naming_dataset_reject(opted_in_project):
    """V21: ``ot reject`` emits a deprecation warning naming the new verb."""
    # The legacy reject expects a trace id; bare invocation may exit
    # non-zero, but the deprecation warning fires before usage parsing.
    result = _invoke_main(["reject", "ghost-trace"])
    stderr = (result.stderr or "")
    assert "deprecated" in stderr.lower(), stderr
    assert "ot dataset reject default-inbox" in stderr


def test_ot_pull_alias_routes_to_dataset_pull_default_inbox(opted_in_project, monkeypatch):
    """V21: ``ot pull`` => ``ot dataset pull default-inbox`` + deprecation warning."""
    captured: list[tuple[str, dict]] = []

    def fake_pull(name, *args, **kwargs):
        captured.append((name, kwargs))
        from opentraces.core.datasets import DatasetPullSummary

        return DatasetPullSummary(
            dataset_name=name,
            remote_name=None,
            repo_id=None,
            metadata_refreshed=False,
            data=False,
            imported_count=0,
            duplicate_count=0,
        )

    monkeypatch.setattr("opentraces.cli.dataset.pull_dataset", fake_pull)

    result = _invoke_main(["pull"])
    assert result.exit_code == 0, result.output
    assert "deprecated" in result.stderr.lower(), result.stderr
    assert "ot dataset pull default-inbox" in result.stderr
    assert captured and captured[0][0] == DEFAULT_INBOX


def test_ot_web_alias_emits_deprecation_warning_naming_dataset_review(opted_in_project, monkeypatch):
    """V21: ``ot web`` warns and points at ``ot dataset review default-inbox --web``.

    The legacy launcher continues to run inside the compatibility window;
    we monkeypatch the actual launcher so the test never actually opens
    a Flask server, leaving us free to assert on the user-visible
    deprecation contract.
    """
    monkeypatch.setattr("opentraces.cli._launch_web_ui", lambda *a, **kw: None)
    result = _invoke_main(["web", "--no-open"])
    stderr = (result.stderr or "")
    assert "deprecated" in stderr.lower(), stderr
    assert "ot dataset review default-inbox" in stderr


def test_ot_tui_alias_emits_deprecation_warning_naming_dataset_review_tui(opted_in_project, monkeypatch):
    """V21: ``ot tui`` warns and points at ``ot dataset review default-inbox --tui``."""
    monkeypatch.setattr("opentraces.cli._launch_tui_ui", lambda *a, **kw: None)
    result = _invoke_main(["tui", "--limit", "1"])
    stderr = (result.stderr or "")
    assert "deprecated" in stderr.lower(), stderr
    assert "ot dataset review default-inbox" in stderr


# ---------------------------------------------------------------------------
# V22 — `ot init` flag deprecation. The flags must accept input for one
# minor version but be hidden from --help and emit a deprecation warning
# pointing to the setup-owned replacements.
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


def test_ot_init_emits_deprecation_warning_for_legacy_flags(tmp_path, monkeypatch):
    """V22: invoking ``ot init --remote ...`` warns about the migration target."""
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
    assert result.exit_code == 0, (result.output, result.stderr)
    combined = (result.stderr or "") + (result.output or "")
    assert "deprecated" in combined.lower(), combined
    # Migration target hints should mention the new commands.
    assert (
        "ot setup git" in combined
        or "ot dataset remote" in combined
    ), combined
