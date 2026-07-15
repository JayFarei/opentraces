from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.arena.contract import build_result
from opentraces.core.arena.run_store import RunStore


def _finalize_run(
    store: RunStore,
    *,
    claim: str = "Stored evidence remains independently verifiable.",
    nodeid: str = "arena::stored",
    started_at: str = "2026-07-15T12:00:00Z",
    verdict: str = "pass",
    verifier_name: str | None = None,
    verifier_digest: str | None = None,
    product_commit: str | None = None,
    capabilities_digest: str | None = None,
) -> Path:
    draft = store.begin()
    draft.write_text("source/scenario.py", "def scenario(): pass\n")
    draft.write_json("source/source.json", {"nodeid": nodeid})
    draft.write_json("artifacts/observation.json", {"healthy": verdict == "pass"})
    verifiers = []
    if verifier_name is not None and verifier_digest is not None:
        verifiers.append(
            {
                "name": verifier_name,
                "source_ref": {
                    "path": "surface_verifier.py",
                    "digest": verifier_digest,
                },
                "status": verdict,
                "duration_ms": 0,
                "evidence_refs": ["artifacts/observation.json"],
                "reason": None,
            }
        )
    pins = {}
    if product_commit is not None:
        pins["product"] = {
            "commit": product_commit,
            "worktree": "clean",
            "dirty_diff_digest": None,
        }
    if capabilities_digest is not None:
        pins["capabilities"] = {"digest": capabilities_digest}
    return draft.finalize(
        build_result(
            run_id=draft.run_id,
            claim=claim,
            nodeid=nodeid,
            source_ref="source/scenario.py",
            execution_mode="direct",
            started_at=started_at,
            duration_ms=1,
            execution_status="complete",
            verdict=verdict,
            reason=None if verdict == "pass" else {"code": "assertion_failed", "message": "red"},
            verifiers=verifiers,
            evidence={"complete": True, "requirements": []},
            recordings={"rewatchable": False, "channels": []},
            artifacts=["artifacts/observation.json"],
            capture=None,
            pins=pins,
        )
    )


def test_bench_list_and_render_expose_verified_stored_runs_as_pure_json(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    finalized = _finalize_run(store)
    pending = store.begin()
    pending.write_text("artifacts/pending.txt", "not finalized")

    listed = CliRunner().invoke(
        main,
        ["bench", "list", "--store-root", str(store.root), "--json"],
    )
    rendered_path = tmp_path / "rendered" / "index.html"
    rendered = CliRunner().invoke(
        main,
        [
            "bench",
            "render",
            finalized.name,
            "--store-root",
            str(store.root),
            "--output",
            str(rendered_path),
            "--json",
        ],
    )

    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output) == {
        "count": 1,
        "runs": [
            {
                "claim": "Stored evidence remains independently verifiable.",
                "execution_status": "complete",
                "run_id": finalized.name,
                "started_at": "2026-07-15T12:00:00Z",
                "verdict": "pass",
            }
        ],
    }
    assert pending.run_id not in listed.output
    assert rendered.exit_code == 0, rendered.output
    assert json.loads(rendered.output) == {
        "page": str(rendered_path),
        "run_id": finalized.name,
        "status": "ok",
    }
    assert rendered_path.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_bench_reverify_requires_the_exact_callable_name_and_digest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module_path = tmp_path / "surface_verifier.py"
    module_path.write_text(
        "def verify_stored(evidence):\n"
        "    observed = evidence.read_json('artifacts/observation.json')\n"
        "    assert observed['healthy'] is True\n"
        "    return {'evidence_refs': ['artifacts/observation.json']}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module("surface_verifier")
    verifier_name = f"{module.verify_stored.__module__}.{module.verify_stored.__qualname__}"
    verifier_digest = f"sha256:{hashlib.sha256(module_path.read_bytes()).hexdigest()}"
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    finalized = _finalize_run(
        store,
        verifier_name=verifier_name,
        verifier_digest=verifier_digest,
    )

    invoked = CliRunner().invoke(
        main,
        [
            "bench",
            "reverify",
            finalized.name,
            "--store-root",
            str(store.root),
            "--verifier-name",
            verifier_name,
            "--verifier-digest",
            verifier_digest,
            "--json",
        ],
    )

    assert invoked.exit_code == 0, invoked.output
    assert json.loads(invoked.output) == {
        "evidence_refs": ["artifacts/observation.json"],
        "reason": None,
        "run_id": finalized.name,
        "schema_version": "opentraces.bench.reverification.v0",
        "status": "pass",
        "verifier": {"digest": verifier_digest, "name": verifier_name},
    }


def test_bench_reverify_refuses_invalid_run_before_importing_verifier(
    tmp_path: Path,
    monkeypatch,
) -> None:
    marker = tmp_path / "imported.txt"
    module_path = tmp_path / "side_effect_verifier.py"
    module_path.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n"
        "def verify_stored(evidence): return {'evidence_refs': []}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")

    invoked = CliRunner().invoke(
        main,
        [
            "bench",
            "reverify",
            "run_missing",
            "--store-root",
            str(store.root),
            "--verifier-name",
            "side_effect_verifier.verify_stored",
            "--verifier-digest",
            "sha256:" + "0" * 64,
            "--json",
        ],
    )

    assert invoked.exit_code != 0
    assert "missing result" in invoked.output
    assert not marker.exists(), "invalid stored evidence must fail before module import"


def test_bench_atlas_build_render_summary_query_and_pr_link_share_stored_truth(
    tmp_path: Path,
) -> None:
    product_commit = "a" * 40
    capabilities_digest = "sha256:" + "b" * 64
    verifier_digest = "sha256:" + "c" * 64
    verifier_name = "arena_guarantees.verify_publish"
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    older_green = _finalize_run(
        store,
        claim="Dataset publication reaches the remote.",
        nodeid="arena::publish",
        started_at="2026-07-14T23:00:00Z",
        verifier_name=verifier_name,
        verifier_digest=verifier_digest,
        product_commit=product_commit,
        capabilities_digest=capabilities_digest,
    )
    latest_red = _finalize_run(
        store,
        claim="Dataset publication reaches the remote.",
        nodeid="arena::publish",
        started_at="2026-07-15T01:00:00Z",
        verdict="fail",
        verifier_name=verifier_name,
        verifier_digest=verifier_digest,
        product_commit=product_commit,
        capabilities_digest=capabilities_digest,
    )
    guarantees_path = tmp_path / "guarantees.json"
    guarantees_path.write_text(
        json.dumps(
            {
                "guarantees": [
                    {
                        "id": "publish",
                        "claim": "Dataset publication reaches the remote.",
                        "nodeid": "arena::publish",
                        "verifier": {"name": verifier_name, "digest": verifier_digest},
                        "black_box_review": "unreviewed",
                    },
                    {
                        "id": "remote-rented-glibc",
                        "claim": "The emulator runs on a remote rented glibc box.",
                        "nodeid": "arena::remote-rented",
                        "verifier": {"name": verifier_name, "digest": verifier_digest},
                        "black_box_review": "unreviewed",
                    },
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    atlas_path = tmp_path / "atlas" / "atlas.json"
    runner = CliRunner()

    built = runner.invoke(
        main,
        [
            "bench",
            "atlas",
            "build",
            str(guarantees_path),
            "--store-root",
            str(store.root),
            "--product-commit",
            product_commit,
            "--capabilities-digest",
            capabilities_digest,
            "--output",
            str(atlas_path),
            "--json",
        ],
    )
    page_path = tmp_path / "atlas" / "index.html"
    rendered = runner.invoke(
        main,
        [
            "bench",
            "atlas",
            "render",
            str(atlas_path),
            "--output",
            str(page_path),
            "--json",
        ],
    )
    summarized = runner.invoke(
        main,
        ["bench", "atlas", "summary", str(atlas_path), "--json"],
    )
    queried = runner.invoke(
        main,
        ["bench", "atlas", "query", str(atlas_path), "--state", "failing", "--json"],
    )
    linked = runner.invoke(
        main,
        [
            "bench",
            "atlas",
            "pr-link",
            str(atlas_path),
            "publish",
            "--page-url",
            "https://evidence.example/atlas/index.html#publish",
            "--json",
        ],
    )

    assert built.exit_code == 0, built.output
    assert json.loads(built.output) == {
        "cross_check": True,
        "output": str(atlas_path),
        "row_count": 2,
        "status": "ok",
    }
    atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    assert [(row["id"], row["state"]) for row in atlas["rows"]] == [
        ("publish", "failing"),
        ("remote-rented-glibc", "unbound"),
    ]
    assert atlas["rows"][0]["latest_run_id"] == latest_red.name
    assert atlas["rows"][0]["latest_run_id"] != older_green.name

    assert rendered.exit_code == 0, rendered.output
    assert json.loads(rendered.output) == {"output": str(page_path), "status": "ok"}
    assert "FAILING" in page_path.read_text(encoding="utf-8")

    assert summarized.exit_code == 0, summarized.output
    summary = json.loads(summarized.output)
    assert summary["failures"] == [
        {
            "claim": "Dataset publication reaches the remote.",
            "evidence_ref": f"runs/v1/{latest_red.name}/result.json",
            "id": "publish",
            "run_id": latest_red.name,
            "state": "failing",
            "verdict": "fail",
        }
    ]
    assert summary["holes"][0]["id"] == "remote-rented-glibc"

    assert queried.exit_code == 0, queried.output
    query = json.loads(queried.output)
    assert query["count"] == 1
    assert query["rows"][0]["id"] == "publish"
    assert linked.exit_code == 0, linked.output
    assert json.loads(linked.output) == {
        "evidence_ref": f"runs/v1/{latest_red.name}/result.json",
        "id": "publish",
        "link": (
            f"[bench evidence: {latest_red.name}]"
            "(https://evidence.example/atlas/index.html#publish) "
            f"(`runs/v1/{latest_red.name}/result.json`)"
        ),
        "run_id": latest_red.name,
    }


def test_atlas_consumer_rechecks_mutable_projection_against_run_store(
    tmp_path: Path,
) -> None:
    product_commit = "a" * 40
    capabilities_digest = "sha256:" + "b" * 64
    verifier_digest = "sha256:" + "c" * 64
    verifier_name = "arena_guarantees.verify_publish"
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    failed = _finalize_run(
        store,
        claim="Dataset publication reaches the remote.",
        nodeid="arena::publish",
        verdict="fail",
        verifier_name=verifier_name,
        verifier_digest=verifier_digest,
        product_commit=product_commit,
        capabilities_digest=capabilities_digest,
    )
    atlas_path = tmp_path / "atlas.json"
    atlas_path.write_text(
        json.dumps(
            {
                "schema_version": "opentraces.arena.atlas.v0",
                "product_commit": product_commit,
                "capabilities_digest": capabilities_digest,
                "inactive_hole_states": ["no-red-proof", "unrepresentative-world"],
                "rows": [
                    {
                        "id": "publish",
                        "claim": "Dataset publication reaches the remote.",
                        "nodeid": "arena::publish",
                        "verifier": {
                            "name": verifier_name,
                            "digest": verifier_digest,
                        },
                        "state": "proven",
                        "latest_run_id": failed.name,
                        "verdict": "pass",
                        "evidence_ref": f"runs/v1/{failed.name}/result.json",
                        "black_box_review": "unreviewed",
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    summarized = CliRunner().invoke(
        main,
        [
            "bench",
            "atlas",
            "summary",
            str(atlas_path),
            "--store-root",
            str(store.root),
            "--json",
        ],
    )

    assert summarized.exit_code != 0
    assert "state" in summarized.output
    assert "disagrees" in summarized.output
