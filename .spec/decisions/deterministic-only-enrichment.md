---
schema_version: "1.0"
title: Deterministic-Only Enrichment (v0.1.0)
scope: src/opentraces/enrichment, src/opentraces/quality
date_detected: 2026-03-28
confidence: high
---

# Deterministic-Only Enrichment (v0.1.0)

## What

All enrichment in v0.1.0 is deterministic: git signals extracted via subprocess, metrics computed from token counts, dependencies parsed from manifests, attribution derived from tool call arguments. No LLM inference is required during the parse/enrich pipeline. The LLM judge in quality assessment is optional and off by default.

## Why

The RATIONALE document explicitly states the design principle: "zero required annotation, all enrichment in v0.1.0 is deterministic." This means:

1. The pipeline runs without API keys or network access (aside from upload)
2. Results are reproducible: same input always produces the same output
3. No cost barrier to processing traces
4. The `committed` field from git state provides the cheapest deterministic quality signal: "did the agent's changes get committed?"

## Tradeoff

**Gained**: Zero-cost, offline, reproducible enrichment. Lower barrier to adoption since users do not need API keys to parse and enrich traces. Content hashing guarantees determinism (re-parsing identical content produces identical hashes).

**Lost**: Richer metadata that LLMs could provide (task categorization, domain tags, quality descriptions). The `task.description` is limited to the first 500 characters of the user's message rather than an LLM-generated summary.

## Alternatives Rejected

1. **LLM-enriched metadata by default**: Would require API keys, add cost per trace, and break offline operation.
2. **Optional LLM enrichment in the parse pipeline**: Would make content hashes non-deterministic (LLM output varies), breaking the deduplication guarantee.
3. **Post-hoc LLM annotation as a separate pipeline stage**: This is the chosen path for the LLM judge in quality assessment, keeping it separate from the deterministic core.

## Source

- `packages/opentraces-schema/RATIONALE-0.1.0.md` (sections "Why committed + commit_sha" and "No LLM enrichment fields")
- `src/opentraces/enrichment/` (all modules use subprocess calls, regex, or arithmetic, never LLM APIs)
- `src/opentraces/quality/judge.py` (optional, off by default, gracefully degrades)

## Transferability

High. Any data pipeline that needs reproducible, offline-capable processing should separate deterministic transforms from optional LLM enrichment. The pattern is: make the core pipeline deterministic and content-addressable, then offer LLM enrichment as an opt-in overlay that does not affect the canonical output hash.
