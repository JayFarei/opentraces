from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from opentraces import capture


@pytest.fixture(autouse=True)
def _restore_registry():
    saved_parsers = dict(capture.PARSERS)
    saved_importers = dict(capture.IMPORTERS)
    saved_installers = dict(capture.HOOK_INSTALLERS)
    saved_resumers = dict(capture.RESUMERS)
    saved_registered = capture._registered
    try:
        yield
    finally:
        capture.PARSERS.clear()
        capture.PARSERS.update(saved_parsers)
        capture.IMPORTERS.clear()
        capture.IMPORTERS.update(saved_importers)
        capture.HOOK_INSTALLERS.clear()
        capture.HOOK_INSTALLERS.update(saved_installers)
        capture.RESUMERS.clear()
        capture.RESUMERS.update(saved_resumers)
        capture._registered = saved_registered


class StubParser:
    agent_name = "stub-agent"

    def discover_sessions(self, projects_path: Path) -> Iterator[Path]:
        return iter(())

    def parse_session(self, session_path: Path, byte_offset: int = 0):
        return None


def test_register_parser_is_idempotent_for_same_class():
    capture.register_parser(StubParser)
    capture.register_parser(StubParser)

    assert capture.get_parser("stub-agent") is StubParser


def test_register_parser_rejects_duplicate_agent_name():
    class OtherStubParser(StubParser):
        agent_name = "stub-agent"

    capture.register_parser(StubParser)

    with pytest.raises(ValueError, match="already registered"):
        capture.register_parser(OtherStubParser)


def test_non_claude_parser_can_register_and_discover_project_sessions(tmp_path):
    session_path = tmp_path / "rollout-2026.jsonl"

    class OtherAgentStubParser(StubParser):
        agent_name = "other-cli"

        def discover_project_sessions(self, project_dir: Path) -> Iterator[Path]:
            yield project_dir / session_path.name

        def session_id_from_path(self, session_path: Path) -> str:
            return f"codex:{session_path.stem}"

    capture.register_parser(OtherAgentStubParser)

    assert capture.discover_project_sessions(
        tmp_path,
        agent_names=["other-cli"],
    ) == [("other-cli", session_path)]
    assert capture.session_id_from_path("other-cli", session_path) == (
        "codex:rollout-2026"
    )


def test_default_registry_registration_is_idempotent():
    first = dict(capture.get_parsers())
    second = dict(capture.get_parsers())

    assert "claude-code" in first
    assert "codex-cli" in first
    assert first == second
