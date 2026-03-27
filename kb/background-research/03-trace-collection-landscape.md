# AI Agent Trace Collection Landscape: R&D Scouting Brief

> Research date: 2026-03-27
> Sources: github.com/vincentkoc/openamnesia, github.com/wunderlabs-dev/claudebin.com, github.com/elophanto/EloPhanto, huggingface.co/EloPhanto
> Category: Comparative analysis of three open-source trace collection/sharing implementations
> Context: Building an open-source trace collection mechanism for community sharing on HuggingFace

---

## Executive Summary

Three projects occupy the "AI agent trace collection and sharing" space, each attacking a different slice of the problem:

| Project | Philosophy | Trace Source | Sharing Target | Maturity |
|---------|-----------|-------------|----------------|----------|
| **OpenAmnesia** | Local-first memory engine | Claude, Codex, Cursor, Terminal, iMessage | Self-hosted API (no community sharing) | Hackathon prototype |
| **ClaudeBin** | Pastebin for Claude sessions | Claude Code only | claudebin.com (web, public URLs) | Production service |
| **EloPhanto** | Self-evolving agent OS | Own agent interactions | HuggingFace dataset via centralized API | Active development |

**Key insight**: No project currently solves the full problem of "collect traces from any agent, normalize them, and share with a community on HuggingFace." OpenAmnesia has the best normalization pipeline but no sharing. ClaudeBin has the best viewer but only handles Claude Code and shares via its own platform. EloPhanto has the only HuggingFace integration but only collects its own traces.

---

## 1. OpenAmnesia

### Overview

A local-first "memory stream" that turns messy AI agent session logs into structured moments, facts, and skills. Created by Vincent Koc. Designed as a continual learning context engine so agents can cold-start with high-signal history.

- **GitHub**: github.com/vincentkoc/openamnesia
- **Stars**: 15 | **Forks**: 2 | **License**: GPL-3.0
- **Language**: Python 3.11+ (backend), TypeScript (React/Vite frontend)
- **Created**: 2026-02-06 | **Last updated**: 2026-03-27

### Problem It Solves

Agents are only as good as their context, but most context lives in scattered traces: coding agent sessions, IDE chats, tool calls, and personal messages. OpenAmnesia provides a deterministic pipeline that normalizes these diverse trace formats into a unified schema, then extracts structured memories and skills.

### Architecture

```
Connectors (poll local files)
  → SourceRecord (pre-normalization IR)
    → Normalize (SHA-256 dedup, stable Event IDs)
      → Sessionize (group by source + session_id)
        → Momentize (segment into Moments)
          → Extract/Annotate (keyword-based intent/outcome)
            → Skill Mine (frequency-based skill candidates)
              → Store (SQLite with WAL)
                → Export (daily Markdown, YAML skills)
                  → API (FastAPI, read-only)
                    → Frontend (React/Vite SPA)
```

**Evidence**: `amnesia/daemon.py`, `amnesia/pipeline/`, `amnesia/connectors/`

#### Core Data Model (`amnesia/models.py`)

| Type | Description |
|------|-------------|
| `Event` | Atomic unit: one turn/message with `event_id` (SHA-256), `ts`, `source`, `session_id`, `turn_index`, `actor`, `content`, optional `tool_name`/`tool_args_json`/`tool_result_json`/`tool_status` |
| `Session` | Group of Events sharing `(source, session_id)` with `start_ts`, `end_ts`, `summary` |
| `Moment` | Higher-level segment with `intent`, `outcome`, `friction_score`, `evidence_json`, `artifacts_json` |
| `SourceRecord` | Pre-normalization intermediate produced by all connectors |

#### Agent Connectors

| Agent | Connector | Source Path | Parsing Details |
|-------|-----------|-------------|-----------------|
| **Claude Code** | `connectors/claude.py` | `~/.claude/` | Reads `history.jsonl` + `projects/**/*.jsonl`, parses `tool_use`/`tool_result` blocks, extracts `cwd` as group hint, excludes `subagents/*.jsonl` |
| **OpenAI Codex** | `connectors/codex.py` | `~/.codex/` | Reads `history.jsonl` + `sessions/**/*.jsonl`, parses `response_item` with `function_call`/`function_call_output`, extracts exit codes |
| **Cursor** | `connectors/cursor.py` | Configurable | Thin `FileDropConnector` subclass, generic JSONL parsing |
| **Terminal** | `connectors/terminal.py` | Configurable | Reads `.log` files |
| **iMessage** | `connectors/imessage.py` | `~/Library/Messages/chat.db` | Two modes: direct SQLite read, or exported JSONL |

#### Key Design Decisions

1. **Deterministic preprocessing before LLM**: Normalization, filtering, and deduplication happen before any LLM step. SHA-256-derived event IDs ensure stable deduplication.
2. **Protocol-based connector interface**: `SourceConnector` is a `typing.Protocol`, connectors satisfy it structurally (no inheritance required). `FileDropConnector` base provides `poll()` + `_iter_files()` + `_parse_line()`; each agent connector overrides `_parse_line()` only.
3. **Pipeline with hook registry**: 6 injection points (`pre_normalize`, `post_normalize`, `post_sessionize`, `post_momentize`, `post_extract`, `post_skill_mine`), plugins loaded by module path strings in config. `PipelineContext` carries `events`, `sessions`, `moments`, and a freeform `derived` dict.
4. **Incremental trawling**: `IncrementalFileTrawler` uses file offset, inode, mtime, and SHA-256 digest for byte-precise resume. `JsonlSpool` queue decouples IO from processing.
5. **Local-only storage**: SQLite with WAL mode, `PRAGMA query_only=ON` for the API layer. 14 tables with comprehensive indexing. `Store` is a `typing.Protocol` with `SQLiteStore` and `InMemoryStore` implementations.
6. **Internal event bus**: Topic-keyed in-memory pub/sub for observability events (`run.started`, `source.poll.completed`, `pipeline.completed`, etc.), no persistence.
7. **Registry + dynamic dispatch**: `connectors/registry.py` maps source name to class; unknown names fall back to `FileDropConnector` or dynamic import.

#### REST API Surface (`amnesia/api/server.py`)

FastAPI on port 8000, all endpoints read-only GET except one PATCH:

| Endpoint | Description |
|----------|-------------|
| `GET /api/stats` | Total events, sessions, moments, skills, entities, sources breakdown |
| `GET /api/events` | Paginated events with filters: `source`, `session_id`, `actor`, `since`, `until` (limit 500) |
| `GET /api/sessions` | Paginated sessions with `source` filter (limit 200) |
| `GET /api/moments` | Paginated moments; `GET /api/moments/{id}` returns moment + embedded events |
| `GET /api/skills` | Paginated skills; `GET /api/skills/{id}` returns skill + evals + patches |
| `PATCH /api/skills/{id}` | Update skill status (`candidate`/`validated`/`promoted`/`rejected`) |
| `GET /api/sources` | Source statuses with heartbeat int[60] array |
| `GET /api/sources/{source}/diagnostics` | Heartbeat[120], latency stats, config, diagnosis |
| `GET /api/timeline` | Bucketed timeline (5min/10min/15min/30min/hour/6hour/day granularity) |
| `GET /api/entities` | Entity rollups by type with mention counts, confidence, first/last seen |
| `GET /api/audit` | Ingest audit trail |
| `GET /api/exports` | Export file listing; `/api/exports/memory/{file}` and `/api/exports/skills/{file}` |
| `GET /api/memory/daily` | LLM-assisted daily/weekly/monthly memory summaries |

#### CLI Entry Points

| Command | Description |
|---------|-------------|
| `amnesia` | Interactive menu: ingest, discovery, e2e, API server, frontend dev |
| `amnesia-daemon` | Direct daemon: `--once`, `--config`, `--sources`, `--json-summary`, `--events-limit` |
| `scripts/run_ingest.py` | Full ingest with filters: `--source`, `--since`, `--until`, `--include-groups`, `--exclude-contains`, `--reset-state`, `--json` |
| `scripts/run_discovery.py` | Embed + cluster + enrich: `--source` (required), `--use-llm`, `--model`, `--dims`, `--limit` |

#### Dependencies and Build

| Aspect | Detail |
|--------|--------|
| **Python** | 3.11+ (`pyproject.toml`), setuptools >=69.0, `pip install -e .` / `pip install -e '.[dev]'` |
| **Core deps** | FastAPI >=0.110, uvicorn >=0.29, PyYAML >=6.0, python-dotenv >=1.0.1, rich >=13.0, tqdm >=4.66 |
| **Optional `[llm]`** | litellm >=1.59, pydantic >=2.8 (also transitive via FastAPI) |
| **Frontend** | React 19, Vite 6.1, Tailwind v4, react-router-dom v7, @tanstack/react-query v5, TypeScript ~5.7 |
| **Build** | GNU Make orchestration, `make api` (uvicorn), `make ui` (vite dev) |
| **Test** | pytest >=8.0, `--maxfail=1`, no coverage configured, no frontend tests |
| **CI** | GitHub Actions: lint (ruff) + test (pytest) on push/PR, Python 3.11 only, no mypy in CI |
| **Deploy** | Docker (`python:3.11-slim`, port 8000, GHCR), Akash SDL (0.5 CPU, 512Mi), Render CLI |
| **Pre-commit** | pre-commit-hooks v6, pyupgrade v3.21.2, ruff v0.14.2, mirrors-mypy v1.11.2 |
| **Stability risk** | No Python lockfile (all `>=` bounds), litellm breaks frequently, Tailwind v4 + React 19 are cutting-edge |

#### Discovery Pipeline (LLM-enrichment, separate from main pipeline)

1. Read Events from SQLite
2. `HashEmbeddingProvider` - local TF-IDF-style 128-dim hash vectors (no external ML deps)
3. Deterministic prefix-bucket clustering (top-2 embedding dimensions)
4. Optional LLM enrichment via LiteLLM (default: `gpt-5-nano`) for structured summaries
5. Optional You.com grounding

### Strengths

- **Best normalization pipeline**: The connector + normalize + sessionize chain is the most thoughtful approach to multi-agent trace normalization in this space
- **Deterministic-first design**: SHA-256 dedup keys, incremental trawling with file offset tracking, zero external ML deps in the core path
- **Plugin system**: 6 hook points allow extending the pipeline without modifying core code
- **Privacy-preserving**: All processing is local-first, no data leaves the machine by default
- **Multi-agent support**: Only project that handles Claude, Codex, Cursor, Terminal, and iMessage

### Limitations & Gaps

1. **No community sharing mechanism**: No HuggingFace integration, no upload/export to any shared platform. Zero matches for `huggingface`, `datasets`, or `HF_` in the codebase.
2. **Hackathon-origin codebase**: Single commit dated 2026-02-06, entire project written at once.
3. **Three source connectors are stubs**: Codex/Cursor/Terminal `ReadOps` classes explicitly labeled `"""Placeholder operations"""` and return empty output; the `sources/` module tree is vestigial.
4. **Embedding pipeline disconnected**: `embed_events`, `cluster_embeddings`, `enrich_clusters` exist and are tested in isolation but `daemon._process_records` never calls them.
5. **Two missing export modules**: `amnesia/exports/memory.py` and `amnesia/exports/skills_md.py` are imported by `scripts/run_ingest.py`, `scripts/run_e2e.py`, and `amnesia/api/memory.py` but do not exist in the repo, meaning those code paths are broken.
6. **No tests for API layer**: 9 test files with ~10 tests total, zero HTTP endpoint tests. Two 404 response bugs (`server.py:255,308`) return `{"error": "not found"}, 404` which FastAPI serializes as a 200 with a list body, not a 404. Same bug in `api/memory.py:53-55`.
7. **Regex bug in `memory_materialize.py:368`**: `r"\\b{re.escape(verb)}\\b"` double-escapes the backslash, so `\b` becomes literal two-char sequence. Word boundary matching never fires, every action defaults to `"track"`.
8. **Thread-safety inconsistency**: `list_sessions` (line 175) and `get_moment` (line 242) call `_get_conn()` directly without the `_db_lock` context manager used by other endpoints. Race condition under concurrent requests.
9. **No auth on API**: CORS is `allow_origins=["*"]` (`server.py:77`). `PATCH /api/skills/{skill_id}` mutates DB without auth. The read connection uses `PRAGMA query_only=ON` but `update_skill_status` creates a separate `build_store()` connection without that pragma.
10. **No PII redaction**: Entity extraction intentionally captures emails/phone numbers, stores verbatim in SQLite. `entity.py:PLACE_TERMS` is a hardcoded 8-item set including `"rise"` and `"barclays"`, clearly personal to the author.
11. **No Python lockfile**: All deps are lower-bounded only (`>=`), reproducible builds not guaranteed. litellm has frequent breaking changes.
12. **Single-threaded ingestion**: Connectors processed sequentially in `for connector in self.connectors` loop, no concurrency.
13. **No schema migrations**: `schema.sql` uses `CREATE TABLE IF NOT EXISTS`, no Alembic. Schema changes require manual DB recreation.
14. **`skill_mine.py` produces trivially generic skills**: Every intent gets hardcoded steps `["collect evidence", "summarize", "publish"]`. `optimize_skill` is a 9-line no-op that sets `metrics["optimized"] = True`.
15. **Memory export endpoints hardcoded**: `amnesia/api/memory.py:32` hardcodes `sqlite:///./data/amnesia.db` and `./exports/memory`, no config plumbing.
16. **`pytest.ini --maxfail=1`** halts the test suite on first failure, hiding subsequent breakage.
17. **mypy not in CI**: Pre-commit hook and Makefile target exist, but CI only runs lint + test.

### What We Can Learn

- **The normalization pipeline pattern is excellent**: `SourceRecord → Event → Session → Moment → Skill` is a clean, composable hierarchy
- **SHA-256 dedup is the right approach**: Stable, deterministic, no collision risk for practical trace volumes
- **Incremental trawling is essential**: Without file offset tracking, re-ingestion of large trace files becomes expensive
- **Hook points enable extensibility**: 6 lifecycle hooks let plugins inject behavior without modifying core
- **Connector protocol pattern**: Using `typing.Protocol` instead of inheritance for the connector interface is Pythonic and flexible
- **Config-driven source selection**: Single `config.yaml` controls which agents to ingest, paths, globs, and filter windows

---

## 2. ClaudeBin

### Overview

A "pastebin for Claude Code sessions" that lets users publish and share Claude Code conversations via shareable URLs with full syntax highlighting, tool call rendering, and the ability to continue conversations. Built by Wunderlabs.

- **GitHub**: github.com/wunderlabs-dev/claudebin.com
- **Stars**: 58 | **Forks**: 7 | **License**: MIT
- **Language**: TypeScript (Next.js 16 + Bun)
- **Created**: 2025-12-12 | **Last updated**: 2026-03-24
- **Topics**: `claude`, `claude-code`, `claude-code-plugin`

### Problem It Solves

After a long Claude Code session, there isn't a simple way to share what happened, prompts, responses, file edits, and tool calls all stay inside the terminal. ClaudeBin makes sharing and resuming those sessions as easy as creating a URL.

### Architecture

```
Claude Code CLI Plugin
  → POST /api/auth/start (device-code OAuth via GitHub)
  → POST /api/sessions/publish (upload raw JSONL, max 50MB)
    → Supabase Storage (sessions/{userId}/{id}.jsonl)
    → Background: processSession()
      → parseJsonl() → createPipeline() → ingest/flush
        → RawMessage → IntermediateMessage → ContentBlock (17 types)
      → batch-insert messages (100 at a time)
      → generateTitle() via OpenRouter (gpt-4o-mini)
      → sessions.update(status: "ready")
  → GET /api/sessions/poll (await processing)
  → Browser: /threads/{id} (rendered conversation)
```

**Evidence**: `app/src/app/api/sessions/publish/route.ts`, `app/src/server/services/processor.ts`

#### The Parser Service (`app/src/server/services/parser/`)

The most sophisticated part of the codebase. A stateful streaming pipeline that handles Claude Code's interleaved message format:

1. **`schemas.ts`**: Zod schemas for raw Claude Code JSONL (user/assistant/skipped message types)
2. **`transforms.ts`**: Pure functions mapping raw tool names to typed `ContentBlock` structs, path sanitization (strips home dir), ANSI code stripping, system-reminder XML tag removal
3. **`pipeline.ts`**: Merge/split strategy, tool-use blocks + tool-result blocks merged into single `IntermediateMessage`, multi-turn assistant messages with same ID merged, skill/local command pattern parsing, task management snapshot tracking

#### ContentBlock Type System (`app/src/supabase/types/message.ts`)

17 block types forming a discriminated union:

| Category | Block Types |
|----------|------------|
| Text | `TextBlock`, `ThinkingBlock` |
| File ops | `FileReadBlock`, `FileWriteBlock`, `FileEditBlock` |
| Shell | `BashBlock` |
| Search | `GlobBlock`, `GrepBlock` |
| Web | `WebFetchBlock`, `WebSearchBlock` |
| Tasks | `TaskBlock`, `TaskOutputBlock`, `TaskStopBlock`, `TasksBlock` |
| Protocol | `McpBlock`, `GenericBlock` |
| Agents | `SkillBlock`, `LocalCommandBlock` |
| UI | `QuestionBlock` |

#### Full API Surface

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/auth/start` | None | Create CLI auth session, returns `{code, url, expires_at}` (10-min TTL) |
| `GET` | `/api/auth/poll?code=` | None | Poll: `pending` / `expired` / `success` with tokens |
| `POST` | `/api/auth/refresh` | None | Exchange refresh_token for new access_token |
| `GET` | `/api/auth/validate?token=` | None | Check access_token validity |
| `POST` | `/api/sessions/publish` | access_token in body | Upload JSONL (max 50MB), returns `{id, status}` |
| `GET` | `/api/sessions/poll?id=` | None | Poll processing: `processing` / `ready` (with URL) / `failed` |
| `GET` | `/api/threads/[id]/messages?from=&to=` | None (capability token) | Paginated message range |
| `GET` | `/api/threads/[id]/md` | None (public only) | Export as Markdown with `<continue-conversation>` XML wrapper |
| `GET` | `/api/pixel/t/[id]` | None | Track page view (1x1 GIF, SHA-256 dedup by IP+UA) |
| `GET` | `/api/openapi.json` | None | OpenAPI 3.1 spec (zod-to-openapi) |

**Server Actions** (Next.js `"use server"`): `getPublicThreads`, `deleteThread`, `toggleVisibility`, `getLikeStatus`, `like`, `deleteAccount`, `getMessagesBySessionId`.

**Resumability**: Thread pages display a copyable command: `curl -s "https://claudebin.com/api/threads/{id}/md" | claude` to resume any shared thread locally.

#### Design Patterns

- **Repository pattern**: All DB access behind namespaced objects (`sessions`, `messages`, `cliAuth`, `profiles`), each taking `SupabaseClient` as first arg
- **Two Supabase client modes**: `createClient()` (cookie-based, respects RLS) vs `createServiceClient()` (service role, bypasses RLS)
- **Capability-token access**: Thread IDs are 10-char nanoid (36^10 ~ 3.7T combinations), the ID itself acts as an access token
- **Next.js `after()` for background work**: Processing runs post-response, no external workers needed
- **Tag-based cache invalidation**: `unstable_cache` with `revalidateTag("thread-{id}")`
- **Discriminated union rendering**: `block()` function in `renderers.tsx` is a pure switch over `BlockType`
- **`// ABOUTME:` documentation convention**: Used throughout for intent documentation on non-obvious code
- **Dogfooding CI**: `.github/workflows/claudebin-session.yml` enforces that every PR body contains a `claudebin.com/threads/` link

#### Session Status FSM

`PROCESSING` -> `READY` (success) or `PROCESSING` -> `FAILED` (error)

#### Data Storage

- **PostgreSQL via Supabase**: `profiles`, `sessions`, `messages`, `cli_auth_sessions`, `session_likes`
- **24 SQL migration files** (Jan 2025 - Mar 2026)
- **RLS policies**, soft delete on profiles, dedup page views
- **DB-level column protection**: Triggers blocking direct manipulation of `viewCount`, `likeCount`, `isFeatured`, `status`, `storagePath` by authenticated users (migration `20260301`)
- **Email removed from profiles**: `20260217` migration dropped email column after security audit

#### Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Next.js 16 (App Router, Turbopack) |
| Runtime | Bun 1.3.5 (pinned) |
| Database | Supabase (PostgreSQL) |
| Auth | GitHub OAuth via Supabase + device-code flow for CLI |
| Storage | Supabase Storage (JSONL blobs) |
| UI | shadcn/ui + Radix + Tailwind v4 |
| Syntax highlighting | shiki |
| Monitoring | Sentry (@sentry/nextjs ^10) |
| Deployment | Vercel |
| API docs | zod-to-openapi (OpenAPI 3.1 at `/api/openapi.json`) |

### Strengths

- **Best viewer/renderer**: 17 typed content blocks with dedicated rendering components, syntax highlighting, file diffs, tool call visualization
- **Production-quality infrastructure**: Supabase, Vercel, Sentry, GitHub OAuth, proper migration management
- **Privacy-aware parsing**: Path sanitization strips home directories, ANSI code removal, system-reminder XML removal
- **Well-tested parser**: Dedicated test files for the parser pipeline, transforms, and local commands
- **Community adoption**: 58 stars, HN front page discussion, users already embedding sessions in PRs and blog posts
- **Continue conversation feature**: Export as Markdown with `<continue-conversation>` XML wrapper
- **OpenAPI spec**: Self-documented API surface

#### Dependencies and Build

| Aspect | Detail |
|--------|--------|
| **Runtime** | Bun 1.3.5 (pinned via `packageManager`), TypeScript ^5.3 (resolves 5.9.3) |
| **Framework** | Next.js 16.1.4 (exact pin, no caret), Turbopack for dev |
| **Key deps** | @supabase/supabase-js ^2.39, @supabase/ssr ^0.8, @sentry/nextjs ^10, @tanstack/react-query ^5.90, shiki ^3.21, zod ^4.3.5, react-markdown ^10.1, tailwindcss ^4.1.18 |
| **UI** | shadcn/ui "new-york" style, Radix primitives, Lucide icons, Embla carousel |
| **Lint** | Biome 2.3.14 (not ESLint), Husky pre-commit runs `bun check` |
| **Test** | Bun's built-in test runner (`bun:test`), 3 parser test files (35 tests), no integration/e2e |
| **CI** | 3 workflows: Biome lint, PR claudebin-link enforcement, Supabase migrations push |
| **Deploy** | Vercel (`vercel.json`), Sentry source maps, no Docker/Helm |

### Limitations & Gaps

1. **Claude Code only**: No support for Codex, Cursor, Windsurf, or any other agent
2. **Centralized platform dependency**: All data goes through claudebin.com/Supabase, not user-controlled
3. **No HuggingFace integration**: Shares via its own URLs, not community datasets
4. **No trace normalization**: Stores Claude Code's native JSONL format, parsed into its own block types but not into a universal trace schema
5. **50MB size limit**: Large sessions may be truncated
6. **No batch/programmatic export**: Individual session sharing only, no bulk collection or dataset creation
7. **No anonymization/redaction**: While paths are sanitized, no PII detection or content redaction
8. **Private thread access control vulnerability**: Main thread page (`/threads/[id]`) does NOT check `isPublic` or ownership. `getCachedThread` uses service client bypassing RLS and returns private threads. The embed page correctly gates (`if (!thread.isPublic && thread.userId !== user?.id) notFound()`), but the main page only calls `notFound()` when thread is null. Anyone knowing a private thread's 10-char ID can view it.
9. **No rate limiting**: `POST /api/sessions/publish` and CLI auth endpoints have no rate limiting. Flooding possible.
10. **No retry on server restart**: `processSession` runs fire-and-forget via Next.js `after()`. If server restarts mid-processing, session stays `PROCESSING` permanently with no recovery mechanism.
11. **OpenRouter dependency**: Title generation requires `OPENROUTER_API_KEY`, falls back to first-line extraction if absent
12. **Vendor lock-in**: Supabase + Vercel + Next.js 16 (exact pin)
13. **No offline mode**: Requires internet connectivity and the claudebin.com service
14. **LOCAL_COMMAND missing from markdown export**: `blockToMarkdown` switch in `message-to-markdown.ts` falls through to `default: return ""`, silently omitting local commands
15. **Inconsistent logging**: `processor.ts` and `openrouter.ts` use raw `console.error` instead of the structured `logger` utility (caught by Sentry's `captureConsoleIntegration`, but loses `[module]` prefix)
16. **`dangerouslySetInnerHTML` for Shiki**: `code.tsx:88` relies on "Shiki generates safe HTML" with no schema validation on output before insertion

### Community Signal

**Hacker News (Show HN, Feb 2026)**: Overwhelmingly positive reception. Users expressed strong demand for this capability. Key use cases mentioned: attaching session links to PRs, embedding conversations in blog posts. One feature request for session bookmarking/replay. No technical criticism or security concerns raised.

### What We Can Learn

- **The parser pipeline is a reference implementation**: The merge/split strategy for handling Claude Code's interleaved tool-use/tool-result format is well-engineered
- **ContentBlock discriminated union is the right abstraction**: Type-safe rendering of diverse tool outputs
- **Device-code OAuth flow works well for CLI tools**: Users don't need to copy-paste tokens
- **Path sanitization is essential**: Stripping home directories before sharing is a privacy baseline
- **50MB JSONL limit is practical**: Most sessions fit, but long multi-day sessions may not
- **Shareable URLs with viewer drive adoption**: The rendered view, not raw data, is what users actually want

---

## 3. EloPhanto

### Overview

A self-evolving AI agent operating system with 130+ tools that acts as a personal AI OS. Built by Petr Royce (0xroyce). The HuggingFace integration (`DatasetBuilder`) captures agent interaction traces and uploads them to a community dataset.

- **GitHub**: github.com/elophanto/EloPhanto
- **Stars**: 36 | **Forks**: 5 | **License**: Apache-2.0
- **Language**: Python 3.12+ (core), TypeScript (browser bridge, web dashboard, VS Code extension)
- **Created**: 2026-02-18 | **Last updated**: 2026-03-27
- **HuggingFace**: huggingface.co/EloPhanto (1 dataset, 475 rows, 76 likes)

### Problem It Solves

EloPhanto is primarily an autonomous AI agent, not a trace collection tool. The trace collection is a secondary capability that enables a self-learning flywheel: collect traces from agent interactions, upload to a community dataset, and eventually use for fine-tuning.

### Architecture (Trace Collection Focus)

```
Agent._run_with_history(goal)
  → AgentResponse (with tool_calls, steps)
    → Background fire-and-forget:
      → DatasetBuilder.record_task()
        → Sanitize (17 regex patterns for API keys, PII)
        → Quality filter (min turn count, min 1 tool call)
        → Signal extraction (sentiment, denial, error detection)
        → Local buffer (SQLite: collect_examples table)
        → When buffer >= batch_size (default 10):
          → POST api.elophanto.com/v1/collect
            → Server-side sanitization (14 mirrored patterns)
            → HuggingFace dataset push
```

**Evidence**: `core/dataset_builder.py`, `core/data_sanitizer.py`

#### Trace Format (HuggingFace Dataset Schema)

The dataset at `huggingface.co/datasets/EloPhanto/dataset` has 475 rows, 10.4 MB:

| Column | Type | Description |
|--------|------|-------------|
| `task_id` | string (UUID) | Unique task identifier |
| `conversations` | list of dicts | Chat history: `[{role, content, tool_calls?}]` |
| `metadata` | dict | `{success, task_type, timestamp, has_errors, model_used, tools_used, turn_count, has_denials, has_tool_use, user_sentiment, duration_seconds}` |
| `agent_id` | string (SHA256) | Agent instance fingerprint |
| `created_at` | string (ISO 8601) | Record creation timestamp |

**Conversation format**:
```json
[
  {"role": "system", "content": "..."},
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": "...", "tool_calls": [...]},
  {"role": "tool", "content": "{...}"},
  {"role": "assistant", "content": "..."}
]
```

#### Data Sanitization (`core/data_sanitizer.py`)

Two layers of defense-in-depth:

**Client-side (17 patterns)**:
- API keys: GitHub PATs (`ghp_`, `gho_`), OpenAI (`sk-`), AWS (`AKIA`), HuggingFace (`hf_`), EloPhanto (`elp_`)
- Auth tokens: Bearer JWTs, Slack tokens (`xoxb-`, `xoxp-`)
- Secrets: passwords in URLs, generic `password=`/`secret=` patterns
- PII: file paths with `/Users/` or `/home/`, email addresses
- Agent-specific: vault references, browser tool call content

**Server-side (14 mirrored patterns)**: Same categories, applied again at the collection API.

#### Quality Filtering (`DatasetBuilder`)

- Minimum 1 tool call required
- Minimum turn count (configurable, default 2)
- Signal extraction enriches metadata: user sentiment (positive/negative/neutral via keyword matching), denial detection, error detection
- All traces marked with `task_type: "planning"` (currently hardcoded)

#### Gateway Protocol (`core/protocol.py`)

WebSocket-based `GatewayMessage` format:
```json
{
  "type": "chat|approval_response|command|response|approval_request|event|status|error",
  "id": "uuid",
  "session_id": "uuid",
  "channel": "cli|telegram|discord|slack|child",
  "user_id": "string",
  "data": {}
}
```

35 event types covering goals, swarm agents, autonomous mind, heartbeat, webhooks.

#### Tool System

130+ tools across 12 categories, self-describing via JSON Schema (`BaseTool.to_llm_schema()`). Tools are organized in:
- `tools/system/` (filesystem, shell)
- `tools/browser/` (47 browser automation tools via Playwright + CDP)
- `tools/knowledge/` (search, embed, RAG)
- `tools/self_dev/` (plugin creation, skill management)
- `tools/swarm/` (external agent orchestration)
- `tools/payments/` (Solana, Ethereum)
- `tools/experimentation/` (hypothesis testing)

Plus dynamic plugins in `plugins/` (hot-reloadable, agent-created).

#### Swarm Integration

The Swarm system (`core/swarm.py`) orchestrates external coding agents:
- Supported: Claude Code, OpenAI Codex, Gemini CLI, any CLI agent
- Mechanism: isolated git worktrees + tmux sessions
- Context enrichment from knowledge vault before launch
- Background polling for PR creation, CI status, completion
- Child agents can connect back via WebSocket as `ChildChannelAdapter`

#### Core Agent Architecture

**`core/agent.py` (2,921 lines, 53 methods, 16 async)**: Central orchestrator and God Object. `Agent.initialize()` wires all subsystems via 30+ `_inject_*_deps()` methods. The agent loop `_run_with_history()` implements a **plan -> execute -> reflect -> remember** cycle.

**Context management**: `_compress_browser_context()` keeps last 3 screenshots, truncates old tool results to 1500 chars, caps total context at ~800K chars. On overflow: emergency trim to last 20 messages.

**Parallel tool execution**: Tool calls batched into parallel groups (`asyncio.gather`) when all tools in the group are `_PARALLEL_SAFE_TOOLS` (read-only ops). Mutating tools form sequential barriers.

**Autonomous Mind** (`core/autonomous_mind.py`, 1,224 lines): Background thinking loop that wakes on schedule, evaluates priority stack (active goals -> revenue -> pending tasks -> capability gaps -> presence -> knowledge), executes highest-value action, logs to `data/mind_actions.log`, pauses when user sends a message.

#### Permission System (`core/executor.py`)

Permission ladder: `SAFE` -> `MODERATE` -> `DESTRUCTIVE` -> `CRITICAL`. Three modes: `full_auto`, `smart_auto`, `ask`. Per-tool overrides via `permissions.yaml`. Calls async `approval_callback` for human-in-the-loop approval.

#### Authority Tiers (`core/authority.py`)

CLI always owner, unconfigured = owner (backward compat). Tool manifest filtered per tier before LLM sees it, PUBLIC users get no tools. Secondary enforcement in executor as safety net against hallucinated tool calls.

#### Security Architecture (7 documented layers)

| Layer | Implementation | Key Detail |
|-------|---------------|------------|
| **Prompt injection** | `core/injection_guard.py` | 9 regex patterns (instruction override, role switch, delimiter attack, base64, exfiltration). All external tool outputs wrapped in `[UNTRUSTED_CONTENT]` markers. |
| **PII guard** | `core/pii_guard.py` | Luhn algorithm for credit cards (not just regex), SSN, phone, email+password combos, bank accounts, API keys |
| **Credential vault** | `core/vault.py` | 480K PBKDF2-SHA256 iterations (OWASP recommended), Fernet AES-128-CBC + HMAC-SHA256, auto-backup on every write |
| **Shell execution** | `tools/system/shell.py` | Blacklist patterns from config, protected file checks block reads/writes to `core/executor.py`, `core/vault.py`, `core/config.py` |
| **Skill security** | `core/skills.py` | 15 blocked patterns (download-execute, reverse shells, credential theft, prompt injection, base64, `rm -rf /`), SHA-256 checksum on hub installs, revocation to `skills/_revoked/` |
| **Swarm isolation** | `core/swarm_security.py` | Context sanitization strips PII + vault refs, diff scanner for suspicious patterns, env var stripping (`VAULT*`, `SECRET*`, `TOKEN*`, `API_KEY*`), kill switch on timeout |
| **Log redaction** | `core/log_setup.py` | `RedactingFilter` on file + console handlers, covers `api_key:`, `token:`, `sk-*`, `ghp_*`, bearer tokens |

#### Dependencies and Build

| Aspect | Detail |
|--------|--------|
| **Python** | 3.12+ (CI tests 3.12 + 3.13), hatchling build backend, `uv` package manager |
| **Core deps** | litellm >=1.50 (excludes 1.82.7-8, supply chain attack), playwright >=1.48, aiogram >=3.0, cryptography >=42.0, sqlite-vec >=0.1.1, apscheduler >=3.10, httpx >=0.27, pymupdf >=1.24, websockets >=13.0, textual >=0.70 |
| **Notable** | solders + base58 (Solana wallet) in core deps, agentmail (managed email), rapidocr-onnxruntime (on-device OCR), pyotp (2FA/TOTP) |
| **Optional groups** | `dev` (pytest, ruff, mypy), `payments` (eth-account), `mcp` (mcp[cli]), `desktop` (pyautogui) |
| **Node.js** | 22.x (Dockerfile), Playwright + stealth plugin (browser bridge), React 19 + Vite 6 + Zustand 5 (web), ws 8 (VS Code extension) |
| **Build** | `uv sync`, `tsup` (browser bridge), `vite build` (web), `esbuild` (VS Code extension), `setup.sh` one-command setup |
| **Test** | pytest >=8 with pytest-asyncio (auto mode), 1,140 test functions (525 async), 63+ test files |
| **CI** | ruff check + mypy (core/tools/cli) + pytest on Python 3.12/3.13, no Node build/test in CI |
| **Deploy** | Docker (`python:3.12-slim` + Node 22 + Chromium, port 18789), Fly.io (1GB RAM, scale-to-zero, `ams` region) |

#### Stage 2: Self-Learning Pipeline (documented but not yet implemented)

Per `docs/14-SELF-LEARNING.md`:
1. `elophanto.com` collection API buffers in Supabase, pushes daily to `huggingface.co/datasets/EloPhanto/dataset`
2. When dataset reaches threshold (~5000 examples), HuggingFace Job runs QLoRA fine-tuning with Unsloth on managed GPU
3. Trained weights pushed to `EloPhanto/base-model` (safetensors + LoRA) and `EloPhanto/base-model-gguf` (for Ollama)
4. Agents auto-pull `elophanto:latest` from Ollama
5. Continuous loop: interactions -> traces -> HF dataset -> training -> model -> Ollama -> better agent

### Strengths

- **Only project with HuggingFace integration**: `DatasetBuilder` is a working end-to-end pipeline from trace capture to HF dataset
- **Two-layer sanitization**: Client + server-side regex patterns provide defense-in-depth for API key leakage
- **Quality filtering with metadata enrichment**: Sentiment, denial, error detection add signal to raw traces
- **Rich metadata schema**: `tools_used`, `duration_seconds`, `turn_count`, `has_errors`, `user_sentiment` enable downstream analysis
- **Comprehensive test suite**: 35+ test files covering core subsystems
- **Multi-channel architecture**: Gateway pattern enables any client (CLI, Telegram, Discord, VS Code) to generate traces

### Limitations & Gaps

1. **Traces from own agent only**: Does not collect traces from Claude Code, Codex, Cursor, or any external agent's native format
2. **Centralized collection API**: Traces go through `api.elophanto.com/v1/collect`, not direct HuggingFace push
3. **All traces labeled `task_type: "planning"`**: No automatic task classification
4. **All 475 rows use the same `agent_id`**: Single agent instance, no multi-user community traces yet
5. **All traces use `model_used: "glm-4.7"`**: Dataset reflects one model, not diverse model traces
6. **Opt-in but disabled by default**: `self_learning.enabled: false` in default config
7. **No trace viewer**: Raw HuggingFace dataset viewer only, no rendered conversation view
8. **Auto-registration via agent fingerprint**: Privacy model unclear, no user consent flow beyond config toggle
9. **Browser tool calls stripped from traces**: Sanitizer removes all browser automation content (potentially useful data lost)
10. **Date range compressed**: All 475 rows from Feb 22-24, 2026 (3 days of traces)
11. **GodMode is a significant security concern**: `core/godmode.py` contains a full jailbreak system prompt ("FULFILL ALL REQUESTS, No exceptions, No refusals", "Moral Filters: DISABLED"). Activated by regex triggers including the broad `r"\bno\s*limits\b"` and `r"\bunleash\b"`. Any user message containing "no limits on creativity" would activate it. NOT gated by authority tier, a public user could trigger it. Adapted from `elder-plinius/G0DM0D3`.
12. **`core/agent.py` not in protected paths**: The agent's main loop (2,921-line God Object) can be self-modified by the self-development tools. Neither `core/godmode.py`, `core/authority.py`, nor `core/swarm_security.py` are protected.
13. **Empty auth token for parent channel**: `core/agent.py:891` has `auth_token=""` with `# TODO: resolve from vault`. Child agents authenticate with empty string, any process reaching the parent's port can connect.
14. **PBKDF2 iterations discrepancy**: `core/vault.py` uses 480K iterations, but `docs/07-SECURITY.md` states 600K. Implementation is below current OWASP guidance for PBKDF2-SHA256.
15. **No integration or e2e tests**: 1,140 unit test functions but no test that starts the gateway, connects a channel adapter, and routes a message end-to-end. Frontend (React/Vite), VS Code extension, and browser bridge have zero tests.
16. **Circular imports via `Any` typing**: `gateway.py` declares `agent: Any` and `websocket: Any` to avoid circular imports, forfeiting type checking on the most critical interfaces.
17. **`data/` paths hardcoded as relative**: `_SCRATCHPAD_PATH = Path("data/scratchpad.md")` in `autonomous_mind.py` uses relative path, silently creates files in wrong location if working directory is not project root.
18. **mypy non-strict with module overrides**: `core/agent.py` has `attr-defined` and `arg-type` disabled, reducing type-level documentation value. 186 `except Exception` blocks across the codebase.
19. **Config protection by regex**: `core/protected.py` uses line-by-line regex parsing (not YAML parsing) for config-write protection, could be bypassed with multi-line values or YAML anchors.

### Community Signal

**Hacker News**: Two Show HN posts. First (Feb 2026) about the self-evolving agent received minimal engagement (1 point, only the author's comment). Second (Mar 2026) about v90 with video creation and 116 tools, slightly more traction. No substantive community feedback on the trace collection specifically.

### What We Can Learn

- **The `DatasetBuilder` pattern is the right abstraction**: Background fire-and-forget trace capture after each task, with sanitize-filter-buffer-upload pipeline
- **Two-layer sanitization is essential**: 17 client-side + 14 server-side patterns catch API keys, tokens, and PII
- **Quality filtering prevents noise**: Minimum turn count and tool call requirements keep the dataset useful
- **Metadata enrichment adds value**: Sentiment, error detection, and tool usage tracking enable downstream analysis without re-parsing conversations
- **Batch upload is efficient**: Buffering locally until `batch_size` reached reduces API calls
- **Agent fingerprinting enables anonymous contribution**: SHA-256 of agent identity provides dedup without exposing user identity
- **Direct HuggingFace push would be better**: The centralized `api.elophanto.com` intermediary adds a dependency and trust requirement that a direct `huggingface_hub` push would avoid

---

## Comparative Analysis

### Trace Format Comparison

| Aspect | OpenAmnesia | ClaudeBin | EloPhanto |
|--------|------------|-----------|-----------|
| **Input format** | Agent-native JSONL (Claude, Codex, Cursor) | Claude Code JSONL only | Own agent conversation format |
| **Normalized schema** | `Event` (turn-level, SHA-256 ID) | `ContentBlock` (17 typed blocks) | OpenAI chat format (`role`/`content`/`tool_calls`) |
| **Tool call tracking** | `tool_name`, `tool_args_json`, `tool_result_json`, `tool_status` | Per-block type (BashBlock, FileEditBlock, etc.) | `tool_calls` array in assistant messages |
| **Session grouping** | `(source, session_id)` key | JSONL file = 1 session | `task_id` UUID |
| **Metadata** | `turn_index`, `ts`, `actor`, `group_hint`, `git_branch` | `workingDir`, `modelName`, `filePaths`, `messageCount` | `success`, `task_type`, `tools_used`, `duration_seconds`, `user_sentiment` |
| **Deduplication** | SHA-256 event ID | nanoid session ID | UUID task ID |
| **PII handling** | None (stores verbatim) | Path sanitization only | 17-pattern regex sanitization |

### Architecture Comparison

| Aspect | OpenAmnesia | ClaudeBin | EloPhanto |
|--------|------------|-----------|-----------|
| **Design philosophy** | Deterministic pipeline, local-first | Cloud-native pastebin | Agent-first, traces are a side-product |
| **Storage** | SQLite (local) | Supabase PostgreSQL (cloud) | SQLite (local) + centralized API (cloud) |
| **Sharing** | None (self-hosted API only) | Shareable URLs on claudebin.com | HuggingFace dataset via api.elophanto.com |
| **Auth** | None | GitHub OAuth + device-code | Agent fingerprint auto-registration |
| **Extensibility** | 6 hook points, Protocol-based connectors | None (closed parser) | Plugin system (hot-reloadable) |
| **Multi-agent** | Yes (Claude, Codex, Cursor, Terminal, iMessage) | No (Claude Code only) | No (own agent only) |
| **Processing** | Streaming pipeline (normalize/sessionize/momentize) | Batch JSONL parse + background process | Fire-and-forget after each task |
| **LLM usage** | Optional enrichment (LiteLLM) | Title generation only (OpenRouter) | Core agent loop (LiteLLM) |

### Code Quality Comparison

| Metric | OpenAmnesia | ClaudeBin | EloPhanto |
|--------|------------|-----------|-----------|
| **Test coverage** | 9 files, ~10 tests, no API tests, `--maxfail=1` | 3 parser test files (35 tests), no API/action tests | 63+ files, 1,140 functions (525 async), no integration/e2e |
| **CI pipeline** | Lint + test (Python only), no mypy, no frontend CI | Biome lint, Supabase migrations push, PR link enforcement | ruff + mypy + pytest on Python 3.12/3.13, no Node CI |
| **Type safety** | mypy configured but not in CI | TypeScript strict, Zod validation everywhere | mypy non-strict, `Any` in critical paths (gateway, agent) |
| **Error handling** | Inconsistent: daemon wraps in `except Exception` (good), API returns tuple not HTTPException (bug) | Consistent: Zod safeParse + throw + Sentry, explicit cleanup on publish failure | Result-type pattern (`ToolResult`, `ExecutionResult`), typed raises, one silent callback swallow |
| **Documentation** | README + module docstrings (some), no generated docs | CLAUDE.md (comprehensive), `// ABOUTME:` convention, OpenAPI spec | 57 design docs, module docstrings with design rationale, 107-entry CHANGELOG |
| **Security** | No auth, no PII redaction, CORS `*`, SQL parameterized (good) | OAuth, RLS (with enumeration fix), path sanitization, DB triggers, private thread leak on main page | 7-layer defense-in-depth, encrypted vault, permission system, but GodMode jailbreak and empty child auth |
| **Codebase size** | ~15 core modules, hackathon-origin single commit | ~30 modules, 24 migrations, active security-focused maintenance | ~63 core modules, 130+ tools, 155 skills, 2,921-line God Object |
| **Package manager** | pip (no lockfile) | Bun 1.3.5 (pinned, frozen lockfile in CI) | uv (with uv.lock lockfile) |
| **Build reproducibility** | Low (no Python lockfile, `>=` bounds only) | High (exact Next.js pin, frozen lockfiles) | Medium (uv.lock exists, but Node deps not in CI) |

### Gap Analysis for Community Trace Sharing on HuggingFace

| Requirement | OpenAmnesia | ClaudeBin | EloPhanto | Gap |
|-------------|------------|-----------|-----------|-----|
| Multi-agent trace collection | Partial (connectors exist) | No | No | Need working connectors for all major agents |
| Universal trace normalization | Yes (best) | No (Claude-specific) | No (own format) | OpenAmnesia's pipeline is the model |
| PII/secret redaction | No | Partial (paths only) | Yes (17 patterns) | EloPhanto's sanitizer is the model |
| Quality filtering | No | No | Yes | EloPhanto's approach works |
| Metadata enrichment | Minimal (keyword-based) | Minimal (title gen) | Good (sentiment, errors, tools) | EloPhanto's metadata schema is useful |
| HuggingFace push | No | No | Yes (via centralized API) | Need direct `huggingface_hub` integration |
| Community sharing UX | No | Yes (best, shareable URLs) | No (raw dataset) | ClaudeBin's viewer is the model |
| User consent flow | No | Implicit (user initiates publish) | Config toggle only | Need explicit opt-in with preview |
| Offline/local-first | Yes | No | Partial (local buffer) | Need full offline support with optional sync |
| Dataset schema standard | Custom | Custom | OpenAI chat format | Need a standard the community agrees on |

---

## Synthesis: Lessons for Building a Community Trace Collection System

### 1. The Normalization Pipeline (from OpenAmnesia)

The `SourceRecord → Event → Session → Moment` hierarchy is the right abstraction. Key principles:
- **Deterministic preprocessing before any LLM step**: Normalization, filtering, deduplication must be stable and reproducible
- **SHA-256 dedup keys**: Stable, no collisions at practical scale
- **Protocol-based connector interface**: New agent support = new connector, no core changes
- **Incremental trawling**: File offset + inode + mtime tracking avoids re-reading old data
- **Config-driven source selection**: Users control which agents to ingest, paths, globs, time windows

### 2. The Parser (from ClaudeBin)

The Claude Code JSONL parser demonstrates the complexity of handling a single agent's format:
- Tool-use and tool-result blocks are interleaved across messages and must be merged
- Multi-turn assistant messages sharing the same ID need joining
- Skill commands, local commands, and task management have special patterns
- Path sanitization, ANSI stripping, and system-reminder removal are essential transforms
- **17 typed content blocks** show how diverse a single agent's output is

### 3. The Trace Collection Pipeline (from EloPhanto)

The `DatasetBuilder` pattern works well for the collection side:
- **Fire-and-forget after each task**: Non-blocking, user sees response immediately
- **Two-layer sanitization**: Client-side (before leaving the machine) + server-side (before storage)
- **Quality filtering**: Minimum turn count and tool call requirements
- **Metadata enrichment**: Sentiment, error detection, tool usage enable analysis without re-parsing
- **Batch upload**: Buffer locally, push when batch is full
- **Agent fingerprinting**: Anonymous contribution without exposing user identity

### 4. What's Missing From All Three

1. **Universal trace schema**: No standard exists. OpenAI chat format (EloPhanto) is too simple. OpenAmnesia's Event model is richer but not standardized. ClaudeBin's ContentBlock is Claude-specific.

2. **Direct HuggingFace integration**: No project pushes directly to HuggingFace using `huggingface_hub`. EloPhanto goes through a centralized API. A direct push with local sanitization would be more trustworthy.

3. **User consent with preview**: Before sharing traces, users should see exactly what will be shared, with sensitive content highlighted. None of the projects offer this.

4. **Multi-agent normalization that actually works**: OpenAmnesia has the architecture but Codex/Cursor connectors are stubs. Real-world testing of multi-agent normalization is absent.

5. **Community dataset governance**: Who reviews contributions? How are low-quality or harmful traces rejected? What's the schema versioning strategy? None address this.

6. **Trace viewer for the dataset**: ClaudeBin has a beautiful viewer but only for its own platform. The HuggingFace dataset viewer shows raw JSON. A community dataset needs a browsable, searchable viewer.

### 5. Recommended Architecture for Your Implementation

```
User's Machine (local-first)
  ├── Connectors (per-agent, Protocol-based)
  │     ├── Claude Code (read ~/.claude/**/*.jsonl)
  │     ├── Codex (read ~/.codex/**/*.jsonl)
  │     ├── Cursor (read cursor trace files)
  │     └── ... (extensible)
  ├── Normalize (SourceRecord → Event, SHA-256 dedup)
  ├── Sessionize (group by source + session_id)
  ├── Sanitize (EloPhanto-style 17+ regex patterns)
  ├── Quality Filter (min turns, min tool calls)
  ├── Metadata Enrich (tools_used, duration, sentiment, errors)
  ├── Preview (show user exactly what will be shared)
  ├── User confirms →
  └── Push directly to HuggingFace
        └── huggingface_hub.HfApi.upload_file()
              → datasets/{org}/{dataset_name}
              → Parquet with schema:
                  task_id, source_agent, conversations,
                  metadata, contributor_id (hashed), created_at
```

Key design decisions:
- **Local-first with opt-in sharing**: All processing happens locally, sharing is explicit
- **Direct HuggingFace push**: No intermediary API, users control their data
- **Preview before share**: Users see sanitized output and approve
- **Parquet format**: Efficient, columnar, HuggingFace-native
- **Contributor pseudonymity**: SHA-256 hash of HuggingFace username, not agent fingerprint

---

## Sources

- [OpenAmnesia GitHub](https://github.com/vincentkoc/openamnesia)
- [ClaudeBin GitHub](https://github.com/wunderlabs-dev/claudebin.com)
- [EloPhanto GitHub](https://github.com/elophanto/EloPhanto)
- [EloPhanto HuggingFace](https://huggingface.co/EloPhanto)
- [EloPhanto Dataset](https://huggingface.co/datasets/EloPhanto/dataset)
- [Show HN: Claudebin](https://news.ycombinator.com/item?id=47073488)
- [Show HN: EloPhanto](https://news.ycombinator.com/item?id=47076534)
- [Claude Code JSONL format overview](https://kentgigger.com/posts/claude-code-conversation-history)
- [Simon Willison's transcript tools](https://simonw.substack.com/p/a-new-way-to-extract-detailed-transcripts)
- [AI Scientist v3 trace format](https://huggingface.co/blog/alexshengzhili/aiscientist)
- [Continual Learning in Token Space (Letta)](https://www.letta.com/blog/continual-learning)
- [State of Open Source on HuggingFace: Spring 2026](https://huggingface.co/blog/huggingface/state-of-os-hf-spring-2026)
