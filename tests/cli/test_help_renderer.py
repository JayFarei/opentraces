"""Tests for the gh-style sectioned root --help renderer (Step 14)."""

from __future__ import annotations

import re

import pytest
from click.testing import CliRunner

from opentraces.cli import main


GLOBAL_SETUP_VERBS = ["setup", "auth", "config", "completions"]
PROJECT_SETUP_VERBS = ["init", "status", "doctor", "remove", "watcher"]
TRACE_VERBS = ["trace"]
TRAIL_VERBS = ["trail"]
WORKFLOW_VERBS = ["workflow"]
DATASET_VERBS = ["dataset"]

# Legacy/internal verbs still registered at the root but NOT advertised in
# the journey-first sections.
LEGACY_HIDDEN_VERBS = [
    "commit", "login", "logout", "whoami", "review-llm", "upgrade",
    "projects",
]

SECTION_HEADERS_IN_ORDER = [
    "GLOBAL SETUP COMMANDS",
    "PROJECT SETUP COMMANDS",
    "TRACE COMMANDS",
    "TRAIL COMMANDS",
    "WORKFLOW COMMANDS",
    "DATASET COMMANDS",
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
    lo = output.find("GLOBAL SETUP COMMANDS")
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
    assert out.index(banner_marker) < out.index("GLOBAL SETUP COMMANDS")


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


def test_global_setup_block_contains_current_primary_verbs():
    out = _run_help()
    block = _section_block(out, "GLOBAL SETUP COMMANDS")
    for verb in GLOBAL_SETUP_VERBS:
        # Match the verb as a whole word (row starts with "ot <verb>").
        assert re.search(rf"\b{re.escape(verb)}\b", block), (verb, block)


def test_project_setup_block_contains_current_primary_verbs():
    out = _run_help()
    block = _section_block(out, "PROJECT SETUP COMMANDS")
    for verb in PROJECT_SETUP_VERBS:
        assert re.search(rf"\b{re.escape(verb)}\b", block), (verb, block)


def test_trace_block_contains_current_primary_verbs():
    out = _run_help()
    block = _section_block(out, "TRACE COMMANDS")
    for verb in TRACE_VERBS:
        assert re.search(rf"\b{re.escape(verb)}\b", block), (verb, block)


def test_trail_block_contains_current_primary_verbs():
    out = _run_help()
    block = _section_block(out, "TRAIL COMMANDS")
    for verb in TRAIL_VERBS:
        assert re.search(rf"\b{re.escape(verb)}\b", block), (verb, block)


def test_workflow_block_contains_current_primary_verbs():
    out = _run_help()
    block = _section_block(out, "WORKFLOW COMMANDS")
    for verb in WORKFLOW_VERBS:
        assert re.search(rf"\b{re.escape(verb)}\b", block), (verb, block)


def test_dataset_block_contains_current_primary_verbs():
    out = _run_help()
    block = _section_block(out, "DATASET COMMANDS")
    for verb in DATASET_VERBS:
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


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["dataset", "--help"], ["apply", "Create a local dataset", "publish", "review"]),
        (["dataset", "remote", "--help"], ["add", "Connect a local dataset", "create"]),
        (["dataset", "schedule", "--help"], ["add", "Add a local schedule", "pause"]),
        (["workflow", "--help"], ["create", "edit", "remove"]),
    ],
)
def test_plan057_058_groups_have_populated_command_help(args, expected):
    runner = CliRunner()
    result = runner.invoke(main, args, color=False)
    assert result.exit_code == 0, result.output
    for text in expected:
        assert text in result.output
