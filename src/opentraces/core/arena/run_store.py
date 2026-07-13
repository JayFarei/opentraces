"""Write-once private run records for bench.v0."""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import paths
from .._bucket_io import _atomic_write_bytes, _atomic_write_json
from .contract import validate_result


RUN_SUBDIRECTORIES = (
    "source",
    "actions",
    "ledgers",
    "capture",
    "recordings",
    "artifacts",
)


class FinalizedRunError(RuntimeError):
    """A caller attempted to mutate a finalized run."""


class RunIntegrityError(RuntimeError):
    """A finalized run no longer matches its write-once index."""


class StorageFinalizeError(RuntimeError):
    """Final storage failed; the provisional outcome remains recoverable."""

    def __init__(self, message: str, *, recovery_path: Path) -> None:
        super().__init__(message)
        self.recovery_path = recovery_path


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"run_{stamp}_{secrets.token_hex(6)}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


class RunStore:
    """Own the ``bucket/runs/v1`` namespace and its external integrity index."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else paths.bucket_dir() / "runs" / "v1"
        self.staging_root = self.root / ".staging"
        self.recovery_root = self.root.parent / "recovery"
        self.index_root = self.root / ".index"

    def begin(self) -> "RunDraft":
        self.staging_root.mkdir(parents=True, exist_ok=True)
        run_id = _new_run_id()
        path = self.staging_root / run_id
        path.mkdir()
        for name in RUN_SUBDIRECTORIES:
            (path / name).mkdir()
        return RunDraft(store=self, run_id=run_id, path=path)

    def verify(self, run_path: Path | str) -> bool:
        run_path = Path(run_path)
        result_path = run_path / "result.json"
        integrity_path = run_path / ".integrity.json"
        index_path = self.index_root / f"{run_path.name}.json"
        if not result_path.is_file() or not integrity_path.is_file() or not index_path.is_file():
            raise RunIntegrityError("finalized run is missing result, integrity manifest, or index")

        index = json.loads(index_path.read_text(encoding="utf-8"))
        if _sha256(result_path) != index.get("result_digest"):
            raise RunIntegrityError("result.json digest differs from the finalized index")
        if _sha256(integrity_path) != index.get("integrity_digest"):
            raise RunIntegrityError(".integrity.json digest differs from the finalized index")

        manifest = json.loads(integrity_path.read_text(encoding="utf-8"))
        expected = manifest.get("files")
        if not isinstance(expected, dict):
            raise RunIntegrityError("integrity manifest has no files object")
        for relative, expected_digest in sorted(expected.items()):
            candidate = run_path / relative
            if not candidate.is_file():
                raise RunIntegrityError(f"missing finalized file: {relative}")
            if _sha256(candidate) != expected_digest:
                raise RunIntegrityError(f"finalized file changed: {relative}")
        actual = {
            path.relative_to(run_path).as_posix()
            for path in run_path.rglob("*")
            if path.is_file() and path.name not in {"result.json", ".integrity.json"}
        }
        unexpected = actual - set(expected)
        if unexpected:
            raise RunIntegrityError(f"unexpected finalized file: {sorted(unexpected)[0]}")
        return True


@dataclass
class RunDraft:
    """Mutable attempt state that becomes immutable when ``result.json`` lands."""

    store: RunStore
    run_id: str
    path: Path
    _finalized: bool = False

    def _target(self, relative: str | Path) -> Path:
        if self._finalized or (self.path / "result.json").exists():
            raise FinalizedRunError(f"run {self.run_id} is finalized")
        relative = Path(relative)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("run paths must be relative and may not escape the run")
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def write_bytes(self, relative: str | Path, payload: bytes) -> Path:
        target = self._target(relative)
        _atomic_write_bytes(target, payload)
        return target

    def write_text(self, relative: str | Path, payload: str) -> Path:
        return self.write_bytes(relative, payload.encode("utf-8"))

    def write_json(self, relative: str | Path, payload: dict[str, Any]) -> Path:
        target = self._target(relative)
        _atomic_write_json(target, payload)
        return target

    def record_source(
        self,
        *,
        source_path: Path,
        nodeid: str,
        claim: str,
        scenario_path: str,
        repository: str,
        commit: str | None,
        dirty_diff_digest: str | None,
    ) -> dict[str, Any]:
        copied_source_path = "source/scenario.py"
        self.write_bytes(copied_source_path, Path(source_path).read_bytes())
        record = {
            "nodeid": nodeid,
            "claim": claim,
            "scenario_path": scenario_path,
            "repository": repository,
            "commit": commit,
            "dirty_diff_digest": dirty_diff_digest,
            "copied_source_path": copied_source_path,
        }
        self.write_json("source/source.json", record)
        return record

    def _manifest(self) -> dict[str, Any]:
        files = {
            path.relative_to(self.path).as_posix(): _sha256(path)
            for path in sorted(self.path.rglob("*"))
            if path.is_file() and path.name not in {"result.json", ".integrity.json"}
        }
        return {"schema_version": "opentraces.bench.integrity.v0", "files": files}

    def _write_result(self, path: Path, payload: dict[str, Any]) -> None:
        _atomic_write_json(path, payload)

    @staticmethod
    def _make_read_only(path: Path) -> None:
        for child in sorted(path.rglob("*"), reverse=True):
            try:
                child.chmod(0o555 if child.is_dir() else 0o444)
            except OSError:
                pass
        try:
            path.chmod(0o555)
        except OSError:
            pass

    def finalize(self, result: dict[str, Any]) -> Path:
        if self._finalized:
            raise FinalizedRunError(f"run {self.run_id} is finalized")
        validate_result(result)
        if result["run_id"] != self.run_id:
            raise ValueError("result run_id does not match the draft")

        self.write_json(".integrity.json", self._manifest())
        final_path = self.store.root / self.run_id
        if final_path.exists():
            raise FinalizedRunError(f"run path already exists: {final_path}")
        self.store.root.mkdir(parents=True, exist_ok=True)
        self.path.replace(final_path)
        self.path = final_path

        try:
            result_path = final_path / "result.json"
            self._write_result(result_path, result)
            index = {
                "schema_version": "opentraces.bench.run-index.v0",
                "run_id": self.run_id,
                "result_digest": _sha256(result_path),
                "integrity_digest": _sha256(final_path / ".integrity.json"),
            }
            _atomic_write_json(self.store.index_root / f"{self.run_id}.json", index)
        except Exception as exc:
            recovery = self.store.recovery_root / self.run_id
            self.store.recovery_root.mkdir(parents=True, exist_ok=True)
            if recovery.exists():
                shutil.rmtree(recovery)
            final_path.replace(recovery)
            self.path = recovery
            _atomic_write_json(recovery / "provisional_result.json", result)
            raise StorageFinalizeError(
                f"could not finalize run {self.run_id}: {exc}", recovery_path=recovery
            ) from exc

        self._finalized = True
        self._make_read_only(final_path)
        return final_path
