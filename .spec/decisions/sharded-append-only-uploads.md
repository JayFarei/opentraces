---
schema_version: "1.0"
title: Sharded Append-Only Uploads
scope: src/opentraces/upload
date_detected: 2026-03-28
confidence: high
---

# Sharded Append-Only Uploads

## What

Each push creates a new JSONL shard file (`traces_{timestamp}_{uuid}.jsonl`) in the `data/` directory of the HuggingFace dataset repo. The system never appends to or modifies existing shards. Deduplication relies on content_hash (SHA-256) computed at the record level.

## Why

HuggingFace Hub datasets use Git LFS for large files. Appending to an existing JSONL file would create a new LFS object for the entire file on every push, wasting storage and bandwidth proportional to the total dataset size. Sharded append-only writes mean each push only uploads the new traces, regardless of how large the existing dataset is.

Additionally, this design supports concurrent uploads from different machines or projects without coordination, since each shard has a unique name (timestamp + UUID).

## Tradeoff

**Gained**: O(new_traces) upload cost per push. No coordination needed between concurrent uploaders. Simple recovery from partial failures (retry creates a new shard). HuggingFace datasets library handles multi-file JSONL datasets natively.

**Lost**: Potential duplicate records across shards (mitigated by content_hash dedup at query time). No ability to update or delete individual records without rewriting shards. Growing number of small files in the dataset repo over time.

## Alternatives Rejected

1. **Single JSONL file with append**: Would require downloading and re-uploading the entire file on every push via Git LFS.
2. **Database-backed storage**: Would add infrastructure requirements and break compatibility with HuggingFace's dataset format.
3. **Parquet shards**: Better query performance but more complex to generate and would require additional dependencies.

## Source

- `src/opentraces/upload/hf_hub.py` (shard naming pattern, upload_traces method)
- `packages/opentraces-schema/RATIONALE-0.1.0.md` (content_hash rationale: "sharded JSONL upload, dedup must happen at record level")

## Transferability

High. Any system publishing data to append-only storage (Git LFS, S3, HDFS) benefits from sharded writes with content-addressable deduplication. The pattern is: generate a uniquely-named shard per write, include a content hash in each record, and defer deduplication to query time.
