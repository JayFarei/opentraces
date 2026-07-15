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
            pins={},
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
