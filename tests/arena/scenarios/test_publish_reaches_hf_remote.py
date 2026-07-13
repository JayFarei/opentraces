"""Launch scenario 2 for bench.v0."""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("OT_BENCH_SCENARIOS") != "1",
    reason="bench scenarios run through `opentraces bench run`",
)


def publish_commit_is_witnessed(run, *, hf):
    witnessed = hf.ledger.contains(
        method="POST",
        path_prefix="/api/datasets/bench/scenario-2/commit/",
        operation_id="commit",
    )
    assert witnessed, "independent Hugging Face ledger has no successful dataset commit"
    return {"evidence_refs": [hf.ledger.evidence_ref]}


def test_publish_reaches_hf_remote(bench):
    """Publishing a dataset reaches the configured Hugging Face remote.

    The product is driven only through its public dataset commands. The pass is
    grounded in the emulator's independently collected raw JSONL ledger.
    """

    with bench.run(app_state="install-only") as run:
        hf = run.emulate("huggingface")
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
        else:
            assert published.returncode == 0, published.stderr
        run.verify(publish_commit_is_witnessed, hf=hf)
