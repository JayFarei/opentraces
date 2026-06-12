"""Shared path-normalized envelope measurement (issue #51).

Both envelope-budget lanes import this module: the nightly otbox sweep
(``tests/otbox/test_envelope_budgets.py``) and the PR-lane sentinel
(``tests/cli/test_envelope_budget_sentinel.py``). Token measurement is
approx-tokens = stdout bytes / 4, but raw stdout embeds absolute
machine paths (e.g. ``search_snapshot.path`` and ``db_sizes.*.path``
in ``trace index status --json``), so identical envelopes measure
differently across checkouts/tmp roots (plan 095 U7 note). Collapsing
every world root to the constant ``<ROOT>`` before counting makes the
measurement path-invariant.

Callers pass BOTH the raw and ``Path(...).resolve()`` forms of each
root (macOS ``/var`` vs ``/private/var``). JSON output never escapes
``/``, so plain string replacement is correct for ``--json`` stdout.
"""

from __future__ import annotations

from typing import Sequence


def normalize_paths(text: str, roots: Sequence[str]) -> str:
    """Collapse every occurrence of each root path to ``<ROOT>``.

    Roots are applied longest-first so nested roots don't leave
    partial-path residue (replacing a short prefix first would split a
    longer root into ``<ROOT>/suffix`` before the longer root could
    match).
    """
    for root in sorted({r for r in roots if r}, key=len, reverse=True):
        text = text.replace(root, "<ROOT>")
    return text


def approx_tokens(text: str, roots: Sequence[str] = ()) -> int:
    """Approximate token count of ``text`` after path normalization."""
    return len(normalize_paths(text or "", roots)) // 4
