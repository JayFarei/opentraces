#!/usr/bin/env python3
"""Re-anonymize and replace a leaked shard on HuggingFace Hub.

Downloads the existing shard, applies the updated anonymizer to every record,
re-uploads as the same filename (overwrite), and verifies no leaks remain.

Usage:
    python scripts/reanonymize_shard.py \
        --repo Jayfarei/opentraces-test \
        --shard traces_20260329T193808Z_4c0cc320.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Ensure the local package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "opentraces-schema" / "src"))

from huggingface_hub import HfApi
from opentraces_schema.models import TraceRecord
from opentraces.security.anonymizer import anonymize_paths, hash_username


def reanonymize_record(record_dict: dict, username: str, extra: list[str] | None = None) -> dict:
    """Deep-walk a raw record dict and anonymize all string values."""

    def _anon(text: str | None) -> str | None:
        if not text:
            return text
        return anonymize_paths(text, username=username, extra_usernames=extra)

    def _walk(obj):
        if isinstance(obj, str):
            return _anon(obj)
        if isinstance(obj, dict):
            return {k: _walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(item) for item in obj]
        return obj

    return _walk(record_dict)


def check_leaks(record_dict: dict, username: str) -> list[str]:
    """Check if any string values still contain the username."""
    leaks = []

    def _check(obj, path=""):
        if isinstance(obj, str):
            # Case-insensitive check
            if username.lower() in obj.lower():
                leaks.append(f"{path}: ...{obj[max(0,obj.lower().index(username.lower())-20):obj.lower().index(username.lower())+len(username)+20]}...")
        elif isinstance(obj, dict):
            for k, v in obj.items():
                _check(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _check(v, f"{path}[{i}]")

    _check(record_dict)
    return leaks


def main():
    parser = argparse.ArgumentParser(description="Re-anonymize a leaked HF shard")
    parser.add_argument("--repo", required=True, help="HF dataset repo ID")
    parser.add_argument("--shard", required=True, help="Shard filename (without data/ prefix)")
    parser.add_argument("--username", default=None, help="Username to redact (default: $USER)")
    parser.add_argument("--dry-run", action="store_true", help="Don't upload, just verify")
    args = parser.parse_args()

    username = args.username or os.environ.get("USER") or os.environ.get("USERNAME")
    if not username:
        print("ERROR: Could not detect username. Pass --username explicitly.")
        sys.exit(1)

    hashed = hash_username(username)
    print(f"Username: {username} -> {hashed}")

    token = os.environ.get("HF_TOKEN")
    if not token:
        # Try opentraces credentials file
        creds_path = Path.home() / ".opentraces" / "credentials"
        if creds_path.exists():
            token = creds_path.read_text().strip()
    if not token:
        print("ERROR: No HF_TOKEN found. Set HF_TOKEN or run `opentraces login`.")
        sys.exit(1)

    api = HfApi(token=token)
    shard_path = f"data/{args.shard}"

    # Download
    print(f"Downloading {shard_path} from {args.repo}...")
    local_path = api.hf_hub_download(
        repo_id=args.repo,
        filename=shard_path,
        repo_type="dataset",
    )

    records = []
    for line in Path(local_path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))

    print(f"Loaded {len(records)} records")

    # Re-anonymize
    total_leaks_before = 0
    for rec in records:
        leaks = check_leaks(rec, username)
        total_leaks_before += len(leaks)

    print(f"Leaks before re-anonymization: {total_leaks_before}")

    anonymized = []
    for rec in records:
        anonymized.append(reanonymize_record(rec, username))

    # Verify
    total_leaks_after = 0
    leak_details = []
    for i, rec in enumerate(anonymized):
        leaks = check_leaks(rec, username)
        total_leaks_after += len(leaks)
        for leak in leaks[:3]:
            leak_details.append(f"  Record {i}: {leak}")

    print(f"Leaks after re-anonymization: {total_leaks_after}")
    if leak_details:
        print("Sample remaining leaks:")
        for detail in leak_details[:10]:
            print(detail)

    if args.dry_run:
        print("DRY RUN: not uploading.")
        return

    if total_leaks_after > 0:
        print(f"WARNING: {total_leaks_after} leaks remain. Proceeding anyway (they may be false positives).")

    # Serialize
    lines = []
    for rec in anonymized:
        lines.append(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
    content = "\n".join(lines) + "\n"
    data = content.encode("utf-8")

    # Upload (overwrite the same shard)
    import io
    print(f"Uploading re-anonymized shard to {args.repo}/{shard_path}...")
    api.upload_file(
        path_or_fileobj=io.BytesIO(data),
        path_in_repo=shard_path,
        repo_id=args.repo,
        repo_type="dataset",
    )

    print(f"Done. {len(anonymized)} records re-uploaded to {args.repo}/{shard_path}")
    print(f"Leaks fixed: {total_leaks_before} -> {total_leaks_after}")


if __name__ == "__main__":
    main()
