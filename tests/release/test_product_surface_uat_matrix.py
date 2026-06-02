from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

from tests.otbox.checkpoints import REGISTRY as OTBOX_CHECKPOINTS


REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = REPO_ROOT / "tests" / "release" / "product_surface_uat_matrix.toml"


def _nav_docs(nav_item: object) -> list[str]:
    if isinstance(nav_item, str):
        return [nav_item] if nav_item.endswith(".md") else []
    if isinstance(nav_item, list):
        docs: list[str] = []
        for item in nav_item:
            docs.extend(_nav_docs(item))
        return docs
    if isinstance(nav_item, dict):
        docs = []
        for value in nav_item.values():
            docs.extend(_nav_docs(value))
        return docs
    return []


def _load_matrix() -> dict:
    with MATRIX_PATH.open("rb") as handle:
        return tomllib.load(handle)


def _assert_pytest_target_exists(target: str) -> None:
    file_part, *symbols = target.split("::")
    file_path = REPO_ROOT / file_part
    assert file_path.is_file(), f"pytest evidence missing file: {target}"
    if not symbols:
        return

    text = file_path.read_text()
    for symbol in symbols:
        assert re.search(rf"(^|\n)\s*def {re.escape(symbol)}\(", text) or re.search(
            rf"(^|\n)\s*class {re.escape(symbol)}\b", text
        ), f"pytest evidence missing symbol {symbol!r}: {target}"


def _assert_otbox_journey_exists(name: str) -> None:
    path = REPO_ROOT / "tests" / "otbox" / "catalogue" / "journeys" / f"{name}.toml"
    assert path.is_file(), f"otbox journey evidence missing: {name}"
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    assert data.get("name") == name, f"otbox journey name mismatch in {path}"
    checkpoints = data.get("from_checkpoints") or []
    assert checkpoints, (
        f"otbox evidence must be checkpoint-aware matrix evidence; "
        f"{name} has no from_checkpoints"
    )
    missing = [checkpoint for checkpoint in checkpoints if checkpoint not in OTBOX_CHECKPOINTS]
    assert not missing, f"otbox journey {name} references unknown checkpoints: {missing}"


def _assert_evidence_exists(ref: str) -> None:
    kind, _, target = ref.partition(":")
    assert kind and target, f"evidence entries must use kind:target form: {ref!r}"

    if kind == "pytest":
        _assert_pytest_target_exists(target)
    elif kind == "otbox":
        _assert_otbox_journey_exists(target)
    elif kind in {"doc", "log", "kb"}:
        if target.startswith("kb/") and not (REPO_ROOT / "kb").exists():
            pytest.skip(f"kb checkout not present for evidence target: {target}")
        assert (REPO_ROOT / target).is_file(), f"{kind} evidence missing file: {target}"
    elif kind == "command":
        assert target.strip(), "command evidence must include a command string"
    else:
        raise AssertionError(f"unknown evidence kind {kind!r} in {ref!r}")


def test_product_surface_matrix_covers_exact_mkdocs_nav_pages() -> None:
    data = _load_matrix()
    nav_source = REPO_ROOT / data["nav_source"]
    docs_root = REPO_ROOT / data["docs_root"]

    with nav_source.open("rb") as handle:
        nav_docs = _nav_docs(yaml.safe_load(handle)["nav"])
    assert len(nav_docs) == 36

    rows = data["page"]
    row_docs = [row["doc"] for row in rows]
    assert len(row_docs) == len(set(row_docs)), "matrix doc rows must be unique"
    assert set(row_docs) == set(nav_docs)

    for doc in row_docs:
        assert (docs_root / doc).is_file(), f"matrix references missing docs page: {doc}"


def test_product_surface_matrix_evidence_targets_exist() -> None:
    data = _load_matrix()
    rows = data["page"]
    assert rows, "matrix must define at least one page row"

    for row in rows:
        assert row["area"].strip(), f"{row['doc']} must declare a docs area"
        assert row["journey"].strip(), f"{row['doc']} must describe a UAT journey"
        assert row["command_families"], f"{row['doc']} must list command families"
        assert row["evidence"], f"{row['doc']} must map to evidence"
        for ref in row["evidence"]:
            _assert_evidence_exists(ref)


def test_remote_and_publish_rows_are_fake_remote_or_check_only_safe() -> None:
    data = _load_matrix()
    rows = data["page"]
    constrained = [row for row in rows if row.get("requires_fake_remote_or_check_only")]
    assert constrained, "remote/publish safety rows must be explicit"

    for row in constrained:
        joined = "\n".join(row["evidence"]).lower()
        assert (
            "check-only" in joined
            or "publish-check-only" in joined
            or "fake_remote" in joined
            or "fake_bucket" in joined
            or "fake remote" in joined
            or "fake-bucket" in joined
        ), f"{row['doc']} must cite fake-remote or check-only evidence"


def test_release_constraints_are_pinned() -> None:
    constraints = _load_matrix()["release_constraints"]
    expected = {
        "default_ci_offline_safe",
        "no_real_hf_publication_gate",
        "fake_remote_or_check_only_for_remote_publish",
        "bucket_dataset_separation",
        "privacy_security_posture",
        "schema_compatibility",
        "supported_agent_parity",
    }
    assert set(constraints) == expected
    assert all(constraints.values())
