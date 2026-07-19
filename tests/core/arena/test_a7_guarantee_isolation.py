"""#338: guarantee generation must bind the requested repository's bytes.

The A7 guarantee generator accepts an explicit repository path but historically
imported process-cached ``tests.*`` modules. Generating for two worktrees in one
process therefore returned the first worktree's verifier digest both times. These
controls pin the isolated behaviour: each generation binds its own source bytes,
and the parent process's ``sys.path``/``sys.modules`` are left unchanged.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

import pytest

from tests.manual.generate_a7_guarantees import canonical_guarantees

REPOSITORY = Path(__file__).resolve().parents[3]
REAL_ARENA = REPOSITORY / "tests/arena"


def _make_repo(root: Path, *, marker: str) -> Path:
    """Materialize a minimal repo whose guarantees.py carries distinct bytes."""

    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    shutil.copytree(REAL_ARENA, tests_dir / "arena")
    guarantees = tests_dir / "arena" / "guarantees.py"
    # Same coordinate + same callables, different source bytes -> different digest.
    guarantees.write_text(
        guarantees.read_text(encoding="utf-8") + f"\n# isolation-marker: {marker}\n",
        encoding="utf-8",
    )
    return root


def _expected_guarantees_digest(root: Path) -> str:
    source = (root / "tests/arena/guarantees.py").read_bytes()
    return f"sha256:{hashlib.sha256(source).hexdigest()}"


def _guarantees_source_verifier_digests(manifest: dict) -> set[str]:
    # Both these guarantees resolve to tests/arena/guarantees.py.
    ids = {"remote-rented-glibc", "linux-x86_64-hf-emulator"}
    return {
        row["verifier"]["digest"]
        for row in manifest["guarantees"]
        if row["id"] in ids
    }


@pytest.mark.parametrize("order", [("A", "B"), ("B", "A")])
def test_two_repo_generation_in_one_process_binds_each_repos_bytes(
    tmp_path: Path, order: tuple[str, str]
) -> None:
    repos = {
        "A": _make_repo(tmp_path / "repo-A", marker="alpha"),
        "B": _make_repo(tmp_path / "repo-B", marker="beta"),
    }
    expected = {name: _expected_guarantees_digest(root) for name, root in repos.items()}
    assert expected["A"] != expected["B"]

    saved_path = list(sys.path)
    saved_modules = set(sys.modules)

    for name in order:
        manifest = canonical_guarantees(repository=repos[name])
        digests = _guarantees_source_verifier_digests(manifest)
        assert digests == {expected[name]}, (
            f"generation for repo {name} bound {digests}, expected "
            f"{expected[name]} -- a cached module leaked another repo's bytes"
        )
        # The generator must not mutate the parent's import state.
        assert list(sys.path) == saved_path
        assert set(sys.modules) == saved_modules


def test_repeated_generation_is_deterministic(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo", marker="gamma")
    first = canonical_guarantees(repository=repo)
    second = canonical_guarantees(repository=repo)
    assert first == second
    assert _guarantees_source_verifier_digests(first) == {
        _expected_guarantees_digest(repo)
    }
