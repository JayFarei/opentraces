"""otbox — snapshottable full test environment for opentraces.

A crabbox-modelled, opentraces-specific test environment. otbox seeds a
fully-populated opentraces world (initialized project, replayed traces,
live git hooks, built index, bound fake HF remote), snapshots it, and
lets any product user journey be torn down and restarted cheaply.

Not a shipped product surface — a dev/CI tool. Invoke via the repo-root
``otbox`` shim or ``python -m tests.otbox``.

See kb/plans/060-otbox-test-environment.md for the governing spec.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
