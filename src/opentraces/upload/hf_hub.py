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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from huggingface_hub import HfApi

logger = logging.getLogger(__name__)

from opentraces_schema.models import TraceRecord


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
        return str(repo_url)

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
                    path_or_fileobj=io.BytesIO(data),
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

    def list_opentraces_datasets(self, username: str | None = None) -> list[dict]:
        """List HuggingFace datasets tagged with 'opentraces'.

        If username is provided, only returns datasets owned by that user.
        Returns a list of dicts with keys: id, tags, private, last_modified.
        """
        try:
            datasets = self.api.list_datasets(
                search="opentraces",
                author=username,
                limit=50,
            )

            results = []
            for ds in datasets:
                tags = list(ds.tags) if ds.tags else []
                if "opentraces" not in tags:
                    continue
                results.append({
                    "id": ds.id,
                    "tags": tags,
                    "private": ds.private,
                    "last_modified": str(ds.last_modified) if ds.last_modified else None,
                })
            return results
        except Exception as e:
            logger.warning("Failed to list opentraces datasets: %s", e)
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
                path_or_fileobj=io.BytesIO(data),
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

    def fetch_all_remote_traces(self) -> list[TraceRecord]:
        """Download and parse every trace record from all existing remote shards.

        Used after a push to build an accurate aggregate dataset card.
        Shards are cached locally by huggingface_hub after the first download,
        so this is fast when called soon after fetch_remote_content_hashes().
        Returns an empty list if the repo has no shards or on total failure.
        """
        shards = self.get_existing_shards()
        if not shards:
            return []

        records: list[TraceRecord] = []
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
                        records.append(TraceRecord.model_validate_json(line))
                    except Exception:
                        continue
            except Exception as e:
                logger.warning("Could not fetch shard %s: %s", shard_path, e)
        return records

    def fetch_remote_content_hashes(self) -> set[str]:
        """Fetch content_hash values from all existing remote shards.

        Best-effort: individual shard failures are logged and skipped.
        Returns an empty set if the repo has no shards or on total failure.
        """
        shards = self.get_existing_shards()
        if not shards:
            return set()

        hashes: set[str] = set()
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
                        ch = record.get("content_hash")
                        if ch:
                            hashes.add(ch)
                    except json.JSONDecodeError:
                        continue
            except Exception as e:
                logger.warning("Could not fetch shard %s: %s", shard_path, e)
        return hashes
