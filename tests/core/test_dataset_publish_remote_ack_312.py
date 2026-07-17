"""Issue #312: dataset publish success requires a remote commit acknowledgment."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from huggingface_hub import CommitInfo

from opentraces.cli.dataset import dataset_group
from opentraces.core.datasets import (
    _upload_public_surface,
    add_dataset_remote,
    append_rows,
    create_dataset,
    publication_state_path,
)
from tests._dataset_egress import neutralize_dataset_egress


_PARENT_OID = "b" * 40


def test_live_upload_uses_the_upload_ack_oid_without_a_second_head_read(
    tmp_path: Path, monkeypatch
) -> None:
    """The write response, not a later read, is the publish success authority."""

    acknowledged_oid = "a" * 40
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "README.md").write_text("# acknowledged\n", encoding="utf-8")
    calls: list[dict[str, object]] = []

    class AckingApi:
        def __init__(self, *, token: str | None = None) -> None:
            assert token == "hf_test_token"

        def upload_folder(self, **kwargs) -> CommitInfo:
            calls.append(kwargs)
            return CommitInfo(
                commit_url=(
                    "https://huggingface.co/datasets/bench/ack/commit/"
                    f"{acknowledged_oid}"
                ),
                commit_message="Upload folder using huggingface_hub",
                commit_description="",
                oid=acknowledged_oid,
                _endpoint="https://huggingface.co",
            )

        def dataset_info(self, _repo_id: str):
            raise AssertionError("publish must not replace the write ack with a head read")

    monkeypatch.delenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", raising=False)
    monkeypatch.setattr("huggingface_hub.HfApi", AckingApi)

    observed = _upload_public_surface(
        "bench/ack",
        staging,
        parent_commit="b" * 40,
        token="hf_test_token",
    )

    assert observed == acknowledged_oid
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("case", "acknowledged_oid"),
    (("missing", None), ("malformed", "not-a-git-oid"), ("unchanged", _PARENT_OID)),
)
def test_cli_rejects_unacknowledged_upload_without_mutating_publication_state(
    case: str,
    acknowledged_oid: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing, malformed, or non-advancing write ack is one atomic refusal."""

    name = f"remote-ack-{case}"
    create_dataset(
        name,
        workflow_digest="sha256:remote-ack-test",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote(name, f"bench/{name}", visibility="private")
    appended = append_rows(
        name,
        [
            {
                "source_trace_id": f"trace-{case}",
                "source_unit_id": f"tu:trace-{case}:trace",
                "summary": "The remote must acknowledge this row.",
            }
        ],
        run_id=f"run-{case}",
    )
    neutralize_dataset_egress(monkeypatch)

    class RejectingAckApi:
        def __init__(self, *, token: str | None = None) -> None:
            assert token == "hf_test_token"

        def dataset_info(self, _repo_id: str) -> SimpleNamespace:
            return SimpleNamespace(sha=_PARENT_OID)

        def upload_folder(self, **_kwargs) -> SimpleNamespace:
            return SimpleNamespace(oid=acknowledged_oid)

    monkeypatch.delenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", raising=False)
    monkeypatch.setattr("huggingface_hub.HfApi", RejectingAckApi)
    monkeypatch.setattr(
        "huggingface_hub.hf_hub_download",
        lambda **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(
        "opentraces.cli.dataset._hf_auth",
        lambda: ("hf_test_token", "bench"),
    )

    state_path = publication_state_path(name)
    state_before = state_path.read_bytes()
    publications_path = state_path.with_name("publications.jsonl")
    assert not publications_path.exists()

    result = CliRunner().invoke(dataset_group, ["publish", name, "--json"])

    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "remote_ack_missing"
    assert "remote_head_after" not in payload["publish"]
    assert state_path.read_bytes() == state_before
    state = json.loads(state_before)
    assert state["rows"][appended.row_ids[0]]["status"] == "publishable"
    assert state["rows"][appended.row_ids[0]]["uploaded_to"] == {}
    assert not publications_path.exists()


@pytest.mark.parametrize("explicit_to", (False, True))
def test_publish_refusal_reports_resolved_remote_in_structured_output(
    explicit_to: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #319: the refusal payload names the resolved remote repository.

    Implicit (no ``--to``) publication resolves the dataset's active remote,
    but the structured ``publish.remote`` field previously echoed only the raw
    option and was therefore ``null``. Both explicit and implicit failures must
    identify the resolved repository while staying nonzero, named, headless, and
    state-stable.
    """

    name = f"remote-resolve-{'explicit' if explicit_to else 'implicit'}"
    repo_id = f"bench/{name}"
    create_dataset(
        name,
        workflow_digest="sha256:remote-resolve-test",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote(name, repo_id, visibility="private")
    appended = append_rows(
        name,
        [
            {
                "source_trace_id": f"trace-{name}",
                "source_unit_id": f"tu:trace-{name}:trace",
                "summary": "The refusal must name the resolved remote.",
            }
        ],
        run_id=f"run-{name}",
    )
    neutralize_dataset_egress(monkeypatch)

    class RejectingAckApi:
        def __init__(self, *, token: str | None = None) -> None:
            assert token == "hf_test_token"

        def dataset_info(self, _repo_id: str) -> SimpleNamespace:
            return SimpleNamespace(sha=_PARENT_OID)

        def upload_folder(self, **_kwargs) -> SimpleNamespace:
            # Missing ack oid -> remote_ack_missing refusal.
            return SimpleNamespace(oid=None)

    monkeypatch.delenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", raising=False)
    monkeypatch.setattr("huggingface_hub.HfApi", RejectingAckApi)
    monkeypatch.setattr(
        "huggingface_hub.hf_hub_download",
        lambda **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(
        "opentraces.cli.dataset._hf_auth",
        lambda: ("hf_test_token", "bench"),
    )

    state_path = publication_state_path(name)
    state_before = state_path.read_bytes()
    publications_path = state_path.with_name("publications.jsonl")

    args = ["publish", name, "--json"]
    if explicit_to:
        args += ["--to", repo_id]
    result = CliRunner().invoke(dataset_group, args)

    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "remote_ack_missing"
    # The load-bearing #319 assertion: the resolved repository is inspectable.
    assert payload["publish"]["remote"] == repo_id
    # #312 controls stay green: named, nonzero, headless, state-stable.
    assert "remote_head_after" not in payload["publish"]
    assert state_path.read_bytes() == state_before
    state = json.loads(state_before)
    assert state["rows"][appended.row_ids[0]]["status"] == "publishable"
    assert state["rows"][appended.row_ids[0]]["uploaded_to"] == {}
    assert not publications_path.exists()
