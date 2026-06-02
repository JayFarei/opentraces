"""Search Evaluation Harness (plan 088).

A single harness that runs the progressive-discovery loop
(``trace query -> map -> slice -> get``) over a real-bucket-sized corpus and
emits both performance (latency / scaling-slope / boundedness) and outcome
(recall@k / MRR / chronological-order / recency-hit) metrics from one
bucket-derived query set.

Phase A (this module set): ``profiler`` (U0), ``generator`` (U1),
``runner`` + ``score_outcome`` + ``report`` (U2), perf/boundedness gate (U3).
The harness reuses ``tests/perf`` for measurement, the bucket/dataset
primitives for data, and ``tests/otbox`` for the standing snapshot-cached gate.
"""
