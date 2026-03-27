<!-- /autoplan restore point: /Users/jayfarei/.gstack/projects/JayFarei-opentraces/main-autoplan-restore-20260327-184357.md -->

# opentraces.ai Implementation Plan

> Build the CLI, review app, trace explorer, and marketing site for crowdsourcing agent traces to HuggingFace Hub.

## Context

- **Source docs**: `resources/intent.md`, `resources/outcome.md`, `kb/discussion-log.md`
- **All design decisions resolved** in discussion-log.md (22 questions answered)
- **Stack**: Python (fastest path, native HF Hub integration, DataClaw patterns referenceable)
- **Distribution**: Option C Hybrid (standalone CLI + optional Claude Code skill layer)
- **Agent scope**: Claude Code only for v0.1
- **Deadline**: Respond to HF founder (Clem Delangue) by 2026-03-29

---

## Phase 0: Project Bootstrap (Day 1)

### 0.1 Repository Structure

```
opentraces/
  src/
    opentraces/
      __init__.py
      cli.py                    # Click-based CLI entry point
      config.py                 # Config management (~/.opentraces/config.json)
      schema.py                 # Pydantic models for the JSONL schema
      parsers/
        __init__.py
        base.py                 # typing.Protocol adapter contract
        claude_code.py          # Claude Code session parser
        dataclaw_import.py      # DataClaw JSONL import adapter
      security/
        __init__.py
        secrets.py              # Vendored from DataClaw (MIT) + extensions
        anonymizer.py           # Vendored from DataClaw (MIT)
        redactor.py             # RedactingFilter for log handlers
      enrichment/
        __init__.py
        attribution.py          # Construct Agent Trace attribution blocks
        dependencies.py         # Extract deps from manifest files
        git_signals.py          # committed, commit_sha, patch extraction
        metrics.py              # Cost estimation, cache hit rate, totals
        quality.py              # Min 1 tool call + min 2 steps filter
      upload/
        __init__.py
        hf_hub.py               # huggingface_hub SDK integration
        dataset_card.py         # Auto-generated README with schema docs
      review/
        __init__.py
        cli_review.py           # Terminal-based review interface
        web/
          __init__.py
          app.py                # Flask/FastAPI local web review server
          static/               # Minimal frontend assets
          templates/             # Jinja2 templates for review UI
      skill/
        SKILL.md                # Claude Code skill file
  tests/
    __init__.py
    test_parser_claude_code.py
    test_security_secrets.py
    test_security_anonymizer.py
    test_enrichment_attribution.py
    test_enrichment_git.py
    test_upload_hf.py
    test_schema.py
    test_cli.py
    fixtures/                   # Sample Claude Code session logs
  pyproject.toml
  README.md
  LICENSE                       # Apache-2.0 for the tool, CC-BY-4.0 for contributed data
```

### 0.2 Dependencies

```toml
[project]
name = "opentraces"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "click>=8.0",
    "huggingface_hub>=0.20.0",
    "pydantic>=2.0",
]

[project.optional-dependencies]
web = ["flask>=3.0", "jinja2>=3.0"]
dev = ["pytest>=8.0", "pytest-cov", "ruff"]

[project.scripts]
opentraces = "opentraces.cli:main"
```

Minimal dependency footprint: `click` (CLI), `huggingface_hub` (upload), `pydantic` (schema validation). Web review is optional extra.

### 0.3 Config System

State persisted to `~/.opentraces/config.json`:
- `hf_token`: HuggingFace auth token (or use HF_TOKEN env var)
- `default_tier`: Security tier (1/2/3), default 3
- `projects`: Dict of project path -> tier override
- `excluded_projects`: List of project paths to skip
- `custom_redact_strings`: User-added redaction patterns
- `pricing_file`: Optional custom pricing table path
- `dataset_name_template`: Default `{username}/opentraces-claude-code`

File permissions: `chmod 0600` on creation (contains potential secrets in redact_strings).

---

## Phase 1: Core Parser + Schema (Days 1-3)

### 1.1 Schema Models (Pydantic)

Implement the full JSONL schema from intent.md as Pydantic v2 models:

- `TraceRecord` (top-level, one per session)
- `Task`, `Agent`, `Environment`, `VCS`
- `Step` (with `role`, `call_type`, `agent_role`, `parent_step`)
- `ToolCall`, `Observation`, `Snippet`
- `TokenUsage` (per-step: input/output/cache_read/cache_write/prefix_reuse)
- `Outcome` (success, signal_source, description, patch, committed, commit_sha)
- `Attribution`, `AttributionFile`, `AttributionConversation`, `AttributionRange`
- `Metrics` (total_steps, total_input/output_tokens, duration, cache_hit_rate, cost)
- `SecurityMetadata` (tier, flags_reviewed, redactions_applied)

`schema_version: "0.1.0"` hardcoded. `content_hash` computed as SHA-256 of serialized trace.

### 1.2 Claude Code Parser

Read sessions from `~/.claude/projects/*/session.jsonl`.

**Parse pipeline:**
1. Discover all project directories under `~/.claude/projects/`
2. For each session file, read JSONL lines
3. Build `tool_result_map` (pre-pass correlating `tool_use_id` -> `tool_result`, DataClaw pattern)
4. Parse into `Step` objects with:
   - Role mapping (`human` -> `user`, `assistant` -> `agent`)
   - Tool call extraction with `tool_call_id`, duration
   - Observation extraction linked via `source_call_id`
   - `reasoning_content` from extended thinking blocks
   - Per-step `token_usage` from API response metadata
   - `system_prompt_hash` dedup into top-level map
   - `tools_available` per step (from tool definitions in system prompt)
   - `parent_step` links for sub-agent steps
   - `agent_role` labels (`main`, `explore`, `plan`)
   - `call_type` classification (`main`, `subagent`, `warmup`)
   - `snippets` extraction (code blocks with file_path, start/end_line, language)
5. Apply quality filter: skip sessions with < 1 tool call or < 2 steps

**Sub-agent handling (per discussion-log Q7):**
- Include sub-agent steps inline with `parent_step` links
- Sub-agent steps from `subagents/*.jsonl` are inlined, not separate records
- Defer full transcript capture to v0.2

**Warm-up calls (per discussion-log Q9):**
- Include with `call_type: "warmup"` label, don't filter

### 1.3 DataClaw Import Adapter

`opentraces import --from dataclaw <path-to-conversations.jsonl>`

Read DataClaw's flat JSONL format, enrich with opentraces schema fields:
- Add `schema_version`, `trace_id` (generated UUID), `content_hash`
- Map DataClaw fields to our schema where possible
- Set `outcome: null`, `attribution: null`, `environment: null` for fields DataClaw doesn't capture
- Preserve all original data

Low effort since DataClaw's format is simple flat JSONL. Captures ~25 existing contributors.

---

## Phase 2: Security Pipeline (Days 3-5)

### 2.1 Tier 1 - Danger Mode (Regex Auto-Redact)

Vendor DataClaw's `secrets.py` (273 lines, MIT) + `anonymizer.py` (105 lines, MIT).

**Vendored patterns (19 from DataClaw):**
JWT, API keys (Anthropic/OpenAI/HF/GitHub/PyPI/NPM/AWS/Slack/Discord), private keys, DB URLs, Bearer tokens, IPs, emails, high-entropy strings.

**Extensions (3 new):**
- Credit card numbers (Luhn validation)
- SSNs (XXX-XX-XXXX pattern)
- Phone numbers (common formats)

**Anonymizer:**
- SHA-256 username hashing (8-char hex prefix)
- `/Users/`/`/home/` path stripping to project-relative
- macOS hyphen-encoded path handling
- Configurable extra usernames

**Implementation:** Two-pass scan (parse-time + pre-upload verification). All matches replaced with `[REDACTED]`. Allowlist for false positives (noreply emails, Python decorators, private IPs, example URLs).

**RedactingFilter:** Applied to all log handlers to prevent credentials leaking into opentraces' own debug logs.

### 2.2 Tier 2 - Automated (Deferred to v0.2)

Per discussion-log Q15: Drop LLM classifier. Realistic middle ground for v0.2:
- Regex scan (Tier 1 baseline)
- Heuristic flagging (internal hostnames, AWS account IDs, DB connection strings)
- Escalation to human review for flagged items

### 2.3 Tier 3 - Manual Review

Nothing uploads until user reviews. Sessions buffered locally in `~/.opentraces/staging/`.

**CLI review** (`opentraces review`):
- List pending sessions with summary (steps, tools used, outcome)
- Per-session: approve / reject / skip
- Per-step: redact specific content
- Annotate outcome signals (optional, per discussion-log Q5: zero required annotation)

**Web review** (`opentraces review --web`):
- Flask app on localhost (optional `[web]` extra)
- Browse sessions, expand steps, view tool calls
- Approve/reject/redact with visual diff
- Push approved traces to HF Hub

---

## Phase 3: Enrichment Pipeline (Days 4-6)

### 3.1 Git Signal Extraction

```python
# git_signals.py
def extract_git_signals(project_path: str) -> dict:
    # Check if session produced a commit
    # Extract: base_commit, branch, committed (bool), commit_sha, patch (unified diff)
    # Uses subprocess to call git commands
```

### 3.2 Attribution Block Construction

Per discussion-log Q18: Ship as best-effort in v0.1, not guaranteed-accurate.

```python
# attribution.py
def build_attribution(steps: list[Step], patch: str | None) -> Attribution | None:
    # Build from unified diff (outcome.patch) rather than individual edit operations
    # Map diff hunks back to conversation steps via timestamp correlation
    # Accept approximate attributions for overlapping ranges
    # Label with confidence field
    # Returns None if no code changes in session
```

### 3.3 Dependency Extraction

```python
# dependencies.py
def extract_dependencies(project_path: str) -> list[str]:
    # Read package.json, Gemfile, requirements.txt, pyproject.toml
    # Extract package names (not versions)
    # Also extract from tool call arguments (npm install X, pip install X)
```

### 3.4 Metrics Computation

```python
# metrics.py
def compute_metrics(steps: list[Step], pricing: dict) -> Metrics:
    # Sum token counts across steps
    # Compute cache_hit_rate from cache_read / total_input
    # Estimate cost from static pricing table
    # Compute total_duration from timestamps
```

Per discussion-log Q19: Static pricing table in the package, versioned with schema. User override via `opentraces config set --pricing-file custom.json`.

### 3.5 Environment Metadata

```python
# Collected at parse time
environment = Environment(
    os=platform.system().lower(),
    shell=os.environ.get("SHELL", "unknown"),
    vcs=extract_vcs_info(project_path),
    language_ecosystem=detect_ecosystems(project_path),
)
```

---

## Phase 4: CLI + Upload (Days 5-7)

### 4.1 CLI Commands

```
opentraces auth              # HF Hub authentication
opentraces config set        # Per-project/global config
opentraces config show       # Display current config (redact_strings masked)
opentraces discover          # List available sessions across projects
opentraces parse             # Parse sessions into enriched JSONL (local)
opentraces review            # Tier 3 manual review (CLI or --web)
opentraces push              # Upload approved traces to HF Hub
opentraces import --from dataclaw <path>  # Import DataClaw exports
opentraces export --format atif           # Lossy ATIF conversion
opentraces capabilities --json            # Machine-discoverable feature list
opentraces introspect --json              # Full API schema
```

**Staged pipeline:** auth -> configure -> review -> publish. Push hard-gated behind review completion. State persisted to `~/.opentraces/config.json`.

**Agent-native output:** Every command emits structured JSON with `next_steps` and `next_command` fields. Human-readable text + `---OPENTRACES_JSON---` sentinel for machine parsing. Structured errors: `{code, kind, message, hint, retryable}`. Exit codes: 0=OK, 2=usage, 3=missing config, 4=network, 5=data corrupt, 7=lock/busy.

### 4.2 HF Hub Upload

```python
# hf_hub.py
def upload_traces(traces: list[TraceRecord], config: Config) -> str:
    # Create/update personal dataset repo (username/opentraces-claude-code)
    # Append JSONL (not overwrite)
    # Tag with 'opentraces' + 'agent-traces'
    # Auto-generate dataset card
    # Return dataset URL
```

**Dataset card:** Auto-generated README.md with YAML frontmatter, schema documentation, model distribution, token counts, outcome signal distribution, security tier used, contributor stats, `datasets.load_dataset()` snippet. License: CC-BY-4.0.

**Consent model (per discussion-log Q4):** Per-project with per-session override. `opentraces config set --project . --tier danger` for persistent config.

### 4.3 Skill File

`opentraces install-skill claude` copies SKILL.md into Claude Code's skill directory. Skill enables natural language commands: "Share this session to opentraces."

---

## Phase 5: Local Web Review App (Days 6-8)

### 5.1 Architecture

Flask app served on localhost by `opentraces review --web`. Optional dependency (`pip install opentraces[web]`).

**Routes:**
- `GET /` - Session list with filters (project, date, model, outcome)
- `GET /session/<id>` - Session detail with expandable steps
- `POST /session/<id>/approve` - Approve session
- `POST /session/<id>/reject` - Reject session
- `POST /session/<id>/step/<n>/redact` - Redact specific step content
- `POST /push` - Push all approved sessions to HF Hub
- `GET /api/sessions` - JSON API for session data

**UI:**
- Security-oriented visual language (green/shield/lock)
- Collapsible tool calls with syntax-highlighted code blocks
- Diff viewer for patches
- Redaction highlighting (yellow background for auto-redacted content)
- Approve/reject buttons per session and per step
- Push button hard-gated behind review completion

### 5.2 Frontend

Minimal: server-rendered HTML with Jinja2 templates + vanilla JS for interactivity. No build step, no npm, no frontend framework. CSS via a lightweight classless CSS library (e.g., Pico CSS or Simple.css).

---

## Phase 6: Trace Explorer HF Space (Days 7-10)

### 6.1 Architecture

Gradio app deployed as HuggingFace Space at `huggingface.co/spaces/opentraces/explorer`.

No backend database, queries HF Hub's Dataset Viewer API across all `opentraces`-tagged datasets.

### 6.2 Features

**Search & Browse:**
- Search by username (contributor lookup)
- Search by intent/task (from first user message or `task.description`)
- Filter by language ecosystem, dependencies, agent, model
- Filter by outcome (success/fail/committed)
- Community stats: total traces, active contributors, agent/model distribution

**Trace Viewer:**
- Render single trace as readable conversation
- Collapsible tool calls with highlighted code blocks
- Outcome summary with patch diff

**Contributor Dashboard (per discussion-log Q14: ship in v0.1):**
- Enter HF username, see personal analytics
- Sessions over time, model distribution, token usage
- Tool usage breakdown
- Success/failure rate
- Efficiency metrics (cache hit rate, cost per session)
- No auth needed (per discussion-log Q21: public data, public dashboard)

### 6.3 Peer Comparisons (Deferred to v0.2)

Per outcome.md "Out of Scope": community comparison features deferred.

---

## Phase 7: Marketing Website (Days 8-12)

### 7.1 Architecture

Static site at opentraces.ai. Astro or Next.js, deployed to Vercel/Netlify.

### 7.2 Pages

- **Landing page**: Two-sided (contributors + ML teams)
  - Contributor side: selfish pitch (analytics), competitive pitch (peer comparison), altruistic pitch (open data), security-first messaging, 3-step install flow
  - ML team side: why this data, standards alignment, how to use it, schema explorer
- **Get Started guide**: Install -> Configure -> Push
- **Schema documentation**: Interactive explorer with annotated sample trace
- **Dashboard preview**: Link to HF Space

### 7.3 Design Principles

- Security-oriented: green/shield/lock visual language
- Technical credibility: show the schema, show the pipeline
- No "protest art" framing: constructive, neutral, infrastructure positioning
- Clear CTA: install the CLI, contribute your first trace

---

## Deliverable Sequencing

```
Day 1-3:   Phase 0 (Bootstrap) + Phase 1 (Parser + Schema)
Day 3-5:   Phase 2 (Security Pipeline)
Day 4-6:   Phase 3 (Enrichment Pipeline)
Day 5-7:   Phase 4 (CLI + Upload)
Day 6-8:   Phase 5 (Web Review App)
Day 7-10:  Phase 6 (HF Space Explorer)
Day 8-12:  Phase 7 (Marketing Website)
```

Phases 1-4 are sequential (each builds on the previous). Phases 5-7 can overlap.

**Critical path:** Schema -> Parser -> Security -> CLI -> Upload. Everything else can be parallelized after the core pipeline works end-to-end.

---

## Testing Strategy

### Unit Tests
- Schema validation (Pydantic model round-trips)
- Secret detection patterns (each regex, entropy, allowlist)
- Anonymizer (username hashing, path stripping)
- Quality filter (edge cases: 0 tools, 1 step, warmup-only)
- Attribution construction (from diff hunks)
- Metrics computation (cost estimation, cache hit rate)

### Integration Tests
- Full pipeline: raw Claude Code session -> enriched JSONL
- Security scan: known secrets are redacted, allowlisted patterns survive
- HF upload: mock `huggingface_hub` for upload verification
- CLI: end-to-end command execution

### Fixtures
- Real (redacted) Claude Code session logs for parser testing
- Crafted sessions with known secrets for security testing
- DataClaw format samples for import adapter testing

---

## Out of Scope for v0.1

- Multi-agent support beyond Claude Code (adapter contract ready)
- Tier 2 automated classifier (requires training data from v0.1)
- Real-time capture / stop-hooks (passive only)
- Canonical aggregated dataset curation
- Parquet dual-write
- Community comparison features in dashboard
- AI-generated summaries/embeddings
- Team/org features
- PR bot / GitHub integration
- Git notes for trace-commit linking

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|------------|
| Claude Code session format changes | Parser breaks | Pin to known format, version detection |
| HF Hub API rate limits | Upload failures | Batched upload with retry + exponential backoff |
| Secret detection false negatives | Leaked credentials in public dataset | Two-pass scan + Tier 3 manual review as default |
| Schema too complex for adoption | Low contribution rate | Zero required annotation, deterministic enrichment |
| DataClaw captures mindshare first | Reduced adoption | Ship faster, differentiate on schema depth + dashboard |

---

## Success Metrics

- CLI installable in single command (`pip install opentraces`)
- End-to-end: install -> parse -> review -> push in under 5 minutes
- At least 10 unique contributors within first month
- Schema validated against ATIF export compatibility
- Zero credential leaks in published datasets (Tier 1 floor guarantee)
