"""Issue #312: dataset publish success requires a remote commit acknowledgment."""

from __future__ import annotations

from pathlib import Path

from huggingface_hub import CommitInfo

from opentraces.core.datasets import _upload_public_surface


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
