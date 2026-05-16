"""Captured agent-session snapshot artifacts (plan 071).

Each subdirectory of this package holds a ``snapshot.tar.gz`` +
``metadata.json`` pair produced by ``./otbox capture-refresh
--scenario <name>``. Plan 072's checkpoints restore from those
artifacts when present, and fall back to the synthetic harness chain
when they are not (default-CI safe).

This module is intentionally empty — it only exists so the directory
is tracked by Python as a package and ships in source distributions
even when no captures have been committed yet.
"""
