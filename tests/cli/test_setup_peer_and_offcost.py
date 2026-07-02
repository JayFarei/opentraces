"""Issue #160 (CLI v7 setup slice) — root peer verbs, hidden llm-review,
the `setup doctor` read-twin mount, and the OFF-cost help text (the
degradation DAG) for every signal/hooks toggle.

Covers work items 1/2/3/6 from the slice brief:
  1. `upgrade`/`uninstall` mounted as root peer verbs, `setup upgrade` /
     `setup uninstall` demoted to hidden-but-callable copies.
  2. `setup llm-review` hidden from `setup --help` (still callable).
  3. `setup doctor` mounts the same `doctor` command object, visible.
  6. Each signal/hooks toggle's `--help` states its OFF-cost consequence.

Root `upgrade` is exercised elsewhere (tests/cli/test_setup_upgrade.py);
this file covers `uninstall`, `llm-review`, `doctor`, and the OFF-cost text.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from opentraces.cli import main


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# 1. `uninstall` root peer verb
# ---------------------------------------------------------------------------
class TestUninstallPeerVerb:
    def test_root_uninstall_is_visible_and_callable(self, runner) -> None:
        assert main.commands["uninstall"].hidden is False
        result = runner.invoke(main, ["uninstall", "--help"])
        assert result.exit_code == 0, result.output
        assert "symmetric inverse" in result.output.lower() or "reverse" in result.output.lower()

    def test_setup_uninstall_is_hidden_but_callable(self, runner) -> None:
        assert main.commands["setup"].commands["uninstall"].hidden is True
        result = runner.invoke(main, ["setup", "uninstall", "--help"])
        assert result.exit_code == 0, result.output

    def test_setup_uninstall_not_in_setup_help_listing(self, runner) -> None:
        result = runner.invoke(main, ["setup", "--help"])
        assert result.exit_code == 0
        commands_block = result.output.split("Commands:", 1)[-1]
        assert "uninstall" not in commands_block

    def test_root_and_setup_uninstall_share_the_same_implementation(self, runner) -> None:
        assert main.commands["uninstall"].callback is (
            main.commands["setup"].commands["uninstall"].callback
        )


# ---------------------------------------------------------------------------
# 2. `setup llm-review` hidden
# ---------------------------------------------------------------------------
class TestLlmReviewHidden:
    def test_setup_llm_review_is_hidden(self, runner) -> None:
        assert main.commands["setup"].commands["llm-review"].hidden is True

    def test_setup_llm_review_off_setup_help_listing(self, runner) -> None:
        result = runner.invoke(main, ["setup", "--help"])
        assert result.exit_code == 0
        commands_block = result.output.split("Commands:", 1)[-1]
        assert "llm-review" not in commands_block

    def test_setup_llm_review_still_resolves(self, runner) -> None:
        """Hidden != removed — the command is still fully callable."""
        result = runner.invoke(main, ["setup", "llm-review", "--help"])
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# 3. `setup doctor` read-twin mount
# ---------------------------------------------------------------------------
class TestSetupDoctorMount:
    def test_setup_doctor_is_visible(self, runner) -> None:
        assert main.commands["setup"].commands["doctor"].hidden is False

    def test_setup_doctor_listed_in_setup_help(self, runner) -> None:
        result = runner.invoke(main, ["setup", "--help"])
        assert result.exit_code == 0
        commands_block = result.output.split("Commands:", 1)[-1]
        assert "doctor" in commands_block

    def test_setup_doctor_and_root_doctor_share_the_same_implementation(self, runner) -> None:
        assert main.commands["doctor"].callback is (
            main.commands["setup"].commands["doctor"].callback
        )

    def test_root_doctor_unchanged(self, runner) -> None:
        assert main.commands["doctor"].hidden is False
        result = runner.invoke(main, ["doctor", "--help"])
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# 6. OFF-cost help text (the degradation DAG)
# ---------------------------------------------------------------------------
OFF_COST_CASES = [
    (["setup", "git", "--help"], ["attribution", "blame", "degrade"]),
    (["setup", "watcher", "--help"], ["no maturation", "no backstop"]),
    (["setup", "capture-otlp", "--help"], ["approximated", "not the byte-perfect wire"]),
    (["setup", "claude-code", "--help"], ["watcher backfill", "coarser", "attribution degrades"]),
    (["setup", "codex-cli", "--help"], ["watcher backfill", "coarser", "attribution degrades"]),
    (["setup", "pi", "--help"], ["watcher backfill", "coarser", "attribution degrades"]),
]


@pytest.mark.parametrize(
    ("argv", "must_contain"),
    OFF_COST_CASES,
    ids=["-".join(tok for tok in a if tok != "--help") for a, _ in OFF_COST_CASES],
)
def test_off_cost_help_text_present(runner, argv, must_contain) -> None:
    result = runner.invoke(main, argv)
    assert result.exit_code == 0, result.output
    lowered = result.output.lower()
    for phrase in must_contain:
        assert phrase.lower() in lowered, (
            f"{' '.join(argv)} --help is missing OFF-cost phrase {phrase!r}:\n{result.output}"
        )
