"""Phase 6: the `opentraces skill-verifier` CLI surface is registered with both modes."""

from __future__ import annotations

from click.testing import CliRunner

from opentraces.cli import main


def test_skill_verifier_group_registered_with_verbs():
    out = CliRunner().invoke(main, ["skill-verifier", "--help"])
    assert out.exit_code == 0, out.output
    for verb in ("autoverify", "align", "score", "status"):
        assert verb in out.output


def test_autoverify_help_documents_self_align():
    out = CliRunner().invoke(main, ["skill-verifier", "autoverify", "--help"])
    assert out.exit_code == 0
    assert "self-align" in out.output.lower()


def test_score_help_documents_emulate_flag():
    out = CliRunner().invoke(main, ["skill-verifier", "score", "--help"])
    assert out.exit_code == 0
    assert "--emulate-labels" in out.output
