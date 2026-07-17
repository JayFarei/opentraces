#!/usr/bin/env python3
"""Generate or check the canonical A7 guarantees manifest."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from opentraces.core.arena.verifier_identity import callable_identity


DEFAULT_REPOSITORY = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = DEFAULT_REPOSITORY / "tests/arena/guarantees.json"


def canonical_guarantees(*, repository: Path) -> dict[str, Any]:
    """Build the manifest, binding it to the requested repository's bytes.

    Verifier modules are loaded in a fresh child process (#338) so that a
    ``tests.*`` module already imported in this process from a different
    worktree cannot bind the manifest to the wrong source digest. The child
    imports the verifiers exclusively from ``repository`` and prints the
    manifest; this parent's ``sys.path``/``sys.modules`` are left untouched.
    """

    repository = Path(repository).resolve()
    child_env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--emit-manifest",
         "--repository", str(repository)],
        env=child_env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _canonical_guarantees_in_process(*, repository: Path) -> dict[str, Any]:
    """Build the manifest in this process from ``repository``'s verifier bytes.

    Only ever called inside the isolated child spawned by
    ``canonical_guarantees``; it inserts ``repository`` at the front of
    ``sys.path`` so the fresh interpreter binds ``tests.*`` to that repository.
    """

    repository = Path(repository).resolve()
    if str(repository) not in sys.path:
        sys.path.insert(0, str(repository))
    from tests.arena.guarantees import (
        verify_linux_x86_64_hf_emulator,
        verify_remote_rented_glibc_emulator,
    )
    from tests.arena.scenarios.test_browser_auth_reaches_hf import cli_reports_authenticated
    from tests.arena.scenarios.test_publish_reaches_hf_remote import (
        publish_commit_is_witnessed,
    )

    browser_identity = callable_identity(cli_reports_authenticated)
    publish_identity = callable_identity(publish_commit_is_witnessed)
    remote_identity = callable_identity(verify_remote_rented_glibc_emulator)
    x64_identity = callable_identity(verify_linux_x86_64_hf_emulator)
    return {
        "guarantees": [
            {
                "id": "browser-auth",
                "claim": "Browser authorization makes a separately invoked CLI report authenticated.",
                "nodeid": (
                    "tests/arena/scenarios/test_browser_auth_reaches_hf.py::"
                    "test_browser_authorization_authenticates_the_cli"
                ),
                "verifier": {"name": browser_identity[0], "digest": browser_identity[1]},
                "black_box_review": "unreviewed",
            },
            {
                "id": "publish-down",
                "claim": "Publishing a dataset reaches the configured Hugging Face remote.",
                "nodeid": (
                    "tests/arena/scenarios/test_publish_reaches_hf_remote.py::"
                    "test_publish_reaches_hf_remote"
                ),
                "verifier": {"name": publish_identity[0], "digest": publish_identity[1]},
                "black_box_review": "unreviewed",
            },
            {
                "id": "remote-rented-glibc",
                "claim": "The pinned Hugging Face emulator runs on a remote rented glibc lease.",
                "nodeid": (
                    "tests/arena/scenarios/test_remote_rented_glibc.py::"
                    "test_hf_emulator_runs_on_remote_rented_glibc"
                ),
                "verifier": {"name": remote_identity[0], "digest": remote_identity[1]},
                "black_box_review": "unreviewed",
            },
            {
                "id": "linux-x86_64-hf-emulator",
                "claim": "The pinned Hugging Face emulator runs on Linux x86_64.",
                "nodeid": (
                    "tests/arena/scenarios/test_linux_x86_64_hf_emulator.py::"
                    "test_hf_emulator_runs_on_linux_x86_64"
                ),
                "verifier": {"name": x64_identity[0], "digest": x64_identity[1]},
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
    # Internal child entrypoint used by ``canonical_guarantees`` to isolate a
    # single repository's verifier bytes from process-cached modules (#338).
    parser.add_argument("--emit-manifest", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.emit_manifest:
        sys.stdout.write(
            _encoded(_canonical_guarantees_in_process(repository=args.repository))
        )
        return 0

    expected = _encoded(canonical_guarantees(repository=args.repository))
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != expected:
            raise SystemExit(f"stale guarantees manifest: {args.output}")
        return 0
    args.output.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
