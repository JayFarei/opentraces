#!/usr/bin/env python3
"""Codex CLI PostCompact hook for OpenTraces."""

from __future__ import annotations

from ._common import record_lifecycle_event


def main() -> None:
    record_lifecycle_event("PostCompact")


if __name__ == "__main__":
    main()
