#!/usr/bin/env python3
"""Codex CLI SessionStart hook for OpenTraces."""

from __future__ import annotations

from ._common import record_lifecycle_event


def main() -> None:
    record_lifecycle_event("SessionStart")


if __name__ == "__main__":
    main()
