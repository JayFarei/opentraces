#!/usr/bin/env python3
"""Reverify A7's bound runs through the public CLI without a box runtime."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


EXPECTED_STATUS = {"browser-auth": "pass", "publish-down": "fail"}
EXPECTED_EXIT = {"browser-auth": 0, "publish-down": 1}


def _guarantees(path: Path) -> dict[str, Mapping[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("guarantees") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        raise AssertionError("guarantees manifest has no guarantees list")
    by_id = {str(row.get("id")): row for row in rows if isinstance(row, Mapping)}
    if not set(EXPECTED_STATUS).issubset(by_id):
        raise AssertionError("guarantees manifest is missing a bound A7 row")
    return by_id


def run_public_reverification(
    *,
    store_root: Path,
    guarantees_path: Path,
    run_ids: Mapping[str, str],
    repository: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Invoke ``bench reverify`` twice with no box binary or usable network proxy."""

    store_root = Path(store_root).resolve()
    repository = Path(repository).resolve()
    guarantees = _guarantees(Path(guarantees_path))
    if set(run_ids) != set(EXPECTED_STATUS):
        raise AssertionError("run ids must bind exactly browser-auth and publish-down")

    temporary_parent = store_root.parent.parent
    temporary_parent.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="a7-reverify-no-box-", dir=temporary_parent) as home:
        environment = {
            "ALL_PROXY": "http://127.0.0.1:9",
            "HOME": home,
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "NO_PROXY": "",
            "PATH": "",
            "PYTHONPATH": os.pathsep.join([str(repository), str(repository / "src")]),
            "TMPDIR": home,
        }
        for guarantee_id in EXPECTED_STATUS:
            row = guarantees[guarantee_id]
            verifier = row.get("verifier")
            if not isinstance(verifier, Mapping):
                raise AssertionError(f"{guarantee_id} has no verifier binding")
            run_id = run_ids[guarantee_id]
            command = [
                sys.executable,
                "-m",
                "opentraces",
                "bench",
                "reverify",
                run_id,
                "--store-root",
                str(store_root),
                "--verifier-name",
                str(verifier.get("name")),
                "--verifier-digest",
                str(verifier.get("digest")),
                "--json",
            ]
            completed = runner(
                command,
                cwd=repository,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if completed.stderr:
                raise AssertionError(f"{guarantee_id} reverify wrote unexpected stderr")
            observed = json.loads(completed.stdout)
            if not isinstance(observed, Mapping):
                raise AssertionError(f"{guarantee_id} reverify did not emit an object")
            expected_status = EXPECTED_STATUS[guarantee_id]
            expected_exit = EXPECTED_EXIT[guarantee_id]
            if observed.get("run_id") != run_id:
                raise AssertionError(f"{guarantee_id} reverify returned the wrong run")
            if observed.get("status") != expected_status or completed.returncode != expected_exit:
                raise AssertionError(f"{guarantee_id} reverify returned the wrong verdict or exit")
            if observed.get("verifier") != dict(verifier):
                raise AssertionError(f"{guarantee_id} reverify returned the wrong verifier binding")
            attempts.append(
                {
                    "guarantee_id": guarantee_id,
                    "run_id": run_id,
                    "exit_code": completed.returncode,
                    "status": expected_status,
                }
            )

    return {
        "schema_version": "opentraces.bench.reverify-without-box.v0",
        "runtime_constraints": {
            "box_runtime": "unavailable",
            "external_process_path": "empty",
            "network": "closed-loop-proxy",
        },
        "attempts": attempts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store-root", required=True, type=Path)
    parser.add_argument("--guarantees", required=True, type=Path)
    parser.add_argument("--browser-run", required=True)
    parser.add_argument("--publish-run", required=True)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    transcript = run_public_reverification(
        store_root=args.store_root,
        guarantees_path=args.guarantees,
        run_ids={"browser-auth": args.browser_run, "publish-down": args.publish_run},
        repository=args.repository,
    )
    args.output.write_text(json.dumps(transcript, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(transcript, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
