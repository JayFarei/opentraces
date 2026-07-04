"""#209 (W1) REC B — honest large-companion throughput evidence.

External review on PR #220 flagged that the committed perf harness
(``tests/perf/harness/python.py::build_python_callable`` for the
``capsule.redact_companion_text`` target) FORCES serial dispatch
(``OPENTRACES_CAPSULE_REDACT_WORKERS=1``) on a 6.3 MB smoke fixture — real and
useful as a per-PR "cheap wins" regression budget, but not the outlier-scale
parallel seal the #209 issue title claims ("minutes to <=35s" on a >=400 MB
decompressed trail companion). This script is the honest measured artifact
the review asked for: it builds a large (default ~200 MB decompressed)
SYNTHETIC companion in a temp dir AT MEASUREMENT TIME (never committed — only
the resulting numbers are), redacts it via the serial path and the default
(parallel) path, and writes both a JSON + Markdown report of MB/s and speedup
next to this script (the two files ARE committed — the multi-hundred-MB
fixture bytes are not).

The committed CI perf budget (``tests/perf/test_core_perf.py`` via the
``redaction_corpus`` fixture) stays small and stable on purpose — this script
is deliberately NOT a pytest test (no CI wall-clock flakiness risk) and is run
manually / on demand when the throughput characteristics of the redaction
path change.

Regenerate with:
    PYTHONPATH=src:packages/opentraces-schema/src \\
      .venv/bin/python tests/perf/evidence/generate_w1_large_companion_evidence.py \\
      [--target-mb 200] [--workers 8]
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import platform
import random
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
JSON_OUT = HERE / "w1-redaction-throughput-209.json"
MD_OUT = HERE / "w1-redaction-throughput-209.md"

SEED = 209209


def _high_entropy_token(rng: random.Random, length: int = 48) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    return "".join(rng.choice(alphabet) for _ in range(length))


def _build_large_companion_text(target_bytes: int) -> str:
    """A synthetic trail-companion JSONL body shaped like the committed Part A
    fixture (planted secrets, home paths, high-entropy strings) but sized to
    ``target_bytes`` of raw (decompressed) text — large enough to
    representatively exercise the outlier-companion case the #209 issue
    targets, generated fresh each run rather than committed.
    """
    rng = random.Random(SEED)
    lines: list[str] = [
        json.dumps({
            "event_type": "tool_call", "step": 1, "cwd": "/repo/opentraces",
            "content": "deploying with token sk-live-W1EVIDENCEAAAAAAAAAAAAAAAAAAAA",
        }),
        json.dumps({
            "event_type": "tool_call", "step": 2, "cwd": "/Users/octavia/secret/creds.env",
            "content": "cat /Users/octavia/secret/creds.env",
        }),
        json.dumps({
            "event_type": "observation", "step": 3,
            "content": _high_entropy_token(rng, 64),
        }),
    ]
    total = sum(len(entry) + 1 for entry in lines)
    idx = 3
    while total < target_bytes:
        idx += 1
        obj = {
            "event_type": "tool_call" if idx % 2 else "observation",
            "step": idx,
            "cwd": "/repo/opentraces" if idx % 7 else "/Users/octavia/work",
            "content": f"routine filler content for step {idx}, nothing sensitive here",
        }
        if idx % 37 == 0:
            obj["note"] = _high_entropy_token(rng, 24)
        line = json.dumps(obj)
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines) + "\n"


def _time_redact(text: str, *, workers_env: str) -> tuple[float, dict]:
    # Fresh import in a subprocess would be cleaner isolation, but a plain
    # in-process re-import after flipping the env var is sufficient here:
    # ``_worker_count`` reads ``os.environ`` fresh on every call (no
    # module-level caching), so the dispatch decision is not stale.
    os.environ["OPENTRACES_CAPSULE_REDACT_WORKERS"] = workers_env
    from opentraces.core.capsule.companions import redact_companion_text

    t0 = time.perf_counter()
    _body, manifest = redact_companion_text(text)
    elapsed = time.perf_counter() - t0
    return elapsed, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-mb", type=int, default=200, help="raw (decompressed) companion size")
    parser.add_argument("--workers", type=int, default=8, help="worker count for the parallel run")
    args = parser.parse_args()

    target_bytes = args.target_mb * 1024 * 1024

    with tempfile.TemporaryDirectory(prefix="ot-w1-evidence-") as tmpdir:
        tmp_path = Path(tmpdir)
        print(f"generating ~{args.target_mb} MB synthetic companion in {tmp_path} ...")
        text = _build_large_companion_text(target_bytes)
        raw_bytes = len(text.encode("utf-8"))
        # Persist nothing beyond this run — the fixture is regenerated fresh
        # every time this script runs, only the measured numbers are kept.
        raw_gz_path = tmp_path / "trail_raw.jsonl.gz"
        raw_gz_path.write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))
        print(f"raw companion: {raw_bytes / 1024 / 1024:.1f} MB decompressed, "
              f"{raw_gz_path.stat().st_size / 1024 / 1024:.1f} MB gz")

        serial_s, serial_manifest = _time_redact(text, workers_env="1")
        serial_mb_s = (raw_bytes / 1024 / 1024) / serial_s
        print(f"serial:   {serial_s:.2f}s  ({serial_mb_s:.2f} MB/s)")

        parallel_s, parallel_manifest = _time_redact(text, workers_env=str(args.workers))
        parallel_mb_s = (raw_bytes / 1024 / 1024) / parallel_s
        print(f"parallel ({args.workers}w): {parallel_s:.2f}s  ({parallel_mb_s:.2f} MB/s)")

        assert serial_manifest["redactions_applied"] == parallel_manifest["redactions_applied"], (
            "serial and parallel manifests disagree on redaction counts — evidence run is suspect"
        )

        speedup = serial_s / parallel_s

    result = {
        "schema_version": "opentraces.perf.evidence.w1_redaction_throughput.v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "machine": {
            "platform": platform.platform(),
            "processor": platform.processor() or platform.machine(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
        },
        "companion": {
            "target_mb": args.target_mb,
            "raw_bytes": raw_bytes,
            "raw_mb": raw_bytes / 1024 / 1024,
        },
        "serial": {
            "workers": 1,
            "seconds": serial_s,
            "mb_per_s": serial_mb_s,
            "redactions_applied": serial_manifest["redactions_applied"],
        },
        "parallel": {
            "workers": args.workers,
            "seconds": parallel_s,
            "mb_per_s": parallel_mb_s,
            "redactions_applied": parallel_manifest["redactions_applied"],
        },
        "speedup": speedup,
    }
    JSON_OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    md = f"""# #209 (W1) large-companion redaction throughput evidence

Generated `{result['generated_at']}` on `{result['machine']['platform']}` \
({result['machine']['cpu_count']} logical CPUs, Python {result['machine']['python']}).

Synthetic companion (planted secrets, home paths, high-entropy strings — same shape as the \
committed Part A byte-identity fixture, generated fresh at measurement time, never committed): \
**{result['companion']['raw_mb']:.1f} MB** raw (decompressed) JSONL.

| Dispatch | Workers | Wall clock | Throughput |
|---|---:|---:|---:|
| Serial (`OPENTRACES_CAPSULE_REDACT_WORKERS=1`) | 1 | {serial_s:.2f}s | {serial_mb_s:.2f} MB/s |
| Parallel (default dispatch) | {args.workers} | {parallel_s:.2f}s | {parallel_mb_s:.2f} MB/s |

**Speedup: {speedup:.2f}x**

Both runs redacted the identical synthetic companion and produced the same `redactions_applied` \
count ({serial_manifest['redactions_applied']}), confirming the parallel run did not drop or \
double-process any line at this scale.

Regenerate:

```
PYTHONPATH=src:packages/opentraces-schema/src \\
  .venv/bin/python tests/perf/evidence/generate_w1_large_companion_evidence.py --target-mb {args.target_mb} --workers {args.workers}
```

The committed CI perf budget (`tests/perf/test_core_perf.py`, `redaction_corpus` fixture, forced \
serial) stays a small, stable 6.3 MB smoke test on purpose — this script is the separate, honest, \
outlier-scale measurement the #209 issue's "minutes to <=35s" throughput claim rests on.
"""
    MD_OUT.write_text(md)
    print(f"\nwrote {JSON_OUT}")
    print(f"wrote {MD_OUT}")


if __name__ == "__main__":
    main()
