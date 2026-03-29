---
schema_version: "1.0"
title: Architecture Overview
scope: full-system
date_detected: 2026-03-28
---

# Architecture Overview

## System Purpose

opentraces is an open protocol and CLI for crowdsourcing AI agent traces to HuggingFace Hub. It parses coding agent sessions (currently Claude Code), applies configurable multi-tier security scanning, enriches with git signals and attribution metadata, provides human review interfaces, and publishes structured JSONL datasets. The system serves three consumer personas: SFT training pipelines, RL/reward modeling researchers, and usage analytics teams.

## System Topology

The system is a monorepo with five packages organized in a clear dependency hierarchy:

```
                      opentraces-schema (leaf, Pydantic v2 models)
                             |
                      opentraces-cli (Python CLI, Click)
                       /    |    \
                  viewer  tests  mktg-site
                  (React)        (Next.js)
```

**opentraces-schema** is a standalone pip package defining the TraceRecord JSONL format. It has zero internal dependencies and is consumed by every other Python package.

**opentraces-cli** is the main application, structured as a sequential pipeline with six processing stages, three review interfaces, and a distribution layer.

**viewer** is a React SPA served by the CLI's Flask backend during web review. It consumes a shared CSS design token package (`@opentraces/ui`).

**mktg-site** is an independent Next.js marketing site with no code dependencies on other packages.

### Pipeline Architecture

The CLI orchestrates a linear pipeline where each stage transforms or enriches a TraceRecord:

```
Discovery -> Parsing -> Security -> Enrichment -> Staging -> Review -> Upload
```

1. **Discovery**: `SessionParser.discover_sessions()` walks `~/.claude/projects/*/` for session files
2. **Parsing**: `ClaudeCodeParser.parse_session()` reads raw JSONL into TraceRecord
3. **Security**: Two-pass scanning (per-field context-aware + serialized bytes), anonymization, classification
4. **Enrichment**: Four independent modules (git_signals, attribution, dependencies, metrics)
5. **Staging**: JSONL written to staging directory, StateManager tracks lifecycle
6. **Review**: Three interfaces (CLI, TUI, Web) read staging, persist approve/reject decisions
7. **Upload**: Sharded JSONL pushed to HuggingFace Hub with dataset card generation

A parallel quality assessment system (`opentraces assess`) runs five persona-based rubrics plus an optional LLM judge against staged traces, producing fitness scores for training, RL, analytics, and domain discovery use cases.

## Request Flows

### Flow 1: Parse Pipeline (Primary Data Ingestion)

```
User runs: opentraces parse
  -> config.load_project_config() resolves tier (1/2/3)
  -> ClaudeCodeParser.discover_sessions() yields session paths
  -> StateManager.should_reprocess() checks inode/mtime for incremental resume
  -> ClaudeCodeParser.parse_session(path, byte_offset) produces TraceRecord
     -> Reads raw JSONL line by line, builds Steps with TAO structure
     -> Inlines subagent trajectories recursively (depth limit 10)
     -> Applies quality gate: min 2 steps AND min 1 tool call
  -> security.scanner.two_pass_scan() detects secrets in all text fields
  -> security.anonymizer.anonymize_paths() replaces usernames with hashed IDs
  -> security.classifier.classify_trace_record() flags sensitive content (tier 2+)
  -> enrichment.git_signals.extract_git_signals() adds VCS metadata
  -> enrichment.attribution.build_attribution() builds code attribution blocks
  -> enrichment.dependencies.extract_dependencies() extracts package names
  -> enrichment.metrics.compute_metrics() computes token/cost/cache metrics
  -> TraceRecord.to_jsonl_line() serializes with content_hash
  -> Written to staging directory, StateManager updated
```

### Flow 2: Web Review

```
User runs: opentraces review --web
  -> Flask app (create_app) serves React SPA + REST API on localhost:5050
  -> GET /api/sessions returns staged trace list with stage info
  -> User selects session in React viewer
  -> GET /api/session/<id>/detail returns full TraceRecord JSON
  -> viewer builds tree from steps (lib/tree.buildTree)
  -> TraceTree renders virtualized step hierarchy
  -> StepDetail shows tool calls, observations, security badges
  -> User approves/rejects via keyboard (s/a/r) or button
  -> POST /api/session/<id>/approve persists via StateManager
  -> POST /api/session/<id>/step/<n>/redact modifies staging JSONL on disk
```

### Flow 3: Upload to HuggingFace Hub

```
User runs: opentraces push
  -> Resolves HF token (env > credentials file > huggingface-cli cache)
  -> StateManager reads approved/committed traces from staging
  -> HFUploader.ensure_repo_exists() creates dataset repo if needed
  -> Traces serialized as JSONL shard (traces_{timestamp}_{uuid}.jsonl)
  -> Upload with exponential backoff (3 retries, 1s/2s/4s)
  -> Dataset card generated/updated (preserves user-edited sections)
  -> StateManager transitions traces to UPLOADED status
```

### Flow 4: Quality Assessment

```
User runs: opentraces assess [--judge]
  -> Loads staged traces from staging directory
  -> quality.engine.assess_batch() registers five personas
  -> Conformance persona runs 27 structural checks (schema, parser, security, structure)
  -> Training/RL/Analytics/Domain personas run 8-10 checks each
  -> Each check returns CheckResult(passed, score, evidence)
  -> Engine computes weighted rubric scores per persona (0-100)
  -> If --judge: LLM evaluator reads persona briefs, scores qualitative dimensions
     -> Hybrid blend: 60% deterministic + 40% judge
  -> schema_audit checks field population rates across batch
  -> Structured report emitted as JSON
```

### Flow 5: Configuration and Init

```
User runs: opentraces init --mode review
  -> Creates .opentraces/ directory and config.json in project
  -> Installs SessionEnd hook in .claude/settings.json
  -> Appends .opentraces/staging/ to .gitignore
  -> Global config at ~/.opentraces/config.json stores defaults
  -> Per-project config overrides tier, mode, remote, visibility
  -> HF token never stored in config (separate credentials file, 0600 permissions)
```

## Cross-Cutting Concerns

### Security

Security is the system's most distinctive architectural concern, implemented as a multi-layer defense:

1. **Secret scanning** (19+ regex patterns + Shannon entropy): Applied context-aware per field type. Tool inputs get full scanning; tool results skip entropy (false positive reduction). Two-pass architecture catches secrets introduced during enrichment.

2. **Path anonymization**: Usernames detected from `/Users/<name>/` patterns, replaced with 8-char SHA-256 prefix. System accounts excluded. Safety limit of 10 auto-detected usernames.

3. **Heuristic classification** (tier 2+): Flags internal hostnames, AWS account IDs, DB connection strings, internal URLs, high identifier density.

4. **Three security tiers**: Tier 1 (open, minimal scanning), Tier 2 (full scanning + classifier), Tier 3 (everything + mandatory human review).

5. **Credential isolation**: HF token resolved from env/file/cache chain, never persisted to config.json. Config files written with 0600 permissions via atomic `os.open()`.

6. **Log redaction**: `RedactingFilter` on Python logging prevents secrets from leaking into debug output.

### State Management

State is managed through two mechanisms:

- **StateManager** (`state.py`): JSON file-based persistence tracking the full trace lifecycle (discovered -> parsed -> staged -> reviewing -> approved -> committed -> uploading -> uploaded). Supports incremental processing via inode/mtime tracking and byte offset resume.

- **StagingLock**: File-based lock (`fcntl.flock`) prevents concurrent upload corruption. Non-blocking acquisition with immediate failure.

- **Config hierarchy**: Global config (`~/.opentraces/config.json`) with per-project overrides (`.opentraces/config.json`). YAML-to-JSON migration for legacy configs.

### Error Handling

The system follows a "skip and continue" pattern for data processing:

- Corrupted JSONL lines below 5% threshold are skipped with warnings; above threshold rejects the session
- Quality checks that throw exceptions are caught and recorded as `passed=False, score=0.0`
- Upload failures retry with exponential backoff (3 attempts), then return error result
- JSON parse errors in staging files are silently skipped
- Git subprocess calls have 10-second timeouts

CLI exit codes provide structured error reporting: 0 (OK), 2 (usage), 3 (missing config), 4 (network), 5 (data corrupt), 7 (lock/busy).

### Observability

- CLI emits structured JSON after a `---OPENTRACES_JSON---` sentinel marker for machine parsing
- `capabilities` and `introspect` commands expose machine-discoverable feature lists
- Quality assessment produces structured reports with per-persona scores
- Schema audit tracks field population rates for gap detection

### Performance

- **Lazy imports**: All sub-module imports in `cli.py` are inside command functions to minimize CLI startup time
- **Virtualized rendering**: Viewer uses `@tanstack/react-virtual` for large trace trees
- **Incremental processing**: Byte offset resume avoids re-parsing already-processed portions of session files
- **Sharded uploads**: Each push creates a new JSONL shard (never appends), enabling concurrent uploads from different machines

## Conventions

### Naming

- Python: snake_case for modules and functions, PascalCase for classes
- TypeScript: PascalCase for components, camelCase for functions and hooks
- Files: kebab-case not used; Python modules use snake_case, TS components use PascalCase
- Protocols use PascalCase and are `@runtime_checkable`

### Import Organization

- CLI entry point uses lazy imports (inside command functions) to avoid loading unused modules
- Schema package is the only cross-package import (opentraces-schema -> opentraces-cli)
- No circular dependencies in the import graph

### Testing

- pytest for Python tests, vitest for viewer tests
- Test files prefixed with `test_` in a top-level `tests/` directory
- Quality conformance checks serve as both validation and regression tests
- E2E flow tests exercise the full parse -> enrich -> assess pipeline

### Adapter Contracts

- All extension points use `typing.Protocol` (structural typing, no inheritance required)
- Three protocols: `SessionParser`, `FormatImporter`, `FormatExporter`
- New parsers/importers/exporters only need to implement the interface without importing the base module
