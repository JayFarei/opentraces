"""Real-box control for A6's non-sudo product workspace contract.

Run from the repository root with::

    .venv/bin/python tests/manual/verify_a6_product_workspace.py

The control intentionally uses a real Crabbox local-container lease.  It proves
the synchronized Git workspace, relative capture output, SSH transport, and
sudo identity boundary together; a scripted subprocess runner cannot establish
that ownership contract.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from opentraces.core.arena.box import PRODUCT_USER, CrabboxRuntime


def _run(*argv: str, cwd: Path) -> None:
    subprocess.run(argv, cwd=cwd, check=True, text=True, capture_output=True)


def verify() -> None:
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="opentraces-a6-workspace-") as directory:
        root = Path(directory)
        repository = root / "repository"
        repository.mkdir()
        _run("git", "init", "--initial-branch=main", cwd=repository)
        _run("git", "config", "user.name", "A6 workspace control", cwd=repository)
        _run("git", "config", "user.email", "a6-workspace@example.invalid", cwd=repository)
        (repository / "seed.txt").write_text("seed\n", encoding="utf-8")
        _run("git", "add", "seed.txt", cwd=repository)
        _run("git", "commit", "-m", "Seed workspace control", cwd=repository)

        evidence = root / "evidence"
        runtime = CrabboxRuntime(home=Path.home(), ssh_config=root / "missing-ssh-config")
        runtime.configure_run_evidence(evidence)
        box = None
        os.chdir(repository)
        try:
            box = runtime.lease()
            runtime.materialize(box, "base-only", repository=repository)

            product = runtime.exec_product(
                box,
                [
                    "sh",
                    "-c",
                    "set -eu; "
                    f'test "$(id -un)" = "{PRODUCT_USER}"; '
                    'test "$(id -u)" -ne 0; '
                    "! sudo -n true >/dev/null 2>&1; "
                    f'test "$(stat -c %U .)" = "{PRODUCT_USER}"; '
                    "mkdir -p .opentraces/bench-capture/workspace-control; "
                    "printf '%s\\n' '{\"completeness\":\"complete\"}' > "
                    ".opentraces/bench-capture/workspace-control/capture_result.json; "
                    "printf '%s\\n' 'agent edit' > a6-product-workspace-proof.txt; "
                    "git config user.name 'A6 product identity'; "
                    "git config user.email 'a6-product@example.invalid'; "
                    "git add a6-product-workspace-proof.txt; "
                    "git commit -m 'Prove product workspace ownership' >/dev/null; "
                    "test \"$(git show HEAD:a6-product-workspace-proof.txt)\" = 'agent edit'",
                ],
                timeout=120,
                timing_path=evidence / "product-workspace-timing.json",
            )
            if product.returncode != 0:
                raise AssertionError(
                    "the non-sudo product identity could not write capture output and commit "
                    f"in the synchronized workspace (rc={product.returncode}): {product.stderr}"
                )

            collected = runtime.collect(
                box,
                [".opentraces/bench-capture/workspace-control/capture_result.json"],
                destination=evidence / "collected",
                repository=repository,
            )
            capture_result = collected.get("capture_result.json")
            if capture_result is None or not capture_result.is_file():
                raise AssertionError("the relative capture result escaped artifact collection")

            transport = runtime.exec(
                box,
                [
                    "sh",
                    "-c",
                    'test "$(id -un)" = crabbox && test "$(stat -c %U "$HOME")" = crabbox',
                ],
                timeout=30,
                timing_path=evidence / "transport-home-timing.json",
            )
            if transport.returncode != 0:
                raise AssertionError("product workspace preparation mutated transport home custody")
        finally:
            try:
                if box is not None:
                    runtime.release(box)
            finally:
                os.chdir(original_cwd)


if __name__ == "__main__":
    verify()
    print("A6 product workspace control: PASS")
