---
title: "feat: Parser & Enrichment Improvements from ICP Feedback"
type: feat
status: active
date: 2026-03-28
deepened: 2026-03-28
---

# Parser & Enrichment Improvements from ICP Feedback

## Overview

The quality harness evaluated 82 traces across 19 projects and three persona analysts identified concrete gaps. This plan addresses each gap with data-grounded fixes, introduces an LLM escalation pattern for extraction failures, and adds drift detection tests.

## Problem Frame

Our downstream users (training teams, RL researchers, domain curators) need populated schema fields. Our investigation found:
- Fields like `vcs.branch`, `system_prompts`, and `environment.os` have data in the raw session but we never wire it
- Dependency extraction finds zero libraries via install commands (nobody runs `pip install` during coding sessions)
- Import-based extraction works for ~21% of observations when the regex is correct, but 59% of matches are false positives (internal packages)
- Reasoning content is available for 98.4% of Sonnet blocks but only 53.8% of Opus blocks (rest are redacted by Anthropic's API)
- The Read tool observation format uses a `→` (U+2192) separator, not `\t`, breaking line-number stripping

## Requirements Trace

- R1. Fill schema fields that have data in the raw session but aren't wired (OS, branch, system_prompts)
- R2. Correctly handle reasoning content: extract when available, mark when redacted, never claim absence when content was withheld
- R3. Extract library dependencies from code content in observations (imports, requires)
- R4. Filter internal packages from dependency extraction to reduce false positives
- R5. Provide a dev-time LLM diagnostic tool that helps developers understand and fix parser failures
- R6. Add drift detection tests that flag when extraction quality degrades
- R7. Parser is always fully deterministic at runtime. No LLM in the extraction path.

## Scope Boundaries

- No schema shape changes (dependencies stays `list[str]`, no structured deps in v0.1)
- No Anthropic API calls to decrypt thinking (impossible, signature is one-way)
- Parser is always fully deterministic. No LLM in the runtime extraction path.
- LLM is used only as a dev-time diagnostic tool to help developers improve the parser
- No new CLI commands (improvements are internal to parser + enrichment)

## Key Technical Decisions

### Reasoning content: mark redacted, don't fake it

Investigation found two thinking block variants:
- **Variant A** (Sonnet 98.4%, Opus 53.8%): `thinking` field has plaintext. `signature` is a cryptographic proof.
- **Variant B** (Opus 46.2%): `thinking` field is empty string `""`. `signature` is present and sometimes large (up to 66K chars). The signature contains model name in protobuf-encoded metadata but NOT the reasoning text.

There is no way to decrypt Variant B. The signature is a one-way integrity proof, not an encryption envelope. Anthropic does not expose a decryption API.

**Decision:** When `thinking == ""` and `signature` is present, set `reasoning_content = "[redacted: model produced reasoning but content was withheld by provider]"`. This tells training consumers "reasoning happened but is not available for SFT", which is materially different from "no reasoning occurred". The rubric gives partial credit (0.5) for this case.

**Why not LLM?** An LLM cannot recover the reasoning content. It could summarize what the model MIGHT have been thinking based on context, but that's fabrication, not extraction. Not appropriate for a training dataset.

### Dependencies: layered extraction with confidence signals

Investigation found the current extraction landscape:

| Signal Source | Coverage | Precision | Available Without Project Dir? |
|---|---|---|---|
| Manifest files on disk | ~100% of projects | Very high | NO |
| Install commands in Bash | 0% of sessions sampled | Very high | YES |
| Import statements in observations | 13% of observations | ~21% precision (59% ambiguous) | YES |
| File extensions in tool calls | ~80% of sessions | High for language, N/A for deps | YES |

The problem: import-based extraction has 59% ambiguous matches (internal packages like `backend`, `utils`, `openviking_cli`). A naive import parser will flood `dependencies` with project-internal names.

**Decision: Three-tier extraction with confidence filtering.**

**Tier 1: High-confidence (deterministic)**
- Scoped npm packages (`@tanstack/react-query`, `@stripe/stripe-js`) - the `@` prefix is unambiguous (filter out `@/` path aliases)
- Well-known package names from a curated allowlist (~500 popular packages across Python/JS/Ruby/Go)
- Packages matching `pip install`/`npm install` commands

**Tier 2: Medium-confidence (heuristic)**
- Import names that don't match the project name (inferred from `cwd`) and aren't in a stdlib blocklist
- Import names with PyPI/npm naming conventions (lowercase, hyphens/underscores)
- Filter out: CamelCase names (likely internal components), `type` keyword (TS false positive), node builtins, single-character names

**Dev-time LLM analysis (not runtime)**
- When drift detection tests fail and a field is unexpectedly empty or wrong, developers can run a diagnostic script that passes the failing observation content to an LLM for analysis
- The LLM explains WHY the deterministic parser missed the signal and suggests regex/filter improvements
- This is a development tool (like a debugger), not a production code path. The parser itself is always fully deterministic.
- Output: a diagnostic report suggesting parser improvements, not runtime extraction results

**Principle:** The parser must be self-contained and deterministic. LLMs assist the developer in improving the parser, never in running it.

### Observation line-number format: fix the regex

The Read tool output uses `→` (U+2192), not `\t`. The line-stripping regex must be `r'^\s*\d+\u2192'` not `r'^\s*\d+\t'`. This is a one-line fix that affects import extraction accuracy.

### Drift detection: characterization tests

Each extraction function gets a "golden set" test: a real observation content string with known imports, expected output. If the parser changes and the golden set drifts, the test fails. This catches regression before it reaches production.

## Open Questions

### Resolved During Planning

- **Can we decrypt encrypted thinking?** No. The signature is a one-way cryptographic proof (protobuf-encoded, likely ECDSA). No API exists to recover content. Confirmed by examining actual signature bytes.
- **Does `redacted_thinking` block type appear?** No. Zero instances across all sessions. Claude Code uses `thinking` type with empty `thinking` field.
- **Is install-command extraction viable?** Effectively no for ongoing work sessions. Zero matches in 100 sessions. Install commands happen in setup sessions, not coding sessions.
- **What's the observation line format?** Spaces + digits + U+2192 arrow + code. NOT tab-separated.

### Deferred to Implementation

- Exact contents of the well-known package allowlist (needs curation, ~500 names)
- Whether Tier 3 LLM escalation should use Claude or support pluggable models
- Optimal observation content sample size for LLM classification (how many import lines to send)

## High-Level Technical Design

> *Directional guidance, not implementation specification.*

```
Raw Session JSONL
       |
       v
  Parser (claude_code.py)
  +--- Wire gitBranch -> vcs.branch          [Tier 0: data already extracted]
  +--- Wire system_prompt_raw -> system_prompts dict
  +--- Parse OS from cwd path prefix
  +--- Mark redacted thinking with sentinel
  +--- Fix line-number stripping regex (U+2192)
       |
       v
  Enrichment Pipeline
  +--- infer_language_ecosystem(steps)        [from file extensions in tool calls]
  +--- extract_deps_from_imports(steps)       [Tier 1+2: deterministic]
  |    +--- strip line numbers correctly
  |    +--- filter stdlib (Python, JS, Ruby, Go)
  |    +--- filter internal packages (match against cwd project name)
  |    +--- filter CamelCase, 'type' keyword, path aliases
  |    +--- apply well-known package allowlist for ambiguous names
  +--- [dev-time only] parser_diagnostic(...)  [when tests fail]
  |    +--- deterministic diff analysis of expected vs actual
  |    +--- optional --llm-explain for natural-language fix suggestions
  +--- build_attribution(steps)               [already works, just wire it]
  +--- compute_metrics(steps)                 [already works]
       |
       v
  TraceRecord with populated fields
       |
       v
  Drift Detection Tests
  +--- Golden set for each extraction function
  +--- Schema audit checks population rates
  +--- Threshold regression alerts
```

## Implementation Units

### Phase 1: Parser Wiring (deterministic, zero risk)

- [ ] **Unit 1: Wire existing metadata to schema fields**

  **Goal:** Connect data the parser already extracts to the schema fields that consumers need.

  **Requirements:** R1

  **Files:**
  - Modify: `src/opentraces/parsers/claude_code.py`
  - Test: `tests/test_parser_claude_code.py`

  **Approach:**
  Four changes in `parse_session()` / `_extract_metadata()`:
  1. If `metadata["git_branch"]` is present and not "HEAD", set `vcs=VCS(type="git", branch=metadata["git_branch"])`
  2. Hash `system_prompt_raw` with SHA-256, store in `system_prompts[hash] = text`, set `system_prompt_hash` on agent steps
  3. Infer OS from `cwd` path: `/Users/` = "darwin", `/home/` or `/root/` = "linux", drive letter = "windows"
  4. Fix line-number stripping regex from `\t` to `\u2192` in snippet extraction

  **Test scenarios:**
  - Session with gitBranch="main" -> vcs.type="git", vcs.branch="main"
  - Session with gitBranch="HEAD" -> vcs.type="git", vcs.branch=None (HEAD is not a real branch)
  - System prompt present -> system_prompts dict has one entry, agent steps reference it
  - cwd="/Users/foo/bar" -> environment.os="darwin"
  - Observation content with arrow-separated line numbers -> imports extracted correctly

  **Verification:** Existing parser tests pass + new tests for each wiring

- [ ] **Unit 2: Handle redacted thinking blocks**

  **Goal:** Distinguish "no reasoning" from "reasoning withheld by provider."

  **Requirements:** R2

  **Files:**
  - Modify: `src/opentraces/parsers/claude_code.py`
  - Modify: `src/opentraces/quality/personas/training.py` (T6, T7 rubric adjustments)
  - Test: `tests/test_parser_claude_code.py`

  **Approach:**
  - In thinking block handling: if `block.get("thinking") == ""` and `block.get("signature")`, set reasoning to `"[redacted: model produced reasoning but content was withheld by provider]"`
  - Training persona T6: count redacted blocks as 0.5 credit (reasoning occurred but is unusable for SFT)
  - Training persona T7: mark `[redacted` prefix as present-but-unreadable
  - Training persona T1: filter `call_type == "subagent"` from alternation check (separate fix, same file)

  **Test scenarios:**
  - Thinking block with empty thinking + signature present -> reasoning_content = "[redacted...]"
  - Thinking block with content + signature -> reasoning_content = actual text
  - Thinking block with empty thinking + no signature -> reasoning_content = None (truly absent)
  - T6 with mix of readable and redacted -> partial score reflecting actual readability

### Phase 2: Dependency Extraction (medium risk, needs precision)

- [ ] **Unit 3: Language ecosystem inference**

  **Goal:** Populate `language_ecosystem` from file extensions in tool calls.

  **Requirements:** R1, R3

  **Files:**
  - Modify: `src/opentraces/enrichment/dependencies.py`
  - Test: `tests/test_enrichment.py`

  **Approach:**
  - New function `infer_language_ecosystem(steps) -> list[str]`
  - Collects file extensions from `tool_call.input.file_path` across all tool calls
  - Maps to ecosystem: `.py` -> "python", `.ts`/`.tsx` -> "typescript", `.js`/`.jsx` -> "javascript", `.rb` -> "ruby", `.rs` -> "rust", `.go` -> "go", `.java` -> "java", `.swift` -> "swift"
  - Filters non-language extensions: json, yaml, md, toml, css, html, svg, png, gitignore
  - Supplements with Bash command patterns: `python`/`pip` -> "python", `node`/`npm` -> "javascript"
  - Returns deduplicated, sorted list

  **Test scenarios:**
  - Steps with .py and .tsx files -> ["python", "typescript"]
  - Steps with only .md files -> []
  - Bash command `npm install foo` -> ["javascript"]
  - Empty steps -> []

- [ ] **Unit 4: Import-based dependency extraction**

  **Goal:** Extract library names from import statements in observation content.

  **Requirements:** R3, R4

  **Files:**
  - Modify: `src/opentraces/enrichment/dependencies.py`
  - Create: `src/opentraces/enrichment/known_packages.py` (allowlist + blocklists)
  - Test: `tests/test_enrichment.py`

  **Approach:**
  - New function `extract_dependencies_from_imports(steps, project_name=None) -> list[str]`
  - Scans `observation.content` for import patterns:
    - Python: `from X import ...` / `import X` (with correct U+2192 line stripping)
    - JS/TS: `import ... from 'X'` / `require('X')` (NOT `import type`)
    - Ruby: `require 'X'`
    - Go: `import "X"`
  - Three-stage filtering:
    1. **Stdlib filter**: curated blocklist per language (Python ~120 names, JS ~30, Ruby ~15)
    2. **Internal package filter**: if name matches project directory basename or common internal names (backend, frontend, utils, shared, core, api, lib, test, tests, config, helpers)
    3. **TS `import type` filter**: specifically reject `type` as a package name, handle `import type { ... } from 'pkg'` correctly by extracting `pkg` not `type`
    4. **CamelCase filter**: reject names starting with uppercase (React component imports, not packages)
    5. **Path alias filter**: reject `@/` prefixed imports (internal monorepo aliases)
  - Well-known package allowlist (~500 names) for ambiguous cases: if a name appears in the allowlist, always include it even if heuristics would reject it
  - Merge results with existing `extract_dependencies_from_steps()` output

  **Test scenarios (golden set):**
  - `"from pydantic import BaseModel"` -> ["pydantic"]
  - `"from backend.api import router"` -> [] (internal package, matches project name)
  - `"import type { Stripe } from 'stripe'"` -> ["stripe"] (not "type")
  - `"import React from 'react'"` -> ["react"]
  - `"from opentraces.parsers import ..."` -> [] (internal, project is opentraces)
  - `"import os"` -> [] (stdlib)
  - `"import SectionRule from '../components/SectionRule'"` -> [] (relative import)
  - `"const express = require('express')"` -> ["express"]
  - `"  42→import flask"` -> ["flask"] (with U+2192 prefix stripped)
  - `"from @tanstack/react-query import ..."` -> ["@tanstack/react-query"]
  - `"import fs from 'fs'"` -> [] (node builtin)

- [ ] **Unit 5: Dev-time parser diagnostic tool**

  **Goal:** When drift detection tests fail, give developers a diagnostic that explains WHY the parser missed a signal and suggests improvements.

  **Requirements:** R5

  **Files:**
  - Create: `src/opentraces/quality/parser_diagnostic.py`
  - Test: `tests/test_parser_diagnostic.py`

  **Approach:**
  - New function `diagnose_extraction_failure(observation_content, expected_imports, actual_imports, project_name) -> DiagnosticReport`
  - `DiagnosticReport` contains: `missed_imports`, `false_positives`, `suggested_regex_fixes`, `sample_lines_that_should_have_matched`
  - The diagnostic is purely deterministic analysis (diff expected vs actual, find the lines that contain the missed imports, show what regex pattern would have caught them)
  - An optional `--llm-explain` mode can pass the failing samples to an LLM for natural-language explanation of what went wrong and how to fix the parser. This is a dev CLI tool, not part of the extraction pipeline.
  - The diagnostic output is written to `tests/reports/parser-diagnostics/` for developer review

  **Test scenarios:**
  - Known missed import -> diagnostic identifies the line and suggests regex fix
  - No failures -> diagnostic reports "all extractions correct"
  - LLM explain mode unavailable -> falls back to deterministic analysis only

### Phase 3: Eval Harness Enrichment + Drift Detection

- [ ] **Unit 6: Wire enrichment into multi-project eval**

  **Goal:** Run available enrichment in parser-only mode so eval scores reflect actual extraction quality.

  **Requirements:** R1, R3

  **Files:**
  - Modify: `src/opentraces/quality/engine.py` (assess_multi_project)

  **Approach:**
  - After parsing each session, also run:
    - `infer_language_ecosystem(record.steps)` -> `record.environment.language_ecosystem`
    - `extract_dependencies_from_imports(record.steps, project_name)` -> merged into `record.dependencies`
    - `build_attribution(record.steps)` -> `record.attribution`
  - Decode project path from folder name for git enrichment when the directory exists on disk
  - No external dependencies, all these functions work from step data

- [ ] **Unit 7: Drift detection characterization tests**

  **Goal:** Golden-set tests that catch extraction quality regression.

  **Requirements:** R6

  **Files:**
  - Create: `tests/test_extraction_drift.py`
  - Create: `tests/fixtures/golden_observations.py` (frozen observation content samples)

  **Approach:**
  - Capture 5-10 real observation content strings from diverse sessions (Python, TS, mixed)
  - For each, record expected extracted imports and expected filtered-out names
  - Test: `extract_dependencies_from_imports(steps_from_golden) == expected_imports`
  - Test: `infer_language_ecosystem(steps_from_golden) == expected_languages`
  - Test: reasoning content handling for redacted vs readable blocks
  - These are characterization tests: they freeze current correct behavior and alert on regression
  - Also add a "population rate" regression test: run the full multi-project eval, assert that population rates for key fields don't drop below current baselines

  **Test scenarios:**
  - Golden Python observation with known imports -> exact match
  - Golden TS observation with `import type` -> `type` NOT in results
  - Golden mixed observation -> both Python and JS imports extracted
  - Schema audit population rates: language_ecosystem > 0% (up from current 0%)

## System-Wide Impact

- **Parser changes are additive:** Wiring new fields doesn't change existing extraction, only adds outputs.
- **Dependency extraction is new code path:** Failures must not break existing pipeline. Each extraction function has independent try/except.
- **LLM escalation is isolated:** Behind a flag, cached, graceful degradation. Zero impact when disabled.
- **Test harness reports will show different numbers:** Population rates will increase. Thresholds may need adjustment.

## Risks & Dependencies

- **Risk: Well-known package allowlist is never complete.** Mitigation: Start with top 500 from PyPI/npm popularity rankings. Community can contribute additions. The allowlist is a precision tool, not the primary extraction mechanism.
- **Risk: Internal package filtering is project-specific.** Mitigation: Use `cwd` basename as the project name. Filter common internal names. Accept some false positives in the medium-confidence tier.
- **Risk: Parser improvements require ongoing maintenance as Claude Code format evolves.** Mitigation: Golden-set characterization tests catch format drift. Dev-time diagnostic tool helps developers quickly understand and fix parser failures.
- **Risk: Observation content truncation (10K chars) limits import visibility.** Mitigation: Import statements typically appear at the top of files. The first 10K chars of a Read result usually covers all imports.

## Sources & References

- Persona analyses: `tests/reports/persona-analyses/` (training.md, rl.md, domain.md)
- Multi-project eval: `.gstack/qa/multi-project-eval.md`
- Schema models: `packages/opentraces-schema/src/opentraces_schema/models.py`
- Parser: `src/opentraces/parsers/claude_code.py`
- Dependencies: `src/opentraces/enrichment/dependencies.py`
- Quality harness: `src/opentraces/quality/`
- Investigation data: 82 traces across 19 projects, 12,361 thinking blocks analyzed, 7,995 observations scanned
