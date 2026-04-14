"""Unit tests for the GitButler-style ASCII graph renderer.

Covers:
- Every row of the fixed prefix table (11 rows).
- Every commit-dot color state (5 states).
- ANSI stripping and SHA/timestamp normalization.
- Single-trace stack grammar.
- Multi-trace stack grammar.
- Trace-primary mode.
- Terminal-width truncation of long subjects.
- Manifesto reference snapshot match (byte-identical after strip).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from opentraces.clients.text import graph_renderer as gr


REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFESTO_SNAPSHOT = REPO_ROOT / "tests" / "integration" / "snapshots" / "manifesto_reference.txt"


# --- Prefix table ---------------------------------------------------------- #

@pytest.mark.parametrize("key,expected", [
    ("stack_header", "┊╭┄"),
    ("stack_continue", "┊├┄"),
    ("commit_nonverbose", "┊●   "),
    ("commit_verbose", "┊● "),
    ("sub_line", "┊│     "),
    ("bare_trunk", "┊"),
    ("trunk_parallel", "┊┊"),
    ("stack_close", "├╯"),
    ("merge_base_cap", "┴ "),
    ("staged_group_header", "┊  ╭┄"),
    ("staged_file", "┊  │ "),
])
def test_prefix_table_row(key: str, expected: str) -> None:
    """All 11 prefix-table rows match the manifesto verbatim."""
    prefix, width = gr.PREFIX[key]
    assert prefix == expected
    assert width == len(expected)


# --- Commit dot colors ----------------------------------------------------- #

def _mk_commit(traces=None) -> gr.Commit:
    return gr.Commit(sha="a" * 40, short_sha="aaaaaaa", subject="x",
                     timestamp="2026-04-14T00:00:00Z", parents=[],
                     traces=traces or [])


def test_dot_no_attribution_default() -> None:
    c = _mk_commit(traces=[])
    glyph, code = gr._commit_dot(c, enabled=True)
    assert glyph == gr.DOT_SOLID
    assert code == ""


def test_dot_full_attribution_green_solid() -> None:
    c = _mk_commit(traces=[gr.TraceContribution("t", 10, attributed_ratio=1.0)])
    glyph, code = gr._commit_dot(c, enabled=True)
    assert glyph == gr.DOT_SOLID
    assert code == gr.ANSI_GREEN


def test_dot_partial_attribution_green_half() -> None:
    c = _mk_commit(traces=[gr.TraceContribution("t", 10, attributed_ratio=0.5)])
    glyph, code = gr._commit_dot(c, enabled=True)
    assert glyph == gr.DOT_HALF
    assert code == gr.ANSI_GREEN


def test_dot_pre_audit_only_yellow_solid() -> None:
    c = _mk_commit(traces=[gr.TraceContribution("t", 10, attributed_ratio=0.0)])
    glyph, code = gr._commit_dot(c, enabled=True)
    assert glyph == gr.DOT_SOLID
    assert code == gr.ANSI_YELLOW


def test_dot_heavy_missing_magenta() -> None:
    c = _mk_commit(traces=[gr.TraceContribution(
        "t", 10, attributed_ratio=0.1, missing_ratio=0.8)])
    glyph, code = gr._commit_dot(c, enabled=True)
    assert glyph == gr.DOT_SOLID
    assert code == gr.ANSI_MAGENTA


# --- ANSI stripping + normalization --------------------------------------- #

def test_strip_ansi_removes_csi() -> None:
    s = f"{gr.ANSI_GREEN}hello{gr.ANSI_RESET}"
    assert gr.strip_ansi(s) == "hello"


def test_normalize_replaces_shas_and_timestamps() -> None:
    s = "601614c add A at 2026-04-14T10:30:00Z"
    n = gr.normalize_for_snapshot(s)
    assert "{sha7}" in n
    assert "{timestamp}" in n
    assert "601614c" not in n


# --- Single-trace stack grammar ------------------------------------------- #

def test_single_trace_stack_grammar() -> None:
    c = gr.Commit(sha="a" * 40, short_sha="aaaaaaa",
                  subject="add thing", timestamp="2026-04-14T00:00:00Z",
                  parents=[],
                  traces=[gr.TraceContribution("traceid01", 5,
                                               lifecycle="final",
                                               attributed_ratio=1.0)])
    out = gr.render([c], gr.RenderOptions(color=False))
    out_no_ansi = gr.strip_ansi(out)
    lines = out_no_ansi.rstrip("\n").split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("┊╭┄")
    assert "[5 lines]" in lines[0]
    # Uniform lifecycle: suffix is suppressed.
    assert "<final>" not in lines[0]
    assert "<provisional>" not in lines[0]
    assert lines[1].startswith("┊●")
    # Date column is dropped.
    assert "2026-04-14" not in lines[1]
    assert "c:aaaaaaa" in lines[1]
    assert lines[2] == "├╯"


def test_lifecycle_mixed_renders_suffix() -> None:
    c = gr.Commit(sha="m" * 40, short_sha="mmmmmmm",
                  subject="mix", timestamp="2026-04-14T00:00:00Z",
                  parents=[],
                  traces=[
                      gr.TraceContribution("t1", 5, lifecycle="final",
                                           attributed_ratio=1.0),
                      gr.TraceContribution("t2", 3, lifecycle="provisional",
                                           attributed_ratio=1.0),
                  ])
    out = gr.strip_ansi(gr.render([c], gr.RenderOptions(color=False)))
    assert "<final>" in out
    assert "<provisional>" in out


def test_lifecycle_uniform_suppresses_suffix() -> None:
    c = gr.Commit(sha="u" * 40, short_sha="uuuuuuu",
                  subject="uniform", timestamp="2026-04-14T00:00:00Z",
                  parents=[],
                  traces=[
                      gr.TraceContribution("t1", 5, lifecycle="provisional",
                                           attributed_ratio=1.0),
                      gr.TraceContribution("t2", 3, lifecycle="provisional",
                                           attributed_ratio=1.0),
                  ])
    out = gr.strip_ansi(gr.render([c], gr.RenderOptions(color=False)))
    assert "<provisional>" not in out
    assert "<final>" not in out


# --- Multi-trace stack grammar -------------------------------------------- #

def test_multi_trace_stack_grammar() -> None:
    traces = [
        gr.TraceContribution("trace001", 10, lifecycle="final",
                             attributed_ratio=1.0),
        gr.TraceContribution("trace002", 5, lifecycle="provisional",
                             attributed_ratio=1.0),
        gr.TraceContribution("trace003", 3, lifecycle="final",
                             attributed_ratio=1.0),
    ]
    c = gr.Commit(sha="b" * 40, short_sha="bbbbbbb", subject="multi",
                  timestamp="2026-04-14T00:00:00Z", parents=[], traces=traces)
    out = gr.strip_ansi(gr.render([c], gr.RenderOptions(color=False)))
    lines = out.rstrip("\n").split("\n")
    # Expect: 3 trace headers + commit line + close = 5 lines.
    assert len(lines) == 5
    assert lines[0].startswith("┊╭┄")  # first: open
    assert lines[1].startswith("┊├┄")  # second: continue
    assert lines[2].startswith("┊├┄")
    assert lines[3].startswith("┊●")
    assert lines[4] == "├╯"


# --- Trace-primary mode --------------------------------------------------- #

def test_trace_primary_mode() -> None:
    pivot = "pivotabc"
    c1 = gr.Commit(sha="1" * 40, short_sha="1111111", subject="first",
                   timestamp="2026-04-14T00:00:00Z", parents=[],
                   traces=[gr.TraceContribution(pivot, 4, entity_count=2,
                                                lifecycle="final")])
    c2 = gr.Commit(sha="2" * 40, short_sha="2222222", subject="second",
                   timestamp="2026-04-14T00:00:01Z", parents=[],
                   traces=[gr.TraceContribution(pivot, 7, entity_count=1,
                                                lifecycle="final")])
    opts = gr.RenderOptions(color=False, mode="trace",
                            pivot_trace_id=pivot, show_entities=True)
    out = gr.strip_ansi(gr.render([c1, c2], opts))
    lines = out.rstrip("\n").split("\n")
    # header + 2 commit lines + close
    assert len(lines) == 4
    assert lines[0].startswith("┊╭┄")
    assert "pivotabc"[:8] in lines[0]
    assert lines[1].startswith("┊●")
    assert "[4 lines]" in lines[1]
    assert "{2 entities}" in lines[1]
    assert lines[2].startswith("┊●")
    assert "[7 lines]" in lines[2]
    assert lines[3] == "├╯"


# --- Width truncation ----------------------------------------------------- #

def test_long_subject_truncated_to_width() -> None:
    c = gr.Commit(sha="c" * 40, short_sha="ccccccc",
                  subject="x" * 200, timestamp="2026-04-14T00:00:00Z",
                  parents=[],
                  traces=[gr.TraceContribution("t", 1, lifecycle="final",
                                               attributed_ratio=1.0)])
    out = gr.strip_ansi(gr.render([c], gr.RenderOptions(width=40, color=False)))
    for line in out.splitlines():
        assert len(line) <= 40, f"line over budget: {len(line)} {line!r}"


# --- Manifesto reference snapshot ----------------------------------------- #

def test_manifesto_reference_byte_identical() -> None:
    """Acceptance test: the rendered reference block matches Appendix A
    character-for-character (after ANSI strip)."""
    rendered = gr.strip_ansi(gr.render_manifesto_reference())
    snapshot = MANIFESTO_SNAPSHOT.read_text()
    assert rendered == snapshot


def test_manifesto_reference_uses_only_alphabet() -> None:
    """No illegal glyphs (┘, ┐, ┼, diagonals) in the reference."""
    ref = gr.render_manifesto_reference()
    for bad in ("\u2518", "\u2510", "\u253C"):
        assert bad not in ref, f"illegal glyph {bad!r} in reference"


# --- Bare-trunk separator between stacks ---------------------------------- #

def test_bare_trunk_between_commits() -> None:
    traces = [gr.TraceContribution("t", 1, lifecycle="final",
                                   attributed_ratio=1.0)]
    c1 = gr.Commit(sha="1" * 40, short_sha="1111111", subject="a",
                   timestamp="2026-04-14T00:00:00Z", parents=[], traces=traces)
    c2 = gr.Commit(sha="2" * 40, short_sha="2222222", subject="b",
                   timestamp="2026-04-14T00:00:01Z", parents=[], traces=list(traces))
    out = gr.strip_ansi(gr.render([c1, c2], gr.RenderOptions(color=False)))
    # Between the two 3-line blocks there must be a bare ┊ line.
    lines = out.rstrip("\n").split("\n")
    assert lines[3] == "┊"
