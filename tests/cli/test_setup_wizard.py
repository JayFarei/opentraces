"""Setup wizard prompt-sequence tests (issue #66).

The bucket security-policy step is the wizard's single interactive security
choice. The old standalone step-6 (trufflehog) and step-7 (llm-review)
questions are gone; only their read-only STATUS lines remain, plus one-line
pointers in the closing "Next steps" panel. If ``strict`` is chosen and the
TruffleHog binary is missing, the existing install flow runs inline at the
policy step.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import opentraces.cli as _cli
from opentraces.cli import main


class _FakeEntityRunner:
    def __init__(self, binary_path=None):
        self.binary_path = binary_path

    def available(self) -> bool:
        return True


@pytest.fixture
def wizard_env(monkeypatch):
    """Stub the wizard's heavyweight integrations so prompts are deterministic.

    Hooks/watcher/entity-parser report installed (no prompts), HuggingFace is
    authenticated, the bucket is unconfigured (so the sync + policy prompts
    fire), and the terminal is interactive.
    """
    cfg = _cli.load_config()
    cfg.hf_token = "hf_dummy_token"
    monkeypatch.setattr(_cli, "load_config", lambda: cfg)
    monkeypatch.setattr(_cli, "save_config", lambda _cfg: None)
    monkeypatch.setattr(_cli, "_auth_identity", lambda _tok: {"name": "tester"})
    monkeypatch.setattr(_cli, "_is_interactive_terminal", lambda: True)

    import opentraces.capture as _capture
    monkeypatch.setattr(_capture, "get_hook_installers", lambda: {})

    import opentraces.watcher.installer as _winst
    monkeypatch.setattr(_winst, "status", lambda: SimpleNamespace(installed=True))

    import opentraces.enrichment.entities as _entities
    import opentraces.enrichment.entities.runner as _entrunner
    monkeypatch.setattr(_entities, "EntityRunner", _FakeEntityRunner)
    monkeypatch.setattr(_entrunner, "resolve_binary_path", lambda: None)

    import opentraces.security.trufflehog as _th
    calls = {"install": 0, "installed": False}

    def fake_find_trufflehog():
        return "trufflehog 3.0.0-test" if calls["installed"] else None

    def fake_install_binary():
        calls["install"] += 1
        calls["installed"] = True
        return True, "test-installer"

    monkeypatch.setattr(_th, "find_trufflehog", fake_find_trufflehog)
    monkeypatch.setattr(_th, "install_binary", fake_install_binary)
    return SimpleNamespace(cfg=cfg, trufflehog_calls=calls)


def _run_wizard(answers: list[str]):
    runner = CliRunner()
    return runner.invoke(main, ["setup"], input="".join(a + "\n" for a in answers))


def test_wizard_has_no_standalone_trufflehog_or_llm_review_prompts(wizard_env):
    # tracking=yes, bucket sync=yes, policy=1 (recommended) — and NOTHING else:
    # the wizard must complete without the old step-6/step-7 questions.
    result = _run_wizard(["y", "y", "1"])
    assert result.exit_code == 0, result.output

    # The policy step remains the single interactive security choice.
    assert "choose bucket security policy" in result.output

    # The standalone questions are gone.
    assert "enable the optional TruffleHog secret detector globally?" not in result.output
    assert "enable optional LLM dataset-row review globally?" not in result.output

    # The status LINES remain (current enabled/disabled state display).
    assert "trufflehog" in result.output
    assert "llm-review" in result.output

    # Recommended policy does not pull in TruffleHog, so no inline install.
    assert wizard_env.trufflehog_calls["install"] == 0

    # Next steps panel points at the optional setup commands.
    assert "opentraces setup trufflehog" in result.output
    assert "opentraces setup llm-review" in result.output


def test_wizard_strict_policy_installs_missing_trufflehog_inline(wizard_env):
    # tracking=yes, bucket sync=yes, policy=3 (strict). The binary is missing,
    # so the existing install flow runs inline at the policy step.
    result = _run_wizard(["y", "y", "3"])
    assert result.exit_code == 0, result.output

    assert wizard_env.trufflehog_calls["install"] == 1
    assert "test-installer" in result.output
    # Still no standalone question.
    assert "enable the optional TruffleHog secret detector globally?" not in result.output
