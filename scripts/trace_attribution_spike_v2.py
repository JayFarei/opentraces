#!/usr/bin/env python3
"""Backward-compat shim for the attribution spike (plan 043 phase 0).

The real module lives at `opentraces.enrichment.git.attribution`. This file
stays in `scripts/` because `scripts/attribution_v2_harness.py`,
`scripts/attribution_v2_selftest.py`, and `scripts/backfill_evaluator.py`
locate the spike via `SPIKE = Path(__file__).parent / "trace_attribution_spike_v2.py"`
and invoke it as a subprocess. Those invocations keep working unchanged.

New code should import directly:

    from opentraces.enrichment.git import attribution
    from opentraces.enrichment.git.attribution import (
        Snapshot, build_audit_history, attribute_commit,
    )
"""
from __future__ import annotations

import sys

from opentraces.enrichment.git.attribution import main

if __name__ == "__main__":
    sys.exit(main())
