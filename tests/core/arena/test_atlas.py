from __future__ import annotations

import pytest

from opentraces.core.arena.atlas import (
    AtlasIntegrityError,
    build_atlas as _build_atlas,
    cross_check_atlas as _cross_check_atlas,
)


CAPABILITIES_DIGEST = "sha256:current-surface"
PRODUCT_COMMIT = "product-current"
GUARANTEES_DIGEST = "sha256:" + "d" * 64


def _result(
    *,
    run_id: str,
    nodeid: str,
    verdict: str = "pass",
    evidence_complete: bool = True,
    product_commit: str = PRODUCT_COMMIT,
    capabilities_digest: str = CAPABILITIES_DIGEST,
    verifier_digest: str = "sha256:verifier-v1",
    verifier_status: str | None = None,
) -> dict[str, object]:
    evidence_refs = ["artifacts/proof.json"]
    return {
        "run_id": run_id,
        "started_at": "2026-07-15T12:00:00Z",
        "scenario": {"nodeid": nodeid, "claim": f"claim for {nodeid}"},
        "verdict": verdict,
        "execution_status": "complete",
        "evidence": {
            "complete": evidence_complete,
            "requirements": [
                {
                    "name": "verify_world",
                    "complete": evidence_complete,
                    "evidence_refs": evidence_refs,
                }
            ],
        },
        "recordings": {"rewatchable": False},
        "pins": {
            "product": {"commit": product_commit},
            "capabilities": {"digest": capabilities_digest},
        },
        "verifiers": [
            {
                "name": "verify_world",
                "source_ref": {"digest": verifier_digest},
                "status": verifier_status or verdict,
                "evidence_refs": evidence_refs,
            }
        ],
    }


def _storage_integrity(results: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {
        str(result["run_id"]): {
            "verified": True,
            "result_digest": "sha256:" + "1" * 64,
            "integrity_digest": "sha256:" + "2" * 64,
        }
        for result in results
    }


def build_atlas(**kwargs):
    results = list(kwargs["results"])
    return _build_atlas(
        **{**kwargs, "results": results},
        storage_integrity_by_run_id=_storage_integrity(results),
    )


def cross_check_atlas(atlas, **kwargs):
    results = list(kwargs["results"])
    return _cross_check_atlas(
        atlas,
        **{**kwargs, "results": results},
        storage_integrity_by_run_id=_storage_integrity(results),
    )


def _guarantee(guarantee_id: str, nodeid: str) -> dict[str, object]:
    return {
        "id": guarantee_id,
        "claim": f"claim for {nodeid}",
        "nodeid": nodeid,
        "verifier": {"name": "verify_world", "digest": "sha256:verifier-v1"},
        "black_box_review": "unreviewed",
    }


def test_unbound_guarantee_is_a_hole_and_never_green() -> None:
    atlas = build_atlas(
        guarantees=[_guarantee("remote-emulator", "scenario::remote")],
        guarantees_digest=GUARANTEES_DIGEST,
        results=[],
        product_commit=PRODUCT_COMMIT,
        capabilities_digest=CAPABILITIES_DIGEST,
    )

    assert atlas["rows"] == [
        {
            "id": "remote-emulator",
            "claim": "claim for scenario::remote",
            "nodeid": "scenario::remote",
            "verifier": {
                "name": "verify_world",
                "digest": "sha256:verifier-v1",
            },
            "state": "unbound",
            "latest_run_id": None,
            "verdict": None,
            "evidence_ref": None,
            "started_at": None,
            "evidence_complete": None,
            "rewatchable": None,
            "storage_integrity": None,
            "black_box_review": "unreviewed",
        }
    ]
    assert atlas["inactive_hole_states"] == ["no-red-proof", "unrepresentative-world"]
    assert (
        cross_check_atlas(
            atlas,
            guarantees=[_guarantee("remote-emulator", "scenario::remote")],
            guarantees_digest=GUARANTEES_DIGEST,
            results=[],
        )
        is True
    )


@pytest.mark.parametrize(
    ("result_overrides", "expected_state"),
    [
        ({"product_commit": "old-product"}, "stale-run"),
        ({"verifier_digest": "sha256:old-verifier"}, "stale-verifier"),
        ({"capabilities_digest": "sha256:old-surface"}, "surface-drift"),
        ({"verdict": "fail"}, "failing"),
        ({"evidence_complete": False}, "failing"),
    ],
)
def test_atlas_names_every_non_green_state(
    result_overrides: dict[str, object], expected_state: str
) -> None:
    result = _result(run_id="run-one", nodeid="scenario::one", **result_overrides)
    atlas = build_atlas(
        guarantees=[_guarantee("one", "scenario::one")],
        guarantees_digest=GUARANTEES_DIGEST,
        results=[result],
        product_commit=PRODUCT_COMMIT,
        capabilities_digest=CAPABILITIES_DIGEST,
    )

    assert atlas["rows"][0]["state"] == expected_state
    assert atlas["rows"][0]["state"] != "proven"
    assert (
        cross_check_atlas(
            atlas,
            guarantees=[_guarantee("one", "scenario::one")],
            guarantees_digest=GUARANTEES_DIGEST,
            results=[result],
        )
        is True
    )


def test_proven_row_is_pinned_to_the_exact_stored_result() -> None:
    result = _result(run_id="run-green", nodeid="scenario::green")
    atlas = build_atlas(
        guarantees=[_guarantee("green", "scenario::green")],
        guarantees_digest=GUARANTEES_DIGEST,
        results=[result],
        product_commit=PRODUCT_COMMIT,
        capabilities_digest=CAPABILITIES_DIGEST,
    )

    row = atlas["rows"][0]
    assert row["state"] == "proven"
    assert row["latest_run_id"] == "run-green"
    assert row["evidence_ref"] == "runs/v1/run-green/result.json"
    assert (
        cross_check_atlas(
            atlas,
            guarantees=[_guarantee("green", "scenario::green")],
            guarantees_digest=GUARANTEES_DIGEST,
            results=[result],
        )
        is True
    )

    row["latest_run_id"] = "run-missing"
    with pytest.raises(AtlasIntegrityError, match="latest_run_id"):
        cross_check_atlas(
            atlas,
            guarantees=[_guarantee("green", "scenario::green")],
            guarantees_digest=GUARANTEES_DIGEST,
            results=[result],
        )


def test_matching_failed_verifier_cannot_be_projected_as_proven() -> None:
    result = _result(
        run_id="run-lying-green",
        nodeid="scenario::lying-green",
        verdict="pass",
        evidence_complete=True,
        verifier_status="fail",
    )
    guarantees = [_guarantee("lying-green", "scenario::lying-green")]

    atlas = build_atlas(
        guarantees=guarantees,
        guarantees_digest=GUARANTEES_DIGEST,
        results=[result],
        product_commit=PRODUCT_COMMIT,
        capabilities_digest=CAPABILITIES_DIGEST,
    )

    assert atlas["rows"][0]["state"] == "failing"
    assert cross_check_atlas(
        atlas,
        guarantees=guarantees,
        guarantees_digest=GUARANTEES_DIGEST,
        results=[result],
    )


@pytest.mark.parametrize(
    ("field", "forged"),
    [
        ("state", "proven"),
        ("verdict", "pass"),
        ("claim", "A forged claim replaces the stored claim."),
    ],
)
def test_cross_check_recomputes_every_truth_bearing_row_field(field: str, forged: str) -> None:
    result = _result(run_id="run-red", nodeid="scenario::red", verdict="fail")
    atlas = build_atlas(
        guarantees=[_guarantee("red", "scenario::red")],
        guarantees_digest=GUARANTEES_DIGEST,
        results=[result],
        product_commit=PRODUCT_COMMIT,
        capabilities_digest=CAPABILITIES_DIGEST,
    )
    assert atlas["rows"][0]["state"] == "failing"

    atlas["rows"][0][field] = forged
    with pytest.raises(AtlasIntegrityError, match=field):
        cross_check_atlas(
            atlas,
            guarantees=[_guarantee("red", "scenario::red")],
            guarantees_digest=GUARANTEES_DIGEST,
            results=[result],
        )


def test_cross_check_rejects_forged_review_against_canonical_guarantees() -> None:
    guarantee = _guarantee("red", "scenario::red")
    result = _result(run_id="run-red", nodeid="scenario::red", verdict="fail")
    atlas = build_atlas(
        guarantees=[guarantee],
        guarantees_digest=GUARANTEES_DIGEST,
        results=[result],
        product_commit=PRODUCT_COMMIT,
        capabilities_digest=CAPABILITIES_DIGEST,
    )
    atlas["rows"][0]["black_box_review"] = "confirmed"

    with pytest.raises(AtlasIntegrityError, match="black_box_review"):
        cross_check_atlas(
            atlas,
            results=[result],
            guarantees=[guarantee],
            guarantees_digest=GUARANTEES_DIGEST,
        )


def test_remote_and_x86_coverage_ship_as_explicit_unbound_rows() -> None:
    atlas = build_atlas(
        guarantees=[
            _guarantee("remote-rented-glibc", "fleet::remote-rented"),
            _guarantee("emulator-linux-x86-64", "fleet::linux-x86-64"),
        ],
        guarantees_digest=GUARANTEES_DIGEST,
        results=[],
        product_commit=PRODUCT_COMMIT,
        capabilities_digest=CAPABILITIES_DIGEST,
    )

    assert [(row["id"], row["state"]) for row in atlas["rows"]] == [
        ("emulator-linux-x86-64", "unbound"),
        ("remote-rented-glibc", "unbound"),
    ]
