#!/usr/bin/env python3
"""Issue #210 (seal-family W2) — Context companion integrity audit.

Independent, read-only verifier. Re-parses raw bucket bytes (never the
backfill's own report) and buckets every trace into exactly one of
``consistent`` / ``legitimately_empty`` / ``inconsistent``. See
``opentraces.core.context_companions_audit`` for the engine; this file is
the thin CLI + the frozen RED-baseline / GREEN-check entrypoint named in
the issue.

Usage:

    PYTHONPATH=src:packages/opentraces-schema/src \\
      python3 scripts/audit_context_companions.py [--bucket-root PATH] [--json] [--sample N]

Defaults to ``~/.opentraces/bucket``. A skip (bucket/mirror unreadable) is
an error (non-zero exit), never a silent pass.

Also directly importable by pytest:

    from scripts.audit_context_companions import run_and_print  # or import
    the engine functions from opentraces.core.context_companions_audit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (_REPO_ROOT / "src", _REPO_ROOT / "packages" / "opentraces-schema" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from opentraces.core.context_companions_audit import audit_bucket  # noqa: E402


def default_bucket_root() -> Path:
    return Path.home() / ".opentraces" / "bucket"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bucket-root",
        type=Path,
        default=None,
        help="bucket root to audit (default: ~/.opentraces/bucket)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON summary")
    parser.add_argument(
        "--sample",
        type=int,
        default=10,
        help="how many inconsistent trace ids to print (text mode only)",
    )
    args = parser.parse_args(argv)

    bucket_root = args.bucket_root or default_bucket_root()

    try:
        report = audit_bucket(bucket_root)
    except (FileNotFoundError, ValueError) as exc:
        print(f"AUDIT ERROR (blocking, not a pass): {exc}", file=sys.stderr)
        return 2

    if args.json:
        payload = {
            "bucket_root": str(report.bucket_root),
            "counts": report.counts(),
            "inconsistent_trace_ids": [t.trace_id for t in report.inconsistent_traces()],
        }
        print(json.dumps(payload, indent=2))
        return 0 if report.inconsistent == 0 else 1

    print(f"=== Context companion audit: {report.bucket_root} ===")
    print(f"total traces:        {report.total_traces}")
    print(f"consistent:          {report.consistent}")
    print(f"legitimately_empty:  {report.legitimately_empty}")
    print(f"inconsistent:        {report.inconsistent}")
    inc = report.inconsistent_traces()
    if inc:
        print(f"\nfirst {min(args.sample, len(inc))} inconsistent traces:")
        for t in inc[: args.sample]:
            print(f"  - {t.trace_id} ({t.project_slug}): {'; '.join(t.reasons)}")
    return 0 if report.inconsistent == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
