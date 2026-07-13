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
