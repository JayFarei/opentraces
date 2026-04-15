"""Tests for new flat workflow verbs (Steps 7 + 8 of the CLI restructure).

Step 7 registers each per-trace verb at the root in addition to its
existing trace-group home: ot add, ot show, ot list, ot reject, ot reset,
ot redact, ot discard, ot llm-review.

Step 8 adds the approval gate to ot add: BLOCKED + REJECTED traces are
refused with a clear pointer to ot redact / ot reject.

ot list also gains --projects (replacing the legacy `ot projects list`
subgroup) and --remote <name> (per-remote pending filter from Step 2).
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.paths import MARKER_FILENAME


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    from opentraces.core import paths as paths_mod
    from opentraces.core import config as config_mod

    opentraces_dir = home / ".opentraces"
    projects_dir = opentraces_dir / "projects"
    monkeypatch.setattr(paths_mod, "OPENTRACES_DIR", opentraces_dir)
    monkeypatch.setattr(paths_mod, "CONFIG_PATH", opentraces_dir / "config.json")
    monkeypatch.setattr(paths_mod, "CREDENTIALS_PATH", opentraces_dir / "credentials")
    monkeypatch.setattr(paths_mod, "PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(config_mod, "OPENTRACES_DIR", opentraces_dir)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", opentraces_dir / "config.json")
    monkeypatch.setattr(config_mod, "CREDENTIALS_PATH", opentraces_dir / "credentials")
    monkeypatch.setattr(config_mod, "PROJECTS_DIR", projects_dir)
    return home


@pytest.fixture
def project_dir(tmp_path, monkeypatch, isolated_home):
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    from opentraces.core.config import save_project_config
    save_project_config(project, {"review_policy": "review"})
    return project


@pytest.fixture
def runner():
    return CliRunner()


def _seed_trace(project_dir, trace_id: str, *, status: str = "staged", block_reason: str | None = None):
    """Helper: seed one trace into the project's state.json."""
    from opentraces.core.config import get_project_state_path, get_project_traces_dir
    from opentraces.core.state import StateManager, TraceStatus

    state = StateManager(state_path=get_project_state_path(project_dir))
    staging_dir = get_project_traces_dir(project_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_file = staging_dir / f"{trace_id}.jsonl"
    # Minimal valid TraceRecord JSON line — enough for state but not for real load.
    staging_file.write_text(json.dumps({"trace_id": trace_id, "session_id": "sess-1", "task": {"description": "test trace"}}) + "\n")

    if status == "blocked":
        state.block_trace(trace_id, reason=block_reason or "test block", session_id="sess-1", file_path=str(staging_file))
    else:
        state.set_trace_status(
            trace_id, TraceStatus(status), session_id="sess-1", file_path=str(staging_file)
        )


# ---------------------------------------------------------------------------
# Step 7: flat verbs registered at the root.
# ---------------------------------------------------------------------------


class TestFlatVerbsExist:
    @pytest.mark.parametrize(
        "verb", ["add", "show", "list", "reject", "reset", "redact", "discard", "llm-review"]
    )
    def test_flat_verb_is_registered_at_root(self, runner, verb) -> None:
        """A registered command's --help exits 0; an unknown one exits 2 with
        'No such command'."""
        result = runner.invoke(main, [verb, "--help"])
        assert result.exit_code == 0, (
            f"`ot {verb} --help` failed (exit {result.exit_code}); output:\n{result.output}"
        )


class TestFlatListProjects:
    def test_ot_list_projects_runs(self, runner, project_dir) -> None:
        """ot list --projects replaces the legacy `ot projects list` group."""
        result = runner.invoke(main, ["list", "--projects"])
        assert result.exit_code == 0, result.output


class TestFlatVerbsDispatchToImpl:
    def test_show_unknown_id_returns_not_found(self, runner, project_dir) -> None:
        # The flat ot show should reach the same not-found path as ot trace show.
        result = runner.invoke(main, ["show", "doesnotexist"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_reject_unknown_id_returns_not_found(self, runner, project_dir) -> None:
        result = runner.invoke(main, ["reject", "doesnotexist"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Step 7 + 8: ot add (replaces ot commit / ot trace commit).
# ---------------------------------------------------------------------------


class TestOtAdd:
    def test_add_existing_staged_trace_succeeds(self, runner, project_dir) -> None:
        """Bare-minimum happy path."""
        _seed_trace(project_dir, "abc123def456", status="staged")
        result = runner.invoke(main, ["add", "abc123def456"])
        assert result.exit_code == 0, result.output

        # Status should now be COMMITTED.
        from opentraces.core.config import get_project_state_path
        from opentraces.core.state import StateManager, TraceStatus
        state = StateManager(state_path=get_project_state_path(project_dir))
        assert state.get_trace("abc123def456").status == TraceStatus.COMMITTED

    def test_add_unknown_trace_errors(self, runner, project_dir) -> None:
        result = runner.invoke(main, ["add", "ghost"])
        assert result.exit_code != 0


class TestOtAddRefusesBlocked:
    """Step 8: BLOCKED and REJECTED traces cannot be staged for push.
    The error message names the unblocking verbs."""

    def test_add_blocked_trace_errors(self, runner, project_dir) -> None:
        _seed_trace(project_dir, "blocked123abc", status="blocked", block_reason="TruffleHog: 1 finding")
        result = runner.invoke(main, ["add", "blocked123abc"])
        assert result.exit_code != 0, result.output

    def test_add_blocked_message_names_unblocking_verbs(self, runner, project_dir) -> None:
        _seed_trace(project_dir, "blocked456def", status="blocked", block_reason="TruffleHog: 1 finding")
        result = runner.invoke(main, ["add", "blocked456def"])
        msg = result.output.lower() + (result.stderr or "").lower()
        # The message should point at the recovery path.
        assert "redact" in msg, f"missing 'redact' hint in: {result.output!r}"
        assert "reject" in msg, f"missing 'reject' hint in: {result.output!r}"

    def test_add_blocked_message_includes_reason(self, runner, project_dir) -> None:
        _seed_trace(project_dir, "blocked789xyz", status="blocked", block_reason="TruffleHog: 3 findings")
        result = runner.invoke(main, ["add", "blocked789xyz"])
        # The block_reason should surface in the error so the user knows what was found.
        assert "TruffleHog" in result.output or "trufflehog" in result.output.lower()

    def test_add_rejected_trace_errors(self, runner, project_dir) -> None:
        _seed_trace(project_dir, "rejected123abc", status="rejected")
        result = runner.invoke(main, ["add", "rejected123abc"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Step 7: ot llm-review (renamed from ot review-llm).
# ---------------------------------------------------------------------------


class TestOtLlmReview:
    def test_llm_review_appears_in_help(self, runner) -> None:
        result = runner.invoke(main, ["--help"])
        assert "llm-review" in result.output

    def test_llm_review_help_works(self, runner) -> None:
        result = runner.invoke(main, ["llm-review", "--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Step 7: ot redact uses the new content-targeted shape.
# ---------------------------------------------------------------------------


class TestFlatRedact:
    def test_flat_redact_help_shows_pattern(self, runner) -> None:
        result = runner.invoke(main, ["redact", "--help"])
        assert result.exit_code == 0
        # Help text should mention pattern (the new positional arg).
        assert "pattern" in result.output.lower() or "PATTERN" in result.output
