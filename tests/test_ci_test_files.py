from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHARDER = ROOT / "scripts/ci_test_files.py"
HF_CONTRACT_FILES = {
    "tests/test_arena_hf_emulator.py",
    "tests/test_arena_hf_emulator_packaging.py",
}
HF_CLIENT_CARRIERS = {
    "src/opentraces/cli/__init__.py",
    "src/opentraces/cli/_auth_impl.py",
    "src/opentraces/cli/_remote_bucket.py",
    "src/opentraces/cli/bucket.py",
    "src/opentraces/cli/capsule.py",
    "src/opentraces/cli/dataset.py",
    "src/opentraces/core/bucket_backend.py",
    "src/opentraces/core/bucket_remote.py",
    "src/opentraces/core/capsule/share.py",
    "src/opentraces/core/config.py",
    "src/opentraces/core/datasets.py",
    "src/opentraces/publish/huggingface/**",
    "src/opentraces/quality/field_intent/auditor.py",
}


def test_premerge_sharder_leaves_hf_contract_to_its_pinned_workflow() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SHARDER),
            "--lane",
            "premerge",
            "--shard-index",
            "0",
            "--shard-total",
            "1",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    selected = set(result.stdout.splitlines())
    assert selected.isdisjoint(HF_CONTRACT_FILES)

    workflow = (ROOT / ".github/workflows/hf-emulator-contract.yml").read_text()
    for contract_file in HF_CONTRACT_FILES:
        assert contract_file in workflow


def test_hf_contract_workflow_watches_every_client_carrier() -> None:
    workflow = (ROOT / ".github/workflows/hf-emulator-contract.yml").read_text()

    for carrier in HF_CLIENT_CARRIERS:
        assert workflow.count(f'- "{carrier}"') == 2, (
            f"{carrier} must trigger the pinned HF contract on pull_request and push"
        )
