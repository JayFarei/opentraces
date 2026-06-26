"""Deterministic fault-injection seam (otbox 2.0 phase 6).

Named sites in load-bearing product paths where the otbox mutation lane can
deterministically break a guarantee to prove its guard journeys fail. This
is NOT a chaos tool: a faultpoint is armed only when BOTH environment
variables are set, so production and ordinary test runs can never trip one:

    OT_FAULTPOINT=<site-name>   which single site to arm
    OT_OTBOX_FAULT_OK=1         explicit acknowledgement gate

``opentraces doctor`` reports any armed faultpoint; the release gate greps
for the variables in CI configs. Sites are stable names — the otbox
guarantees.toml references them, so renaming one is a reviewed contract
change.
"""

from __future__ import annotations

import os

# Stable site names. Add sites sparingly; each must be referenced by at
# least one guarantees.toml kill or it is dead weight.
SITES = frozenset(
    {
        "maturation-returns-unknown",   # anchor maturation degrades to unknown
        "schema-compare-inverted",      # remote-schema version comparison flips
        "redaction-skipped",            # sanitize_record returns input unchanged
        # Trace Slicer Library (issue #141) — the three red-capability mutants
        # the conformance journey must catch (loopcraft step 5).
        "slicer-boundary-off-by-one",   # one slicer boundary shifted by +1
        "slicer-drop-continuation",     # S1 control/continuation heuristic disabled
        "slicer-self-report-valid-gap", # envelope drops a trajectory but claims valid
    }
)


def armed(site: str) -> bool:
    """True when ``site`` is the single armed faultpoint.

    Misuse is loud: arming an unknown site raises immediately rather than
    silently never firing.
    """
    target = os.environ.get("OT_FAULTPOINT")
    if not target:
        return False
    if os.environ.get("OT_OTBOX_FAULT_OK") != "1":
        return False
    if target not in SITES:
        raise RuntimeError(
            f"OT_FAULTPOINT={target!r} is not a registered faultpoint site "
            f"(known: {sorted(SITES)})"
        )
    return target == site


def armed_site() -> str | None:
    """The currently armed site name, for doctor reporting (None = safe)."""
    target = os.environ.get("OT_FAULTPOINT")
    if target and os.environ.get("OT_OTBOX_FAULT_OK") == "1" and target in SITES:
        return target
    return None
