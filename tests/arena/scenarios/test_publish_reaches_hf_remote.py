"""Launch scenario 2 for bench.v0."""

from __future__ import annotations

import json
import os

import pytest

from opentraces.core.arena.engine import VerificationFailed
from opentraces.core.arena.retrieval import StoredEvidence


pytestmark = pytest.mark.skipif(
    os.environ.get("OT_BENCH_SCENARIOS") != "1",
    reason="bench scenarios run through `opentraces bench run`",
)


def publish_commit_is_witnessed(evidence, *, hf=None):
    if isinstance(evidence, StoredEvidence):
        result = evidence.read_json("result.json")
        verifier_name = (
            f"{publish_commit_is_witnessed.__module__}.{publish_commit_is_witnessed.__qualname__}"
        )
        verifier = next(row for row in result["verifiers"] if row["name"] == verifier_name)
        ledger_ref = verifier["evidence_refs"][0]
        rows = [json.loads(line) for line in evidence.read_text(ledger_ref).splitlines()]
        witnessed = any(
            row.get("method") == "POST"
            and str(row.get("path") or "").startswith("/api/datasets/bench/scenario-2/commit/")
            and row.get("operation_id") == "commit"
            for row in rows
        )
    else:
        assert hf is not None
        ledger_ref = hf.ledger.evidence_ref
        witnessed = hf.ledger.contains(
            method="POST",
            path_prefix="/api/datasets/bench/scenario-2/commit/",
            operation_id="commit",
        )
    if not witnessed:
        raise VerificationFailed(
            "independent Hugging Face ledger has no successful dataset commit",
            evidence_refs=[ledger_ref],
        )
    return {"evidence_refs": [ledger_ref]}


def test_publish_reaches_hf_remote(bench):
    """Publishing a dataset reaches the configured Hugging Face remote.

    The product is driven only through its public dataset commands. The pass is
    grounded in the emulator's independently collected raw JSONL ledger.
    """

    with bench.run(app_state="install-only") as run:
        hf = run.emulate("huggingface")
        run.require_capabilities("cli:dataset.publish", "emulator:huggingface")
        files = run.terminal.exec(
            "python3",
            "-c",
            "import json; from pathlib import Path; "
            "Path('/tmp/scenario-2-rows.jsonl').write_text("
            "json.dumps({'value': 1}) + '\\n'); "
            "Path('/tmp/scenario-2-schema.json').write_text("
            "json.dumps({'type': 'object', 'properties': {'value': {'type': 'integer'}}, "
            "'required': ['value']}))",
            env=hf.env,
        )
        assert files.returncode == 0, files.stderr
        created = run.terminal.exec(
            "opentraces",
            "dataset",
            "new",
            "scenario-2",
            "--rows-file",
            "/tmp/scenario-2-rows.jsonl",
            "--schema",
            "/tmp/scenario-2-schema.json",
            "--json",
            env=hf.env,
        )
        assert created.returncode == 0, created.stderr
        approved = run.terminal.exec(
            "opentraces",
            "dataset",
            "review",
            "approve",
            "scenario-2",
            "--all",
            "--json",
            env=hf.env,
        )
        assert approved.returncode == 0, approved.stderr
        remote = run.terminal.exec(
            "opentraces",
            "dataset",
            "remote",
            "create",
            "scenario-2",
            "bench/scenario-2",
            "--private",
            "--json",
            env=hf.env,
        )
        assert remote.returncode == 0, remote.stderr

        if os.environ.get("OT_BENCH_HF_DOWN_CONTROL") == "1":
            hf.stop()

        published = run.terminal.exec(
            "opentraces",
            "dataset",
            "publish",
            "scenario-2",
            "--json",
            env=hf.env,
        )
        if os.environ.get("OT_BENCH_HF_DOWN_CONTROL") == "1":
            assert published.returncode != 0, "publish unexpectedly reached a stopped world"
            payload = json.loads(published.stdout)
            assert payload.get("status") != "ok"
            publish_payload = payload.get("publish") or {}
            assert publish_payload.get("uploaded") is not True
            assert not publish_payload.get("remote_head_after")
            assert not hf.ledger.contains(
                method="POST",
                path_prefix="/api/datasets/bench/scenario-2/commit/",
                operation_id="commit",
            )
        else:
            assert published.returncode == 0, published.stderr
        run.verify(publish_commit_is_witnessed, hf=hf)
