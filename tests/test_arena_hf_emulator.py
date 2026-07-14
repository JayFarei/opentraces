from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest

from opentraces.core.arena.emulate.huggingface.runtime import (
    DEFAULT_PORT,
    EmulatorBinaryPin,
    HuggingFaceEmulator,
    LEDGER_EVIDENCE_REF,
    REMOTE_LEDGER,
    pinned_bun_command,
    wait_for_hf_emulator,
)


ROOT = Path(__file__).resolve().parents[1]
SERVER_SOURCE = ROOT / "src/opentraces/core/arena/emulate/huggingface/server.ts"


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
        command = pinned_bun_command("run", str(SERVER_SOURCE))

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


class _RealLocalStopRuntime:
    """Execute the production stop script against a real local sidecar process."""

    def __init__(self, *, pid_path: Path, ledger_path: Path) -> None:
        self.pid_path = pid_path
        self.ledger_path = ledger_path

    def exec(self, _box, argv, *, cwd=None, env=None, timeout=60, timing_path=None):
        assert argv[:2] == ["sh", "-c"]
        script = argv[2]
        script = script.replace("/tmp/opentraces-hf-emulator.pid", str(self.pid_path))
        script = script.replace(f"sudo -u opentraces-hf cat {REMOTE_LEDGER}", f"cat {self.ledger_path}")
        observed = subprocess.run(
            ["sh", "-c", script],
            cwd=cwd,
            env=env,
            timeout=timeout,
            check=False,
            capture_output=True,
            text=True,
        )
        return SimpleNamespace(
            returncode=observed.returncode,
            stdout=observed.stdout,
            stderr=observed.stderr,
        )


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url) as response:
        return json.load(response)


def _request_json(
    url: str,
    *,
    method: str = "POST",
    payload: object | None = None,
    token: str | None = None,
) -> dict:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload if payload is not None else {}).encode(),
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def _assert_invalid_token(
    url: str,
    *,
    method: str,
    payload: object,
    token: str | None,
) -> None:
    with pytest.raises(urllib.error.HTTPError) as caught:
        _request_json(url, method=method, payload=payload, token=token)
    assert caught.value.code == 401
    assert caught.value.headers["X-Error-Code"] == "InvalidToken"


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


def _run_opentraces(
    endpoint: str,
    home: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    executable = Path(sys.executable).with_name("opentraces")
    return subprocess.run(
        [str(executable), *args],
        env={
            **os.environ,
            "HOME": str(home),
            "HF_ENDPOINT": endpoint,
            "HF_TOKEN": "hf_bench_user_token",
        },
        check=False,
        capture_output=True,
        text=True,
    )


def _prepare_publishable_dataset(
    endpoint: str,
    home: Path,
    tmp_path: Path,
    *,
    name: str,
) -> None:
    rows = tmp_path / f"{name}-rows.jsonl"
    rows.write_text('{"value":1}\n', encoding="utf-8")
    schema = tmp_path / f"{name}-schema.json"
    schema.write_text(
        '{"type":"object","properties":{"value":{"type":"integer"}},"required":["value"]}\n',
        encoding="utf-8",
    )
    commands = (
        ("dataset", "new", name, "--rows-file", str(rows), "--schema", str(schema), "--json"),
        ("dataset", "review", "approve", name, "--all", "--json"),
        ("dataset", "remote", "create", name, f"bench/{name}", "--private", "--json"),
    )
    for command in commands:
        result = _run_opentraces(endpoint, home, *command)
        assert result.returncode == 0, result.stderr


def test_manifest_declares_exact_honest_huggingface_surface(tmp_path: Path) -> None:
    assert version("huggingface-hub") == "1.10.2"
    assert version("hf-xet") == "1.4.3"

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
        "validateYaml": "hand-authored",
        "listDatasets": "partial",
        "deleteRepo": "partial",
        "lfsBatch": "unsupported",
        "xetUpload": "unsupported",
    }
    assert manifest["connections"][0]["template"] == ("HF_ENDPOINT={{baseUrl}}\nHF_TOKEN={{token}}")
    assert manifest["controlPlane"] == {
        "credentials": "POST /_emulate/credentials",
        "seed": "POST /_emulate/seed",
        "reset": "POST /_emulate/reset",
    }
    assert manifest["auth"][0]["validation"] == "minted-or-baseline-state"


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
    assert [(row["method"], row["path"], row["operation_id"]) for row in ledger_rows] == [
        ("GET", "/_emulate/manifest", "manifest"),
        ("POST", "/api/repos/create", "createRepo"),
        ("GET", "/api/datasets/bench/contract", "datasetInfo"),
    ]


def test_real_hf_client_auth_settings_listing_and_delete(tmp_path: Path) -> None:
    with running_emulator(tmp_path) as (endpoint, ledger_path):
        first_credential = _request_json(
            f"{endpoint}/_emulate/credentials", payload={"name": "bench"}
        )
        second_credential = _request_json(
            f"{endpoint}/_emulate/credentials", payload={"name": "bench"}
        )
        assert (
            first_credential
            == second_credential
            == {
                "name": "bench",
                "token": "hf_bench_user_token",
                "type": "user",
            }
        )
        assert _request_json(
            f"{endpoint}/_emulate/seed",
            payload={"repos": [{"repo_id": "bench/seeded", "private": True}]},
        ) == {"repos_seeded": ["bench/seeded"]}

        seeded = _run_hf_client(
            endpoint,
            """
import json
from huggingface_hub import HfApi
info = HfApi(token="hf_bench_user_token").dataset_info("bench/seeded")
print(json.dumps({"id": info.id, "private": info.private}))
""",
        )
        assert seeded.returncode == 0, seeded.stderr
        assert json.loads(seeded.stdout) == {"id": "bench/seeded", "private": True}
        assert _request_json(f"{endpoint}/_emulate/reset") == {"ok": True}

        _assert_invalid_token(
            f"{endpoint}/api/repos/create",
            method="POST",
            payload={"name": "missing", "organization": "bench", "type": "dataset"},
            token=None,
        )
        _assert_invalid_token(
            f"{endpoint}/api/repos/create",
            method="POST",
            payload={"name": "invalid", "organization": "bench", "type": "dataset"},
            token="hf_not_minted",
        )
        _request_json(
            f"{endpoint}/api/repos/create",
            payload={"name": "guard", "organization": "bench", "type": "dataset"},
            token="hf_bench_user_token",
        )
        guarded_mutations = [
            ("PUT", f"{endpoint}/api/datasets/bench/guard/settings", {"private": True}),
            ("POST", f"{endpoint}/api/datasets/bench/guard/preupload/main", {"files": []}),
            ("POST", f"{endpoint}/api/datasets/bench/guard/commit/main", {}),
            (
                "DELETE",
                f"{endpoint}/api/repos/delete",
                {"name": "guard", "organization": "bench", "type": "dataset"},
            ),
        ]
        for method, url, payload in guarded_mutations:
            _assert_invalid_token(
                url,
                method=method,
                payload=payload,
                token="hf_not_minted",
            )

        result = _run_hf_client(
            endpoint,
            """
import json
from huggingface_hub import HfApi
from huggingface_hub.errors import HfHubHTTPError, RepositoryNotFoundError

api = HfApi(token="hf_bench_user_token")
identity = api.whoami()
try:
    HfApi(token="hf_not_minted").whoami()
except HfHubHTTPError as error:
    invalid_identity = [error.response.status_code, error.response.headers["x-error-code"]]
else:
    invalid_identity = None
api.create_repo("bench/settings", repo_type="dataset", private=True)
api.update_repo_settings(
    "bench/settings",
    repo_type="dataset",
    private=False,
    gated="manual",
)
listed = list(api.list_datasets(author="bench", limit=10))
updated = api.dataset_info("bench/settings")
api.upload_file(
    repo_id="bench/settings",
    repo_type="dataset",
    path_in_repo="stale.txt",
    path_or_fileobj=b"must-not-resurrect",
)
api.delete_repo("bench/settings", repo_type="dataset")
try:
    api.dataset_info("bench/settings")
except RepositoryNotFoundError:
    missing_error = "RepositoryNotFoundError"
else:
    missing_error = None
api.create_repo("bench/settings", repo_type="dataset", private=True)
recreated_files = api.list_repo_files("bench/settings", repo_type="dataset")
recreated_sha = api.dataset_info("bench/settings").sha
print(json.dumps({
    "identity": identity,
    "invalid_identity": invalid_identity,
    "listed": [item.id for item in listed],
    "private": updated.private,
    "gated": updated.gated,
    "missing_error": missing_error,
    "recreated_files": recreated_files,
    "recreated_sha": recreated_sha,
}))
""",
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "identity": {"name": "bench", "type": "user"},
        "invalid_identity": [401, "InvalidToken"],
        "listed": ["bench/guard", "bench/settings"],
        "private": False,
        "gated": "manual",
        "missing_error": "RepositoryNotFoundError",
        "recreated_files": [],
        "recreated_sha": "0" * 40,
    }
    ledger_rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    update_row = next(
        row
        for row in ledger_rows
        if row["operation_id"] == "updateSettings" and row["response"]["status"] == 200
    )
    delete_row = next(
        row
        for row in ledger_rows
        if row["operation_id"] == "deleteRepo" and row["response"]["status"] == 200
    )
    assert update_row["request"] == {
        "gated": "manual",
        "visibility": "public",
    }
    assert delete_row["request"] == {
        "name": "settings",
        "organization": "bench",
        "type": "dataset",
    }


def test_real_hf_client_listing_is_scoped_to_authenticated_owner(
    tmp_path: Path,
) -> None:
    with running_emulator(tmp_path) as (endpoint, _ledger_path):
        _request_json(
            f"{endpoint}/_emulate/seed",
            payload={
                "repos": [
                    {"repo_id": "bench/public"},
                    {"repo_id": "bench/private", "private": True},
                    {"repo_id": "bench/gated", "gated": "manual"},
                    {"repo_id": "other/public"},
                    {"repo_id": "other/private", "private": True},
                    {"repo_id": "other/gated", "gated": "manual"},
                ]
            },
        )
        result = _run_hf_client(
            endpoint,
            """
import json
from huggingface_hub import HfApi

anonymous = HfApi(token=False)
valid = HfApi(token="hf_bench_user_token")
print(json.dumps({
    "anonymous_all": [item.id for item in anonymous.list_datasets(limit=20)],
    "anonymous_bench": [
        item.id for item in anonymous.list_datasets(author="bench", limit=20)
    ],
    "anonymous_other": [
        item.id for item in anonymous.list_datasets(author="other", limit=20)
    ],
    "valid_all": [item.id for item in valid.list_datasets(limit=20)],
    "valid_bench": [
        item.id for item in valid.list_datasets(author="bench", limit=20)
    ],
    "valid_other": [
        item.id for item in valid.list_datasets(author="other", limit=20)
    ],
}, sort_keys=True))
""",
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "anonymous_all": [],
        "anonymous_bench": [],
        "anonymous_other": [],
        "valid_all": ["bench/public", "bench/private", "bench/gated"],
        "valid_bench": ["bench/public", "bench/private", "bench/gated"],
        "valid_other": [],
    }


def test_raw_listing_rejects_invalid_bearer_and_honors_owner_author_filter(
    tmp_path: Path,
) -> None:
    with running_emulator(tmp_path) as (endpoint, _ledger_path):
        _request_json(
            f"{endpoint}/_emulate/seed",
            payload={
                "repos": [
                    {"repo_id": "bench/public"},
                    {"repo_id": "bench/private", "private": True},
                    {"repo_id": "other/public"},
                    {"repo_id": "other/gated", "gated": "manual"},
                ]
            },
        )

        def raw_list(*, author: str | None = None, token: str | None = None) -> list[str]:
            suffix = f"?author={author}" if author is not None else ""
            headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
            request = urllib.request.Request(
                f"{endpoint}/api/datasets{suffix}",
                headers=headers,
                method="GET",
            )
            with urllib.request.urlopen(request) as response:
                return [row["id"] for row in json.load(response)]

        assert raw_list() == []
        assert raw_list(author="bench") == []
        assert raw_list(author="other") == []
        assert raw_list(token="hf_bench_user_token") == [
            "bench/public",
            "bench/private",
        ]
        assert raw_list(author="other", token="hf_bench_user_token") == []

        with pytest.raises(urllib.error.HTTPError) as caught:
            raw_list(token="hf_not_minted")
        assert caught.value.code == 401
        assert caught.value.headers["X-Error-Code"] == "InvalidToken"
        assert json.load(caught.value) == {"error": "invalid access token"}


def test_cross_owner_token_cannot_read_or_mutate_bench_repositories(
    tmp_path: Path,
) -> None:
    with running_emulator(tmp_path) as (endpoint, _ledger_path):
        _request_json(
            f"{endpoint}/_emulate/seed",
            payload={
                "repos": [
                    {"repo_id": "bench/public"},
                    {"repo_id": "bench/private", "private": True},
                    {"repo_id": "bench/gated", "gated": "manual"},
                ]
            },
        )
        other_token = _request_json(
            f"{endpoint}/_emulate/credentials",
            payload={"name": "other"},
        )["token"]
        result = _run_hf_client(
            endpoint,
            f"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from huggingface_hub import HfApi
from huggingface_hub.errors import RepositoryNotFoundError

owner = HfApi(token="hf_bench_user_token")
other = HfApi(token={json.dumps(other_token)})
anonymous = HfApi(token=False)
for repo_id in ("bench/public", "bench/private", "bench/gated"):
    owner.upload_file(
        repo_id=repo_id,
        repo_type="dataset",
        path_in_repo="proof.txt",
        path_or_fileobj=repo_id.encode(),
    )
other.create_repo("other/own", repo_type="dataset", private=True)

def resolve(api, repo_id):
    with TemporaryDirectory() as cache:
        return Path(api.hf_hub_download(
            repo_id,
            "proof.txt",
            repo_type="dataset",
            cache_dir=cache,
        )).read_text()

def hidden_read(call):
    try:
        call()
    except RepositoryNotFoundError as error:
        return [
            type(error).__name__,
            error.response.status_code,
            error.response.headers["x-error-code"],
        ]
    return None

reads = {{}}
for repo_id in ("bench/private", "bench/gated"):
    reads[repo_id] = {{
        "info": hidden_read(lambda repo_id=repo_id: other.dataset_info(repo_id)),
        "tree": hidden_read(
            lambda repo_id=repo_id: other.list_repo_files(
                repo_id, repo_type="dataset"
            )
        ),
        "resolve": hidden_read(lambda repo_id=repo_id: resolve(other, repo_id)),
    }}

observed = {{
    "anonymous_public": [
        anonymous.dataset_info("bench/public").id,
        anonymous.list_repo_files("bench/public", repo_type="dataset"),
        resolve(anonymous, "bench/public"),
    ],
    "owner_reads": [
        owner.dataset_info(repo_id).id
        for repo_id in ("bench/public", "bench/private", "bench/gated")
    ],
    "other_public": [
        other.dataset_info("bench/public").id,
        other.list_repo_files("bench/public", repo_type="dataset"),
        resolve(other, "bench/public"),
    ],
    "owner_listing": [item.id for item in owner.list_datasets(limit=20)],
    "other_listing": [item.id for item in other.list_datasets(limit=20)],
    "reads": reads,
}}

def raw_mutation(method, path, payload, content_type="application/json"):
    data = (
        json.dumps(payload).encode()
        if content_type == "application/json"
        else payload.encode()
    )
    request = urllib.request.Request(
        f"{{os.environ['HF_ENDPOINT']}}{{path}}",
        data=data,
        headers={{
            "Authorization": "Bearer {other_token}",
            "Content-Type": content_type,
        }},
        method=method,
    )
    try:
        with urllib.request.urlopen(request) as response:
            return [response.status, response.headers.get("X-Error-Code")]
    except urllib.error.HTTPError as error:
        return [error.code, error.headers["X-Error-Code"]]

mutations = {{
    "create": raw_mutation(
        "POST",
        "/api/repos/create",
        {{"name": "foreign", "organization": "bench", "type": "dataset"}},
    ),
    "settings": raw_mutation(
        "PUT",
        "/api/datasets/bench/public/settings",
        {{"private": True}},
    ),
    "preupload": raw_mutation(
        "POST",
        "/api/datasets/bench/public/preupload/main",
        {{"files": []}},
    ),
    "commit": raw_mutation(
        "POST",
        "/api/datasets/bench/public/commit/main",
        '{{"key":"header","value":{{"summary":"forbidden"}}}}\\n',
        "application/x-ndjson",
    ),
    "delete": raw_mutation(
        "DELETE",
        "/api/repos/delete",
        {{"name": "public", "organization": "bench", "type": "dataset"}},
    ),
}}

observed["mutations"] = mutations
print(json.dumps(observed, sort_keys=True))
""",
        )

    assert result.returncode == 0, result.stderr
    hidden = ["RepositoryNotFoundError", 404, "RepoNotFound"]
    forbidden = [403, "Forbidden"]
    assert json.loads(result.stdout) == {
        "anonymous_public": ["bench/public", ["proof.txt"], "bench/public"],
        "owner_listing": ["bench/public", "bench/private", "bench/gated"],
        "owner_reads": ["bench/public", "bench/private", "bench/gated"],
        "other_listing": ["other/own"],
        "other_public": ["bench/public", ["proof.txt"], "bench/public"],
        "reads": {
            "bench/gated": {
                "info": hidden,
                "resolve": hidden,
                "tree": hidden,
            },
            "bench/private": {
                "info": hidden,
                "resolve": hidden,
                "tree": hidden,
            },
        },
        "mutations": {
            "commit": forbidden,
            "create": forbidden,
            "delete": forbidden,
            "preupload": forbidden,
            "settings": forbidden,
        },
    }


def test_real_hf_client_dataset_info_enforces_repo_read_access(tmp_path: Path) -> None:
    with running_emulator(tmp_path) as (endpoint, _ledger_path):
        _request_json(
            f"{endpoint}/_emulate/seed",
            payload={
                "repos": [
                    {"repo_id": "bench/public", "private": False},
                    {"repo_id": "bench/private", "private": True},
                    {"repo_id": "bench/gated", "gated": "manual"},
                ]
            },
        )
        result = _run_hf_client(
            endpoint,
            """
import json
from huggingface_hub import HfApi
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError

anonymous = HfApi(token=False)
valid = HfApi(token="hf_bench_user_token")
invalid = HfApi(token="hf_not_minted")
observed = {
    "public_anonymous": anonymous.dataset_info("bench/public").id,
    "valid": [
        valid.dataset_info("bench/public").id,
        valid.dataset_info("bench/private").id,
        valid.dataset_info("bench/gated").id,
    ],
}
try:
    anonymous.dataset_info("bench/private")
except HfHubHTTPError as error:
    observed["private_anonymous"] = [
        type(error).__name__,
        error.response.status_code,
        error.response.headers["x-error-code"],
    ]
try:
    anonymous.dataset_info("bench/gated")
except GatedRepoError as error:
    observed["gated_anonymous"] = [
        type(error).__name__,
        error.response.status_code,
        error.response.headers["x-error-code"],
    ]
try:
    invalid.dataset_info("bench/public")
except HfHubHTTPError as error:
    observed["public_unminted"] = [
        type(error).__name__,
        error.response.status_code,
        error.response.headers["x-error-code"],
    ]
print(json.dumps(observed, sort_keys=True))
""",
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "gated_anonymous": ["GatedRepoError", 403, "GatedRepo"],
        "private_anonymous": ["HfHubHTTPError", 401, "InvalidToken"],
        "public_anonymous": "bench/public",
        "public_unminted": ["HfHubHTTPError", 401, "InvalidToken"],
        "valid": ["bench/public", "bench/private", "bench/gated"],
    }


def test_real_hf_client_tree_enforces_repo_read_access(tmp_path: Path) -> None:
    with running_emulator(tmp_path) as (endpoint, _ledger_path):
        _request_json(
            f"{endpoint}/_emulate/seed",
            payload={
                "repos": [
                    {"repo_id": "bench/public", "private": False},
                    {"repo_id": "bench/private", "private": True},
                    {"repo_id": "bench/gated", "gated": "manual"},
                ]
            },
        )
        result = _run_hf_client(
            endpoint,
            """
import json
from huggingface_hub import HfApi
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError

anonymous = HfApi(token=False)
valid = HfApi(token="hf_bench_user_token")
invalid = HfApi(token="hf_not_minted")
observed = {
    "public_anonymous": anonymous.list_repo_files("bench/public", repo_type="dataset"),
    "valid": [
        valid.list_repo_files(repo_id, repo_type="dataset")
        for repo_id in ("bench/public", "bench/private", "bench/gated")
    ],
}
for label, api, repo_id, error_type in (
    ("private_anonymous", anonymous, "bench/private", HfHubHTTPError),
    ("gated_anonymous", anonymous, "bench/gated", GatedRepoError),
    ("public_unminted", invalid, "bench/public", HfHubHTTPError),
):
    try:
        api.list_repo_files(repo_id, repo_type="dataset")
    except error_type as error:
        observed[label] = [
            type(error).__name__,
            error.response.status_code,
            error.response.headers["x-error-code"],
        ]
print(json.dumps(observed, sort_keys=True))
""",
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "gated_anonymous": ["GatedRepoError", 403, "GatedRepo"],
        "private_anonymous": ["HfHubHTTPError", 401, "InvalidToken"],
        "public_anonymous": [],
        "public_unminted": ["HfHubHTTPError", 401, "InvalidToken"],
        "valid": [[], [], []],
    }


def test_real_hf_client_resolve_enforces_repo_read_access(tmp_path: Path) -> None:
    with running_emulator(tmp_path) as (endpoint, _ledger_path):
        _request_json(
            f"{endpoint}/_emulate/seed",
            payload={
                "repos": [
                    {"repo_id": "bench/public", "private": False},
                    {"repo_id": "bench/private", "private": True},
                    {"repo_id": "bench/gated", "gated": "manual"},
                ]
            },
        )
        result = _run_hf_client(
            endpoint,
            """
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from huggingface_hub import HfApi
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError

valid = HfApi(token="hf_bench_user_token")
for repo_id in ("bench/public", "bench/private", "bench/gated"):
    valid.upload_file(
        repo_id=repo_id,
        repo_type="dataset",
        path_in_repo="proof.txt",
        path_or_fileobj=repo_id.encode(),
    )

def resolve(api, repo_id):
    with TemporaryDirectory() as cache:
        return Path(api.hf_hub_download(
            repo_id,
            "proof.txt",
            repo_type="dataset",
            cache_dir=cache,
        )).read_text()

anonymous = HfApi(token=False)
invalid = HfApi(token="hf_not_minted")
observed = {
    "public_anonymous": resolve(anonymous, "bench/public"),
    "valid": [resolve(valid, repo_id) for repo_id in (
        "bench/public", "bench/private", "bench/gated"
    )],
}
for label, api, repo_id, error_type in (
    ("private_anonymous", anonymous, "bench/private", HfHubHTTPError),
    ("gated_anonymous", anonymous, "bench/gated", GatedRepoError),
    ("public_unminted", invalid, "bench/public", HfHubHTTPError),
):
    try:
        resolve(api, repo_id)
    except error_type as error:
        observed[label] = [
            type(error).__name__,
            error.response.status_code,
            error.response.headers["x-error-code"],
        ]
print(json.dumps(observed, sort_keys=True))
""",
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "gated_anonymous": ["GatedRepoError", 403, "GatedRepo"],
        "private_anonymous": ["HfHubHTTPError", 401, "InvalidToken"],
        "public_anonymous": "bench/public",
        "public_unminted": ["HfHubHTTPError", 401, "InvalidToken"],
        "valid": ["bench/public", "bench/private", "bench/gated"],
    }


def test_real_hf_client_can_read_immutable_content_after_later_commits(
    tmp_path: Path,
) -> None:
    with running_emulator(tmp_path) as (endpoint, _ledger_path):
        result = _run_hf_client(
            endpoint,
            """
import json
import os
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from huggingface_hub import HfApi

api = HfApi(token="hf_bench_user_token")
api.create_repo("bench/history", repo_type="dataset")
first = api.upload_file(
    repo_id="bench/history",
    repo_type="dataset",
    path_in_repo="value.txt",
    path_or_fileobj=b"first",
)
second = api.upload_file(
    repo_id="bench/history",
    repo_type="dataset",
    path_in_repo="value.txt",
    path_or_fileobj=b"second",
)
with TemporaryDirectory() as cache:
    old_path = api.hf_hub_download(
        "bench/history",
        "value.txt",
        repo_type="dataset",
        revision=first.oid,
        cache_dir=cache,
    )
    old_content = Path(old_path).read_text()

def resolved_commit(revision):
    request = urllib.request.Request(
        f"{os.environ['HF_ENDPOINT']}/datasets/bench/history/resolve/{revision}/value.txt",
        method="HEAD",
        headers={"Authorization": "Bearer hf_bench_user_token"},
    )
    with urllib.request.urlopen(request) as response:
        return response.headers["X-Repo-Commit"]

print(json.dumps({
    "first_oid": first.oid,
    "second_oid": second.oid,
    "old_content": old_content,
    "old_resolved_commit": resolved_commit(first.oid),
    "main_resolved_commit": resolved_commit("main"),
    "old_tree": api.list_repo_files(
        "bench/history", repo_type="dataset", revision=first.oid
    ),
}))
""",
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["first_oid"] != payload["second_oid"]
    assert payload["old_content"] == "first"
    assert payload["old_resolved_commit"] == payload["first_oid"]
    assert payload["main_resolved_commit"] == payload["second_oid"]
    assert payload["old_tree"] == ["value.txt"]


def test_real_hf_client_parent_commit_chains_and_rejects_a_stale_parent(
    tmp_path: Path,
) -> None:
    with running_emulator(tmp_path) as (endpoint, ledger_path):
        result = _run_hf_client(
            endpoint,
            """
import json
from huggingface_hub import CommitOperationAdd, HfApi
from huggingface_hub.errors import HfHubHTTPError

api = HfApi(token="hf_bench_user_token")
api.create_repo("bench/parent-commit", repo_type="dataset")
initial_head = api.dataset_info("bench/parent-commit").sha
first = api.create_commit(
    repo_id="bench/parent-commit",
    repo_type="dataset",
    operations=[CommitOperationAdd(path_in_repo="first.txt", path_or_fileobj=b"first")],
    commit_message="first",
    parent_commit=initial_head,
)
second = api.create_commit(
    repo_id="bench/parent-commit",
    repo_type="dataset",
    operations=[CommitOperationAdd(path_in_repo="second.txt", path_or_fileobj=b"second")],
    commit_message="second",
    parent_commit=first.oid,
)
try:
    api.create_commit(
        repo_id="bench/parent-commit",
        repo_type="dataset",
        operations=[CommitOperationAdd(path_in_repo="stale.txt", path_or_fileobj=b"stale")],
        commit_message="stale",
        parent_commit=first.oid,
    )
except HfHubHTTPError as error:
    conflict = [
        error.response.status_code,
        error.response.headers["x-error-code"],
    ]
else:
    conflict = None
after = api.dataset_info("bench/parent-commit")
print(json.dumps({
    "initial_head": initial_head,
    "first_oid": first.oid,
    "second_oid": second.oid,
    "conflict": conflict,
    "after_head": after.sha,
    "after_files": api.list_repo_files("bench/parent-commit", repo_type="dataset"),
}))
""",
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["first_oid"] != payload["initial_head"]
    assert payload["second_oid"] != payload["first_oid"]
    assert payload["conflict"] == [409, "Conflict"]
    assert payload["after_head"] == payload["second_oid"]
    assert payload["after_files"] == ["first.txt", "second.txt"]

    commit_rows = [
        json.loads(line)
        for line in ledger_path.read_text().splitlines()
        if json.loads(line)["operation_id"] == "commit"
    ]
    assert [row["response"]["status"] for row in commit_rows] == [200, 200, 409]
    assert commit_rows[-1]["request"]["parent_commit"] == payload["first_oid"]
    assert "commit_oid" not in commit_rows[-1]["response"]


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
import os
import urllib.error
import urllib.request
from tempfile import TemporaryDirectory
from huggingface_hub import HfApi
from huggingface_hub.errors import (
    HfHubHTTPError,
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
try:
    api.create_repo("bench/errors", repo_type="dataset", exist_ok=False)
except HfHubHTTPError as error:
    errors["create_conflict"] = [
        type(error).__name__,
        error.response.headers["x-error-code"],
    ]
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
try:
    api.upload_file(
        repo_id="bench/errors",
        repo_type="dataset",
        path_in_repo="rejected.json",
        path_or_fileobj=b"{}",
        revision="not-a-revision",
    )
except RevisionNotFoundError as error:
    errors["preupload_revision"] = [
        type(error).__name__,
        error.response.headers["x-error-code"],
    ]

commit_request = urllib.request.Request(
    f"{os.environ['HF_ENDPOINT']}/api/datasets/bench/errors/commit/not-a-revision",
    data=b'{"key":"header","value":{"summary":"rejected"}}\\n',
    headers={
        "Authorization": "Bearer hf_bench_user_token",
        "Content-Type": "application/x-ndjson",
    },
    method="POST",
)
try:
    urllib.request.urlopen(commit_request)
except urllib.error.HTTPError as error:
    errors["commit_revision"] = [
        error.code,
        error.headers["X-Error-Code"],
    ]

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
        "commit_revision": [404, "RevisionNotFound"],
        "create_conflict": ["HfHubHTTPError", "Conflict"],
        "entry": ["RemoteEntryNotFoundError", "EntryNotFound"],
        "preupload_revision": ["RevisionNotFoundError", "RevisionNotFound"],
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
        rows_before_restart = [json.loads(line) for line in ledger_path.read_text().splitlines()]

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
    commit_row = next(row for row in rows_after_restart if row["operation_id"] == "commit")
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


def test_pinned_client_can_validate_the_dataset_card_before_folder_upload(
    tmp_path: Path,
) -> None:
    """The public dataset publish path validates README metadata before commit."""

    with running_emulator(tmp_path) as (endpoint, ledger_path):
        result = _run_hf_client(
            endpoint,
            """
from pathlib import Path
from tempfile import TemporaryDirectory
from huggingface_hub import HfApi

api = HfApi(token="hf_bench_user_token")
api.create_repo("bench/folder-publish", repo_type="dataset")
with TemporaryDirectory() as raw:
    folder = Path(raw)
    (folder / "README.md").write_text("---\\nlicense: mit\\n---\\n# Proof\\n")
    (folder / "data.jsonl").write_text('{"proof":true}\\n')
    api.upload_folder(
        repo_id="bench/folder-publish",
        repo_type="dataset",
        folder_path=folder,
        commit_message="folder proof",
    )
""",
        )

    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert any(
        row["method"] == "POST"
        and row["path"] == "/api/validate-yaml"
        and row["response"]["status"] == 200
        for row in rows
    )
    assert any(
        row["operation_id"] == "commit" and row["request"]["repo_id"] == "bench/folder-publish"
        for row in rows
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


def test_hf_stop_proves_real_process_death_before_final_ledger_snapshot(
    tmp_path: Path,
) -> None:
    """The production lifecycle stops a real pinned sidecar before sealing evidence."""

    endpoint = f"http://127.0.0.1:{DEFAULT_PORT}"
    ledger_path = tmp_path / "real-stop-ledger.jsonl"
    pid_path = tmp_path / "real-stop.pid"
    process = subprocess.Popen(
        pinned_bun_command("run", str(SERVER_SOURCE)),
        env={
            **os.environ,
            "PORT": str(DEFAULT_PORT),
            "LEDGER_PATH": str(ledger_path),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
    waiter: threading.Thread | None = None
    try:
        wait_for_hf_emulator(endpoint)
        observed = _run_hf_client(
            endpoint,
            """
from huggingface_hub import HfApi
HfApi(token="hf_bench_user_token").whoami()
""",
        )
        assert observed.returncode == 0, observed.stderr
        pre_stop_rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
        pre_stop_whoami = next(row for row in pre_stop_rows if row["operation_id"] == "whoami")

        run_path = tmp_path / "run"
        hf = HuggingFaceEmulator(
            runtime=_RealLocalStopRuntime(pid_path=pid_path, ledger_path=ledger_path),
            box=None,
            repository=ROOT,
            run_path=run_path,
            binary_pin=EmulatorBinaryPin(sha256="0" * 64, size_bytes=0),
            world_setup={},
        )
        waiter = threading.Thread(target=process.wait, daemon=True)
        waiter.start()
        hf.stop()
        waiter.join(timeout=2)

        assert process.returncode is not None
        with pytest.raises(OSError):
            urllib.request.urlopen(f"{endpoint}/_emulate/manifest", timeout=0.2)

        post_death = _run_hf_client(
            endpoint,
            """
from huggingface_hub import HfApi
HfApi(token="hf_bench_user_token").create_repo(
    "bench/post-death", repo_type="dataset"
)
""",
        )
        assert post_death.returncode != 0
        final_path = run_path / LEDGER_EVIDENCE_REF
        assert final_path.read_bytes() == ledger_path.read_bytes()
        final_rows = [json.loads(line) for line in final_path.read_text().splitlines()]
        assert pre_stop_whoami in final_rows
        assert not any(row["operation_id"] == "commit" for row in final_rows)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)
        if waiter is not None:
            waiter.join(timeout=2)


def test_dataset_publish_dead_endpoint_is_named_and_does_not_advance_local_state(
    tmp_path: Path,
) -> None:
    """A confirmed-dead bound remote is an inspectable refusal, never success."""

    home = tmp_path / "home"
    home.mkdir()

    with running_emulator(tmp_path) as (endpoint, ledger_path):
        _prepare_publishable_dataset(endpoint, home, tmp_path, name="remote-ack")

    # The context manager has terminated and waited for the sidecar. This is a
    # confirmed-dead endpoint, unlike a best-effort signal with no death proof.
    published = _run_opentraces(
        endpoint,
        home,
        "dataset",
        "publish",
        "remote-ack",
        "--json",
    )

    assert published.returncode == 3
    payload = json.loads(published.stdout)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "remote_unavailable"
    assert "remote_head_after" not in payload.get("publish", {})
    assert "Traceback" not in published.stderr

    state_path = (
        home / ".opentraces" / "datasets" / "remote-ack" / ".opentraces" / "publication_state.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    only_row = next(iter(state["rows"].values()))
    assert only_row["status"] == "publishable"
    assert only_row["uploaded_to"] == {}
    assert not state_path.with_name("publications.jsonl").exists()
    ledger_rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert not any(row["operation_id"] == "commit" for row in ledger_rows)


def test_dataset_publish_reports_the_pinned_clients_commit_ack(tmp_path: Path) -> None:
    """The public product journey reports the write response's exact commit oid."""

    home = tmp_path / "home"
    home.mkdir()
    with running_emulator(tmp_path) as (endpoint, ledger_path):
        _prepare_publishable_dataset(endpoint, home, tmp_path, name="remote-ack-live")
        published = _run_opentraces(
            endpoint,
            home,
            "dataset",
            "publish",
            "remote-ack-live",
            "--json",
        )

    assert published.returncode == 0, published.stderr
    payload = json.loads(published.stdout)
    assert payload["status"] == "ok"
    acknowledged_oid = payload["publish"]["remote_head_after"]
    commit_rows = [
        row
        for row in (json.loads(line) for line in ledger_path.read_text().splitlines())
        if row["operation_id"] == "commit"
    ]
    assert len(commit_rows) == 1
    assert acknowledged_oid == commit_rows[0]["response"]["commit_oid"]
