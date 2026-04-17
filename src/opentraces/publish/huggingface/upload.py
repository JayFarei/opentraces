"""Sharded JSONL upload to HuggingFace Hub.

Each push creates a NEW JSONL shard file (never appends to existing).
Filename pattern: traces_{timestamp}_{uuid_short}.jsonl
"""

from __future__ import annotations

import io
import json
import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import os

from huggingface_hub import HfApi

logger = logging.getLogger(__name__)

from opentraces_schema.models import TraceRecord


# ---------------------------------------------------------------------------
# Part B: HF token resolution (3 sources, mirrors pi-share-hf DX)
# ---------------------------------------------------------------------------

# Exposed as module-level so tests can monkeypatch a tmp_path.
_HF_CACHE_TOKEN_PATH: Path = Path.home() / ".cache" / "huggingface" / "token"

_LOGIN_HINT = (
    "No HuggingFace token found. Run 'opentraces auth login' or "
    "'hf auth login' to authenticate,"
    "or set HF_TOKEN / HUGGINGFACE_TOKEN in your environment."
)


def verify_hf_token() -> tuple[str | None, str]:
    """Resolve a HuggingFace token from the 3 standard locations.

    Checks in order:
      1. ``HF_TOKEN`` environment variable
      2. ``HUGGINGFACE_TOKEN`` environment variable
      3. ``~/.cache/huggingface/token`` file

    Returns:
        ``(token, source_description)`` on success.
        ``(None, diagnostic_message)`` when no token is available.
        The diagnostic always mentions how the user can log in.
    """
    env_token = os.environ.get("HF_TOKEN", "").strip()
    if env_token:
        return env_token, "HF_TOKEN environment variable"

    env_token = os.environ.get("HUGGINGFACE_TOKEN", "").strip()
    if env_token:
        return env_token, "HUGGINGFACE_TOKEN environment variable"

    try:
        if _HF_CACHE_TOKEN_PATH.exists():
            file_token = _HF_CACHE_TOKEN_PATH.read_text().strip()
            if file_token:
                return file_token, f"cache file {_HF_CACHE_TOKEN_PATH}"
    except OSError as exc:
        logger.debug("Could not read HF token cache: %s", exc)

    return None, _LOGIN_HINT


class RemoteShardError(RuntimeError):
    """Raised when a remote shard is unavailable during dedup or stats fetch.

    Extends RuntimeError so the push command's existing error handler
    (``except RuntimeError``) aborts the push cleanly without a traceback.
    """


@dataclass
class UploadResult:
    """Result of an upload operation."""

    shard_name: str
    trace_count: int
    repo_url: str
    success: bool
    error: str | None = None


class HFUploader:
    """Uploads trace batches as sharded JSONL files to HuggingFace Hub."""

    MAX_RETRIES = 3
    BASE_DELAY = 1.0  # seconds

    def __init__(self, token: str, repo_id: str) -> None:
        """Initialize with HF token and dataset repo ID."""
        self.token = token
        self.repo_id = repo_id
        self.api = HfApi(token=token)

    def ensure_repo_exists(self, private: bool = False) -> str:
        """Create the dataset repo if it doesn't exist. Return repo URL."""
        repo_url = self.api.create_repo(
            repo_id=self.repo_id,
            repo_type="dataset",
            exist_ok=True,
            private=private,
        )
        # Tag the repo
        try:
            self.api.update_repo_settings(
                repo_id=self.repo_id,
                repo_type="dataset",
                tags=["opentraces", "agent-traces"],
            )
        except Exception as e:
            # Tagging is best-effort, not all API versions support update_repo_settings
            logger.debug("Could not tag repo %s: %s", self.repo_id, e)

        # Upload dataset_infos.json so HF datasets-server never has to infer
        # the schema from raw shards. Without this, schema evolution across
        # pushes causes config-info failures (null field in shard-1 conflicts
        # with string field in shard-2).
        self._upload_dataset_infos()

        return str(repo_url)

    def _upload_dataset_infos(self) -> None:
        """Upload dataset_infos.json to declare the full schema to HF."""
        try:
            from .schema import build_dataset_infos
            repo_name = self.repo_id.split("/")[-1]
            infos = build_dataset_infos(repo_name)
            data = json.dumps(infos, indent=2).encode("utf-8")
            self.api.upload_file(
                path_or_fileobj=data,
                path_in_repo="dataset_infos.json",
                repo_id=self.repo_id,
                repo_type="dataset",
                commit_message=f"chore: declare schema for HF datasets-server (opentraces-schema)",
            )
            logger.debug("Uploaded dataset_infos.json to %s", self.repo_id)
        except Exception as e:
            logger.warning("Could not upload dataset_infos.json to %s: %s", self.repo_id, e)

    def _generate_shard_name(self) -> str:
        """Generate a unique shard filename."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        short_uuid = uuid.uuid4().hex[:8]
        return f"traces_{ts}_{short_uuid}.jsonl"

    def _serialize_traces(self, traces: list[TraceRecord]) -> bytes:
        """Serialize traces to JSONL bytes."""
        lines = []
        for trace in traces:
            lines.append(trace.to_jsonl_line())
        content = "\n".join(lines) + "\n"
        return content.encode("utf-8")

    def upload_traces(self, traces: list[TraceRecord]) -> UploadResult:
        """Upload a batch of traces as a new JSONL shard.

        Returns UploadResult with shard_name, trace_count, repo_url, success, error.
        Retries with exponential backoff on transient failures (max 3 retries).
        """
        if not traces:
            return UploadResult(
                shard_name="",
                trace_count=0,
                repo_url=f"https://huggingface.co/datasets/{self.repo_id}",
                success=False,
                error="No traces to upload",
            )

        shard_name = self._generate_shard_name()
        data = self._serialize_traces(traces)
        repo_url = f"https://huggingface.co/datasets/{self.repo_id}"

        last_error: str | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                self.api.upload_file(
                    path_or_fileobj=data,
                    path_in_repo=f"data/{shard_name}",
                    repo_id=self.repo_id,
                    repo_type="dataset",
                )
                return UploadResult(
                    shard_name=shard_name,
                    trace_count=len(traces),
                    repo_url=repo_url,
                    success=True,
                )
            except Exception as e:
                last_error = str(e)
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.BASE_DELAY * (2**attempt)
                    logger.warning(
                        "Upload attempt %d/%d failed: %s (retrying in %.1fs)",
                        attempt + 1, self.MAX_RETRIES, last_error, delay,
                    )
                    time.sleep(delay)

        return UploadResult(
            shard_name=shard_name,
            trace_count=len(traces),
            repo_url=repo_url,
            success=False,
            error=f"Upload failed after {self.MAX_RETRIES} retries: {last_error}",
        )

    def publish_dataset(self) -> None:
        """Change a private dataset to public."""
        self.api.update_repo_settings(
            repo_id=self.repo_id,
            repo_type="dataset",
            private=False,
        )

    def set_gated(self, gated: str = "auto") -> None:
        """Enable gated access on a dataset."""
        self.api.update_repo_settings(
            repo_id=self.repo_id,
            repo_type="dataset",
            gated=gated,
        )

    def list_user_datasets(self, username: str | None = None) -> list[dict]:
        """List a user's HuggingFace datasets, flagging opentraces-tagged ones.

        If username is provided, only returns datasets owned by that user.
        Returns a list of dicts with keys: id, tags, private, last_modified, tagged.
        ``tagged`` is True when the dataset carries the 'opentraces' tag, so
        callers can group or prioritise known opentraces inboxes.

        Previously this method passed ``search="opentraces"`` to HF, which is a
        name/description filter, not a tag filter; tagged datasets whose names
        did not contain the string "opentraces" were silently dropped.
        """
        try:
            datasets = self.api.list_datasets(
                author=username,
                limit=100,
            )

            results = []
            for ds in datasets:
                tags = list(ds.tags) if ds.tags else []
                results.append({
                    "id": ds.id,
                    "tags": tags,
                    "private": ds.private,
                    "last_modified": str(ds.last_modified) if ds.last_modified else None,
                    "tagged": "opentraces" in tags,
                })
            return results
        except Exception as e:
            logger.warning("Failed to list datasets for %s: %s", username, e)
            return []

    def get_existing_shards(self) -> list[str]:
        """List existing trace shard files in the repo."""
        try:
            files = self.api.list_repo_files(
                repo_id=self.repo_id,
                repo_type="dataset",
            )
            return [
                f for f in files
                if f.startswith("data/traces_") and f.endswith(".jsonl")
            ]
        except Exception as e:
            logger.warning("Failed to list shards for %s: %s", self.repo_id, e)
            return []

    def upload_quality_json(self, summary_dict: dict) -> bool:
        """Upload quality.json sidecar to the dataset repo.

        Returns True on success, False on failure.
        """
        try:
            data = json.dumps(summary_dict, indent=2).encode("utf-8")
            self.api.upload_file(
                path_or_fileobj=data,
                path_in_repo="quality.json",
                repo_id=self.repo_id,
                repo_type="dataset",
                commit_message="chore: update quality scores",
            )
            return True
        except Exception as e:
            logger.warning("Failed to upload quality.json: %s", e)
            return False

    def fetch_quality_json(self) -> dict | None:
        """Fetch quality.json sidecar from the dataset repo.

        Returns parsed dict on success, None if not found or on error.
        """
        try:
            local_path = self.api.hf_hub_download(
                repo_id=self.repo_id,
                filename="quality.json",
                repo_type="dataset",
            )
            return json.loads(Path(local_path).read_text())
        except Exception as e:
            logger.debug("Could not fetch quality.json from %s: %s", self.repo_id, e)
            return None

    def iter_remote_traces(self) -> Iterator[TraceRecord]:
        """Stream every trace record from all existing remote shards one at a time.

        Yields one TraceRecord per line, processing one shard before downloading
        the next so only one shard lives in memory at a time.
        Shards are cached locally by huggingface_hub after the first download.
        Raises RemoteShardError on any shard failure: partial data would produce
        incorrect stats and quality scores, so fail-closed is safer.
        """
        shards = self.get_existing_shards()
        if not shards:
            return

        for shard_path in shards:
            try:
                local_path = self.api.hf_hub_download(
                    repo_id=self.repo_id,
                    filename=shard_path,
                    repo_type="dataset",
                )
                for line in Path(local_path).read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield TraceRecord.model_validate_json(line)
                    except Exception:
                        continue
            except RemoteShardError:
                raise
            except Exception as e:
                raise RemoteShardError(
                    f"Shard {shard_path} unavailable: {e}. "
                    "Retry when the shard is accessible."
                ) from e

    def fetch_all_remote_traces(self) -> list[TraceRecord]:
        """Download and parse every trace record from all existing remote shards.

        Convenience wrapper around iter_remote_traces() that returns a list.
        Used after a push to build an accurate aggregate dataset card.
        Shards are cached locally by huggingface_hub after the first download,
        so this is fast when called soon after fetch_remote_content_hashes().
        Returns an empty list if the repo has no shards or on total failure.
        """
        return list(self.iter_remote_traces())

    def detect_outdated_shards(self, target_version: str) -> dict:
        """Inspect remote shards and report which contain records with a
        schema_version != target_version.

        Returns a dict:
          {
            "shards": [{"path": str, "outdated_records": int, "versions": {ver: count}}],
            "version_counts": {ver: total_records_across_repo},
            "total_outdated": int,
          }
        Raises RemoteShardError on shard fetch failure.
        """
        shards = self.get_existing_shards()
        report: dict = {"shards": [], "version_counts": {}, "total_outdated": 0}
        if not shards:
            return report

        for shard_path in shards:
            try:
                local_path = self.api.hf_hub_download(
                    repo_id=self.repo_id,
                    filename=shard_path,
                    repo_type="dataset",
                )
            except Exception as e:
                raise RemoteShardError(
                    f"Cannot inspect shard {shard_path}: {e}"
                ) from e

            per_shard: dict[str, int] = {}
            outdated = 0
            for line in Path(local_path).read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ver = str(rec.get("schema_version") or "unknown")
                per_shard[ver] = per_shard.get(ver, 0) + 1
                report["version_counts"][ver] = report["version_counts"].get(ver, 0) + 1
                if ver != target_version:
                    outdated += 1

            if outdated:
                report["shards"].append({
                    "path": shard_path,
                    "outdated_records": outdated,
                    "versions": per_shard,
                })
                report["total_outdated"] += outdated

        return report

    def migrate_outdated_shards(self, target_version: str) -> dict:
        """Re-parse and re-upload shards containing records with schema_version
        != target_version. Pydantic fills in any newly-added fields with their
        defaults; the resulting record is then stamped with target_version.

        Returns:
          {
            "migrated_shards": [shard_path, ...],
            "migrated_records": int,
            "skipped_shards": int,
            "errors": [{"shard": ..., "error": ...}],
          }
        """
        result: dict = {
            "migrated_shards": [],
            "migrated_records": 0,
            "skipped_shards": 0,
            "errors": [],
        }
        report = self.detect_outdated_shards(target_version)
        for entry in report["shards"]:
            shard_path = entry["path"]
            try:
                local_path = self.api.hf_hub_download(
                    repo_id=self.repo_id,
                    filename=shard_path,
                    repo_type="dataset",
                )
                migrated_lines: list[str] = []
                migrated_count = 0
                for line in Path(local_path).read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = TraceRecord.model_validate_json(line)
                    except Exception as e:
                        # Unparseable record: keep the original line verbatim
                        # so we never destroy data we don't understand.
                        logger.warning(
                            "Skipping unparseable record in %s: %s", shard_path, e
                        )
                        migrated_lines.append(line)
                        continue
                    if record.schema_version != target_version:
                        record = record.model_copy(update={"schema_version": target_version})
                        migrated_count += 1
                    migrated_lines.append(record.to_jsonl_line())

                if migrated_count == 0:
                    result["skipped_shards"] += 1
                    continue

                content = ("\n".join(migrated_lines) + "\n").encode("utf-8")
                self.api.upload_file(
                    path_or_fileobj=content,
                    path_in_repo=shard_path,
                    repo_id=self.repo_id,
                    repo_type="dataset",
                    commit_message=f"chore: migrate {migrated_count} record(s) to schema {target_version}",
                )
                result["migrated_shards"].append(shard_path)
                result["migrated_records"] += migrated_count
            except Exception as e:
                result["errors"].append({"shard": shard_path, "error": str(e)})
        return result

    def fetch_remote_content_hashes(self) -> set[str]:
        """Fetch content_hash values from all existing remote shards.

        Raises RemoteShardError if any shard is unavailable. Proceeding with
        a partial hash set would silently allow duplicate traces to be uploaded,
        so fail-closed is the only safe behavior here.
        """
        return self.fetch_remote_index().content_hashes

    def fetch_remote_index(self) -> "RemoteIndex":
        """One-pass scan of all shards yielding dedup + lineage info.

        Returns a ``RemoteIndex`` carrying both the ``content_hash`` set
        used by the push-time dedup filter and ``{session_id:
        max(generation_index)}`` used by the TUI's "supersedes remote"
        hint. Fail-closed: if any shard is unavailable, raises
        ``RemoteShardError`` so callers don't silently miss duplicates
        or lineage.
        """
        from .remote_index import RemoteIndex

        index = RemoteIndex.empty(self.repo_id)
        shards = self.get_existing_shards()
        if not shards:
            return index

        for shard_path in shards:
            try:
                local_path = self.api.hf_hub_download(
                    repo_id=self.repo_id,
                    filename=shard_path,
                    repo_type="dataset",
                )
                for line in Path(local_path).read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ch = record.get("content_hash")
                    if ch:
                        index.content_hashes.add(ch)
                    sid = record.get("session_id")
                    gen = record.get("generation_index")
                    if sid and isinstance(gen, int):
                        prior = index.session_generations.get(sid, -1)
                        if gen > prior:
                            index.session_generations[sid] = gen
            except RemoteShardError:
                raise
            except Exception as e:
                raise RemoteShardError(
                    f"Cannot safely dedup: shard {shard_path} unavailable: {e}. "
                    "Retry when the shard is accessible."
                ) from e
        return index
