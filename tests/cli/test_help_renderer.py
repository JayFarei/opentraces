"""Tests for the gh-style sectioned root --help renderer (Step 14)."""

from __future__ import annotations

import re

from click.testing import CliRunner

from opentraces.cli import main


CORE_VERBS = ["add", "push", "pull", "list", "show", "status", "trail", "blame", "resume"]
INBOX_VERBS = [
    "reject", "reset", "redact", "discard", "llm-review", "export",
    "tui", "web", "stats", "log", "graph", "assess",
]
PROJECT_VERBS = ["init", "doctor", "remove"]
RESOURCE_VERBS = ["remote", "auth", "config", "setup", "completions"]

# Legacy verbs still registered at the root but NOT advertised in the
# new gh-style sections. Step 15 will remove them entirely.
LEGACY_HIDDEN_VERBS = [
    "commit", "login", "logout", "whoami", "review-llm", "upgrade",
    "projects", "trace",
]

SECTION_HEADERS_IN_ORDER = [
    "CORE COMMANDS",
    "INBOX COMMANDS",
    "PROJECT COMMANDS",
    "RESOURCE COMMANDS",
]


def _run_help() -> str:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"], color=False)
    assert result.exit_code == 0, result.output
    return result.output


def _strip_framing(output: str) -> str:
    """Keep only the section-listing portion of help output.

    Drops the ASCII banner, USAGE line, Options block, and any trailing
    FLAGS / EXAMPLES / LEARN MORE blocks so we can assert on visible
    command rows without false positives from usage strings or epilogs.
    """
    # Start at the first section header we emit.
    lo = output.find("CORE COMMANDS")
    if lo == -1:
        return output
    return output[lo:]


def test_help_exits_zero():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"], color=False)
    assert result.exit_code == 0


def test_ascii_banner_present_at_top():
    out = _run_help()
    # Banner is a multiline ASCII of "OT"; the "|\___" run is unique to it.
    banner_marker = "|\\___"
    assert banner_marker in out
    # Banner must precede the first section header.
    assert out.index(banner_marker) < out.index("CORE COMMANDS")


def test_section_headers_appear_in_order():
    out = _run_help()
    positions = [out.find(h) for h in SECTION_HEADERS_IN_ORDER]
    assert all(p >= 0 for p in positions), positions
    assert positions == sorted(positions), positions


def _section_block(output: str, header: str) -> str:
    """Return the substring from ``header`` up to the next section header."""
    start = output.find(header)
    assert start != -1, f"missing header {header!r}"
    remaining_headers = [
        h for h in SECTION_HEADERS_IN_ORDER
        if output.find(h) > start
    ]
    end = min(
        (output.find(h) for h in remaining_headers if output.find(h) > start),
        default=len(output),
    )
    return output[start:end]


def test_core_commands_block_contains_every_core_verb():
    out = _run_help()
    block = _section_block(out, "CORE COMMANDS")
    for verb in CORE_VERBS:
        # Match the verb as a whole word (row starts with "ot <verb>").
        assert re.search(rf"\b{re.escape(verb)}\b", block), (verb, block)


def test_inbox_commands_block_contains_every_inbox_verb():
    out = _run_help()
    block = _section_block(out, "INBOX COMMANDS")
    for verb in INBOX_VERBS:
        assert re.search(rf"\b{re.escape(verb)}\b", block), (verb, block)


def test_project_commands_block_contains_every_project_verb():
    out = _run_help()
    block = _section_block(out, "PROJECT COMMANDS")
    for verb in PROJECT_VERBS:
        assert re.search(rf"\b{re.escape(verb)}\b", block), (verb, block)


def test_resource_commands_block_contains_every_resource_verb():
    out = _run_help()
    block = _section_block(out, "RESOURCE COMMANDS")
    for verb in RESOURCE_VERBS:
        assert re.search(rf"\b{re.escape(verb)}\b", block), (verb, block)


def test_legacy_verbs_are_not_advertised():
    """Legacy verbs are still registered but must not appear in section blocks."""
    out = _run_help()
    advertised = _strip_framing(out)
    for verb in LEGACY_HIDDEN_VERBS:
        # Use a row-start pattern: "ot <verb> " (two-space right padding
        # from write_dl) or "ot <verb>" at line end. This avoids matching
        # the verb as a substring of another command's short-help text.
        row_pattern = rf"(?m)^\s*ot {re.escape(verb)}\b"
        assert not re.search(row_pattern, advertised), (
            f"legacy verb {verb!r} should not be advertised in root --help; "
            f"matched in:\n{advertised}"
        )
