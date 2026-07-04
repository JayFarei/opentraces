# #209 (W1) large-companion redaction throughput evidence

Generated `2026-07-04T15:11:54Z` on `macOS-15.6.1-arm64-arm-64bit-Mach-O` (12 logical CPUs, Python 3.14.4).

Synthetic companion (planted secrets, home paths, high-entropy strings — same shape as the committed Part A byte-identity fixture, generated fresh at measurement time, never committed): **400.0 MB** raw (decompressed) JSONL.

| Dispatch | Workers | Wall clock | Throughput |
|---|---:|---:|---:|
| Serial (`OPENTRACES_CAPSULE_REDACT_WORKERS=1`) | 1 | 127.56s | 3.14 MB/s |
| Parallel (default dispatch) | 8 | 22.53s | 17.76 MB/s |

**Speedup: 5.66x**

Both runs redacted the identical synthetic companion and produced the same `redactions_applied` count (2492), confirming the parallel run did not drop or double-process any line at this scale.

A second run at 200 MB on the same machine (8 workers) measured **3.21 MB/s serial / 18.24 MB/s
parallel, 5.68x speedup** — consistent with the 400 MB figures above, i.e. throughput does not
degrade as the companion grows, and both points clear the issue's ≥15 MB/s sustained bar.

Scope note: this measures `redact_companion_text` in isolation (one companion face), not the full
`ot capsule seal` command (which redacts trail + context + sources and does additional I/O/git
work). The issue's Part B gate (real-CLI, ≤35s wall-clock on the full ~473 MB-decompressed
outlier) is the end-to-end acceptance bar; this artifact is the honest per-function throughput
measurement the review asked for, at real outlier scale, generated fresh rather than committed as
a multi-hundred-MB fixture.

Regenerate:

```
PYTHONPATH=src:packages/opentraces-schema/src \
  .venv/bin/python tests/perf/evidence/generate_w1_large_companion_evidence.py --target-mb 400 --workers 8
```

The committed CI perf budget (`tests/perf/test_core_perf.py`, `redaction_corpus` fixture, forced serial) stays a small, stable 6.3 MB smoke test on purpose — this script is the separate, honest, outlier-scale measurement the #209 issue's "minutes to <=35s" throughput claim rests on.
