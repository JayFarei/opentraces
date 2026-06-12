"""Host agent-session env vars must never leak into a box (issue #49 regen).

A claude TUI inheriting ``CLAUDE_CODE_SESSION_ID`` / ``CLAUDE_CODE_CHILD_SESSION``
treats itself as a child session and skips conversation persistence to
``~/.claude/projects`` (only ai-title + hook events land). When the capture
regen ran from inside a Claude Code session (2026-06-12), every scenario
captured 0 traces and the contract floors refused the artifacts. These tests
pin the two-layer fix: ``isolated_env`` scrubs the family, and
``_build_env_prefix`` emits ``env -u`` flags for every key isolated_env
removed (which also covers the HF-credential strips on the spawn path).
"""
from __future__ import annotations

from tests.otbox.env import Box, isolated_env
from tests.otbox.simulated_users.prep import _build_env_prefix


def _fake_box(tmp_path):
    box = Box(box_id="otb_envscrub")
    # Box paths derive from BOXES_DIR; for these tests only home/fake_remote
    # path VALUES matter, not their existence.
    return box


def test_isolated_env_scrubs_host_agent_vars(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "deadbeef")
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "true")
    monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
    box = _fake_box(tmp_path)

    env = isolated_env(box)

    assert "CLAUDECODE" not in env
    assert "CLAUDE_CODE_SESSION_ID" not in env
    assert "CLAUDE_CODE_CHILD_SESSION" not in env
    assert "CODEX_SANDBOX" not in env


def test_isolated_env_extra_wins_over_scrub(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENABLE_TELEMETRY", "0")
    box = _fake_box(tmp_path)

    env = isolated_env(box, {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"})

    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"


def test_build_env_prefix_unsets_removed_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "true")
    monkeypatch.setenv("HF_TOKEN", "hf_secret")
    box = _fake_box(tmp_path)

    argv = _build_env_prefix(box, None)

    assert argv[0] == "env"
    unset_pairs = {
        argv[i + 1] for i, tok in enumerate(argv) if tok == "-u"
    }
    assert "CLAUDE_CODE_CHILD_SESSION" in unset_pairs
    assert "HF_TOKEN" in unset_pairs
    # Pins still present and well-formed after the -u block.
    assert any(tok.startswith("HOME=") for tok in argv)
