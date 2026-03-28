# Architecture

open traces operates as three layers, with its own parsers and vendored security modules.

## Three-Layer Architecture

```
+-----------------------------------------------------+
|  Layer 1: INGESTION (open traces parsers)            |
|                                                      |
|  opentraces.discover_projects()                      |
|    -> scans ~/.claude, ~/.codex, ~/.gemini, etc.     |
|  opentraces.parse_session()                          |
|    -> adapter-per-agent (v0.1: Claude Code)          |
|    -> outputs rich schema directly (steps, tokens,   |
|      parent_step, system_prompts, tool_definitions)  |
|  Vendored from DataClaw (MIT):                       |
|    -> anonymizer: path/username sanitization          |
|    -> secrets: 19 regex + entropy + allowlist         |
|                                                      |
|  Output: enriched session data                       |
+------------------------+----------------------------+
                         |
                         v
+-----------------------------------------------------+
|  Layer 2: ENRICHMENT + SECURITY                      |
|                                                      |
|  Schema enrichment:                                  |
|    -> Add schema_version, content_hash               |
|    -> Construct attribution block from edit ops      |
|    -> Extract dependencies from manifest files       |
|    -> Correlate with git (committed, commit_sha)     |
|    -> Compute metrics (cost, cache_hit_rate)         |
|    -> Attach outcome signals                         |
|                                                      |
|  Security tiers:                                     |
|    -> Tier 1 (Open): vendored patterns + extras      |
|    -> Tier 2 (Guarded): classifier + escalation      |
|    -> Tier 3 (Strict): CLI/web review interface      |
|                                                      |
|  Quality filter:                                     |
|    -> Min 1 tool call, min 2 steps                   |
|    -> Content dedup via SHA-256 content_hash          |
|                                                      |
|  Output: enriched JSONL                              |
+------------------------+----------------------------+
                         |
                         v
+-----------------------------------------------------+
|  Layer 3: HF EXPERIENCE                              |
|                                                      |
|  Upload:                                             |
|    -> huggingface_hub SDK to personal dataset repos  |
|    -> Auto-generated dataset card                    |
|    -> Tagged 'opentraces' for community discovery    |
|    -> Batched upload (local buffer, threshold flush) |
|                                                      |
|  Contributor Dashboard (HF Space):                   |
|    -> Personal analytics from your published traces  |
|    -> Community comparisons and benchmarks            |
|                                                      |
|  Staged pipeline:                                    |
|    -> auth -> configure -> review -> publish          |
|    -> Push hard-gated behind review completion       |
|    -> Agent-native JSON output on every command      |
+-----------------------------------------------------+
```

## Passive Capture Only

open traces reads existing agent log files from disk after sessions end. No stop-hooks, no runtime instrumentation, no background daemons.

**Rationale:**

- Agent log files are already on disk after every session. There is nothing to "capture" that isn't already captured.
- Nobody consumes traces in real-time. Training pipelines run hours or days later.
- Real-time capture adds hook complexity, crash recovery, state management, and mid-session security exposure.
- This eliminates an entire class of bugs (hook registration failures, file locking, partial trace corruption) without sacrificing any capability.

## CLI-First, Agent-Operable Design

open traces is a CLI tool first, designed to be operated by agents as easily as by humans.

- **Git post-commit hook** integration: after a commit, `opentraces publish` runs automatically, enriching the trace with commit metadata.
- **Agent-native JSON output** on every command. Every command emits structured JSON with `next_steps` and `next_command` fields so agents can chain operations.
- **Skill file** (`SKILL.md`) ships with the package for Claude Code integration.
- **Structured errors** with `{code, kind, message, hint, retryable}` fields and a defined exit code vocabulary.
- **Machine-discoverable API**: `opentraces capabilities --json` for feature/version discovery.

## Vendor and Reference, Not Depend

open traces vendors DataClaw's small utility modules and writes its own parsers that reference DataClaw's implementations for agent-specific edge cases.

**Why not runtime dependency:** DataClaw's parsers output "flat session dicts." open traces needs access to raw trace data to construct `parent_step` links, `attribution` blocks, `system_prompt_hash`, step-level `token_usage`, and `snippets` with file positions.

**What we vendor (MIT license):**

- `secrets.py` (~273 lines): 19 regex patterns + Shannon entropy analysis + allowlist
- `anonymizer.py` (~105 lines): SHA-256 username hashing, path stripping

**What we write ourselves:**

- Parsers that output our enriched schema directly, including step-level token usage, parent_step hierarchy, system prompt extraction, tool definitions, and attribution data. v0.1 ships only the Claude Code parser, with the adapter contract ready for multi-agent expansion.
