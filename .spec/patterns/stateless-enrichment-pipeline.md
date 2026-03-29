---
schema_version: "1.0"
title: Stateless Enrichment Pipeline
scope: data processing
pattern_type: architectural
transferable: true
---

# Stateless Enrichment Pipeline

## Overview

The data enrichment stage is organized as independent, stateless modules that each add one signal type to a shared data record. Modules do not depend on each other and can run in any order. Each module is a pure function: it takes a TraceRecord (or its fields) and writes to specific output fields without side effects.

## How It Works

The CLI orchestrates six enrichment modules sequentially, but their independence means they could run in parallel:

1. **git_signals** - Adds VCS metadata (branch, base commit, commit detection) via subprocess git calls
2. **attribution** - Builds code attribution blocks from Edit/Write tool call arguments
3. **dependencies** - Extracts package names from manifests, install commands, and import statements
4. **metrics** - Computes aggregated token counts, cost estimates, and duration from step data
5. **snippets** - Extracts code blocks from tool results (consumed by attribution, not by the pipeline directly)
6. **known_packages** - Static reference data for stdlib/well-known package filtering

Each module:
- Takes a TraceRecord or subset of its fields as input
- Reads only from well-defined input fields
- Writes only to its designated output fields
- Has no shared mutable state with other modules
- Handles errors internally (returns empty/default values on failure)

## Key Files

- `src/opentraces/enrichment/git_signals.py` - VCS metadata extraction
- `src/opentraces/enrichment/attribution.py` - Code attribution construction
- `src/opentraces/enrichment/dependencies.py` - Package dependency extraction
- `src/opentraces/enrichment/metrics.py` - Token/cost/duration computation
- `src/opentraces/enrichment/snippets.py` - Code block extraction
- `src/opentraces/cli.py` - Pipeline orchestration (sequential calls in `parse` command)

## How to Replicate

1. Identify distinct signal types your pipeline needs to extract
2. Create one module per signal type with a clear input/output contract
3. Make each module a pure function: input fields -> output fields, no side effects
4. Handle errors within each module (return defaults, log warnings, never raise)
5. Orchestrate from a central entry point, calling modules sequentially or in parallel
6. Keep modules importable independently (no cross-module imports within the enrichment layer)

## When to Use

- Data pipelines that extract multiple independent signals from the same input
- When enrichment modules may fail independently without invalidating the whole record
- When you want to add new enrichment types without modifying existing modules
- When modules may need to run in different environments (some need filesystem access, some need network)

## When NOT to Use

- When enrichment steps have ordering dependencies (module B needs output of module A)
- When a single enrichment failure should invalidate the entire record
- When the overhead of separate module boundaries exceeds the benefit (very simple pipelines)
