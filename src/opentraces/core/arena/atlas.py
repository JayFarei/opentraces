"""Honest, regenerable atlas projection over stored bench results."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any


ATLAS_SCHEMA_VERSION = "opentraces.arena.atlas.v0"
BLACK_BOX_REVIEW_VALUES = frozenset({"confirmed", "rejected", "unreviewed"})
INACTIVE_HOLE_STATES = ("no-red-proof", "unrepresentative-world")


class AtlasIntegrityError(ValueError):
    """An atlas row does not resolve to the stored result it names."""


def _object(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _latest(results: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    return max(
        results,
        key=lambda result: (
            str(result.get("started_at") or ""),
            str(result.get("run_id") or ""),
        ),
    )


def _verifier_matches(
    result: Mapping[str, Any], *, name: str, digest: str
) -> bool:
    for verifier in result.get("verifiers") or []:
        row = _object(verifier)
        source_ref = _object(row.get("source_ref"))
        if row.get("name") == name and source_ref.get("digest") == digest:
            return True
    return False


def _row_state(
    result: Mapping[str, Any],
    *,
    verifier_name: str,
    verifier_digest: str,
    product_commit: str,
    capabilities_digest: str,
) -> str:
    pins = _object(result.get("pins"))
    product = _object(pins.get("product"))
    capabilities = _object(pins.get("capabilities"))
    evidence = _object(result.get("evidence"))
    if product.get("commit") != product_commit:
        return "stale-run"
    if not _verifier_matches(
        result, name=verifier_name, digest=verifier_digest
    ):
        return "stale-verifier"
    if capabilities.get("digest") != capabilities_digest:
        return "surface-drift"
    if (
        result.get("execution_status") != "complete"
        or result.get("verdict") != "pass"
        or evidence.get("complete") is not True
    ):
        return "failing"
    return "proven"


def build_atlas(
    *,
    guarantees: Iterable[Mapping[str, Any]],
    results: Iterable[Mapping[str, Any]],
    product_commit: str,
    capabilities_digest: str,
) -> dict[str, Any]:
    """Project guarantee rows from exact stored results, never authored status."""

    results_by_nodeid: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    result_rows = list(results)
    for result in result_rows:
        scenario = _object(result.get("scenario"))
        nodeid = scenario.get("nodeid")
        run_id = result.get("run_id")
        if not isinstance(nodeid, str) or not nodeid:
            raise AtlasIntegrityError("stored result has no scenario nodeid")
        if not isinstance(run_id, str) or not run_id:
            raise AtlasIntegrityError("stored result has no run_id")
        results_by_nodeid[nodeid].append(result)

    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for guarantee in sorted(guarantees, key=lambda row: str(row.get("id") or "")):
        guarantee_id = guarantee.get("id")
        claim = guarantee.get("claim")
        nodeid = guarantee.get("nodeid")
        verifier = _object(guarantee.get("verifier"))
        review = guarantee.get("black_box_review")
        if not all(isinstance(value, str) and value for value in (guarantee_id, claim, nodeid)):
            raise AtlasIntegrityError("guarantee id, claim, and nodeid must be non-empty")
        if guarantee_id in seen_ids:
            raise AtlasIntegrityError(f"duplicate guarantee id: {guarantee_id}")
        seen_ids.add(guarantee_id)
        if review not in BLACK_BOX_REVIEW_VALUES:
            raise AtlasIntegrityError(f"invalid black_box_review for {guarantee_id}")
        verifier_name = verifier.get("name")
        verifier_digest = verifier.get("digest")
        if not all(
            isinstance(value, str) and value
            for value in (verifier_name, verifier_digest)
        ):
            raise AtlasIntegrityError(f"guarantee {guarantee_id} has no verifier pin")

        candidates = results_by_nodeid.get(nodeid, [])
        if not candidates:
            rows.append(
                {
                    "id": guarantee_id,
                    "claim": claim,
                    "nodeid": nodeid,
                    "state": "unbound",
                    "latest_run_id": None,
                    "verdict": None,
                    "evidence_ref": None,
                    "black_box_review": review,
                }
            )
            continue

        latest = _latest(candidates)
        run_id = str(latest["run_id"])
        rows.append(
            {
                "id": guarantee_id,
                "claim": claim,
                "nodeid": nodeid,
                "state": _row_state(
                    latest,
                    verifier_name=str(verifier_name),
                    verifier_digest=str(verifier_digest),
                    product_commit=product_commit,
                    capabilities_digest=capabilities_digest,
                ),
                "latest_run_id": run_id,
                "verdict": latest.get("verdict"),
                "evidence_ref": f"runs/v1/{run_id}/result.json",
                "black_box_review": review,
            }
        )

    return {
        "schema_version": ATLAS_SCHEMA_VERSION,
        "product_commit": product_commit,
        "capabilities_digest": capabilities_digest,
        "inactive_hole_states": list(INACTIVE_HOLE_STATES),
        "rows": rows,
    }


def cross_check_atlas(
    atlas: Mapping[str, Any], *, results: Iterable[Mapping[str, Any]]
) -> bool:
    """Fail closed unless every bound row resolves to its exact result."""

    if atlas.get("schema_version") != ATLAS_SCHEMA_VERSION:
        raise AtlasIntegrityError("unsupported atlas schema_version")
    results_by_id = {str(result.get("run_id")): result for result in results}
    for raw_row in atlas.get("rows") or []:
        row = _object(raw_row)
        run_id = row.get("latest_run_id")
        state = row.get("state")
        if state == "unbound":
            if run_id is not None or row.get("evidence_ref") is not None:
                raise AtlasIntegrityError("unbound row carries stored-run evidence")
            continue
        if not isinstance(run_id, str) or run_id not in results_by_id:
            raise AtlasIntegrityError(f"atlas row does not resolve to stored run {run_id}")
        result = results_by_id[run_id]
        scenario = _object(result.get("scenario"))
        if scenario.get("nodeid") != row.get("nodeid"):
            raise AtlasIntegrityError(f"atlas row {row.get('id')} resolves to the wrong scenario")
        expected_ref = f"runs/v1/{run_id}/result.json"
        if row.get("evidence_ref") != expected_ref:
            raise AtlasIntegrityError(f"atlas row {row.get('id')} has a false evidence ref")
    return True
