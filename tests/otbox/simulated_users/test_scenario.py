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
