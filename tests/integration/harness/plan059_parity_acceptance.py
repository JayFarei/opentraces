"""Plan-59 parity acceptance harness.

Headless companion to ``tests/integration/test_trace_query_parity.py``.
Runs the full FilterCase matrix outside pytest, emits a structured JSON
summary suitable for CI artefact upload, and exits non-zero when any case
fails to verdict ``pass``.

Usage:

    .venv/bin/python tests/integration/harness/plan059_parity_acceptance.py --json
    .venv/bin/python tests/integration/harness/plan059_parity_acceptance.py --output kb/uat/plan059.json

By default the harness seeds the synthetic Plan-59 corpus into a tmp
``$HOME/.opentraces`` so the run does not pollute the developer's actual
trace store. Pass ``--use-real-home`` to validate against the user's real
on-disk corpus (read-only — no traces are written).
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

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"
SCHEMA_SRC = REPO_ROOT / "packages" / "opentraces-schema" / "src"
TESTS_DIR = REPO_ROOT / "tests"
for path in (str(SRC), str(SCHEMA_SRC), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)


@contextlib.contextmanager
def _isolated_home() -> Iterator[Path]:
    """Patch ``HOME`` and the path constants for the duration of a run."""

    from opentraces.core import config as config_mod
    from opentraces.core import paths as paths_mod

    saved_env = os.environ.get("HOME")
    with tempfile.TemporaryDirectory(prefix="plan059-parity-") as tmp:
        home = Path(tmp)
        opentraces_dir = home / ".opentraces"
        projects_dir = opentraces_dir / "projects"
        staging_dir = opentraces_dir / "staging"
        opentraces_dir.mkdir(parents=True, exist_ok=True)
        projects_dir.mkdir(parents=True, exist_ok=True)
        staging_dir.mkdir(parents=True, exist_ok=True)
        os.environ["HOME"] = str(home)
        saved = {}
        names = ("OPENTRACES_DIR", "CONFIG_PATH", "CREDENTIALS_PATH", "PROJECTS_DIR", "STAGING_DIR")
        for mod in (paths_mod, config_mod):
            saved[mod] = {name: getattr(mod, name, None) for name in names}
            mod.OPENTRACES_DIR = opentraces_dir
            mod.CONFIG_PATH = opentraces_dir / "config.json"
            mod.CREDENTIALS_PATH = opentraces_dir / "credentials"
            mod.PROJECTS_DIR = projects_dir
            if hasattr(mod, "STAGING_DIR"):
                mod.STAGING_DIR = staging_dir
        try:
            yield opentraces_dir
        finally:
            for mod, values in saved.items():
                for name, value in values.items():
                    if value is None:
                        if hasattr(mod, name):
                            delattr(mod, name)
                    else:
                        setattr(mod, name, value)
            if saved_env is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = saved_env


def _run_matrix(seed_corpus: bool, *, scale: int = 1) -> list[dict[str, Any]]:
    from tests.integration._trace_query_corpus import write_corpus
    from tests.integration._trace_query_oracle import (
        ensure_index_built,
        load_oracle,
        run_indexed_query,
        run_oracle,
    )
    from tests.integration.test_trace_query_parity import FILTER_MATRIX

    if seed_corpus:
        write_corpus(scale=scale)
    ensure_index_built()
    oracle = load_oracle(force=True)

    rows: list[dict[str, Any]] = []
    for case in FILTER_MATRIX:
        # Warmup pass to stabilize the latency reading.
        run_indexed_query(case.cli_args)
        run_oracle(case.crawl_predicate, oracle)

        oracle_ids, oracle_bytes, oracle_ms = run_oracle(case.crawl_predicate, oracle)
        index_ids, index_bytes, index_ms = run_indexed_query(case.cli_args)

        ratio = (index_ms / oracle_ms) if oracle_ms > 0 else None
        byte_ratio = (index_bytes / oracle_bytes) if oracle_bytes > 0 else None
        rows.append(
            {
                "name": case.name,
                "filter_args": case.cli_args,
                "expected_status": case.expected_status,
                "oracle_count": len(oracle_ids),
                "index_count": len(index_ids),
                "missing_from_index": sorted(list(oracle_ids - index_ids))[:10],
                "extra_in_index": sorted(list(index_ids - oracle_ids))[:10],
                "oracle_latency_ms": round(oracle_ms, 3),
                "index_latency_ms": round(index_ms, 3),
                "latency_ratio": round(ratio, 3) if ratio is not None else None,
                "oracle_bytes": oracle_bytes,
                "index_bytes": index_bytes,
                "byte_ratio": round(byte_ratio, 3) if byte_ratio is not None else None,
                "verdict": "pass" if oracle_ids == index_ids else "fail",
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan-59 parity acceptance harness.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full per-case JSON summary on stdout.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output file (defaults to tests/integration/parity_report.json).",
    )
    parser.add_argument(
        "--use-real-home",
        action="store_true",
        help="Validate against the developer's real $HOME/.opentraces (read-only).",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=20,
        help="Corpus scale multiplier; ~24 traces per repeat. Default 20 ≈ 480 traces.",
    )
    args = parser.parse_args()

    if args.use_real_home:
        rows = _run_matrix(seed_corpus=False)
    else:
        with _isolated_home():
            rows = _run_matrix(seed_corpus=True, scale=args.scale)

    fail_rows = [row for row in rows if row["verdict"] != "pass"]
    summary = {
        "schema": "plan059-parity-acceptance/v1",
        "total_cases": len(rows),
        "passing": len(rows) - len(fail_rows),
        "failing": len(fail_rows),
        "verdict": "pass" if not fail_rows else "fail",
        "cases": rows,
    }

    output_path = args.output or (REPO_ROOT / "tests" / "integration" / "parity_report.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True))

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            f"plan059 parity: {summary['passing']}/{summary['total_cases']} pass "
            f"(report: {output_path})"
        )

    return 0 if summary["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
