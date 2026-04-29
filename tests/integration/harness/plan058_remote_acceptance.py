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


def _hf_token() -> str | None:
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")


def _list_hf_remote_files(repo_id: str, token: str | None) -> list[str]:
    """Recursively list every file path in the live HF dataset tree."""
    from huggingface_hub import list_repo_tree

    files: list[str] = []
    stack = [""]
    while stack:
        prefix = stack.pop()
        for entry in list_repo_tree(
            repo_id=repo_id,
            repo_type="dataset",
            path_in_repo=prefix or None,
            token=token,
        ):
            kind = getattr(entry, "type", None)
            if entry.__class__.__name__ == "RepoFolder" or kind == "directory":
                stack.append(entry.path)
            else:
                files.append(entry.path)
    return sorted(files)


def run_hf_live() -> dict[str, Any]:
    if os.environ.get("OPENTRACES_RUN_LIVE_PLAN058_HF") != "1":
        return {
            "status": "skipped",
            "mode": "hf-live",
            "reason": "set OPENTRACES_RUN_LIVE_PLAN058_HF=1 to run live HF acceptance",
        }
    token = _hf_token()
    if not token:
        return {
            "status": "skipped",
            "mode": "hf-live",
            "reason": "HF_TOKEN or HUGGINGFACE_TOKEN is required for live HF acceptance",
        }

    import secrets
    from huggingface_hub import HfApi

    from opentraces.core.datasets import (
        add_dataset_remote,
        apply_remote_dataset,
        create_dataset,
        dataset_path,
        publish_dataset,
        pull_dataset,
        read_row_index,
    )

    api = HfApi(token=token)
    user = api.whoami()["name"]
    suffix = secrets.token_hex(4)
    repo_id = f"{user}/opentraces-plan058-uat-{suffix}"
    api.create_repo(
        repo_id=repo_id,
        repo_type="dataset",
        private=True,
        exist_ok=False,
    )

    cleanup_error: str | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="opentraces-plan058-hf-live-") as tmp_raw:
            tmp = Path(tmp_raw)
            _isolate(tmp)
            # Important: do NOT set OPENTRACES_PLAN058_FAKE_REMOTE_ROOT. The
            # absence of that env var is what routes core.datasets through
            # the real huggingface_hub adapter.
            os.environ.pop("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", None)

            create_dataset(
                "live-source",
                workflow_skill="curator",
                workflow_digest="sha256:workflow",
                publication_policy={"review": "auto"},
            )
            add_dataset_remote("live-source", repo_id, visibility="private")
            from opentraces.core.datasets import append_rows

            originals = [
                {
                    "source_trace_id": "trace-live-1",
                    "source_unit_id": "tu:trace-live-1:trace",
                    "summary": "Live row one.",
                },
                {
                    "source_trace_id": "trace-live-2",
                    "source_unit_id": "tu:trace-live-2:trace",
                    "summary": "Live row two.",
                },
            ]
            append_rows("live-source", originals, run_id="run-live")

            published = publish_dataset(
                "live-source",
                contributor="tester",
                token=token,
            )

            remote_files = _list_hf_remote_files(repo_id, token)
            no_control_plane_leak = not any(
                ".opentraces" in Path(rel).parts for rel in remote_files
            )

            apply_remote_dataset(
                f"hf://{repo_id}",
                as_name="live-clone",
                read_only=True,
                token=token,
            )
            pulled = pull_dataset("live-clone", data=True, token=token)
            clone_row_count = len(read_row_index("live-clone"))

            # V24 mirror via datasets.load_dataset on the clone.
            try:
                import datasets as _hf_datasets

                loaded = _hf_datasets.load_dataset(str(dataset_path("live-clone")))
                load_dataset_rows = (
                    loaded["train"].num_rows if "train" in loaded else 0
                )
                load_dataset_ok = load_dataset_rows == len(originals)
            except Exception as exc:  # noqa: BLE001 — surface error in payload
                load_dataset_rows = 0
                load_dataset_ok = False
                cleanup_error = f"load_dataset failed: {exc!r}"

            return {
                "status": "ok",
                "mode": "hf-live",
                "repo_id": repo_id,
                "published": {
                    "uploaded": published.uploaded,
                    "new_row_count": published.new_row_count,
                    "attempts": published.attempts,
                    "staged_files": published.staged_files,
                    "remote_head_after": published.remote_head_after,
                },
                "remote_file_count": len(remote_files),
                "no_control_plane_leak": no_control_plane_leak,
                "pull": {
                    "imported_count": pulled.imported_count,
                    "duplicate_count": pulled.duplicate_count,
                    "metadata_refreshed": pulled.metadata_refreshed,
                },
                "clone_row_count": clone_row_count,
                "load_dataset": {
                    "ok": load_dataset_ok,
                    "rows": load_dataset_rows,
                },
                "cleanup_error": cleanup_error,
            }
    finally:
        try:
            api.delete_repo(
                repo_id=repo_id,
                repo_type="dataset",
                missing_ok=True,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            cleanup_error = f"delete_repo failed: {exc!r}"


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
