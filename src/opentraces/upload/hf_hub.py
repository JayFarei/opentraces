"""Sharded JSONL upload to HuggingFace Hub.

Each push creates a NEW JSONL shard file (never appends to existing).
Filename pattern: traces_{timestamp}_{uuid_short}.jsonl
"""

from __future__ import annotations

import io
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from huggingface_hub import HfApi
from huggingface_hub.utils import HfHubHTTPError

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
        except Exception:
            # Tagging is best-effort, not all API versions support update_repo_settings
            pass
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
            except HfHubHTTPError as e:
                last_error = str(e)
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.BASE_DELAY * (2**attempt)
                    time.sleep(delay)
            except Exception as e:
                last_error = str(e)
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.BASE_DELAY * (2**attempt)
                    time.sleep(delay)

        return UploadResult(
            shard_name=shard_name,
            trace_count=len(traces),
            repo_url=repo_url,
            success=False,
            error=f"Upload failed after {self.MAX_RETRIES} retries: {last_error}",
        )

    def publish_dataset(self, repo_id: str) -> None:
        """Change a private dataset to public."""
        self.api.update_repo_settings(
            repo_id=repo_id,
            repo_type="dataset",
            private=False,
        )

    def set_gated(self, repo_id: str, gated: str = "auto") -> None:
        """Enable gated access on a dataset."""
        self.api.update_repo_settings(
            repo_id=repo_id,
            repo_type="dataset",
            gated=gated,
        )

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
        except Exception:
            return []
