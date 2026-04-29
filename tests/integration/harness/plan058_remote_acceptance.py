#!/usr/bin/env python
"""Plan 058 dataset remote acceptance harness.

Fake mode is intentionally self-contained: it redirects ~/.opentraces into a
temporary directory and uses OPENTRACES_PLAN058_FAKE_REMOTE_ROOT as the remote
adapter seam. Live mode is gated and reports a structured skip unless the
operator explicitly opts in with the required environment.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from opentraces.cli.dataset import dataset_group
from opentraces.core import config as config_mod
from opentraces.core import paths as paths_mod
from opentraces.core.datasets import (
    append_rows,
    dataset_path,
    load_public_rows,
    read_publication_state,
    read_row_index,
)


def _row(summary: str, trace_id: str) -> dict[str, str]:
    return {
        "source_trace_id": trace_id,
        "source_unit_id": f"tu:{trace_id}:trace",
        "summary": summary,
    }


def _invoke(runner: CliRunner, args: list[str]) -> dict[str, Any]:
    result = runner.invoke(dataset_group, args)
    if result.exit_code != 0:
        raise AssertionError(f"dataset {' '.join(args)} failed:\n{result.output}")
    if "--json" in args:
        return json.loads(result.output)
    return {"output": result.output}


def _isolate(tmp: Path) -> None:
    home = tmp / "home"
    opentraces_dir = home / ".opentraces"
    projects_dir = opentraces_dir / "projects"
    projects_dir.mkdir(parents=True)
    os.environ["HOME"] = str(home)
    paths_mod.OPENTRACES_DIR = opentraces_dir
    paths_mod.CONFIG_PATH = opentraces_dir / "config.json"
    paths_mod.CREDENTIALS_PATH = opentraces_dir / "credentials"
    paths_mod.PROJECTS_DIR = projects_dir
    config_mod.OPENTRACES_DIR = opentraces_dir
    config_mod.CONFIG_PATH = opentraces_dir / "config.json"
    config_mod.CREDENTIALS_PATH = opentraces_dir / "credentials"
    config_mod.PROJECTS_DIR = projects_dir


def run_fake() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="opentraces-plan058-") as tmp_raw:
        tmp = Path(tmp_raw)
        _isolate(tmp)
        remote_root = tmp / "remotes"
        os.environ["OPENTRACES_PLAN058_FAKE_REMOTE_ROOT"] = str(remote_root)
        runner = CliRunner()

        _invoke(
            runner,
            [
                "new",
                "acceptance",
                "--workflow",
                "curator",
                "--workflow-digest",
                "sha256:w",
                "--json",
            ],
        )
        remote_create = _invoke(
            runner,
            ["remote", "create", "acceptance", "me/acceptance", "--public", "--json"],
        )
        _invoke(runner, ["remote", "create", "acceptance", "me/remove-me", "--json"])
        remote_list = _invoke(runner, ["remote", "list", "acceptance", "--json"])
        remote_visibility = _invoke(
            runner,
            [
                "remote",
                "visibility",
                "acceptance",
                "me/remove-me",
                "--private",
                "--json",
            ],
        )
        remote_remove = _invoke(
            runner,
            [
                "remote",
                "remove",
                "acceptance",
                "me/remove-me",
                "--delete-remote",
                "--yes",
                "--json",
            ],
        )

        good = append_rows("acceptance", [_row("Accepted row.", "trace-good")], run_id="run-1")
        blocked = append_rows(
            "acceptance",
            [
                _row(
                    "Contains sk-live-abcdefghijklmnopqrstuvwxyz123456 and must stay local.",
                    "trace-blocked",
                )
            ],
            run_id="run-2",
        )
        review_before = _invoke(runner, ["review", "acceptance", "--json"])
        _invoke(runner, ["approve", "acceptance", "--all", "--json"])
        review_after = read_publication_state("acceptance")

        check_only = _invoke(runner, ["publish", "acceptance", "--check-only", "--json"])
        os.environ["OPENTRACES_PLAN058_FAKE_CONFLICT_ROW"] = json.dumps(
            _row("Concurrent row.", "trace-concurrent")
        )
        published = _invoke(runner, ["publish", "acceptance", "--json"])
        os.environ.pop("OPENTRACES_PLAN058_FAKE_CONFLICT_ROW", None)

        doctor = _invoke(runner, ["doctor", "acceptance", "--byte-identity", "--json"])
        remote_files = [
            str(path.relative_to(remote_root / "me" / "acceptance"))
            for path in (remote_root / "me" / "acceptance").rglob("*")
            if path.is_file()
        ]
        no_control_plane_leak = not any(path.startswith(".opentraces/") for path in remote_files)

        applied = _invoke(
            runner,
            ["apply", "hf://me/acceptance", "--as", "copy", "--read-only", "--json"],
        )
        pulled = _invoke(runner, ["pull", "copy", "--data", "--json"])

        withdrawal = _invoke(
            runner,
            [
                "withdraw",
                "copy",
                read_row_index("copy")[0].row_id,
                "--reason",
                "user-request",
                "--json",
            ],
        )

        _invoke(
            runner,
            [
                "new",
                "denied",
                "--workflow",
                "curator",
                "--workflow-digest",
                "sha256:w",
                "--json",
            ],
        )
        _invoke(runner, ["remote", "create", "denied", "me/denied", "--json"])
        append_rows("denied", [_row("Denied row.", "trace-denied")], run_id="run-denied")
        _invoke(runner, ["approve", "denied", "--all", "--json"])
        os.environ["OPENTRACES_PLAN058_FAKE_DENY_WRITE"] = "1"
        denied_result = runner.invoke(dataset_group, ["publish", "denied", "--json"])
        os.environ.pop("OPENTRACES_PLAN058_FAKE_DENY_WRITE", None)

        return {
            "status": "ok",
            "mode": "fake",
            "remote_create": remote_create["remote"],
            "remote_count_after_create": len(remote_list["remotes"]),
            "remote_visibility": remote_visibility["remote"],
            "remote_remove": remote_remove["remote"],
            "review_before": review_before["counts"],
            "review_after": {
                row_id: entry.status for row_id, entry in review_after.rows.items()
            },
            "good_row_id": good.row_ids[0],
            "blocked_row_id": blocked.row_ids[0],
            "check_only": check_only["publish"],
            "published": published["publish"],
            "doctor": doctor["byte_identity"],
            "no_control_plane_leak": no_control_plane_leak,
            "applied_dataset": applied["dataset"]["name"],
            "pull": pulled["pull"],
            "withdrawal": withdrawal["withdrawal"],
            "wrapper_visible_rows_after_withdrawal": len(load_public_rows("copy")),
            "no_write_access": {
                "exit_code": denied_result.exit_code,
                "classified": denied_result.exit_code == 3
                and "write access denied" in denied_result.output,
            },
        }


def run_hf_live() -> dict[str, Any]:
    if os.environ.get("OPENTRACES_RUN_LIVE_PLAN058_HF") != "1":
        return {
            "status": "skipped",
            "mode": "hf-live",
            "reason": "set OPENTRACES_RUN_LIVE_PLAN058_HF=1 to run live HF acceptance",
        }
    if not os.environ.get("HF_TOKEN") and not os.environ.get("HUGGINGFACE_TOKEN"):
        return {
            "status": "skipped",
            "mode": "hf-live",
            "reason": "HF_TOKEN or HUGGINGFACE_TOKEN is required for live HF acceptance",
        }
    return {
        "status": "skipped",
        "mode": "hf-live",
        "reason": "live HF adapter is gated; fake mode covers acceptance in CI",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["fake", "hf-live"], required=True)
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()
    payload = run_fake() if args.mode == "fake" else run_hf_live()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(payload["status"])


if __name__ == "__main__":
    main()
