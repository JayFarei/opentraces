"""Tests for the scenario TOML parser + validator (plan 071, R2).

Covers the five contract points called out in the agent-B
delivery spec: round-trip load of the bundled echo-meta scenario,
unknown-name failure, empty-turns rejection, template resolution,
and digest stability.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.otbox.simulated_users.scenario import (
    SCENARIOS_DIR,
    TEMPLATES_DIR,
    Scenario,
    ScenarioError,
    Turn,
    available_scenarios,
    load_scenario,
    scenario_digest,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _write_scenario(tmp_path: Path, name: str, body: str) -> Path:
    """Write ``body`` to ``SCENARIOS_DIR/<name>.toml`` for the duration
    of one test, returning the path so the test can also clean it up.

    We write directly into SCENARIOS_DIR (not tmp_path) because
    ``load_scenario(name)`` resolves against the package's scenarios
    dir by name. The test cleans the file up in a finally block.
    """
    path = SCENARIOS_DIR / f"{name}.toml"
    path.write_text(textwrap.dedent(body).lstrip())
    return path


# ---------------------------------------------------------------------------
# contract tests
# ---------------------------------------------------------------------------
def test_load_scenario_echo_meta() -> None:
    """The bundled echo-meta scenario loads and every field is populated."""
    scenario = load_scenario("echo-meta")
    assert isinstance(scenario, Scenario)
    assert scenario.name == "echo-meta"
    assert scenario.agent == "echo"
    assert scenario.binary_name == "_echo_binary.py"
    assert scenario.description  # non-empty
    assert scenario.source_path == SCENARIOS_DIR / "echo-meta.toml"

    # Initial state template resolved to a real directory.
    assert scenario.initial_state.template_name == "single-file-python-project"
    assert scenario.initial_state.template_dir is not None
    assert scenario.initial_state.template_dir.is_dir()
    assert (scenario.initial_state.template_dir / "src" / "app.py").is_file()

    # Turns parsed into Turn dataclasses with prompt + expect_regex set.
    assert len(scenario.turns) == 3
    for turn in scenario.turns:
        assert isinstance(turn, Turn)
        assert turn.prompt.strip()
        assert turn.expect_regex.strip()
        assert turn.timeout_s > 0

    # Capture spec.
    assert scenario.capture.artifact_dir_name == "echo-meta"
    assert "src/app.py" in scenario.capture.expected_paths


def test_load_scenario_unknown_name_raises() -> None:
    with pytest.raises(ScenarioError) as excinfo:
        load_scenario("does-not-exist")
    msg = str(excinfo.value)
    assert "does-not-exist" in msg


def test_validator_rejects_empty_turns() -> None:
    """A scenario with an empty [[turns]] list must fail validation
    with an error message that names the offending key."""
    body = """
        name = "empty-turns-meta"
        description = "test fixture — must be rejected"
        agent = "echo"
        binary_name = "_echo_binary.py"

        turns = []

        [capture]
        artifact_dir = "empty-turns-meta"
        expected_paths = []
    """
    path = _write_scenario(Path.cwd(), "empty-turns-meta", body)
    try:
        with pytest.raises(ScenarioError) as excinfo:
            load_scenario("empty-turns-meta")
        assert "turns" in str(excinfo.value).lower()
    finally:
        path.unlink(missing_ok=True)


def test_template_resolves_to_real_dir() -> None:
    """The bundled single-file-python-project template ships with the
    files the scenarios reference, so the resolution path is real."""
    template_dir = TEMPLATES_DIR / "single-file-python-project"
    assert template_dir.is_dir()
    app = template_dir / "src" / "app.py"
    assert app.is_file()
    contents = app.read_text()
    assert "def greet" in contents
    assert (template_dir / "README.md").is_file()


def test_scenario_digest_stable() -> None:
    """Digest is stable across loads of the same TOML and changes the
    moment the underlying bytes change."""
    first = load_scenario("echo-meta")
    second = load_scenario("echo-meta")
    digest_a = scenario_digest(first)
    digest_b = scenario_digest(second)
    assert digest_a == digest_b
    assert len(digest_a) == 64  # sha256 hex

    # Mutate the TOML bytes through a sibling fixture (don't touch
    # the canonical echo-meta) and confirm a different digest.
    body = """
        name = "digest-fixture"
        description = "stability fixture"
        agent = "echo"
        binary_name = "_echo_binary.py"

        [initial_state]
        template = "single-file-python-project"

        [[turns]]
        prompt = "first prompt"
        expect_regex = "(?i)hello"
        timeout_s = 5

        [capture]
        artifact_dir = "digest-fixture"
        expected_paths = []
    """
    path = _write_scenario(Path.cwd(), "digest-fixture", body)
    try:
        fixture_first = load_scenario("digest-fixture")
        digest_first = scenario_digest(fixture_first)

        path.write_text(path.read_text() + "\n# trailing change\n")
        fixture_second = load_scenario("digest-fixture")
        digest_second = scenario_digest(fixture_second)
        assert digest_first != digest_second
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# small extras — catalogue + agent validation, since they're cheap and
# guard the surfaces Agent C will consume.
# ---------------------------------------------------------------------------
def test_available_scenarios_includes_echo_meta() -> None:
    """available_scenarios() returns a summary list with echo-meta in it."""
    entries = {entry["name"]: entry for entry in available_scenarios()}
    assert "echo-meta" in entries
    summary = entries["echo-meta"]
    assert summary["agent"] == "echo"
    assert summary["binary_name"] == "_echo_binary.py"
    assert summary["turn_count"] == 3
    # add-helper-function ships as a stub but should also appear.
    assert "add-helper-function" in entries


def test_validator_rejects_invalid_agent() -> None:
    body = """
        name = "bad-agent-meta"
        description = "test fixture"
        agent = "not-a-real-agent"
        binary_name = "whatever"

        [[turns]]
        prompt = "hello"
        expect_regex = "(?i)hi"
        timeout_s = 5

        [capture]
        artifact_dir = "bad-agent-meta"
        expected_paths = []
    """
    path = _write_scenario(Path.cwd(), "bad-agent-meta", body)
    try:
        with pytest.raises(ScenarioError) as excinfo:
            load_scenario("bad-agent-meta")
        assert "agent" in str(excinfo.value).lower()
    finally:
        path.unlink(missing_ok=True)


def test_validator_rejects_unknown_template() -> None:
    body = """
        name = "bad-template-meta"
        description = "test fixture"
        agent = "echo"
        binary_name = "_echo_binary.py"

        [initial_state]
        template = "does-not-exist-template"

        [[turns]]
        prompt = "hello"
        expect_regex = "(?i)hi"
        timeout_s = 5

        [capture]
        artifact_dir = "bad-template-meta"
        expected_paths = []
    """
    path = _write_scenario(Path.cwd(), "bad-template-meta", body)
    try:
        with pytest.raises(ScenarioError) as excinfo:
            load_scenario("bad-template-meta")
        assert "template" in str(excinfo.value).lower()
    finally:
        path.unlink(missing_ok=True)


def test_validator_rejects_turn_missing_prompt() -> None:
    body = """
        name = "no-prompt-meta"
        description = "test fixture"
        agent = "echo"
        binary_name = "_echo_binary.py"

        [[turns]]
        expect_regex = "(?i)hi"
        timeout_s = 5

        [capture]
        artifact_dir = "no-prompt-meta"
        expected_paths = []
    """
    path = _write_scenario(Path.cwd(), "no-prompt-meta", body)
    try:
        with pytest.raises(ScenarioError) as excinfo:
            load_scenario("no-prompt-meta")
        assert "prompt" in str(excinfo.value).lower()
    finally:
        path.unlink(missing_ok=True)


def test_validator_rejects_capture_missing_artifact_dir() -> None:
    body = """
        name = "no-artifact-dir-meta"
        description = "test fixture"
        agent = "echo"
        binary_name = "_echo_binary.py"

        [[turns]]
        prompt = "hello"
        expect_regex = "(?i)hi"
        timeout_s = 5

        [capture]
        expected_paths = []
    """
    path = _write_scenario(Path.cwd(), "no-artifact-dir-meta", body)
    try:
        with pytest.raises(ScenarioError) as excinfo:
            load_scenario("no-artifact-dir-meta")
        assert "artifact_dir" in str(excinfo.value).lower()
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# issue #49 — per-turn git-state verification + contract floors
# ---------------------------------------------------------------------------
def test_turn_parses_verify_command_and_regex() -> None:
    """A [[turns]] entry may carry a declarative post-turn verification:
    a ``verify_command`` (argv list) executed in the box AND a
    ``verify_regex`` its combined output must match."""
    body = """
        name = "verify-cmd-meta"
        description = "test fixture"
        agent = "echo"
        binary_name = "_echo_binary.py"

        [[turns]]
        prompt = "commit it"
        expect_regex = "(?i)committed"
        timeout_s = 5
        verify_command = ["git", "log", "-1", "--format=%H"]
        verify_regex = "[0-9a-f]{7,}"

        [capture]
        artifact_dir = "verify-cmd-meta"
        expected_paths = []
    """
    path = _write_scenario(Path.cwd(), "verify-cmd-meta", body)
    try:
        scenario = load_scenario("verify-cmd-meta")
        turn = scenario.turns[0]
        assert turn.verify_command == ["git", "log", "-1", "--format=%H"]
        assert turn.verify_regex == "[0-9a-f]{7,}"
    finally:
        path.unlink(missing_ok=True)


def test_turn_parses_expect_revert_commit() -> None:
    """``expect_revert_commit = true`` is a structured assertion that a
    commit on HEAD carries the ``This reverts commit `` body marker."""
    body = """
        name = "revert-meta"
        description = "test fixture"
        agent = "echo"
        binary_name = "_echo_binary.py"

        [[turns]]
        prompt = "revert it"
        expect_regex = "(?i)revert"
        timeout_s = 5
        expect_revert_commit = true

        [capture]
        artifact_dir = "revert-meta"
        expected_paths = []
    """
    path = _write_scenario(Path.cwd(), "revert-meta", body)
    try:
        scenario = load_scenario("revert-meta")
        assert scenario.turns[0].expect_revert_commit is True
    finally:
        path.unlink(missing_ok=True)


def test_turn_parses_fresh_session_flag() -> None:
    """``fresh_session = true`` requests a fresh agent invocation for the
    turn so the turn lands its own session JSONL (its own trace)."""
    body = """
        name = "fresh-session-meta"
        description = "test fixture"
        agent = "echo"
        binary_name = "_echo_binary.py"

        [[turns]]
        prompt = "first"
        expect_regex = "(?i)ok"
        timeout_s = 5

        [[turns]]
        prompt = "second"
        expect_regex = "(?i)ok"
        timeout_s = 5
        fresh_session = true

        [capture]
        artifact_dir = "fresh-session-meta"
        expected_paths = []
    """
    path = _write_scenario(Path.cwd(), "fresh-session-meta", body)
    try:
        scenario = load_scenario("fresh-session-meta")
        assert scenario.turns[0].fresh_session is False
        assert scenario.turns[1].fresh_session is True
    finally:
        path.unlink(missing_ok=True)


def test_echo_verify_scenario_carries_turn_contracts() -> None:
    """The committed echo-verify meta scenario pins the issue #49 turn
    contract on the default-CI echo lane: per-turn verify assertions plus
    a fresh_session restart turn."""
    scenario = load_scenario("echo-verify")
    assert scenario.agent == "echo"
    assert scenario.turns[0].verify_command == ["cat", "src/app.py"]
    assert scenario.turns[0].verify_regex == "def greet"
    assert scenario.turns[1].fresh_session is True


def test_turn_verify_defaults_are_none() -> None:
    """A turn without verification keys leaves the new fields unset so
    legacy scenarios keep their TUI-regex-only behavior."""
    scenario = load_scenario("echo-meta")
    for turn in scenario.turns:
        assert turn.verify_command is None
        assert turn.verify_regex is None
        assert turn.expect_revert_commit is False
        assert turn.fresh_session is False


def test_capture_parses_contract_floors() -> None:
    """[capture] may declare quantitative pre-snapshot contract floors:
    min_traces, require_security_fingerprints, require_revert_commit,
    plus a security-tool enable list."""
    body = """
        name = "floors-meta"
        description = "test fixture"
        agent = "echo"
        binary_name = "_echo_binary.py"

        [[turns]]
        prompt = "go"
        expect_regex = "(?i)ok"
        timeout_s = 5

        [capture]
        artifact_dir = "floors-meta"
        expected_paths = []
        min_traces = 3
        require_security_fingerprints = true
        require_revert_commit = true
        enable_security_tools = ["regex", "entropy"]
    """
    path = _write_scenario(Path.cwd(), "floors-meta", body)
    try:
        scenario = load_scenario("floors-meta")
        assert scenario.capture.min_traces == 3
        assert scenario.capture.require_security_fingerprints is True
        assert scenario.capture.require_revert_commit is True
        assert scenario.capture.enable_security_tools == ["regex", "entropy"]
    finally:
        path.unlink(missing_ok=True)


def test_capture_floor_defaults() -> None:
    """[capture] floors default to the permissive baseline (min_traces=1,
    no security/revert requirement, no security tools enabled)."""
    scenario = load_scenario("echo-meta")
    assert scenario.capture.min_traces == 1
    assert scenario.capture.require_security_fingerprints is False
    assert scenario.capture.require_revert_commit is False
    assert scenario.capture.enable_security_tools == []


def test_claude_scenarios_declare_issue49_contracts() -> None:
    """The five claude-* scenarios carry the hardened contracts issue #49
    requires: per-turn commit verification on commit-claiming turns, the
    revert marker on with-revert, the trace floors on multi-skill /
    pr-branch, and the security-tool enable on with-secrets."""
    # with-revert: turn 1 must assert the literal revert marker.
    revert = load_scenario("claude-with-revert")
    assert any(t.expect_revert_commit for t in revert.turns), (
        "claude-with-revert must assert a revert commit on some turn"
    )
    assert revert.capture.require_revert_commit is True

    # multi-skill: >= 3 traces via fresh sessions per turn.
    multi = load_scenario("claude-multi-skill")
    assert multi.capture.min_traces >= 3
    assert sum(1 for t in multi.turns if t.fresh_session) >= 2, (
        "multi-skill needs fresh-session turns to land >= 3 traces"
    )

    # pr-branch: >= 3 traces, >= 2 branch commits.
    pr = load_scenario("claude-pr-branch")
    assert pr.capture.min_traces >= 3

    # with-secrets: enables the security tools so fingerprints exist.
    secrets = load_scenario("claude-with-secrets")
    assert "regex" in secrets.capture.enable_security_tools
    assert "entropy" in secrets.capture.enable_security_tools
    assert secrets.capture.require_security_fingerprints is True

    # linear-edit: at least one turn carries a commit verification.
    linear = load_scenario("claude-linear-edit")
    assert any(
        t.verify_command and t.verify_regex for t in linear.turns
    ), "claude-linear-edit must verify a commit landed"
