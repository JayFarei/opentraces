#!/usr/bin/env python3
"""Synthetic interactive REPL for runner meta-tests (plan 071, R3).

Reads stdin lines. For each line, matches against a small set of
trigger patterns and emits a canned response. Lets the PTY runner be
exercised in default CI without a real agent binary.

Triggers (case-insensitive substring match):
  * "add a farewell" or "add farewell"  -> "OK, I'll add the helper now"
  * "yes" (exact, stripped)             -> "Done! Committed."
  * "quit" or "exit"                    -> exit cleanly

Anything else echoes back "Unknown command; try 'help'".
Supports --version flag printing "otbox-echo 1.0.0".
"""
from __future__ import annotations

import sys


def main() -> int:
    if "--version" in sys.argv:
        print("otbox-echo 1.0.0")
        return 0
    while True:
        try:
            line = input()
        except EOFError:
            break
        stripped = line.strip()
        low = stripped.lower()
        if low in ("quit", "exit"):
            print("Goodbye.", flush=True)
            break
        if "add a farewell" in low or "add farewell" in low:
            print("OK, I'll add the helper now", flush=True)
            continue
        if stripped == "yes":
            # Don't terminate on yes — real agents stay alive after
            # a confirmation so the simulated user can drive more
            # turns. The runner sends exit/quit to wind down.
            print("Done! Committed.", flush=True)
            continue
        print("Unknown command; try 'help'", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
