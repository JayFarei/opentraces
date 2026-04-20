"""Local-disk stand-in for a HuggingFace dataset repo.

``FakeHFRepo`` implements the subset of ``HfApi`` that ``HFUploader`` and the
publish flow call during a push: ``create_repo``, ``update_repo_settings``,
``list_repo_files``, ``hf_hub_download`` and ``upload_file``. A single instance
is backed by a writable directory (typically a pytest ``tmp_path`` sub-dir),
seeded from a fixture, so that multiple HFUploader calls within one test see
a coherent, evolving remote state.

We deliberately avoid trying to emulate more of HfApi than our code actually
uses. If future push-path code calls another method, add it explicitly so the
test suite keeps pointing at the real integration surface.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class UploadCall:
    path_in_repo: str
    data: bytes
    commit_message: str | None = None


@dataclass
class FakeHFRepo:
    """A directory-backed stand-in for a HF dataset repo."""

    workdir: Path
    uploads: list[UploadCall] = field(default_factory=list)
    create_repo_calls: list[dict[str, Any]] = field(default_factory=list)
    update_repo_settings_calls: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_fixture(cls, fixture_dir: Path, workdir: Path) -> "FakeHFRepo":
        """Seed ``workdir`` from ``fixture_dir`` (copying every file) and
        return a fake backed by ``workdir``.

        When ``fixture_dir`` does not exist, ``workdir`` is created empty —
        this is how the ``fresh`` scenario (no files) is represented.
        """
        workdir.mkdir(parents=True, exist_ok=True)
        if fixture_dir.exists():
            for src in fixture_dir.rglob("*"):
                if src.is_file():
                    rel = src.relative_to(fixture_dir)
                    dst = workdir / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
        return cls(workdir=workdir)

    # --- HfApi surface ----------------------------------------------------

    def create_repo(self, *, repo_id: str, repo_type: str, exist_ok: bool,
                    private: bool = False) -> str:
        self.create_repo_calls.append(
            {"repo_id": repo_id, "repo_type": repo_type,
             "exist_ok": exist_ok, "private": private}
        )
        return f"https://huggingface.co/datasets/{repo_id}"

    def update_repo_settings(self, **kwargs: Any) -> None:
        self.update_repo_settings_calls.append(kwargs)

    def list_repo_files(self, *, repo_id: str, repo_type: str) -> list[str]:
        return sorted(
            str(p.relative_to(self.workdir)).replace("\\", "/")
            for p in self.workdir.rglob("*")
            if p.is_file()
        )

    def hf_hub_download(self, *, repo_id: str, filename: str, repo_type: str,
                        **_kwargs: Any) -> str:
        target = self.workdir / filename
        if not target.exists():
            try:
                from huggingface_hub.errors import EntryNotFoundError
            except ImportError:  # older huggingface_hub
                from huggingface_hub.utils import EntryNotFoundError  # type: ignore
            raise EntryNotFoundError(f"{filename} not in fake repo {repo_id}")
        return str(target)

    def upload_file(self, *, path_or_fileobj: bytes, path_in_repo: str,
                    repo_id: str, repo_type: str,
                    commit_message: str | None = None,
                    **_kwargs: Any) -> None:
        if not isinstance(path_or_fileobj, (bytes, bytearray)):
            raise TypeError(
                f"FakeHFRepo.upload_file expects bytes, got {type(path_or_fileobj)!r}"
            )
        target = self.workdir / path_in_repo
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(path_or_fileobj))
        self.uploads.append(
            UploadCall(
                path_in_repo=path_in_repo,
                data=bytes(path_or_fileobj),
                commit_message=commit_message,
            )
        )

    # --- Query helpers ----------------------------------------------------

    def uploads_to(self, path_in_repo: str) -> list[UploadCall]:
        return [c for c in self.uploads if c.path_in_repo == path_in_repo]

    def read_file(self, path_in_repo: str) -> bytes:
        return (self.workdir / path_in_repo).read_bytes()
