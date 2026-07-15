"""Honest, regenerable atlas projection over stored bench results."""

from __future__ import annotations

import hashlib
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


def guarantees_source_digest(source: bytes) -> str:
    """Bind an atlas to the exact external guarantees source bytes."""

    return f"sha256:{hashlib.sha256(source).hexdigest()}"


def _require_guarantees_digest(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise AtlasIntegrityError("guarantees source digest must be sha256")
    hexadecimal = value.removeprefix("sha256:")
    if len(hexadecimal) != 64 or any(
        character not in "0123456789abcdef" for character in hexadecimal
    ):
        raise AtlasIntegrityError("guarantees source digest must be sha256")
    return value


def _validated_guarantees(
    guarantees: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for guarantee in guarantees:
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
        if not all(isinstance(value, str) and value for value in (verifier_name, verifier_digest)):
            raise AtlasIntegrityError(f"guarantee {guarantee_id} has no verifier pin")
        rows.append(
            {
                "id": guarantee_id,
                "claim": claim,
                "nodeid": nodeid,
                "verifier": {"name": verifier_name, "digest": verifier_digest},
                "black_box_review": review,
            }
        )
    return sorted(rows, key=lambda row: str(row["id"]))


def _latest(results: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    return max(
        results,
        key=lambda result: (
            str(result.get("started_at") or ""),
            str(result.get("run_id") or ""),
        ),
    )


def _verifier_matches(result: Mapping[str, Any], *, name: str, digest: str) -> bool:
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
    if not _verifier_matches(result, name=verifier_name, digest=verifier_digest):
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
    guarantees_digest: str,
    results: Iterable[Mapping[str, Any]],
    product_commit: str,
    capabilities_digest: str,
) -> dict[str, Any]:
    """Project guarantee rows from exact stored results, never authored status."""

    trusted_guarantees = _validated_guarantees(guarantees)
    trusted_digest = _require_guarantees_digest(guarantees_digest)
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
    for guarantee in trusted_guarantees:
        guarantee_id = str(guarantee["id"])
        claim = str(guarantee["claim"])
        nodeid = str(guarantee["nodeid"])
        verifier = _object(guarantee.get("verifier"))
        review = guarantee.get("black_box_review")
        verifier_name = str(verifier["name"])
        verifier_digest = str(verifier["digest"])

        candidates = results_by_nodeid.get(nodeid, [])
        if not candidates:
            rows.append(
                {
                    "id": guarantee_id,
                    "claim": claim,
                    "nodeid": nodeid,
                    "verifier": {
                        "name": verifier_name,
                        "digest": verifier_digest,
                    },
                    "state": "unbound",
                    "latest_run_id": None,
                    "verdict": None,
                    "evidence_ref": None,
                    "black_box_review": review,
                }
            )
            continue

        latest = _latest(candidates)
        scenario = _object(latest.get("scenario"))
        if scenario.get("claim") != claim:
            raise AtlasIntegrityError(
                f"guarantee {guarantee_id} claim differs from its stored result"
            )
        run_id = str(latest["run_id"])
        rows.append(
            {
                "id": guarantee_id,
                "claim": claim,
                "nodeid": nodeid,
                "verifier": {
                    "name": verifier_name,
                    "digest": verifier_digest,
                },
                "state": _row_state(
                    latest,
                    verifier_name=verifier_name,
                    verifier_digest=verifier_digest,
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
        "guarantees_digest": trusted_digest,
        "product_commit": product_commit,
        "capabilities_digest": capabilities_digest,
        "inactive_hole_states": list(INACTIVE_HOLE_STATES),
        "rows": rows,
    }


def cross_check_atlas(
    atlas: Mapping[str, Any],
    *,
    guarantees: Iterable[Mapping[str, Any]],
    guarantees_digest: str,
    results: Iterable[Mapping[str, Any]],
) -> bool:
    """Fail closed unless every bound row resolves to its exact result."""

    if atlas.get("schema_version") != ATLAS_SCHEMA_VERSION:
        raise AtlasIntegrityError("unsupported atlas schema_version")
    trusted_digest = _require_guarantees_digest(guarantees_digest)
    if atlas.get("guarantees_digest") != trusted_digest:
        raise AtlasIntegrityError("atlas guarantees_digest disagrees with canonical source")
    guarantee_rows = _validated_guarantees(guarantees)
    guarantees_by_id = {str(row["id"]): row for row in guarantee_rows}
    product_commit = atlas.get("product_commit")
    capabilities_digest = atlas.get("capabilities_digest")
    if not isinstance(product_commit, str) or not product_commit:
        raise AtlasIntegrityError("atlas product_commit is missing")
    if not isinstance(capabilities_digest, str) or not capabilities_digest:
        raise AtlasIntegrityError("atlas capabilities_digest is missing")
    result_rows = list(results)
    results_by_id = {str(result.get("run_id")): result for result in result_rows}
    if len(results_by_id) != len(result_rows):
        raise AtlasIntegrityError("stored results contain duplicate run ids")
    results_by_nodeid: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for result in result_rows:
        nodeid = _object(result.get("scenario")).get("nodeid")
        if not isinstance(nodeid, str) or not nodeid:
            raise AtlasIntegrityError("stored result has no scenario nodeid")
        results_by_nodeid[nodeid].append(result)

    rows = atlas.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise AtlasIntegrityError("atlas rows must be an array of objects")
    seen_ids: set[str] = set()
    for raw_row in rows:
        row = _object(raw_row)
        row_id = row.get("id")
        nodeid = row.get("nodeid")
        if not isinstance(row_id, str) or not row_id or row_id in seen_ids:
            raise AtlasIntegrityError("atlas row id is missing or duplicated")
        seen_ids.add(row_id)
        guarantee = guarantees_by_id.get(row_id)
        if guarantee is None:
            raise AtlasIntegrityError(f"atlas row {row_id} is absent from canonical guarantees")
        if not isinstance(nodeid, str) or not nodeid:
            raise AtlasIntegrityError(f"atlas row {row_id} nodeid is missing")
        for field in ("claim", "nodeid", "black_box_review"):
            if row.get(field) != guarantee.get(field):
                raise AtlasIntegrityError(
                    f"atlas row {row_id} {field} disagrees with canonical guarantees"
                )
        verifier = _object(row.get("verifier"))
        if verifier != guarantee.get("verifier"):
            raise AtlasIntegrityError(
                f"atlas row {row_id} verifier disagrees with canonical guarantees"
            )
        verifier_name = verifier.get("name")
        verifier_digest = verifier.get("digest")
        if not all(isinstance(value, str) and value for value in (verifier_name, verifier_digest)):
            raise AtlasIntegrityError(f"atlas row {row_id} verifier pin is missing")
        candidates = results_by_nodeid.get(nodeid, [])
        run_id = row.get("latest_run_id")
        state = row.get("state")
        if state == "unbound":
            if candidates:
                raise AtlasIntegrityError(f"atlas row {row_id} state disagrees with stored results")
            if run_id is not None or row.get("evidence_ref") is not None:
                raise AtlasIntegrityError("unbound row carries stored-run evidence")
            if row.get("verdict") is not None:
                raise AtlasIntegrityError(f"atlas row {row_id} verdict disagrees with unbound")
            continue
        if not candidates:
            raise AtlasIntegrityError(f"atlas row {row_id} state disagrees with stored results")
        latest = _latest(candidates)
        expected_run_id = str(latest.get("run_id"))
        if run_id != expected_run_id:
            raise AtlasIntegrityError(
                f"atlas row {row_id} latest_run_id disagrees with stored results"
            )
        if not isinstance(run_id, str) or run_id not in results_by_id:
            raise AtlasIntegrityError(f"atlas row does not resolve to stored run {run_id}")
        result = results_by_id[run_id]
        scenario = _object(result.get("scenario"))
        if scenario.get("nodeid") != nodeid:
            raise AtlasIntegrityError(f"atlas row {row_id} resolves to the wrong scenario")
        if row.get("claim") != scenario.get("claim"):
            raise AtlasIntegrityError(f"atlas row {row_id} claim disagrees with stored result")
        expected_ref = f"runs/v1/{run_id}/result.json"
        if row.get("evidence_ref") != expected_ref:
            raise AtlasIntegrityError(f"atlas row {row_id} has a false evidence ref")
        if row.get("verdict") != result.get("verdict"):
            raise AtlasIntegrityError(f"atlas row {row_id} verdict disagrees with stored result")
        expected_state = _row_state(
            result,
            verifier_name=str(verifier_name),
            verifier_digest=str(verifier_digest),
            product_commit=product_commit,
            capabilities_digest=capabilities_digest,
        )
        if state != expected_state:
            raise AtlasIntegrityError(f"atlas row {row_id} state disagrees with stored result")
    missing_rows = sorted(set(guarantees_by_id) - seen_ids)
    if missing_rows:
        raise AtlasIntegrityError(f"atlas is missing canonical guarantee row {missing_rows[0]}")
    return True
