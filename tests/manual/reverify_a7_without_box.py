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
from pathlib import Path, PurePosixPath
from typing import Any

from opentraces.core.arena.run_store import RunStore


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
    integrity_by_run_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Invoke ``bench reverify`` twice with no box binary or usable network proxy."""

    store_root = Path(store_root).resolve()
    repository = Path(repository).resolve()
    guarantees = _guarantees(Path(guarantees_path))
    if set(run_ids) != set(EXPECTED_STATUS):
        raise AssertionError("run ids must bind exactly browser-auth and publish-down")
    if integrity_by_run_id is None:
        store = RunStore(store_root)
        integrity_by_run_id = {
            run_id: store.verified_integrity(store.root / run_id) for run_id in run_ids.values()
        }
    if set(integrity_by_run_id) != set(run_ids.values()):
        raise AssertionError("storage integrity does not bind the exact reverified runs")

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
            evidence_refs = observed.get("evidence_refs")
            if not isinstance(evidence_refs, list) or not evidence_refs:
                raise AssertionError(f"{guarantee_id} reverify returned no evidence refs")
            for evidence_ref in evidence_refs:
                path = PurePosixPath(str(evidence_ref))
                if path.is_absolute() or ".." in path.parts or str(path) != evidence_ref:
                    raise AssertionError(
                        f"{guarantee_id} reverify returned an unstable evidence ref"
                    )
            storage_integrity = dict(integrity_by_run_id[run_id])
            if (
                storage_integrity.get("verified") is not True
                or not str(storage_integrity.get("result_digest") or "").startswith("sha256:")
                or not str(storage_integrity.get("integrity_digest") or "").startswith("sha256:")
            ):
                raise AssertionError(f"{guarantee_id} run has no verified storage binding")
            attempts.append(
                {
                    "guarantee_id": guarantee_id,
                    "run_id": run_id,
                    "run_ref": f"runs/v1/{run_id}/result.json",
                    "storage_integrity": storage_integrity,
                    "exit_code": completed.returncode,
                    "status": expected_status,
                    "verifier": dict(verifier),
                    "evidence_refs": list(evidence_refs),
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
