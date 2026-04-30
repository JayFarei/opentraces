"""Calibration validator for the burst calibration corpus v1.

Cluster H — runs ``detect_bursts`` against each hand-labeled trace in
``tests/integration/fixtures/burst_calibration_corpus/v1/`` and checks
the produced burst list matches the labels. The pass criterion is
≥ 80% accuracy on hand-labeled bursts (a "match" is the labeled
``step_range`` equals the produced ``step_range``).

The corpus has two label modes:

* ``expected_bursts`` — produced when running with default settings
  (T9 off). All traces have this label set.
* ``expected_bursts_with_t9`` — produced when running with T9 on. Only
  set where T9 changes the answer (currently trace_003).

The accuracy metric is computed across the union of (trace × labeled
burst) pairs in BOTH modes — so a heuristic that gets the default
mode right but loses the T9 mode would land at ~83% (5 default + 1
T9 = 6 total; 1 wrong = 5/6 ≈ 83%).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from opentraces_schema import TraceRecord

from opentraces.core.bursts import detect_bursts
from opentraces.core.trace_map import build_trace_map


CORPUS_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "burst_calibration_corpus"
    / "v1"
)
TRACES_DIR = CORPUS_DIR / "traces"
LABELS_PATH = CORPUS_DIR / "labels.json"


@pytest.fixture(scope="module")
def labels() -> dict[str, Any]:
    return json.loads(LABELS_PATH.read_text())


def _load_record(name: str) -> TraceRecord:
    path = TRACES_DIR / f"{name}.jsonl"
    return TraceRecord.model_validate_json(path.read_text().strip())


def _detected(record: TraceRecord, *, t9: bool) -> list[list[int]]:
    tm = build_trace_map(record)
    bursts = detect_bursts(
        tm.nodes,
        trace_record=record,
        commit_lookup=False,
        hard_split_on_user_pivot=t9,
    )
    return [list(b.step_range) for b in bursts]


def test_calibration_corpus_detection_accuracy_above_80pct(
    labels: dict[str, Any],
) -> None:
    """≥ 80% of hand-labeled bursts must be detected at the correct step_range.

    A "match" is: the labeled burst's ``step_range`` exactly equals a
    produced burst's ``step_range``. Spurious extra bursts the
    detector emits are NOT counted against accuracy here (they are
    reported in the per-trace breakdown for diagnosis).
    """
    total = 0
    matched = 0
    breakdown: list[dict[str, Any]] = []
    for trace_name, label_payload in labels.items():
        if trace_name.startswith("_"):
            continue
        record = _load_record(trace_name)
        # Default mode
        produced_default = _detected(record, t9=False)
        for expected in label_payload.get("expected_bursts", []):
            total += 1
            if list(expected["step_range"]) in produced_default:
                matched += 1
                breakdown.append(
                    {
                        "trace": trace_name,
                        "mode": "default",
                        "expected": expected["step_range"],
                        "match": True,
                    }
                )
            else:
                breakdown.append(
                    {
                        "trace": trace_name,
                        "mode": "default",
                        "expected": expected["step_range"],
                        "match": False,
                        "produced": produced_default,
                    }
                )
        # T9 mode (only where labels exist)
        t9_label = label_payload.get("expected_bursts_with_t9")
        if t9_label is not None:
            produced_t9 = _detected(record, t9=True)
            for expected in t9_label:
                total += 1
                if list(expected["step_range"]) in produced_t9:
                    matched += 1
                    breakdown.append(
                        {
                            "trace": trace_name,
                            "mode": "t9",
                            "expected": expected["step_range"],
                            "match": True,
                        }
                    )
                else:
                    breakdown.append(
                        {
                            "trace": trace_name,
                            "mode": "t9",
                            "expected": expected["step_range"],
                            "match": False,
                            "produced": produced_t9,
                        }
                    )

    assert total > 0, "No labeled bursts in corpus"
    accuracy = matched / total
    detail = "\n".join(
        f"  {row['trace']} [{row['mode']}]: expected={row['expected']} "
        f"match={row['match']}"
        + (f" produced={row.get('produced')}" if not row["match"] else "")
        for row in breakdown
    )
    assert accuracy >= 0.80, (
        f"Calibration accuracy {accuracy:.1%} ({matched}/{total}) "
        f"below 80% threshold.\n{detail}"
    )


def test_calibration_trace_001_one_burst_default() -> None:
    record = _load_record("trace_001_single_burst_clean")
    bursts = _detected(record, t9=False)
    assert bursts == [[5, 12]]


def test_calibration_trace_002_two_bursts_default() -> None:
    record = _load_record("trace_002_two_distinct_bursts")
    bursts = _detected(record, t9=False)
    assert bursts == [[5, 10], [80, 85]]


def test_calibration_trace_003_one_burst_default_two_with_t9() -> None:
    """T9 is the only thing that should split this trace."""
    record = _load_record("trace_003_long_burst_with_user_pivot")
    assert _detected(record, t9=False) == [[5, 25]]
    assert _detected(record, t9=True) == [[5, 10], [20, 25]]


def test_calibration_trace_004_sparse_widens_via_adaptive_gap() -> None:
    """3 edits across 200 steps must coalesce into 1 burst (adaptive gap)."""
    record = _load_record("trace_004_sparse_edits")
    bursts = _detected(record, t9=False)
    assert bursts == [[50, 200]], (
        f"Sparse trace fragmented: got {bursts}; adaptive gap should widen."
    )


def test_calibration_trace_005_dense_stays_one_burst() -> None:
    """30 dense edits must stay as one burst (the gap floor protects this)."""
    record = _load_record("trace_005_dense_loop")
    bursts = _detected(record, t9=False)
    assert len(bursts) == 1, f"Dense trace fragmented: got {bursts}"
    assert bursts[0][0] == 5
    assert bursts[0][1] >= 49
