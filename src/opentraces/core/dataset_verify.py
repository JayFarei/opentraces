"""``dataset verify`` — replay the seal and grade its explainability (#193).

A dataset is a growing, reviewed seal whose per-row provenance carries the full
contract triple (workflow@digest + bucket_state@digest + answers). This module
ENFORCES that contract: it re-executes the bound workflow against the bucket in
a side-effect-free mode (recorded answers make a judgment workflow
deterministic — ADR-0008 §1: judgment is an INPUT, not an exception), projects
the re-run rows through the SAME sanitize->validate->dedup->canonical transform
an append would apply, and BYTE-compares them against the stored public rows.

The outcome is classified into exactly three honest verdicts (never row
counts):

* ``reproduces`` — the re-run rows are byte-identical to ``data/train.jsonl``.
* ``bucket-advanced`` — the stored rows are a strict subset of the re-run and
  the bucket watermark moved past the recorded one; the delta is enumerated
  (reuses the #192 watermark). An EXPLAINED difference, so exit 0.
* ``integrity-failure`` — a stored row was hand-mutated (its bytes no longer
  hash to the recorded ``payload_hash``) OR the rows differ with no watermark
  explanation. Exit non-zero.

Side-effect-free by construction: the re-run writes to a throwaway run dir and
NOTHING is appended, no cursor/watermark is advanced.
"""

from __future__ import annotations

import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .datasets import (
    bucket_watermark,
    load_dataset,
    read_row_index,
    read_rows_by_id,
    reproduce_train_lines,
    row_payload_hash,
)

VERIFY_SCHEMA_VERSION = "opentraces.dataset.verify.v1"

VERDICT_REPRODUCES = "reproduces"
VERDICT_BUCKET_ADVANCED = "bucket-advanced"
VERDICT_INTEGRITY_FAILURE = "integrity-failure"

# ``dataset verify`` exit codes (documented, distinct from the publish family):
# 0 for an explained outcome (reproduces / bucket-advanced), a dedicated
# non-zero for an integrity failure.
INTEGRITY_FAILURE_EXIT_CODE = 7


@dataclass(frozen=True)
class DatasetVerifyResult:
    dataset_name: str
    verdict: str
    stored_row_count: int
    reproduced_row_count: int
    byte_identical: bool
    detail: str
    delta: list[str] = field(default_factory=list)
    mutated_rows: list[dict[str, Any]] = field(default_factory=list)
    recorded_watermark: dict[str, Any] | None = None
    current_watermark: dict[str, Any] | None = None

    @property
    def exit_code(self) -> int:
        return (
            INTEGRITY_FAILURE_EXIT_CODE
            if self.verdict == VERDICT_INTEGRITY_FAILURE
            else 0
        )


def _cursor_entry(dataset) -> dict[str, Any]:
    cursors_path = dataset.path / ".opentraces" / "cursors.yaml"
    if not cursors_path.exists():
        return {}
    try:
        data = yaml.safe_load(cursors_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    query = dataset.manifest.candidate_query
    query_name = query.name if query else "default"
    entry = (data.get("queries") or {}).get(query_name)
    return entry if isinstance(entry, dict) else {}


def _recorded_watermark(dataset) -> dict[str, Any] | None:
    watermark = _cursor_entry(dataset).get("watermark")
    return watermark if isinstance(watermark, dict) else None


def _recorded_answers(dataset) -> dict[str, Any]:
    """Answers recorded by the last successful run (the #186 fourth input)."""
    from .datasets import _read_run_answers

    run_id = _cursor_entry(dataset).get("last_successful_run_id")
    if not isinstance(run_id, str) or not run_id:
        return {}
    return _read_run_answers(dataset, run_id)


def _recorded_privacy_tier(dataset) -> str | None:
    """The privacy tier the stored rows were sanitized at, from provenance."""
    from .datasets import read_row_provenance

    for provenance in read_row_provenance(dataset.name).values():
        privacy = provenance.get("privacy") if isinstance(provenance, dict) else None
        tier = privacy.get("privacy_tier") if isinstance(privacy, dict) else None
        if isinstance(tier, str) and tier:
            return tier
    return None


def _rerun_rows(dataset, answers: dict[str, Any]) -> list[dict[str, Any]]:
    """Re-execute the bound workflow into a THROWAWAY dir (no append/advance)."""
    from .workflow_runner import (
        _read_output_rows,
        _workflow_package_for_dataset,
        execute_workflow,
    )

    package = _workflow_package_for_dataset(dataset)
    scope: dict[str, Any] = {"scope": "all-projects"}
    query = dataset.manifest.candidate_query
    if query is not None:
        scope = {"scope": query.scope or "all-projects", "verify": True}
    with tempfile.TemporaryDirectory(prefix="ot-verify-") as tmp:
        run_dir = Path(tmp)
        output_path = run_dir / "output_rows.jsonl"
        execute_workflow(
            dataset.manifest.workflow.skill,
            scope=scope,
            output_path=output_path,
            executor="script",
            run_dir=run_dir,
            workflow_package=package,
            answers=answers or None,
            verify_digest=False,
        )
        return _read_output_rows(output_path)


def _multiset_subset(a: Counter, b: Counter) -> bool:
    """``True`` iff every line in ``a`` appears in ``b`` with at least its
    multiplicity — a genuine multiset ⊆, so a duplicated stored line is only a
    subset when the re-run produced at least as many copies."""
    return all(b[line] >= count for line, count in a.items())


def _multiset_delta(reproduced_lines: list[str], stored_counts: Counter) -> list[str]:
    """The re-run lines beyond what the stored multiset accounts for, in re-run
    order (the enumerated ``bucket-advanced`` delta)."""
    remaining = Counter(stored_counts)
    delta: list[str] = []
    for line in reproduced_lines:
        if remaining.get(line, 0) > 0:
            remaining[line] -= 1
        else:
            delta.append(line)
    return delta


def _watermark_advanced(
    recorded: dict[str, Any] | None, current: dict[str, Any] | None
) -> bool:
    if not current:
        return False
    if not recorded:
        # No baseline recorded (a pre-#192 dataset): any current bucket position
        # is treated as "ahead" so a strict-superset re-run reads as advanced.
        return True
    return recorded.get("manifest_digest") != current.get("manifest_digest")


def verify_dataset(name: str) -> DatasetVerifyResult:
    dataset = load_dataset(name)

    stored_lines = [
        line
        for line in (
            (dataset.path / "data" / "train.jsonl").read_text(encoding="utf-8")
            if (dataset.path / "data" / "train.jsonl").exists()
            else ""
        ).splitlines()
        if line.strip()
    ]

    # (1) Hand-mutation check — the most direct integrity signal, independent of
    # a re-run: a stored row whose bytes no longer hash to the recorded
    # payload_hash was mutated out-of-band.
    rows_by_id = read_rows_by_id(name)
    mutated: list[dict[str, Any]] = []
    for entry in read_row_index(name):
        stored_row = rows_by_id.get(entry.row_id)
        if stored_row is None:
            continue
        if row_payload_hash(stored_row) != entry.payload_hash:
            mutated.append(
                {
                    "row_id": entry.row_id,
                    "recorded_payload_hash": entry.payload_hash,
                    "observed_payload_hash": row_payload_hash(stored_row),
                }
            )

    recorded_wm = _recorded_watermark(dataset)
    current_wm = bucket_watermark()

    if mutated:
        return DatasetVerifyResult(
            dataset_name=name,
            verdict=VERDICT_INTEGRITY_FAILURE,
            stored_row_count=len(stored_lines),
            reproduced_row_count=0,
            byte_identical=False,
            detail=(
                f"{len(mutated)} stored row(s) were hand-mutated "
                "(payload_hash mismatch); the dataset seal is broken"
            ),
            mutated_rows=mutated,
            recorded_watermark=recorded_wm,
            current_watermark=current_wm,
        )

    # (2) Re-run the bound workflow with recorded answers and reproduce the
    # would-be public lines through the exact append transform.
    answers = _recorded_answers(dataset)
    raw_rows = _rerun_rows(dataset, answers)
    reproduced_lines = reproduce_train_lines(
        dataset, raw_rows, privacy_tier=_recorded_privacy_tier(dataset)
    )

    # #193 requires BYTE comparison, not set equality: ``reproduces`` demands
    # ordered byte-identity, and ``bucket-advanced`` uses a MULTISET subset so a
    # duplicated / reordered stored line cannot false-clean against a re-run that
    # dedupes by identity.
    stored_counts = Counter(stored_lines)
    reproduced_counts = Counter(reproduced_lines)
    byte_identical = reproduced_lines == stored_lines

    if byte_identical:
        verdict = VERDICT_REPRODUCES
        detail = "re-run rows are byte-identical to the stored public rows"
        delta: list[str] = []
    elif _multiset_subset(stored_counts, reproduced_counts) and _watermark_advanced(
        recorded_wm, current_wm
    ):
        verdict = VERDICT_BUCKET_ADVANCED
        delta = _multiset_delta(reproduced_lines, stored_counts)
        detail = (
            f"stored rows reproduce; the bucket advanced past the recorded "
            f"watermark, adding {len(delta)} row(s)"
        )
    else:
        verdict = VERDICT_INTEGRITY_FAILURE
        delta = []
        detail = (
            "re-run rows differ from the stored rows with no watermark "
            "explanation; the dataset seal is broken"
        )

    return DatasetVerifyResult(
        dataset_name=name,
        verdict=verdict,
        stored_row_count=len(stored_lines),
        reproduced_row_count=len(reproduced_lines),
        byte_identical=byte_identical,
        detail=detail,
        delta=delta,
        recorded_watermark=recorded_wm,
        current_watermark=current_wm,
    )


def verify_envelope(result: DatasetVerifyResult) -> dict[str, Any]:
    """The frozen ``opentraces.dataset.verify.v1`` consumer envelope.

    ``status`` carries the verdict (satisfies the uniform envelope header), and
    ``schema_version`` freezes the shape — any later field/verdict change is a
    version bump.
    """
    return {
        "status": result.verdict,
        "schema_version": VERIFY_SCHEMA_VERSION,
        "dataset": result.dataset_name,
        "verdict": result.verdict,
        "stored_row_count": result.stored_row_count,
        "reproduced_row_count": result.reproduced_row_count,
        "byte_identical": result.byte_identical,
        "delta": result.delta,
        "mutated_rows": result.mutated_rows,
        "recorded_watermark": result.recorded_watermark,
        "current_watermark": result.current_watermark,
        "detail": result.detail,
    }
