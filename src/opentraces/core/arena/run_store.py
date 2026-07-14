"""Write-once private run records for bench.v0."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import paths
from .._bucket_io import _atomic_write_bytes, _atomic_write_json, _canonical_json
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


def _json_digest(payload: dict[str, Any]) -> str:
    serialized = _canonical_json(payload, pretty=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(serialized).hexdigest()}"


def _process_is_alive(pid: object) -> bool:
    try:
        value = int(pid)
        if value <= 0:
            return False
        os.kill(value, 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


class RunStore:
    """Own the ``bucket/runs/v1`` namespace and its external integrity index."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else paths.bucket_dir() / "runs" / "v1"
        self.staging_root = self.root / ".staging"
        self.recovery_root = self.root.parent / "recovery"
        self.index_root = self.root / ".index"
        self.transaction_root = self.root / ".transactions"

    def begin(self) -> "RunDraft":
        self.reconcile()
        self.staging_root.mkdir(parents=True, exist_ok=True)
        run_id = _new_run_id()
        path = self.staging_root / run_id
        path.mkdir()
        for name in RUN_SUBDIRECTORIES:
            (path / name).mkdir()
        return RunDraft(store=self, run_id=run_id, path=path)

    def open_pending(self, run_id: str) -> "RunDraft":
        path = self.staging_root / run_id
        if not path.is_dir() or not (path / ".pending-result.json").is_file():
            raise FinalizedRunError(f"run {run_id} has no pending result")
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
            if path.is_file()
            and path.relative_to(run_path).as_posix()
            not in {"result.json", ".integrity.json"}
        }
        unexpected = actual - set(expected)
        if unexpected:
            raise RunIntegrityError(f"unexpected finalized file: {sorted(unexpected)[0]}")
        return True

    def reconcile(self) -> list[Path]:
        """Complete final publications left behind by an interrupted process."""

        if not self.transaction_root.is_dir():
            return []
        reconciled: list[Path] = []
        for intent_path in sorted(self.transaction_root.glob("*.json")):
            intent = json.loads(intent_path.read_text(encoding="utf-8"))
            if _process_is_alive(intent.get("owner_pid")):
                continue
            run_id = str(intent.get("run_id") or "")
            result = intent.get("result")
            if not run_id or not isinstance(result, dict):
                raise RunIntegrityError(f"invalid finalization intent: {intent_path.name}")
            validate_result(result)
            if result["run_id"] != run_id:
                raise RunIntegrityError(
                    f"finalization intent run_id mismatch: {intent_path.name}"
                )
            final_path = self.root / run_id
            staging_path = self.staging_root / run_id
            if final_path.is_dir():
                draft_path = final_path
            elif staging_path.is_dir():
                draft_path = staging_path
            else:
                recovery = self.recovery_root / run_id
                recovery.mkdir(parents=True, exist_ok=True)
                _atomic_write_json(recovery / "provisional_result.json", result)
                intent_path.unlink()
                continue
            draft = RunDraft(store=self, run_id=run_id, path=draft_path)
            published = draft._complete_finalization(result)
            draft._finalized = True
            draft._make_read_only(published)
            intent_path.unlink()
            reconciled.append(published)
        return reconciled


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
            if path.is_file()
            and path.relative_to(self.path).as_posix()
            not in {"result.json", ".integrity.json"}
        }
        return {"schema_version": "opentraces.bench.integrity.v0", "files": files}

    def _write_result(self, path: Path, payload: dict[str, Any]) -> None:
        _atomic_write_json(path, payload)

    def stage_result(self, result: dict[str, Any]) -> None:
        """Hold a validated result until its parent runner has exited."""

        validate_result(result)
        if result["run_id"] != self.run_id:
            raise ValueError("result run_id does not match the draft")
        self.write_json(".pending-result.json", result)

    def take_staged_result(self) -> dict[str, Any]:
        pending = self.path / ".pending-result.json"
        if not pending.is_file():
            raise FinalizedRunError(f"run {self.run_id} has no pending result")
        result = json.loads(pending.read_text(encoding="utf-8"))
        pending.unlink()
        validate_result(result)
        return result

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

        intent_path = self.store.transaction_root / f"{self.run_id}.json"
        try:
            _atomic_write_json(
                intent_path,
                {
                    "schema_version": "opentraces.bench.finalization-intent.v0",
                    "run_id": self.run_id,
                    "owner_pid": os.getpid(),
                    "result": result,
                },
            )
            final_path = self._complete_finalization(result)
        except Exception as exc:
            final_path = self.store.root / self.run_id
            index_path = self.store.index_root / f"{self.run_id}.json"
            recovery = self.store.recovery_root / self.run_id
            self.store.recovery_root.mkdir(parents=True, exist_ok=True)
            if recovery.exists():
                shutil.rmtree(recovery)
            source = final_path if final_path.exists() else self.path
            if source.exists():
                source.replace(recovery)
            else:
                recovery.mkdir(parents=True)
            self.path = recovery
            try:
                index_path.unlink(missing_ok=True)
            except OSError:
                pass
            _atomic_write_json(recovery / "provisional_result.json", result)
            intent_path.unlink(missing_ok=True)
            raise StorageFinalizeError(
                f"could not finalize run {self.run_id}: {exc}", recovery_path=recovery
            ) from exc

        self._finalized = True
        self._make_read_only(final_path)
        intent_path.unlink()
        return final_path

    def _complete_finalization(self, result: dict[str, Any]) -> Path:
        """Idempotently cross the move, index, and final-marker boundaries."""

        final_path = self.store.root / self.run_id
        staging_path = self.store.staging_root / self.run_id
        if self.path == final_path:
            pass
        elif self.path == staging_path:
            if final_path.exists():
                raise FinalizedRunError(f"run path already exists: {final_path}")
            if not (self.path / ".integrity.json").is_file():
                self.write_json(".integrity.json", self._manifest())
            self.store.root.mkdir(parents=True, exist_ok=True)
            self.path.replace(final_path)
            self.path = final_path
        else:
            raise FinalizedRunError(
                f"run {self.run_id} is outside staging and final namespaces"
            )

        integrity_path = final_path / ".integrity.json"
        if not integrity_path.is_file():
            _atomic_write_json(integrity_path, self._manifest())
        index_path = self.store.index_root / f"{self.run_id}.json"
        index = {
            "schema_version": "opentraces.bench.run-index.v0",
            "run_id": self.run_id,
            "result_digest": _json_digest(result),
            "integrity_digest": _sha256(integrity_path),
        }
        _atomic_write_json(index_path, index)
        result_path = final_path / "result.json"
        if result_path.is_file():
            if _sha256(result_path) != index["result_digest"]:
                raise RunIntegrityError(
                    "existing result.json differs from finalization intent"
                )
        else:
            self._write_result(result_path, result)
        return final_path
