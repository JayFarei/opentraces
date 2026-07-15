from __future__ import annotations

import pytest

from opentraces.core.arena.atlas import AtlasIntegrityError, build_atlas, cross_check_atlas


CAPABILITIES_DIGEST = "sha256:current-surface"
PRODUCT_COMMIT = "product-current"


def _result(
    *,
    run_id: str,
    nodeid: str,
    verdict: str = "pass",
    evidence_complete: bool = True,
    product_commit: str = PRODUCT_COMMIT,
    capabilities_digest: str = CAPABILITIES_DIGEST,
    verifier_digest: str = "sha256:verifier-v1",
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "scenario": {"nodeid": nodeid, "claim": f"claim for {nodeid}"},
        "verdict": verdict,
        "execution_status": "complete",
        "evidence": {"complete": evidence_complete},
        "pins": {
            "product": {"commit": product_commit},
            "capabilities": {"digest": capabilities_digest},
        },
        "verifiers": [
            {
                "name": "verify_world",
                "source_ref": {"digest": verifier_digest},
                "status": verdict,
            }
        ],
    }


def _guarantee(guarantee_id: str, nodeid: str) -> dict[str, object]:
    return {
        "id": guarantee_id,
        "claim": f"guarantee {guarantee_id}",
        "nodeid": nodeid,
        "verifier": {"name": "verify_world", "digest": "sha256:verifier-v1"},
        "black_box_review": "unreviewed",
    }


def test_unbound_guarantee_is_a_hole_and_never_green() -> None:
    atlas = build_atlas(
        guarantees=[_guarantee("remote-emulator", "scenario::remote")],
        results=[],
        product_commit=PRODUCT_COMMIT,
        capabilities_digest=CAPABILITIES_DIGEST,
    )

    assert atlas["rows"] == [
        {
            "id": "remote-emulator",
            "claim": "guarantee remote-emulator",
            "nodeid": "scenario::remote",
            "state": "unbound",
            "latest_run_id": None,
            "verdict": None,
            "evidence_ref": None,
            "black_box_review": "unreviewed",
        }
    ]
    assert atlas["inactive_hole_states"] == ["no-red-proof", "unrepresentative-world"]
    assert cross_check_atlas(atlas, results=[]) is True


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
        results=[result],
        product_commit=PRODUCT_COMMIT,
        capabilities_digest=CAPABILITIES_DIGEST,
    )

    assert atlas["rows"][0]["state"] == expected_state
    assert atlas["rows"][0]["state"] != "proven"
    assert cross_check_atlas(atlas, results=[result]) is True


def test_proven_row_is_pinned_to_the_exact_stored_result() -> None:
    result = _result(run_id="run-green", nodeid="scenario::green")
    atlas = build_atlas(
        guarantees=[_guarantee("green", "scenario::green")],
        results=[result],
        product_commit=PRODUCT_COMMIT,
        capabilities_digest=CAPABILITIES_DIGEST,
    )

    row = atlas["rows"][0]
    assert row["state"] == "proven"
    assert row["latest_run_id"] == "run-green"
    assert row["evidence_ref"] == "runs/v1/run-green/result.json"
    assert cross_check_atlas(atlas, results=[result]) is True

    row["latest_run_id"] = "run-missing"
    with pytest.raises(AtlasIntegrityError, match="run-missing"):
        cross_check_atlas(atlas, results=[result])


def test_remote_and_x86_coverage_ship_as_explicit_unbound_rows() -> None:
    atlas = build_atlas(
        guarantees=[
            _guarantee("remote-rented-glibc", "fleet::remote-rented"),
            _guarantee("emulator-linux-x86-64", "fleet::linux-x86-64"),
        ],
        results=[],
        product_commit=PRODUCT_COMMIT,
        capabilities_digest=CAPABILITIES_DIGEST,
    )

    assert [(row["id"], row["state"]) for row in atlas["rows"]] == [
        ("emulator-linux-x86-64", "unbound"),
        ("remote-rented-glibc", "unbound"),
    ]
