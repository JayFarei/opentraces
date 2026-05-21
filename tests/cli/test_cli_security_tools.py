"""CLI tests for the ``opentraces security`` group.

Covers ``security sanitize`` (text + row payloads), ``security tools list``,
and ``security tools info <name>``. The CLI is a thin wrapper around the
public ``sanitize_*`` API; these tests exercise the wire encoding (stdin
JSON → stdout JSON) and basic option handling.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.cli.security import security_group


def _run(args: list[str], stdin: str = "") -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(security_group, args, input=stdin)
    return result.exit_code, result.output


class TestSanitize:
    def test_text_path_with_explicit_tools(self) -> None:
        code, out = _run(
            ["sanitize", "--tools", "regex", "--field-type", "general"],
            stdin=json.dumps({"text": "leak AKIAIOSFODNN7EXAMPLE here"}),
        )
        assert code == 0, out
        payload = json.loads(out)
        assert payload["sanitized"] == "leak [REDACTED] here"
        assert any(f["pattern"] == "aws_access_key" for f in payload["findings"])

    def test_row_path_with_explicit_tools(self) -> None:
        code, out = _run(
            ["sanitize", "--tools", "regex,entropy"],
            stdin=json.dumps({
                "row": {"top": "x", "nested": {"key": "AKIAIOSFODNN7EXAMPLE"}}
            }),
        )
        assert code == 0, out
        payload = json.loads(out)
        assert payload["sanitized"]["nested"]["key"] == "[REDACTED]"
        report = payload["report"]
        assert "regex" in report["tools_applied"]
        assert "entropy" in report["tools_applied"]
        assert report["redactions_applied"] >= 1

    def test_missing_resolution_flag_errors(self) -> None:
        code, out = _run(
            ["sanitize"],
            stdin=json.dumps({"text": "x"}),
        )
        assert code != 0
        assert "--tools" in out or "--use-config" in out

    def test_both_flags_errors(self) -> None:
        code, out = _run(
            ["sanitize", "--tools", "regex", "--use-config"],
            stdin=json.dumps({"text": "x"}),
        )
        assert code != 0
        assert "not both" in out

    def test_invalid_json_errors_cleanly(self) -> None:
        code, out = _run(["sanitize", "--tools", "regex"], stdin="{not json")
        assert code != 0
        assert "invalid JSON" in out or "JSON" in out

    def test_empty_stdin_errors_cleanly(self) -> None:
        code, out = _run(["sanitize", "--tools", "regex"], stdin="")
        assert code != 0
        assert "empty" in out or "expected JSON" in out


class TestToolsList:
    def test_human_readable_lists_every_tool(self) -> None:
        code, out = _run(["tools", "list"])
        assert code == 0, out
        for name in ("regex", "entropy", "trufflehog", "privacy_filter",
                     "llm_pii", "path_anonymizer", "classifier"):
            assert name in out

    def test_json_output_shape(self) -> None:
        code, out = _run(["tools", "list", "--json"])
        assert code == 0, out
        payload = json.loads(out)
        assert isinstance(payload, list)
        assert len(payload) == 7
        for entry in payload:
            assert set(entry.keys()) >= {"name", "display_name", "kind", "enabled", "state"}


class TestToolsInfo:
    def test_known_tool(self) -> None:
        code, out = _run(["tools", "info", "regex"])
        assert code == 0, out
        assert "Regex patterns" in out
        assert "kind:" in out

    def test_unknown_tool(self) -> None:
        code, out = _run(["tools", "info", "not_a_tool"])
        assert code != 0
        assert "unknown" in out

    def test_json_output(self) -> None:
        code, out = _run(["tools", "info", "regex", "--json"])
        assert code == 0, out
        payload = json.loads(out)
        assert payload["name"] == "regex"
        assert payload["kind"] == "detector"
        assert payload["enabled"] is False

    def test_llm_pii_does_not_advertise_missing_setup_command(self) -> None:
        code, out = _run(["tools", "info", "llm_pii", "--json"])
        assert code == 0, out
        payload = json.loads(out)
        assert payload["setup_cmd"] is None
        assert payload["disable_cmd"] is None
        assert "advanced" in payload["detail"]


def test_security_group_uses_canonical_help_renderer() -> None:
    result = CliRunner().invoke(main, ["security", "--help"], color=False)
    assert result.exit_code == 0, result.output
    assert "opentraces security" in result.output
    assert "Commands" in result.output
