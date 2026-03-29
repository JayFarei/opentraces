---
schema_version: "1.0"
title: Three-Tier Security Model
scope: src/opentraces/security, src/opentraces/config.py
date_detected: 2026-03-28
confidence: high
---

# Three-Tier Security Model

## What

Security processing is organized into three configurable tiers (open, guarded, strict) rather than a single binary redact/no-redact mode. The tier is set per-project at init time and controls scanning strictness, classification depth, and human review requirements.

## Why

Users work on both open-source and proprietary code. A single redaction mode forces an all-or-nothing choice: either apply heavy redaction to open-source traces (losing useful content) or skip redaction on proprietary traces (risking credential leaks). The tier system enables per-project configuration so traces from public repos get minimal processing while proprietary repos get full scanning and mandatory review.

The RATIONALE document states: "Existing trace-sharing tools typically offer a single redaction mode: everything is processed the same way. A tier system enables per-project configuration with per-session override."

## Tradeoff

**Gained**: Fine-grained security control matching the actual sensitivity of each project. Users are more likely to share traces when they trust the security model fits their situation.

**Lost**: Complexity in the security pipeline (context-aware scanning rules per field type, tier-dependent classifier activation, review flow branching). The two-pass scanning architecture (per-field + serialized bytes) adds processing overhead even for Tier 1.

## Alternatives Rejected

1. **Binary redact/no-redact**: Too coarse. Would discourage sharing from users with mixed public/private codebases.
2. **Per-field granular controls**: Too complex for users to configure correctly. The tier abstraction hides the per-field rules behind a single integer.
3. **Server-side scanning only**: Would require uploading raw content before security processing, which violates the zero-trust principle of scanning before any network transfer.

## Source

- `packages/opentraces-schema/RATIONALE-0.1.0.md` (section "Why a 3-tier security model")
- `src/opentraces/security/scanner.py` (two-pass architecture, context-aware field scanning)
- `src/opentraces/config.py` (tier configuration, default tier 3)

## Transferability

High. Any system that processes user data with varying sensitivity levels benefits from a tiered approach. The pattern is: define a small number of named presets (2-4), map each to a bundle of technical controls, default to the strictest tier, and allow per-entity overrides. The key insight is that users understand "open/guarded/strict" better than individual toggle configurations.
