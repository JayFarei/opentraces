"""Issue #310: the A3 two-proof split must be discoverable where a maintainer reads.

Real-fixture compatibility and substantive Trail parity are proven by two
separate controls:

  * ``test_persistent_capture_preserves_literal_legacy_bytes_on_committed_real_fixture``
    proves literal real-fixture byte compatibility and honestly exercises the
    unreachable-tree / ``unproven`` Trail branch (committed fixture trees are
    unreachable in a fresh repository);
  * ``test_placement_acceptance_exercises_every_capture_view_and_preserves_asymmetry``
    proves substantive ``trail_matches`` parity from a harness-constructed
    ``write_worktree_tree`` relationship.

Both halves exist and are correct, but the split is undiscoverable unless it is
named where a maintainer reads. This is a documentation-scoped control: it reads
ONLY the real-fixture test's adjacent prose and ``capture/README.md`` and fails
until both name both proof halves and point to the separate substantive
Trail-parity control. It asserts nothing about capture, compatibility, or parity
runtime behavior.
"""

from __future__ import annotations

import ast
from pathlib import Path

TEST_FILE = Path(__file__).with_name("test_portable_capture.py")
README = Path(__file__).parents[2] / "src" / "opentraces" / "capture" / "README.md"

REAL_FIXTURE_TEST = (
    "test_persistent_capture_preserves_literal_legacy_bytes_on_committed_real_fixture"
)
SUBSTANTIVE_PARITY_TEST = (
    "test_placement_acceptance_exercises_every_capture_view_and_preserves_asymmetry"
)


def _adjacent_prose() -> str:
    """Return only the comment block + docstring adjacent to the real-fixture test."""

    source = TEST_FILE.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == REAL_FIXTURE_TEST
    )
    docstring = ast.get_docstring(node) or ""
    def_idx = node.lineno - 1  # 0-based line index of the ``def`` line
    comments: list[str] = []
    cursor = def_idx - 1
    while cursor >= 0 and lines[cursor].strip().startswith("#"):
        comments.append(lines[cursor])
        cursor -= 1
    return "\n".join(reversed(comments)) + "\n" + docstring


def test_real_fixture_prose_names_both_proof_halves() -> None:
    prose = _adjacent_prose()
    assert "literal legacy bytes" in prose, prose
    assert "unproven" in prose, prose
    assert "write_worktree_tree" in prose, prose
    assert SUBSTANTIVE_PARITY_TEST in prose, prose


def test_capture_readme_states_the_proof_split_and_points_to_both_tests() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "literal legacy bytes" in readme, "README must name the literal-bytes half"
    assert "unproven" in readme, "README must name the honest unproven Trail branch"
    assert REAL_FIXTURE_TEST in readme, "README must point to the real-fixture test"
    assert SUBSTANTIVE_PARITY_TEST in readme, "README must point to the parity test"
