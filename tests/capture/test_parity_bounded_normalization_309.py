"""Issue #309 (A3 polish): bounded resolved-root substitution vs blanket rewriting.

The parity mechanism in ``opentraces.capture.parity._normalize`` normalizes
placement noise by *bounded substring substitution of resolved placement roots*
(the ``Path(raw).is_absolute()`` branch keyed on the declared replacements),
NOT by general path-shaped-string rewriting.

This adversarial control proves the distinction has teeth on a mirrored fixture
that carries, in each mode:

  * a legitimate placement-root path in a path-bearing field;
  * the same placement-root text embedded in explanatory prose; and
  * two *non-root* absolute paths embedded in prose whose only difference is
    semantic (the service name).

Bounded substitution collapses the placement path equally across persistent and
leased modes yet leaves the non-root prose byte-identical, so the two service
paths stay distinct. A deliberately blanket path-regex normalizer instead erases
that distinction (semantic collapse) — the ``blanket`` parametrization is a
strict-xfail, which is RED with blanket normalization and GREEN with the bounded
resolved-root substitution the implementation actually uses.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

import pytest

from opentraces.capture.parity import _normalize, _path_aliases

# The over-broad strategy the bounded implementation deliberately rejects: any
# absolute-path-shaped substring collapses to a single token.
_BLANKET_ABS_PATH = re.compile(r"/(?:[A-Za-z0-9._-]+)(?:/[A-Za-z0-9._-]+)*")


def _blanket_regex_normalize(
    value: Any,
    replacements: dict[str, str],
    *,
    domain: str = "semantic",
    path: tuple[str, ...] = (),
) -> Any:
    """Mirror ``_normalize``'s recursion but collapse *every* absolute path.

    This is the adversarial counterfactual — path-shaped-string rewriting rather
    than bounded resolved-root substitution.
    """

    if isinstance(value, dict):
        return {
            key: _blanket_regex_normalize(
                child, replacements, domain=domain, path=(*path, key)
            )
            for key, child in sorted(value.items())
        }
    if isinstance(value, list):
        return [
            _blanket_regex_normalize(child, replacements, domain=domain, path=(*path, "[]"))
            for child in value
        ]
    if isinstance(value, str):
        return _BLANKET_ABS_PATH.sub("<path>", value)
    return value


def _root_replacements(root: Path) -> dict[str, str]:
    """Build the resolved-placement-root replacements ``compare_placements`` uses."""

    replacements: dict[str, str] = {}
    for alias in _path_aliases(root):
        replacements[alias] = "<workspace>"
    replacements[root.resolve().name] = "<workspace-name>"
    return replacements


def _mirrored_fixture(root: Path) -> dict[str, Any]:
    """A trace-shaped mirror carrying a placement path plus non-root prose."""

    return {
        # A legitimate placement-root path in a path-bearing field.
        "placement_path": f"{root}/src/module.py",
        # The same placement-root text embedded in explanatory prose.
        "prose_root": (
            f"the observer wrote raw artifacts under {root}/out during capture"
        ),
        # Two NON-root absolute paths in prose; identical across modes, distinct
        # from each other only by the service segment.
        "prose_nonroot_a": (
            "the failing assertion originated from /opt/service-alpha/handler.py"
        ),
        "prose_nonroot_b": (
            "the failing assertion originated from /opt/service-beta/handler.py"
        ),
    }


@pytest.mark.parametrize(
    ("normalizer", "mode"),
    (
        (_normalize, "bounded"),
        pytest.param(
            _blanket_regex_normalize,
            "blanket",
            marks=pytest.mark.xfail(
                strict=True,
                reason=(
                    "#309: a blanket path-regex normalizer erases the non-root "
                    "prose distinction (semantic collapse)"
                ),
            ),
        ),
    ),
)
def test_bounded_resolved_root_substitution_preserves_nonroot_prose(
    normalizer: Callable[..., Any],
    mode: str,
    tmp_path: Path,
) -> None:
    persistent_root = tmp_path / "persistent" / "workspace-p"
    leased_root = tmp_path / "leased" / "workspace-l"
    persistent_root.mkdir(parents=True)
    leased_root.mkdir(parents=True)

    persistent_fixture = _mirrored_fixture(persistent_root)
    leased_fixture = _mirrored_fixture(leased_root)

    persistent_norm = normalizer(
        persistent_fixture, _root_replacements(persistent_root), domain="trace"
    )
    leased_norm = normalizer(
        leased_fixture, _root_replacements(leased_root), domain="trace"
    )

    # The intended placed path normalizes equally across persistent and leased
    # modes (both strategies collapse the declared root).
    assert persistent_norm["placement_path"] == leased_norm["placement_path"]
    assert persistent_norm["prose_root"] == leased_norm["prose_root"]

    # The discriminating property: two distinct non-root absolute paths in prose
    # stay distinct. A blanket path-regex normalizer erases this (xfail above).
    assert persistent_norm["prose_nonroot_a"] != persistent_norm["prose_nonroot_b"]
    assert leased_norm["prose_nonroot_a"] != leased_norm["prose_nonroot_b"]

    # ...and non-root prose is left byte-identical (semantically untouched).
    assert persistent_norm["prose_nonroot_a"] == persistent_fixture["prose_nonroot_a"]
    assert persistent_norm["prose_nonroot_b"] == persistent_fixture["prose_nonroot_b"]
