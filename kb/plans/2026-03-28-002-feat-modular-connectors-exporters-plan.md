---
title: "feat: Modular Connectors and Exporters Architecture"
type: feat
status: approved
date: 2026-03-28
---

# Modular Connectors and Exporters Architecture

## Overview

Add two parallel extensibility mechanisms to the opentraces CLI:
1. **Connectors** (importers) -- modular ingestion from different code agent sources, named by agent (claude-code, codex, cursor, etc.). DataClaw's parsers and security modules are vendored internally but never exposed to users.
2. **Exporters** -- CLI-local format converters that project our superset TraceRecord into downstream schemas (ADP, ATIF, OTel). The HF dataset always stores native opentraces JSONL, users export locally when needed.

Both use Protocol-based contracts with simple dict registries, matching the existing `SessionParser` pattern.

## Problem Frame

The opentraces CLI currently has:
- A `SessionParser` Protocol for live agent parsing, but only `ClaudeCodeParser` implements it
- A hardcoded `import --from dataclaw` command with standalone functions that don't satisfy any Protocol
- A stub `export --format atif` command that prints "will be implemented later"
- Hardcoded format lists in the `capabilities` command

The academic survey (kb/background-research/13) positions opentraces as the **collection layer that bridges three downstream ecosystems**:
- **Training** (ADP, ATIF) for SFT/RL pipelines
- **Observability** (OTel GenAI) for monitoring dashboards
- **Evaluation** (TRAIL-style) for benchmarking

This bridge only works if users can actually convert traces to these formats. The ADP paper (kb/background-research/12) empirically validates that unified trajectory data yields ~20% SFT improvements, but ADP consumers can't use our native format without a converter. Similarly, ATIF v1.6 consumers need RL-specific fields (token IDs, logprobs, per-step cost) that require a lossy projection from our schema.

Without modular connectors, adding support for new agent sources (Codex, Cursor, Gemini CLI) requires editing cli.py directly. Without a registry, the `capabilities` command can't auto-report available formats, breaking the agent-native CLI protocol.

## Requirements Trace

- R1. `FormatImporter` Protocol alongside existing `SessionParser` Protocol
- R2. `FormatExporter` Protocol with `field_coverage()` for dry-run transparency
- R3. Dict-based registries in `__init__.py` files (no plugin frameworks)
- R4. DataClaw import refactored to satisfy `FormatImporter` without exposing "dataclaw" as user-facing agent name
- R5. ATIF exporter as first implementation, doing the lossy projection from TraceRecord to ATIF v1.6
- R6. CLI `import` command uses registry dispatch instead of hardcoded if/elif
- R7. CLI `export` command replaces stub with real implementation (format, output, dry-run, trace-id)
- R8. CLI `capabilities` command auto-populates from registries
- R9. Adding a new connector or exporter = one file + one line in registry dict

## Scope Boundaries

- No ADP exporter in this PR (architecture supports it, implementation is next)
- No OTel exporter in this PR (future v0.2 scope)
- No changes to `SessionParser` Protocol (stays as-is for live agent parsing)
- No changes to the upload pipeline (always native JSONL)
- No multi-config HF dataset uploads (single native format, export is local)
- No new agent parsers (Codex, Cursor, etc.) -- just the architecture that enables them

## Context & Research

### Why These Three Export Formats Are Structurally Different

The academic survey and ADP paper research establish that ADP, ATIF, and OTel are not field renames but fundamentally different data structures:

| Aspect | ADP | ATIF v1.6 | OTel GenAI |
|--------|-----|-----------|------------|
| **Core unit** | Flat `Action\|Observation` list | Hierarchical `Step` array | Tree of `Span` objects |
| **Identity** | `Trajectory.id` | `session_id` | `trace_id` + `span_id` + `parent_span_id` |
| **Actions** | 3 discriminated unions (API, Code, Message) | `tool_calls` array on Step | `tool` span kind with attributes |
| **Observations** | 2 types (Text, Web) as separate list items | Nested `observation.results` linked via `source_call_id` | Events on spans |
| **Reasoning** | `description` field on Action | `reasoning_content` + `reasoning_effort` on Step | Not captured |
| **Tokens** | None | Per-step: tokens, cost, token_ids, logprobs | `gen_ai.usage.input_tokens` metric |
| **Hierarchy** | Flat (no sub-agents) | `subagent_trajectory_ref` | Parent-child span tree |

An ADP consumer cannot use ATIF data without conversion, and neither can consume OTel spans directly. Each export is a genuinely different serialization, not a reshuffling of columns.

### DataClaw as Internal Infrastructure

Per project memory and competitive analysis (kb/background-research/06-dataclaw.md):

- DataClaw (2K stars, MIT license) has 7 agent parsers (Claude Code, Codex, Gemini CLI, OpenCode, OpenClaw, Kimi CLI) and security modules (secrets.py ~273 lines, anonymizer.py ~105 lines)
- **Vendor, don't depend**: Security modules vendored directly, parsers used as reference implementations
- **Reference parsers, write our own**: DataClaw's parsers contain edge-case knowledge (Gemini SHA-256 hash resolution, Codex event-stream parsing, tool_result_map correlation) but output a flat format insufficient for our enriched schema
- The existing `dataclaw_import.py` is a migration path for DataClaw users, not a dependency
- Users never see "dataclaw" as a format name -- connectors are named by agent

### Upload Strategy Decision

**Decision**: Upload is always native opentraces schema. Export is a local CLI operation.

```
opentraces push                              # always native JSONL to HF
opentraces export --format adp -o out.jsonl  # local conversion for training
opentraces export --format atif -o out.jsonl # local conversion for RL
```

Rationale:
- One source of truth on HF, no sync complexity between multiple configs
- Schema iteration doesn't require coordinating three formats simultaneously
- Downstream consumers convert on their end, or use our CLI locally
- Storage is not tripled per trace

### What Each Exporter Projects (Field Coverage)

| TraceRecord Field | ATIF | ADP (future) | OTel (future) |
|-------------------|------|-------------|--------------|
| steps | yes (as steps) | yes (as Action/Observation list) | yes (as spans) |
| tool_calls | yes (tool_calls on step) | yes (as APIAction) | yes (tool span kind) |
| observations | yes (observation.results) | yes (as TextObservation) | yes (span events) |
| reasoning_content | yes | yes (as description on Action) | no |
| token_usage | yes (per-step metrics) | no | yes (gen_ai.usage attributes) |
| system_prompts | no (ATIF stores inline) | no | no |
| outcome | partially (no ATIF equivalent) | no | no |
| attribution | no | no | no |
| security | no | no | no |
| environment | no | no | no |
| metrics aggregate | no | no | yes (span metrics) |
| dependencies | no | no | no |
| content_hash | no | no | no |

Security, attribution, environment, and dependencies are opentraces-only fields -- our differentiators that no downstream format captures.

## Key Technical Decisions

- **Two import-side Protocols, one export Protocol:** `SessionParser` (existing, live discovery + incremental parsing) and `FormatImporter` (new, file-based import) are distinct because they solve different problems. `FormatExporter` is singular because all exports share the same shape: records in, formatted strings out.

- **Dict registries, not entry points or auto-scan:** Each `__init__.py` builds a dict mapping format names to instances. Adding a new connector = one file + one line. This matches the project's "no heavy frameworks" constraint and follows the existing pattern where `cli.py` does lazy imports inside command functions.

- **Protocols are structural (typing.Protocol), not inheritance:** Matches the existing `SessionParser` pattern. New adapters only need to implement the interface without importing the Protocol module. `@runtime_checkable` enables isinstance checks in tests.

- **`field_coverage()` on FormatExporter:** Enables `--dry-run` UX where users see what gets kept vs dropped before committing to an export. Makes the "superset -> lossy projection" model transparent.

- **DataClawImporter wraps existing function:** The `import_dataclaw()` function stays as-is for backward compat. The class delegates to it. No rewrite of the import logic.

- **Validation inside CLI command body, not at decoration time:** Click's `type=click.Choice(...)` evaluates at decoration time, which would require importing registries at module scope. Instead, accept plain strings and validate against the registry inside the function body, matching the existing lazy-import pattern throughout cli.py.

## Relevant Code and Patterns

### Current Parser Architecture

- `src/opentraces/parsers/base.py:16-35` -- `SessionParser` Protocol definition (`agent_name: str`, `discover_sessions()`, `parse_session()`)
- `src/opentraces/parsers/claude_code.py` -- `ClaudeCodeParser`, the only implementer (~400 LOC)
- `src/opentraces/parsers/dataclaw_import.py:115-139` -- `import_dataclaw()` function and helpers
- `src/opentraces/parsers/__init__.py` -- Currently empty (single blank line)
- `src/opentraces/parsers/quality.py` -- `meets_quality_threshold()` filter

### Current CLI Commands

- `src/opentraces/cli.py:750-829` -- `parse` command: hardcoded `ClaudeCodeParser()` instantiation at line 765
- `src/opentraces/cli.py:1170-1207` -- `import` command: hardcoded `click.Choice(["dataclaw"])` and `if from_format == "dataclaw"` dispatch
- `src/opentraces/cli.py:1210-1218` -- `export` command: stub that prints "will be implemented later"
- `src/opentraces/cli.py:1238-1261` -- `capabilities` command: hardcoded `"agents": ["claude-code"]`, `"import_formats": ["dataclaw"]`, `"export_formats": ["atif"]`
- `src/opentraces/cli.py:1188-1199` -- Staging loop to extract as `_stage_records()` helper

### Schema Models

- `packages/opentraces-schema/src/opentraces_schema/models.py` -- All 17 Pydantic v2 models
- `TraceRecord.to_jsonl_line()` -- Native JSONL serialization
- `TraceRecord.model_dump()` -- Full dict representation for export conversion

### ATIF v1.6 Schema (from kb/background-research/01 and 13)

Root fields: `schema_version`, `session_id`, `agent` (AgentSchema with name, version, model_name, tool_definitions), `steps` (array), `notes`, `final_metrics`, `continued_trajectory_ref`, `extra`

Step fields: `step_id`, `source` ("system"/"user"/"agent"), `message`, `reasoning_content`, `reasoning_effort`, `tool_calls` (array of ToolCallSchema), `observation` (ObservationSchema), `metrics` (MetricsSchema), `model_name`, `timestamp`, `extra`

ToolCallSchema: `tool_call_id`, `function_name`, `arguments`

ObservationSchema: `results` array of ObservationResultSchema (`source_call_id`, `content`, `subagent_trajectory_ref`)

MetricsSchema: `prompt_tokens`, `completion_tokens`, `cached_tokens`, `cost_usd`, `prompt_token_ids`, `completion_token_ids`, `logprobs`

### Test Patterns

- `tests/test_e2e_flow.py` -- Click `CliRunner` tests with `runner.invoke(main, [...])`
- `tests/test_parser_claude_code.py` -- `_make_minimal_session()` factory for synthetic sessions
- `tests/test_schema.py` -- Pydantic model construction and validation tests

## High-Level Technical Design

```
                    ┌─────────────────┐
   Agent sources    │   Connectors    │     TraceRecord
   (claude logs,    │  (by agent)     │──────────────────┐
    codex logs,     │  SessionParser  │                  │
    dataclaw JSONL) │  FormatImporter │                  │
                    └─────────────────┘                  │
                                                         ▼
                                                   ┌──────────┐
                                            push   │ opentraces│  export
                                          ──────>  │ TraceRecord│ <──────
                                          native   │ (superset) │  local
                                          JSONL    └──────────┘  CLI op
                                                         │
                    ┌────────────┬────────────┬──────────┘
                    ▼            ▼            ▼
              ┌──────────┐ ┌──────────┐ ┌──────────┐
              │ Training │ │ Observ.  │ │ Eval     │
              │ ADP,ATIF │ │ OTel     │ │ (future) │
              └──────────┘ └──────────┘ └──────────┘
```

### Registry and Protocol Architecture

```
src/opentraces/parsers/
  base.py
    SessionParser(Protocol)        # existing, unchanged
    FormatImporter(Protocol)       # new
  __init__.py
    PARSERS = {"claude-code": ClaudeCodeParser()}
    IMPORTERS = {"dataclaw-jsonl": DataClawImporter()}
  claude_code.py                   # unchanged
  dataclaw_import.py               # add DataClawImporter class wrapper
  quality.py                       # unchanged

src/opentraces/exporters/
  base.py
    FormatExporter(Protocol)       # new
  __init__.py
    EXPORTERS = {"atif": ATIFExporter()}
  atif.py                          # new, ATIF v1.6 projection
```

### CLI Command Flow (export)

```
opentraces export --format atif --dry-run
  |
  v
cli.py: from .exporters import EXPORTERS
  |
  v
Validate: "atif" in EXPORTERS? yes
  |
  v
--dry-run? -> exporter.field_coverage()
  |
  v
Output:
  Export to Agent Trajectory Interchange Format v1.6 (atif)
    Preserved: steps, tool_calls, observations, reasoning_content,
               token_usage, agent, session_id
    Dropped:   attribution, security, environment, outcome,
               dependencies, system_prompts, metrics, content_hash
    3 staged traces available
```

```
opentraces export --format atif -o training.jsonl
  |
  v
_load_staged_records(trace_ids) -> list[TraceRecord]
  |
  v
exporter.export_traces(records) -> Iterator[str]
  |
  v
Write each line to training.jsonl
```

## Implementation Units

- [ ] **Unit 1: Add FormatImporter Protocol and DataClawImporter wrapper**

  **Goal:** Define the file-based import contract and make DataClaw satisfy it.

  **Requirements:** R1, R4

  **Dependencies:** None

  **Files:**
  - Modify: `src/opentraces/parsers/base.py` -- add `FormatImporter` Protocol (~15 lines)
  - Modify: `src/opentraces/parsers/dataclaw_import.py` -- add `DataClawImporter` class (~10 lines)

  **Approach:**
  - `FormatImporter` Protocol: `format_name: str`, `file_extensions: list[str]`, `import_traces(input_path: Path) -> list[TraceRecord]`
  - `DataClawImporter` class wraps existing `import_dataclaw()` function. `format_name = "dataclaw-jsonl"`, `file_extensions = [".jsonl"]`.
  - Keep `import_dataclaw()` as module-level function for backward compat.

  **Test scenarios:**
  - `isinstance(DataClawImporter(), FormatImporter)` returns True
  - `DataClawImporter().import_traces(path)` produces same result as `import_dataclaw(path)`

  **Verification:**
  - Existing `tests/test_e2e_flow.py` import tests still pass

- [ ] **Unit 2: Create connector registries in parsers/__init__.py**

  **Goal:** Replace empty `__init__.py` with PARSERS and IMPORTERS dicts.

  **Requirements:** R3, R9

  **Dependencies:** Unit 1

  **Files:**
  - Modify: `src/opentraces/parsers/__init__.py` (~15 lines)

  **Approach:**
  - Import `ClaudeCodeParser` and `DataClawImporter`
  - Build `PARSERS` dict (agent name -> SessionParser instance)
  - Build `IMPORTERS` dict (format name -> FormatImporter instance)
  - Both are instantiated at import time (stateless classes, safe to instantiate eagerly)

  **Test scenarios:**
  - `from opentraces.parsers import PARSERS, IMPORTERS` succeeds
  - `"claude-code" in PARSERS` is True
  - `"dataclaw-jsonl" in IMPORTERS` is True

- [ ] **Unit 3: Create exporters directory with FormatExporter Protocol**

  **Goal:** Define the export contract with field_coverage() for dry-run transparency.

  **Requirements:** R2

  **Dependencies:** None (parallel with Units 1-2)

  **Files:**
  - Create: `src/opentraces/exporters/__init__.py` (~10 lines)
  - Create: `src/opentraces/exporters/base.py` (~35 lines)

  **Approach:**
  - `FormatExporter` Protocol: `format_name: str`, `file_extension: str`, `description: str`, `export_traces(records: list[TraceRecord]) -> Iterator[str]`, `field_coverage() -> dict[str, bool]`
  - `field_coverage()` returns a dict mapping TraceRecord field names to booleans (True = preserved, False = dropped). Used by `--dry-run` CLI flag.
  - `__init__.py` initially imports nothing (ATIFExporter added in Unit 4)

  **Test scenarios:**
  - Importing `FormatExporter` from `opentraces.exporters.base` succeeds
  - Protocol is `@runtime_checkable`

- [ ] **Unit 4: Implement ATIFExporter**

  **Goal:** First concrete exporter, projecting TraceRecord to ATIF v1.6 JSONL.

  **Requirements:** R5

  **Dependencies:** Unit 3

  **Files:**
  - Create: `src/opentraces/exporters/atif.py` (~80 lines)
  - Modify: `src/opentraces/exporters/__init__.py` (add to EXPORTERS registry)

  **Approach:**

  The `_to_atif(record: TraceRecord) -> dict` method does the lossy projection:

  | opentraces field | ATIF v1.6 mapping |
  |-----------------|-------------------|
  | `schema_version` | `schema_version: "ATIF-v1.6"` |
  | `session_id` | `session_id` |
  | `agent.name` | `agent.name` |
  | `agent.version` | `agent.version` |
  | `agent.model` | `agent.model_name` |
  | `tool_definitions` | `agent.tool_definitions` |
  | `steps[i].step_index` | `steps[i].step_id` (1-indexed) |
  | `steps[i].role` | `steps[i].source` (agent stays agent, system/user stay) |
  | `steps[i].content` | `steps[i].message` |
  | `steps[i].reasoning_content` | `steps[i].reasoning_content` |
  | `steps[i].model` | `steps[i].model_name` |
  | `steps[i].timestamp` | `steps[i].timestamp` |
  | `steps[i].tool_calls[j].tool_call_id` | `steps[i].tool_calls[j].tool_call_id` |
  | `steps[i].tool_calls[j].tool_name` | `steps[i].tool_calls[j].function_name` |
  | `steps[i].tool_calls[j].input` | `steps[i].tool_calls[j].arguments` |
  | `steps[i].observations[j].source_call_id` | `steps[i].observation.results[j].source_call_id` |
  | `steps[i].observations[j].content` | `steps[i].observation.results[j].content` |
  | `steps[i].token_usage.input_tokens` | `steps[i].metrics.prompt_tokens` |
  | `steps[i].token_usage.output_tokens` | `steps[i].metrics.completion_tokens` |
  | `steps[i].token_usage.cache_read_tokens` | `steps[i].metrics.cached_tokens` |
  | **Dropped** | attribution, security, environment, system_prompts dict, outcome, dependencies, metrics aggregate, content_hash, snippets, call_type, parent_step, agent_role |

  Fields we cannot populate in ATIF (not available from CLI-level traces):
  - `metrics.prompt_token_ids`, `metrics.completion_token_ids`, `metrics.logprobs` -- require inference-layer access
  - `metrics.cost_usd` -- could compute from our `estimated_cost_usd / total_steps` but precision would be poor

  `field_coverage()` returns:
  ```python
  {
      "steps": True, "tool_calls": True, "observations": True,
      "reasoning_content": True, "token_usage": True, "agent": True,
      "session_id": True, "tool_definitions": True, "timestamps": True,
      "attribution": False, "security": False, "environment": False,
      "outcome": False, "dependencies": False, "system_prompts": False,
      "metrics_aggregate": False, "content_hash": False, "snippets": False,
      "hierarchy": False,
  }
  ```

  **Test scenarios:**
  - `isinstance(ATIFExporter(), FormatExporter)` returns True
  - Minimal TraceRecord with 2 steps -> ATIFExporter -> parse each yielded line as JSON -> validate ATIF root fields (`schema_version`, `session_id`, `agent`, `steps`) present
  - Step with tool_calls -> ATIF step has `tool_calls` with `function_name` (not `tool_name`)
  - Step with observations -> ATIF step has `observation.results` with `source_call_id`
  - Step with token_usage -> ATIF step has `metrics.prompt_tokens` and `metrics.completion_tokens`
  - `field_coverage()` returns expected keep/drop map
  - Empty record list -> no output lines

  **Verification:**
  - Output JSONL lines are valid JSON
  - Each line has `schema_version: "ATIF-v1.6"`

- [ ] **Unit 5: Update CLI import command**

  **Goal:** Replace hardcoded dispatch with registry lookup. Extract reusable staging helper.

  **Requirements:** R6

  **Dependencies:** Units 1-2

  **Files:**
  - Modify: `src/opentraces/cli.py` (lines 1170-1207)

  **Approach:**
  - Extract `_stage_records(records: list[TraceRecord])` helper from lines 1188-1199
  - Replace `type=click.Choice(["dataclaw"])` with plain string `--from` option
  - Replace `if from_format == "dataclaw": from .parsers.dataclaw_import import import_dataclaw` with `from .parsers import IMPORTERS` + registry lookup
  - Validate format name inside function body, emit error with available formats list if unknown
  - Keep existing error handling for file-not-found

  **Test scenarios:**
  - `opentraces import --from dataclaw-jsonl <file>` works (migration path, new name)
  - `opentraces import --from nonexistent <file>` shows error with available formats
  - Staged records appear in staging directory

  **Verification:**
  - `tests/test_e2e_flow.py` import tests pass (update format name if needed)

- [ ] **Unit 6: Update CLI export command**

  **Goal:** Replace stub with real implementation using registry.

  **Requirements:** R7

  **Dependencies:** Units 3-4

  **Files:**
  - Modify: `src/opentraces/cli.py` (lines 1210-1218)

  **Approach:**
  - Add options: `--output` / `-o` (file path, default: stdout), `--trace-id` (multiple, filter), `--dry-run` (show field coverage)
  - Add `_load_staged_records(trace_ids: tuple[str, ...]) -> list[TraceRecord]` helper: reads all `.jsonl` files from `STAGING_DIR`, optionally filtered by trace IDs. Uses `TraceRecord.model_validate_json()` for deserialization.
  - `--dry-run`: call `exporter.field_coverage()`, display kept/dropped fields, show trace count
  - Normal mode: iterate `exporter.export_traces(records)`, write to file or stdout
  - Emit structured JSON after sentinel (existing pattern)

  **Test scenarios:**
  - `opentraces export --format atif` no longer returns stub message
  - `opentraces export --format atif --dry-run` shows field coverage
  - `opentraces export --format atif -o output.jsonl` creates file with valid JSONL
  - `opentraces export --format nonexistent` shows error with available formats
  - `opentraces export --format atif --trace-id abc` filters to specific trace

  **Verification:**
  - CliRunner tests in `tests/test_e2e_flow.py`

- [ ] **Unit 7: Update CLI capabilities command**

  **Goal:** Auto-populate format lists from registries.

  **Requirements:** R8

  **Dependencies:** Units 2, 4

  **Files:**
  - Modify: `src/opentraces/cli.py` (lines 1238-1261)

  **Approach:**
  - Replace hardcoded `"agents": ["claude-code"]` with `list(PARSERS.keys())`
  - Replace hardcoded `"import_formats": ["dataclaw"]` with `list(IMPORTERS.keys())`
  - Replace hardcoded `"export_formats": ["atif"]` with structured list from EXPORTERS: `[{"name": e.format_name, "description": e.description, "file_extension": e.file_extension} for e in EXPORTERS.values()]`

  **Test scenarios:**
  - `opentraces capabilities` output includes `"agents": ["claude-code"]`
  - Adding a new exporter to EXPORTERS dict appears in capabilities output

  **Verification:**
  - CliRunner test

- [ ] **Unit 8: Tests**

  **Goal:** Full test coverage for new protocols, exporters, and CLI changes.

  **Requirements:** All

  **Dependencies:** Units 1-7

  **Files:**
  - Create: `tests/test_exporters.py`
  - Modify: `tests/test_e2e_flow.py`

  **Approach:**

  `tests/test_exporters.py`:
  ```python
  class TestATIFExporter:
      def test_protocol_conformance(self): ...
      def test_produces_valid_jsonl(self): ...
      def test_step_mapping(self): ...
      def test_tool_call_mapping(self): ...
      def test_observation_mapping(self): ...
      def test_token_usage_mapping(self): ...
      def test_field_coverage(self): ...
      def test_empty_input(self): ...

  class TestDataClawImporterProtocol:
      def test_protocol_conformance(self): ...
      def test_delegates_to_function(self): ...
  ```

  Reuse `_make_step()`, `_make_edit_tc()` helpers from existing test patterns. Create a `_make_minimal_record()` helper for export tests.

  `tests/test_e2e_flow.py` additions:
  ```python
  def test_export_atif_produces_output(self): ...
  def test_export_dry_run(self): ...
  def test_export_to_file(self): ...
  def test_import_via_registry(self): ...
  def test_capabilities_dynamic_formats(self): ...
  ```

## Open Questions

### Resolved During Planning

- **Should connectors share the SessionParser Protocol?** No. `SessionParser` has `discover_sessions()` which doesn't apply to file imports. Separate `FormatImporter` Protocol.
- **Should we use entry points or auto-scan?** No. Simple dict registries. Adding a connector = one file + one line.
- **Should we upload multiple format configs to HF?** No. Native opentraces only. Export is a local CLI operation.
- **Should "dataclaw" be a user-facing format name?** No. DataClaw is internal infrastructure. The import format is `dataclaw-jsonl` (technical migration identifier), not an agent name.

### Deferred to Implementation

- Exact ATIF v1.6 field mapping for edge cases (multi-content-part messages, empty observations)
- Whether `_load_staged_records()` should also read from approved/uploaded states
- Whether the `--from` flag on import should accept agent names (e.g., `--from codex`) in addition to format names, for future agent-specific file importers
- ATIF step_id 1-indexing vs our 0-indexed step_index

## Future Exporters (architecture supports, not in this PR)

| Exporter | Format | Downstream | Priority | Notes |
|----------|--------|------------|----------|-------|
| `ATIFExporter` | ATIF v1.6 JSONL | RL/SFT pipelines | **This PR** | Lossy: drops attribution, security, environment |
| `ADPExporter` | ADP Trajectory JSONL | Agent harness training (OpenHands, SWE-Agent, AgentLab) | Next | Flat action/observation list, 3 action types |
| `OTelExporter` | OTel GenAI spans JSON | Monitoring dashboards (Datadog, Langfuse) | v0.2 | Span tree structure, fundamentally different |
| `PROVExporter` | W3C PROV-JSON | Scientific provenance | Future | Per PROV-AGENT paper (kb/background-research/13) |

## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale |
|---|-------|----------|---------------|-----------|-----------|
| 1 | CEO | SELECTIVE EXPANSION mode | Mechanical | P1+P2 | Architecture + first exporter, not full expansion |
| 2 | CEO | Dict registries over entry points | Mechanical | P5+P3 | Explicit, debuggable, sufficient for 2-3 formats |
| 3 | CEO | Defer DataClaw upstream contribution | Mechanical | P3 | Outside blast radius, add to TODOS |
| 4 | CEO | Rename dataclaw-jsonl -> accept both names | Taste | P1 | Breaking change needs deprecation path |
| 5 | CEO | Add documented mapping tables | Mechanical | P1 | User-approved at premise gate |
| 6 | Eng | Store classes not instances in registries | Mechanical | P5 | Avoids eager import bomb |
| 7 | Eng | Exporter renumbers steps at export time | Mechanical | P5 | Source step_index is inconsistent (0 vs 1-based) |
| 8 | Eng | Use project-local staging in load helper | Mechanical | P5 | Match existing CLI behavior |
| 9 | Eng | field_coverage: full/partial/dropped (not bool) | Taste | P5 | Partial mappings need distinction |
| 10 | Eng | Add round-trip test with realistic data | Mechanical | P1 | Both voices flagged this gap |
| 11 | Eng | Add --max-records guard on importer | Mechanical | P1 | Unbounded read is security risk |
| 12 | Eng | ATIF observation wrapper: document + test 0/1/N | Mechanical | P5 | Singular vs plural is the #1 likely bug |
| 13 | Eng | Skip + count errors in export iterator | Mechanical | P1 | Partial writes are worse than skipped records |
| 14 | Eng | Negative export tests (zero records, no matches) | Mechanical | P1 | Both voices flagged |
| 15 | Eng | Defer discover/parse registry wiring to follow-up | Taste | P6 | In blast radius but 100+ LOC across 3 commands |
| 16 | Eng | Check ATIF spec for arguments type (dict vs string) | Mechanical | P5 | Wrong type = every consumer breaks |

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | Clean | 8 findings (2 high, 4 medium, 2 taste) |
| Codex CEO | `codex exec` | Independent 2nd opinion | 1 | Truncated | N/A (response cut off) |
| Claude Subagent CEO | `Agent` | Strategic independence | 1 | Clean | 8 findings, strong coverage |
| Eng Review | `/plan-eng-review` | Architecture & tests | 1 | Clean | 18 findings (5 high, all resolved) |
| Codex Eng | `codex exec` | Architecture challenge | 1 | Clean | 6 findings (3 high) |
| Claude Subagent Eng | `Agent` | Independent review | 1 | Clean | 18 findings, code-grounded |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | Skipped | No UI scope |

**VERDICT:** APPROVED. All high-severity findings resolved via auto-decisions. 2 taste decisions accepted at default.

## Future Connectors (architecture supports, not in this PR)

| Connector | Type | Source | Priority | Notes |
|-----------|------|--------|----------|-------|
| `ClaudeCodeParser` | SessionParser | `~/.claude/projects/` | **Existing** | Live discovery + incremental parsing |
| `DataClawImporter` | FormatImporter | DataClaw `conversations.jsonl` | **This PR** | Migration path for DataClaw users |
| `CodexParser` | SessionParser | `~/.codex/` | Next | Reference: DataClaw's `connectors/codex.py` event-stream parsing |
| `CursorParser` | SessionParser | Configurable | Future | Reference: DataClaw's thin `FileDropConnector` |
| `GeminiCLIParser` | SessionParser | `~/.gemini/` | Future | Reference: DataClaw's SHA-256 hash resolution for project dirs |
