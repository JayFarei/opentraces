from __future__ import annotations

from opentraces.core.arena.atlas_views import (
    build_agent_summary,
    format_pr_evidence_link,
    query_atlas,
)


def _atlas() -> dict[str, object]:
    return {
        "schema_version": "opentraces.arena.atlas.v0",
        "rows": [
            {
                "id": "auth",
                "claim": "Browser authentication reaches the CLI identity.",
                "state": "proven",
                "latest_run_id": "run-auth-green",
                "verdict": "pass",
                "evidence_ref": "runs/v1/run-auth-green/result.json",
                "started_at": "2026-07-15T11:00:00Z",
                "evidence_complete": True,
                "rewatchable": True,
                "storage_integrity": {
                    "verified": True,
                    "result_digest": "sha256:" + "1" * 64,
                    "integrity_digest": "sha256:" + "2" * 64,
                },
                "black_box_review": "confirmed",
            },
            {
                "id": "publish",
                "claim": "Dataset publication reaches the remote.",
                "state": "failing",
                "latest_run_id": "run-publish-red",
                "verdict": "fail",
                "evidence_ref": "runs/v1/run-publish-red/result.json",
                "started_at": "2026-07-15T12:00:00Z",
                "evidence_complete": False,
                "rewatchable": False,
                "storage_integrity": {
                    "verified": True,
                    "result_digest": "sha256:" + "3" * 64,
                    "integrity_digest": "sha256:" + "4" * 64,
                },
                "black_box_review": "unreviewed",
            },
            {
                "id": "remote-rented-glibc",
                "claim": "The emulator runs on a remote rented glibc box.",
                "state": "unbound",
                "latest_run_id": None,
                "verdict": None,
                "evidence_ref": None,
                "started_at": None,
                "evidence_complete": None,
                "rewatchable": None,
                "storage_integrity": None,
                "black_box_review": "unreviewed",
            },
        ],
    }


def test_agent_summary_answers_what_failed_and_where_from_summary_alone() -> None:
    summary = build_agent_summary(_atlas())

    assert summary == {
        "schema_version": "opentraces.arena.agent-summary.v0",
        "counts": {"failing": 1, "proven": 1, "unbound": 1},
        "failures": [
            {
                "id": "publish",
                "claim": "Dataset publication reaches the remote.",
                "state": "failing",
                "verdict": "fail",
                "run_id": "run-publish-red",
                "evidence_ref": "runs/v1/run-publish-red/result.json",
                "started_at": "2026-07-15T12:00:00Z",
                "evidence_complete": False,
                "rewatchable": False,
                "storage_integrity": {
                    "verified": True,
                    "result_digest": "sha256:" + "3" * 64,
                    "integrity_digest": "sha256:" + "4" * 64,
                },
            }
        ],
        "holes": [
            {
                "id": "remote-rented-glibc",
                "claim": "The emulator runs on a remote rented glibc box.",
                "state": "unbound",
                "run_id": None,
                "evidence_ref": None,
                "started_at": None,
                "evidence_complete": None,
                "rewatchable": None,
                "storage_integrity": None,
            }
        ],
    }


def test_agent_summary_keeps_machinery_errors_and_surface_drift_failures_once() -> None:
    atlas = _atlas()
    machinery_error = atlas["rows"][1]
    machinery_error["verdict"] = None
    atlas["rows"].append(
        {
            **machinery_error,
            "id": "publish-surface-drift",
            "state": "surface-drift",
            "verdict": "fail",
            "latest_run_id": "run-publish-drift-red",
            "evidence_ref": "runs/v1/run-publish-drift-red/result.json",
        }
    )

    failures = build_agent_summary(atlas)["failures"]

    assert [row["id"] for row in failures] == ["publish", "publish-surface-drift"]
    assert [row["evidence_ref"] for row in failures] == [
        "runs/v1/run-publish-red/result.json",
        "runs/v1/run-publish-drift-red/result.json",
    ]


def test_machine_query_filters_without_changing_row_truth() -> None:
    atlas = _atlas()

    assert [row["id"] for row in query_atlas(atlas)] == [
        "auth",
        "publish",
        "remote-rented-glibc",
    ]
    assert query_atlas(atlas, states={"failing"}) == [atlas["rows"][1]]
    assert query_atlas(atlas, guarantee_ids={"auth"}) == [atlas["rows"][0]]


def test_pr_evidence_link_names_the_exact_run_and_human_page() -> None:
    row = _atlas()["rows"][0]

    assert format_pr_evidence_link(
        row,
        page_url="https://evidence.example/runs/run-auth-green/page.html",
    ) == (
        "[bench evidence: run-auth-green]"
        "(https://evidence.example/runs/run-auth-green/page.html) "
        "(`runs/v1/run-auth-green/result.json`)"
    )
