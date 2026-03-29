---
schema_version: "1.0"
title: Superset Schema Over Standard Adoption
scope: packages/opentraces-schema
date_detected: 2026-03-28
confidence: high
---

# Superset Schema Over Standard Adoption

## What

The project defines its own TraceRecord schema as a superset of three existing standards (ATIF, Agent Trace, OTel GenAI) rather than adopting any single standard natively. Interoperability is provided through lossy export (`opentraces export --format atif`).

## Why

No single existing standard covers the full surface needed: ATIF optimizes for training pipelines (token IDs, logprobs), Agent Trace captures code attribution only, and OTel captures observability only. The project needs trajectory + attribution + security + environment in a single self-contained JSONL line. The RATIONALE-0.1.0.md document explicitly explains: "No single standard covers trajectory + attribution + security + environment."

## Tradeoff

**Gained**: A single record format that serves training, RL, analytics, and attribution use cases without external file lookups. Each JSONL line is independently useful.

**Lost**: Direct compatibility with existing tooling that expects ATIF or OTel formats. Consumers must either use the opentraces format or go through the lossy export path. The export system explicitly documents what is dropped via `field_coverage()`.

## Alternatives Rejected

1. **ATIF-native with extensions**: Would have given training pipeline compatibility but lacks fields for security metadata, code attribution, and environment context.
2. **OTel span model**: Production observability focus with request-scoped trace IDs, misaligned with session-scoped agent traces consumed by researchers.
3. **Agent Trace-native**: Attribution-only, no trajectory or observability data.

## Source

- `packages/opentraces-schema/RATIONALE-0.1.0.md` (section "Why an independent schema, not ATIF-native")
- `README.md` (schema section listing all four referenced standards)

## Transferability

High. Any project bridging multiple incompatible standards faces the "adopt one vs. create superset" decision. The pattern of superset-with-lossy-export is reusable when: (a) no single standard covers the full use case, (b) the primary format is for storage/distribution and exports serve specific consumers, and (c) the export system can explicitly document signal loss.
