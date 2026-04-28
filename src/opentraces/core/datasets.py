"""Local HF-shaped dataset store for Plan 57 workflows."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from opentraces_schema import (
    DatasetIdentity,
    DatasetManifest,
    DatasetRowIndexEntry,
    WorkflowRef,
)

from . import paths

_DATASET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class LocalDataset:
    name: str
    path: Path
    manifest: DatasetManifest


@dataclass(frozen=True)
class AppendSummary:
    dataset_name: str
    run_id: str
    dry_run: bool
    emitted_count: int
    appended_count: int = 0
    would_append_count: int = 0
    duplicate_count: int = 0
    validation_error_count: int = 0
    validation_errors: list[dict[str, Any]] = field(default_factory=list)
    row_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RebuildSummary:
    dataset_name: str
    rebuilt_count: int
    digest: str


def datasets_dir() -> Path:
    return paths.OPENTRACES_DIR / "datasets"


def dataset_path(name: str) -> Path:
    return datasets_dir() / validate_dataset_name(name)


def validate_dataset_name(name: str) -> str:
    if not name or not _DATASET_NAME_RE.fullmatch(name):
        raise ValueError(
            "dataset name must start with a letter or number and contain only "
            "letters, numbers, '.', '_', or '-'"
        )
    return name


def create_dataset(
    name: str,
    *,
    description: str | None = None,
    workflow_skill: str | None = None,
    workflow_digest: str = "sha256:unconfigured",
    row_schema: dict[str, Any] | None = None,
    identity: DatasetIdentity | dict[str, Any] | None = None,
    replace: bool = False,
) -> LocalDataset:
    validate_dataset_name(name)
    root = dataset_path(name)
    if root.exists():
        if not replace:
            raise FileExistsError(f"dataset already exists: {name}")
        shutil.rmtree(root)

    schema_payload = row_schema or default_row_schema()
    identity_model = (
        identity
        if isinstance(identity, DatasetIdentity)
        else DatasetIdentity.model_validate(identity or {"mode": "payload_hash"})
    )
    workflow = WorkflowRef(
        skill=workflow_skill or f"{name}-workflow",
        digest=workflow_digest,
    )
    manifest = DatasetManifest(
        name=name,
        description=description,
        schema={"path": "schemas/row.schema.json", "version": "1.0.0"},
        workflow=workflow,
        identity=identity_model,
    )
    schema_digest = digest_payload(schema_payload)
    manifest.schema_ref.digest = schema_digest

    (root / "schemas").mkdir(parents=True)
    (root / "data").mkdir()
    (root / ".opentraces" / "runs").mkdir(parents=True)
    write_json(root / "schemas" / "row.schema.json", schema_payload)
    (root / "data" / "train.jsonl").write_text("", encoding="utf-8")
    (root / ".opentraces" / "row_index.jsonl").write_text("", encoding="utf-8")
    (root / ".opentraces" / "cursors.yaml").write_text("queries: {}\n", encoding="utf-8")
    write_json(root / "dataset_infos.json", build_dataset_infos(name, schema_payload))
    (root / "README.md").write_text(
        build_dataset_card(name, description, manifest),
        encoding="utf-8",
    )
    save_manifest(root, manifest)
    return LocalDataset(name=name, path=root, manifest=manifest)


def load_manifest(root: Path | str) -> DatasetManifest:
    root = Path(root)
    manifest_path = root / ".opentraces" / "manifest.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    return DatasetManifest.model_validate(raw)


def save_manifest(root: Path, manifest: DatasetManifest) -> None:
    manifest_path = root / ".opentraces" / "manifest.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    data = manifest.model_dump(mode="json", by_alias=True, exclude_none=True)
    manifest_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def list_datasets() -> list[LocalDataset]:
    root = datasets_dir()
    if not root.exists():
        return []
    datasets: list[LocalDataset] = []
    for item in sorted(root.iterdir(), key=lambda p: p.name):
        manifest_path = item / ".opentraces" / "manifest.yaml"
        if item.is_dir() and manifest_path.exists():
            datasets.append(LocalDataset(item.name, item, load_manifest(item)))
    return datasets


def load_dataset(name: str) -> LocalDataset:
    root = dataset_path(name)
    if not root.exists():
        raise FileNotFoundError(f"dataset not found: {name}")
    return LocalDataset(name=name, path=root, manifest=load_manifest(root))


def append_rows(
    name: str,
    rows: list[dict[str, Any]],
    *,
    run_id: str,
    dry_run: bool = False,
) -> AppendSummary:
    dataset = load_dataset(name)
    schema = read_json(dataset.path / dataset.manifest.schema_ref.path)
    schema_digest = dataset.manifest.schema_ref.digest or digest_payload(schema)
    existing = read_row_index(name)
    existing_identity_hashes = {entry.identity_hash for entry in existing}
    appended_entries: list[DatasetRowIndexEntry] = []
    appended_rows: list[dict[str, Any]] = []
    duplicate_count = 0
    validation_errors: list[dict[str, Any]] = []
    would_append_count = 0
    current_line = _line_count(dataset.path / "data" / "train.jsonl")

    for index, row in enumerate(rows, start=1):
        errors = validate_row(row, schema)
        if errors:
            validation_errors.append({"line": index, "errors": errors})
            continue
        identity_hash = row_identity_hash(row, dataset.manifest.identity)
        if identity_hash in existing_identity_hashes:
            duplicate_count += 1
            continue
        if dry_run:
            would_append_count += 1
            existing_identity_hashes.add(identity_hash)
            continue
        payload_hash = row_payload_hash(row)
        current_line += 1
        row_id = f"row_{identity_hash.removeprefix('sha256:')[:16]}"
        appended_entries.append(
            DatasetRowIndexEntry(
                row_id=row_id,
                identity_hash=identity_hash,
                payload_hash=payload_hash,
                schema_digest=schema_digest,
                data_file="data/train.jsonl",
                line=current_line,
                run_id=run_id,
                appended_at=_utc_now(),
            )
        )
        appended_rows.append(row)
        existing_identity_hashes.add(identity_hash)

    if appended_rows:
        data_file = dataset.path / "data" / "train.jsonl"
        with data_file.open("a", encoding="utf-8") as stream:
            for row in appended_rows:
                stream.write(_canonical_json(row) + "\n")
        row_index = dataset.path / ".opentraces" / "row_index.jsonl"
        with row_index.open("a", encoding="utf-8") as stream:
            for entry in appended_entries:
                stream.write(entry.model_dump_json() + "\n")

    return AppendSummary(
        dataset_name=name,
        run_id=run_id,
        dry_run=dry_run,
        emitted_count=len(rows),
        appended_count=len(appended_rows),
        would_append_count=would_append_count,
        duplicate_count=duplicate_count,
        validation_error_count=len(validation_errors),
        validation_errors=validation_errors,
        row_ids=[entry.row_id for entry in appended_entries],
    )


def read_row_index(name: str) -> list[DatasetRowIndexEntry]:
    row_index = dataset_path(name) / ".opentraces" / "row_index.jsonl"
    if not row_index.exists():
        return []
    entries: list[DatasetRowIndexEntry] = []
    for line in row_index.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(DatasetRowIndexEntry.model_validate_json(line))
    return entries


def rebuild_row_index(name: str) -> RebuildSummary:
    dataset = load_dataset(name)
    schema = read_json(dataset.path / dataset.manifest.schema_ref.path)
    schema_digest = dataset.manifest.schema_ref.digest or digest_payload(schema)
    entries: list[DatasetRowIndexEntry] = []
    data_file = dataset.path / "data" / "train.jsonl"
    for line_no, line in enumerate(data_file.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        identity_hash = row_identity_hash(row, dataset.manifest.identity)
        payload_hash = row_payload_hash(row)
        entries.append(
            DatasetRowIndexEntry(
                row_id=f"row_{identity_hash.removeprefix('sha256:')[:16]}",
                identity_hash=identity_hash,
                payload_hash=payload_hash,
                schema_digest=schema_digest,
                data_file="data/train.jsonl",
                line=line_no,
                run_id="rebuild",
                appended_at=_utc_now(),
            )
        )
    row_index = dataset.path / ".opentraces" / "row_index.jsonl"
    row_index.write_text(
        "".join(entry.model_dump_json() + "\n" for entry in entries),
        encoding="utf-8",
    )
    digest = digest_payload([entry.model_dump(mode="json") for entry in entries])
    return RebuildSummary(dataset_name=name, rebuilt_count=len(entries), digest=digest)


def export_jsonl(name: str, output: Path) -> dict[str, Any]:
    dataset = load_dataset(name)
    source = dataset.path / "data" / "train.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)
    return {
        "dataset": name,
        "output": str(output),
        "row_count": _line_count(source),
        "digest": file_digest(output),
    }


def validate_row(row: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if schema.get("type") == "object" and not isinstance(row, dict):
        return ["row must be an object"]
    required = schema.get("required") or []
    for field_name in required:
        if field_name not in row:
            errors.append(f"missing required field: {field_name}")
    properties = schema.get("properties") or {}
    for field_name, value in row.items():
        field_schema = properties.get(field_name)
        if field_schema is None:
            if schema.get("additionalProperties") is False:
                errors.append(f"additional property not allowed: {field_name}")
            continue
        expected_type = field_schema.get("type")
        if expected_type and not _matches_json_type(value, expected_type):
            errors.append(f"{field_name} must be {expected_type}")
    return errors


def row_identity_hash(row: dict[str, Any], identity: DatasetIdentity) -> str:
    if identity.mode == "fields":
        identity_payload = {field: row.get(field) for field in identity.fields}
        return digest_payload(identity_payload)
    return row_payload_hash(row)


def row_payload_hash(row: dict[str, Any]) -> str:
    return digest_payload(row)


def digest_payload(payload: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(payload).encode('utf-8')).hexdigest()}"


def file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def default_row_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["source_trace_id", "source_unit_id", "summary"],
        "properties": {
            "source_trace_id": {"type": "string"},
            "source_unit_id": {"type": "string"},
            "summary": {"type": "string"},
        },
        "additionalProperties": False,
    }


def build_dataset_card(
    name: str,
    description: str | None,
    manifest: DatasetManifest,
) -> str:
    tags = manifest.discoverability.tags
    frontmatter = yaml.safe_dump(
        {
            "configs": [
                {
                    "config_name": "default",
                    "data_files": [{"split": "train", "path": "data/train.jsonl"}],
                }
            ],
            "tags": tags,
            "license": manifest.discoverability.license,
        },
        sort_keys=False,
    )
    body = description or f"Local OpenTraces dataset `{name}`."
    return f"---\n{frontmatter}---\n# {name}\n\n{body}\n"


def build_dataset_infos(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "default": {
            "builder_name": "json",
            "config_name": "default",
            "dataset_name": name,
            "features": _features_from_json_schema(schema),
        }
    }


def _features_from_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    features: dict[str, Any] = {}
    for field_name, field_schema in (schema.get("properties") or {}).items():
        features[field_name] = _feature_from_schema(field_schema)
    return features


def _feature_from_schema(schema: dict[str, Any]) -> dict[str, str]:
    dtype = {
        "string": "string",
        "integer": "int64",
        "number": "float64",
        "boolean": "bool",
    }.get(schema.get("type"), "string")
    return {"_type": "Value", "dtype": dtype}


def _matches_json_type(value: Any, expected_type: str | list[str]) -> bool:
    if isinstance(expected_type, list):
        return any(_matches_json_type(value, item) for item in expected_type)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return (isinstance(value, int | float) and not isinstance(value, bool))
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "null":
        return value is None
    return True


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
