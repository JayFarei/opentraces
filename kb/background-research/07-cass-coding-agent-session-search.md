# CASS (Coding Agent Session Search): R&D Scouting Brief

> Research date: 2026-03-27
> Source: https://github.com/Dicklesworthstone/coding_agent_session_search
> Category: tool (CLI + TUI)

---

## Overview

CASS (`cass`) is a Rust CLI/TUI tool by Jeff Emanuel (Dicklesworthstone) that indexes and searches coding agent session histories from 19+ providers into a unified, local-first searchable store. It combines BM25 full-text search (Tantivy), optional semantic vector search (MiniLM via FastEmbed), and hybrid RRF fusion, all backed by SQLite, with a rich terminal UI and a comprehensive robot/agent-mode JSON API. 633 stars, 85 forks, actively developed (v0.2.4, March 2026).

## Problem It Solves

Developers using multiple coding agents (Claude Code, Codex, Cursor, Gemini CLI, Aider, Cline, etc.) accumulate thousands of conversation sessions scattered across different formats (JSONL, SQLite, Markdown, JSON) in different locations. CASS solves the "I know I solved this before but can't find it" problem by:

1. Auto-discovering sessions from 19 agent formats across local and remote machines
2. Normalizing them into a unified `Conversation -> Message -> Snippet` model
3. Indexing for sub-60ms search with multiple retrieval strategies
4. Providing both human (TUI) and agent (JSON API) interfaces

## How It Works

### Architecture

```
[Agent Session Files]          [Remote Machines via SSH/rsync]
   (19 formats)                        |
        |                              v
        v                    [Local Mirror in data_dir]
[franken_agent_detection]              |
   (connector per agent)               |
        |                              |
        +--------- Normalized ---------+
                   Conversations
                        |
                        v
              [Streaming Indexer Pipeline]
              (producer-consumer, batch 64)
                   |         |
                   v         v
          [FrankenSQLite]  [Tantivy BM25]  [FSVI Vectors + HNSW]
          (source of truth) (lexical search)  (semantic search)
                   |         |                    |
                   +-------- | -------------------+
                             v
                    [Search Engine]
                    (lexical | semantic | hybrid RRF)
                             |
                   +---------+---------+
                   |                   |
                   v                   v
              [Rich TUI]        [Robot JSON API]
              (FrankenTUI)      (--robot/--json)
```

### Key Concepts

- **Connectors**: 19 agent-specific parsers in `franken_agent_detection` crate. Each implements a `Connector` trait with `detect()` and `scan()` methods. Auto-discovery from standard paths (`~/.claude/projects/`, `~/.codex/sessions/`, etc.). Formats: JSONL (Claude Code, Codex, Pi-Agent), SQLite (Cursor, OpenCode), Markdown (Aider), JSON (Gemini, ChatGPT), encrypted JSON (ChatGPT v2/v3).

- **Normalized Model** (`src/model/types.rs`):
  - `Conversation`: agent_slug, workspace, source_path, timestamps, approx_tokens, messages, source_id, origin_host
  - `Message`: idx, role (User/Agent/Tool/System), content, snippets, extra_json
  - `Snippet`: file_path, start/end_line, language, text

- **Dual Storage**: SQLite (source of truth, CRUD, migrations, FTS5 fallback) + Tantivy (BM25 full-text, edge n-gram prefix index) + optional FSVI vectors (semantic). All local, no external services.

- **Search Modes**: Lexical (BM25, default), Semantic (vector similarity via FastEmbed MiniLM-L6-v2 or FNV-1a hash fallback), Hybrid (Reciprocal Rank Fusion with query-class-adaptive multipliers: identifier queries weight lexical 6:2, natural language weights semantic 2:8).

- **Two-Tier Progressive Search** (`src/search/two_tier_search.rs`): Fast embedder (hash, ~1ms) returns initial results immediately, quality daemon (MiniLM, ~130ms) refines via Unix Domain Socket.

- **Remote Sources**: SSH-based sync via rsync/SFTP. `sources.toml` config defines remote machines. Provenance tracked per conversation (source_id, origin_host, workspace_original).

- **Secret Redaction** (`src/indexer/redact_secrets.rs`): Compiled regex patterns scrub AWS keys, GitHub PATs, API keys, Bearer tokens, JWTs, PEM keys before data reaches SQLite or the search index.

- **Warm Model Daemon** (`src/daemon/`): MessagePack over Unix Domain Sockets, length-prefixed framing, wire-compatible with `xf` tool. Keeps MiniLM model loaded for fast inference.

### Core API / Interface

**Binary**: `cass` (Rust, single static binary with bundled SQLite and ONNX Runtime)

**Primary CLI Commands**:

```bash
# Health & discovery
cass health --json                           # <50ms pre-flight check
cass status --json                           # index freshness, counts
cass capabilities --json                     # features, connectors, limits
cass introspect --json                       # full API schema

# Indexing
cass index --full                            # full rebuild
cass index --watch                           # daemon mode, reindex on changes
cass index --semantic --build-hnsw           # build vector index

# Search
cass search "query" --robot --limit 10       # structured JSON output
cass search "auth" --mode hybrid --robot     # hybrid BM25 + semantic
cass search "error" --aggregate agent,workspace --robot  # server-side aggregation
cass search "bug" --robot-format sessions | cass search "fix" --sessions-from -  # pipeline

# Session exploration
cass sessions --current --json               # current workspace sessions
cass view /path/to/session.jsonl -n 42 --json   # view at line
cass expand /path/to/session.jsonl -n 42 -C 3 --json  # context around line
cass context /path/to/session --json         # find related sessions
cass timeline --today --json --group-by hour # activity timeline

# Analytics
cass analytics tokens --days 30 --json       # token usage over time
cass analytics tools --limit 20 --json       # tool call frequency
cass analytics models --json                 # model usage breakdown

# Export
cass export /path/to/session --format json --include-tools
cass export-html session.jsonl --encrypt --password "secret"
cass pages --target github                   # encrypted static archive

# Remote sources
cass sources setup                           # interactive SSH wizard
cass sources sync --source laptop            # rsync sessions
cass sources doctor --json                   # health check remotes
```

**Robot Mode Output** (search example):
```json
{
  "query": "authentication error",
  "count": 5,
  "total_matches": 42,
  "hits": [{
    "title": "...",
    "snippet": "...",
    "content": "...",
    "score": 0.85,
    "source_path": "/path/to/session.jsonl",
    "agent": "claude-code",
    "workspace": "/path/to/project",
    "created_at": 1700000000000,
    "line_number": 42,
    "match_type": "exact",
    "source_id": "local"
  }],
  "cursor": "eyJ...",
  "_meta": {
    "elapsed_ms": 12,
    "wildcard_fallback": false,
    "cache_stats": {"hits": 3, "misses": 1}
  }
}
```

**Key Environment Variables**: `CASS_DATA_DIR`, `CASS_DB_PATH`, `CASS_OUTPUT_FORMAT`, `CASS_TRACE_FILE`, `CASS_REDACT_SECRETS`, `CASS_SEMANTIC_EMBEDDER`.

**Exit Codes**: 0 (OK), 2 (usage), 3 (missing index), 4 (network), 5 (corrupt), 6 (version mismatch), 7 (lock/busy), 8 (partial result).

---

## Maturity & Traction

- **License**: Non-standard / NOASSERTION (check with author)
- **Stars/Forks**: 633 / 85
- **Latest Release**: v0.2.4 (March 27, 2026)
- **Backing**: Solo developer (Jeff Emanuel / Dicklesworthstone), prolific open-source contributor
- **Production Users**: Unknown, but the BrightCoding blog review and Show HN post suggest real adoption
- **Ecosystem Size**: Part of a broader "agentic coding flywheel" ecosystem by the same author:
  - `franken_agent_detection` - multi-agent format parser
  - `frankensearch` - search engine (Tantivy + vector + RRF)
  - `frankensqlite` - custom SQLite wrapper
  - `frankentui` / `ftui` - TUI framework
  - `cross_agent_session_resumer` - resume sessions across agents
  - `agentic_coding_flywheel_setup` - VPS bootstrap for multi-agent dev
  - `beads_viewer` - graph-aware issue tracker TUI

---

## Strengths

- **Comprehensive agent coverage**: 19 connectors covering every major coding agent. Auto-discovery means zero configuration for most users.
- **Excellent agent/robot API design**: Self-documenting (`introspect`, `capabilities`, `robot-docs`), forgiving syntax (typo correction, case normalization, alias resolution), structured errors with hints and retryable flags, cursor-based pagination, request correlation IDs, token budget management, aggregations, chained pipeline search.
- **Search quality**: Three-mode search (lexical, semantic, hybrid) with query-class-adaptive RRF multipliers, two-tier progressive search, cross-encoder reranking, and a hash embedder fallback that requires no ML model.
- **Performance**: Sub-60ms search latency, edge n-gram prefix indexing, sharded LRU cache with bloom filter pre-checks, predictive index warming, streaming producer-consumer indexing pipeline.
- **Security**: Ingestion-time secret redaction, AES-256-GCM encryption for exports, Argon2id KDF, zeroize for key material, cargo-audit in CI.
- **Testing depth**: 4,988 tests, no-mock CI policy, property-based testing (proptest), fuzz targets, snapshot tests (insta), benchmark regression detection, cross-platform CI matrix.
- **Multi-machine support**: SSH/rsync-based remote source sync with provenance tracking, path mappings, and interactive setup wizard.

## Limitations & Risks

- **License unclear**: Listed as "NOASSERTION" on GitHub. Non-standard license needs clarification before any integration or dependency.
- **Monolith files**: `src/ui/app.rs` is 41,576 lines, `src/lib.rs` is 18,113 lines, `src/storage/sqlite.rs` is 14,120 lines. These create maintenance and contribution barriers.
- **Private dependency chain**: Core crates (`franken_agent_detection`, `frankensearch`, `frankensqlite`, `ftui`, `asupersync`) are either local path dependencies or git-pinned to the author's repos. Building from source requires cloning multiple sibling repos. External contribution and auditing is harder.
- **Wildcard version specifiers**: ~45 dependencies use `"*"` versions in Cargo.toml. While Cargo.lock pins them, this is a supply chain risk.
- **Solo maintainer**: Entire ecosystem depends on one developer. Bus factor of 1.
- **No export API for trace datasets**: CASS is optimized for search/retrieval, not for producing structured trace datasets suitable for training jobs, skill extraction, or community publishing. Export formats are markdown, text, JSON (per-session), and HTML, not columnar/Parquet for bulk analysis.
- **Linux glibc requirement**: Pre-built binaries require glibc 2.38+ (Ubuntu 24.04+). Older distros must build from source.
- **`unsafe impl Send/Sync`**: Three instances in storage layer to work around upstream library constraints. Correct but fragile.

---

## Competitive Landscape

| Alternative | Differentiator | Trade-off |
|-------------|---------------|-----------|
| **Agtrace** | Live monitoring dashboard (top/tail -f for agents), context pressure tracking, cost tracking | Real-time focus, less search depth |
| **Capsule** | Interactive session log explorer with built-in anonymizer for sharing | Sharing-focused, less search power |
| **claude-trace** | Lightweight JSONL + HTML interceptor for Claude Code | Capture only, single agent, no search |
| **LangSmith** | Full observability platform with production monitoring | SaaS, LangChain-ecosystem-tied, not local |
| **Cursor's semantic search** | Custom embeddings trained on agent traces for code retrieval | Internal tool, not for session history search |
| **grepai** | Local semantic code search via Ollama MCP | Code search, not session history |

**CASS's unique position**: It's the only tool that combines multi-agent auto-discovery (19 agents), multi-machine sync, hybrid search (BM25 + semantic + RRF), a rich TUI, and a comprehensive robot/agent API in a single local-first binary. No other tool covers this full surface.

---

## Community Signal

**Hacker News** ([Show HN, Dec 2025](https://news.ycombinator.com/item?id=46130481)): Presented as solving the direct pain point of "knowing you solved something before but can't find it across multiple agent tools." Community reception positive.

**YC S26 Application discussion** ([Feb 2026](https://news.ycombinator.com/item?id=46889045)): YC's application asked to "attach a coding agent session you're particularly proud of," validating that agent session history has become a first-class artifact worth preserving and showcasing.

**BrightCoding blog** ([Mar 2026](https://www.blog.brightcoding.dev/2026/03/22/cass-the-revolutionary-tool-that-unifies-your-ai-coding-history)): Positive review calling it "revolutionary" for unifying AI coding history.

**Ecosystem pattern**: The author has built an entire "agentic coding flywheel" ecosystem (`agentic_coding_flywheel_setup`, `cross_agent_session_resumer`, `beads_viewer`), suggesting deep domain commitment.

---

## Integration Analysis: Trace Publishing Platform

### Fit Assessment: Strong Fit (as data source, not as the publishing tool itself)

CASS solves the *ingestion and normalization* problem extremely well, exactly the hardest part of our trace publishing pipeline. Its 19 connectors + unified model + secret redaction are directly reusable. However, it is optimized for search/retrieval, not for producing structured trace datasets for downstream consumption (training, skills, analysis).

### Integration Points

1. **Normalized data model**: CASS's `Conversation -> Message -> Snippet` model with `agent_slug`, `workspace`, `source_path`, `approx_tokens`, and `MessageRole` maps cleanly to our Layer 2 structured trace format. The normalization from 19 agent-specific formats into this unified model is the most valuable piece.

2. **franken_agent_detection crate**: The connector library is a separate crate. If it were available as a standalone dependency (rather than a local path dep), it could be used directly as our parsing layer without depending on CASS itself.

3. **Secret redaction**: `src/indexer/redact_secrets.rs` already handles AWS keys, GitHub PATs, API keys, Bearer tokens, JWTs, PEM keys. This directly addresses our privacy/security requirement for trace publishing.

4. **Robot JSON API**: `cass search --robot` output could be consumed by an agent building a domain dataset, but only for search, not bulk export. The `--aggregate` feature supports domain-level queries (by agent, workspace, date).

5. **Remote sources**: SSH/rsync sync means CASS can already pull sessions from multiple machines, which maps to our multi-machine trace collection need.

### Gaps Between CASS and Our Needs

| CASS Has | We Need | Gap |
|----------|---------|-----|
| Normalized conversations | Two-layer trace format (metadata envelope + structured calls) | Need to add Layer 1 metadata extraction (domain_tags, task_type, outcome, prefix_reuse_rate) |
| Per-session JSON export | Bulk Parquet/columnar export for HF Hub | Need export pipeline |
| Search-optimized storage | Query-optimized dataset (HF Dataset Viewer compatible) | Need Parquet writer |
| Secret redaction (regex) | Configurable redaction + anonymization | May need to extend |
| Local-first storage | HF Hub / Storage Bucket publishing | Need upload pipeline |
| 19 agent connectors | Same, plus trace enrichment (LLM pass for tags/classification) | Need enrichment layer |
| `parent_call` not tracked | Agent hierarchy reconstruction | Need to infer from session structure |

### Proposed Integration Pattern

**Option A: Use CASS as ingestion layer, build export pipeline on top**
```
[Agent Sessions] -> [CASS index] -> [cass export --format json] -> [Enrichment Pipeline] -> [HF Hub]
```
Pro: Leverage existing connectors and normalization. Con: Depends on CASS's export format, which is per-session JSON not optimized for bulk.

**Option B: Extract franken_agent_detection as a library dependency**
```
[Agent Sessions] -> [franken_agent_detection::scan()] -> [Our Enrichment Pipeline] -> [HF Hub]
```
Pro: Direct access to normalized conversations, no CASS runtime dependency. Con: franken_agent_detection is a local path dependency, not published to crates.io. Would need to fork or convince author to publish.

**Option C: Build a CASS skill/plugin that exports to HF format**
```
[CASS indexed data] -> [cass export-hf --format parquet --enrich] -> [HF Hub]
```
Pro: Extends CASS rather than competing. Con: Couples to CASS release cycle and architecture.

### Effort Estimate

- Option A: Short (days) for basic pipeline, Medium (weeks) for enrichment
- Option B: Medium (weeks), depends on extracting the crate
- Option C: Medium (weeks), requires understanding CASS internals

### Open Questions

1. What is the actual license? "NOASSERTION" needs clarification before any integration.
2. Is the author open to publishing `franken_agent_detection` as a standalone crate?
3. Would the author be interested in a community trace publishing feature as a CASS extension?
4. How does `cross_agent_session_resumer` (the author's other tool) relate? It converts between agent formats, which might be closer to our trace format standardization need.

---

## Key Takeaways

1. **CASS's 19-agent connector library is the most comprehensive multi-agent session parser available.** The `franken_agent_detection` crate handles JSONL, SQLite, Markdown, JSON, and encrypted formats from every major coding agent. This is the hardest part of trace ingestion and it's already solved. If we can use it as a library, we skip months of parser development.

2. **CASS is optimized for search, not for dataset production.** The architecture (SQLite + Tantivy + TUI) is built for interactive retrieval, not for producing bulk structured datasets for training jobs or HF Hub publishing. Our trace publishing tool would need to build the export/enrichment/publishing pipeline that CASS doesn't have.

3. **The robot/agent API is a reference implementation for agent-consumable CLI design.** Self-documenting introspection, forgiving syntax, structured errors, token budgets, cursor pagination, aggregations, and chained pipeline search. If we're building a trace publishing CLI, this API design should be studied and borrowed from.

---

## Sources

- [GitHub: coding_agent_session_search](https://github.com/Dicklesworthstone/coding_agent_session_search)
- [Show HN: Coding Agent Session Search](https://news.ycombinator.com/item?id=46130481)
- [YC S26 Application discussion on agent sessions](https://news.ycombinator.com/item?id=46889045)
- [Show HN: Capsule session log explorer](https://news.ycombinator.com/item?id=46975031)
- [Show HN: Agtrace](https://news.ycombinator.com/item?id=46425670)
- [BrightCoding: CASS review](https://www.blog.brightcoding.dev/2026/03/22/cass-the-revolutionary-tool-that-unifies-your-ai-coding-history)
- [Cursor: Semantic search with agent traces](https://cursor.com/blog/semsearch)
- [GitHub Copilot: Semantic code search for coding agent](https://github.blog/changelog/2026-03-17-copilot-coding-agent-works-faster-with-semantic-code-search/)
- [cross_agent_session_resumer](https://github.com/Dicklesworthstone/cross_agent_session_resumer)
- [agentic_coding_flywheel_setup](https://github.com/Dicklesworthstone/agentic_coding_flywheel_setup)
- [SourcePulse: CASS project page](https://www.sourcepulse.org/projects/21817022)
- [learn-skills.dev: CASS skill](https://www.learn-skills.dev/en/skills/dicklesworthstone/agent_flywheel_clawdbot_skills_and_integrations/cass)
