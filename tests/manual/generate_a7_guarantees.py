#!/usr/bin/env python3
"""Generate or check the canonical A7 guarantees manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_REPOSITORY = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = DEFAULT_REPOSITORY / "tests/arena/guarantees.json"


def _digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def canonical_guarantees(*, repository: Path) -> dict[str, Any]:
    """Build the manifest after all verifier sources are final."""

    repository = Path(repository).resolve()
    browser_source = repository / "tests/arena/scenarios/test_browser_auth_reaches_hf.py"
    publish_source = repository / "tests/arena/scenarios/test_publish_reaches_hf_remote.py"
    future_source = repository / "tests/arena/guarantees.py"
    return {
        "guarantees": [
            {
                "id": "browser-auth",
                "claim": "Browser authorization makes a separately invoked CLI report authenticated.",
                "nodeid": (
                    "tests/arena/scenarios/test_browser_auth_reaches_hf.py::"
                    "test_browser_authorization_authenticates_the_cli"
                ),
                "verifier": {
                    "name": (
                        "tests.arena.scenarios.test_browser_auth_reaches_hf."
                        "cli_reports_authenticated"
                    ),
                    "digest": _digest(browser_source),
                },
                "black_box_review": "unreviewed",
            },
            {
                "id": "publish-down",
                "claim": "Publishing a dataset reaches the configured Hugging Face remote.",
                "nodeid": (
                    "tests/arena/scenarios/test_publish_reaches_hf_remote.py::"
                    "test_publish_reaches_hf_remote"
                ),
                "verifier": {
                    "name": (
                        "tests.arena.scenarios.test_publish_reaches_hf_remote."
                        "publish_commit_is_witnessed"
                    ),
                    "digest": _digest(publish_source),
                },
                "black_box_review": "unreviewed",
            },
            {
                "id": "remote-rented-glibc",
                "claim": "The pinned Hugging Face emulator runs on a remote rented glibc lease.",
                "nodeid": (
                    "tests/arena/scenarios/test_remote_rented_glibc.py::"
                    "test_hf_emulator_runs_on_remote_rented_glibc"
                ),
                "verifier": {
                    "name": "tests.arena.guarantees.verify_remote_rented_glibc_emulator",
                    "digest": _digest(future_source),
                },
                "black_box_review": "unreviewed",
            },
            {
                "id": "linux-x86_64-hf-emulator",
                "claim": "The pinned Hugging Face emulator runs on Linux x86_64.",
                "nodeid": (
                    "tests/arena/scenarios/test_linux_x86_64_hf_emulator.py::"
                    "test_hf_emulator_runs_on_linux_x86_64"
                ),
                "verifier": {
                    "name": "tests.arena.guarantees.verify_linux_x86_64_hf_emulator",
                    "digest": _digest(future_source),
                },
                "black_box_review": "unreviewed",
            },
        ]
    }


def _encoded(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=DEFAULT_REPOSITORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    expected = _encoded(canonical_guarantees(repository=args.repository))
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != expected:
            raise SystemExit(f"stale guarantees manifest: {args.output}")
        return 0
    args.output.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
