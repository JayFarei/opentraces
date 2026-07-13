from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVER_SOURCE = (
    ROOT / "src/opentraces/core/arena/emulate/huggingface/server.ts"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def running_emulator(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    compiled_binary = os.environ.get("OPENTRACES_HF_EMULATOR_BINARY")
    if compiled_binary is not None:
        command = [compiled_binary]
    else:
        bun = shutil.which("bun")
        if bun is None:
            pytest.skip(
                "bun or OPENTRACES_HF_EMULATOR_BINARY is required to exercise the emulator"
            )
        command = [bun, "run", str(SERVER_SOURCE)]

    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}"
    ledger = tmp_path / "huggingface-ledger.jsonl"
    env = {
        **os.environ,
        "PORT": str(port),
        "LEDGER_PATH": str(ledger),
    }
    process = subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while True:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"emulator exited before readiness\nstdout:\n{stdout}\nstderr:\n{stderr}"
                )
            try:
                with urllib.request.urlopen(f"{endpoint}/_emulate/manifest") as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise AssertionError("emulator did not become ready within 5 seconds")
            time.sleep(0.02)
        yield endpoint, ledger
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url) as response:
        return json.load(response)


def _run_hf_client(endpoint: str, script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "HF_ENDPOINT": endpoint,
            "HF_TOKEN": "hf_bench_user_token",
        },
        check=False,
        capture_output=True,
        text=True,
    )


def test_manifest_declares_exact_honest_huggingface_surface(tmp_path: Path) -> None:
    with running_emulator(tmp_path) as (endpoint, _ledger):
        manifest = _get_json(f"{endpoint}/_emulate/manifest")

    operations = {
        operation["operationId"]: operation["status"]
        for operation in manifest["specs"][0]["operations"]
    }
    assert manifest["id"] == "huggingface"
    assert operations == {
        "createRepo": "hand-authored",
        "datasetInfo": "hand-authored",
        "listRepoTree": "hand-authored",
        "resolveFile": "hand-authored",
        "preupload": "hand-authored",
        "commit": "hand-authored",
        "whoami": "hand-authored",
        "updateSettings": "hand-authored",
        "listDatasets": "partial",
        "deleteRepo": "partial",
        "lfsBatch": "unsupported",
        "xetUpload": "unsupported",
    }
    assert manifest["connections"][0]["template"] == (
        "HF_ENDPOINT={{baseUrl}}\nHF_TOKEN={{token}}"
    )


def test_real_hf_client_creates_and_reads_dataset_through_hf_endpoint(
    tmp_path: Path,
) -> None:
    with running_emulator(tmp_path) as (endpoint, ledger_path):
        result = _run_hf_client(
            endpoint,
            """
import json
from huggingface_hub import HfApi

api = HfApi(token="hf_bench_user_token")
repo_url = api.create_repo(
    "bench/contract",
    repo_type="dataset",
    private=True,
    exist_ok=False,
)
info = api.dataset_info("bench/contract")
print(json.dumps({
    "endpoint": api.endpoint,
    "repo_url": str(repo_url),
    "id": info.id,
    "private": info.private,
    "sha": info.sha,
}))
""",
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "endpoint": endpoint,
        "repo_url": f"{endpoint}/datasets/bench/contract",
        "id": "bench/contract",
        "private": True,
        "sha": "0" * 40,
    }
    ledger_rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert [
        (row["method"], row["path"], row["operation_id"]) for row in ledger_rows
    ] == [
        ("GET", "/_emulate/manifest", "manifest"),
        ("POST", "/api/repos/create", "createRepo"),
        ("GET", "/api/datasets/bench/contract", "datasetInfo"),
    ]


def test_real_hf_client_auth_settings_listing_and_delete(tmp_path: Path) -> None:
    with running_emulator(tmp_path) as (endpoint, _ledger_path):
        result = _run_hf_client(
            endpoint,
            """
import json
from huggingface_hub import HfApi
from huggingface_hub.errors import RepositoryNotFoundError

api = HfApi(token="hf_bench_user_token")
identity = api.whoami()
api.create_repo("bench/settings", repo_type="dataset", private=True)
api.update_repo_settings(
    "bench/settings",
    repo_type="dataset",
    private=False,
    gated="manual",
)
listed = list(api.list_datasets(author="bench", limit=10))
updated = api.dataset_info("bench/settings")
api.delete_repo("bench/settings", repo_type="dataset")
try:
    api.dataset_info("bench/settings")
except RepositoryNotFoundError:
    missing_error = "RepositoryNotFoundError"
else:
    missing_error = None
print(json.dumps({
    "identity": identity,
    "listed": [item.id for item in listed],
    "private": updated.private,
    "gated": updated.gated,
    "missing_error": missing_error,
}))
""",
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "identity": {"name": "bench", "type": "user"},
        "listed": ["bench/settings"],
        "private": False,
        "gated": "manual",
        "missing_error": "RepositoryNotFoundError",
    }


def test_real_hf_client_uploads_inline_without_lfs_or_xet(tmp_path: Path) -> None:
    with running_emulator(tmp_path) as (endpoint, ledger_path):
        result = _run_hf_client(
            endpoint,
            """
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from huggingface_hub import HfApi

api = HfApi(token="hf_bench_user_token")
api.create_repo("bench/upload", repo_type="dataset", private=True)
content = (b"regular-upload-proof-" * 8192)
commit = api.upload_file(
    repo_id="bench/upload",
    repo_type="dataset",
    path_in_repo="data/proof.parquet",
    path_or_fileobj=content,
    commit_message="contract upload",
)
files = api.list_repo_files("bench/upload", repo_type="dataset")
with TemporaryDirectory() as cache:
    downloaded = api.hf_hub_download(
        "bench/upload",
        "data/proof.parquet",
        repo_type="dataset",
        cache_dir=cache,
    )
    round_trip = Path(downloaded).read_bytes() == content
print(json.dumps({
    "oid": commit.oid,
    "files": files,
    "round_trip": round_trip,
}))
""",
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert len(payload["oid"]) == 40
    assert payload["files"] == ["data/proof.parquet"]
    assert payload["round_trip"] is True
    ledger_rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    paths = [row["path"] for row in ledger_rows]
    assert any("/preupload/" in path for path in paths)
    assert any("/commit/" in path for path in paths)
    assert not any("/info/lfs/" in path for path in paths)
    assert not any("xet" in path.lower() for path in paths)


def test_real_hf_client_dispatches_from_error_headers(tmp_path: Path) -> None:
    with running_emulator(tmp_path) as (endpoint, _ledger_path):
        result = _run_hf_client(
            endpoint,
            """
import json
from tempfile import TemporaryDirectory
from huggingface_hub import HfApi
from huggingface_hub.errors import (
    RemoteEntryNotFoundError,
    RepositoryNotFoundError,
    RevisionNotFoundError,
)

api = HfApi(token="hf_bench_user_token")
errors = {}
try:
    api.dataset_info("bench/missing")
except RepositoryNotFoundError as error:
    errors["repo"] = [type(error).__name__, error.response.headers["x-error-code"]]

api.create_repo("bench/errors", repo_type="dataset")
with TemporaryDirectory() as cache:
    try:
        api.hf_hub_download(
            "bench/errors",
            "missing.json",
            repo_type="dataset",
            cache_dir=cache,
        )
    except RemoteEntryNotFoundError as error:
        errors["entry"] = [type(error).__name__, error.response.headers["x-error-code"]]

api.upload_file(
    repo_id="bench/errors",
    repo_type="dataset",
    path_in_repo="present.json",
    path_or_fileobj=b"{}",
)
with TemporaryDirectory() as cache:
    try:
        api.hf_hub_download(
            "bench/errors",
            "present.json",
            repo_type="dataset",
            revision="not-a-revision",
            cache_dir=cache,
        )
    except RevisionNotFoundError as error:
        errors["revision"] = [type(error).__name__, error.response.headers["x-error-code"]]

print(json.dumps(errors, sort_keys=True))
""",
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "entry": ["RemoteEntryNotFoundError", "EntryNotFound"],
        "repo": ["RepositoryNotFoundError", "RepoNotFound"],
        "revision": ["RevisionNotFoundError", "RevisionNotFound"],
    }


def test_ledger_is_append_only_and_exposes_commit_proof(tmp_path: Path) -> None:
    ledger_path = tmp_path / "huggingface-ledger.jsonl"
    with running_emulator(tmp_path) as (endpoint, _):
        first = _run_hf_client(
            endpoint,
            """
from huggingface_hub import HfApi
api = HfApi(token="hf_bench_user_token")
api.create_repo("bench/first", repo_type="dataset")
api.upload_file(
    repo_id="bench/first",
    repo_type="dataset",
    path_in_repo="proof.json",
    path_or_fileobj=b'{"passed":true}',
    commit_message="proof",
)
""",
        )
        assert first.returncode == 0, first.stderr
        rows_before_restart = [
            json.loads(line) for line in ledger_path.read_text().splitlines()
        ]

    with running_emulator(tmp_path) as (endpoint, _):
        second = _run_hf_client(
            endpoint,
            """
from huggingface_hub import HfApi
HfApi(token="hf_bench_user_token").create_repo(
    "bench/second", repo_type="dataset"
)
""",
        )
        assert second.returncode == 0, second.stderr
        with urllib.request.urlopen(f"{endpoint}/_emulate/ledger") as response:
            rows_after_restart = [
                json.loads(line) for line in response.read().decode().splitlines()
            ]

    assert rows_after_restart[: len(rows_before_restart)] == rows_before_restart
    commit_row = next(
        row for row in rows_after_restart if row["operation_id"] == "commit"
    )
    assert commit_row["request"] == {
        "repo_id": "bench/first",
        "revision": "main",
        "files": ["proof.json"],
    }
    assert len(commit_row["response"]["commit_oid"]) == 40


def test_opentraces_uploader_publish_is_proven_by_ledger(tmp_path: Path) -> None:
    with running_emulator(tmp_path) as (endpoint, ledger_path):
        result = _run_hf_client(
            endpoint,
            """
import json
from opentraces.publish.huggingface.upload import HFUploader
from opentraces_schema.models import Agent, TraceRecord

uploader = HFUploader(token="hf_bench_user_token", repo_id="bench/opentraces")
uploader.ensure_repo_exists(private=True)
upload = uploader.upload_traces([
    TraceRecord(
        trace_id="scenario-2-proof",
        session_id="session-2-proof",
        agent=Agent(name="contract-agent"),
    )
])
print(json.dumps({
    "success": upload.success,
    "trace_count": upload.trace_count,
    "shard_name": upload.shard_name,
}))
""",
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert payload["trace_count"] == 1
    assert payload["shard_name"].startswith("traces_")
    ledger_rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    commit_rows = [row for row in ledger_rows if row["operation_id"] == "commit"]
    assert any(
        path == f"data/{payload['shard_name']}"
        for row in commit_rows
        for path in row["request"]["files"]
    )


def test_opentraces_publish_is_red_when_emulator_is_down() -> None:
    unavailable_endpoint = f"http://127.0.0.1:{_free_port()}"
    result = _run_hf_client(
        unavailable_endpoint,
        """
from opentraces.publish.huggingface.upload import HFUploader

HFUploader(
    token="hf_bench_user_token",
    repo_id="bench/emulator-down-control",
).ensure_repo_exists(private=True)
""",
    )

    assert result.returncode != 0
    assert "Connection refused" in result.stderr or "ConnectError" in result.stderr
