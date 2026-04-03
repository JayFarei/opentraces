#!/usr/bin/env python3
"""Disposable import script for lambda/hermes-agent-reasoning-traces.

Remaps lambda-specific fields to canonical Hermes parser conventions,
then runs the standard opentraces import pipeline.

Usage:
    python scripts/import_lambda_hermes.py --limit 5 --dry-run
    python scripts/import_lambda_hermes.py --auto

Prerequisites:
    pip install 'opentraces[import]'
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import click

DATASET_ID = "lambda/hermes-agent-reasoning-traces"
SPLIT = "train"

# Model from dataset README card. DatasetCard.load() exposes card.text (markdown body)
# but has no structured field for this dataset. Regex match, then normalise to HF ID.
_MODEL_RE = re.compile(r"Kimi[-\s]K2\.5", re.IGNORECASE)
_MODEL_FALLBACK = "moonshotai/kimi-k2.5"


def _read_model_from_card(dataset_id: str) -> str:
    try:
        from huggingface_hub import DatasetCard
        card = DatasetCard.load(dataset_id)
        if _MODEL_RE.search(card.text or ""):
            return _MODEL_FALLBACK
    except Exception:
        pass
    return _MODEL_FALLBACK


@click.command()
@click.option("--limit", default=0, type=int, help="Max rows to import (0 = all)")
@click.option("--dry-run", is_flag=True, help="Parse and report without writing to staging")
@click.option("--auto", is_flag=True, help="Auto-commit imported traces (skip manual review)")
def main(limit: int, dry_run: bool, auto: bool) -> None:
    """Import lambda/hermes-agent-reasoning-traces into an opentraces project."""
    try:
        import datasets as ds_lib
        from huggingface_hub import HfApi
    except ImportError:
        click.echo("Missing dependencies. Run: pip install 'opentraces[import]'", err=True)
        sys.exit(2)

    # Project guard (mirrors cli.py:2467-2476)
    project_dir = Path.cwd()
    if not (project_dir / ".opentraces").exists():
        click.echo("Not an opentraces project. Run 'opentraces init' first.", err=True)
        sys.exit(3)

    from opentraces.config import get_project_staging_dir, get_project_state_path, load_config
    from opentraces.parsers.hermes import HermesParser
    from opentraces.pipeline import process_imported_trace
    from opentraces.state import StateManager, TraceStatus

    cfg = load_config()
    parser = HermesParser()

    # Fetch dataset revision SHA for provenance (mirrors cli.py:2506-2520)
    click.echo(f"Fetching dataset info for {DATASET_ID}...")
    try:
        revision = HfApi().dataset_info(DATASET_ID).sha or "unknown"
    except Exception as e:
        click.echo(f"Warning: could not fetch dataset revision: {e}", err=True)
        revision = "unknown"

    model = _read_model_from_card(DATASET_ID)
    click.echo(f"Model: {model}")

    source_info = {
        "dataset_id": DATASET_ID,
        "revision": revision,
        "subset": "default",
        "split": SPLIT,
    }

    # Load dataset (mirrors cli.py:2522-2564)
    click.echo(f"Loading {DATASET_ID} (split={SPLIT})...")
    dataset = ds_lib.load_dataset(DATASET_ID, split=SPLIT)
    total = len(dataset)
    rows_to_process = min(total, limit) if limit > 0 else total
    click.echo(f"Processing {rows_to_process} of {total} rows...")

    # Setup staging + state (mirrors cli.py:2574-2578)
    staging_dir = get_project_staging_dir(project_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path if state_path.parent.exists() else None)

    parsed = skipped = errors = redactions = 0

    for i, row in enumerate(dataset):
        if limit > 0 and parsed >= limit:
            break

        # Remap lambda fields the parser already reads natively.
        # Only inject into keys the parser actually consumes.
        row = dict(row)
        row.setdefault("source_row", {})["original_prompt"] = row.get("task")
        row.setdefault("metadata", {})["model"] = model

        try:
            record = parser.map_record(row, i, source_info)
        except Exception as e:
            errors += 1
            click.echo(f"  Parse error at row {i}: {e}", err=True)
            continue

        if record is None:
            skipped += 1
            continue

        # Abort if error rate > 10% after 10+ attempted rows (mirrors cli.py:2606-2620)
        total_attempted = parsed + errors
        if total_attempted >= 10 and errors / total_attempted > 0.10:
            click.echo(
                f"Aborting: error rate {errors}/{total_attempted} "
                f"({errors / total_attempted:.0%}) exceeds 10% threshold",
                err=True,
            )
            sys.exit(1)

        # Patch lambda-specific fields onto the returned TraceRecord.
        # These are not canonical Hermes format, so the parser returns defaults.
        raw_tools = row.get("tools")
        if raw_tools:
            if isinstance(raw_tools, list):
                # Parquet materializes the column as native list[dict]
                record.tool_definitions = raw_tools
            elif isinstance(raw_tools, str):
                # JSONL path: column is a stringified JSON array
                try:
                    parsed_tools = json.loads(raw_tools)
                    if isinstance(parsed_tools, list):
                        record.tool_definitions = parsed_tools
                except json.JSONDecodeError:
                    pass  # leave as []

        if row.get("category"):
            record.metadata["source_category"] = row["category"]
        if row.get("subcategory"):
            record.metadata["source_subcategory"] = row["subcategory"]
        if row.get("id"):
            record.metadata["source_row_id"] = row["id"]

        try:
            result = process_imported_trace(record, cfg)
        except Exception as e:
            errors += 1
            click.echo(f"  Pipeline error at row {i}: {e}", err=True)
            continue

        redactions += result.redaction_count

        if not dry_run:
            # Write staging + update state (mirrors cli.py:2633-2656)
            staging_file = staging_dir / f"{result.record.trace_id}.jsonl"
            staging_file.write_text(result.record.to_jsonl_line() + "\n")

            if auto and not result.needs_review:
                state.set_trace_status(
                    result.record.trace_id,
                    TraceStatus.COMMITTED,
                    session_id=result.record.session_id,
                    file_path=str(staging_file),
                )
                task_desc = record.task.description or result.record.trace_id[:12]
                state.create_commit_group(
                    [result.record.trace_id],
                    task_desc[:80],
                )
            else:
                state.set_trace_status(
                    result.record.trace_id,
                    TraceStatus.STAGED,
                    session_id=result.record.session_id,
                    file_path=str(staging_file),
                )

        parsed += 1

    mode = "dry-run" if dry_run else ("auto-committed" if auto else "staged")
    click.echo(
        f"\nDone: {parsed} {mode}, {skipped} skipped, "
        f"{errors} errors, {redactions} redactions"
    )
    if not dry_run:
        next_cmd = "opentraces push --dataset lambda-hermes-agent-reasoning-opentraces"
        click.echo(f"Next: {next_cmd}")


if __name__ == "__main__":
    main()
