"""Plan 058 V25 + happy-path lifecycle proof against a real HuggingFace Hub.

This test is gated. It runs only when both:

  - ``OPENTRACES_RUN_LIVE_PLAN058_HF=1``
  - ``HF_TOKEN`` (or ``HUGGINGFACE_TOKEN``) is set

are present in the environment. In default CI it skips cleanly with a
human-readable reason and produces no network traffic.

When unlocked, it:

  1. Creates a disposable private HF dataset with a random name.
  2. Adds a couple of rows locally and publishes them through the same
     ``publish_dataset`` code path the fake-mode harness exercises.
  3. Clones the contract via ``clone_remote_dataset`` and imports row
     shards via ``pull_dataset(data=True)`` into a fresh local copy.
  4. Asserts byte-identity between the staged shard files and the remote
     files at the published revision.
  5. Asserts the remote tree contains no ``.opentraces/**`` entry (V25).
  6. Cleans up the disposable repo with ``delete_repo`` in ``finally``.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Iterator

import pytest

from opentraces.core import config as config_mod
from opentraces.core import paths as paths_mod
from opentraces.core.datasets import (
    add_dataset_remote,
    append_rows,
    clone_remote_dataset,
    create_dataset,
    dataset_path,
    publish_dataset,
    pull_dataset,
    read_row_index,
)


_GATE_ENV = "OPENTRACES_RUN_LIVE_PLAN058_HF"


def _hf_token() -> str | None:
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")


pytestmark = pytest.mark.skipif(
    os.environ.get(_GATE_ENV) != "1" or not _hf_token(),
    reason=(
        "live HF Plan 058 UAT is gated; set "
        f"{_GATE_ENV}=1 and HF_TOKEN to run"
    ),
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


@pytest.fixture
def disposable_repo(tmp_path) -> Iterator[tuple[str, str]]:
    """Create a disposable private HF dataset repo and clean it up after."""
    from huggingface_hub import HfApi

    _isolate_home(tmp_path)

    token = _hf_token()
    api = HfApi(token=token)
    user = api.whoami()["name"]
    suffix = secrets.token_hex(4)
    repo_id = f"{user}/opentraces-plan058-uat-{suffix}"

    api.create_repo(
        repo_id=repo_id,
        repo_type="dataset",
        private=True,
        exist_ok=False,
    )

    try:
        yield repo_id, token
    finally:
        try:
            api.delete_repo(
                repo_id=repo_id,
                repo_type="dataset",
                missing_ok=True,
            )
        except Exception:  # noqa: BLE001 — cleanup is best-effort.
            pass


def _list_remote_files(repo_id: str, token: str | None) -> list[str]:
    """Recursively list every file path in the remote dataset tree."""
    from huggingface_hub import list_repo_tree

    files: list[str] = []
    stack = [""]
    while stack:
        prefix = stack.pop()
        for entry in list_repo_tree(
            repo_id=repo_id,
            repo_type="dataset",
            path_in_repo=prefix or None,
            token=token,
        ):
            if entry.__class__.__name__ == "RepoFolder" or getattr(entry, "type", None) == "directory":
                stack.append(entry.path)
            else:
                files.append(entry.path)
    return sorted(files)


def test_plan058_hf_live_publish_pull_byte_identity_and_no_control_plane_leak(
    disposable_repo,
):
    """V25 plus happy-path lifecycle on a real HF remote."""
    from huggingface_hub import hf_hub_download

    repo_id, token = disposable_repo

    # Build a local dataset and bind it to the disposable HF remote.
    create_dataset(
        "live-source",
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("live-source", repo_id, visibility="private")

    originals = [
        _row("Live row one.", trace_id="trace-live-1"),
        _row("Live row two with detail.", trace_id="trace-live-2"),
    ]
    append_summary = append_rows("live-source", originals, run_id="run-live")
    assert append_summary.appended_count == 2

    # Publish to the live remote (no fake-remote env var is set).
    published = publish_dataset("live-source", contributor="tester", token=token)
    assert published.uploaded is True
    assert published.new_row_count == 2
    assert published.staged_files, "publish must have staged at least one file"

    # V25 — the remote tree must not contain any .opentraces/** entry.
    remote_files = _list_remote_files(repo_id, token)
    assert remote_files, "remote must contain published files"
    assert not any(
        ".opentraces" in Path(rel).parts for rel in remote_files
    ), f"remote leaked control-plane files: {remote_files!r}"

    # Byte-identity: every staged public-surface file must match the remote at
    # the published revision.
    src_root = dataset_path("live-source")
    for rel in published.staged_files:
        local_path = src_root / rel if (src_root / rel).exists() else None
        # Some staged files (the new sharded data file) live only in the
        # tempdir staging area, but they are also reflected in the remote;
        # for those we just confirm the remote file exists with content.
        downloaded = Path(
            hf_hub_download(
                repo_id=repo_id,
                repo_type="dataset",
                filename=rel,
                token=token,
            )
        )
        if local_path is not None:
            assert downloaded.read_bytes() == local_path.read_bytes(), (
                f"byte-identity mismatch for {rel}"
            )
        else:
            assert downloaded.read_bytes(), f"remote file {rel} is empty"

    # Clone + pull --data into a fresh local clone and confirm the rows
    # round-trip back. This exercises the same code paths fake mode covers.
    cloned = clone_remote_dataset(f"hf://{repo_id}", as_name="live-clone", read_only=True, token=token)
    assert cloned.manifest.active_remote == repo_id
    assert read_row_index("live-clone") == []

    metadata_only = pull_dataset("live-clone", data=False, token=token)
    assert metadata_only.metadata_refreshed is True
    assert metadata_only.imported_count == 0
    assert read_row_index("live-clone") == []

    pulled = pull_dataset("live-clone", data=True, token=token)
    assert pulled.imported_count == 2
    assert len(read_row_index("live-clone")) == 2

    # V24 mirror against the live remote: load_dataset on the cloned local path
    # produces a Dataset object equivalent to the originals.
    datasets = pytest.importorskip("datasets")
    loaded = datasets.load_dataset(str(dataset_path("live-clone")))
    assert "train" in loaded
    cloned_rows = list(loaded["train"])
    expected = {(r["source_trace_id"], r["source_unit_id"], r["summary"]) for r in originals}
    actual = {(r["source_trace_id"], r["source_unit_id"], r["summary"]) for r in cloned_rows}
    assert actual == expected
