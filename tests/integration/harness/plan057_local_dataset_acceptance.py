"""Plan 57 local dataset/workflow acceptance harness.

Runs a seeded, no-network proof of the M2 local dataset loop:

    python tests/integration/harness/plan057_local_dataset_acceptance.py --mode local --json
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator

from click.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"
SCHEMA_SRC = REPO_ROOT / "packages" / "opentraces-schema" / "src"
for path in (str(SRC), str(SCHEMA_SRC), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)


@contextlib.contextmanager
def _temporary_opentraces_home(home: Path) -> Iterator[None]:
    old_home = os.environ.get("HOME")
    old_fake_rows = os.environ.get("OPENTRACES_FAKE_CLAUDE_CODE_HEADLESS_ROWS")
    os.environ["HOME"] = str(home)

    from opentraces.core import config as config_mod
    from opentraces.core import paths as paths_mod

    names = ("OPENTRACES_DIR", "CONFIG_PATH", "CREDENTIALS_PATH", "PROJECTS_DIR")
    saved = {mod: {name: getattr(mod, name) for name in names} for mod in (paths_mod, config_mod)}
    opentraces_dir = home / ".opentraces"
    projects_dir = opentraces_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    for mod in (paths_mod, config_mod):
        mod.OPENTRACES_DIR = opentraces_dir
        mod.CONFIG_PATH = opentraces_dir / "config.json"
        mod.CREDENTIALS_PATH = opentraces_dir / "credentials"
        mod.PROJECTS_DIR = projects_dir
    try:
        yield
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        if old_fake_rows is None:
            os.environ.pop("OPENTRACES_FAKE_CLAUDE_CODE_HEADLESS_ROWS", None)
        else:
            os.environ["OPENTRACES_FAKE_CLAUDE_CODE_HEADLESS_ROWS"] = old_fake_rows
        for mod, values in saved.items():
            for name, value in values.items():
                setattr(mod, name, value)


def _fake_rows() -> str:
    rows = [
        {
            "source_trace_id": "trace-1",
            "source_unit_id": "tu:trace-1:trace",
            "summary": "The user wanted a stricter design review.",
        },
        {
            "source_trace_id": "trace-1",
            "source_unit_id": "tu:trace-1:trace",
            "summary": "The user wanted a stricter design review.",
        },
        {
            "source_trace_id": "trace-2",
            "source_unit_id": "tu:trace-2:trace",
            "summary": "Invalid extra field.",
            "extra": True,
        },
    ]
    return "\n".join(json.dumps(row, sort_keys=True) for row in rows)


def _invoke(runner: CliRunner, argv: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    from opentraces.cli import main

    result = runner.invoke(main, argv)
    payload = json.loads(result.output) if result.output.strip().startswith("{") else {}
    transcript = {
        "argv": "ot " + " ".join(argv),
        "exit_code": result.exit_code,
        "output_bytes": len(result.output.encode()),
    }
    if result.exit_code != 0:
        transcript["error_preview"] = result.output[:240]
    return transcript, payload


def _row_index_state_digest(path: Path) -> str:
    from opentraces.core.datasets import digest_payload

    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        rows.append(
            {
                "identity_hash": entry["identity_hash"],
                "payload_hash": entry["payload_hash"],
                "schema_digest": entry["schema_digest"],
                "data_file": entry["data_file"],
                "line": entry["line"],
            }
        )
    return digest_payload(rows)


def run_local() -> dict[str, Any]:
    from opentraces.core.datasets import dataset_path, file_digest, rebuild_row_index

    with tempfile.TemporaryDirectory(prefix="opentraces-plan057-") as tmp:
        root = Path(tmp)
        home = root / "home"
        export_path = root / "rows.jsonl"
        with _temporary_opentraces_home(home):
            os.environ["OPENTRACES_FAKE_CLAUDE_CODE_HEADLESS_ROWS"] = _fake_rows()
            runner = CliRunner()
            command_transcripts: list[dict[str, Any]] = []

            workflow_new, workflow_payload = _invoke(
                runner,
                [
                    "workflow",
                    "new",
                    "grill-me-intent-curator",
                    "--description",
                    "Build rows for the grill-me-intents dataset",
                    "--json",
                ],
            )
            command_transcripts.append(workflow_new)
            workflow_digest = workflow_payload["workflow"]["digest"]

            dataset_new, _ = _invoke(
                runner,
                [
                    "dataset",
                    "new",
                    "grill-me-intents",
                    "--workflow",
                    "grill-me-intent-curator",
                    "--workflow-digest",
                    workflow_digest,
                    "--json",
                ],
            )
            command_transcripts.append(dataset_new)

            dry_run, dry_payload = _invoke(
                runner,
                [
                    "dataset",
                    "run",
                    "grill-me-intents",
                    "--dry-run",
                    "--executor",
                    "claude-code-headless",
                    "--limit",
                    "5",
                    "--verbose",
                    "--json",
                ],
            )
            command_transcripts.append(dry_run)

            real_run, real_payload = _invoke(
                runner,
                [
                    "dataset",
                    "run",
                    "grill-me-intents",
                    "--executor",
                    "claude-code-headless",
                    "--json",
                ],
            )
            command_transcripts.append(real_run)

            repeat_run, repeat_payload = _invoke(
                runner,
                [
                    "dataset",
                    "run",
                    "grill-me-intents",
                    "--executor",
                    "claude-code-headless",
                    "--json",
                ],
            )
            command_transcripts.append(repeat_run)

            export_cmd, export_payload = _invoke(
                runner,
                [
                    "dataset",
                    "export",
                    "grill-me-intents",
                    "--format",
                    "jsonl",
                    "--output",
                    str(export_path),
                    "--json",
                ],
            )
            command_transcripts.append(export_cmd)

            schedule_add, schedule_payload = _invoke(
                runner,
                [
                    "dataset",
                    "schedule",
                    "add",
                    "grill-me-intents",
                    "--every",
                    "2h",
                    "--executor",
                    "claude-code-headless",
                    "--json",
                ],
            )
            command_transcripts.append(schedule_add)

            index_path = dataset_path("grill-me-intents") / ".opentraces" / "row_index.jsonl"
            before_rebuild_digest = _row_index_state_digest(index_path)
            rebuild = rebuild_row_index("grill-me-intents")
            after_rebuild_digest = _row_index_state_digest(index_path)
            data_file = dataset_path("grill-me-intents") / "data" / "train.jsonl"

            return {
                "status": "ok",
                "mode": "local",
                "command_transcripts": command_transcripts,
                "run_ids": {
                    "dry_run": dry_payload["run_id"],
                    "real_run": real_payload["run_id"],
                    "repeat_run": repeat_payload["run_id"],
                },
                "counts": {
                    "dry_run": {
                        "would_append": dry_payload["run"]["would_append_count"],
                        "deduped": dry_payload["run"]["duplicate_count"],
                        "validation_errors": dry_payload["run"]["validation_error_count"],
                    },
                    "real_run": {
                        "appended": real_payload["run"]["appended_count"],
                        "deduped": real_payload["run"]["duplicate_count"],
                        "validation_errors": real_payload["run"]["validation_error_count"],
                    },
                    "repeat_run": {
                        "appended": repeat_payload["run"]["appended_count"],
                        "deduped": repeat_payload["run"]["duplicate_count"],
                    },
                },
                "row_index_rebuild_digests": {
                    "before": before_rebuild_digest,
                    "after": after_rebuild_digest,
                    "equivalent": before_rebuild_digest == after_rebuild_digest,
                    "rebuilt_count": rebuild.rebuilt_count,
                },
                "schedule_status": schedule_payload["schedule"],
                "public_data_file_digests": {
                    "train": file_digest(data_file),
                    "export": export_payload["export"]["digest"],
                    "export_matches_train": file_digest(data_file)
                    == export_payload["export"]["digest"],
                },
            }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["local"], default="local")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = run_local()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Plan 57 acceptance: {payload['status']}")
        print(f"  appended: {payload['counts']['real_run']['appended']}")
        print(f"  exported: {payload['public_data_file_digests']['export_matches_train']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
