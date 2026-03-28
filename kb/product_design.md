# opentraces.ai — Implementation Plan (Final)

> Build the complete open protocol + CLI + explorer + marketing site for crowdsourcing agent traces to HuggingFace Hub. Single deliverable for HF competitive bid.

## Context

**Why this change**: HF founder Clem Delangue has requested help building agent trace infrastructure for HuggingFace Hub. This is a competitive bid — we need an impressive end-to-end product that justifies HF's support. The product must demonstrate the full vision: rich schema, working parser with sub-agent hierarchy, all three security tiers, contributor dashboard, and marketing site.

**Source docs**: `resources/intent.md`, `resources/outcome.md`, `kb/discussion-log.md`, `kb/background-research/` (8 research reports)

**All 22 design decisions resolved** in `kb/discussion-log.md`

---

## Stack Decisions

- **Language**: Python 3.10+ (native `huggingface_hub`, DataClaw patterns referenceable)
- **Distribution**: Hybrid — standalone CLI (`pip install opentraces`) + optional Claude Code skill
- **Agent scope**: Claude Code only for v0.1 (adapter contract ready for multi-agent)
- **Schema package**: Published separately as `opentraces-schema` (protocol positioning)
- **Local web review**: Flask (optional `[web]` extra)
- **HF Space explorer**: Gradio
- **Marketing site**: Astro on Vercel

---

## Repository Structure

```
opentraces/
  packages/
    opentraces-schema/              # Standalone schema package
      src/opentraces_schema/
        __init__.py
        models.py                   # All Pydantic v2 models
        version.py
      pyproject.toml                # Depends only on pydantic>=2.0

  src/opentraces/
    __init__.py
    cli.py                          # Click CLI entry point
    config.py                       # ~/.opentraces/config.json management
    state.py                        # Staging state machine + upload tracking
    parsers/
      __init__.py
      base.py                       # typing.Protocol adapter contract
      claude_code.py                # Claude Code session parser
      quality.py                    # Min 1 tool call + min 2 steps
      dataclaw_import.py            # DataClaw JSONL import adapter
    security/
      __init__.py
      secrets.py                    # Vendored DataClaw (MIT) + extensions
      anonymizer.py                 # Vendored DataClaw (MIT) + Windows/WSL
      scanner.py                    # Context-aware scanning (field_type param)
      classifier.py                 # Tier 2 heuristic classifier
      redactor.py                   # RedactingFilter for log handlers
    enrichment/
      __init__.py
      attribution.py                # Full attribution from Edit ops + diff
      dependencies.py               # Extract deps from manifests
      git_signals.py                # committed, commit_sha, patch
      metrics.py                    # Cost, cache hit rate, totals
      snippets.py                   # Multi-tool snippet extraction
    upload/
      __init__.py
      hf_hub.py                     # Sharded JSONL upload
      dataset_card.py               # Auto-generated README
    review/
      cli_review.py                 # Terminal review (zero optional deps)
      web/
        app.py                      # Flask local web review
        static/
        templates/
    skill/
      SKILL.md
  tests/
    fixtures/                       # Real (redacted) Claude Code sessions
    test_schema.py
    test_parser_claude_code.py
    test_security.py
    test_enrichment.py
    test_upload.py
    test_cli.py
  pyproject.toml
  README.md
  LICENSE                           # Apache-2.0 (tool), CC-BY-4.0 (data)

explorer/                           # HF Space (Gradio)
  app.py
  requirements.txt

site/                               # Marketing site (Astro)
  src/
  astro.config.mjs
  package.json
```

---

## Phase 1: Schema + Project Bootstrap

### 1.1 Schema Package (`opentraces-schema`)

Full JSONL schema as Pydantic v2 models:

- `TraceRecord` — top-level, one per session
- `Task` — description, source, repository, base_commit
- `Agent` — name, version, model (provider/model convention)
- `Environment` — os, shell, vcs (type: "git"|"none"), language_ecosystem
- `Step` — role, content, reasoning_content, model, system_prompt_hash, agent_role, parent_step, call_type (main/subagent/warmup), tools_available, tool_calls, observations, snippets, token_usage, timestamp
- `ToolCall` — tool_call_id, tool_name, input, duration_ms
- `Observation` — source_call_id, content, output_summary, error
- `Snippet` — file_path, start_line, end_line, language, text
- `TokenUsage` — input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, prefix_reuse_tokens
- `Outcome` — success, signal_source (deterministic/user_annotation/ci), signal_confidence (derived/inferred/annotated), description, patch, committed, commit_sha
- `Attribution` — version, experimental (bool), files[].path, files[].conversations[].contributor, files[].conversations[].url (opentraces://trace_id/step_N), files[].conversations[].ranges[].start_line/end_line/content_hash/confidence
- `Metrics` — total_steps, total_input/output_tokens, total_duration_s, cache_hit_rate, estimated_cost_usd
- `SecurityMetadata` — tier, flags_reviewed, redactions_applied, classifier_version

`schema_version: "0.1.0"`, `content_hash: SHA-256`.

### 1.2 Config System

`~/.opentraces/config.json` with `config_version: "0.1.0"`:

- hf_token, default_tier (1/2/3, default 3), projects (path->tier), excluded_projects
- custom_redact_strings (masked in display), pricing_file, dataset_name_template
- projects_path (override ~/.claude/projects/), classifier_sensitivity (low/medium/high)
- File permissions: `chmod 0600`. Migration function when config_version mismatches.

### 1.3 Dependencies

```toml
# opentraces-schema
dependencies = ["pydantic>=2.0"]

# opentraces
dependencies = [
    "opentraces-schema>=0.1.0",
    "click>=8.0",
    "huggingface_hub>=0.20.0",
]
[project.optional-dependencies]
web = ["flask>=3.0"]
dev = ["pytest>=8.0", "pytest-cov", "ruff"]
```

---

## Phase 2: Claude Code Parser (Full Depth)

### 2.1 Session Discovery

- Scan `~/.claude/projects/*/` (or --projects-path override)
- Track processed files in `~/.opentraces/state.json` with (file_path, inode, mtime, last_byte_offset)
- Incremental on re-runs: seek to last_byte_offset, process only new lines
- Structured error if directory doesn't exist: `{code: "NO_SESSIONS_FOUND", hint: "..."}`

### 2.2 Parse Pipeline

1. **Line-level parsing**: try/except per line, skip corrupted, reject session if >5% fail
2. **Pre-pass**: Build `tool_result_map` (tool_use_id -> tool_result) across message boundaries. Handle parallel tool calls within single turn. Handle dangling IDs (emit observation with `error: "no_result"`)
3. **Step construction**:
   - Role mapping (human->user, assistant->agent)
   - Tool call extraction with tool_call_id, duration
   - Observation linking via source_call_id
   - `reasoning_content` from thinking blocks (`type: "thinking"`)
   - Per-step `token_usage` from API response metadata
   - `system_prompt_hash` dedup into top-level map
   - `tools_available` per step
   - `call_type`: main / subagent / warmup (session_id "0" + empty output)
   - `agent_role`: main / explore / plan (from subagent tool call name)
4. **Unknown block detection**: Log warning for unknown `type` fields, count in `metadata.unknown_blocks`, fail loudly if >20%
5. **Quality filter**: Skip sessions < 1 tool call or < 2 steps

### 2.3 Recursive Sub-Agent Loading

Sub-agent sessions live at `~/.claude/projects/<project>/subagents/<session_id>.jsonl`.

1. Parse parent file, identify tool calls with `name: "Task"` or `name: "Agent"`
2. Extract sub-agent session_id from tool call arguments
3. Locate sub-agent JSONL file by session_id
4. **Recursively parse** sub-agent file (sub-agents can spawn sub-agents)
5. Inline sub-agent steps with `parent_step` links back to the parent's invoking step
6. Set `agent_role` from the subagent type (explore, plan, etc.)
7. Set `subagent_trajectory_ref` to the sub-agent session_id

**Edge cases:**

- Sub-agent file doesn't exist: emit step with `call_type: "subagent"`, `subagent_trajectory_ref`, observations with `error: "subagent_file_missing"`
- Sub-agent file corrupted: same treatment as corrupted lines (skip, log warning)
- Circular references (sub-agent spawns parent): track visited session_ids, break cycles
- Max recursion depth: 10 levels (configurable)

### 2.4 Full Snippet Extraction

Per-tool-type strategies:

- **Read** results: file_path from tool call args, start_line from `offset` param, end_line from offset + content line count, language from file extension
- **Edit** results: file_path + old_string/new_string from args, line numbers from context matching, language from extension
- **Write** results: file_path from args, full file content as snippet, language from extension
- **Grep** results: parse structured output for file_path + line numbers per match
- **Bash** results: extract file paths from common patterns (`cat`, `head`, `tail` arguments), language inference from content if file path identified. Skip if no file path discernible.
- **Glob** results: file paths listed (no content, just metadata)

All snippets tagged with source_step for attribution linking.

### 2.5 DataClaw Import Adapter

`opentraces import --from dataclaw <path>` — reads flat JSONL, maps to schema, sets unknowns to null.

---

## Phase 3: Security Pipeline (All Three Tiers)

### 3.1 Tier 1 — Open Mode (Regex Auto-Redact)

**Vendored from DataClaw (MIT)**: 19 regex patterns (JWT, API keys by provider, private keys, DB URLs, Bearer tokens, IPs, emails, high-entropy strings) + allowlist.

**Extensions**: Credit card (Luhn), SSNs, phone numbers, internal hostnames (heuristic), AWS account IDs, DB connection strings.

**Anonymizer extensions**: Windows paths (`C:\Users\<name>\`), WSL paths (`/mnt/[a-z]/Users/<name>/`), WSL UNC (`\\wsl.localhost\<distro>\home\<name>\`), tilde expansion.

**Context-aware scanning** (`scanner.py`):

- Tool call inputs (bash, write, edit): Full regex + entropy
- Tool call results (read, grep): Regex only, no entropy
- `reasoning_content`: Regex only, no entropy
- `--explain-redactions` flag: shows which pattern matched each redaction

**Two-pass scan**:

- Pass 1: During parsing, on raw step content
- Pass 2: On final serialized JSONL bytes after enrichment (catches anything introduced during attribution construction)

**RedactingFilter** on all log handlers. Default WARNING to stderr. DEBUG requires explicit flag.

### 3.2 Tier 2 — Guarded Screening + Heuristic Classifier

Per discussion-log Q15: Drop LLM classifier. Build heuristic classifier instead.

`classifier.py` implements:

1. **Pattern-based flagging** (beyond Tier 1 regex):

   - Internal hostname patterns (_.internal, _.corp, \*.local, custom TLDs)
   - AWS account IDs (12-digit numbers in ARN patterns)
   - Database connection strings (jdbc:, mongodb://, redis://)
   - Internal URL patterns (Jira, Confluence, Slack workspace URLs)
   - Project codenames / internal tool references (heuristic: CamelCase names that don't match known public packages)

2. **Contextual risk scoring**:

   - File path depth analysis: deeply nested paths suggest internal project structure
   - Unique identifier density: high density of UUIDs/hashes suggests internal systems
   - Domain reference analysis: references to non-public domains

3. **Escalation**: Anything flagged gets surfaced to user in review (CLI or web). User sees what was flagged, why, and can approve/redact/reject per trace.

4. **Sensitivity configuration**: `classifier_sensitivity` in config (low/medium/high) controls flagging thresholds.

### 3.3 Tier 3 — Strict Review

Sessions buffered in `~/.opentraces/staging/`.

**State machine** (`state.py`):

```
discovered -> parsed -> staged -> reviewing -> approved -> uploading -> uploaded
                                            -> rejected -> (deleted or archived)
                                  uploading -> failed -> staged (retry)
```

File lock (`fcntl.flock`) during upload. Never delete staged file until confirmed.

**CLI review** (`opentraces review`): List pending with summary, approve/reject/skip per session, redact per step. Shows redaction counts and classifier flags.

**Web review** (`opentraces review --web`): Flask on localhost. Collapsible steps, syntax-highlighted code, diff viewer for patches, redaction highlighting (yellow), classifier flag highlighting (orange), approve/reject per session and per step, push gated behind review.

---

## Phase 4: Enrichment Pipeline (Full)

### 4.1 Git Signal Extraction

```python
def extract_git_signals(project_path: str) -> VCS:
    # VCS(type="none") if not git repo
    # VCS(type="git", base_commit, branch, committed, commit_sha, patch)
```

### 4.2 Full Attribution Block

Derive from **Edit tool calls directly** (most accurate) + **unified diff for coverage**:

1. **Primary**: Each `Edit` tool call provides file_path, old_string, new_string. Map these to line ranges via content matching against the post-edit file state. Link to the step that made the edit.
2. **Secondary**: For `Write` tool calls (new files), attribute entire file to the step.
3. **Validation**: Cross-reference with `outcome.patch` to verify Edit-derived attributions cover the actual diff. Flag any diff hunks not accounted for by Edit/Write operations (manual edits, other tools).
4. **Multi-edit same file**: Track all Edits to each file in sequence, compute cumulative line shifts, attribute final ranges to their originating steps. When edits overlap (later edit modifies earlier edit's output), attribute to the later step.
5. **Confidence scoring**: `high` for single-edit files, `medium` for multi-edit with no overlap, `low` for overlapping edits, `null` for unattributable hunks.

All attribution blocks marked with `experimental: true` in schema docs.

Agent Trace compatible format: `files[].conversations[].ranges[]` with `content_hash` (murmur3) for cross-refactor tracking.

### 4.3 Dependencies

Extract from manifest files in project directory:

- `package.json` -> dependencies + devDependencies keys
- `requirements.txt` -> package names (strip versions)
- `pyproject.toml` -> [project].dependencies
- `Gemfile` -> gem names
- Tool call arguments: `npm install X`, `pip install X` patterns

### 4.4 Metrics

- Sum token counts across steps
- `cache_hit_rate` = cache_read / total_input
- `estimated_cost_usd` from static pricing table (versioned in package)
- `total_duration_s` from timestamp range
- User override via `opentraces config set --pricing-file custom.json`

### 4.5 Outcome Signals

```python
class Outcome(BaseModel):
    success: bool | None = None        # None = unknown
    signal_source: str = "deterministic"
    signal_confidence: str = "derived"  # derived / inferred / annotated
    description: str | None = None
    patch: str | None = None
    committed: bool = False
    commit_sha: str | None = None
```

Deterministic signals: `committed` from git, `patch` from diff. `signal_confidence: "derived"` indicates this is behavioral proxy, not ground truth.

---

## Phase 5: CLI + Upload

### 5.1 Commands

```
opentraces auth                    # HF Hub authentication + optional post-session hook setup
opentraces config set/show         # Per-project/global config
opentraces discover                # List available sessions across projects
opentraces parse [--auto]          # Parse sessions into enriched JSONL
opentraces review [--web]          # Tier 3 strict review (CLI or web)
opentraces push [--approved-only]  # Upload to HF Hub
opentraces import --from dataclaw  # Import DataClaw exports
opentraces export --format atif    # Lossy ATIF conversion
opentraces migrate                 # Schema version check + migration
opentraces capabilities --json     # Machine-discoverable features
opentraces introspect --json       # Full API schema
```

**Agent-native output**: JSON with `next_steps`/`next_command`. `---OPENTRACES_JSON---` sentinel. Structured errors: `{code, kind, message, hint, retryable}`. Exit codes: 0/2/3/4/5/7.

### 5.2 Sharded HF Hub Upload

Each push creates `traces_{timestamp}_{uuid}.jsonl` (new shard per push, never append to existing).

**Per-trace state tracking** in `~/.opentraces/state.json`: pending / uploaded / failed. Retry only sends pending/failed.

**Dataset card**: Auto-generated with sentinel-delimited sections. Machine section (YAML frontmatter, stats) regenerated. User section preserved.

**Tags**: `opentraces` + `agent-traces`.

### 5.3 Post-Session Hook

`opentraces auth` offers to install a shell hook:

```bash
# In .zshrc/.bashrc
opentraces parse --auto && opentraces push --approved-only
```

### 5.4 Skill File

`opentraces install-skill claude` — copies SKILL.md into Claude Code skill directory.

---

## Phase 6: Local Web Review App

Flask on localhost via `opentraces review --web`. Optional `[web]` extra.

**Routes:**

- `GET /` — Session list with filters (project, date, model, outcome, security tier)
- `GET /session/<id>` — Session detail with expandable steps, attribution view
- `POST /session/<id>/approve|reject` — Approve/reject
- `POST /session/<id>/step/<n>/redact` — Redact step content
- `POST /push` — Push approved sessions
- `GET /api/sessions` — JSON API

**UI:**

- Security-oriented visual language (green/shield/lock)
- Collapsible tool calls with syntax-highlighted code blocks
- Sub-agent steps indented under parent with visual hierarchy
- Diff viewer for patches
- Attribution view: which lines were produced by which step
- Redaction highlighting (yellow auto-redacted, orange classifier-flagged)
- Classifier flag detail panel (what was flagged, which pattern, sensitivity level)
- Approve/reject per session and per step
- Push hard-gated behind review completion

**Frontend**: Server-rendered Jinja2 + vanilla JS. Pico CSS for styling. No build step.

---

## Phase 7: Trace Explorer (HF Space)

Gradio app at `huggingface.co/spaces/opentraces/explorer`.

No backend database — queries HF Hub Dataset Viewer API across `opentraces`-tagged datasets.

**Search & Browse:**

- By username (contributor lookup)
- By intent/task (from task.description or first user message)
- By language ecosystem, dependencies, agent, model
- By outcome (success/fail/committed)
- By schema fields (attribution present, sub-agent depth)

**Trace Viewer:**

- Readable conversation with collapsible tool calls
- Sub-agent hierarchy visualization (indented tree)
- Syntax-highlighted code blocks
- Attribution view: linked files/lines with confidence scores
- Outcome summary with patch diff

**Contributor Dashboard:**

- Enter HF username, see personal analytics
- Sessions over time, model distribution
- Token usage breakdown (input/output/cache, cost estimates)
- Tool usage patterns
- Success/failure rate (where outcome signals exist)
- Efficiency metrics: cache hit rate, cost per session, tokens per successful outcome
- No auth needed (public data, public dashboard)

**Community Stats:**

- Total traces, active contributors
- Agent/model distribution
- Top dependencies, language ecosystems
- Schema coverage (% with attribution, % with outcome signals)

---

## Phase 8: Marketing Website (opentraces.ai)

Astro on Vercel. Scaffold deploys day 1 (URL exists immediately).

**Landing Page — Two Sides:**

_For developers (contributors):_

- Selfish pitch: "Share traces, get a free analytics dashboard. Your Spotify Wrapped for coding agents."
- Security-first: Three-tier pipeline visualization. "You control what leaves your machine."
- 3-step install: Install -> Configure tier -> Push
- Screenshots: CLI in action, web review UI, dashboard

_For ML teams (consumers):_

- Why this data: real workflows, not synthetic benchmarks. Outcome signals for RL.
- Standards: how schema relates to ATIF, ADP, Agent Trace, OTel
- Usage: `datasets.load_dataset("username/opentraces-claude-code")` one-liner
- Schema explorer: interactive annotated sample trace

**Additional Pages:**

- Get Started guide
- Schema documentation
- Dashboard preview (link to HF Space)

**Design**: Security-oriented (green/shield/lock), technical credibility, neutral infrastructure positioning. No "protest art" framing.

---

## Build Sequence

```
Phase 1: Schema + Bootstrap          ─── Foundation
Phase 2: Claude Code Parser          ─── Core value (produces data)
Phase 3: Security Pipeline           ─── Trust layer (required before any upload)
Phase 4: Enrichment Pipeline         ─── Schema depth differentiator
Phase 5: CLI + Upload                ─── User-facing product
Phase 6: Web Review App              ─── Tier 3 experience
Phase 7: Explorer HF Space           ─── Contributor incentive (the hook)
Phase 8: Marketing Website           ─── Distribution (scaffold day 1, full build last)
```

Phases 1-5 are sequential (each builds on previous). Phases 6-8 can overlap after Phase 5 works end-to-end.

**Critical path**: Schema -> Parser (with sub-agents) -> Security (all 3 tiers) -> Enrichment (with full attribution) -> CLI -> Upload. First end-to-end trace on HF Hub validates the entire pipeline.

---

## Testing Strategy

**Unit**: Schema round-trips, every secret detection regex + allowlist, anonymizer (all path types including Windows/WSL), quality filter, Edit-derived attribution (single + multi-edit), snippet extraction per tool type, classifier heuristics, metrics computation, config migration.

**Integration**: Full pipeline (raw session -> enriched JSONL with sub-agents inlined), security scan (known secrets redacted, allowlist survives, classifier flags correct patterns), sharded HF upload (mock SDK), CLI end-to-end, recursive sub-agent parsing.

**Fixtures**: Real redacted Claude Code sessions (with sub-agents), crafted sessions with known secrets, DataClaw format samples, corrupted/incomplete sessions, very long sessions (memory test).

---

## Risk Register

| Risk                                   | Impact             | Mitigation                                                          |
| -------------------------------------- | ------------------ | ------------------------------------------------------------------- |
| Claude Code format changes             | Parser garbage     | Unknown block detection + warning threshold                         |
| HF Hub rate limits                     | Upload failures    | Sharded files + per-trace state + retry                             |
| Secret detection false negatives       | Leaked credentials | Two-pass scan + Tier 2 classifier + Tier 3 default                  |
| Secret detection false positives       | User abandonment   | Context-aware scanning, no entropy on results, --explain-redactions |
| Large sessions OOM                     | Parser crash       | Streaming parser with generators                                    |
| Schema needs breaking change           | Migration pain     | schema_version + opentraces migrate command                         |
| DataClaw captures mindshare            | Reduced adoption   | Lock in HF relationship (real moat) + full product depth            |
| Attribution wrong for complex sessions | ML distrust        | Confidence scoring, null for unattributable, experimental flag      |
| Recursive sub-agent parsing cycles     | Infinite loop      | Visited set + max depth (10)                                        |
| Competitive bid lost                   | No HF partnership  | Ship impressive end-to-end product demonstrating full vision        |

---

## Success Criteria for Competitive Bid

1. **End-to-end demo**: Install -> parse real session (with sub-agents) -> security scan -> review in web UI -> push to HF -> view in explorer dashboard
2. **Schema differentiation visible**: Attribution blocks, sub-agent hierarchy, per-step tokens, outcome signals — all present in the published trace
3. **All three security tiers working**: Tier 1 auto-redact, Tier 2 classifier flagging, Tier 3 web review
4. **Contributor dashboard live**: Enter a username, see real analytics from published traces
5. **Marketing site live**: opentraces.ai with clear positioning, schema docs, install guide
6. **Zero credential leaks**: Tier 1 floor guarantee across all published traces
7. **Schema published as protocol**: `pip install opentraces-schema` works, others can adopt

---

## Verification Plan

1. `pip install opentraces-schema && python -c "from opentraces_schema import TraceRecord; print('ok')"` — schema package works standalone
2. `pip install opentraces && opentraces --help` — CLI installs
3. `opentraces discover` — finds sessions (or structured error)
4. `opentraces parse` — produces JSONL with sub-agent steps inlined, snippets extracted, attribution blocks
5. `opentraces review --web` — web UI shows sessions with classifier flags, redaction highlights
6. `opentraces push` — sharded JSONL uploaded, dataset card generated
7. `datasets.load_dataset("username/opentraces-claude-code")` — loads from HF Hub
8. HF Space explorer shows the published trace with full detail
9. Security: known secrets redacted, classifier flags internal patterns, allowlist survives
10. Large session test: 5k+ step session parses without OOM
11. Sub-agent test: session with nested sub-agents produces correct parent_step links
12. Attribution test: Edit-derived attribution correct for single + multi-edit files

---

## GSTACK REVIEW REPORT

| Review        | Trigger               | Why                     | Runs | Status          | Findings                                                                                             |
| ------------- | --------------------- | ----------------------- | ---- | --------------- | ---------------------------------------------------------------------------------------------------- |
| CEO Review    | `/plan-ceo-review`    | Scope & strategy        | 1    | Complete        | 6 findings (2 critical: timeline, HF moat; 3 high: schema-first, Claude-only pitch, site sequencing) |
| Eng Review    | `/plan-eng-review`    | Architecture & tests    | 1    | Complete        | 18 findings (4 critical: sub-agent scope, OOM, upload atomicity, error recovery; 8 high)             |
| Design Review | `/plan-design-review` | UI/UX gaps              | 0    | Folded into Eng | Web review + explorer covered in Eng findings                                                        |
| Codex Review  | `/codex review`       | Independent 2nd opinion | 0    | —               | —                                                                                                    |

**VERDICT:** CEO + ENG REVIEWS COMPLETE. All critical findings addressed in revised plan. User overrode scope reduction decisions (added back sub-agents, snippets, attribution, Tier 2). Plan approved for implementation.
