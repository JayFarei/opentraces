from __future__ import annotations

import shutil
from pathlib import Path

from opentraces_schema import Agent, Step, TraceRecord


class _FakeHfApi:
    def __init__(self, token: str, root: Path) -> None:
        self.token = token
        self.root = root
        self.create_repo_calls: list[dict[str, object]] = []
        self.uploads: list[str] = []

    def _repo(self, repo_id: str) -> Path:
        owner, _, name = repo_id.partition("/")
        return self.root / owner / name

    def create_repo(self, *, repo_id: str, repo_type: str, exist_ok: bool, private: bool):
        self.create_repo_calls.append(
            {
                "repo_id": repo_id,
                "repo_type": repo_type,
                "exist_ok": exist_ok,
                "private": private,
            }
        )
        self._repo(repo_id).mkdir(parents=True, exist_ok=True)
        return f"https://huggingface.co/datasets/{repo_id}"

    def list_repo_files(self, *, repo_id: str, repo_type: str) -> list[str]:
        repo = self._repo(repo_id)
        if not repo.exists():
            raise FileNotFoundError(f"404 repo not found: {repo_id}")
        return sorted(
            path.relative_to(repo).as_posix()
            for path in repo.rglob("*")
            if path.is_file()
        )

    def upload_file(
        self,
        *,
        path_or_fileobj: str,
        path_in_repo: str,
        repo_id: str,
        repo_type: str,
    ) -> None:
        source = Path(path_or_fileobj)
        target = self._repo(repo_id) / path_in_repo
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        self.uploads.append(path_in_repo)

    def hf_hub_download(self, *, repo_id: str, filename: str, repo_type: str) -> str:
        path = self._repo(repo_id) / filename
        if not path.exists():
            raise FileNotFoundError(filename)
        return str(path)


def _trace(trace_id: str, content: str = "Private bucket sync") -> TraceRecord:
    from opentraces.security import SECURITY_VERSION

    record = TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code"),
        task={"description": content},
        steps=[Step(step_index=1, role="user", content=content)],
    )
    record.security.scanned = True
    record.security.classifier_version = SECURITY_VERSION
    return record


def _configure_hf_bucket(monkeypatch, remote_root: Path) -> _FakeHfApi:
    from opentraces.core.config import (
        BucketConfig,
        BucketRemoteConfig,
        load_config,
        save_config,
    )

    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    fake_api = _FakeHfApi("hf_test_token", remote_root)
    monkeypatch.setattr(
        "opentraces.core.bucket_remote.HfApi",
        lambda token: fake_api,
    )
    cfg = load_config()
    cfg.bucket = BucketConfig(
        storage="remote",
        local_cache=True,
        remote=BucketRemoteConfig(
            enabled=True,
            provider="huggingface",
            url="hf://me/private-bucket",
            visibility="private",
            sync_policy="daemon",
        ),
    )
    save_config(cfg)
    return fake_api


def test_huggingface_bucket_push_status_and_pull(monkeypatch, tmp_path):
    from opentraces.core.bucket_remote import remote_pull, remote_push, remote_status
    from opentraces.core.bucket_store import iter_trace_record_objects, write_trace_record

    fake_api = _configure_hf_bucket(monkeypatch, tmp_path / "hf")
    write_trace_record(
        _trace("trace-hf-bucket-1"),
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=False,
    )

    missing = remote_status()
    assert missing["state"] == "missing"
    assert missing["repo_id"] == "me/private-bucket"

    pushed = remote_push()
    assert pushed["state"] == "pushed"
    assert pushed["provider"] == "huggingface"
    assert pushed["files_uploaded"] >= 2
    assert fake_api.create_repo_calls == [
        {
            "repo_id": "me/private-bucket",
            "repo_type": "dataset",
            "exist_ok": True,
            "private": True,
        }
    ]
    assert "manifest.json" in fake_api.uploads

    current = remote_status()
    assert current["state"] == "current"
    assert current["local_digest"] == current["remote_digest"]

    write_trace_record(
        _trace("trace-local-only"),
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=False,
    )
    assert remote_status()["state"] == "different"

    pulled = remote_pull()
    assert pulled["state"] == "pulled"
    assert [obj.trace_id for obj in iter_trace_record_objects()] == ["trace-hf-bucket-1"]


def test_daemon_reconcile_pushes_when_remote_differs(monkeypatch, tmp_path):
    from opentraces.core.bucket_remote import reconcile_once, remote_status
    from opentraces.core.bucket_store import write_trace_record

    _configure_hf_bucket(monkeypatch, tmp_path / "hf")
    write_trace_record(
        _trace("trace-daemon-bucket"),
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=False,
    )

    result = reconcile_once(reason="watcher")

    assert result["state"] == "pushed"
    assert result["reason"] == "watcher"
    assert remote_status()["state"] == "current"


def test_remote_status_reports_auth_error_without_throwing(monkeypatch):
    from opentraces.core.bucket_remote import remote_status
    from opentraces.core.config import (
        BucketConfig,
        BucketRemoteConfig,
        load_config,
        save_config,
    )

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    cfg = load_config()
    cfg.hf_token = None
    cfg.bucket = BucketConfig(
        storage="remote",
        local_cache=True,
        remote=BucketRemoteConfig(
            enabled=True,
            provider="huggingface",
            url="hf://me/private-bucket",
        ),
    )
    save_config(cfg)

    status = remote_status()

    assert status["state"] == "error"
    assert "not authenticated" in status["error"]
