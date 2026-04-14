"""CLI import-hf command: import traces from a HuggingFace dataset."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click

from opentraces import cli as _cli
from . import main

logger = logging.getLogger("opentraces.cli.import_hf")


def load_config():
    return _cli.load_config()


def emit_json(data):
    return _cli.emit_json(data)


def error_response(*a, **k):
    return _cli.error_response(*a, **k)


def human_echo(*a, **k):
    return _cli.human_echo(*a, **k)


def _auth_identity(*a, **k):
    return _cli._auth_identity(*a, **k)




@main.command(
    "pull",
    examples=[
        "opentraces pull owner/dataset --parser hermes",
        "opentraces pull owner/dataset --parser hermes --limit 10 --dry-run",
        "opentraces pull owner/dataset --parser hermes --auto",
    ],
    see_also=[
        ("opentraces push", "upload committed traces to HuggingFace"),
        ("opentraces status", "inspect the inbox after import"),
    ],
    option_groups=[
        ("Source", ["parser_name", "subset", "split", "limit"]),
        ("Staging", ["auto", "dry_run"]),
    ],
)
@click.argument("dataset_id")
@click.option("--parser", "parser_name", required=True, help="Format parser (e.g. hermes)")
@click.option("--subset", default=None, help="Dataset subset/config name")
@click.option("--split", default="train", show_default=True, help="Dataset split")
@click.option("--limit", type=int, default=0, help="Max rows to import (0 = all)")
@click.option("--auto", is_flag=True, help="Auto-commit imported traces (skip review)")
@click.option("--dry-run", is_flag=True, help="Parse and report without writing to staging")
def import_hf(
    dataset_id: str,
    parser_name: str,
    subset: str | None,
    split: str,
    limit: int,
    auto: bool,
    dry_run: bool,
) -> None:
    """Import traces from a HuggingFace dataset."""
    from ..core.config import get_project_traces_dir, get_project_state_path, project_is_opted_in
    from ..capture import get_importers
    from ..core.pipeline import process_imported_trace
    from ..core.state import StateManager, TraceStatus

    # 1. Project guard
    project_dir = Path.cwd()
    if not project_is_opted_in(project_dir):
        human_echo("Not an opentraces project. Run 'opentraces init' first.")
        emit_json(error_response(
            "NOT_INITIALIZED", "setup", "Not an opentraces project",
            hint="Run 'opentraces init' first",
        ))
        sys.exit(3)

    # 2. Resolve parser
    importers = get_importers()
    if parser_name not in importers:
        available = ", ".join(sorted(importers.keys())) or "(none)"
        human_echo(f"Unknown parser: {parser_name}. Available: {available}")
        emit_json(error_response(
            "UNKNOWN_PARSER", "config",
            f"Unknown parser: {parser_name}",
            hint=f"Available parsers: {available}",
        ))
        sys.exit(2)
    parser = importers[parser_name]()

    # 3. Import datasets library
    try:
        import datasets as ds_lib
        from huggingface_hub import HfApi
    except ImportError:
        human_echo("Missing dependencies. Run: pip install 'opentraces[import]'")
        emit_json(error_response(
            "MISSING_DEPS", "setup",
            "datasets library not installed",
            hint="pip install 'opentraces[import]'",
        ))
        sys.exit(2)

    cfg = load_config()

    # 4. Fetch dataset revision SHA for provenance
    human_echo(f"Fetching dataset info for {dataset_id}...")
    try:
        api = HfApi()
        info = api.dataset_info(dataset_id)
        revision = info.sha or "unknown"
    except Exception as e:
        human_echo(f"Failed to fetch dataset info: {e}")
        emit_json(error_response(
            "HF_API_ERROR", "network",
            f"Failed to fetch dataset info: {e}",
            hint="Check the dataset ID and your network connection",
            retryable=True,
        ))
        sys.exit(1)

    # 5. Load dataset (try datasets library first, fall back to raw JSONL download)
    dataset = None
    human_echo(f"Loading dataset {dataset_id} (subset={subset}, split={split})...")
    try:
        dataset = ds_lib.load_dataset(dataset_id, subset, split=split)
    except Exception as ds_err:
        human_echo(f"  datasets library failed ({type(ds_err).__name__}), trying raw JSONL download...")
        # Fall back to direct file download for datasets with heterogeneous schemas
        try:
            from huggingface_hub import hf_hub_download
            # Guess the file path from subset name
            file_candidates = [
                f"data/{subset}.jsonl" if subset else "data/train.jsonl",
                f"{subset}.jsonl" if subset else "train.jsonl",
                f"data/{split}.jsonl",
            ]
            jsonl_path = None
            for candidate in file_candidates:
                try:
                    jsonl_path = hf_hub_download(dataset_id, candidate, repo_type="dataset")
                    break
                except Exception:
                    continue
            if jsonl_path is None:
                raise RuntimeError(f"Could not find JSONL file for subset={subset}")
            # Load as list of dicts
            rows = []
            with open(jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
            dataset = rows
            human_echo(f"  Loaded {len(rows)} rows from raw JSONL")
        except Exception as fallback_err:
            human_echo(f"Failed to load dataset: {ds_err} (fallback: {fallback_err})")
            emit_json(error_response(
                "DATASET_LOAD_ERROR", "network",
                f"Failed to load dataset: {ds_err}",
                hint="Check dataset ID, subset, and split names",
                retryable=True,
            ))
            sys.exit(1)

    # 6. Build source_info for provenance
    source_info = {
        "dataset_id": dataset_id,
        "revision": revision,
        "subset": subset or "default",
        "split": split,
    }

    # 7. Setup staging
    staging_dir = get_project_traces_dir(project_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path)

    # 8. Process rows
    parsed_count = 0
    skipped_count = 0
    error_count = 0
    total_redactions = 0
    total_rows = len(dataset)
    rows_to_process = min(total_rows, limit) if limit > 0 else total_rows

    human_echo(f"Processing {rows_to_process} of {total_rows} rows...")

    for i, row in enumerate(dataset):
        if limit > 0 and parsed_count >= limit:
            break

        # Parse row
        try:
            record = parser.map_record(row, i, source_info)
        except Exception as e:
            error_count += 1
            logger.warning("Parse error at row %d: %s", i, e)
            continue

        if record is None:
            skipped_count += 1
            continue

        # Abort if failure rate > 10% (after 10+ rows the parser actually attempted).
        # Skipped rows (e.g. no 'conversations' key) are excluded from the denominator
        # since they represent valid parser decisions, not parser failures.
        total_attempted = parsed_count + error_count
        if total_attempted >= 10 and error_count / total_attempted > 0.10:
            human_echo(
                f"Aborting: error rate {error_count}/{total_attempted} "
                f"({error_count / total_attempted:.0%}) exceeds 10% threshold"
            )
            emit_json(error_response(
                "HIGH_ERROR_RATE", "data",
                f"Error rate {error_count}/{total_attempted} exceeds 10%",
                hint="Check that the dataset matches the parser format",
            ))
            sys.exit(1)

        # Enrich + security scan
        try:
            result = process_imported_trace(record, cfg)
        except Exception as e:
            error_count += 1
            logger.warning("Pipeline error at row %d: %s", i, e)
            continue

        total_redactions += result.redaction_count

        if not dry_run:
            # Write staging file
            staging_file = staging_dir / f"{result.record.trace_id}.jsonl"
            staging_file.write_text(result.record.to_jsonl_line() + "\n")

            # Step 5: route through decide_post_parse_status so TruffleHog
            # findings land as BLOCKED rather than auto-promoted.
            from ..core.workflow import decide_post_parse_status
            decided_status, block_reason = decide_post_parse_status(
                result, review_policy="auto" if auto else "review"
            )

            if decided_status == TraceStatus.BLOCKED:
                state.block_trace(
                    result.record.trace_id,
                    reason=block_reason or "security finding",
                    session_id=result.record.session_id,
                    file_path=str(staging_file),
                )
            elif decided_status == TraceStatus.COMMITTED:
                state.set_trace_status(
                    result.record.trace_id,
                    TraceStatus.COMMITTED,
                    session_id=result.record.session_id,
                    file_path=str(staging_file),
                )
                task_desc = record.task.description or record.session_id
                state.create_commit_group(
                    [result.record.trace_id],
                    task_desc[:80] if task_desc else result.record.trace_id[:12],
                )
            else:
                state.set_trace_status(
                    result.record.trace_id,
                    TraceStatus.STAGED,
                    session_id=result.record.session_id,
                    file_path=str(staging_file),
                )

        parsed_count += 1

    # 9. Summary
    mode = "dry-run" if dry_run else ("auto-committed" if auto else "staged")
    human_echo(
        f"\nDone: {parsed_count} {mode}, {skipped_count} skipped, "
        f"{error_count} errors, {total_redactions} redactions"
    )
    emit_json({
        "status": "ok",
        "dataset": dataset_id,
        "parsed": parsed_count,
        "skipped": skipped_count,
        "errors": error_count,
        "redactions": total_redactions,
        "dry_run": dry_run,
        "next_steps": (
            ["Review with 'opentraces status'"]
            if not auto else ["Push with 'opentraces push'"]
        ) if not dry_run else ["Re-run without --dry-run to stage traces"],
        "next_command": (
            "opentraces status" if not auto else "opentraces push"
        ) if not dry_run else f"opentraces pull {dataset_id} --parser {parser_name}",
    })


