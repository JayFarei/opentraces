# HuggingFace Private Datasets: R&D Scouting Brief

> Research date: 2026-03-28
> Source: https://huggingface.co/docs/hub/datasets-overview, https://huggingface.co/docs/huggingface_hub/
> Category: platform feature analysis

---

## Overview

HuggingFace Hub supports private datasets as a first-class feature on all tiers, including free. Any user or organization can create dataset repositories with `private=True`, making them invisible (404) to unauthorized users. The `huggingface_hub` Python SDK provides full programmatic control over visibility, gating, and access management.

## Problem It Solves

Contributors who want to share agent traces face a tension: they need to review and redact sensitive data before publishing, but the current opentraces pipeline only supports public uploads. Private datasets solve the "staging gap", allowing contributors to push traces to HF Hub immediately after parsing, review them remotely (or share with trusted reviewers), and only make them public when ready.

For enterprise users, private datasets enable internal trace collection without any public exposure.

## How It Works

### Visibility Model

HF Hub repositories have three visibility states:

| State | Behavior | Who Can Access |
|-------|----------|---------------|
| **Public** | Discoverable, searchable, downloadable | Anyone |
| **Private** | Returns 404 to unauthorized users | Owner, org members, or explicitly granted users |
| **Gated** | Publicly visible, but files locked until access granted | Users who request and receive access |

Private + Gated can be combined: the repo is hidden AND requires approval to access.

### Key Concepts

- **Private repos return 404, not 403**: Unauthorized users cannot even confirm the repo exists. This makes debugging access issues harder but is better for security.
- **No per-user sharing on personal repos**: You cannot add collaborators to a personal private dataset. To share with specific people, move it to an org or use gated access.
- **All-or-nothing at repo level**: No row-level or file-level access control. If you need per-contributor isolation, you need separate repos.
- **Organization roles**: `read`, `contributor` (can create repos, only modify own), `write`, `admin`.
- **Resource Groups** (Team/Enterprise): Fine-grained sub-org access control. A private repo in a Resource Group is only visible to that group's members.

### Core API / Interface

```python
from huggingface_hub import HfApi

api = HfApi(token="hf_xxx")

# Create a private dataset
api.create_repo(
    repo_id="username/my-traces",
    repo_type="dataset",
    private=True,
    exist_ok=True,
)

# Upload (same API as public, auth is automatic)
api.upload_file(
    path_or_fileobj="traces.jsonl",
    path_in_repo="data/traces_001.jsonl",
    repo_id="username/my-traces",
    repo_type="dataset",
)

# Change visibility: private -> public
api.update_repo_settings(
    repo_id="username/my-traces",
    repo_type="dataset",
    private=False,
)

# Or enable gated access (auto-approve)
api.update_repo_settings(
    repo_id="username/my-traces",
    repo_type="dataset",
    gated="auto",  # "auto" | "manual" | False
)

# Download from private dataset
from datasets import load_dataset
ds = load_dataset("username/my-traces", token="hf_xxx")
```

**Key `create_repo` signature:**
```python
create_repo(
    repo_id: str,
    *,
    token: Union[str, bool, None] = None,
    private: Optional[bool] = None,
    repo_type: Optional[str] = None,  # "dataset"
    exist_ok: bool = False,
    resource_group_id: Optional[str] = None,  # Enterprise only
) -> RepoUrl
```

**Gated access management:**
```python
# List/approve/reject access requests
api.list_pending_access_requests(repo_id, repo_type="dataset")
api.accept_access_request(repo_id, user="some_user", repo_type="dataset")
api.reject_access_request(repo_id, user="some_user", repo_type="dataset")
api.grant_access(repo_id, user="some_user", repo_type="dataset")  # proactive grant
```

## Maturity & Traction

- **License**: N/A (platform feature)
- **Availability**: All tiers (Free, PRO, Team, Enterprise)
- **Backing**: Hugging Face Inc.
- **Production Users**: Every enterprise HF customer uses private repos
- **GDPR**: HF is SOC2 Type 2 certified, CNIL-regulated (France). Enterprise plan offers BAA/DPA and storage region selection.

## Pricing & Storage Limits

| Tier | Private Storage Included | Dataset Viewer on Private | Cost |
|------|-------------------------|--------------------------|------|
| Free | ~100 GB | No | $0 |
| PRO | 1 TB | Yes | $9/month |
| Team | 1 TB/seat | Yes | $20/user/month |
| Enterprise | 1 TB/seat | Yes | $50+/user/month |

Overage: $18/TB/month (down to $12/TB at 500TB+).

**Repository limits:**
- < 100,000 files per repo recommended
- < 10,000 files per folder
- < 5 GB per file (Xet backend improving this)
- Text files (`.jsonl`, `.csv`) over 10MB must be LFS-tracked or compressed to `.gz`

**Rate limits (per 5-minute window):**

| Plan | API Calls | Downloads |
|------|-----------|-----------|
| Free | 1,000 | 5,000 |
| PRO | 2,500 | 12,000 |
| Team | 3,000 | 20,000 |
| Enterprise | 6,000 | 50,000 |

## Strengths

- Free tier is generous enough for individual trace contributors (~100GB)
- `private=True` is a single parameter change in existing `create_repo` calls
- Gated datasets provide a middle ground: publicly discoverable but access-controlled
- Organization roles and Resource Groups enable enterprise access patterns
- SDK handles 429 rate limit errors with automatic retry (v1.2.0+)
- GDPR compliance is solid at the platform level

## Limitations & Risks

- **No per-user sharing on personal repos**: Cannot add collaborators without creating an org. This limits the "share with my team" use case for individual users.
- **Gated access is per-individual, not per-org**: Cannot grant an entire organization access to a gated dataset in one action.
- **Dataset Viewer unavailable on free-tier private datasets**: Contributors on free plans cannot browse their private traces via the HF web UI.
- **Authentication fragility**: The `datasets` library had a breaking change in v2.14 where `token=` parameter stopped working for private datasets. Workaround requires `DownloadConfig(token=...)`.
- **macOS Keychain conflicts**: Users hit credential conflicts between `huggingface-cli login`, git credential helpers, and Keychain Access.
- **Text files over 10MB silently fail** if not LFS-tracked, which is easy to miss with JSONL uploads.
- **Private repo 404 behavior** makes debugging access issues harder (no 403 to distinguish "repo doesn't exist" from "you don't have access").

## Community Signal

- Authentication is the #1 pain point in community discussions. Token management across environments (Colab, SageMaker, CI/CD) is fragile.
- Organization API Tokens were deprecated but old tutorials still reference them, causing confusion.
- Enterprise users report Resource Groups work well for internal dataset isolation.
- The free tier storage limit (~100GB) is not prominently documented, which surprises users who hit it.

## Integration Analysis: opentraces

### Current State

The opentraces upload module (`src/opentraces/upload/hf_hub.py`) currently:
- Creates repos via `create_repo(repo_id, repo_type="dataset", exist_ok=True)`, **no `private=` argument**
- Uploads sharded JSONL via `upload_file()`
- Tags repos via `update_repo_settings(tags=[...])`
- Has no visibility controls, no CLI flags for private/public, no config field for dataset visibility

All datasets are created **public by default**.

### Fit Assessment

**Strong Fit.** Adding private dataset support requires minimal code changes (one parameter on `create_repo`, one config field, one CLI flag) and addresses a real gap in the contributor workflow.

### Proposed Feature: Private-First Push Workflow

**Core idea:** Contributors push to private datasets by default, then explicitly publish when ready.

#### Config Changes (`config.py`)

Add to `Config`:
```python
dataset_visibility: Literal["public", "private"] = "private"  # default private
```

#### CLI Changes (`cli.py`)

Add flags to `push`:
```
opentraces push --private          # Force private (override config)
opentraces push --public           # Force public (override config)
opentraces push --publish          # Change existing private dataset to public
```

#### Upload Changes (`hf_hub.py`)

1. Pass `private=` to `create_repo()` based on config/flag
2. Add `publish_dataset()` method that calls `update_repo_settings(private=False)`
3. Optionally enable gated access: `update_repo_settings(gated="auto")`

#### Workflow

```
opentraces parse                    # Parse sessions locally
opentraces review --web             # Review + approve
opentraces push                     # Push to private HF dataset (default)
# ... contributor reviews on HF, shares URL with trusted people ...
opentraces push --publish           # Make dataset public when ready
```

### Effort Estimate

**Quick (hours).** The SDK already supports everything needed. Changes are:
1. Add `dataset_visibility` config field (~5 lines)
2. Pass `private=` to `create_repo()` (~3 lines)
3. Add `--private`/`--public`/`--publish` flags to CLI (~20 lines)
4. Add `publish_dataset()` function (~10 lines)

### Open Questions

1. **Should private be the default?** The product design positions opentraces as community/open data. Defaulting to private adds friction for the open-data mission but is safer for new users. Recommendation: default private, prompt to publish after first successful push.
2. **Gated datasets**: Should we support `opentraces push --gated` as a middle ground? Publicly discoverable but access-controlled. Useful for the community aggregated dataset.
3. **Organization support**: Should `dataset_name_template` support org namespaces? e.g., `"{org}/opentraces-traces"`. Currently it only uses `{username}`.
4. **Dataset Viewer gap**: Free-tier users cannot browse private datasets on HF. Should we warn them, or is the local web review (`opentraces review --web`) sufficient?
5. **10MB JSONL gotcha**: Should we compress shards to `.jsonl.gz` by default to avoid the text file LFS requirement?

## Key Takeaways

1. **Yes, private datasets are fully supported on HuggingFace, including the free tier** (~100GB). Adding `private=True` to the existing `create_repo()` call is the minimal change needed.
2. **Private-first is the right default for a security-focused product.** Contributors can review remotely before publishing. The `--publish` flag provides an explicit gate.
3. **Gated datasets are a compelling middle ground** for the community aggregated dataset, enabling public discoverability with access control and user tracking.
4. **Compress JSONL shards** to `.jsonl.gz` to avoid the 10MB text file LFS gotcha and reduce storage costs.
5. **The implementation is ~40 lines of code** across config, CLI, and upload modules.

## Sources

- [HF Hub: Datasets Overview](https://huggingface.co/docs/hub/datasets-overview)
- [HF Hub: Repository Settings](https://huggingface.co/docs/hub/repositories-settings)
- [HF Hub: Gated Datasets](https://huggingface.co/docs/hub/datasets-gated)
- [HF Hub: Storage Limits](https://huggingface.co/docs/hub/storage-limits)
- [HF Hub: Rate Limits](https://huggingface.co/docs/hub/en/rate-limits)
- [HF Hub: Access Control in Organizations](https://huggingface.co/docs/hub/en/organizations-security)
- [HF Hub: Resource Groups](https://huggingface.co/docs/hub/security-resource-groups)
- [HF Hub: Programmatic Access Control](https://huggingface.co/docs/hub/en/programmatic-user-access-control)
- [huggingface_hub SDK: HfApi Reference](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api)
- [huggingface_hub SDK: Upload Guide](https://huggingface.co/docs/huggingface_hub/guides/upload)
- [huggingface_hub SDK: Repository Management](https://huggingface.co/docs/huggingface_hub/guides/repository)
- [HuggingFace Pricing](https://huggingface.co/pricing)
- [GitHub Issue #6126: Private datasets token regression](https://github.com/huggingface/datasets/issues/6126)
