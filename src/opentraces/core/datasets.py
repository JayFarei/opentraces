"""Local HF-shaped dataset store for Plan 57 workflows."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from opentraces.core._time import utc_now_str
from pathlib import Path
from typing import Any

import yaml

try:
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

from opentraces_schema import (
    DatasetCandidateQuery,
    DatasetIdentity,
    DatasetManifest,
    DatasetPublicationPolicy,
    DatasetPublicationState,
    DatasetPublicationStateEntry,
    DatasetRemote,
    DatasetRowIndexEntry,
    DatasetSecurityOverride,
    DatasetSecurityPolicy,
    WorkflowRef,
)
from opentraces_schema.dataset import SECURITY_TOOL_ORDER

from ..security import SECURITY_VERSION
from ..security.dataset_rows import (
    DatasetRowSecurity,
    sanitize_dataset_row,
    unsupported_dataset_row_tools,
)
from ..security.privacy import DEFAULT_PRIVACY_TIER, normalize_privacy_tier
from ..security.scanner import scan_serialized

from . import paths

logger = logging.getLogger(__name__)

_DATASET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
SOURCE_PROVENANCE_SCHEMA = "opentraces.dataset.source_provenance.v1"


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


@dataclass(frozen=True)
class DatasetRemoteSummary:
    dataset_name: str
    name: str
    url: str
    visibility: str
    active: bool


@dataclass(frozen=True)
class DatasetPublishSummary:
    dataset_name: str
    remote_name: str
    repo_id: str
    run_id: str
    uploaded: bool
    check_only: bool
    new_row_count: int
    duplicate_count: int
    needs_review_count: int
    blocked_count: int
    staged_files: list[str]
    remote_head_before: str | None
    remote_head_after: str | None
    attempts: int = 1
    message: str = ""
    # Cluster F D8: row-level filter telemetry. ``None`` when no
    # filters were requested so existing summary consumers stay
    # unchanged.
    filter_summary: dict[str, Any] | None = None


@dataclass(frozen=True)
class DatasetWithdrawalRecord:
    target: str
    target_id: str
    reason: str
    requested_at: str


PUBLIC_SURFACE_PATTERNS = [
    "README.md",
    "dataset_infos.json",
    "schemas/**",
    "data/**",
    "quality.json",
    "_withdrawals/**",
]


# Bounded retry budget for ``publish_dataset`` parent_commit conflicts. Plan 058
# verification requires concurrent publishers to retry 412 conflicts without
# looping forever.
MAX_PARENT_COMMIT_RETRIES = 5


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
    workflow_instructions: str | None = None,
    workflow_config: dict[str, Any] | None = None,
    row_schema: dict[str, Any] | None = None,
    identity: DatasetIdentity | dict[str, Any] | None = None,
    publication_policy: DatasetPublicationPolicy | dict[str, Any] | None = None,
    candidate_query: DatasetCandidateQuery | dict[str, Any] | None = None,
    security: DatasetSecurityPolicy | dict[str, Any] | None = None,
    source_provenance: dict[str, Any] | None = None,
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
    policy_model = (
        publication_policy
        if isinstance(publication_policy, DatasetPublicationPolicy)
        else DatasetPublicationPolicy.model_validate(publication_policy or {})
    )
    if isinstance(candidate_query, DatasetCandidateQuery):
        query_model = candidate_query
    elif candidate_query:
        query_model = DatasetCandidateQuery.model_validate(candidate_query)
    else:
        query_model = None
    provenance_payload = source_provenance or _source_provenance_for_query(query_model)
    security_model = (
        security
        if isinstance(security, DatasetSecurityPolicy)
        else DatasetSecurityPolicy.model_validate(security or {})
    )
    # A dataset security contract may only require/offer tools that can actually
    # run over a projected row dict; reject a contract that lists tools which
    # silently would not run (trufflehog/llm_pii/capsule_scope/classifier).
    unsupported = unsupported_dataset_row_tools(
        [*security_model.required_tools, *security_model.optional_tools, *security_model.enabled_tools]
    )
    if unsupported:
        raise ValueError(
            "workflow security contract references tools that cannot run over "
            f"dataset rows: {', '.join(unsupported)}. Dataset-applicable tools "
            "are: regex, entropy, privacy_filter, business_logic, path_anonymizer."
        )
    workflow = WorkflowRef(
        skill=workflow_skill or f"{name}-workflow",
        digest=workflow_digest,
        instructions=workflow_instructions,
        config=workflow_config or {},
    )
    manifest = DatasetManifest(
        name=name,
        description=description,
        schema={"path": "schemas/row.schema.json", "version": "1.0.0"},
        workflow=workflow,
        identity=identity_model,
        candidate_query=query_model,
        publication_policy=policy_model,
        security=security_model,
    )
    schema_digest = digest_payload(schema_payload)
    manifest.schema_ref.digest = schema_digest

    (root / "schemas").mkdir(parents=True)
    (root / "data").mkdir()
    (root / ".opentraces" / "runs").mkdir(parents=True)
    write_json(root / "schemas" / "row.schema.json", schema_payload)
    (root / "data" / "train.jsonl").write_text("", encoding="utf-8")
    (root / ".opentraces" / "row_index.jsonl").write_text("", encoding="utf-8")
    (root / ".opentraces" / "row_provenance.jsonl").write_text("", encoding="utf-8")
    (root / ".opentraces" / "cursors.yaml").write_text("queries: {}\n", encoding="utf-8")
    if provenance_payload is not None:
        write_source_provenance(root, provenance_payload)
    write_json(root / "dataset_infos.json", build_dataset_infos(name, schema_payload))
    (root / "README.md").write_text(
        build_dataset_card(name, description, manifest),
        encoding="utf-8",
    )
    save_manifest(root, manifest)
    return LocalDataset(name=name, path=root, manifest=manifest)


def _canonical_tools(names: "set[str]") -> list[str]:
    return [name for name in SECURITY_TOOL_ORDER if name in names]


def apply_dataset_security_edit(
    policy: DatasetSecurityPolicy,
    *,
    enable: "tuple[str, ...] | list[str]" = (),
    disable: "tuple[str, ...] | list[str]" = (),
    unsafe_override: bool = False,
    reason: str | None = None,
) -> tuple[DatasetSecurityPolicy, dict[str, list[str]]]:
    """Edit a single dataset's resolved security policy.

    Optional tools may be toggled freely (within the workflow contract). A
    required tool can only be disabled when the workflow contract allows it
    (``allow_disable_required``) AND the caller passes ``unsafe_override``; the
    opt-out is then recorded as a :class:`DatasetSecurityOverride`. Returns the
    new policy plus the enabled/disabled change delta.
    """

    enable_names = [n for n in dict.fromkeys(enable) if n]
    disable_names = [n for n in dict.fromkeys(disable) if n]
    overlap = set(enable_names) & set(disable_names)
    if overlap:
        raise ValueError(
            f"security tool(s) both enabled and disabled: {', '.join(sorted(overlap))}"
        )

    contract_tools = set(policy.required_tools) | set(policy.optional_tools)
    required = set(policy.required_tools)
    disallowed = set(policy.disallowed_tools)
    enabled_set = set(policy.enabled_tools)
    overrides = {o.tool: o for o in policy.overrides}
    before = set(enabled_set)

    for tool in enable_names:
        if tool in disallowed:
            raise ValueError(f"{tool} is disallowed by the workflow security contract")
        if tool not in contract_tools:
            raise ValueError(
                f"{tool} is not part of this dataset's security contract"
            )
        enabled_set.add(tool)
        overrides.pop(tool, None)  # re-enabling a required tool clears its override

    for tool in disable_names:
        if tool not in contract_tools:
            raise ValueError(
                f"{tool} is not part of this dataset's security contract"
            )
        if tool in required:
            if not policy.allow_disable_required:
                raise ValueError(
                    f"{tool} is a required security tool and the workflow contract "
                    "forbids disabling it"
                )
            if not unsafe_override:
                raise ValueError(
                    f"{tool} is a required security tool; pass --unsafe-override to "
                    "disable it (the opt-out is recorded in the manifest)"
                )
            overrides[tool] = DatasetSecurityOverride(tool=tool, reason=reason)
        enabled_set.discard(tool)

    update: dict[str, Any] = {
        "enabled_tools": _canonical_tools(enabled_set),
        "overrides": [
            overrides[name] for name in SECURITY_TOOL_ORDER if name in overrides
        ],
    }
    # A human edit marks the policy as manually managed so downstream consumers
    # can distinguish it from the untouched workflow seed.
    if (enable_names or disable_names) and policy.source != "manual":
        update["source"] = "manual"
    new_policy = policy.model_copy(update=update)
    changes = {
        "enabled": _canonical_tools(set(new_policy.enabled_tools) - before),
        "disabled": _canonical_tools(before - set(new_policy.enabled_tools)),
    }
    return new_policy, changes


def _source_provenance_for_query(
    query: DatasetCandidateQuery | None,
) -> dict[str, Any] | None:
    if query is None:
        return None
    from .bucket_store import bucket_manifest, trace_record_snapshot

    projection: dict[str, Any] | None = None
    try:
        from .search_projection import search_projection_status

        status = search_projection_status()
        if status.get("state") == "ok":
            projection = {
                "name": "search",
                "version": "v1",
                "build_id": status.get("build_id"),
                "manifest_path": status.get("manifest_path"),
                "doc_count": status.get("doc_count"),
                "trace_count": status.get("trace_count"),
            }
    except Exception:
        projection = None
    manifest_snapshot = bucket_manifest(write=False, include_objects=False)
    return {
        "schema_version": SOURCE_PROVENANCE_SCHEMA,
        "bucket_snapshot": trace_record_snapshot(include_objects=False),
        "bucket_manifest": {
            "digest": manifest_snapshot.get("digest"),
            "updated_at": manifest_snapshot.get("updated_at"),
            "trace_records": manifest_snapshot.get("trace_records"),
            "raw_sources": manifest_snapshot.get("raw_sources"),
            "trail_events": manifest_snapshot.get("trail_events"),
            "sync": manifest_snapshot.get("sync"),
        },
        "projection": projection,
        "query_fingerprint": digest_payload(query.model_dump(mode="json")),
    }


def source_provenance_path(root: Path | str) -> Path:
    return Path(root) / ".opentraces" / "source_provenance.json"


def read_source_provenance(root: Path | str) -> dict[str, Any] | None:
    path = source_provenance_path(root)
    if not path.exists():
        return None
    payload = read_json(path)
    return payload if isinstance(payload, dict) else None


def write_source_provenance(root: Path | str, payload: dict[str, Any]) -> None:
    path = source_provenance_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, payload)


def load_manifest(root: Path | str) -> DatasetManifest:
    root = Path(root)
    manifest_path = root / ".opentraces" / "manifest.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if isinstance(raw, dict) and isinstance(raw.get("source_provenance"), dict):
        write_source_provenance(root, raw.pop("source_provenance"))
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


def normalize_hf_repo_id(repo: str, username_hint: str | None = None) -> str:
    if "://" in repo:
        repo = repo.split("://", 1)[1]
    if "/" in repo:
        return repo
    if not username_hint:
        raise ValueError(
            f"'{repo}' is a short name. Use '<owner>/{repo}' or authenticate first."
        )
    return f"{username_hint}/{repo}"


def hf_url(repo_id: str) -> str:
    return repo_id if "://" in repo_id else f"hf://{repo_id}"


def repo_id_from_remote(remote_name: str, remote: DatasetRemote | dict[str, Any]) -> str:
    if isinstance(remote, DatasetRemote):
        url = remote.url
    else:
        url = str(remote.get("url") or remote_name)
    return url.split("://", 1)[1] if "://" in url else url


def add_dataset_remote(
    name: str,
    repo_id: str,
    *,
    visibility: str,
    set_active: bool = False,
) -> DatasetRemoteSummary:
    dataset = load_dataset(name)
    if repo_id in dataset.manifest.remotes:
        raise ValueError(f"{repo_id} is already connected")
    remotes = dict(dataset.manifest.remotes)
    remotes[repo_id] = DatasetRemote(url=hf_url(repo_id), visibility=visibility)
    active = dataset.manifest.active_remote or repo_id
    if set_active:
        active = repo_id
    manifest = dataset.manifest.model_copy(
        update={"remotes": remotes, "active_remote": active}
    )
    save_manifest(dataset.path, manifest)
    return DatasetRemoteSummary(
        dataset_name=name,
        name=repo_id,
        url=hf_url(repo_id),
        visibility=visibility,
        active=manifest.active_remote == repo_id,
    )


def set_dataset_remote_visibility(
    name: str,
    remote_name: str | None,
    *,
    visibility: str,
) -> DatasetRemoteSummary:
    dataset = load_dataset(name)
    resolved = _resolve_dataset_remote_name(dataset.manifest, remote_name)
    remotes = dict(dataset.manifest.remotes)
    remote = remotes[resolved].model_copy(update={"visibility": visibility})
    remotes[resolved] = remote
    manifest = dataset.manifest.model_copy(update={"remotes": remotes})
    save_manifest(dataset.path, manifest)
    return DatasetRemoteSummary(
        dataset_name=name,
        name=resolved,
        url=remote.url,
        visibility=visibility,
        active=manifest.active_remote == resolved,
    )


def remove_dataset_remote(name: str, remote_name: str | None = None) -> DatasetRemoteSummary:
    dataset = load_dataset(name)
    resolved = _resolve_dataset_remote_name(dataset.manifest, remote_name)
    remotes = dict(dataset.manifest.remotes)
    remote = remotes.pop(resolved)
    active = dataset.manifest.active_remote
    if active == resolved:
        active = next(iter(remotes), None)
    manifest = dataset.manifest.model_copy(update={"remotes": remotes, "active_remote": active})
    save_manifest(dataset.path, manifest)
    return DatasetRemoteSummary(
        dataset_name=name,
        name=resolved,
        url=remote.url,
        visibility=remote.visibility,
        active=False,
    )


def list_dataset_remotes(name: str) -> list[DatasetRemoteSummary]:
    dataset = load_dataset(name)
    return [
        DatasetRemoteSummary(
            dataset_name=name,
            name=remote_name,
            url=remote.url,
            visibility=remote.visibility,
            active=dataset.manifest.active_remote == remote_name,
        )
        for remote_name, remote in sorted(dataset.manifest.remotes.items())
    ]


def _resolve_dataset_remote_name(
    manifest: DatasetManifest,
    remote_name: str | None,
) -> str:
    if not manifest.remotes:
        raise ValueError("no remote connected")
    if remote_name is None:
        if len(manifest.remotes) == 1:
            return next(iter(manifest.remotes))
        if manifest.active_remote:
            return manifest.active_remote
        raise ValueError("multiple remotes connected; specify which remote")
    if remote_name not in manifest.remotes:
        raise ValueError(f"remote not found: {remote_name}")
    return remote_name


def append_rows(
    name: str,
    rows: list[dict[str, Any]],
    *,
    run_id: str,
    dry_run: bool = False,
    privacy_tier: str | None = None,
    run_provenance: dict[str, Any] | None = None,
    trail_freshness: list[dict[str, Any]] | None = None,
) -> AppendSummary:
    dataset = load_dataset(name)
    default_privacy_tier = (
        DEFAULT_PRIVACY_TIER
        if dataset.manifest.publication_policy.review == "auto"
        and not dataset.manifest.remotes
        else "medium"
    )
    resolved_privacy_tier = normalize_privacy_tier(
        privacy_tier,
        default=default_privacy_tier,
    )
    schema = read_json(dataset.path / dataset.manifest.schema_ref.path)
    schema_digest = dataset.manifest.schema_ref.digest or digest_payload(schema)
    existing = read_row_index(name)
    existing_identity_hashes = {entry.identity_hash for entry in existing}
    appended_entries: list[DatasetRowIndexEntry] = []
    appended_rows: list[dict[str, Any]] = []
    appended_provenance: list[dict[str, Any]] = []
    duplicate_count = 0
    validation_errors: list[dict[str, Any]] = []
    would_append_count = 0
    current_line = _line_count(dataset.path / "data" / "train.jsonl")
    row_security_by_id: dict[str, DatasetRowSecurity] = {}

    # When the dataset's security policy enables tools, they are authoritative
    # and run over every row. When the policy enables NOTHING (manual/ad-hoc
    # dataset, or a contract whose tools are all disabled), fall back to the
    # coarse privacy-tier mapping so rows never ship below the tier floor.
    security_policy = dataset.manifest.security
    policy_tools: list[str] | None = (
        list(security_policy.enabled_tools) if security_policy.enabled_tools else None
    )

    for index, row in enumerate(rows, start=1):
        sanitized = sanitize_dataset_row(
            row, privacy_tier=resolved_privacy_tier, tools=policy_tools
        )
        row = sanitized.row
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
        row_security_by_id[row_id] = sanitized.security
        provenance = _build_row_provenance(
            row,
            dataset=dataset,
            row_id=row_id,
            run_id=run_id,
            run_provenance=run_provenance,
            trail_freshness=trail_freshness,
            row_security=sanitized.security,
        )
        appended_entries.append(
            DatasetRowIndexEntry(
                row_id=row_id,
                identity_hash=identity_hash,
                payload_hash=payload_hash,
                schema_digest=schema_digest,
                data_file="data/train.jsonl",
                line=current_line,
                run_id=run_id,
                appended_at=utc_now_str(),
                source_trace_id=provenance["source_refs"].get("trace_id"),
                source_unit_id=provenance["source_refs"].get("unit_id"),
                source_slice_id=provenance["source_refs"].get("slice_id"),
                provenance=provenance,
            )
        )
        appended_rows.append(row)
        appended_provenance.append(provenance)
        existing_identity_hashes.add(identity_hash)

    if appended_rows:
        data_file = dataset.path / "data" / "train.jsonl"
        row_index = dataset.path / ".opentraces" / "row_index.jsonl"
        row_provenance = row_provenance_path(name)
        with _append_lock(dataset.path):
            with data_file.open("a", encoding="utf-8") as stream:
                for row in appended_rows:
                    stream.write(_canonical_json(row) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            with row_index.open("a", encoding="utf-8") as stream:
                for entry in appended_entries:
                    stream.write(entry.model_dump_json() + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            with row_provenance.open("a", encoding="utf-8") as stream:
                for provenance in appended_provenance:
                    stream.write(_canonical_json(provenance) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        evaluate_publication_state(
            name,
            row_ids=[entry.row_id for entry in appended_entries],
            row_security=row_security_by_id,
            privacy_tier=resolved_privacy_tier,
        )

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


def row_provenance_path(name: str) -> Path:
    return dataset_path(name) / ".opentraces" / "row_provenance.jsonl"


def read_row_provenance(name: str) -> dict[str, dict[str, Any]]:
    path = row_provenance_path(name)
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("row_id"):
            out[str(payload["row_id"])] = payload
    return out


def _build_row_provenance(
    row: dict[str, Any],
    *,
    dataset: LocalDataset,
    row_id: str,
    run_id: str,
    run_provenance: dict[str, Any] | None,
    trail_freshness: list[dict[str, Any]] | None,
    row_security: DatasetRowSecurity,
) -> dict[str, Any]:
    source_refs = _extract_row_source_refs(row)
    dataset_source = read_source_provenance(dataset.path) or {}
    bucket_snapshot = dataset_source.get("bucket_snapshot") or {}
    bucket_manifest_snapshot = dataset_source.get("bucket_manifest") or {}
    trail_events = bucket_manifest_snapshot.get("trail_events") or {}
    trace_record_ref = _bucket_record_ref(source_refs.get("trace_id"))
    return {
        "schema_version": "opentraces.dataset.row_provenance.v1",
        "row_id": row_id,
        "run_id": run_id,
        "dataset": dataset.name,
        "source_refs": source_refs,
        "workflow": {
            "skill": dataset.manifest.workflow.skill,
            "digest": dataset.manifest.workflow.digest,
            "config": dataset.manifest.workflow.config,
        },
        "bucket": {
            "manifest_digest": bucket_manifest_snapshot.get("digest"),
            "snapshot_digest": bucket_snapshot.get("digest"),
            "object_count": bucket_snapshot.get("object_count"),
            "source_trace_record": trace_record_ref,
        },
        "trail": {
            "freshness": list(trail_freshness or []),
            "event_count": trail_events.get("event_count"),
            "repository_count": trail_events.get("repository_count"),
            "sampled_at": utc_now_str(),
        },
        "privacy": {
            "privacy_tier": row_security.privacy_tier,
            "security_version": row_security.security_version,
            "redactions_applied": row_security.redactions_applied,
        },
        "security_policy": {
            "source": dataset.manifest.security.source,
            "enabled_tools": list(dataset.manifest.security.enabled_tools),
            "required_tools": list(dataset.manifest.security.required_tools),
            "required_satisfied": dataset.manifest.security.required_tools_satisfied(),
            "tools_applied": list(row_security.tools_applied),
        },
        "run": run_provenance or {},
    }


def _extract_row_source_refs(row: dict[str, Any]) -> dict[str, Any]:
    refs: dict[str, Any] = {
        "trace_id": _first_str(row, "source_trace_id", "trace_id"),
        "unit_id": _first_str(row, "source_unit_id", "unit_id", "candidate_id"),
        "slice_id": _first_str(row, "source_slice_id", "slice_id"),
    }
    if isinstance(row.get("source"), dict):
        source = row["source"]
        refs["trace_id"] = refs["trace_id"] or _first_str(source, "trace_id", "id")
        refs["unit_id"] = refs["unit_id"] or _first_str(source, "unit_id", "candidate_id")
        refs["slice_id"] = refs["slice_id"] or _first_str(source, "slice_id")
    for key in ("step_range", "steps", "source_steps"):
        if key in row:
            refs["step_range"] = row[key]
            break
    return {key: value for key, value in refs.items() if value not in (None, "")}


def _bucket_record_ref(trace_id: Any) -> dict[str, Any] | None:
    if not isinstance(trace_id, str) or not trace_id:
        return None
    try:
        from . import paths
        from .bucket_store import iter_trace_record_objects

        for obj in iter_trace_record_objects():
            if obj.trace_id != trace_id:
                continue
            try:
                object_path = obj.path.relative_to(paths.bucket_dir()).as_posix()
            except ValueError:
                object_path = str(obj.path)
            return {
                "trace_id": obj.trace_id,
                "project_slug": obj.project_slug,
                "record_hash": obj.record_hash,
                "object_path": object_path,
            }
    except Exception:
        return None
    return None


def _first_str(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def publication_state_path(name: str) -> Path:
    return dataset_path(name) / ".opentraces" / "publication_state.json"


def read_publication_state(name: str) -> DatasetPublicationState:
    path = publication_state_path(name)
    if not path.exists():
        return DatasetPublicationState()
    return DatasetPublicationState.model_validate_json(path.read_text(encoding="utf-8"))


def write_publication_state(name: str, state: DatasetPublicationState) -> None:
    path = publication_state_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, state.model_dump(mode="json"))


def evaluate_publication_state(
    name: str,
    *,
    row_ids: list[str] | None = None,
    row_security: dict[str, DatasetRowSecurity] | None = None,
    privacy_tier: str | None = None,
) -> DatasetPublicationState:
    """Ensure every row has a publication-state sidecar entry.

    Plan 058 keeps row-index data rebuildable and stores human publication
    decisions separately. This evaluator is intentionally conservative:
    `review: required` creates `needs_review`, while `review: auto` creates
    `publishable`; current Tier 1 scanning can still block either state.
    """

    dataset = load_dataset(name)
    # Plan 092 R10: the required-tools gate is keyed on EXECUTION EVIDENCE, not
    # manifest membership — a row publishes only if the tools that actually ran
    # over it (recorded per-row in provenance) cover the contract's required
    # tools. This blocks rows where a required tool could not run, was disabled
    # via override, or was re-enabled only after the row was appended raw.
    required_tools = set(dataset.manifest.security.required_tools)
    provenance = read_row_provenance(name) if required_tools else {}
    selected = set(row_ids or [])
    state = read_publication_state(name)
    rows_by_id = read_rows_by_id(name)
    for entry in read_row_index(name):
        if selected and entry.row_id not in selected:
            continue
        row = rows_by_id.get(entry.row_id)
        if row is None:
            continue
        existing = state.rows.get(entry.row_id)
        security = (row_security or {}).get(entry.row_id)
        entry_privacy_tier = (
            security.privacy_tier
            if security
            else existing.privacy_tier
            if existing and existing.privacy_tier
            else normalize_privacy_tier(privacy_tier, default=DEFAULT_PRIVACY_TIER)
        )
        entry_security_version = (
            security.security_version
            if security
            else existing.security_version
            if existing and existing.security_version
            else (None if entry_privacy_tier == "off" else SECURITY_VERSION)
        )
        redactions_applied = (
            security.redactions_applied
            if security
            else existing.redactions_applied
            if existing
            else 0
        )
        # Tools that actually ran over this row: fresh from row_security, else
        # the per-row provenance recorded at append time.
        if security is not None:
            row_tools = set(security.tools_applied)
        else:
            row_tools = set(
                (provenance.get(entry.row_id, {}).get("security_policy", {}) or {}).get(
                    "tools_applied", []
                )
            )
        scan = scan_serialized(
            (_canonical_json(row) + "\n").encode("utf-8"),
            include_entropy=entry_privacy_tier != "low",
        )
        block_reasons = sorted({match.pattern_name for match in scan.matches})
        # privacy_tier_off only blocks a genuinely raw row — if a contract's
        # tools actually ran, the row is not raw even at tier "off".
        if entry_privacy_tier == "off" and not row_tools:
            block_reasons.append("privacy_tier_off")
        security_stale = bool(
            entry_privacy_tier != "off" and entry_security_version != SECURITY_VERSION
        )
        if security_stale:
            block_reasons.append("security_version_stale")
        if required_tools - row_tools:
            block_reasons.append("required_security_tools_missing")
        block_reasons = sorted(set(block_reasons))
        if block_reasons:
            status = "blocked"
        elif existing and existing.status in {"rejected", "published", "publishable"}:
            status = existing.status
        else:
            status = (
                "publishable"
                if dataset.manifest.publication_policy.review == "auto"
                else "needs_review"
            )
            block_reasons = []
        state.rows[entry.row_id] = DatasetPublicationStateEntry(
            row_id=entry.row_id,
            status=status,
            uploaded_to=dict(existing.uploaded_to) if existing else {},
            reviewed_at=existing.reviewed_at if existing else None,
            reviewed_by=existing.reviewed_by if existing else None,
            block_reasons=block_reasons,
            security_version=entry_security_version,
            source_security_version=(
                security.security_version
                if security
                else existing.source_security_version
                if existing
                else entry_security_version
            ),
            privacy_tier=entry_privacy_tier,
            security_stale=security_stale,
            redactions_applied=redactions_applied,
            updated_at=utc_now_str(),
        )
    write_publication_state(name, state)
    return state


def set_publication_review_status(
    name: str,
    row_ids: list[str],
    status: str,
    *,
    reviewer: str | None = None,
) -> DatasetPublicationState:
    if status not in {"publishable", "rejected", "reset"}:
        raise ValueError("status must be publishable, rejected, or reset")
    state = evaluate_publication_state(name)
    missing = [row_id for row_id in row_ids if row_id not in state.rows]
    if missing:
        raise ValueError(f"row not found: {', '.join(missing)}")
    for row_id in row_ids:
        entry = state.rows[row_id]
        if entry.status == "blocked":
            continue
        if status == "reset":
            state.rows[row_id] = entry.model_copy(
                update={
                    "status": "publishable"
                    if load_dataset(name).manifest.publication_policy.review == "auto"
                    else "needs_review",
                    "reviewed_at": None,
                    "reviewed_by": None,
                    "updated_at": utc_now_str(),
                }
            )
            continue
        state.rows[row_id] = entry.model_copy(
            update={
                "status": status,
                "reviewed_at": utc_now_str(),
                "reviewed_by": reviewer or "cli",
                "updated_at": utc_now_str(),
            }
        )
    write_publication_state(name, state)
    return state


def read_rows_by_id(name: str) -> dict[str, dict[str, Any]]:
    dataset = load_dataset(name)
    rows: dict[str, dict[str, Any]] = {}
    for entry in read_row_index(name):
        data_file = dataset.path / entry.data_file
        if not data_file.exists():
            continue
        lines = data_file.read_text(encoding="utf-8").splitlines()
        if entry.line - 1 >= len(lines):
            continue
        line = lines[entry.line - 1]
        if line.strip():
            rows[entry.row_id] = json.loads(line)
    return rows


def _row_mean_retention(row: dict[str, Any]) -> float | None:
    """Cluster F D8: mean retention across patches_with_survival.

    Skips ``None`` values (alive_on_path / lost rows have None or 1.0
    mixed with nulls). Returns ``None`` when no defined fractions exist.
    """
    psv = row.get("patches_with_survival") or []
    fractions = [
        p.get("retention_fraction")
        for p in psv
        if isinstance(p, dict) and p.get("retention_fraction") is not None
    ]
    if not fractions:
        return None
    return sum(fractions) / len(fractions)


def _row_has_state(row: dict[str, Any], state: str) -> bool:
    psv = row.get("patches_with_survival") or []
    return any(
        isinstance(p, dict) and p.get("survival_state") == state
        for p in psv
    )


def _filter_rows_for_publish(
    selected_rows: list[tuple[str, dict[str, Any]]],
    *,
    min_retention: float | None,
    exclude_states: list[str] | None,
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    """Cluster F D8: drop rows that fail quality filters.

    Returns ``(kept_rows, filter_summary)``. The summary surfaces:

    * ``min_retention`` — the threshold (or ``None``)
    * ``dropped_min_retention`` — count dropped for low retention
    * ``exclude_states`` — list of states excluded
    * ``dropped_by_state`` — ``{state: count}``
    * ``rows_dropped_total`` — distinct rows dropped (one row may
      satisfy more than one filter)
    """
    summary: dict[str, Any] = {
        "min_retention": min_retention,
        "exclude_states": list(exclude_states or []),
        "dropped_min_retention": 0,
        "dropped_by_state": {state: 0 for state in (exclude_states or [])},
        "rows_dropped_total": 0,
    }
    if min_retention is None and not exclude_states:
        return selected_rows, summary

    kept: list[tuple[str, dict[str, Any]]] = []
    for row_id, row in selected_rows:
        dropped_for_state: list[str] = []
        if exclude_states:
            for state in exclude_states:
                if _row_has_state(row, state):
                    dropped_for_state.append(state)
        below_retention = False
        if min_retention is not None:
            mean = _row_mean_retention(row)
            if mean is not None and mean < min_retention:
                below_retention = True
        if dropped_for_state or below_retention:
            if below_retention:
                summary["dropped_min_retention"] += 1
            for state in dropped_for_state:
                summary["dropped_by_state"][state] += 1
            summary["rows_dropped_total"] += 1
            continue
        kept.append((row_id, row))
    return kept, summary


def publish_dataset(
    name: str,
    *,
    to: str | None = None,
    check_only: bool = False,
    resume: str | None = None,
    contributor: str | None = None,
    token: str | None = None,
    max_retries: int = MAX_PARENT_COMMIT_RETRIES,
    min_retention: float | None = None,
    exclude_states: list[str] | None = None,
) -> DatasetPublishSummary:
    dataset = load_dataset(name)
    remote_name = to or dataset.manifest.active_remote
    if not remote_name:
        raise ValueError(
            "dataset has no active remote; run `opentraces dataset remote create "
            f"{name} <owner/name>` or `opentraces dataset remote add {name} <owner/name>`"
        )
    if remote_name not in dataset.manifest.remotes:
        raise ValueError(f"remote not found: {remote_name}")
    repo_id = repo_id_from_remote(remote_name, dataset.manifest.remotes[remote_name])
    run_id = resume or f"pub_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    contributor_name = contributor or os.environ.get("USER") or "local"

    attempts = 0
    while True:
        attempts += 1
        remote_head_before = _remote_head(repo_id, token)
        _check_remote_schema_not_ahead(dataset, repo_id, token)
        state = evaluate_publication_state(name)
        remote_row_ids = _remote_row_ids(repo_id, dataset.manifest.identity, token)
        rows_by_id = read_rows_by_id(name)
        selected_rows: list[tuple[str, dict[str, Any]]] = []
        duplicate_count = 0
        needs_review_count = 0
        blocked_count = 0
        for row_id, entry in sorted(state.rows.items()):
            if row_id in remote_row_ids or remote_name in entry.uploaded_to:
                duplicate_count += 1
                continue
            if entry.status == "blocked":
                blocked_count += 1
                continue
            if entry.status in {"needs_review", "rejected"}:
                needs_review_count += 1
                continue
            if entry.status in {"publishable", "published"} and row_id in rows_by_id:
                selected_rows.append((row_id, rows_by_id[row_id]))

        # Cluster F D8: row-level filters on patches_with_survival.
        selected_rows, filter_summary = _filter_rows_for_publish(
            selected_rows,
            min_retention=min_retention,
            exclude_states=exclude_states,
        )

        staging_path, staged_files = _stage_publication(
            dataset,
            selected_rows,
            contributor=contributor_name,
            run_id=run_id,
        )
        changed_files = _changed_staged_files(repo_id, staging_path, token)
        # Surface filter telemetry only when filters were configured.
        emitted_filter = (
            filter_summary
            if (min_retention is not None or exclude_states)
            else None
        )
        if not changed_files:
            return DatasetPublishSummary(
                dataset_name=name,
                remote_name=remote_name,
                repo_id=repo_id,
                run_id=run_id,
                uploaded=False,
                check_only=check_only,
                new_row_count=0,
                duplicate_count=duplicate_count,
                needs_review_count=needs_review_count,
                blocked_count=blocked_count,
                staged_files=[],
                remote_head_before=remote_head_before,
                remote_head_after=remote_head_before,
                attempts=attempts,
                message="nothing to publish",
                filter_summary=emitted_filter,
            )
        if check_only:
            return DatasetPublishSummary(
                dataset_name=name,
                remote_name=remote_name,
                repo_id=repo_id,
                run_id=run_id,
                uploaded=False,
                check_only=True,
                new_row_count=len(selected_rows),
                duplicate_count=duplicate_count,
                needs_review_count=needs_review_count,
                blocked_count=blocked_count,
                staged_files=changed_files,
                remote_head_before=remote_head_before,
                remote_head_after=remote_head_before,
                attempts=attempts,
                message="check passed",
                filter_summary=emitted_filter,
            )
        try:
            remote_head_after = _upload_public_surface(
                repo_id,
                staging_path,
                parent_commit=remote_head_before,
                token=token,
            )
        except RemoteHeadConflict:
            if attempts > max_retries:
                raise
            continue
        _mark_rows_uploaded(name, [row_id for row_id, _row in selected_rows], remote_name)
        _append_publication_event(
            dataset.path,
            {
                "run_id": run_id,
                "remote": remote_name,
                "repo_id": repo_id,
                "remote_head_before": remote_head_before,
                "remote_head_after": remote_head_after,
                "files": {
                    path: file_digest(staging_path / path)
                    for path in changed_files
                    if (staging_path / path).is_file()
                },
                "row_ids": [row_id for row_id, _row in selected_rows],
                "published_at": utc_now_str(),
            },
        )
        return DatasetPublishSummary(
            dataset_name=name,
            remote_name=remote_name,
            repo_id=repo_id,
            run_id=run_id,
            uploaded=True,
            check_only=False,
            new_row_count=len(selected_rows),
            duplicate_count=duplicate_count,
            needs_review_count=needs_review_count,
            blocked_count=blocked_count,
            staged_files=changed_files,
            remote_head_before=remote_head_before,
            remote_head_after=remote_head_after,
            attempts=attempts,
            message="published",
            filter_summary=emitted_filter,
        )


def withdraw_dataset_row(
    name: str,
    row_id: str,
    *,
    reason: str,
    hard: bool = False,
    confirm: str | None = None,
) -> DatasetWithdrawalRecord:
    if hard and confirm != "HARD_DELETE":
        raise ValueError("hard delete requires --confirm HARD_DELETE")
    rows = read_rows_by_id(name)
    if row_id not in rows:
        raise ValueError(f"row not found: {row_id}")
    record = DatasetWithdrawalRecord(
        target="row",
        target_id=row_id,
        reason=reason,
        requested_at=utc_now_str(),
    )
    _write_withdrawal_record(name, record)
    if hard:
        _hard_delete_row(name, row_id)
    return record


def forget_trace_cascade(
    trace_id: str,
    *,
    reason: str = "user-request",
) -> dict[str, Any]:
    """Cascade source-trace withdrawal across every local dataset.

    Resolves all dataset rows whose ``source_trace_id`` matches ``trace_id``
    and emits a row-level withdrawal for each one. Datasets with no matching
    rows are untouched.
    """

    affected: list[dict[str, Any]] = []
    for dataset in list_datasets():
        rows = read_rows_by_id(dataset.name)
        matches = [
            row_id
            for row_id, row in rows.items()
            if row.get("source_trace_id") == trace_id
        ]
        if not matches:
            continue
        withdrawn_ids: list[str] = []
        for row_id in matches:
            withdraw_dataset_row(dataset.name, row_id, reason=reason)
            withdrawn_ids.append(row_id)
        affected.append({"dataset": dataset.name, "row_ids": withdrawn_ids})
    return {
        "trace_id": trace_id,
        "reason": reason,
        "affected": affected,
        "withdrawn_count": sum(len(entry["row_ids"]) for entry in affected),
    }


def load_public_rows(name: str, *, apply_withdrawals: bool = True) -> list[dict[str, Any]]:
    """Read public rows, filtering withdrawal tombstones by default.

    Plain ``datasets.load_dataset(...)`` reads raw shards and does not apply
    OpenTraces tombstones. This helper is the OpenTraces wrapper for consumers
    that want withdrawal-aware reads.
    """

    rows = read_rows_by_id(name)
    withdrawn = _withdrawn_row_ids(name) if apply_withdrawals else set()
    return [row for row_id, row in rows.items() if row_id not in withdrawn]


def _write_withdrawal_record(name: str, record: DatasetWithdrawalRecord) -> Path:
    root = dataset_path(name)
    digest = hashlib.sha256(_canonical_json(record.__dict__).encode("utf-8")).hexdigest()[:12]
    timestamp = re.sub(r"[^0-9A-Za-z]+", "", record.requested_at)[:15]
    path = root / "_withdrawals" / f"{timestamp}-local-{digest}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(_canonical_json(record.__dict__) + "\n")
    return path


def _withdrawn_row_ids(name: str) -> set[str]:
    root = dataset_path(name)
    withdrawn: set[str] = set()
    for path in sorted((root / "_withdrawals").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if payload.get("target") == "row" and payload.get("target_id"):
                withdrawn.add(str(payload["target_id"]))
    return withdrawn


def _hard_delete_row(name: str, row_id: str) -> None:
    dataset = load_dataset(name)
    entries = read_row_index(name)
    by_file: dict[str, set[int]] = {}
    for entry in entries:
        if entry.row_id == row_id:
            by_file.setdefault(entry.data_file, set()).add(entry.line)
    for data_file, lines_to_remove in by_file.items():
        path = dataset.path / data_file
        lines = path.read_text(encoding="utf-8").splitlines()
        kept = [
            line
            for line_no, line in enumerate(lines, start=1)
            if line_no not in lines_to_remove
        ]
        path.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
    rebuild_row_index(name)
    state = read_publication_state(name)
    state.rows.pop(row_id, None)
    write_publication_state(name, state)


class RemoteHeadConflict(RuntimeError):
    pass


class DatasetRemotePermissionError(RuntimeError):
    classification = "permission_denied"


class DatasetRemoteSchemaAheadError(RuntimeError):
    def __init__(self, remote_version: str, local_version: str) -> None:
        super().__init__(
            f"Remote dataset schema is {remote_version}; local dataset schema is {local_version}. "
            "Upgrade or migrate before publishing."
        )
        self.remote_version = remote_version
        self.local_version = local_version


class DatasetSecurityFindingsError(RuntimeError):
    """Raised when publication-time security re-scan finds matches."""

    classification = "security_findings"
    exit_code = 8

    def __init__(self, findings: list[dict[str, Any]]) -> None:
        super().__init__(
            f"publication blocked: {len(findings)} security finding(s) in withdrawal records"
        )
        self.findings = findings


def _stage_publication(
    dataset: LocalDataset,
    rows: list[tuple[str, dict[str, Any]]],
    *,
    contributor: str,
    run_id: str,
) -> tuple[Path, list[str]]:
    staging = Path(tempfile.mkdtemp(prefix=f"opentraces-{dataset.name}-{run_id}-"))
    staged: list[str] = []
    schema = read_json(dataset.path / dataset.manifest.schema_ref.path)
    write_json(staging / "dataset_infos.json", build_dataset_infos(dataset.name, schema))
    staged.append("dataset_infos.json")
    (staging / "schemas").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(dataset.path / dataset.manifest.schema_ref.path, staging / dataset.manifest.schema_ref.path)
    staged.append(dataset.manifest.schema_ref.path)
    (staging / "README.md").write_text(
        build_dataset_card(dataset.name, dataset.manifest.description, dataset.manifest),
        encoding="utf-8",
    )
    staged.append("README.md")
    quality = dataset.path / "quality.json"
    if quality.exists():
        shutil.copyfile(quality, staging / "quality.json")
        staged.append("quality.json")
    withdrawals = dataset.path / "_withdrawals"
    if withdrawals.exists():
        scan_findings: list[dict[str, Any]] = []
        for item in sorted(withdrawals.glob("*.jsonl")):
            target = staging / "_withdrawals" / item.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, target)
            staged.append(str(target.relative_to(staging)))
            scan = scan_serialized(item.read_bytes())
            if scan.matches:
                for match in scan.matches:
                    scan_findings.append(
                        {
                            "path": str(target.relative_to(staging)),
                            "pattern": match.pattern_name,
                            "field": getattr(match, "field_type", None)
                            and match.field_type.value,
                        }
                    )
        if scan_findings:
            raise DatasetSecurityFindingsError(scan_findings)
    if rows:
        digest = hashlib.sha256()
        lines: list[str] = []
        for _row_id, row in rows:
            line = _canonical_json(row)
            digest.update(line.encode("utf-8"))
            lines.append(line)
        shard = (
            staging
            / "data"
            / f"{_safe_slug(contributor)}-{run_id}-{digest.hexdigest()[:12]}.jsonl"
        )
        shard.parent.mkdir(parents=True, exist_ok=True)
        shard.write_text("\n".join(lines) + "\n", encoding="utf-8")
        staged.append(str(shard.relative_to(staging)))
    return staging, staged


def _changed_staged_files(repo_id: str, staging: Path, token: str | None) -> list[str]:
    changed: list[str] = []
    remote_root = _fake_remote_dir(repo_id)
    for path in _iter_files(staging):
        rel = str(path.relative_to(staging))
        if rel.startswith(".opentraces/"):
            continue
        if remote_root is None:
            changed.append(rel)
            continue
        remote_path = remote_root / rel
        if not remote_path.exists() or remote_path.read_bytes() != path.read_bytes():
            changed.append(rel)
    return sorted(changed)


def _remote_card_text(repo_id: str, token: str | None = None) -> str | None:
    """Return the remote dataset card (README.md) text, or ``None`` if absent.

    ``None`` is the "treat remote as fresh" signal: no card on the remote yet
    (first publish) or an unreadable / unreachable remote. Matching the
    HFUploader's lenient stance, a missing card or a network blip never blocks a
    publish — we would rather stamp our own fresh schema than fail on a hiccup.

    The fake-remote path (Plan 058 tests) is preserved byte-for-byte: read the
    on-disk README.md if it exists, else return ``None``.
    """
    remote_root = _fake_remote_dir(repo_id)
    if remote_root is not None:
        card = remote_root / "README.md"
        if card.exists():
            return card.read_text(encoding="utf-8")
        return None

    try:
        from huggingface_hub.errors import (  # type: ignore
            EntryNotFoundError,
            RepositoryNotFoundError,
        )
    except ImportError:  # older huggingface_hub
        from huggingface_hub.utils import (  # type: ignore
            EntryNotFoundError,
            RepositoryNotFoundError,
        )

    from huggingface_hub import HfApi

    try:
        local_path = HfApi(token=token).hf_hub_download(
            repo_id=repo_id,
            filename="README.md",
            repo_type="dataset",
        )
    except (EntryNotFoundError, RepositoryNotFoundError):
        return None
    except Exception as e:  # network blip / unexpected 404 variant
        logger.debug("Could not fetch README.md for %s: %s", repo_id, e)
        return None

    try:
        return Path(local_path).read_text(encoding="utf-8")
    except OSError as e:
        logger.debug("README.md at %s is not readable: %s", repo_id, e)
        return None


def _check_remote_schema_not_ahead(
    dataset: LocalDataset, repo_id: str, token: str | None = None
) -> None:
    card_text = _remote_card_text(repo_id, token)
    if card_text is None:
        return
    contract = _read_card_contract(card_text)
    remote_schema = contract.get("schema") if isinstance(contract, dict) else None
    remote_version = remote_schema.get("version") if isinstance(remote_schema, dict) else None
    local_version = dataset.manifest.schema_ref.version
    if _semver_tuple(remote_version) and _semver_tuple(local_version):
        if _semver_tuple(remote_version) > _semver_tuple(local_version):
            raise DatasetRemoteSchemaAheadError(str(remote_version), local_version)


def _upload_public_surface(
    repo_id: str,
    staging: Path,
    *,
    parent_commit: str | None,
    token: str | None,
) -> str:
    remote_root = _fake_remote_dir(repo_id)
    if remote_root is not None:
        _fake_upload_folder(repo_id, staging, parent_commit=parent_commit)
        return _remote_head(repo_id, token) or ""
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.upload_folder(
        repo_id=repo_id,
        repo_type="dataset",
        folder_path=str(staging),
        allow_patterns=PUBLIC_SURFACE_PATTERNS,
        ignore_patterns=[".opentraces/**"],
        create_pr=False,
        parent_commit=parent_commit,
    )
    return _remote_head(repo_id, token) or ""


def _remote_head(repo_id: str, token: str | None) -> str | None:
    remote_root = _fake_remote_dir(repo_id)
    if remote_root is not None:
        remote_root.mkdir(parents=True, exist_ok=True)
        meta = remote_root / ".fake_head"
        if not meta.exists():
            meta.write_text("fake-head-0\n", encoding="utf-8")
        return meta.read_text(encoding="utf-8").strip()
    from huggingface_hub import HfApi

    info = HfApi(token=token).dataset_info(repo_id)
    return getattr(info, "sha", None)


def _remote_row_ids(
    repo_id: str,
    identity: DatasetIdentity,
    token: str | None,
) -> set[str]:
    rows: set[str] = set()
    remote_root = _fake_remote_dir(repo_id)
    if remote_root is None:
        return rows
    data_dir = remote_root / "data"
    if not data_dir.exists():
        return rows
    for shard in sorted(data_dir.glob("*.jsonl")):
        for line in shard.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            identity_hash = row_identity_hash(row, identity)
            rows.add(f"row_{identity_hash.removeprefix('sha256:')[:16]}")
    return rows


def _fake_upload_folder(repo_id: str, staging: Path, *, parent_commit: str | None) -> None:
    remote_root = _fake_remote_dir(repo_id)
    if remote_root is None:
        raise RuntimeError("fake remote root is not configured")
    remote_root.mkdir(parents=True, exist_ok=True)
    current = _remote_head(repo_id, None)
    if parent_commit and current and parent_commit != current:
        raise RemoteHeadConflict(f"remote head moved: {current}")
    if os.environ.get("OPENTRACES_PLAN058_FAKE_DENY_WRITE"):
        raise DatasetRemotePermissionError(f"write access denied for {repo_id}")
    _maybe_fake_conflict_once(remote_root)
    _maybe_fake_conflict(remote_root)
    for path in _iter_files(staging):
        rel = path.relative_to(staging)
        rel_text = str(rel)
        if rel_text.startswith(".opentraces/"):
            continue
        if not _matches_public_surface(rel_text):
            continue
        target = remote_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    _refresh_fake_head(remote_root)


def _maybe_fake_conflict_once(remote_root: Path) -> None:
    conflict_row = os.environ.get("OPENTRACES_PLAN058_FAKE_CONFLICT_ROW")
    if not conflict_row:
        return
    marker = remote_root / ".fake_conflict_once"
    if marker.exists():
        return
    marker.write_text("done\n", encoding="utf-8")
    row = json.loads(conflict_row)
    shard = remote_root / "data" / "concurrent-conflict.jsonl"
    shard.parent.mkdir(parents=True, exist_ok=True)
    with shard.open("a", encoding="utf-8") as stream:
        stream.write(_canonical_json(row) + "\n")
    _refresh_fake_head(remote_root)
    raise RemoteHeadConflict("simulated concurrent remote update")


def _maybe_fake_conflict(remote_root: Path) -> None:
    """Raise a configured number of fake remote-head conflicts."""

    raw = os.environ.get("OPENTRACES_PLAN058_FAKE_CONFLICT_COUNT")
    if not raw:
        return
    try:
        budget = int(raw)
    except ValueError:
        return
    if budget <= 0:
        return
    counter_path = remote_root / ".fake_conflict_count"
    seen = 0
    if counter_path.exists():
        try:
            seen = int(counter_path.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            seen = 0
    if seen >= budget:
        return
    counter_path.write_text(f"{seen + 1}\n", encoding="utf-8")
    _refresh_fake_head(remote_root)
    raise RemoteHeadConflict(
        f"simulated concurrent remote update ({seen + 1}/{budget})"
    )


def _refresh_fake_head(remote_root: Path) -> None:
    digest = hashlib.sha256()
    for path in _iter_files(remote_root):
        if path.name in {".fake_head", ".fake_conflict_once"}:
            continue
        digest.update(str(path.relative_to(remote_root)).encode("utf-8"))
        digest.update(path.read_bytes())
    (remote_root / ".fake_head").write_text(f"fake-{digest.hexdigest()[:16]}\n", encoding="utf-8")


def _read_card_contract(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
    contract = frontmatter.get("opentraces")
    return contract if isinstance(contract, dict) else {}


def _fake_remote_dir(repo_id: str) -> Path | None:
    root = os.environ.get("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT")
    if not root:
        return None
    owner, _, name = repo_id.partition("/")
    if not owner or not name:
        return Path(root) / repo_id
    return Path(root) / owner / name


def fake_remote_probe(repo_id: str) -> dict[str, Any] | None:
    remote_root = _fake_remote_dir(repo_id)
    if remote_root is None or not remote_root.exists():
        return None
    meta = _fake_remote_meta(remote_root)
    return {"id": repo_id, "private": bool(meta.get("private", True))}


def fake_remote_create(repo_id: str, private: bool) -> bool:
    remote_root = _fake_remote_dir(repo_id)
    if remote_root is None:
        return False
    if remote_root.exists():
        return False
    remote_root.mkdir(parents=True)
    _write_fake_remote_meta(remote_root, {"private": private})
    _remote_head(repo_id, None)
    return True


def fake_remote_delete(repo_id: str) -> None:
    remote_root = _fake_remote_dir(repo_id)
    if remote_root is not None and remote_root.exists():
        shutil.rmtree(remote_root)


def fake_remote_set_visibility(repo_id: str, private: bool) -> None:
    remote_root = _fake_remote_dir(repo_id)
    if remote_root is None:
        raise FileNotFoundError(repo_id)
    remote_root.mkdir(parents=True, exist_ok=True)
    meta = _fake_remote_meta(remote_root)
    meta["private"] = private
    _write_fake_remote_meta(remote_root, meta)


def _fake_remote_meta(remote_root: Path) -> dict[str, Any]:
    meta_path = remote_root / ".fake_meta.json"
    if not meta_path.exists():
        return {"private": True}
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _write_fake_remote_meta(remote_root: Path, meta: dict[str, Any]) -> None:
    write_json(remote_root / ".fake_meta.json", meta)


def _mark_rows_uploaded(name: str, row_ids: list[str], remote_name: str) -> None:
    state = read_publication_state(name)
    now = utc_now_str()
    for row_id in row_ids:
        entry = state.rows[row_id]
        uploaded_to = dict(entry.uploaded_to)
        uploaded_to[remote_name] = now
        state.rows[row_id] = entry.model_copy(
            update={
                "status": "published",
                "uploaded_to": uploaded_to,
                "updated_at": now,
            }
        )
    write_publication_state(name, state)


def _append_publication_event(root: Path, event: dict[str, Any]) -> None:
    path = root / ".opentraces" / "publications.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(_canonical_json(event) + "\n")


def _last_publication_event(root: Path) -> dict[str, Any] | None:
    path = root / ".opentraces" / "publications.jsonl"
    if not path.exists():
        return None
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return events[-1] if events else None


def _iter_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _matches_public_surface(rel: str) -> bool:
    return (
        rel in {"README.md", "dataset_infos.json", "quality.json"}
        or rel.startswith("schemas/")
        or rel.startswith("data/")
        or rel.startswith("_withdrawals/")
    )


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "local"


def _semver_tuple(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None
    parts = value.split(".")
    if len(parts) != 3:
        return None
    try:
        return tuple(int(part) for part in parts)  # type: ignore[return-value]
    except ValueError:
        return None


def rebuild_row_index(name: str) -> RebuildSummary:
    dataset = load_dataset(name)
    schema = read_json(dataset.path / dataset.manifest.schema_ref.path)
    schema_digest = dataset.manifest.schema_ref.digest or digest_payload(schema)
    entries: list[DatasetRowIndexEntry] = []
    rebuild_run_id = f"rebuild_{utc_now_str()}"
    for data_file in _iter_data_files(dataset.path):
        relative = data_file.relative_to(dataset.path).as_posix()
        for line_no, line in enumerate(
            data_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
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
                    data_file=relative,
                    line=line_no,
                    run_id=rebuild_run_id,
                    appended_at=utc_now_str(),
                )
            )
    row_index = dataset.path / ".opentraces" / "row_index.jsonl"
    row_index.write_text(
        "".join(entry.model_dump_json() + "\n" for entry in entries),
        encoding="utf-8",
    )
    digest = digest_payload([entry.model_dump(mode="json") for entry in entries])
    return RebuildSummary(dataset_name=name, rebuilt_count=len(entries), digest=digest)


def _iter_data_files(root: Path) -> list[Path]:
    files = sorted((root / "data").glob("*.jsonl"))
    if not files:
        files = [root / "data" / "train.jsonl"]
    return files


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
    frontmatter_payload: dict[str, Any] = {
        "configs": [
            {
                "config_name": "default",
                "data_files": [{"split": "train", "path": "data/*.jsonl"}],
            }
        ],
        "tags": tags,
        "opentraces": {
            "name": manifest.name,
            "description": manifest.description,
            "schema": manifest.schema_ref.model_dump(mode="json"),
            "workflow": manifest.workflow.model_dump(mode="json"),
            "identity": manifest.identity.model_dump(mode="json"),
            "publication_policy": manifest.publication_policy.model_dump(mode="json"),
        },
    }
    if manifest.discoverability.license:
        frontmatter_payload["license"] = manifest.discoverability.license
    frontmatter = yaml.safe_dump(
        frontmatter_payload,
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


@contextlib.contextmanager
def _append_lock(root: Path, *, timeout: float = 30.0):
    """Best-effort advisory lock around dataset row appends.

    fcntl-based on POSIX; falls back to an O_EXCL polling sentinel on
    platforms without fcntl. Only protects cooperating callers — direct
    writes to ``data/train.jsonl`` bypass the lock.
    """

    lock_path = root / ".opentraces" / ".append.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is not None:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        return
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.close(fd)
            break
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            if time.monotonic() >= deadline:
                raise TimeoutError(f"append lock timeout: {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


