"""Plan 058 V24 + V25 proof — local-as-remote loadability via ``datasets.load_dataset``.

This test publishes a local dataset to a fake HF remote (via
``OPENTRACES_PLAN058_FAKE_REMOTE_ROOT``) and proves that the published
local dataset path is loadable by the standard HuggingFace ``datasets``
library and yields rows equivalent to the originals (V24).

It also asserts that no ``.opentraces/**`` directory leaks into the fake
remote tree (V25 fake-mode mirror of the live UAT).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests._dataset_egress import neutralize_dataset_egress


@pytest.fixture(autouse=True)
def _clear_dataset_egress(monkeypatch):
    # This publish-then-load round-trip predates the #194 egress clearance gate
    # and publishes synthetic trace ids with no bucket entry.
    neutralize_dataset_egress(monkeypatch)
from typing import Any

import pytest

from opentraces.core import config as config_mod
from opentraces.core import paths as paths_mod
from opentraces.core.datasets import (
    add_dataset_remote,
    append_rows,
    create_dataset,
    dataset_path,
    publish_dataset,
)


def _isolate_home(tmp: Path) -> None:
    home = tmp / "home"
    opentraces_dir = home / ".opentraces"
    projects_dir = opentraces_dir / "projects"
    projects_dir.mkdir(parents=True)
    os.environ["HOME"] = str(home)
    paths_mod.OPENTRACES_DIR = opentraces_dir
    paths_mod.CONFIG_PATH = opentraces_dir / "config.json"
    paths_mod.CREDENTIALS_PATH = opentraces_dir / "credentials"
    paths_mod.PROJECTS_DIR = projects_dir
    config_mod.OPENTRACES_DIR = opentraces_dir
    config_mod.CONFIG_PATH = opentraces_dir / "config.json"
    config_mod.CREDENTIALS_PATH = opentraces_dir / "credentials"
    config_mod.PROJECTS_DIR = projects_dir


def _row(summary: str, trace_id: str) -> dict[str, str]:
    return {
        "source_trace_id": trace_id,
        "source_unit_id": f"tu:{trace_id}:trace",
        "summary": summary,
    }


def _no_control_plane_leak(remote_root: Path) -> bool:
    if not remote_root.exists():
        return True
    return not any(
        ".opentraces" in path.relative_to(remote_root).parts
        for path in remote_root.rglob("*")
        if path.is_file()
    )


def _row_id_set(rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Return a comparable identity for each row (trace + unit + summary)."""
    return {(r["source_trace_id"], r["source_unit_id"], r["summary"]) for r in rows}


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    """Fully isolate ~/.opentraces and the fake remote root for this test."""
    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    _isolate_home(tmp_path)
    yield tmp_path


def test_plan058_published_local_path_is_loadable_by_hf_datasets(isolated_workspace):
    datasets = pytest.importorskip("datasets")

    tmp_path = isolated_workspace
    remote_root = tmp_path / "remotes" / "me" / "roundtrip"

    create_dataset(
        "roundtrip-source",
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("roundtrip-source", "me/roundtrip", visibility="private")

    originals = [
        _row("First publishable row.", trace_id="trace-1"),
        _row("Second publishable row with detail.", trace_id="trace-2"),
        _row("Third row spans multiple words for fidelity.", trace_id="trace-3"),
    ]
    summary = append_rows("roundtrip-source", originals, run_id="run-roundtrip")
    assert summary.appended_count == 3

    published = publish_dataset("roundtrip-source", contributor="tester")
    assert published.uploaded is True
    assert published.new_row_count == 3

    # V25: fake remote must contain no .opentraces/** entries.
    assert _no_control_plane_leak(remote_root), (
        "fake remote tree must not contain .opentraces/**; saw: "
        + ", ".join(
            str(p.relative_to(remote_root))
            for p in remote_root.rglob("*")
            if p.is_file()
        )
    )

    # V24: the source local dataset path and the published fake-remote
    # dataset path must both be loadable by the HF ``datasets`` library and
    # must yield rows equivalent to the originals.
    src_local_root = dataset_path("roundtrip-source")
    for loadable_root in (src_local_root, remote_root):
        loaded = datasets.load_dataset(str(loadable_root))
        assert "train" in loaded
        train = loaded["train"]
        assert train.num_rows == 3
        rows = list(train)
        assert _row_id_set(rows) == _row_id_set(originals)

        by_trace = {r["source_trace_id"]: r for r in rows}
        for original in originals:
            cloned = by_trace[original["source_trace_id"]]
            assert cloned["source_unit_id"] == original["source_unit_id"]
            assert cloned["summary"] == original["summary"]


def test_plan058_dataset_infos_and_schema_present_on_remote(isolated_workspace):
    """V25 + remote contract sanity: remote must contain dataset_infos.json
    and schemas/row.schema.json after publish, and never .opentraces/**."""

    tmp_path = isolated_workspace
    remote_root = tmp_path / "remotes" / "me" / "contract"

    create_dataset(
        "contract-source",
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("contract-source", "me/contract", visibility="private")
    append_rows(
        "contract-source",
        [_row("Row A.", "trace-A"), _row("Row B.", "trace-B")],
        run_id="run-contract",
    )
    publish_dataset("contract-source", contributor="tester")

    files = sorted(
        str(p.relative_to(remote_root))
        for p in remote_root.rglob("*")
        if p.is_file()
    )
    assert "README.md" in files
    assert "dataset_infos.json" in files
    assert "schemas/row.schema.json" in files
    assert any(f.startswith("data/") and f.endswith(".jsonl") for f in files)
    assert _no_control_plane_leak(remote_root)

    infos = json.loads((remote_root / "dataset_infos.json").read_text())
    assert "default" in infos
    features = infos["default"].get("features", {})
    assert {"source_trace_id", "source_unit_id", "summary"}.issubset(features.keys())
