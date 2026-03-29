---
schema_version: "1.0"
title: Upload & Distribution
scope: src/opentraces/upload, src/opentraces/state.py, src/opentraces/config.py
---

# Upload & Distribution

## Entities

### HFUploader
Manages uploads to HuggingFace Hub. Initialized with `token` and `repo_id`. Uses the `huggingface_hub.HfApi` client.

### UploadResult
Dataclass returned from uploads: `shard_name`, `trace_count`, `repo_url`, `success`, `error`.

### StateManager
Manages persistent state for the full trace lifecycle. Stored at `~/.opentraces/state.json`.

### TraceStagingEntry
State for a single trace: `trace_id`, `session_id`, `status` (TraceStatus enum), `file_path`, `error`, `uploaded_at`, `created_at`.

### CommitGroup
A group of traces committed together for push: `commit_id` (12-char hex), `trace_ids`, `message`, `created_at`.

### ProcessedFile
Tracks a processed session file for incremental re-runs: `file_path`, `inode`, `mtime`, `last_byte_offset`.

### StagingLock
File lock (fcntl.flock) on `~/.opentraces/staging/.lock` to prevent concurrent upload corruption. Context manager interface.

### Config
Root configuration model (Pydantic). Key fields:
- `hf_token`: Resolved from env > credentials file > huggingface-cli token (never persisted to config.json)
- `default_tier`: 1-3, default 3
- `projects`: Per-project overrides (tier, excluded, mode, remote, visibility)
- `excluded_projects`: List of excluded project paths
- `custom_redact_strings`: User-provided strings to always redact
- `classifier_sensitivity`: low/medium/high
- `dataset_visibility`: public/private

## Business Rules

### Sharded Upload Design
- Each push creates a NEW JSONL shard file (never appends to existing shards)
- Shard naming: `traces_{YYYYMMDDTHHMMSSZ}_{8-char-uuid}.jsonl`
- Shards are uploaded to `data/` directory in the HF dataset repo
- Traces are serialized with `to_jsonl_line()` which computes content_hash before serializing

### Upload Retry with Exponential Backoff
- Max retries: 3
- Base delay: 1.0 seconds
- Delay formula: `base_delay * 2^attempt` (1s, 2s, 4s)
- Catches both `HfHubHTTPError` and generic `Exception`
- Returns `UploadResult(success=False)` with error message after all retries exhausted

### Repository Management
- `ensure_repo_exists()`: Creates dataset repo with `exist_ok=True`, tags with "opentraces" and "agent-traces"
- `publish_dataset()`: Changes private dataset to public
- `set_gated()`: Enables gated access (default "auto" approval)
- `list_opentraces_datasets()`: Lists datasets tagged "opentraces", optionally filtered by username

### Dataset Card Generation
- Auto-generates README.md with YAML frontmatter and machine-managed stats section
- Stats include: total traces, steps, tokens, date range, schema version, model distribution, agent distribution
- **Preservation on update**: Uses HTML comment markers (`<!-- opentraces:auto-stats-start -->` / `<!-- opentraces:auto-stats-end -->`) to replace only the machine-managed section
- User-edited sections outside the markers are preserved
- Frontmatter is always regenerated (license: cc-by-4.0, tags, size category)

### Size Category Mapping
| Trace Count | HF Size Category |
|-------------|-----------------|
| < 100 | n<1K |
| < 1,000 | 1K<n<10K |
| < 10,000 | 10K<n<100K |
| >= 10,000 | 100K<n<1M |

### HF Token Resolution Chain
Priority order (first wins):
1. `HF_TOKEN` environment variable (for CI/CD)
2. `~/.opentraces/credentials` file (opentraces-managed, must start with `hf_`)
3. `~/.cache/huggingface/token` (huggingface-cli login, must start with `hf_`)

Token is never persisted to `config.json` (excluded from `save_config` via `model_dump(exclude={"hf_token"})`).

### Credential Storage Security
- Config file written with `0600` permissions (owner read/write only)
- Uses `os.open(O_CREAT)` to avoid TOCTOU race where file is briefly world-readable between creation and chmod
- Credentials file also written with `0600`

### Incremental Processing
StateManager tracks processed files by `(file_path, inode, mtime)`:
- **Same inode, newer mtime**: Resume from `last_byte_offset`
- **Different inode**: File was replaced, reprocess from offset 0
- **Same inode, same mtime**: Skip (already processed)

### Project Configuration
Two levels:
1. **Global config** (`~/.opentraces/config.json`): Default tier, excluded projects, classifier sensitivity
2. **Project-local config** (`.opentraces/config.json` in project dir): Per-project tier, mode, remote, visibility

Project-local config supports migration from YAML format (`.opentraces/config.yml`). On detection, YAML is parsed, converted to JSON, and the original is renamed to `.bak`.

**Tier-to-mode backfill**: If config has `tier` but no `mode`, tier 1 maps to "auto", everything else maps to "review".

### Project Exclusion
A project is excluded (returns tier=-1) when:
- Project path appears in `excluded_projects` list
- Project's `ProjectConfig.excluded == True`

## Calculations

- **Dataset stats**: Aggregated from all traces: total steps, total tokens (input + output), model/agent distributions, date range from sorted timestamps

## State Machines

### Trace Lifecycle
```
discovered -> parsed -> staged -> reviewing -> approved -> committed -> uploading -> uploaded
                                            -> rejected
                                  uploading -> failed -> staged (retry)
```

Key transitions:
- `discovered -> parsed`: Parser produces TraceRecord
- `parsed -> staged`: Trace enriched and written to staging dir
- `staged -> reviewing`: Review interface opened
- `reviewing -> approved/rejected`: Human decision
- `approved -> committed`: Traces grouped into CommitGroup
- `committed -> uploading`: Push initiated
- `uploading -> uploaded`: HF upload succeeded
- `uploading -> failed -> staged`: Retry path

### Pending Upload Query
`get_pending_upload_traces()` returns traces with status `APPROVED` or `FAILED` (failed traces can be retried).

## Edge Cases

1. **Concurrent upload protection**: `StagingLock` uses `fcntl.LOCK_EX | LOCK_NB` (non-blocking). If lock cannot be acquired, raises `RuntimeError` immediately rather than waiting.
2. **Empty upload**: Returns `UploadResult(success=False, error="No traces to upload")` without making any API calls.
3. **Repo tagging failure**: `update_repo_settings` for tags is wrapped in bare `except` (best-effort, not all API versions support it).
4. **Config version migration**: One-way migration from older versions. Currently only `0.0.0 -> 0.1.0` (no-op, just bumps version). Future migrations added as elif chains.
5. **Commit group creation**: State file persisted (`save()`) after each status transition to ensure crash safety.
6. **Dataset card existing_card update**: If the existing card has frontmatter, it is replaced. If it has auto-stats markers, only that section is replaced. All other content is preserved.
