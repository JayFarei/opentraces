---
schema_version: "1.0"
title: Enrichment Pipeline
scope: src/opentraces/enrichment
---

# Enrichment Pipeline

## Entities

### Enrichment Modules
Six independent modules, each enriching a TraceRecord with one signal type:
- `git_signals.py` - VCS metadata and commit outcome detection
- `attribution.py` - Agent Trace-compatible code attribution blocks
- `metrics.py` - Aggregated session metrics (tokens, cost, cache, duration)
- `dependencies.py` - Package dependency extraction from manifests and imports
- `snippets.py` - Code block extraction (consumed by attribution, not directly by pipeline)
- `known_packages.py` - Static lists of stdlib modules and well-known packages

Each module operates on a TraceRecord (or its steps) and writes to specific fields. Modules are stateless and independent.

## Business Rules

### Git Signal Extraction
Two commit detection strategies (strict and time-window):

1. **`detect_commits_from_steps()` (strict, preferred)**: Scans Bash tool calls for `git commit` commands and extracts commit SHA from the observation output. Pattern: `[branch_name SHA] message`. Only claims `committed=True` when the session itself contains the commit. No project directory needed.

2. **`check_committed()` (time-window, fallback)**: Runs `git log --after=START --before=END` against the project directory. Requires the project directory to exist on disk. Falls back to unbounded `--after=START -n 5` if the time-window query returns nothing.

Rule: A session that produced a commit is treated as `success=True` (reasonable proxy). Signal source is "deterministic", confidence is "derived".

Git subprocess timeout: 10 seconds per command.

### Attribution Construction
Attribution is built from Edit and Write tool calls in the session steps:

1. **Edit tool calls** map to line ranges based on `old_string`/`new_string` input parameters. The module tracks cumulative file content to compute accurate line positions.
2. **Write tool calls** (new files) attribute the entire file to that step.
3. **Read tool calls** are tracked for file content, used to improve future edit line calculations (not directly attributed).
4. **Cross-referencing**: If `outcome_patch` is provided, the attribution is cross-referenced against the actual diff to find unaccounted files.

**Confidence scoring**:
- `"high"`: Single-edit files (one edit operation)
- `"medium"`: Multi-edit files with no overlapping ranges
- `"low"`: Files with overlapping edit ranges

**Content hash**: MD5 truncated to 8 hex chars (murmur3 stand-in). Used for cross-refactor tracking.

Returns `None` if no Edit/Write tool calls are found.

### Metrics Computation
- **Token aggregation**: Sums input_tokens, output_tokens, cache_read_tokens, cache_write_tokens across all steps
- **Per-tier cost estimation**: Tracks tokens per model tier (sonnet/opus/haiku), applies static pricing per 1M tokens
- **Duration**: From earliest to latest timestamp across all steps

**Static pricing** (per 1M tokens):
| Tier | Input | Output | Cache Read |
|------|-------|--------|------------|
| sonnet | $3.00 | $15.00 | $0.30 |
| opus | $15.00 | $75.00 | $1.50 |
| haiku | $0.80 | $4.00 | $0.08 |

**Model tier detection**: Checks for "opus" or "haiku" in lowercased model name. Everything else defaults to "sonnet".

### Dependency Extraction
Three extraction methods, merged and deduplicated:

1. **Manifest file parsing**: Reads package.json, requirements.txt, pyproject.toml, Gemfile, go.mod from the project directory. Extracts package names only (strips version specifiers).

2. **Install command detection**: Scans Bash tool calls for `npm install`, `pip install`, `gem install`, `go get`, `cargo add` patterns. Strips flags (tokens starting with `-`) and version specifiers.

3. **Import statement analysis**: Scans observation content for Python, JS/TS, Ruby, Go import patterns. Applies three-stage filtering:
   - Stage 1: Stdlib filter (Python stdlib, Node builtins)
   - Stage 2: Internal package filter (project name match, common internal names, CamelCase, relative imports, single-char names)
   - Stage 3: Well-known packages always pass

### Language Ecosystem Inference
Detects programming languages from two sources:
1. **File extensions** in tool call inputs (`file_path`, `path` keys)
2. **Bash command patterns** (python, npm, cargo, go, bundle/gem)

Ignored extensions: .json, .yaml, .md, .toml, .css, .html, .svg, .png, .lock, etc.

## Calculations

- **Cache hit rate**: `cache_read_tokens / (input_tokens + cache_read_tokens)`, rounded to 4 decimal places
- **Estimated cost**: `sum(tier_tokens[type] * pricing[type] / 1_000_000)` across all tiers and token types
- **Duration**: `(last_timestamp - first_timestamp).total_seconds()`, rounded to 2 decimal places

## State Machines

None. Each enrichment module is a pure function: `TraceRecord -> enriched fields`.

## Edge Cases

1. **First commit in repo**: `git diff SHA~1..SHA` fails for root commits. Falls back to `git show --format= --patch SHA`.
2. **Detached HEAD**: `git rev-parse --abbrev-ref HEAD` returns "HEAD", handled specially in VCS inference.
3. **Timestamp parsing tolerance**: Handles trailing "Z" by replacing with "+00:00" before `datetime.fromisoformat()`.
4. **Manifest parse errors**: All manifest parsers catch `JSONDecodeError` and `OSError`, returning empty lists on failure.
5. **JS scoped packages**: `@scope/name` is normalized to the two-segment form. `@/` relative imports are filtered out.
6. **Line number stripping**: Import analysis strips line number prefixes (e.g., `42->code`) from observation content before matching.
7. **pyproject.toml parsing**: Hand-parsed (not TOML library). Only extracts from `dependencies = [` block. Does not handle inline tables or optional-dependencies.
8. **Custom pricing**: `compute_metrics()` accepts optional pricing dict override. Falls back to sonnet pricing for unknown tiers.
